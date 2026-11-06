"""Cut detection core for the cut worker (#7): video in, cuts and slots out, as decided in ADR 0003.

PySceneDetect's AdaptiveDetector compares each frame's content change with the average change around it, so a cut
stands out even in footage that moves a lot. Every detected cut is kept; suspect ones are flagged for the user to
review, never deleted here.

This module doesn't depend on the shared worker base (#53). The worker's `detect()` calls `find_cuts()` and
`to_partial()`; the base adds provenance, validates the result and stores it.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ADR 0003. Any change here changes the output, so the worker reports these as its params (its cache key).
# Recall tuning (#7): ADR 0003's 2.0 with PySceneDetect's default window of 2 gave P 0.881 / R 0.925 on 14 golden
# videos, under the 0.95 recall floor. 1.7 with a window of 3 gives P 0.852 / R 0.958 on the same 14 (tag
# `tuning-7-recall`). PROVISIONAL: chosen on the videos it's scored on. Choosing on 13 and scoring the 14th gave
# R 0.932, because the recovered cuts come from single videos (v12's flash burst, v14's 1-frame shots). Re-check on
# held-out videos once #52 adds more of those.
ADAPTIVE_THRESHOLD = 1.7
WINDOW_WIDTH = 3  # frames each side of the current one in the rolling average; PySceneDetect's default is 2
MIN_SCENE_LEN = 1  # frames; PySceneDetect's default of 15 drops fast cuts
# Flagging (#7): the content check in `suspect_cuts()`. For two detections close together, is what's between them a
# shot of its own (a real fast cut, or a flash) or just the change from one shot to the next (a duplicate detection)?
# Closeness alone was tried and rejected: real cuts can be 2-3 frames apart (v06's montage, every flash cut).
# PROVISIONAL: DISTINCT_MIN was chosen after looking at v01-v06. Score v07-v10 with it unchanged before tuning on them.
PAIR_WINDOW_S = (
    0.5  # seconds; pairs of detections further apart than this aren't checked
)
DISTINCT_MIN = 30.0  # mean colour difference (0-255) the in-between frames need from both neighbouring shots
SAMPLE_SIZE = (
    64,
    112,
)  # frames are shrunk to this (width, height) before comparing, as when DISTINCT_MIN was chosen
# The convention while the flag lives in `confidence` (no schema field yet): below this means "needs review".
# Provisional: how flags are stored long-term is decided in #343, so code reading it as a flag should link there.
# Unflagged cuts score 0.5 or more; flagged ones are scaled into 0-0.4, so the most suspect still sort lowest.
REVIEW_BELOW = 0.5
FLAGGED_MAX = 0.4
PARAMS: dict[str, Any] = {
    "detector": "pyscenedetect.AdaptiveDetector",
    "adaptive_threshold": ADAPTIVE_THRESHOLD,
    "window_width": WINDOW_WIDTH,
    "min_scene_len": MIN_SCENE_LEN,
    "flag_rule": "content",
    "pair_window_s": PAIR_WINDOW_S,
    "distinct_min": DISTINCT_MIN,
    "sample_size": list(SAMPLE_SIZE),
    "review_below": REVIEW_BELOW,
    "flag_unscored": True,
}


@dataclass(frozen=True)
class Cut:
    frame: int  # the first frame of the new shot, as `cuts[].frame` in the schema
    score: float  # PySceneDetect's adaptive ratio at that frame: how far it stands out from its neighbours


@dataclass(frozen=True)
class Detection:
    cuts: list[Cut]
    frames: int  # frames decoded, so slots can run to the end of the video


def find_cuts(video: Path) -> Detection:
    """Run AdaptiveDetector over every frame of `video`."""
    from scenedetect import AdaptiveDetector, SceneManager, StatsManager, open_video

    stream = open_video(str(video))
    detector = AdaptiveDetector(
        adaptive_threshold=ADAPTIVE_THRESHOLD,
        min_scene_len=MIN_SCENE_LEN,
        window_width=WINDOW_WIDTH,
    )
    stats = StatsManager()
    manager = SceneManager(stats_manager=stats)
    manager.add_detector(detector)
    manager.detect_scenes(stream)
    ratio_key = next(
        k for k in detector.get_metrics() if k.startswith("adaptive_ratio")
    )

    scenes = manager.get_scene_list(start_in_scene=True)
    frames = scenes[-1][1].frame_num if scenes else stream.frame_number
    cuts = []
    for start, _ in scenes[1:]:
        (ratio,) = stats.get_metrics(start.frame_num, [ratio_key])
        cuts.append(Cut(start.frame_num, cut_score(ratio)))
    return Detection(cuts, frames)


def cut_score(ratio: float | None) -> float:
    """A cut's score from its adaptive ratio. A missing ratio is NaN, so the cut is flagged for review (`to_partial()`)."""
    return float(ratio) if ratio is not None else math.nan


def library_versions() -> dict[str, str]:
    """The decoding and detection libraries' versions. Results can change with them (decoding differs by a cut
    between builds, see `test_golden.py`), so they're part of the worker's params, its cache key."""
    import cv2
    import scenedetect

    return {"scenedetect": scenedetect.__version__, "opencv": cv2.__version__}


def suspect_cuts(video: Path, cuts: list[Cut], fps: float) -> frozenset[int]:
    """Frames of cuts to flag for review: both cuts of any pair, at most PAIR_WINDOW_S apart, whose in-between frames
    aren't a shot of their own.

    The in-between frames are compared with the last frame before the pair and the first frame after it. A real fast
    cut (or a flash) differs from both, so it's left alone however close the cuts are. A duplicate detection, where one
    shot change was reported twice, comes out close to one of them. Cuts inside a labeled transition look the same way
    (the frames between are a blend), so they can be flagged too until the transition detector (#23) can say so.
    """
    import cv2

    window = max(1, round(PAIR_WINDOW_S * fps))
    frames = sorted(c.frame for c in cuts)
    pairs = [(a, b) for a, b in itertools.pairwise(frames) if b - a <= window]
    if not pairs:
        return frozenset()

    # Read forward once and keep only the frames the pairs need. Seeking (CAP_PROP_POS_FRAMES) isn't guaranteed to land
    # on the exact frame in H.264 files, and seeking per frame is slow.
    needed = {n for a, b in pairs for n in range(a - 1, b + 1)}
    sampled: dict[int, Any] = {}
    capture = cv2.VideoCapture(str(video))
    try:
        for n in range(max(needed) + 1):
            ok, image = capture.read()
            if not ok:
                raise ValueError(f"can't read frame {n} of {video}")
            if n in needed:
                sampled[n] = cv2.resize(
                    image, SAMPLE_SIZE, interpolation=cv2.INTER_AREA
                )  # uint8: 4x less memory
    finally:
        capture.release()

    out: set[int] = set()
    for a, b in pairs:
        before, after = sampled[a - 1], sampled[b]
        between = [sampled[n] for n in range(a, b)]
        to_before = min(float(cv2.absdiff(m, before).mean()) for m in between)
        to_after = min(float(cv2.absdiff(m, after).mean()) for m in between)
        if min(to_before, to_after) < DISTINCT_MIN:
            out.update((a, b))
    return frozenset(out)


def confidence(score: float) -> float:
    """Map an adaptive ratio to 0-1: 0.5 at the threshold, rising towards 1 as the cut stands out more (0.75 at twice
    the threshold, 0.9 at five times). It never saturates, because real cuts on the golden set score 3 to 92.

    It measures how strongly a cut stands out, not whether it's real: on the golden set, split-screen false cuts score
    up to 28 and a picture-in-picture one 87, while some real cuts score 3. So it can't be the flag on its own (ADR 0003);
    a flag rule has to decide that, and `to_partial()` combines the two.
    """
    if math.isnan(score) or score <= 0:
        return 0.0
    return min(1.0, max(0.0, 1 - ADAPTIVE_THRESHOLD / (2 * score)))


def cut_confidence(cut: Cut, is_flagged: bool) -> float:
    """Strength from `confidence()`, kept at REVIEW_BELOW or more when unflagged, scaled into 0-FLAGGED_MAX when
    flagged."""
    strength = confidence(cut.score)
    if is_flagged:
        return round(FLAGGED_MAX * strength, 4)
    return round(max(REVIEW_BELOW, strength), 4)


def to_partial(detection: Detection, suspect: frozenset[int] = frozenset()) -> dict:
    """One video track with the cuts and the slots between them, for the worker base to stamp and validate.

    Slots tile the video: a boundary at every cut. A slot is only as certain as its least certain boundary cut, so a
    slot next to a flagged cut is flagged too; a video with no cuts is one slot, as certain as can be. `suspect` holds
    the frames of cuts `suspect_cuts()` marks for review, and a cut with no score is flagged too; confidence below
    REVIEW_BELOW means "needs review".
    """
    cuts = sorted(detection.cuts, key=lambda c: c.frame)
    conf = {
        c.frame: cut_confidence(c, c.frame in suspect or math.isnan(c.score))
        for c in cuts
    }
    edges = [0, *(c.frame for c in cuts), detection.frames]
    slots = []
    for i, (a, b) in enumerate(itertools.pairwise(edges)):
        bounds = [conf[f] for f in (a, b) if f in conf]
        slots.append(
            {
                "id": f"slot-{i + 1}",
                "range": {"start": a, "duration": b - a},
                "confidence": min(bounds, default=1.0),
            }
        )
    return {
        "tracks": [
            {
                "id": "video-1",
                "kind": "video",
                "slots": slots,
                "cuts": [
                    {
                        "id": f"cut-{i + 1}",
                        "frame": c.frame,
                        "confidence": conf[c.frame],
                    }
                    for i, c in enumerate(cuts)
                ],
            }
        ]
    }

"""The cut detection core, on synthetic videos with cuts at known frames, so it runs without the private golden set."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from editrace_schema import Template
from workers.cuts.detect import (
    ADAPTIVE_THRESHOLD,
    FLAGGED_MAX,
    PARAMS,
    REVIEW_BELOW,
    Cut,
    Detection,
    confidence,
    cut_confidence,
    cut_score,
    find_cuts,
    suspect_cuts,
    to_partial,
)

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
pytest.importorskip("scenedetect")

COLORS = [(40, 40, 200), (200, 60, 40), (40, 180, 60), (220, 220, 40), (150, 40, 160)]


def make_video(
    path: Path, frames: int, cuts: list[int], size: tuple[int, int] = (96, 64)
) -> Path:
    """Solid-color shots with a little moving texture, changing color at each cut frame."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, size)
    rng = np.random.default_rng(7)
    shot = 0
    for i in range(frames):
        if i in cuts:
            shot += 1
        frame = np.full((size[1], size[0], 3), COLORS[shot % len(COLORS)], np.uint8)
        noise = rng.integers(0, 6, frame.shape, dtype=np.uint8)
        writer.write(cv2.add(frame, noise))
    writer.release()
    return path


def validate(partial: dict, frames: int) -> None:
    """Stamp provenance as the worker base does, wrap in a template, and check it against the schema models."""
    for track in partial["tracks"]:
        for element in track["slots"] + track["cuts"]:
            element["provenance"] = {"detector": "cuts", "version": "0.1.0"}
    Template.model_validate(
        {
            "schema_version": "0.1.0",
            "source": {
                "duration": frames,
                "fps": {"num": 30, "den": 1},
                "resolution": {"width": 96, "height": 64},
                "aspect_ratio": {"num": 3, "den": 2},
                "content_hash": "sha256:" + "0" * 64,
            },
            **partial,
        }
    )


def test_cuts_land_on_the_first_frame_of_each_new_shot(tmp_path: Path) -> None:
    video = make_video(tmp_path / "three_cuts.mp4", 100, [20, 45, 70])
    detection = find_cuts(video)
    assert detection.frames == 100
    assert [c.frame for c in detection.cuts] == [20, 45, 70]


def test_the_detector_runs_with_the_settings_in_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`PARAMS` is the cache key, so a setting it records but `find_cuts()` doesn't pass would serve wrong results."""
    import scenedetect

    seen: list[dict] = []
    real = scenedetect.AdaptiveDetector

    def spy(**kwargs):
        seen.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(scenedetect, "AdaptiveDetector", spy)
    find_cuts(make_video(tmp_path / "one_cut.mp4", 40, [20]))
    assert seen == [
        {
            "adaptive_threshold": PARAMS["adaptive_threshold"],
            "min_scene_len": PARAMS["min_scene_len"],
            "window_width": PARAMS["window_width"],
        }
    ]


def test_partial_has_slots_tiling_the_video_and_validates(tmp_path: Path) -> None:
    video = make_video(tmp_path / "three_cuts.mp4", 100, [20, 45, 70])
    partial = to_partial(find_cuts(video))
    (track,) = partial["tracks"]
    assert [s["range"] for s in track["slots"]] == [
        {"start": 0, "duration": 20},
        {"start": 20, "duration": 25},
        {"start": 45, "duration": 25},
        {"start": 70, "duration": 30},
    ]
    assert [c["id"] for c in track["cuts"]] == ["cut-1", "cut-2", "cut-3"]
    assert all(0 <= e["confidence"] <= 1 for e in track["slots"] + track["cuts"])
    validate(partial, 100)


def test_a_video_without_cuts_is_one_certain_slot(tmp_path: Path) -> None:
    video = make_video(tmp_path / "one_shot.mp4", 60, [])
    partial = to_partial(find_cuts(video))
    (track,) = partial["tracks"]
    assert track["cuts"] == []
    assert track["slots"] == [
        {"id": "slot-1", "range": {"start": 0, "duration": 60}, "confidence": 1.0}
    ]
    validate(partial, 60)


def test_a_slot_is_as_certain_as_its_weakest_boundary() -> None:
    weak, strong = ADAPTIVE_THRESHOLD, 5 * ADAPTIVE_THRESHOLD
    partial = to_partial(
        Detection([Cut(40, strong), Cut(10, weak)], 60)
    )  # out of order on purpose
    slots = partial["tracks"][0]["slots"]
    assert [s["confidence"] for s in slots] == [0.5, 0.5, 0.9]
    assert [c["frame"] for c in partial["tracks"][0]["cuts"]] == [10, 40]


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (ADAPTIVE_THRESHOLD, 0.5),
        (2 * ADAPTIVE_THRESHOLD, 0.75),
        (5 * ADAPTIVE_THRESHOLD, 0.9),
        (math.inf, 1.0),
        (math.nan, 0.0),
        (0.0, 0.0),
    ],
)
def test_confidence(score: float, expected: float) -> None:
    assert confidence(score) == pytest.approx(expected)


def test_nothing_is_flagged_without_a_rule() -> None:
    """Close cuts alone aren't suspect: v06 has real cuts 2-3 frames apart, and a flash is two real cuts 1-3 apart."""
    montage = [Cut(f, 3 * ADAPTIVE_THRESHOLD) for f in (4, 7, 9, 12, 15, 18)]
    track = to_partial(Detection(montage, 60))["tracks"][0]
    assert all(e["confidence"] >= REVIEW_BELOW for e in track["cuts"] + track["slots"])


def test_suspect_cuts_and_their_sliver_sit_below_the_review_line() -> None:
    lone, a, b = Cut(30, 10.0), Cut(100, 10.0), Cut(105, 10.0)
    track = to_partial(Detection([lone, a, b], 200), suspect=frozenset({100, 105}))[
        "tracks"
    ][0]
    conf = {c["frame"]: c["confidence"] for c in track["cuts"]}
    assert conf[30] >= REVIEW_BELOW
    assert conf[100] < REVIEW_BELOW and conf[105] < REVIEW_BELOW
    assert max(conf[100], conf[105]) <= FLAGGED_MAX
    slots = {s["range"]["start"]: s["confidence"] for s in track["slots"]}
    assert slots[100] < REVIEW_BELOW  # the 5-frame sliver between the two flagged cuts
    assert slots[0] >= REVIEW_BELOW


def test_unflagged_cuts_never_fall_below_the_line() -> None:
    weak = Cut(50, math.nan)  # a missing ratio maps to strength 0
    assert cut_confidence(weak, is_flagged=False) == REVIEW_BELOW
    assert cut_confidence(Cut(50, math.inf), is_flagged=True) == FLAGGED_MAX


def test_a_missing_ratio_is_the_least_certain_cut_not_the_most() -> None:
    assert math.isnan(cut_score(None))
    assert cut_score(3.5) == 3.5
    partial = to_partial(Detection([Cut(50, cut_score(None))], 100))
    assert (
        partial["tracks"][0]["cuts"][0]["confidence"] == 0.0
    )  # flagged, not passed at the line


A, B, C, WHITE = (40, 40, 200), (200, 60, 40), (40, 180, 60), (255, 255, 255)


def write_frames(path: Path, colors: list[tuple[int, int, int]]) -> Path:
    """A video with one solid colour per frame (plus a little noise), for the content check."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (96, 64))
    rng = np.random.default_rng(3)
    for color in colors:
        frame = np.full((64, 96, 3), color, np.uint8)
        writer.write(cv2.add(frame, rng.integers(0, 6, frame.shape, dtype=np.uint8)))
    writer.release()
    return path


def cuts_at(*frames: int) -> list[Cut]:
    return [Cut(f, 3 * ADAPTIVE_THRESHOLD) for f in frames]


def test_a_duplicate_detection_is_flagged(tmp_path: Path) -> None:
    # One change from A to B, reported at 30 and again at 33: the frames between are already B.
    video = write_frames(tmp_path / "dup.mp4", [A] * 30 + [B] * 30)
    assert suspect_cuts(video, cuts_at(30, 33), fps=30) == {30, 33}


def test_a_real_fast_cut_is_left_alone(tmp_path: Path) -> None:
    # A 3-frame shot of its own (C) between A and B: two real cuts, however close.
    video = write_frames(tmp_path / "fast.mp4", [A] * 30 + [C] * 3 + [B] * 27)
    assert suspect_cuts(video, cuts_at(30, 33), fps=30) == frozenset()


def test_a_flash_is_left_alone(tmp_path: Path) -> None:
    # A one-frame white flash between two shots: two real cuts one frame apart (LABELING.md rule 3).
    video = write_frames(tmp_path / "flash.mp4", [A] * 30 + [WHITE] + [B] * 29)
    assert suspect_cuts(video, cuts_at(30, 31), fps=30) == frozenset()


def test_the_pair_window_is_in_seconds(tmp_path: Path) -> None:
    # Detections 15 frames apart with nothing new between them: 0.5 s at 30 fps is checked, 0.63 s at 24 fps isn't.
    video = write_frames(tmp_path / "far.mp4", [A] * 30 + [B] * 40)
    assert suspect_cuts(video, cuts_at(30, 45), fps=30) == {30, 45}
    assert suspect_cuts(video, cuts_at(30, 45), fps=24000 / 1001) == frozenset()
    assert suspect_cuts(video, cuts_at(10, 60), fps=30) == frozenset()

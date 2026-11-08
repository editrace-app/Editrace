"""Text overlay detection core for the OCR worker (#13): video in, on-screen text with in/out frames out.

Two passes, because OCR on one keyframe per shot misses text that changes inside a shot (see the note on #13):

1. A coarse pass reads every `SAMPLE_STEP_S` seconds and links readings across samples into tracks.
2. An edge pass pins each track's first and last frame by probing the frames between the samples, so the in/out times
   aren't limited to the sampling interval. Each edge takes about log2(step) probes of a small crop.

Everything here is pure except `PaddleReader` and `read_video`, so the tracking, edge search, styling and output are
tested without Paddle or a GPU. The worker (worker.py) wires them together; the base adds provenance and validates.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

import numpy as np

# Any change here changes the output, so the worker reports these as its params (its cache key).
SAMPLE_STEP_S = 0.25  # coarse pass: 4 readings per second
FRAME_HEIGHT = 720  # frames are shrunk to this before OCR; boxes are normalized, so it doesn't leak into the output
MIN_CONFIDENCE = 0.6  # a reading below this is noise
MIN_TEXT_CHARS = 2  # single glyphs are mostly logos and UI furniture
LINK_IOU = 0.5  # a reading continues a track if its box overlaps the track's last box this much...
LINK_TEXT_SIMILARITY = (
    0.8  # ...or if the text is this similar and the boxes still touch
)
MAX_MISSED_SAMPLES = (
    1  # OCR flickers: a track survives this many empty samples before it closes
)
PROBE_PAD = 0.02  # fraction of the frame added around a track's box when probing for it
MIN_TRACK_SAMPLES = (
    2  # a text seen in one sample only is as likely a flash of noise as an overlay
)
PARAMS: dict[str, Any] = {
    "detector": "paddleocr.PP-OCRv5_mobile",
    "sample_step_s": SAMPLE_STEP_S,
    "frame_height": FRAME_HEIGHT,
    "min_confidence": MIN_CONFIDENCE,
    "min_text_chars": MIN_TEXT_CHARS,
    "link_iou": LINK_IOU,
    "link_text_similarity": LINK_TEXT_SIMILARITY,
    "max_missed_samples": MAX_MISSED_SAMPLES,
    "min_track_samples": MIN_TRACK_SAMPLES,
}


@dataclass(frozen=True)
class Reading:
    """One piece of text OCR found in one frame. The box is normalized: x, y top-left; w, h size; all 0-1."""

    text: str
    confidence: float
    x: float
    y: float
    w: float
    h: float

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)


class Reader(Protocol):
    """Finds text in an image (height x width x 3, BGR). The Paddle implementation is `PaddleReader`; tests use fakes."""

    def read(self, image: np.ndarray) -> list[Reading]: ...


@dataclass
class Track:
    """One piece of text followed through the video. `first` and `last` are the first and last frames it was seen in;
    `readings` holds the per-sample readings it was built from."""

    first: int
    last: int
    readings: list[Reading] = field(default_factory=list)
    missed: int = 0

    @property
    def text(self) -> str:
        """The reading seen most often, weighted by confidence: OCR wobbles by a letter between samples."""
        votes: dict[str, float] = {}
        for r in self.readings:
            votes[r.text] = votes.get(r.text, 0.0) + r.confidence
        return max(votes, key=lambda k: votes[k])

    @property
    def confidence(self) -> float:
        return sum(r.confidence for r in self.readings) / len(self.readings)

    @property
    def box(self) -> tuple[float, float, float, float]:
        """The median box, so a sample where the text was half-faded in doesn't skew it."""
        cols = list(zip(*(r.box for r in self.readings), strict=True))
        x, y, w, h = (float(np.median(c)) for c in cols)
        return _clamp_box(x, y, w, h)


def _clamp_box(
    x: float, y: float, w: float, h: float
) -> tuple[float, float, float, float]:
    x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
    w, h = min(max(w, 1e-6), 1.0 - x or 1e-6), min(max(h, 1e-6), 1.0 - y or 1e-6)
    return (x, y, w, h)


def iou(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio()


def keep_reading(r: Reading) -> bool:
    return r.confidence >= MIN_CONFIDENCE and len(r.text.strip()) >= MIN_TEXT_CHARS


def _continues(track: Track, r: Reading) -> bool:
    last = track.readings[-1]
    overlap = iou(last.box, r.box)
    return overlap >= LINK_IOU or (
        overlap > 0 and similar(last.text, r.text) >= LINK_TEXT_SIMILARITY
    )


def link(samples: Sequence[tuple[int, list[Reading]]]) -> list[Track]:
    """Link per-sample readings into tracks. `samples` is (frame, readings) in time order. A reading continues the
    open track it overlaps most, if any; otherwise it starts one. Each track takes at most one reading per sample."""
    done: list[Track] = []
    open_: list[Track] = []
    for frame, readings in samples:
        taken: set[int] = set()
        for r in (r for r in readings if keep_reading(r)):
            best, best_score = None, 0.0
            for i, t in enumerate(open_):
                if i in taken or not _continues(t, r):
                    continue
                score = iou(t.readings[-1].box, r.box)
                if best is None or score > best_score:
                    best, best_score = i, score
            if best is None:
                open_.append(Track(first=frame, last=frame, readings=[r]))
                taken.add(len(open_) - 1)
            else:
                t = open_[best]
                t.readings.append(r)
                t.last, t.missed = frame, 0
                taken.add(best)
        still_open: list[Track] = []
        for i, t in enumerate(open_):
            if i not in taken:
                t.missed += 1
            (done if t.missed > MAX_MISSED_SAMPLES else still_open).append(t)
        open_ = still_open
    return [t for t in done + open_ if len(t.readings) >= MIN_TRACK_SAMPLES]


Probe = Callable[[int, Track], bool]
"""Is this track's text on screen at this frame?"""


def _first_true(lo: int, hi: int, present: Callable[[int], bool]) -> int:
    """The smallest frame in [lo, hi] where `present` holds, assuming it is false before some frame and true from there
    on, and true at `hi`. Binary search: about log2(hi - lo) probes."""
    while lo < hi:
        mid = (lo + hi) // 2
        if present(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


def refine(
    tracks: Sequence[Track], probe: Probe, step: int, frames: int
) -> list[tuple[Track, int, int]]:
    """Pin each track's start and end between the samples. Returns (track, start, end_exclusive) per track.

    The text was seen at `first` but not at `first - step`, so it appeared somewhere in between; likewise it ended
    somewhere in (last, last + step]. A track at the very start or end of the video has nothing to search."""
    out: list[tuple[Track, int, int]] = []
    for t in tracks:
        start = t.first
        if t.first > 0:
            start = _first_true(
                max(t.first - step + 1, 0), t.first, lambda f, t=t: probe(f, t)
            )
        end = t.last + 1
        if t.last + 1 < frames:
            # The first frame at or after `last` where it is gone, searched up to one step further on.
            hi = min(t.last + step, frames - 1)
            end = _first_true(
                t.last + 1, hi + 1, lambda f, t=t, hi=hi: f > hi or not probe(f, t)
            )
        out.append((t, start, max(end, start + 1)))
    return out


# --- style ---------------------------------------------------------------------------------------------------------


def _hex(bgr: np.ndarray) -> str:
    b, g, r = (round(float(c)) for c in bgr)
    return f"#{r:02x}{g:02x}{b:02x}"


def style_hints(
    image: np.ndarray, box: tuple[float, float, float, float]
) -> dict[str, Any]:
    """Colour and size hints from one frame, as the schema's TextStyle. Font family and weight aren't guessed: nothing
    here measures them, and a wrong guess misleads an editor more than a missing hint (the fields are optional)."""
    h_img, w_img = image.shape[:2]
    x, y, w, h = box
    x0, y0 = int(x * w_img), int(y * h_img)
    x1, y1 = max(x0 + 1, int((x + w) * w_img)), max(y0 + 1, int((y + h) * h_img))
    crop = image[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
    style: dict[str, Any] = {"font_size": round(min(h, 1.0) * 0.7, 4)}
    if len(crop) < 4:
        return style
    # Two-colour split by brightness around the crop's own mean: the minority colour is the text, the majority the
    # background. Crude, but it needs no model and holds for the usual white-on-footage or black-on-box titles.
    brightness = crop.mean(axis=1)
    dark = brightness < brightness.mean()
    if dark.all() or not dark.any():
        return style
    groups = (crop[dark], crop[~dark])
    text_group, bg_group = sorted(groups, key=len)
    style["color"] = _hex(text_group.mean(axis=0))
    # A background box shows as a flat colour: low spread among the majority pixels.
    if bg_group.std(axis=0).mean() < 18:
        style["background_color"] = _hex(bg_group.mean(axis=0))
    cx = x + w / 2
    style["alignment"] = (
        "center" if abs(cx - 0.5) < 0.1 else ("left" if cx < 0.5 else "right")
    )
    return style


# --- output --------------------------------------------------------------------------------------------------------


def to_partial(
    spans: Sequence[tuple[Track, int, int]],
    styles: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """The partial template: one text track holding every overlay, in time order. No overlays still gives a valid text
    track (an empty list), so "looked and found none" is distinguishable from "didn't run"."""
    overlays = []
    order = sorted(range(len(spans)), key=lambda i: (spans[i][1], spans[i][0].box[1]))
    for n, i in enumerate(order, start=1):
        track, start, end = spans[i]
        x, y, w, h = track.box
        overlay: dict[str, Any] = {
            "id": f"text-{n}",
            "text": track.text,
            "bbox": {
                "x": round(x, 5),
                "y": round(y, 5),
                "w": round(w, 5),
                "h": round(h, 5),
            },
            "range": {"start": start, "duration": end - start},
            "confidence": round(min(max(track.confidence, 0.0), 1.0), 4),
        }
        if styles[i]:
            overlay["style"] = styles[i]
        overlays.append(overlay)
    return {"tracks": [{"id": "text-1", "kind": "text", "text_overlays": overlays}]}


def drop_caption_duplicates(
    overlays: Sequence[dict[str, Any]],
    caption_words: Sequence[tuple[str, int, int]],
    min_similarity: float = 0.8,
) -> list[dict[str, Any]]:
    """Remove overlays that are really captions (#12 owns those). `caption_words` is (word, start, end_exclusive) in
    frames. An overlay is a caption duplicate when most of its words are caption words spoken while it is on screen.
    Called by whoever merges the two partials; the worker itself can't see captions."""
    kept = []
    for o in overlays:
        start = o["range"]["start"]
        end = start + o["range"]["duration"]
        words = o["text"].split()
        live = [w for w, s, e in caption_words if s < end and e > start]
        hits = sum(
            1 for w in words if any(similar(w, c) >= min_similarity for c in live)
        )
        if words and hits / len(words) >= 0.5:
            continue
        kept.append(o)
    return kept


# --- Paddle and video I/O ------------------------------------------------------------------------------------------


class PaddleReader:
    """PaddleOCR PP-OCRv5 mobile models on the GPU when Paddle has one. Loaded on first use so importing this module
    stays free."""

    def __init__(self, device: str = "gpu") -> None:
        self.device = device
        self._ocr: Any = None

    def _load(self) -> Any:
        if self._ocr is None:
            from paddleocr import PaddleOCR  # pyright: ignore[reportMissingImports]  # not in the root lock

            self._ocr = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device=self.device,
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
            )
        return self._ocr

    def read(self, image: np.ndarray) -> list[Reading]:
        h, w = image.shape[:2]
        result = self._load().predict(image)[0]
        readings = []
        for text, score, box in zip(
            result["rec_texts"], result["rec_scores"], result["rec_boxes"], strict=True
        ):
            x0, y0, x1, y1 = (float(v) for v in box)
            readings.append(
                Reading(
                    text=str(text).strip(),
                    confidence=float(score),
                    x=x0 / w,
                    y=y0 / h,
                    w=(x1 - x0) / w,
                    h=(y1 - y0) / h,
                )
            )
        return readings


def shrink(image: np.ndarray, height: int = FRAME_HEIGHT) -> np.ndarray:
    import cv2

    h, w = image.shape[:2]
    if h <= height:
        return image
    return cv2.resize(
        image, (round(w * height / h), height), interpolation=cv2.INTER_AREA
    )


def read_video(video: Path) -> tuple[Any, int, float]:
    """Open the video; returns (capture, frame count, fps). Raises if it can't be decoded."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    if not cap.isOpened() or frames <= 0 or fps <= 0:
        raise ValueError(f"can't decode {video.name}")
    return cap, frames, fps


def frame_at(cap: Any, index: int) -> np.ndarray | None:
    import cv2

    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, image = cap.read()
    return shrink(image) if ok else None


def sample_frames(frames: int, step: int) -> Iterator[int]:
    return iter(range(0, frames, step))


def sample_step(fps: float) -> int:
    return max(1, round(SAMPLE_STEP_S * fps))


@dataclass(frozen=True)
class Detection:
    spans: list[tuple[Track, int, int]]
    styles: list[dict[str, Any]]
    frames: int


def find_text(video: Path, reader: Reader) -> Detection:
    """Run both passes over `video`. Returns the tracks with their pinned frames and style hints."""
    cap, frames, fps = read_video(video)
    try:
        step = sample_step(fps)
        samples: list[tuple[int, list[Reading]]] = []
        for f in sample_frames(frames, step):
            image = frame_at(cap, f)
            samples.append((f, reader.read(image) if image is not None else []))
        tracks = link(samples)

        def probe(f: int, t: Track) -> bool:
            image = frame_at(cap, f)
            if image is None:
                return False
            x, y, w, h = t.box
            x0, y0 = max(x - PROBE_PAD, 0.0), max(y - PROBE_PAD, 0.0)
            x1, y1 = min(x + w + PROBE_PAD, 1.0), min(y + h + PROBE_PAD, 1.0)
            ih, iw = image.shape[:2]
            crop = image[
                int(y0 * ih) : math.ceil(y1 * ih), int(x0 * iw) : math.ceil(x1 * iw)
            ]
            return any(
                keep_reading(r) and similar(r.text, t.text) >= LINK_TEXT_SIMILARITY
                for r in reader.read(crop)
            )

        spans = refine(tracks, probe, step, frames)
        styles = []
        for t, start, end in spans:
            mid = frame_at(cap, (start + end) // 2)
            styles.append(style_hints(mid, t.box) if mid is not None else {})
        return Detection(spans, styles, frames)
    finally:
        cap.release()


def library_versions() -> dict[str, str]:
    """Installed versions of the libraries that decide the output, so upgrading one re-analyzes instead of serving a
    stale cache. Paddle ships as `paddlepaddle` (CPU) or `paddlepaddle-gpu`; whichever is installed is reported."""
    from importlib.metadata import PackageNotFoundError, version

    found = {}
    for name in (
        "paddleocr",
        "paddlepaddle",
        "paddlepaddle-gpu",
        "opencv-python",
        "numpy",
    ):
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            pass
    return found

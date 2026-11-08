"""OCR core (#13) on synthetic videos. A fake reader stands in for Paddle, so these run anywhere; the real engine is
exercised in `test_paddle.py` when it is installed."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")
import cv2

from editrace_schema import Template
from workers.ocr.detect import (
    Reading,
    drop_caption_duplicates,
    find_text,
    link,
    refine,
    style_hints,
    to_partial,
)

FPS, SIZE = 30, (160, 90)  # width, height; small so the tests are fast


def make_video(path: Path, frames: int, shows: dict[str, tuple[int, int]]) -> Path:
    """Dark frames; each entry draws a bright block, standing in for text, over [start, end) frames at a fixed place."""
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),  # type: ignore[attr-defined]
        FPS,
        SIZE,
    )
    for i in range(frames):
        frame = np.full((SIZE[1], SIZE[0], 3), 20, np.uint8)
        for name, (start, end) in shows.items():
            if start <= i < end:
                y = 10 if name == "TOP" else 60
                frame[y : y + 12, 40:120] = (255, 255, 255)
        writer.write(frame)
    writer.release()
    return path


class BrightBlockReader:
    """Reads a bright block as text. Which text depends on the half of the image, so two blocks are two overlays."""

    def __init__(self) -> None:
        self.calls = 0

    def read(self, image: np.ndarray) -> list[Reading]:
        self.calls += 1
        h, w = image.shape[:2]
        found = []
        for name, lo, hi in (("TOP", 0.0, 0.5), ("LOW", 0.5, 1.0)):
            offset = int(lo * h)
            band = image[offset : int(hi * h)].mean(axis=2)
            ys, xs = np.where(band > 200)
            if len(xs) > 20:
                y0, y1 = ys.min() + offset, ys.max() + offset + 1
                x0, x1 = xs.min(), xs.max() + 1
                found.append(
                    Reading(name, 0.95, x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h)
                )
        return found


def validate(partial: dict, frames: int) -> None:
    for track in partial["tracks"]:
        for o in track["text_overlays"]:
            o["provenance"] = {"detector": "ocr", "version": "0.1.0"}
    Template.model_validate(
        {
            "schema_version": "0.1.0",
            "source": {
                "duration": frames,
                "fps": {"num": FPS, "den": 1},
                "resolution": {"width": SIZE[0], "height": SIZE[1]},
                "aspect_ratio": {"num": 16, "den": 9},
                "content_hash": "sha256:" + "0" * 64,
            },
            **partial,
        }
    )


def test_overlay_in_and_out_land_on_the_exact_frames(tmp_path: Path) -> None:
    # 8 frames per sample: the edges (43 and 97) sit between samples, so only the edge pass can find them.
    video = make_video(tmp_path / "one.mp4", 150, {"LOW": (43, 97)})
    detection = find_text(video, BrightBlockReader())
    (span,) = detection.spans
    assert (span[1], span[2]) == (43, 97)
    partial = to_partial(detection.spans, detection.styles)
    (overlay,) = partial["tracks"][0]["text_overlays"]
    assert overlay["range"] == {"start": 43, "duration": 54}
    assert overlay["text"] == "LOW"
    validate(partial, 150)


def test_edge_pass_probes_a_handful_of_frames_not_every_frame(tmp_path: Path) -> None:
    video = make_video(tmp_path / "one.mp4", 150, {"LOW": (43, 97)})
    reader = BrightBlockReader()
    find_text(video, reader)
    samples = -(-150 // 8)
    assert reader.calls <= samples + 2 * 4 + 1  # coarse pass + ~log2(8) probes per edge


def test_two_overlays_at_once_are_separate_and_ordered(tmp_path: Path) -> None:
    video = make_video(tmp_path / "two.mp4", 150, {"TOP": (30, 120), "LOW": (60, 90)})
    detection = find_text(video, BrightBlockReader())
    overlays = to_partial(detection.spans, detection.styles)["tracks"][0][
        "text_overlays"
    ]
    assert [
        (o["text"], o["range"]["start"], o["range"]["duration"]) for o in overlays
    ] == [("TOP", 30, 90), ("LOW", 60, 30)]
    assert [o["id"] for o in overlays] == ["text-1", "text-2"]


def test_overlay_running_to_the_end_of_the_video_ends_at_the_last_frame(
    tmp_path: Path,
) -> None:
    video = make_video(tmp_path / "tail.mp4", 100, {"LOW": (70, 100)})
    (span,) = find_text(video, BrightBlockReader()).spans
    assert (span[1], span[2]) == (70, 100)


def test_a_video_without_text_gives_an_empty_text_track(tmp_path: Path) -> None:
    video = make_video(tmp_path / "none.mp4", 60, {})
    detection = find_text(video, BrightBlockReader())
    partial = to_partial(detection.spans, detection.styles)
    assert partial["tracks"][0]["text_overlays"] == []
    validate(partial, 60)


def r(text: str, conf: float = 0.9, x: float = 0.2, y: float = 0.5) -> Reading:
    return Reading(text, conf, x, y, 0.4, 0.1)


def test_one_missed_sample_does_not_split_a_track() -> None:
    samples = [
        (0, [r("HI THERE")]),
        (8, []),
        (16, [r("HI THERE")]),
        (24, [r("HI THERE")]),
    ]
    (track,) = link(samples)
    assert (track.first, track.last) == (0, 24)


def test_two_missed_samples_do_close_the_track() -> None:
    samples = [
        (0, [r("HI")]),
        (8, [r("HI")]),
        (16, []),
        (24, []),
        (32, [r("HI")]),
        (40, [r("HI")]),
    ]
    assert [(t.first, t.last) for t in link(samples)] == [(0, 8), (32, 40)]


def test_a_reading_in_one_sample_only_is_noise() -> None:
    assert link([(0, [r("FLASH")]), (8, []), (16, [])]) == []


def test_low_confidence_and_single_glyph_readings_are_dropped() -> None:
    samples = [(0, [r("OK", 0.3), r("A")]), (8, [r("OK", 0.3), r("A")])]
    assert link(samples) == []


def test_text_that_wobbles_by_a_letter_is_one_track_with_the_majority_reading() -> None:
    samples = [(0, [r("HELLO")]), (8, [r("HELL0")]), (16, [r("HELLO")])]
    (track,) = link(samples)
    assert track.text == "HELLO"


def test_text_that_moves_to_a_different_place_is_a_new_track() -> None:
    samples = [
        (0, [r("ONE", y=0.1)]),
        (8, [r("ONE", y=0.1)]),
        (16, [r("TWO", y=0.7)]),
        (24, [r("TWO", y=0.7)]),
    ]
    assert [t.text for t in link(samples)] == ["ONE", "TWO"]


def test_refine_is_a_binary_search() -> None:
    (track,) = link([(48, [r("HI")]), (56, [r("HI")])])
    probes: list[int] = []

    def probe(f: int, _track: object) -> bool:
        probes.append(f)
        return 43 <= f < 60

    ((_, start, end),) = refine([track], probe, step=8, frames=200)
    assert (start, end) == (43, 60)
    assert len(probes) <= 8


def test_style_picks_up_text_colour_and_a_flat_background_box() -> None:
    image = np.full((90, 160, 3), 200, np.uint8)  # light, flat background
    image[40:50, 60:100] = (0, 0, 0)  # dark text, BGR
    style = style_hints(image, (0.3, 0.4, 0.4, 0.2))
    assert style["color"] == "#000000"
    assert style["background_color"] == "#c8c8c8"
    assert style["alignment"] == "center"
    assert 0 < style["font_size"] <= 1


def test_caption_duplicates_are_dropped_and_real_titles_kept() -> None:
    overlays = [
        {
            "id": "text-1",
            "text": "so I went to the store",
            "range": {"start": 10, "duration": 24},
        },
        {"id": "text-2", "text": "SUMMER SALE", "range": {"start": 10, "duration": 24}},
    ]
    words = [
        (w, 10 + i * 4, 14 + i * 4)
        for i, w in enumerate(["so", "I", "went", "to", "the", "store"])
    ]
    assert [o["id"] for o in drop_caption_duplicates(overlays, words)] == ["text-2"]


def test_a_caption_that_is_not_on_screen_at_the_time_does_not_dedupe() -> None:
    overlays = [
        {
            "id": "text-1",
            "text": "went to the store",
            "range": {"start": 200, "duration": 20},
        }
    ]
    assert (
        drop_caption_duplicates(overlays, [("went", 10, 14), ("to", 14, 18)])
        == overlays
    )

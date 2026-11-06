"""The real engine (#13): PaddleOCR reads rendered text on a synthetic video and the times land within ±3 frames."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("paddleocr")
import cv2

from workers.ocr.detect import PaddleReader, find_text

FPS, SIZE, START, END = 30, (640, 360), 47, 131


def make_video(path: Path) -> Path:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),  # type: ignore[attr-defined]
        FPS,
        SIZE,
    )
    rng = np.random.default_rng(0)
    for i in range(180):
        frame = np.full((SIZE[1], SIZE[0], 3), 40, np.uint8)
        frame = cv2.add(frame, rng.integers(0, 8, frame.shape, dtype=np.uint8))
        if START <= i < END:
            cv2.putText(
                frame,
                "SUMMER SALE",
                (90, 200),
                cv2.FONT_HERSHEY_DUPLEX,
                2.0,
                (255, 255, 255),
                4,
                cv2.LINE_AA,
            )
        writer.write(frame)
    writer.release()
    return path


def test_paddle_reads_the_text_and_times_it_within_three_frames(tmp_path: Path) -> None:
    detection = find_text(make_video(tmp_path / "title.mp4"), PaddleReader())
    ((track, start, end),) = detection.spans
    assert track.text.upper() == "SUMMER SALE"
    assert abs(start - START) <= 3
    assert abs(end - END) <= 3

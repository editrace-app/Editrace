"""The text overlay OCR worker (#13): PaddleOCR on the shared worker base (#53).

The base fetches and checks the video, caches by content hash, version and params, stamps provenance, validates the
partial template against the schema, stores it and reports status. This class only says what to run.

    python -m workers.run workers.ocr.worker:OcrWorker clip.mp4 --source source.json

Dedupe against captions (#12) is `detect.drop_caption_duplicates`, applied where the two partials are merged: a worker
sees only its own video, not another worker's output.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from workers.base import Compute, Job, Worker
from workers.cache import Partial
from workers.ocr.detect import (
    PARAMS,
    PaddleReader,
    Reader,
    find_text,
    library_versions,
    to_partial,
)


class OcrWorker(Worker):
    name: ClassVar[str] = "ocr"
    # Bump on any change that can change the output; it is part of the cache key, along with params().
    version: ClassVar[str] = "0.1.0"
    # PROVISIONAL until measured on Modal: the mobile PP-OCRv5 models fit a T4 comfortably.
    compute: ClassVar[Compute] = Compute(
        gpu="T4", cpu=2.0, memory_mb=8192, timeout_s=600
    )

    def __init__(self, *args: Any, reader: Reader | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reader: Reader = reader or PaddleReader()

    def params(self) -> Mapping[str, Any]:
        return {**PARAMS, "libraries": library_versions()}

    def detect(self, video: Path, job: Job) -> Partial:
        detection = find_text(video, self.reader)
        expected = job.source["duration"]
        if detection.frames != expected:
            # Overlay times are frames on the source timebase; a different count means another frame grid.
            raise ValueError(
                f"decoded {detection.frames} frames but the source says {expected}; "
                "the video and its source section don't match"
            )
        return to_partial(detection.spans, detection.styles)

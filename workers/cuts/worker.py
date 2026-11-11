"""The cut worker (#7): PySceneDetect's AdaptiveDetector on the shared worker base (#53), as decided in ADR 0003.

The base fetches and checks the video, caches by content hash, version and params, stamps provenance, validates the
partial template against the schema, stores it and reports status. This class only says what to run.

    python -m workers.run workers.cuts.worker:CutsWorker clip.mp4 --source source.json
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from workers.base import Compute, Job, Worker
from workers.cache import Partial
from workers.cuts.detect import (
    PARAMS,
    find_cuts,
    library_versions,
    suspect_cuts,
    to_partial,
)


class CutsWorker(Worker):
    name: ClassVar[str] = "cuts"
    # Bump on any change that can change the output; it is part of the cache key, along with params().
    version: ClassVar[str] = "0.2.0"
    # CPU only: AdaptiveDetector needs no GPU. PROVISIONAL until #7 measures latency and cost on Modal.
    compute: ClassVar[Compute] = Compute(cpu=2.0, memory_mb=4096, timeout_s=300)

    def params(self) -> Mapping[str, Any]:
        """The detector and flagging settings and the library versions, so changing any of them re-analyzes instead of
        serving a cached result."""
        return {**PARAMS, "libraries": library_versions()}

    def detect(self, video: Path, job: Job) -> Partial:
        detection = find_cuts(video)
        expected = job.source["duration"]
        if detection.frames != expected:
            # Slots must tile the source exactly, and a different count means the cuts sit on another frame grid.
            raise ValueError(
                f"decoded {detection.frames} frames but the source says {expected}; "
                "the video and its source section don't match"
            )
        fps = job.source["fps"]["num"] / job.source["fps"]["den"]
        return to_partial(detection, suspect_cuts(video, detection.cuts, fps))

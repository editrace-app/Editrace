"""Beat/downbeat worker (#14); sound suggestions require a separate confirmed-owner flow."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from workers.base import Compute, Job, Worker
from workers.beats.detect import (
    MAX_SECONDS,
    PARAMS,
    SAMPLE_RATE,
    decode_audio,
    library_versions,
    load_tracker,
    to_partial,
)
from workers.cache import Partial
from workers.common.log import get_logger


class BeatsWorker(Worker):
    name: ClassVar[str] = "beats"
    version: ClassVar[str] = "0.1.0"
    # Provisional CPU resources, to be measured on Modal before production rollout.
    compute: ClassVar[Compute] = Compute(cpu=2.0, memory_mb=4096, timeout_s=300)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.ffmpeg = os.environ.get("EDITRACE_FFMPEG", "ffmpeg")
        self.checkpoint = os.environ.get(
            "EDITRACE_BEATS_CHECKPOINT", "/opt/models/beat_this-final0.ckpt"
        )

    def params(self) -> Mapping[str, Any]:
        return {**PARAMS, "libraries": library_versions(self.ffmpeg)}

    def detect(self, video: Path, job: Job) -> Partial:
        fps = Fraction(job.source["fps"]["num"], job.source["fps"]["den"])
        duration = job.source["duration"]
        if duration <= 0 or fps <= 0 or Fraction(duration, 1) / fps > MAX_SECONDS:
            raise ValueError("beat analysis requires a source of 0 to 180 seconds")
        started = time.monotonic()
        cpu_started = time.process_time()
        samples = decode_audio(video, self.ffmpeg)
        try:
            # Empty tracks and digital silence have no tempo and need no model.
            if not len(samples) or not np.any(samples):
                return {}
            tracker = load_tracker(self.checkpoint)
            beats, downbeats = tracker(samples, SAMPLE_RATE)
            return to_partial(beats, downbeats, fps, duration)
        finally:
            # No PCM enters result stores/logs; clear the owned buffer on errors too.
            samples.fill(0)
            get_logger("beats", stream=sys.stderr).info(
                "beat analysis finished",
                extra={
                    "fields": {
                        "job_id": job.job_id,
                        "worker_version": self.version,
                        "duration_s": time.monotonic() - started,
                        "cpu_s": time.process_time() - cpu_started,
                        "gpu_s": 0,
                    }
                },
            )

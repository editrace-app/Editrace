"""In-memory beat analysis. The base owns video I/O; this module never writes audio.

beat_this returns seconds. Convert once to source frames, round half up, discard
out-of-range markers and deduplicate. Confidence measures grid regularity only;
it is not a calibrated probability or a claim that music is present.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import statistics
import subprocess
from collections.abc import Iterable
from fractions import Fraction
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np

SAMPLE_RATE = 22050
MAX_SECONDS = 180
CHECKPOINT_SHA256 = "8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331"
PARAMS = {
    "model": "beat_this.final0",
    "checkpoint_sha256": CHECKPOINT_SHA256,
    "sample_rate": SAMPLE_RATE,
    "max_seconds": MAX_SECONDS,
    "dbn": False,
    "device": "cpu",
    "float16": False,
    "rounding": "half_up",
    "confidence": "median_interval_relative_deviation_v1",
}


@lru_cache(maxsize=4)
def library_versions(ffmpeg: str) -> dict[str, str]:
    result = subprocess.run(
        [ffmpeg, "-version"], capture_output=True, check=True, timeout=10
    )
    return {
        **{
            name: version(name)
            for name in ("beat-this", "torch", "torchaudio", "numpy")
        },
        "ffmpeg": result.stdout.decode().splitlines()[0],
    }


def decode_audio(video: Path, ffmpeg: str) -> np.ndarray:
    """Decode at most 181 s to a bounded PCM pipe, preserving initial audio delay.

    An absent stream is empty input, whereas corrupt input and decode timeouts
    are failures. Do not include ffmpeg stderr (which can contain media metadata)
    in errors, status events or cache results.
    """
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-copyts",
                "-start_at_zero",
                "-i",
                str(video),
                "-map",
                "0:a:0?",
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-af",
                "aresample=async=1:first_pts=0",
                "-t",
                str(MAX_SECONDS + 1),
                "-f",
                "f32le",
                "pipe:1",
            ],
            capture_output=True,
            check=False,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("audio decoding exceeded 90 seconds") from None
    if result.returncode:
        if b"does not contain any stream" in result.stderr:
            return np.empty(0, dtype=np.float32)
        raise ValueError("audio decoding failed")
    samples = np.frombuffer(result.stdout, dtype="<f4").copy()
    if len(samples) > MAX_SECONDS * SAMPLE_RATE:
        raise ValueError("audio exceeds the 180 second analysis limit")
    if not np.isfinite(samples).all():
        raise ValueError("decoded audio contains non-finite samples")
    return samples


@lru_cache(maxsize=1)
def load_tracker(checkpoint: str) -> Any:
    """Use pre-provisioned final0 weights; never download during a user job."""
    path = Path(checkpoint)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != CHECKPOINT_SHA256:
        raise ValueError("beat_this final0 checkpoint checksum mismatch")
    from beat_this.inference import Audio2Beats

    return Audio2Beats(
        checkpoint_path=str(path), device="cpu", float16=False, dbn=False
    )


def to_partial(
    beats: Iterable[float], downbeats: Iterable[float], fps: Fraction, duration: int
) -> dict[str, Any]:
    """Build the schema's beat grid, or omit it if tempo cannot be estimated."""
    seconds = float(Fraction(duration, 1) / fps)

    def clean(values: Iterable[float]) -> list[float]:
        values = [float(t) for t in values]
        if not all(math.isfinite(t) for t in values):
            raise ValueError("beat tracker returned non-finite times")
        return sorted({t for t in values if 0 <= t < seconds})

    beat_times, downbeat_times = clean(beats), clean(downbeats)
    # Downbeats are beats too, even if the postprocessor omitted one from beats.
    times = sorted(set(beat_times) | set(downbeat_times))
    if len(times) < 3:
        return {}
    intervals = [b - a for a, b in itertools.pairwise(times)]
    period = statistics.median(intervals)
    bpm = 60 / period
    if not 0 < bpm <= 400:
        return {}

    def frames(values: list[float]) -> list[int]:
        rounded = {int(Fraction(str(t)) * fps + Fraction(1, 2)) for t in values}
        return sorted(f for f in rounded if 0 <= f < duration)

    beat_frames, downbeat_frames = frames(times), frames(downbeat_times)
    if len(beat_frames) < 3:
        return {}
    # A stable grid can still be half/double tempo or a hallucination on speech.
    # Cap the heuristic below certainty; real accuracy requires hand beat labels.
    deviation = statistics.median(abs(i - period) for i in intervals) / period
    confidence = min(0.9, max(0.0, 1 - deviation))
    return {
        "beats": {
            "bpm": bpm,
            "beat_frames": beat_frames,
            "downbeat_frames": downbeat_frames,
            "confidence": confidence,
        }
    }

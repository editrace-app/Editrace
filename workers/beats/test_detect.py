"""Frame/time invariants and bounded, memory-only decoding for #14."""

import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from workers.beats import detect


def test_rational_fps_rounding_bounds_duplicates_and_downbeat_membership():
    result = detect.to_partial(
        [-1, 0, 0.5, 1, 1, 1.5, 2, 99], [0, 1.5], Fraction(30000, 1001), 60
    )["beats"]
    assert result["beat_frames"] == [0, 15, 30, 45]
    assert result["downbeat_frames"] == [0, 45]
    assert result["bpm"] == 120
    assert result["confidence"] == 0.9
    assert detect.to_partial([0, 0.5, 1], [], Fraction(25), 26)["beats"][
        "beat_frames"
    ] == [0, 13, 25]


def test_downbeats_are_included_and_rounded_past_end_is_removed():
    result = detect.to_partial([0, 1, 2, 2.999], [0.5], Fraction(30), 90)["beats"]
    assert result["beat_frames"] == [0, 15, 30, 60]
    assert result["downbeat_frames"] == [15]


@pytest.mark.parametrize("beats", [[], [0], [0, 1], [0, 0.01, 0.02]])
def test_no_grid_without_enough_distinct_beats_or_valid_tempo(beats):
    assert detect.to_partial(beats, [], Fraction(30), 90) == {}


def test_collapsed_frames_are_not_a_grid():
    assert detect.to_partial([0, 0.2, 0.4], [], Fraction(1), 1) == {}


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
def test_bad_model_times_fail(invalid):
    with pytest.raises(ValueError, match="non-finite"):
        detect.to_partial([0, invalid], [], Fraction(30), 90)


def test_irregular_grid_has_lower_heuristic_confidence():
    result = detect.to_partial([0, 0.2, 1, 1.2, 2], [], Fraction(30), 90)
    assert result["beats"]["confidence"] == pytest.approx(0.4)


def test_decoder_only_outputs_pcm_to_a_bounded_pipe(monkeypatch):
    def run(command, **kwargs):
        assert command[-3:] == ["-f", "f32le", "pipe:1"]
        assert command[command.index("-t") + 1] == "181"
        assert "aresample=async=1:first_pts=0" in command
        assert kwargs["timeout"] == 90
        return subprocess.CompletedProcess(
            command, 0, np.array([0.25, -0.5], dtype="<f4").tobytes(), b""
        )

    monkeypatch.setattr(detect.subprocess, "run", run)
    samples = detect.decode_audio(Path("video"), "ffmpeg")
    assert samples.tolist() == [0.25, -0.5]
    samples.fill(0)  # must own a writable buffer, not an immutable pipe view


@pytest.mark.parametrize(
    "error,empty",
    [(b"Output file does not contain any stream", True), (b"private metadata", False)],
)
def test_absent_audio_vs_corrupt_media(monkeypatch, error, empty):
    monkeypatch.setattr(
        detect.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", error),
    )
    if empty:
        assert not len(detect.decode_audio(Path("video"), "ffmpeg"))
    else:
        with pytest.raises(ValueError, match="^audio decoding failed$"):
            detect.decode_audio(Path("video"), "ffmpeg")


def test_timeout_does_not_leak_stderr(monkeypatch):
    def run(*a, **k):
        raise subprocess.TimeoutExpired("ffmpeg", 90, stderr=b"private metadata")

    monkeypatch.setattr(detect.subprocess, "run", run)
    with pytest.raises(ValueError, match="^audio decoding exceeded 90 seconds$"):
        detect.decode_audio(Path("video"), "ffmpeg")


@pytest.mark.parametrize(
    "samples,error",
    [
        (np.array([np.nan], dtype="<f4"), "non-finite"),
        (np.zeros((detect.MAX_SECONDS * detect.SAMPLE_RATE) + 1, dtype="<f4"), "limit"),
    ],
)
def test_decoded_audio_validation(monkeypatch, samples, error):
    monkeypatch.setattr(
        detect.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, samples.tobytes(), b""),
    )
    with pytest.raises(ValueError, match=error):
        detect.decode_audio(Path("video"), "ffmpeg")


def test_checkpoint_is_local_and_checksum_verified(tmp_path):
    with pytest.raises(FileNotFoundError):
        detect.load_tracker(str(tmp_path / "missing.ckpt"))
    checkpoint = tmp_path / "wrong.ckpt"
    checkpoint.write_bytes(b"not the pinned weights")
    with pytest.raises(ValueError, match="checksum mismatch"):
        detect.load_tracker(str(checkpoint))


@pytest.mark.parametrize("audio", ["none", "silent", "delayed"])
def test_real_decoder_handles_absent_silent_and_delayed_audio(tmp_path, audio):
    ffmpeg = os.environ.get("EDITRACE_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg not installed")
    assert ffmpeg is not None
    video = tmp_path / "clip.mp4"
    command = [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=s=96x64:r=30:d=3"]
    if audio == "silent":
        command += ["-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono:d=3"]
    elif audio == "delayed":
        command += [
            "-itsoffset",
            "1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
        ]
    subprocess.run([*command, "-c:v", "mpeg4", "-c:a", "aac", str(video)], check=True)
    samples = detect.decode_audio(video, ffmpeg)
    if audio == "none":
        assert samples.size == 0
    elif audio == "silent":
        assert samples.size >= 3 * detect.SAMPLE_RATE
        assert not samples.any()
    else:
        assert samples.size >= 1.9 * detect.SAMPLE_RATE
        assert np.max(np.abs(samples[: int(0.8 * detect.SAMPLE_RATE)])) < 1e-5
        assert np.max(np.abs(samples[int(1.1 * detect.SAMPLE_RATE) :])) > 0.01

"""Schema/cache/privacy contracts on the actual shared worker lifecycle."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from workers.base import Job
from workers.beats import worker as module
from workers.beats.worker import BeatsWorker
from workers.cache import FileResultStore
from workers.storage import LocalFetcher


@pytest.fixture
def setup(tmp_path, monkeypatch):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"test video; injected decoder")
    source = {
        "duration": 90,
        "fps": {"num": 30, "den": 1},
        "resolution": {"width": 96, "height": 64},
        "aspect_ratio": {"num": 3, "den": 2},
        "content_hash": "sha256:" + hashlib.sha256(video.read_bytes()).hexdigest(),
    }
    store = FileResultStore(tmp_path / "cache")
    monkeypatch.setattr(module, "library_versions", lambda _: {"test": "1"})
    return (
        BeatsWorker(store, LocalFetcher()),
        Job("beats-job", str(video), source),
        store,
    )


def test_run_stamps_validates_caches_and_retains_only_features(
    setup, monkeypatch, tmp_path, capsys
):
    worker, job, store = setup
    audio = np.array([0.314159, -0.271828], dtype=np.float32)
    calls = []
    temporary_videos = []

    def decode(video, ffmpeg):
        assert video.read_bytes() == Path(job.video).read_bytes()
        temporary_videos.append(video)
        return audio

    def tracker(samples, sr):
        assert sr == 22050 and samples is audio
        calls.append(sr)
        return np.array([0, 0.5, 1, 1.5, 2, 2.5]), np.array([0, 2])

    monkeypatch.setattr(module, "decode_audio", decode)
    monkeypatch.setattr(module, "load_tracker", lambda _: tracker)
    result = worker.run(job)
    assert result.partial == {
        "beats": {
            "bpm": 120,
            "beat_frames": [0, 15, 30, 45, 60, 75],
            "downbeat_frames": [0, 60],
            "confidence": 0.9,
            "provenance": {"detector": "beats", "version": "0.1.0"},
        }
    }
    assert not audio.any()
    assert all(not p.parent.exists() for p in temporary_videos)
    assert worker.run(job).cached and len(calls) == 1
    assert all(p.suffix == ".json" for p in store.root.rglob("*") if p.is_file())
    saved = "".join(p.read_text() for p in store.root.rglob("*.json"))
    assert "0.314159" not in saved and "sound_ref" not in saved
    captured = capsys.readouterr()
    logs = captured.out + captured.err
    assert "0.314159" not in logs


@pytest.mark.parametrize(
    "samples", [np.empty(0, dtype=np.float32), np.zeros(8, dtype=np.float32)]
)
def test_no_audio_or_silence_skips_model_and_returns_empty_partial(
    setup, monkeypatch, samples
):
    worker, job, _ = setup
    monkeypatch.setattr(module, "decode_audio", lambda *a: samples)
    monkeypatch.setattr(
        module, "load_tracker", lambda _: pytest.fail("no model needed")
    )
    assert worker.run(job).partial == {}


def test_failed_inference_clears_audio_and_does_not_cache(setup, monkeypatch):
    worker, job, store = setup
    audio = np.ones(50, dtype=np.float32)
    monkeypatch.setattr(module, "decode_audio", lambda *a: audio)

    def fail(*args):
        raise ValueError("model failed")

    monkeypatch.setattr(module, "load_tracker", lambda _: fail)
    with pytest.raises(ValueError, match="model failed"):
        worker.run(job)
    assert not audio.any()
    assert store.get(worker.key_for(job)) is None


def test_cap_rejects_before_decoding(setup, monkeypatch):
    worker, job, _ = setup
    job.source["duration"] = 5401
    monkeypatch.setattr(module, "decode_audio", lambda *a: pytest.fail("over cap"))
    with pytest.raises(ValueError, match="180 seconds"):
        worker.run(job)


def test_cache_changes_with_detector_params(setup, monkeypatch):
    worker, job, _ = setup
    before = worker.key_for(job)
    monkeypatch.setitem(module.PARAMS, "checkpoint_sha256", "changed")
    assert before != worker.key_for(job)


def test_real_ffmpeg_worker_lifecycle_on_tiny_audio_video(setup, monkeypatch, tmp_path):
    worker, job, _ = setup
    ffmpeg = os.environ.get("EDITRACE_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg not installed")
    assert ffmpeg is not None
    video = tmp_path / "tone.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=96x64:r=30:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        check=True,
    )
    job.source["content_hash"] = (
        "sha256:" + hashlib.sha256(video.read_bytes()).hexdigest()
    )
    job = Job(job.job_id, str(video), job.source)
    worker.ffmpeg = ffmpeg
    captured = []

    def tracker(samples, sr):
        assert samples.size > 22050 * 2 and np.any(samples)
        captured.append(samples)
        return [0, 0.5, 1, 1.5, 2], [0, 2]

    monkeypatch.setattr(module, "load_tracker", lambda _: tracker)
    result = worker.run(job)
    assert result.partial["beats"]["bpm"] == 120
    assert captured and not captured[0].any()
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "cache",
        "input.mp4",
        "tone.mp4",
    ]
    assert json.dumps(result.partial).find("audio") == -1

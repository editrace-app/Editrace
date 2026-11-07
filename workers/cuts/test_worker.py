"""The cut worker on the shared base (#53), end to end on synthetic videos: fetch, detect, stamp, validate, cache."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from workers.base import Job
from workers.cache import FileResultStore, Partial
from workers.cuts.detect import PARAMS
from workers.cuts.test_detect import make_video
from workers.cuts.worker import CutsWorker
from workers.storage import LocalFetcher

pytest.importorskip("scenedetect")


class Counting(CutsWorker):
    """The cut worker, counting how often the detector actually ran."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.detect_calls = 0

    def detect(self, video: Path, job: Job) -> Partial:
        self.detect_calls += 1
        return super().detect(video, job)


def source_for(video: Path, frames: int) -> dict[str, Any]:
    return {
        "duration": frames,
        "fps": {"num": 30, "den": 1},
        "resolution": {"width": 96, "height": 64},
        "aspect_ratio": {"num": 3, "den": 2},
        "content_hash": "sha256:" + hashlib.sha256(video.read_bytes()).hexdigest(),
    }


@pytest.fixture
def store(tmp_path: Path) -> FileResultStore:
    return FileResultStore(tmp_path / "cache")


def test_cuts_come_back_stamped_and_tiling_the_source(
    tmp_path: Path, store: FileResultStore
) -> None:
    video = make_video(tmp_path / "clip.mp4", 100, [20, 45, 70])
    job = Job("job-1", str(video), source_for(video, 100))
    result = CutsWorker(store, LocalFetcher()).run(job)

    (track,) = result.partial["tracks"]
    assert [c["frame"] for c in track["cuts"]] == [20, 45, 70]
    assert sum(s["range"]["duration"] for s in track["slots"]) == 100
    stamp = {"detector": "cuts", "version": CutsWorker.version}
    assert all(e["provenance"] == stamp for e in track["slots"] + track["cuts"])
    assert not result.cached


def test_a_second_run_is_a_cache_hit_that_skips_detection(
    tmp_path: Path, store: FileResultStore
) -> None:
    video = make_video(tmp_path / "clip.mp4", 60, [30])
    job = Job("job-1", str(video), source_for(video, 60))
    worker = Counting(store, LocalFetcher())
    first, second = worker.run(job), worker.run(job)
    assert worker.detect_calls == 1
    assert second.cached and second.partial == first.partial


def test_params_carry_every_setting_that_changes_the_output(
    store: FileResultStore,
) -> None:
    params = CutsWorker(store, LocalFetcher()).params()
    assert {k: v for k, v in params.items() if k != "libraries"} == PARAMS
    for setting in (
        "adaptive_threshold",
        "window_width",
        "min_scene_len",
        "flag_rule",
        "review_below",
    ):
        assert setting in params
    # Decoding can differ between library builds, so a version bump must miss the cache.
    assert set(params["libraries"]) == {"scenedetect", "opencv"}


def test_a_source_with_another_frame_count_fails_and_is_not_cached(
    tmp_path: Path, store: FileResultStore
) -> None:
    video = make_video(tmp_path / "clip.mp4", 60, [30])
    job = Job("job-1", str(video), source_for(video, 61))
    worker = Counting(store, LocalFetcher())
    with pytest.raises(ValueError, match="decoded 60 frames but the source says 61"):
        worker.run(job)
    assert worker.detect_calls == 1  # a bad source isn't worth retrying
    assert store.get(worker.key_for(job)) is None

"""The captions worker (#12): grouping transcribed words into caption lines, converting seconds to frames, and the
full `run()` pipeline through the worker base with a fake transcriber. No test here loads a real Whisper model or
runs ffmpeg -- `Transcriber` is the seam, exactly as `storage.Fetcher` is for the video download."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from workers.base import Job
from workers.cache import FileResultStore
from workers.captions import (
    CaptionsWorker,
    TranscribedWord,
    Transcription,
    WhisperTranscriber,
    _extract_audio,
    _group_into_lines,
    _line_to_caption,
    _to_frame,
    refine_word_starts,
)
from workers.storage import LocalFetcher

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
VIDEO_BYTES = b"not really a video, but the worker only needs bytes"
CONTENT_HASH = "sha256:" + hashlib.sha256(VIDEO_BYTES).hexdigest()
FPS30 = {"num": 30, "den": 1}


def word(
    text: str, start: float, end: float, probability: float = 0.9
) -> TranscribedWord:
    return TranscribedWord(text, start, end, probability)


class FakeTranscriber:
    def __init__(self, transcription: Transcription) -> None:
        self.transcription = transcription
        # The video is a temp copy the base deletes once detect() returns, so read it now, not the path.
        self.calls: list[tuple[bytes, str | None]] = []

    def transcribe(self, video: Path, language: str | None) -> Transcription:
        self.calls.append((video.read_bytes(), language))
        return self.transcription


@pytest.fixture
def source() -> dict[str, Any]:
    example = json.loads(
        (SCHEMA_DIR / "examples" / "01-simple-reel-30fps.json").read_text()
    )
    return {**example["source"], "content_hash": CONTENT_HASH}


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(VIDEO_BYTES)
    return path


@pytest.fixture
def job(source: dict[str, Any], video: Path) -> Job:
    return Job("job-1", str(video), source)


@pytest.fixture
def store(tmp_path: Path) -> FileResultStore:
    return FileResultStore(tmp_path / "cache")


# --- seconds -> frames ---------------------------------------------------------------------------------------------


def test_seconds_convert_to_the_nearest_frame() -> None:
    assert _to_frame(0.0, FPS30) == 0
    assert _to_frame(0.5, FPS30) == 15
    assert _to_frame(0.01, FPS30) == 0  # rounds down
    assert _to_frame(0.02, FPS30) == 1  # rounds up


def test_negative_seconds_clamp_to_frame_zero() -> None:
    assert _to_frame(-0.5, FPS30) == 0


# --- grouping words into lines -------------------------------------------------------------------------------------


def test_words_within_limits_form_one_line() -> None:
    words = [word("one", 0.0, 0.2), word("two", 0.2, 0.4), word("three", 0.4, 0.6)]
    assert _group_into_lines(words, max_words=6, max_chars=24, pause_s=0.6) == [words]


def test_a_silent_pause_starts_a_new_line() -> None:
    words = [word("one", 0.0, 0.2), word("two", 2.0, 2.2)]  # 1.8s gap
    assert _group_into_lines(words, max_words=6, max_chars=24, pause_s=0.6) == [
        [words[0]],
        [words[1]],
    ]


def test_reaching_the_word_cap_starts_a_new_line() -> None:
    words = [word(str(i), float(i), i + 0.2) for i in range(5)]
    lines = _group_into_lines(words, max_words=2, max_chars=1000, pause_s=10)
    assert [len(line) for line in lines] == [2, 2, 1]


def test_reaching_the_char_cap_starts_a_new_line() -> None:
    words = [
        word("aaaaaaaaaa", 0.0, 0.2),
        word("bbbbbbbbbb", 0.2, 0.4),
        word("c", 0.4, 0.6),
    ]
    lines = _group_into_lines(words, max_words=10, max_chars=15, pause_s=10)
    # "aaaaaaaaaa" alone is 10 chars; adding " bbbbbbbbbb" would be 21, over the cap of 15.
    assert lines == [[words[0]], [words[1], words[2]]]


def test_a_single_word_over_the_char_cap_still_gets_its_own_line() -> None:
    long_word = word("a" * 30, 0.0, 0.2)
    words = [long_word, word("next", 0.2, 0.4)]
    lines = _group_into_lines(words, max_words=10, max_chars=15, pause_s=10)
    assert lines == [[long_word], [words[1]]]


# --- a line of words -> a Caption block ----------------------------------------------------------------------------


def test_a_line_becomes_a_schema_shaped_caption_block() -> None:
    words = [word("Three", 0.4, 0.87, 0.9), word("tips", 0.87, 1.2, 0.8)]
    caption = _line_to_caption("caption-1", words, "en", FPS30)
    assert caption["id"] == "caption-1"
    assert caption["language"] == "en"
    assert caption["range"] == {"start": 12, "duration": 24}  # frame 12 to frame 36
    assert caption["lines"] == [
        {
            "words": [
                {"text": "Three", "range": {"start": 12, "duration": 14}},
                {"text": "tips", "range": {"start": 26, "duration": 10}},
            ]
        }
    ]
    assert caption["confidence"] == pytest.approx(0.85)


def test_a_zero_duration_word_still_gets_at_least_one_frame() -> None:
    words = [word("click", 1.0, 1.0)]
    caption = _line_to_caption("caption-1", words, "en", FPS30)
    assert caption["lines"][0]["words"][0]["range"]["duration"] == 1


# --- the worker, end to end through the base -------------------------------------------------------------------


def make_worker(
    store: FileResultStore, transcription: Transcription, **kwargs: Any
) -> tuple[CaptionsWorker, FakeTranscriber]:
    fake = FakeTranscriber(transcription)
    worker = CaptionsWorker(store, LocalFetcher(), transcriber=fake, **kwargs)
    return worker, fake


def test_no_speech_produces_no_tracks(store: FileResultStore, job: Job) -> None:
    worker, _ = make_worker(store, Transcription("en", []))
    assert worker.run(job).partial == {}


def test_transcribed_words_land_on_a_valid_captions_track(
    store: FileResultStore, job: Job
) -> None:
    words = [word("Hello", 0.0, 0.3, 0.95), word("world", 0.3, 0.7, 0.9)]
    worker, fake = make_worker(store, Transcription("en", words))
    partial = worker.run(job).partial

    assert fake.calls == [(VIDEO_BYTES, None)]
    assert len(partial["tracks"]) == 1
    track = partial["tracks"][0]
    assert track["id"] == "captions-1" and track["kind"] == "captions"
    assert len(track["captions"]) == 1
    caption = track["captions"][0]
    assert caption["provenance"] == {"detector": "captions", "version": "0.2.0"}
    assert [w["text"] for w in caption["lines"][0]["words"]] == ["Hello", "world"]
    # CaptionWord has no confidence field, so the base must not stamp provenance onto it.
    assert "provenance" not in caption["lines"][0]["words"][0]


def test_a_pause_produces_two_caption_blocks(store: FileResultStore, job: Job) -> None:
    words = [word("Hello", 0.0, 0.3), word("world", 5.0, 5.3)]
    worker, _ = make_worker(store, Transcription("en", words))
    track = worker.run(job).partial["tracks"][0]
    assert len(track["captions"]) == 2


def test_the_configured_language_is_passed_to_the_transcriber(
    store: FileResultStore, job: Job
) -> None:
    worker, fake = make_worker(store, Transcription("es", []), language="es")
    worker.run(job)
    assert [language for _, language in fake.calls] == ["es"]


def test_params_reflect_the_configured_settings(
    store: FileResultStore, job: Job
) -> None:
    worker, _ = make_worker(
        store,
        Transcription("en", []),
        model_size="medium",
        language="en",
        max_words_per_line=4,
        max_chars_per_line=20,
        pause_s=0.4,
        vad_filter=False,
        refine_starts=False,
    )
    assert worker.params() == {
        "model_size": "medium",
        "language": "en",
        "max_words_per_line": 4,
        "max_chars_per_line": 20,
        "pause_s": 0.4,
        "vad_filter": False,
        "refine_starts": False,
    }


def test_changed_params_are_a_cache_miss(store: FileResultStore, job: Job) -> None:
    words = [word("Hello", 0.0, 0.3)]
    worker_a, _ = make_worker(store, Transcription("en", words))
    worker_a.run(job)
    worker_b, fake_b = make_worker(
        store, Transcription("en", words), max_words_per_line=2
    )
    assert not worker_b.run(job).cached
    assert (
        fake_b.calls
    )  # detect() actually ran, rather than serving worker_a's cached result


# --- ffmpeg, the real transcriber's audio-extraction step -----------------------------------------------------


def test_missing_ffmpeg_raises_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name, path=None: None)
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        _extract_audio(tmp_path / "in.mp4", tmp_path / "out.wav")


def test_ffmpeg_failure_surfaces_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeResult:
        returncode = 1
        stderr = "Invalid data found when processing input"

    monkeypatch.setattr("shutil.which", lambda name, path=None: "/usr/bin/ffmpeg")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeResult())
    with pytest.raises(RuntimeError, match="Invalid data"):
        _extract_audio(tmp_path / "in.mp4", tmp_path / "out.wav")


@pytest.mark.parametrize(
    "message",
    [
        "[out#0/wav @ 0x1] Output file does not contain any stream",
        "Output file #0 does not contain any stream",
    ],
)
def test_a_video_with_no_audio_stream_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    class FakeResult:
        returncode = 1
        stderr = message

    monkeypatch.setattr("shutil.which", lambda name, path=None: "/usr/bin/ffmpeg")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeResult())
    assert _extract_audio(tmp_path / "in.mp4", tmp_path / "out.wav") is False


def test_a_video_with_no_audio_stream_transcribes_to_no_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Never reaches the model: nothing to load, so the optional faster-whisper dependency is not needed.
    monkeypatch.setattr("workers.captions._extract_audio", lambda *a, **k: False)
    transcription = WhisperTranscriber().transcribe(tmp_path / "in.mp4", None)
    assert list(transcription.words) == []


def test_whisper_transcriber_construction_is_lazy() -> None:
    # Must not import faster-whisper (an optional, heavy dependency) just to build the object.
    WhisperTranscriber(model_size="tiny")


def envelope(*stretches: tuple[float, float]) -> list[float]:
    """A 10 ms loudness envelope built from (seconds, dB) stretches."""
    return [db for seconds, db in stretches for _ in range(round(seconds * 100))]


def test_a_start_inside_silence_snaps_forward_to_where_the_audio_gets_loud() -> None:
    # Whisper says "hello" starts at 0.1 s, but the audio is silent until 0.6 s.
    env = envelope((0.6, -60), (0.4, -10), (0.2, -60))
    [hello] = refine_word_starts([word("hello", 0.1, 1.0)], env)
    assert hello.start_s == pytest.approx(0.6, abs=0.011)
    assert hello.end_s == 1.0


def test_a_start_already_on_the_sound_is_left_alone() -> None:
    env = envelope((0.2, -60), (0.8, -10), (0.2, -60))
    words = [word("hello", 0.2, 1.0)]
    assert refine_word_starts(words, env) == words


def test_a_short_dip_inside_a_word_does_not_move_its_start() -> None:
    # Loud, a 50 ms dip, loud again: the word starts on the first stretch, so it stays put.
    env = envelope((0.3, -10), (0.05, -60), (0.3, -10), (0.2, -60))
    words = [word("hello", 0.0, 0.65)]
    assert refine_word_starts(words, env) == words


def test_a_start_never_moves_so_late_that_the_word_vanishes() -> None:
    env = envelope((0.5, -60), (0.01, -10), (0.5, -60))
    words = [word("click", 0.0, 0.52)]
    assert refine_word_starts(words, env) == words


def test_each_word_is_refined_against_its_own_peak() -> None:
    # A quiet word after a loud one is judged by its own level, not the loud word's.
    env = envelope((0.3, -10), (0.4, -60), (0.3, -35), (0.2, -60))
    loud, quiet = refine_word_starts(
        [word("loud", 0.0, 0.3), word("quiet", 0.3, 1.0)], env
    )
    assert loud.start_s == 0.0
    assert quiet.start_s == pytest.approx(0.7, abs=0.011)


def test_no_audio_means_no_change() -> None:
    words = [word("hello", 0.0, 0.5)]
    assert refine_word_starts(words, []) == words

"""text_to_label.py merges hand-labeled text into the committed golden labels, so a bug here corrupts the answer key the
OCR worker (#13) is scored against."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import text_to_label as t

from editrace_schema import Template

DURATION = 300
HASH = "sha256:" + "0" * 64


@pytest.fixture
def golden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "labels").mkdir()
    label = {
        "schema_version": "0.1.0",
        "source": {
            "duration": DURATION,
            "fps": {"num": 30, "den": 1},
            "resolution": {"width": 720, "height": 1280},
            "aspect_ratio": {"num": 9, "den": 16},
            "content_hash": HASH,
        },
        "tracks": [
            {
                "id": "video-1",
                "kind": "video",
                "slots": [],
                "cuts": [
                    {
                        "id": "cut-1",
                        "frame": 50,
                        "confidence": 1,
                        "provenance": {"detector": "manual", "version": "1"},
                    }
                ],
                "transitions": [],
            }
        ],
    }
    (tmp_path / "labels" / "v01.json").write_text(json.dumps(label))
    entry = {"id": "v01", "labeled": True, "notes": ["kept"], "ambiguous": [9]}
    (tmp_path / "manifest.json").write_text(json.dumps({"videos": [entry]}))
    monkeypatch.setattr(t, "ROOT", tmp_path)
    return tmp_path


def item(
    start: int, end: int, text: str = "HELLO", kind: str = "overlay", **extra
) -> dict:
    bbox = {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.1}
    return {
        "start": start,
        "end": end,
        "text": text,
        "kind": kind,
        "bbox": bbox,
        **extra,
    }


def saved(golden: Path, items: list[dict], **extra) -> Path:
    path = golden / "v01.text.json"
    path.write_text(
        json.dumps({"video_id": "v01", "frames": DURATION, "items": items, **extra})
    )
    return path


def run(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(sys, "argv", ["text_to_label.py", "v01", str(path)])
    t.main()


def read(golden: Path, name: str) -> dict:
    return json.loads((golden / name).read_text())


def test_overlays_become_a_text_track_and_the_label_still_validates(
    golden: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(
        monkeypatch,
        saved(golden, [item(30, 90, "SUMMER SALE"), item(100, 130, " Free ")]),
    )
    label = read(golden, "labels/v01.json")
    Template.model_validate(label)
    video, text = label["tracks"]
    assert video["cuts"][0]["frame"] == 50  # cuts untouched
    assert (text["id"], text["kind"]) == ("text-1", "text")
    assert [(o["id"], o["text"], o["range"]) for o in text["text_overlays"]] == [
        ("text-1", "SUMMER SALE", {"start": 30, "duration": 60}),
        ("text-2", "Free", {"start": 100, "duration": 30}),
    ]
    assert all(
        o["provenance"] == {"detector": "manual", "version": "1"}
        for o in text["text_overlays"]
    )


def test_captions_and_scene_text_go_to_the_manifest_not_the_label(
    golden: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [
        item(30, 90, "TITLE"),
        item(40, 70, "so I went", "caption"),
        item(200, 240, "OPEN 24 HOURS", "scene", ambiguous=True),
    ]
    run(monkeypatch, saved(golden, items, labeling_minutes=7))
    (text,) = [
        x for x in read(golden, "labels/v01.json")["tracks"] if x["kind"] == "text"
    ]
    assert [o["text"] for o in text["text_overlays"]] == ["TITLE"]
    entry = read(golden, "manifest.json")["videos"][0]
    assert entry["text_labeled"] is True
    assert entry["text_other"] == [
        {"kind": "caption", "text": "so I went", "start": 40, "duration": 30},
        {"kind": "scene", "text": "OPEN 24 HOURS", "start": 200, "duration": 40},
    ]
    assert entry["text_ambiguous"] == [200]
    assert entry["text_labeling_minutes"] == 7
    assert entry["notes"] == ["kept"] and entry["ambiguous"] == [
        9
    ]  # cut labeling untouched


def test_a_video_with_no_text_gets_an_empty_text_track(
    golden: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(monkeypatch, saved(golden, []))
    label = read(golden, "labels/v01.json")
    Template.model_validate(label)
    assert label["tracks"][1]["text_overlays"] == []
    assert read(golden, "manifest.json")["videos"][0]["text_labeled"] is True


def test_running_again_replaces_the_text_track_instead_of_adding_one(
    golden: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(monkeypatch, saved(golden, [item(30, 90, "OLD")]))
    run(monkeypatch, saved(golden, [item(10, 20, "NEW")]))
    tracks = read(golden, "labels/v01.json")["tracks"]
    assert [x["id"] for x in tracks] == ["video-1", "text-1"]
    assert tracks[1]["text_overlays"][0]["text"] == "NEW"


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (item(30, 90, kind="logo"), "kind must be one of"),
        (item(30, 90, text="   "), "no text"),
        (item(90, 30), "aren't inside the video"),
        (item(30, DURATION + 1), "aren't inside the video"),
        (item(-1, 30), "aren't inside the video"),
        (
            {**item(30, 90), "bbox": {"x": 0.8, "y": 0.2, "w": 0.5, "h": 0.1}},
            "isn't inside the frame",
        ),
        (
            {**item(30, 90), "bbox": {"x": 0.1, "y": 0.2, "w": 0, "h": 0.1}},
            "isn't inside the frame",
        ),
    ],
)
def test_bad_items_stop_the_run_before_anything_is_written(
    golden: Path, monkeypatch: pytest.MonkeyPatch, bad: dict, message: str
) -> None:
    before = (read(golden, "labels/v01.json"), read(golden, "manifest.json"))
    with pytest.raises(SystemExit, match=message):
        run(monkeypatch, saved(golden, [item(1, 5), bad]))
    assert (read(golden, "labels/v01.json"), read(golden, "manifest.json")) == before


def test_a_file_for_another_video_or_frame_count_is_refused(
    golden: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = golden / "x.json"
    path.write_text(json.dumps({"video_id": "v02", "frames": DURATION, "items": []}))
    with pytest.raises(SystemExit, match="is for v02, not v01"):
        run(monkeypatch, path)
    path.write_text(
        json.dumps({"video_id": "v01", "frames": DURATION + 5, "items": []})
    )
    with pytest.raises(SystemExit, match="labeler saw"):
        run(monkeypatch, path)

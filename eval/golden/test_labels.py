"""The committed golden labels: every one is a valid template and matches its manifest entry. Catches schema drift
and hand edits that break a label, which the converter tests can't."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from editrace_schema import Template

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / "manifest.json").read_text())


@pytest.mark.parametrize("video", MANIFEST["videos"], ids=lambda v: v["id"])
def test_label_is_a_valid_template_matching_the_manifest(video: dict) -> None:
    label = json.loads((ROOT / "labels" / f"{video['id']}.json").read_text())
    Template.model_validate(label)
    source = label["source"]
    assert source["content_hash"] == video["content_hash"]
    assert source["duration"] == video["frames"]
    assert source["fps"] == video["fps"]
    assert source["resolution"] == video["resolution"]
    cuts = [c["frame"] for c in label["tracks"][0]["cuts"]]
    assert all(0 < f < video["frames"] for f in cuts)
    assert len(cuts) == len(set(cuts))
    # held_out (#52): the test set nobody tunes on; CI skips it (ci_eval.py). Only labeled videos can be held out.
    assert isinstance(video.get("held_out", False), bool)
    assert not video.get("held_out") or video.get("labeled") is True
    for r in video.get("exclude", []):
        assert 0 <= r["start"] and r["duration"] > 0
        assert r["start"] + r["duration"] <= video["frames"]
        assert not any(r["start"] <= f < r["start"] + r["duration"] for f in cuts)


def test_ids_are_unique_and_each_has_one_label_file() -> None:
    # Two labelers on different branches can claim the same id; git doesn't always catch it when both merge.
    ids = [v["id"] for v in MANIFEST["videos"]]
    assert len(ids) == len(set(ids)), (
        f"duplicate ids: {sorted({i for i in ids if ids.count(i) > 1})}"
    )
    assert {p.stem for p in (ROOT / "labels").glob("*.json")} == set(ids)

"""ci_eval.py (#344): which videos CI scores, the floors that fail the check, and a report that stays private. Uses a
small fake golden set, so no videos are needed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import ci_eval
import pytest
from score import Counts


def test_only_labeled_videos_that_arent_held_out_are_scored() -> None:
    manifest = {
        "videos": [
            {"id": "v01", "labeled": True},
            {"id": "v02", "labeled": True, "held_out": True},
            {"id": "v03", "labeled": False},
            {"id": "v04", "labeled": True, "held_out": False},
        ]
    }
    assert [v["id"] for v in ci_eval.evaluated_videos(manifest)] == ["v01", "v04"]


@pytest.mark.parametrize(
    ("counts", "failed"),
    [
        (Counts(90, 10, 10), []),  # P 0.90, R 0.90: exactly at the recall floor passes
        (Counts(89, 10, 11), ["recall"]),  # R 0.89
        (Counts(90, 31, 0), ["precision"]),  # P 0.744
        (Counts(80, 40, 20), ["precision", "recall"]),
    ],
)
def test_the_check_fails_below_a_floor(counts: Counts, failed: list[str]) -> None:
    assert [f.split()[1] for f in ci_eval.failures("cuts", counts)] == failed


def test_recall_between_the_floor_and_adr_0003_warns_but_passes() -> None:
    counts = Counts(92, 10, 8)  # R 0.92, today's situation on #7
    assert ci_eval.failures("cuts", counts) == []
    assert ci_eval.missed_targets("cuts", counts) == [
        "cuts recall 0.920 is under ADR 0003's 0.95 (not enforced yet, #7)"
    ]


def test_the_report_compares_with_main_and_names_the_worst_regressions() -> None:
    base = {"v01": Counts(10, 0, 0), "v02": Counts(10, 0, 0), "v03": Counts(10, 0, 0)}
    head = {"v01": Counts(10, 0, 0), "v02": Counts(5, 0, 5), "v03": Counts(9, 0, 1)}
    text = ci_eval.render(head, base, "abc1234", "def5678")
    assert text.startswith(
        "### Golden eval · cuts · abc1234 vs def5678\nVideos: 3 (0 failed) · tolerance: ±2 frames"
    )
    assert "| cuts | 1.000 → 1.000 | 1.000 → 0.800 |" in text
    assert "Worst regressions: v02 (cuts R -0.50), v03 (cuts R -0.10)" in text
    assert "❌ cuts recall 0.800 is under the 0.90 floor" in text


def test_the_report_holds_no_paths_or_urls() -> None:
    per_video = {f"v{i:02d}": Counts(9, 1, 1) for i in range(1, 16)}
    text = ci_eval.render(per_video, per_video)
    for leak in ("://", ".mp4", "videos/", "token", "sig="):
        assert leak not in text


def golden_set(root: Path, cuts: list[int], held_out_cuts: list[int]) -> None:
    """A fake golden set: v01 scored, v02 held out (and given no prediction at all)."""
    (root / "labels").mkdir(parents=True)
    manifest = {
        "videos": [
            {"id": "v01", "labeled": True},
            {"id": "v02", "labeled": True, "held_out": True},
        ]
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    for vid, frames in (("v01", cuts), ("v02", held_out_cuts)):
        label = {
            "source": {"content_hash": f"sha256:{vid}"},
            "tracks": [{"cuts": [{"frame": f} for f in frames]}],
        }
        (root / "labels" / f"{vid}.json").write_text(json.dumps(label))


def predict(directory: Path, frames: list[int]) -> Path:
    directory.mkdir()
    template = {
        "source": {"content_hash": "sha256:v01"},
        "tracks": [{"cuts": [{"frame": f} for f in frames]}],
    }
    (directory / "v01.json").write_text(json.dumps(template))
    return directory


def run(monkeypatch: pytest.MonkeyPatch, root: Path, *args: str) -> int:
    monkeypatch.setattr(ci_eval, "ROOT", root)
    monkeypatch.setattr(sys, "argv", ["ci_eval.py", *args])
    with pytest.raises(SystemExit) as exit_:
        ci_eval.main()
    return exit_.value.code


def test_report_exits_1_below_the_floor_and_0_above(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    golden_set(
        tmp_path / "golden",
        cuts=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
        held_out_cuts=[5],
    )
    good = predict(tmp_path / "good", [10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
    bad = predict(tmp_path / "bad", [10, 20, 30, 40, 50, 60, 70, 80])  # R 0.80
    assert run(monkeypatch, tmp_path / "golden", "report", str(good)) == 0
    assert (
        run(monkeypatch, tmp_path / "golden", "report", str(bad), "--base", str(good))
        == 1
    )
    assert "❌ cuts recall 0.800 is under the 0.90 floor" in capsys.readouterr().out


def test_plan_lists_only_what_ci_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    golden_set(tmp_path / "golden", cuts=[10], held_out_cuts=[5])
    monkeypatch.setattr(ci_eval, "ROOT", tmp_path / "golden")
    monkeypatch.setattr(sys, "argv", ["ci_eval.py", "plan"])
    ci_eval.main()
    assert capsys.readouterr().out.split() == ["v01"]


def test_a_missing_or_mismatched_prediction_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    golden_set(tmp_path / "golden", cuts=[10], held_out_cuts=[5])
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(ci_eval, "ROOT", tmp_path / "golden")
    with pytest.raises(SystemExit, match="no prediction for v01"):
        ci_eval.score_predictions(empty, [{"id": "v01"}])
    other = predict(tmp_path / "other", [10])
    (other / "v01.json").write_text(
        json.dumps({"source": {"content_hash": "sha256:else"}, "tracks": []})
    )
    with pytest.raises(SystemExit, match="different video"):
        ci_eval.score_predictions(other, [{"id": "v01"}])

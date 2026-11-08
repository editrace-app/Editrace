"""score_text.py is the ruler the OCR worker (#13) is measured with, so its counting is pinned case by case."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import score
import score_text as st

HASH = "sha256:" + "0" * 64


def template(*overlays: tuple[str, int, int], content_hash: str = HASH) -> dict:
    """Overlays as (text, start, end exclusive)."""
    return {
        "source": {"content_hash": content_hash},
        "tracks": [
            {
                "id": "text-1",
                "kind": "text",
                "text_overlays": [
                    {"text": t, "range": {"start": s, "duration": e - s}}
                    for t, s, e in overlays
                ],
            }
        ],
    }


def other(kind: str, text: str, start: int, end: int) -> dict:
    return {"kind": kind, "text": text, "start": start, "duration": end - start}


def score_one(pred, truth, others=(), exclude=(), tolerance=3) -> st.TextCounts:
    return st.score_text(pred, truth, list(others), list(exclude), tolerance)


def test_a_match_needs_similar_text_at_the_same_time() -> None:
    truth = template(("SUMMER SALE", 30, 90))
    assert score_one(template(("SUMMER SALE", 30, 90)), truth).matched == 1
    assert (
        score_one(template(("Summer  sale", 30, 90)), truth).matched == 1
    )  # case and spacing
    assert (
        score_one(template(("SUMMER SALF", 30, 90)), truth).matched == 1
    )  # one wrong letter
    assert score_one(template(("WINTER", 30, 90)), truth).matched == 0  # other text
    assert (
        score_one(template(("SUMMER SALE", 100, 150)), truth).matched == 0
    )  # other time


def test_on_time_needs_both_edges_within_the_tolerance() -> None:
    truth = template(("HELLO", 30, 90))
    assert (
        score_one(template(("HELLO", 33, 87)), truth).on_time == 1
    )  # both exactly at ±3
    assert score_one(template(("HELLO", 34, 90)), truth).on_time == 0  # in is 4 off
    assert score_one(template(("HELLO", 30, 94)), truth).on_time == 0  # out is 4 off
    found_late = score_one(template(("HELLO", 34, 90)), truth)
    assert found_late.matched == 1 and found_late.recall == 1.0
    assert (
        found_late.on_time_recall == 0.0
    )  # found, but not on time: the #13 number is what drops


def test_one_prediction_cannot_match_two_labels_and_vice_versa() -> None:
    two_labels = template(("GO", 10, 50), ("GO", 20, 60))
    one_pred = score_one(template(("GO", 10, 60)), two_labels)
    assert (one_pred.matched, one_pred.labeled, one_pred.predicted) == (1, 2, 1)
    two_preds = score_one(
        template(("GO", 10, 50), ("GO", 20, 60)), template(("GO", 10, 60))
    )
    assert (two_preds.matched, two_preds.false_positive_other) == (1, 1)


def test_the_closest_text_wins_when_two_labels_overlap_a_prediction() -> None:
    truth = template(("BUY NOW", 10, 80), ("BUY NOW!!", 10, 80))
    counts = score_one(template(("BUY NOW!!", 10, 80)), truth)
    assert counts.matched == 1 and counts.similarity_sum == pytest.approx(1.0)


def test_text_accuracy_is_the_mean_similarity_of_the_matches() -> None:
    counts = score_one(
        template(("ABCDEFGHIJ", 0, 50), ("KLMNOPQRST", 60, 100)),
        template(("ABCDEFGHIJ", 0, 50), ("KLMNOPQRSX", 60, 100)),
    )
    assert counts.matched == 2
    assert counts.text_accuracy == pytest.approx((1.0 + 0.9) / 2)


def test_false_positives_are_sorted_by_what_they_resemble() -> None:
    others = [
        other("caption", "so I went to the store", 100, 160),
        other("scene", "OPEN 24 HOURS", 200, 260),
    ]
    pred = template(
        ("went to the", 110, 140),  # a fragment of the caption line
        ("OPEN 24 HOURS", 200, 255),  # the sign
        ("random noise", 300, 340),  # neither
        ("went to the", 400, 440),  # caption text, but when no caption is on screen
    )
    counts = score_one(pred, template(), others)
    assert (
        counts.false_positive_caption,
        counts.false_positive_scene,
        counts.false_positive_other,
    ) == (1, 1, 2)
    assert counts.precision == 0.0


def test_a_video_with_no_text_scores_no_false_negatives() -> None:
    counts = score_one(template(), template())
    assert (counts.labeled, counts.predicted, counts.matched) == (0, 0, 0)
    assert counts.precision == counts.recall == counts.on_time_recall == 0.0


def test_excluded_ranges_are_left_out_on_both_sides() -> None:
    exclude = [{"start": 900, "duration": 60}]
    pred = template(("LOGO CARD", 905, 950))
    truth = template(("LOGO CARD", 905, 950), ("TITLE", 10, 50))
    counts = score_one(pred, truth, exclude=exclude)
    assert (counts.labeled, counts.predicted) == (1, 0)


@pytest.fixture
def golden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "v01.json").write_text(
        json.dumps(template(("SUMMER SALE", 30, 90)))
    )
    (tmp_path / "labels" / "v02.json").write_text(json.dumps(template()))
    (tmp_path / "labels" / "v03.json").write_text(json.dumps(template()))
    videos = [
        {"id": "v01", "labeled": True, "text_labeled": True, "text_other": []},
        {
            "id": "v02",
            "labeled": True,
            "text_labeled": True,
        },  # labeled for text, no prediction
        {"id": "v03", "labeled": True},  # cuts only: never scored for text
    ]
    (tmp_path / "manifest.json").write_text(json.dumps({"videos": videos}))
    monkeypatch.setattr(st, "ROOT", tmp_path)
    preds = tmp_path / "preds"
    preds.mkdir()
    return preds


def run(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["score.py", "--element", "text", *args])
    score.main()


def test_cli_reports_totals_missing_and_unlabeled(
    golden: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (golden / "v01.json").write_text(json.dumps(template(("SUMMER SALE", 31, 90))))
    (golden / "v03.json").write_text(json.dumps(template(("NOT SCORED", 0, 10))))
    run(monkeypatch, str(golden), "--json")
    out = json.loads(capsys.readouterr().out)
    assert out["element"] == "text" and out["tolerance"] == st.TEXT_TOLERANCE == 3
    assert out["missing"] == ["v02"] and out["unlabeled"] == ["v03"]
    assert set(out["videos"]) == {"v01"}
    assert out["total"]["matched"] == 1 and out["total"]["on_time_recall"] == 1.0

    run(monkeypatch, str(golden), "--tolerance", "0")
    text = capsys.readouterr().out
    assert "±0 frames" in text and "no prediction: v02" in text


def test_cli_refuses_a_prediction_from_another_video(
    golden: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (golden / "v01.json").write_text(
        json.dumps(template(("X", 0, 5), content_hash="sha256:" + "1" * 64))
    )
    (golden / "v02.json").write_text(json.dumps(template()))
    # the labels in the fixture carry HASH, so a prediction with another hash is from a different video
    with pytest.raises(SystemExit, match="different video"):
        run(monkeypatch, str(golden))


def test_cli_with_no_text_labeled_predictions_stops(
    golden: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (golden / "v03.json").write_text(json.dumps(template()))
    with pytest.raises(SystemExit, match="no predictions for any text-labeled"):
        run(monkeypatch, str(golden))

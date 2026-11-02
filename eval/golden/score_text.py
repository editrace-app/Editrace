"""Scores predicted text overlays against the golden labels (#13, #52). Run through `score.py --element text`.

Text overlays are matched on text and time, not on a single frame like cuts:

- A predicted overlay matches a labeled one when the two are on screen at the same time and their text is similar
  (SequenceMatcher ratio of at least 0.6, case and spacing ignored). Matching is one-to-one, most similar pairs first.
- Of the matches, "on time" means both the first and the last frame are within the tolerance (default 3, the #13
  criterion).
- The report has: precision and recall of finding the text; the share of labeled overlays found on time (the number
  #13 asks for); and the mean text similarity of the matches.
- A predicted overlay with no match is a false positive. If it resembles a labeled caption or scene-text item (the
  manifest's `text_other`) it is counted as that kind, so the report shows what the worker mistakes for titles.
- Frames inside a video's `exclude` ranges are left out of scoring on both sides, as for cuts.
- A video is scored only if the manifest says `text_labeled: true` and a prediction exists. Others are reported, never
  silently dropped.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEXT_TOLERANCE = 3
MATCH_SIMILARITY = 0.6


@dataclass
class TextCounts:
    labeled: int = 0
    predicted: int = 0
    matched: int = 0  # found: roughly the same text at the same time
    on_time: int = 0  # of the matched, first and last frame both within the tolerance
    similarity_sum: float = 0.0  # over the matched
    false_positive_caption: int = 0
    false_positive_scene: int = 0
    false_positive_other: int = 0

    def add(self, other: TextCounts) -> None:
        for name in asdict(self):
            setattr(self, name, getattr(self, name) + getattr(other, name))

    @property
    def precision(self) -> float:
        return self.matched / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return self.matched / self.labeled if self.labeled else 0.0

    @property
    def on_time_recall(self) -> float:
        """Share of labeled overlays found with the right text and both edges within the tolerance."""
        return self.on_time / self.labeled if self.labeled else 0.0

    @property
    def text_accuracy(self) -> float:
        return self.similarity_sum / self.matched if self.matched else 0.0


def norm_text(text: str) -> str:
    return " ".join(text.casefold().split())


def text_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()


def text_ranges(template: dict) -> list[dict]:
    """Every overlay as {text, start, end (exclusive)}."""
    return [
        {
            "text": o["text"],
            "start": o["range"]["start"],
            "end": o["range"]["start"] + o["range"]["duration"],
        }
        for track in template["tracks"]
        for o in track.get("text_overlays", [])
    ]


def overlaps(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def resembles(pred: dict, other: dict) -> bool:
    """On screen together, and the text is similar or one contains the other (OCR often reads a caption line as two
    fragments)."""
    a, b = norm_text(pred["text"]), norm_text(other["text"])
    return overlaps(pred, other) and (
        text_similarity(a, b) >= MATCH_SIMILARITY or a in b or b in a
    )


def excluded(frame: int, ranges: list[dict]) -> bool:
    return any(r["start"] <= frame < r["start"] + r["duration"] for r in ranges)


def score_text(
    predicted: dict,
    labeled: dict,
    others: list[dict],
    exclude: list[dict],
    tolerance: int,
) -> TextCounts:
    """`others` is the manifest's `text_other`: captions and scene text, each {kind, text, start, duration}."""
    pred = [r for r in text_ranges(predicted) if not excluded(r["start"], exclude)]
    truth = [r for r in text_ranges(labeled) if not excluded(r["start"], exclude)]
    pairs = sorted(
        (
            (text_similarity(p["text"], t["text"]), i, j)
            for i, p in enumerate(pred)
            for j, t in enumerate(truth)
            if overlaps(p, t)
        ),
        reverse=True,
    )
    used_p: set[int] = set()
    used_t: set[int] = set()
    counts = TextCounts(labeled=len(truth), predicted=len(pred))
    for similarity, i, j in pairs:
        if similarity < MATCH_SIMILARITY:
            break
        if i in used_p or j in used_t:
            continue
        used_p.add(i)
        used_t.add(j)
        counts.matched += 1
        counts.similarity_sum += similarity
        if (
            abs(pred[i]["start"] - truth[j]["start"]) <= tolerance
            and abs(pred[i]["end"] - truth[j]["end"]) <= tolerance
        ):
            counts.on_time += 1
    for i, p in enumerate(pred):
        if i in used_p:
            continue
        kinds = {
            o["kind"]
            for o in others
            if resembles(
                p,
                {
                    "text": o["text"],
                    "start": o["start"],
                    "end": o["start"] + o["duration"],
                },
            )
        }
        if "caption" in kinds:
            counts.false_positive_caption += 1
        elif "scene" in kinds:
            counts.false_positive_scene += 1
        else:
            counts.false_positive_other += 1
    return counts


def score_dir(
    predictions: Path, tolerance: int
) -> tuple[dict[str, TextCounts], list[str], list[str]]:
    """Per-video counts for every text-labeled video with a prediction, the labeled ids with no prediction, and the ids
    not text-labeled yet."""
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    scored: dict[str, TextCounts] = {}
    missing: list[str] = []
    unlabeled: list[str] = []
    for video in manifest["videos"]:
        if video.get("text_labeled") is not True:
            unlabeled.append(video["id"])
            continue
        pred_path = predictions / f"{video['id']}.json"
        if not pred_path.exists():
            missing.append(video["id"])
            continue
        predicted = json.loads(pred_path.read_text(encoding="utf-8"))
        labeled = json.loads(
            (ROOT / "labels" / f"{video['id']}.json").read_text(encoding="utf-8")
        )
        if predicted["source"]["content_hash"] != labeled["source"]["content_hash"]:
            sys.exit(
                f"{pred_path} was made from a different video than labels/{video['id']}.json (content_hash)"
            )
        scored[video["id"]] = score_text(
            predicted,
            labeled,
            video.get("text_other", []),
            video.get("exclude", []),
            tolerance,
        )
    return scored, missing, unlabeled


def run(predictions: Path, tolerance: int | None, as_json: bool) -> None:
    tol = TEXT_TOLERANCE if tolerance is None else tolerance
    scored, missing, unlabeled = score_dir(predictions, tol)
    if not scored:
        sys.exit(f"no predictions for any text-labeled golden video in {predictions}")
    total = TextCounts()
    for counts in scored.values():
        total.add(counts)

    def row(c: TextCounts) -> dict:
        return {
            **asdict(c),
            "precision": c.precision,
            "recall": c.recall,
            "on_time_recall": c.on_time_recall,
            "text_accuracy": c.text_accuracy,
        }

    if as_json:
        print(
            json.dumps(
                {
                    "element": "text",
                    "tolerance": tol,
                    "missing": missing,
                    "unlabeled": unlabeled,
                    "videos": {vid: row(c) for vid, c in scored.items()},
                    "total": row(total),
                },
                indent=2,
            )
        )
        return
    print(
        f"text · ±{tol} frames · {len(scored)} videos"
        + (f" · no prediction: {', '.join(missing)}" if missing else "")
        + (f" · not text-labeled yet: {', '.join(unlabeled)}" if unlabeled else "")
    )
    print(
        f"{'video':<8}{'P':>7}{'R':>7}{'on-time':>9}{'text':>7}{'lab':>5}{'pred':>6}{'cap':>5}{'scene':>7}{'other':>7}"
    )
    for vid, c in [*scored.items(), ("total", total)]:
        print(
            f"{vid:<8}{c.precision:7.3f}{c.recall:7.3f}{c.on_time_recall:9.3f}{c.text_accuracy:7.3f}"
            f"{c.labeled:5d}{c.predicted:6d}{c.false_positive_caption:5d}{c.false_positive_scene:7d}"
            f"{c.false_positive_other:7d}"
        )
    print(
        "P/R: found the text at the right time · on-time: found with both edges within the tolerance · "
        "text: mean similarity of the matches · cap/scene/other: false positives by what they resemble"
    )

"""Scores predicted templates against the golden labels (#52). Evaluation is a template-vs-template diff: for each
element type, precision and recall with a frame tolerance. Cuts and text overlays are scored so far; other element types
are added as their labels exist. `--element text` scores text overlays (see score_text.py).

- A predicted cut matches a labeled cut when their frames differ by at most the tolerance. Matching is one-to-one and
  maximal (as many matches as possible), so one prediction never counts for two labels.
- Frames inside a video's `exclude` ranges in the manifest (e.g. platform end cards, LABELING.md rule 12) are left out
  of scoring on both sides.
- A video is scored only if it's hand-labeled (`labeled: true` in the manifest) and a prediction for it exists.
  Videos ingested but not labeled yet have an empty label file, so scoring them would turn every real predicted cut
  into a false positive. Unlabeled videos and missing predictions are both reported, never silently dropped.

Usage: python3.11 eval/golden/score.py PREDICTIONS_DIR [--element cuts|text] [--tolerance N] [--json]
(the default tolerance is 2 frames for cuts and 3 for text)
PREDICTIONS_DIR holds one template per video, named like the labels (`v01.json`, ...).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_TOLERANCE = 2


@dataclass
class Counts:
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    def add(self, other: Counts) -> None:
        self.true_positives += other.true_positives
        self.false_positives += other.false_positives
        self.false_negatives += other.false_negatives

    @property
    def precision(self) -> float:
        found = self.true_positives + self.false_positives
        return self.true_positives / found if found else 0.0

    @property
    def recall(self) -> float:
        labeled = self.true_positives + self.false_negatives
        return self.true_positives / labeled if labeled else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def match_count(predicted: list[int], labeled: list[int], tolerance: int) -> int:
    """Size of a maximum one-to-one matching between frames within the tolerance.

    On a line, walking both sorted lists and pairing each label with the earliest prediction that can still reach it
    is optimal. Pairing the closest frames first is not: labels 10, 12 and predictions 12, 14 at ±2 give one match
    that way, but two are possible (10-12, 12-14).
    """
    p, t = sorted(predicted), sorted(labeled)
    i = j = matches = 0
    while i < len(p) and j < len(t):
        if p[i] < t[j] - tolerance:
            i += 1
        elif p[i] > t[j] + tolerance:
            j += 1
        else:
            matches += 1
            i += 1
            j += 1
    return matches


def excluded(frame: int, ranges: list[dict]) -> bool:
    return any(r["start"] <= frame < r["start"] + r["duration"] for r in ranges)


def cut_frames(template: dict) -> list[int]:
    return [c["frame"] for track in template["tracks"] for c in track.get("cuts", [])]


def score_cuts(
    predicted: dict, labeled: dict, exclude: list[dict], tolerance: int
) -> Counts:
    pred = [f for f in cut_frames(predicted) if not excluded(f, exclude)]
    truth = [f for f in cut_frames(labeled) if not excluded(f, exclude)]
    matches = match_count(pred, truth, tolerance)
    return Counts(matches, len(pred) - matches, len(truth) - matches)


def score_dir(
    predictions: Path, tolerance: int
) -> tuple[dict[str, Counts], list[str], list[str]]:
    """Per-video cut counts for every labeled video with a prediction, the labeled ids with no prediction, and the
    ids not labeled yet (never scored, whether or not a prediction exists)."""
    manifest = json.loads((ROOT / "manifest.json").read_text())
    scored, missing, unlabeled = {}, [], []
    for video in manifest["videos"]:
        if video.get("labeled") is not True:
            unlabeled.append(video["id"])
            continue
        pred_path = predictions / f"{video['id']}.json"
        if not pred_path.exists():
            missing.append(video["id"])
            continue
        predicted = json.loads(pred_path.read_text())
        labeled = json.loads((ROOT / "labels" / f"{video['id']}.json").read_text())
        if predicted["source"]["content_hash"] != labeled["source"]["content_hash"]:
            sys.exit(
                f"{pred_path} was made from a different video than labels/{video['id']}.json (content_hash)"
            )
        scored[video["id"]] = score_cuts(
            predicted, labeled, video.get("exclude", []), tolerance
        )
    return scored, missing, unlabeled


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("predictions", type=Path)
    parser.add_argument(
        "--tolerance",
        type=int,
        default=None,
        help="frames (default 2 for cuts, 3 for text)",
    )
    parser.add_argument(
        "--element", choices=["cuts", "text"], default="cuts", help="what to score"
    )
    parser.add_argument(
        "--json", action="store_true", help="print machine-readable results"
    )
    args = parser.parse_args()

    if args.element == "text":
        import score_text

        score_text.run(args.predictions, args.tolerance, args.json)
        return
    if args.tolerance is None:
        args.tolerance = DEFAULT_TOLERANCE
    scored, missing, unlabeled = score_dir(args.predictions, args.tolerance)
    if not scored:
        sys.exit(f"no predictions for any labeled golden video in {args.predictions}")
    total = Counts()
    for counts in scored.values():
        total.add(counts)

    if args.json:
        rows = {
            vid: {**asdict(c), "precision": c.precision, "recall": c.recall}
            for vid, c in scored.items()
        }
        print(
            json.dumps(
                {
                    "element": "cuts",
                    "tolerance": args.tolerance,
                    "missing": missing,
                    "unlabeled": unlabeled,
                    "videos": rows,
                    "total": {
                        **asdict(total),
                        "precision": total.precision,
                        "recall": total.recall,
                        "f1": total.f1,
                    },
                },
                indent=2,
            )
        )
        return
    print(
        f"cuts · ±{args.tolerance} frames · {len(scored)} videos"
        + (f" · no prediction: {', '.join(missing)}" if missing else "")
        + (f" · not labeled yet: {', '.join(unlabeled)}" if unlabeled else "")
    )
    print(f"{'video':<8}{'P':>7}{'R':>7}{'TP':>5}{'FP':>5}{'FN':>5}")
    for vid, c in [*scored.items(), ("total", total)]:
        print(
            f"{vid:<8}{c.precision:7.3f}{c.recall:7.3f}{c.true_positives:5d}{c.false_positives:5d}{c.false_negatives:5d}"
        )
    print(f"F1 {total.f1:.3f}")


if __name__ == "__main__":
    main()

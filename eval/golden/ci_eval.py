"""The golden eval as CI runs it on worker changes (#344): which videos to score, the PR-vs-`main` report, and whether
the PR passes the regression floors.

- Scores only labeled videos that aren't `held_out` in the manifest: the held-out ones are the test set nobody tunes on
  (#52), so CI never looks at them.
- The PR and `main` are scored on the same runner, so a cut that flips between platforms (decoding differs by a frame
  or two, see `workers/cuts/test_golden.py`) can't show up as a change.
- The report holds only video ids and numbers, never paths or URLs: the videos are private (#52).

Usage:
  python eval/golden/ci_eval.py plan
      the ids to run the workers on, one per line
  python eval/golden/ci_eval.py report HEAD_DIR [--base BASE_DIR] [--head-sha SHA] [--base-sha SHA] [--out REPORT.md]
      HEAD_DIR and BASE_DIR hold one predicted template per video (`v01.json`, ...), from the PR and from `main`.
      Prints the Markdown report and exits 1 if the PR is below a floor.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import score

ROOT = Path(__file__).resolve().parent

# Below these the check fails. Cuts: ADR 0003's precision floor, and the hard recall floor that #7's golden test
# (`workers/cuts/test_golden.py`) keeps under ADR 0003's 0.95.
FLOORS = {"cuts": {"precision": 0.75, "recall": 0.90}}
# Reported when missed, but not enforced yet: ADR 0003's recall floor. #350's threshold reaches 0.958 on 14 videos,
# but it was tuned on them (0.932 when each is held out), so enforce it once held-out videos confirm it (#311).
TARGETS = {"cuts": {"recall": 0.95}}
WORST = 3  # videos listed under "worst regressions"


def evaluated_videos(manifest: dict) -> list[dict]:
    """Manifest entries CI scores: labeled, and not held out."""
    return [
        v
        for v in manifest["videos"]
        if v.get("labeled") is True and v.get("held_out") is not True
    ]


def score_predictions(
    predictions: Path, videos: list[dict], tolerance: int = score.DEFAULT_TOLERANCE
) -> dict[str, score.Counts]:
    """Cut counts per video. Every video needs a prediction made from the same file as its label: a missing or
    mismatched one is an error, never silently dropped."""
    out = {}
    for video in videos:
        vid = video["id"]
        path = predictions / f"{vid}.json"
        if not path.exists():
            sys.exit(f"no prediction for {vid}")
        predicted = json.loads(path.read_text())
        labeled = json.loads((ROOT / "labels" / f"{vid}.json").read_text())
        if predicted["source"]["content_hash"] != labeled["source"]["content_hash"]:
            sys.exit(
                f"the prediction for {vid} was made from a different video than its label (content_hash)"
            )
        out[vid] = score.score_cuts(
            predicted, labeled, video.get("exclude", []), tolerance
        )
    return out


def total(per_video: dict[str, score.Counts]) -> score.Counts:
    t = score.Counts()
    for counts in per_video.values():
        t.add(counts)
    return t


def failures(element: str, counts: score.Counts) -> list[str]:
    """The floors this result is under; empty means the check passes."""
    return [
        f"{element} {metric} {getattr(counts, metric):.3f} is under the {floor:.2f} floor"
        for metric, floor in FLOORS[element].items()
        if getattr(counts, metric) < floor
    ]


def missed_targets(element: str, counts: score.Counts) -> list[str]:
    return [
        f"{element} {metric} {getattr(counts, metric):.3f} is under ADR 0003's {target:.2f} (not enforced yet, #7)"
        for metric, target in TARGETS.get(element, {}).items()
        if getattr(counts, metric) < target
    ]


def render(
    head: dict[str, score.Counts],
    base: dict[str, score.Counts] | None = None,
    head_sha: str = "PR",
    base_sha: str = "main",
    tolerance: int = score.DEFAULT_TOLERANCE,
) -> str:
    """The PR comment, in `/golden-eval`'s format. Ids and numbers only."""
    h = total(head)
    title = f"### Golden eval · cuts · {head_sha}" + (
        f" vs {base_sha}" if base is not None else ""
    )
    lines = [
        title,
        f"Videos: {len(head)} (0 failed) · tolerance: ±{tolerance} frames",
        "",
    ]
    if base is not None:
        b = total(base)
        lines += [
            "| Element | P (base → PR) | R (base → PR) | F1 Δ |",
            "|---|---|---|---|",
            f"| cuts | {b.precision:.3f} → {h.precision:.3f} | {b.recall:.3f} → {h.recall:.3f} | {h.f1 - b.f1:+.3f} |",
        ]
        drops = sorted(
            ((head[v].recall - base[v].recall, v) for v in head if v in base),
            key=lambda d: d[0],
        )
        worst = [f"{v} (cuts R {d:+.2f})" for d, v in drops[:WORST] if d < 0]
        lines += ["", f"Worst regressions: {', '.join(worst) if worst else 'none'}"]
    else:
        lines += [
            "| Element | P | R | F1 |",
            "|---|---|---|---|",
            f"| cuts | {h.precision:.3f} | {h.recall:.3f} | {h.f1:.3f} |",
        ]
    failed, missed = failures("cuts", h), missed_targets("cuts", h)
    lines += [""] + [f"❌ {f}" for f in failed] + [f"⚠️ {m}" for m in missed]
    if not failed:
        floors = ", ".join(f"{m} ≥ {v:.2f}" for m, v in FLOORS["cuts"].items())
        lines.append(f"✅ above the floors (cuts {floors})")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Golden eval for CI (#344)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="print the ids CI scores")
    report = sub.add_parser("report", help="score and compare predictions")
    report.add_argument("head", type=Path)
    report.add_argument("--base", type=Path)
    report.add_argument("--head-sha", default="PR")
    report.add_argument("--base-sha", default="main")
    report.add_argument("--out", type=Path)
    args = parser.parse_args()

    videos = evaluated_videos(json.loads((ROOT / "manifest.json").read_text()))
    if args.command == "plan":
        print("\n".join(v["id"] for v in videos))
        return
    head = score_predictions(args.head, videos)
    base = score_predictions(args.base, videos) if args.base else None
    text = render(head, base, args.head_sha, args.base_sha)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    sys.exit(1 if failures("cuts", total(head)) else 0)


if __name__ == "__main__":
    main()

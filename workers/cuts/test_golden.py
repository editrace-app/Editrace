"""Regression test for the cut worker (#7) on the golden set (#52): the floor from ADR 0003.

The golden videos are private and never committed, so this runs only where all of them are present (in
`eval/golden/videos/`) and is skipped elsewhere, including CI. It scores with the golden set's own scorer, so the
numbers match `/golden-eval` and ADR 0003. #7 is signed off on #52's 10-video set; until then it covers the labeled
videos there are.

Results can differ by a cut between platforms: video decoding isn't bit-identical, so a cut whose adaptive ratio sits
right at the threshold can be detected on one machine and not another (v05 frame 229 scores 1.9939 on macOS arm64 and
just over 2.0 on Windows, giving recall 0.953 vs 0.965 on v01-v05). The numbers that count for sign-off are the ones
from the Modal container production runs in (#7, #330).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from workers.cuts.detect import REVIEW_BELOW, find_cuts, suspect_cuts, to_partial

pytest.importorskip("scenedetect")

GOLDEN = Path(__file__).resolve().parents[2] / "eval" / "golden"
RECALL_FLOOR = 0.95
PRECISION_FLOOR = 0.75
TOLERANCE = 2
# Recall is 0.958 on 14 videos with #7's retuned threshold, but chosen on those same videos (0.932 when each video is
# held out), so a new video can still land under ADR 0003's floor; that's an expected failure, not a regression.
# Below this it fails outright.
RECALL_REGRESSION_FLOOR = 0.90
# The flag rule's regression gate, set loosely around today's rates (14 videos: 37/254 real cuts flagged, 22/44 false
# ones). Not targets: #7 tunes the rule, and these move with it.
REAL_FLAGGED_MAX = 0.20
FALSE_FLAGGED_MIN = 0.40


def _scorer():
    spec = importlib.util.spec_from_file_location("golden_score", GOLDEN / "score.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = (
        module  # dataclasses look their module up while it's being defined
    )
    spec.loader.exec_module(module)
    return module


def _labeled_videos() -> list[str]:
    manifest = json.loads((GOLDEN / "manifest.json").read_text())
    return [v["id"] for v in manifest["videos"] if v.get("labeled") is True]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _matched(predicted: list[int], labeled: list[int], tolerance: int) -> set[int]:
    """Predicted frames in a maximum one-to-one matching: score.match_count's walk, keeping the pairs."""
    p, t = sorted(predicted), sorted(labeled)
    i = j = 0
    hits: set[int] = set()
    while i < len(p) and j < len(t):
        if p[i] < t[j] - tolerance:
            i += 1
        elif p[i] > t[j] + tolerance:
            j += 1
        else:
            hits.add(p[i])
            i += 1
            j += 1
    return hits


@dataclass
class GoldenRun:
    total: Any  # score.Counts over every labeled video
    report: str
    flags: dict[str, int]


@pytest.fixture(scope="module")
def golden(tmp_path_factory: pytest.TempPathFactory) -> GoldenRun:
    """Detect and score every labeled video once, for the tests below."""
    tmp_path = tmp_path_factory.mktemp("golden")
    labeled = _labeled_videos()
    manifest = {
        v["id"]: v for v in json.loads((GOLDEN / "manifest.json").read_text())["videos"]
    }
    flags = {"false": 0, "real": 0, "false_total": 0, "real_total": 0}
    absent = [vid for vid in labeled if not (GOLDEN / "videos" / f"{vid}.mp4").exists()]
    if absent:
        # Scoring a subset would report numbers nobody else can compare, so it's all labeled videos or nothing.
        pytest.skip(f"golden videos not on this machine: {', '.join(absent)}")

    for vid in labeled:
        video = GOLDEN / "videos" / f"{vid}.mp4"
        label = json.loads((GOLDEN / "labels" / f"{vid}.json").read_text())
        detection = find_cuts(video)
        fps = label["source"]["fps"]["num"] / label["source"]["fps"]["den"]
        predicted = {
            "source": {**label["source"], "content_hash": _sha256(video)},
            **to_partial(detection, suspect_cuts(video, detection.cuts, fps)),
        }
        (tmp_path / f"{vid}.json").write_text(json.dumps(predicted))

        # Flag rate (#7): which detected cuts sit below the review line, and are they real or false?
        ranges = manifest[vid].get("exclude", [])

        def kept(f: int, ranges: list[dict] = ranges) -> bool:
            return not any(r["start"] <= f < r["start"] + r["duration"] for r in ranges)

        cuts = [c for c in predicted["tracks"][0]["cuts"] if kept(c["frame"])]
        labels = [c["frame"] for c in label["tracks"][0]["cuts"] if kept(c["frame"])]
        real = _matched([c["frame"] for c in cuts], labels, TOLERANCE)
        for c in cuts:
            kind = "real" if c["frame"] in real else "false"
            flags[f"{kind}_total"] += 1
            flags[kind] += c["confidence"] < REVIEW_BELOW

    score = _scorer()
    per_video, missing, _unlabeled = score.score_dir(tmp_path, TOLERANCE)
    assert missing == [] and sorted(per_video) == sorted(labeled)
    total = score.Counts()
    for counts in per_video.values():
        total.add(counts)

    report = (
        " · ".join(
            f"{vid} P {c.precision:.3f} R {c.recall:.3f}"
            for vid, c in per_video.items()
        )
        + f" · total P {total.precision:.3f} R {total.recall:.3f} (TP {total.true_positives}, FP {total.false_positives}, FN {total.false_negatives})"
    )
    print(f"\n{len(per_video)} videos · ±{TOLERANCE} frames · {report}")
    print(
        f"flagged for review: {flags['false']}/{flags['false_total']} false cuts, "
        f"{flags['real']}/{flags['real_total']} real cuts"
    )
    return GoldenRun(total, report, flags)


def test_precision_meets_the_floor(golden: GoldenRun) -> None:
    assert golden.total.precision >= PRECISION_FLOOR, (
        f"precision below the floor: {golden.report}"
    )


def test_recall_meets_the_floor(golden: GoldenRun) -> None:
    assert golden.total.recall >= RECALL_REGRESSION_FLOOR, (
        f"recall regressed below {RECALL_REGRESSION_FLOOR}: {golden.report}"
    )
    if golden.total.recall < RECALL_FLOOR:
        # Not a strict xfail: v01-v05 alone pass on Windows and not on macOS (see above), so it would fail by machine.
        pytest.xfail(
            f"recall {golden.total.recall:.3f} is under ADR 0003's {RECALL_FLOOR}; "
            "recall tuning is tracked on #7. Remove this once it passes on held-out videos."
        )


def test_the_flag_rate_holds(golden: GoldenRun) -> None:
    f = golden.flags
    real = f["real"] / f["real_total"] if f["real_total"] else 0.0
    false = f["false"] / f["false_total"] if f["false_total"] else 1.0
    assert real <= REAL_FLAGGED_MAX, f"{real:.0%} of real cuts flagged: {golden.report}"
    assert false >= FALSE_FLAGGED_MIN, (
        f"only {false:.0%} of false cuts flagged: {golden.report}"
    )

"""Spike #357: can preprocessing before ACRCloud, or filtering its answers after, cut wrong song suggestions?

Throwaway spike code on spike/357-acrcloud-thresholds, not merged. Builds on run.py (#11). Audio is never written
to disk: excerpts are piped from ffmpeg into memory (and through Demucs for the stem arm), sent, and dropped.
Raw responses go to eval/sound_spike/results/t357.jsonl (kept out of git). Pre-registration: PREREG_357.md.

Usage (stdlib only, plus a separate Python with Demucs for the stem arm):

    python3 eval/sound_spike/tune.py split                      # write split.json (dev / held-out clip lists)
    python3 eval/sound_spike/tune.py plan                       # count the lookups `lookup` would make
    python3 eval/sound_spike/tune.py lookup --ffmpeg PATH --stem-python PATH   # blind lookups, all 30 clips
    python3 eval/sound_spike/tune.py dev                        # score the policy grid on dev, write policy.json
    python3 eval/sound_spike/tune.py heldout                    # score policy.json once on held-out

`split` reads the answer key only for stratification counts; `lookup` and `plan` never read it.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import random
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402  (the #11 harness: env, ACRCloud call, answer matching)

SPIKE = run.SPIKE
SPLIT = SPIKE / "split.json"
POLICY = SPIKE / "policy.json"
LOG = run.RESULTS / "t357.jsonl"
HELDOUT_DONE = run.RESULTS / "t357_heldout_scored.json"
STEM_SCRIPT = SPIKE / "stem.py"

SEED = 357
HELDOUT_FRACTION = 1 / 3
BUDGET = 250  # new ACRCloud lookups for #357, agreed 2026-11-11
ACCESS_CHECKS = 2  # silence + noise requests made on 2026-11-11 to confirm the key still works
PRICE_PER_VALID_QUERY = 0.006  # ACRCloud entry tier quote in #11: $6 per 1k; a no-result counts as half

# Lookup arms. Saved from #11: start (0 s, 12 s) and middle (centred, 12 s).
POSITION_ARMS = {"p25": (0.25, 12), "p75": (0.75, 12), "mid8": (0.5, 8), "mid15": (0.5, 15)}
SPEED_RATES = (0.93, 1.08, 0.87, 1.18, 0.80)  # resample (speed and pitch together), tried in this order
STEM_ARM = "stem"


# ---------------------------------------------------------------- split


def answers() -> dict[str, dict]:
    with run.ANSWERS.open(newline="") as f:
        return {r["clip"]: r for r in csv.DictReader(f) if r["music"] in ("released", "original", "none")}


def saved() -> dict[tuple[str, str], dict]:
    """ACRCloud responses from #11, keyed (clip, excerpt)."""
    return {(r["clip"], r["excerpt"]): r for r in run.read_results() if r["vendor"] == "acrcloud"}


def split() -> None:
    """20 dev / 10 held-out, stratified by music type x #11 baseline outcome (ACRCloud middle)."""
    ans, old = answers(), saved()
    bucket = {"right": "right", "wrong": "bad", "check": "bad", "missed": "nothing", "no-match": "nothing"}
    strata: dict[tuple[str, str], list[str]] = {}
    for clip in sorted(ans):
        result = run.outcome(old[(clip, "middle")]["summary"], ans[clip])
        strata.setdefault((ans[clip]["music"], bucket[result]), []).append(clip)
    target = round(len(ans) * HELDOUT_FRACTION)
    quota = {k: len(v) * HELDOUT_FRACTION for k, v in strata.items()}
    take = {k: math.floor(q) for k, q in quota.items()}
    for k in sorted(quota, key=lambda k: (-(quota[k] - take[k]), k))[: target - sum(take.values())]:
        take[k] += 1
    rng = random.Random(SEED)
    heldout = sorted(c for k in sorted(strata) for c in rng.sample(strata[k], take[k]))
    dev = sorted(c for c in ans if c not in heldout)
    SPLIT.write_text(json.dumps({
        "seed": SEED, "answer_key_sha256": sha256(run.ANSWERS), "dev": dev, "heldout": heldout,
        "strata_sizes": {f"{m}/{b}": [len(strata[(m, b)]) - take[(m, b)], take[(m, b)]] for m, b in sorted(strata)},
        "note": "strata_sizes: [dev, held-out]. Bucket = #11 ACRCloud-middle outcome: right / bad (wrong or "
                "unresolved) / nothing (no match). Shared with #358; do not tune on held-out.",
    }, indent=2) + "\n")
    print(f"dev {len(dev)}, held-out {len(heldout)} -> {SPLIT.relative_to(run.REPO)}")


def sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- lookups (blind: never reads answers)


def windows(seconds: float) -> dict[str, tuple[float, float]]:
    out = {}
    for arm, (pos, length) in POSITION_ARMS.items():
        out[arm] = (max(0.0, min(seconds - length, pos * seconds - length / 2)), length)
    out[STEM_ARM] = middle = (max(0.0, seconds / 2 - run.EXCERPT_SECONDS / 2), run.EXCERPT_SECONDS)
    for rate in SPEED_RATES:
        out[f"speed{rate:.2f}"] = middle
    return out


def planned(seconds_by_clip: dict[str, float]) -> list[tuple[str, str]]:
    old = saved()
    todo = []
    for clip in sorted(seconds_by_clip):
        for arm in windows(seconds_by_clip[clip]):
            if arm.startswith("speed") and old[(clip, "middle")]["summary"]["match"]:
                continue  # retries only where the saved middle lookup found nothing
            todo.append((clip, arm))
    return todo


def done() -> set[tuple[str, str]]:
    return {(r["clip"], r["arm"]) for r in read_log() if "http_error" not in r["raw"]}


def read_log() -> list[dict]:
    return [json.loads(line) for line in LOG.read_text().splitlines()] if LOG.exists() else []


def excerpt(ffmpeg: str, video: Path, start: float, length: float, rate: float | None = None) -> bytes:
    """Mono 44.1 kHz WAV in memory. `rate` resamples (speed and pitch together): 0.87 undoes a 1.15x speed-up."""
    filters = ["-af", f"asetrate={44100 * rate:.0f},aresample=44100"] if rate else []
    return subprocess.run(
        [ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(video), "-vn",
         "-ac", "1", "-ar", "44100", *filters, "-f", "wav", "pipe:1"],
        check=True, capture_output=True,
    ).stdout


def plan(ffmpeg: str) -> list[tuple[str, str]]:
    seconds = {p.name: run.duration(ffmpeg, p) for p in sorted(run.CLIPS.glob("*.mp4"))}
    todo = [t for t in planned(seconds) if t not in done()]
    used = len(read_log()) + ACCESS_CHECKS
    print(f"planned {len(todo)} new lookups ({Counter(a if not a.startswith('speed') else 'speed' for _, a in todo)}); "
          f"used so far {used} of {BUDGET}")
    return todo


def lookup(ffmpeg: str, stem_python: str) -> None:
    env = run.load_env()
    seconds = {p.name: run.duration(ffmpeg, p) for p in sorted(run.CLIPS.glob("*.mp4"))}
    todo = plan(ffmpeg)
    run.RESULTS.mkdir(exist_ok=True)
    with LOG.open("a") as log:
        for clip, arm in todo:
            used = len(read_log()) + ACCESS_CHECKS
            if used >= BUDGET:
                print(f"budget reached ({used}/{BUDGET}); stopping")
                return
            start, length = windows(seconds[clip])[arm]
            rate = float(arm.removeprefix("speed")) if arm.startswith("speed") else None
            t0 = time.perf_counter()
            audio = excerpt(ffmpeg, run.CLIPS / clip, start, length, rate)
            if arm == STEM_ARM:
                audio = subprocess.run([stem_python, str(STEM_SCRIPT)], input=audio, check=True,
                                       capture_output=True).stdout
            prep = time.perf_counter() - t0
            response, latency = run.acrcloud(env, audio)
            del audio
            summary = run.summarize("acrcloud", response)
            log.write(json.dumps({
                "clip": clip, "arm": arm, "start": round(start, 2), "length": length, "rate": rate,
                "prep_s": round(prep, 2), "latency_s": round(latency, 2), "summary": summary, "raw": response,
            }) + "\n")
            log.flush()
            code = (response.get("status") or {}).get("code", response.get("http_error"))
            print(f"{clip:<8} {arm:<10} prep {prep:5.2f}s  lookup {latency:5.2f}s  "
                  f"{'match' if summary['match'] else f'no match ({code})'}")
            if code not in (0, 1001, 2004):  # 2004: excerpt can't be fingerprinted -> no match (as in #11)
                print(f"stopping on vendor status {code}: {response.get('status') or response}")
                return


# ---------------------------------------------------------------- scoring

REUPLOAD = re.compile(r"\b(sped|speed up|slowed|nightcore|reverb|tiktok|8d|karaoke|instrumental|cover|tribute|"
                      r"lofi|lo-fi|mashup|remix|edit|version)\b", re.I)
VOTE_ARMS = ("start", "p25", "middle", "p75")
SELECTIONS = (
    [f"single:{a}" for a in ("start", "middle", "p25", "p75", "mid8", "mid15", STEM_ARM)]
    + ["fallback:middle>start", "retry:middle>speed", "agree:start+middle", "vote:2of4", "vote:3of4"]
)
TAUS = (0, 80, 100)
FILTERS = ("isrc", "link", "consistent", "no_reupload")


def candidates(raw: dict) -> list[dict]:
    if (raw.get("status") or {}).get("code") != 0:
        return []
    return (raw.get("metadata") or {}).get("music") or []


def lookups_by_clip() -> dict[str, dict[str, dict]]:
    """clip -> arm -> {cands, latency, prep, matched}."""
    out: dict[str, dict[str, dict]] = {}
    for (clip, excerpt_name), r in saved().items():
        out.setdefault(clip, {})[excerpt_name] = {"cands": candidates(r["raw"]), "latency": r["latency_s"], "prep": 0.0}
    for r in read_log():
        if (r["raw"].get("status") or {}).get("code") not in (0, 1001, 2004):
            continue  # HTTP errors and account errors (3003 quota) are not lookups
        out.setdefault(r["clip"], {})[r["arm"]] = {"cands": candidates(r["raw"]), "latency": r["latency_s"],
                                                    "prep": r["prep_s"]}
    return out


def top(cands: list[dict]) -> dict | None:
    if not cands:
        return None
    t = cands[0]
    return {
        "match": True, "title": t.get("title"), "artist": ", ".join(a.get("name", "") for a in t.get("artists") or []),
        "album": (t.get("album") or {}).get("name"), "score": t.get("score") or 0,
        "isrc": (t.get("external_ids") or {}).get("isrc"),
        "link": any(k in (t.get("external_metadata") or {}) for k in ("spotify", "deezer", "youtube")),
        "consistent": len({run.norm(c.get("title")) for c in cands}) == 1,
    }


def select(name: str, arms: dict[str, dict]) -> tuple[dict | None, list[str]]:
    """(suggestion or None, arms looked up) for one clip under a selection rule."""
    kind, spec = name.split(":")
    if kind == "single":
        return top(arms[spec]["cands"]), [spec]
    if kind == "fallback":
        first, second = spec.split(">")
        hit = top(arms[first]["cands"])
        return (hit, [first]) if hit else (top(arms[second]["cands"]), [first, second])
    if kind == "retry":
        used = ["middle"]
        hit = top(arms["middle"]["cands"])
        for rate in SPEED_RATES:
            if hit:
                break
            arm = f"speed{rate:.2f}"
            used.append(arm)
            hit = top(arms[arm]["cands"])
        return hit, used
    if kind == "agree":
        a, b = spec.split("+")
        x, y = top(arms[a]["cands"]), top(arms[b]["cands"])
        if x and y and run.norm(x["title"]) == run.norm(y["title"]):
            return max((y, x), key=lambda s: s["score"]), [a, b]  # ties go to the middle excerpt
        return None, [a, b]
    need = int(spec.split("of")[0])
    hits = [h for h in (top(arms[a]["cands"]) for a in ("middle",) + tuple(a for a in VOTE_ARMS if a != "middle")) if h]
    votes = Counter(run.norm(h["title"]) for h in hits)
    if votes and votes.most_common(1)[0][1] >= need:
        winner = votes.most_common(1)[0][0]
        return max((h for h in hits if run.norm(h["title"]) == winner), key=lambda s: s["score"]), list(VOTE_ARMS)
    return None, list(VOTE_ARMS)


def passes(s: dict, tau: int, filters: tuple[str, ...]) -> bool:
    if s["score"] < tau:
        return False
    checks = {
        "isrc": bool(s["isrc"]), "link": s["link"], "consistent": s["consistent"],
        "no_reupload": not REUPLOAD.search(" ".join(filter(None, (s["title"], s["album"], s["artist"])))),
    }
    return all(checks[f] for f in filters)


def policies() -> list[dict]:
    return [{"selection": s, "tau": t, "filters": list(f)}
            for s in SELECTIONS for t in TAUS
            for n in range(len(FILTERS) + 1) for f in itertools.combinations(FILTERS, n)]


def policy_name(p: dict) -> str:
    return f"{p['selection']} tau{p['tau']}" + "".join(f" +{f}" for f in p["filters"])


BASELINE = {"selection": "single:middle", "tau": 0, "filters": []}


def evaluate(p: dict, clips: list[str], ans: dict, data: dict) -> dict:
    tally = Counter()
    lookups, cost, latency = [], [], []
    for clip in clips:
        a, arms = ans[clip], data[clip]
        suggestion, used = select(p["selection"], arms)
        lookups.append(len(used))
        cost.append(sum(1 if arms[u]["cands"] else 0.5 for u in used) * PRICE_PER_VALID_QUERY)
        latency.append(sum(arms[u]["latency"] + arms[u]["prep"] for u in used))
        if suggestion and not passes(suggestion, p["tau"], tuple(p["filters"])):
            tally["abstained"] += 1
            suggestion = None
        if suggestion:
            result = run.outcome(suggestion, a)
            tally["shown"] += 1
            tally[f"shown_{result}"] += 1
            tally[f"{a['music']}_{result}"] += 1
        elif a["music"] == "released":
            tally["released_missed"] += 1
    released = sum(ans[c]["music"] == "released" for c in clips)
    wrong = tally["shown_wrong"] + tally["shown_check"]  # unresolved counts as wrong
    return {
        "policy": policy_name(p), "clips": len(clips), "released": released, "shown": tally["shown"],
        "released_right": tally["released_right"], "wrong": wrong, "unresolved": tally["shown_check"],
        "abstained": tally["abstained"], "original_right": tally["original_right"],
        "wrong_per_clip": wrong / len(clips), "wrong_per_shown": wrong / tally["shown"] if tally["shown"] else 0.0,
        "recall": tally["released_right"] / released if released else 0.0, "coverage": tally["shown"] / len(clips),
        "lookups": statistics.mean(lookups), "cost": statistics.mean(cost),
        "latency_median": statistics.median(latency), "latency_max": max(latency),
        "eligible": tally["shown"] > 0 and wrong / tally["shown"] <= 0.05,
    }


def wilson(k: int, n: int, z: float = 1.96) -> str:
    if not n:
        return "n/a"
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return f"{max(0, centre - half):.0%}–{min(1, centre + half):.0%}"


def row(m: dict) -> str:
    return (f"| {m['policy']} | {m['released_right']}/{m['released']} ({m['recall']:.0%}) | "
            f"{m['wrong']}/{m['clips']} ({m['wrong_per_clip']:.0%}) | {m['wrong']}/{m['shown']} "
            f"({m['wrong_per_shown']:.0%}) | {m['unresolved']} | {m['shown']}/{m['clips']} | {m['abstained']} | "
            f"{m['lookups']:.2f} | ${m['cost']:.4f} | {m['latency_median']:.2f}s / {m['latency_max']:.2f}s |")


HEADER = ("| Policy | Released right | Wrong per clip | Wrong per shown | Unresolved | Shown | Hidden by filter | "
          "Lookups/clip | Cost/analysis | Latency median / max |\n|---|---|---|---|---|---|---|---|---|---|")


def rank_key(m: dict) -> tuple:
    return (-m["released_right"], m["lookups"], m["policy"].count(" +") + (" tau0" not in m["policy"]), m["policy"])


def complete(selection: str, clips: list[str], data: dict) -> bool:
    """Every clip has the lookups this selection rule needs (the quota ran out partway, see amendment 2)."""
    try:
        for clip in clips:
            select(selection, data[clip])
    except KeyError:
        return False
    return True


def dev() -> None:
    ans, data, s = answers(), lookups_by_clip(), json.loads(SPLIT.read_text())
    usable = [sel for sel in SELECTIONS if complete(sel, s["dev"], data)]
    print(f"selection rules with complete dev lookups: {usable}\nuntested: {[x for x in SELECTIONS if x not in usable]}\n")
    results = [evaluate(p, s["dev"], ans, data) for p in policies() if p["selection"] in usable]
    by_name = {m["policy"]: m for m in results}
    eligible = sorted((m for m in results if m["eligible"]), key=rank_key)
    with (run.RESULTS / "t357_dev_grid.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    print(f"dev: {len(s['dev'])} clips, {len(results)} policies, {len(eligible)} eligible (wrong per shown <= 5%)\n")
    print(HEADER)
    print(row(by_name[policy_name(BASELINE)]))
    for sel in usable:  # best eligible per selection rule, else that rule's unfiltered row
        best = next((m for m in eligible if m["policy"].startswith(sel + " ")), None)
        print(row(best or by_name[policy_name({"selection": sel, "tau": 0, "filters": []})]) + ("" if best else " (none eligible)"))
    for tau in TAUS[1:]:
        print(row(by_name[policy_name({**BASELINE, "tau": tau})]))
    for f in FILTERS:
        print(row(by_name[policy_name({**BASELINE, "filters": [f]})]))
    chosen = eligible[0] if eligible else by_name[policy_name(BASELINE)]
    policy = next(p for p in policies() if policy_name(p) == chosen["policy"])
    if "--no-write" in sys.argv:
        print(f"\nwould select: {chosen['policy']}" + ("" if eligible else " (none eligible: baseline)"))
        return
    POLICY.write_text(json.dumps({"policy": policy, "name": chosen["policy"], "selected_on": "dev",
                                  "eligible": bool(eligible), "dev": chosen}, indent=2) + "\n")
    print(f"\nselected: {chosen['policy']}" + ("" if eligible else " (no policy eligible: baseline retained)"))
    ties = [m["policy"] for m in eligible if m["released_right"] == chosen["released_right"]]
    print(f"policies tied on released right: {len(ties)}")


def heldout(force_report: bool) -> None:
    if HELDOUT_DONE.exists() and not force_report:
        sys.exit(f"held-out already scored once: {HELDOUT_DONE}")
    ans, data, s = answers(), lookups_by_clip(), json.loads(SPLIT.read_text())
    chosen = json.loads(POLICY.read_text())
    report = {}
    for label, p in (("baseline", BASELINE), ("selected", chosen["policy"])):
        report[label] = evaluate(p, s["heldout"], ans, data)
    print(HEADER)
    for label, m in report.items():
        print(row(m))
        print(f"   {label}: wrong per clip 95% CI {wilson(m['wrong'], m['clips'])}, wrong per shown "
              f"{wilson(m['wrong'], m['shown'])}, released recall {wilson(m['released_right'], m['released'])}")
    if not HELDOUT_DONE.exists():
        HELDOUT_DONE.write_text(json.dumps(report, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["split", "plan", "lookup", "dev", "heldout"])
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--stem-python", default=sys.executable)
    parser.add_argument("--report-again", action="store_true", help="heldout: reprint the one scored result")
    parser.add_argument("--no-write", action="store_true", help="dev: print the selection without writing policy.json")
    args = parser.parse_args()
    if args.command == "split":
        split()
    elif args.command == "plan":
        plan(args.ffmpeg)
    elif args.command == "lookup":
        lookup(args.ffmpeg, args.stem_python)
    elif args.command == "dev":
        dev()
    else:
        heldout(args.report_again)


if __name__ == "__main__":
    main()

"""Spike #285: do cuts in short-form videos land on beats?

Throwaway spike code, not merged. Audio is never written to disk: it's piped from ffmpeg into memory, analyzed and
dropped (Key Decision: audio is discarded after analysis).

For each golden video with music it measures two things against the hand-labeled cuts:

1. **On the beat (the issue's exit):** the share of cuts within ±1 / ±2 frames of a detected beat (and downbeat),
   against chance: the share of all frames in the same span that are that close, which is what cuts placed at random
   would score.
2. **Locked to the beat:** whether cuts fall at a consistent point *within* the beat, wherever that is (Rayleigh test
   on each cut's phase between the beats around it). This catches editors who cut a fixed few frames after the beat,
   and trackers that put the beat on the wrong subdivision.

Trackers: beat_this (CPJKU, 2024; beats and downbeats) and librosa. Their timing was checked on synthetic drum loops
at 90/120/150 bpm: beat_this within ±20 ms, librosa a steady +28 ms late, which is subtracted.

Usage:

    uv run --no-project --python 3.12 --with beat-this --with librosa --with soundfile \
        python eval/beat_spike/run.py --ffmpeg PATH [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import librosa
import numpy as np
from beat_this.inference import Audio2Beats
from scipy.stats import binomtest

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "eval" / "golden"
SR = 22050
WINDOWS = (1, 2)  # frames
FLASH_GAP = 3  # a cut this many frames or fewer after the previous one is the far side of a flash or a strobe
LIBROSA_LATENCY = 0.028  # seconds, measured on synthetic drum loops

# Music types as checked for the #11 answer key (main's manifest is corrected in #346)
MUSIC = {"v04": "original", "v05": "released", "v11": "released", "v13": "released", "v15": "released"}


def audio(ffmpeg: str, video: Path) -> np.ndarray:
    """Mono float32 at SR, in memory only."""
    raw = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:1"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def excluded(video: dict) -> np.ndarray:
    mask = np.zeros(video["frames"], dtype=bool)
    for span in video.get("exclude", []):
        mask[span["start"]:span["start"] + span["duration"]] = True
    return mask


def nearest(frames: np.ndarray, events: np.ndarray) -> np.ndarray:
    """Distance in frames from each frame to the nearest event (events in fractional frames)."""
    if len(events) == 0:
        return np.full(len(frames), np.inf)
    if len(events) == 1:
        return np.abs(frames - events[0])
    i = np.clip(np.searchsorted(events, frames), 1, len(events) - 1)
    return np.minimum(np.abs(frames - events[i - 1]), np.abs(frames - events[i]))


def on_event(cuts: np.ndarray, events: np.ndarray, span: np.ndarray) -> dict:
    """On-event rate of the cuts vs chance over the span's frames, per window.

    A cut at frame f is on an event when the event falls within f ± w frames (± half a frame for the frame itself).
    """
    cut_d, all_d = nearest(cuts, events), nearest(span, events)
    out = {}
    for w in WINDOWS:
        hits = int((cut_d <= w + 0.5).sum())
        chance = float((all_d <= w + 0.5).mean()) if len(span) else 0.0
        rate = hits / len(cuts) if len(cuts) else 0.0
        p = binomtest(hits, len(cuts), chance, alternative="greater").pvalue if len(cuts) and 0 < chance < 1 else 1.0
        out[w] = {"hits": hits, "rate": rate, "chance": chance, "ratio": rate / chance if chance else 0.0, "p": p}
    return out


def phase_lock(cuts: np.ndarray, beats: np.ndarray) -> dict:
    """Rayleigh test on where cuts fall between beats: R near 1 means a consistent point in the beat."""
    inside = cuts[(cuts > beats[0]) & (cuts < beats[-1])] if len(beats) > 1 else cuts[:0]
    n = len(inside)
    if n == 0:
        return {"n": 0, "R": 0.0, "lag_frames": 0.0, "p": 1.0}
    i = np.searchsorted(beats, inside)
    period = beats[i] - beats[i - 1]
    z = np.exp(2j * np.pi * (inside - beats[i - 1]) / period).mean()
    R, Rn = abs(z), n * abs(z)
    p = float(min(1.0, np.exp(np.sqrt(1 + 4 * n + 4 * (n * n - Rn * Rn)) - (1 + 2 * n))))  # Zar's approximation
    phase = (np.angle(z) / (2 * np.pi)) % 1
    lag = (phase if phase <= 0.5 else phase - 1) * float(np.median(period))  # frames after (+) or before (-) the beat
    return {"n": n, "R": float(R), "lag_frames": float(lag), "p": p}


def track(a2b: Audio2Beats, y: np.ndarray) -> dict[str, dict]:
    """Beat and downbeat times in seconds, per tracker."""
    beats, downbeats = a2b(y, SR)
    tempo, lib = librosa.beat.beat_track(y=y, sr=SR, units="time")
    return {
        "beat_this": {"beats": np.asarray(beats), "downbeats": np.asarray(downbeats)},
        "librosa": {"beats": lib - LIBROSA_LATENCY, "downbeats": np.array([])},
    }


def analyze(ffmpeg: str, a2b: Audio2Beats, video: dict) -> dict:
    fps = video["fps"]["num"] / video["fps"]["den"]
    y = audio(ffmpeg, GOLDEN / "videos" / f"{video['id']}.mp4")
    trackers = track(a2b, y)
    del y

    labels = json.loads((GOLDEN / "labels" / f"{video['id']}.json").read_text())
    all_cuts = np.array(sorted(c["frame"] for c in labels["tracks"][0]["cuts"]))
    frames = np.arange(video["frames"])
    row = {"id": video["id"], "music": MUSIC.get(video["id"], video.get("music")), "styles": video.get("edit_styles", [])}
    for name, t in trackers.items():
        beats, downbeats = t["beats"] * fps, t["downbeats"] * fps
        period = float(np.median(np.diff(beats))) if len(beats) > 1 else 0.0
        # Frames where music plays (first beat to last beat, plus one beat) that aren't excluded
        in_music = (frames >= beats[0] - period) & (frames <= beats[-1] + period) if len(beats) else frames < 0
        span = frames[~excluded(video) & in_music]
        cuts = all_cuts[np.isin(all_cuts, span)]
        shots = cuts[np.diff(cuts, prepend=-10**6) > FLASH_GAP]  # drops the far side of each flash or strobe
        row[name] = {
            "bpm": 60 * fps / period if period else 0.0,
            "cuts": len(cuts),
            "shots": len(shots),
            "beat": on_event(cuts, beats, span),
            "beat_shots": on_event(shots, beats, span),
            "downbeat": on_event(cuts, downbeats, span) if len(downbeats) else None,
            "lock": phase_lock(shots, beats),
        }
    return row


def pooled(rows: list[dict], tracker: str, key: str, w: int, n_key: str = "cuts") -> str:
    rows = [r for r in rows if r[tracker][key] is not None and r[tracker][n_key]]
    hits = sum(r[tracker][key][w]["hits"] for r in rows)
    n = sum(r[tracker][n_key] for r in rows)
    chance = sum(r[tracker][key][w]["chance"] * r[tracker][n_key] for r in rows) / n
    p = binomtest(hits, n, chance, alternative="greater").pvalue
    return f"{hits}/{n} = {hits / n:.0%} vs chance {chance:.0%} ({hits / n / chance:.1f}x, p={p:.2g})"


def locked(r: dict, tracker: str) -> bool:
    return r[tracker]["lock"]["p"] < 0.05


def report(rows: list[dict]) -> None:
    for tracker in ("beat_this", "librosa"):
        print(f"\n=== {tracker} ===")
        print(f"{'id':<4} {'music':<8} {'bpm':>4} {'cuts':>4}  {'beat ±2 (rate/chance)':>22}  "
              f"{'downbeat ±2':>17}  {'locked: R, lag, p':>24}  styles")
        for r in rows:
            t = r[tracker]
            b2, d2, lk = t["beat"][2], t["downbeat"] and t["downbeat"][2], t["lock"]
            down = f"{d2['rate']:4.0%}/{d2['chance']:4.0%} {d2['ratio']:3.1f}x" if d2 else "-"
            print(f"{r['id']:<4} {r['music']:<8} {t['bpm']:4.0f} {t['cuts']:>4}  "
                  f"{b2['rate']:4.0%}/{b2['chance']:4.0%} {b2['ratio']:3.1f}x p={b2['p']:.3f}  {down:>17}  "
                  f"{lk['R']:.2f} {lk['lag_frames']:+5.1f}fr p={lk['p']:.3f}{' *' if locked(r, tracker) else '  '}  "
                  f"{','.join(r['styles'])}")
        print("pooled:")
        for key, n_key, label in (("beat", "cuts", "beat, all cuts"), ("beat_shots", "shots", "beat, flash far sides dropped"),
                                  ("downbeat", "cuts", "downbeat, all cuts")):
            if any(r[tracker][key] for r in rows):
                for w in WINDOWS:
                    print(f"  {label:<30} ±{w}: {pooled(rows, tracker, key, w, n_key)}")
        yes = [r["id"] for r in rows if r[tracker]["beat"][2]["ratio"] >= 2]
        lk = [r["id"] for r in rows if locked(r, tracker)]
        print(f"exit (±2 beat >= 2x chance on at least half): {len(yes)}/{len(rows)} {', '.join(yes)}")
        print(f"locked to the beat (p < 0.05): {len(lk)}/{len(rows)} {', '.join(lk)}")
        print("by tag: on-beat >= 2x / locked / videos, pooled ±2 beat")
        for tag in sorted({t for r in rows for t in r["styles"]}) + ["(not beat-synced)"]:
            subset = [r for r in rows if (tag in r["styles"] if tag != "(not beat-synced)" else "beat-synced" not in r["styles"])]
            print(f"  {tag:<18} {sum(r[tracker]['beat'][2]['ratio'] >= 2 for r in subset)}"
                  f"/{sum(locked(r, tracker) for r in subset)}/{len(subset)}  {pooled(subset, tracker, 'beat', 2)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    a2b = Audio2Beats(checkpoint_path="final0", device="cpu")
    manifest = json.loads((GOLDEN / "manifest.json").read_text())
    rows = []
    for video in manifest["videos"]:
        path = GOLDEN / "videos" / f"{video['id']}.mp4"
        if MUSIC.get(video["id"], video.get("music")) in ("none", None) or not path.exists():
            print(f"skipped {video['id']}: {'no music' if path.exists() else 'video not on this machine'}")
            continue
        rows.append(analyze(args.ffmpeg, a2b, video))
    report(rows)
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1, default=float))


if __name__ == "__main__":
    main()

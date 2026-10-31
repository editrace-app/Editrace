"""Spike #11: send short audio excerpts to AudD and ACRCloud and record what each identifies.

Throwaway spike code, not merged. Audio is never written to disk: each excerpt is piped from ffmpeg into memory,
sent, and dropped (Key Decision: audio is discarded after analysis). Raw vendor responses go to
eval/sound_spike/results/ (kept out of git).

Usage (Python 3.10+, stdlib only; keys in .env.local):

    python3.11 eval/sound_spike/run.py pilot --ffmpeg PATH   # the 5 golden videos, start + middle excerpts
    python3.11 eval/sound_spike/run.py test --ffmpeg PATH    # every clip in clips/, start + middle excerpts
    python3.11 eval/sound_spike/run.py score                 # test results against answers.csv

The answer key (answers.csv: clip,music,title,artist,notes) is read only by `score`, never by `test`.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import json
import re
import statistics
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "eval" / "golden"
SPIKE = Path(__file__).resolve().parent
CLIPS = SPIKE / "clips"
ANSWERS = SPIKE / "answers.csv"
RESULTS = SPIKE / "results"
VENDORS = ("audd", "acrcloud")
POLICIES = ("start", "middle", "fallback")  # fallback: middle only when start found nothing (1-2 lookups)
EXCERPT_SECONDS = 12  # AudD's standard endpoint reads the first 12 s; ACRCloud recommends < 15 s


def load_env() -> dict[str, str]:
    env = {}
    for line in (REPO / ".env.local").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip("\"'")
    return env


def excerpt(ffmpeg: str, video: Path, start: float) -> bytes:
    """A mono 44.1 kHz WAV excerpt, in memory only."""
    return subprocess.run(
        [ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-t", str(EXCERPT_SECONDS), "-i", str(video),
         "-vn", "-ac", "1", "-ar", "44100", "-f", "wav", "pipe:1"],
        check=True, capture_output=True,
    ).stdout


def multipart(fields: dict[str, str], file_field: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="excerpt.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n".encode() + data + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def post(url: str, body: bytes, content_type: str) -> tuple[dict, float]:
    request = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    start = time.perf_counter()
    try:
        response = json.load(urllib.request.urlopen(request, timeout=60))
    except urllib.error.HTTPError as error:
        response = {"http_error": error.code, "body": error.read().decode(errors="replace")[:500]}
    return response, time.perf_counter() - start


def audd(env: dict[str, str], audio: bytes) -> tuple[dict, float]:
    body, ctype = multipart({"api_token": env["AUDD_API_TOKEN"], "return": "apple_music,spotify,deezer"}, "file", audio)
    return post("https://api.audd.io/", body, ctype)


def acrcloud(env: dict[str, str], audio: bytes) -> tuple[dict, float]:
    timestamp = str(int(time.time()))
    to_sign = "\n".join(["POST", "/v1/identify", env["ACRCLOUD_ACCESS_KEY"], "audio", "1", timestamp])
    signature = base64.b64encode(
        hmac.new(env["ACRCLOUD_ACCESS_SECRET"].encode(), to_sign.encode(), hashlib.sha1).digest()
    ).decode()
    fields = {
        "access_key": env["ACRCLOUD_ACCESS_KEY"],
        "data_type": "audio",
        "signature_version": "1",
        "signature": signature,
        "timestamp": timestamp,
        "sample_bytes": str(len(audio)),
    }
    body, ctype = multipart(fields, "sample", audio)
    return post(f"https://{env['ACRCLOUD_HOST']}/v1/identify", body, ctype)


def summarize(vendor: str, response: dict) -> dict:
    """The top match as comparable fields, or none."""
    if vendor == "audd":
        result = response.get("result")
        if response.get("status") != "success" or not result:
            return {"match": False, "detail": response.get("error", {}).get("error_message", "no result")}
        return {
            "match": True,
            "title": result.get("title"),
            "artist": result.get("artist"),
            "isrc": (result.get("apple_music") or {}).get("isrc") or (result.get("deezer") or {}).get("isrc"),
            "score": None,
            "link": result.get("song_link"),
            "offset": result.get("timecode"),
        }
    status = response.get("status", {})
    music = (response.get("metadata") or {}).get("music") or []
    if status.get("code") != 0 or not music:
        return {"match": False, "detail": f"{status.get('code')} {status.get('msg')}"}
    top = music[0]
    external = top.get("external_metadata") or {}
    return {
        "match": True,
        "title": top.get("title"),
        "artist": ", ".join(a.get("name", "") for a in top.get("artists") or []),
        "isrc": (top.get("external_ids") or {}).get("isrc"),
        "score": top.get("score"),
        "link": "spotify" if external.get("spotify") else ("youtube" if external.get("youtube") else None),
        "offset": top.get("play_offset_ms"),
        "alternatives": len(music) - 1,
    }


def pilot(ffmpeg: str) -> None:
    env = load_env()
    manifest = json.loads((GOLDEN / "manifest.json").read_text())
    RESULTS.mkdir(exist_ok=True)
    log = (RESULTS / "pilot.jsonl").open("a")
    for video in manifest["videos"]:
        path = GOLDEN / "videos" / f"{video['id']}.mp4"
        seconds = video["frames"] * video["fps"]["den"] / video["fps"]["num"]
        starts = {"start": 0.0, "middle": max(0.0, seconds / 2 - EXCERPT_SECONDS / 2)}
        for where, start in starts.items():
            audio = excerpt(ffmpeg, path, start)
            for vendor, call in (("audd", audd), ("acrcloud", acrcloud)):
                response, latency = call(env, audio)
                summary = summarize(vendor, response)
                log.write(json.dumps({
                    "video": video["id"], "music": video["music"], "excerpt": where, "start": round(start, 2),
                    "vendor": vendor, "latency_s": round(latency, 2), "summary": summary, "raw": response,
                }) + "\n")
                shown = f"{summary.get('title')} — {summary.get('artist')} (isrc {summary.get('isrc')}, score {summary.get('score')})" \
                    if summary["match"] else f"no match ({summary.get('detail')})"
                print(f"{video['id']} [{video['music']:>8}] {where:<6} {vendor:<8} {latency:5.2f}s  {shown}")
            del audio
    log.close()


def duration(ffmpeg: str, video: Path) -> float:
    """Seconds, from ffmpeg's header dump (works without ffprobe)."""
    stderr = subprocess.run([ffmpeg, "-i", str(video)], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", stderr).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def test(ffmpeg: str) -> None:
    """Look up every clip blind: no answer key, no manifest. Appends to results/test.jsonl."""
    env = load_env()
    RESULTS.mkdir(exist_ok=True)
    done = {(r["clip"], r["excerpt"], r["vendor"]) for r in read_results()}
    with (RESULTS / "test.jsonl").open("a") as log:
        for path in sorted(CLIPS.glob("*.mp4")):
            seconds = duration(ffmpeg, path)
            starts = {"start": 0.0, "middle": max(0.0, seconds / 2 - EXCERPT_SECONDS / 2)}
            for where, start in starts.items():
                if all((path.name, where, v) in done for v in VENDORS):
                    continue
                audio = excerpt(ffmpeg, path, start)
                for vendor, call in (("audd", audd), ("acrcloud", acrcloud)):
                    if (path.name, where, vendor) in done:
                        continue
                    response, latency = call(env, audio)
                    summary = summarize(vendor, response)
                    log.write(json.dumps({
                        "clip": path.name, "excerpt": where, "start": round(start, 2), "vendor": vendor,
                        "latency_s": round(latency, 2), "summary": summary, "raw": response,
                    }) + "\n")
                    log.flush()
                    print(f"{path.stem:<4} {where:<6} {vendor:<8} {latency:5.2f}s  "
                          f"{'match' if summary['match'] else 'no match'}")
                del audio


def read_results() -> list[dict]:
    path = RESULTS / "test.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def norm(text: str | None) -> str:
    """Lowercase, without bracketed parts (feat., remix, sped up), featured artists or punctuation."""
    text = re.sub(r"[(\[].*?[)\]]", " ", (text or "").lower())
    text = re.split(r"\s(?:feat\.?|ft\.?|featuring)\s", f" {text} ")[0]
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def clean(text: str | None) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", (text or "").lower()).split())


def featured(summary: dict) -> bool:
    """The match credits more than one artist (features, collaborators): a bonus, not required."""
    artists = re.split(r",|&|;|\s(?:feat\.?|ft\.?|featuring|with|x)\s", (summary.get("artist") or "").lower())
    return sum(bool(clean(a)) for a in artists) > 1 or bool(re.search(r"\b(feat|ft|featuring)\b", (summary.get("title") or "").lower()))


def same_song(summary: dict, answer: dict) -> str:
    """'right', 'wrong' or 'check' (confirm by hand).

    Right needs the song's title and its main artist, as the answer key gives it. Featured artists are ignored
    either way; crediting them is counted separately as a bonus. Non-Latin script can't be compared by text, so
    a mismatch there is 'check'.

    A clip that uses more than one song (a mashup, or an edit that switches songs) lists each one, separated by
    `|` in both title and artist (e.g. `Colors|Bags` and `Halsey|Clairo`). A match to any of them counts.
    """
    titles, artists = answer["title"].split("|"), answer["artist"].split("|")
    if len(titles) > 1:
        results = [same_song(summary, {**answer, "title": t, "artist": a})
                   for t, a in zip(titles, artists + [""] * (len(titles) - len(artists)))]
        return next((r for r in ("right", "check") if r in results), "wrong")
    title, want = norm(summary.get("title")), norm(answer["title"])
    non_latin = bool(re.search(r"[^\x00-\x7f]", f"{summary.get('title')}{summary.get('artist')}"))
    if not want or not title:
        return "wrong"
    if not (title == want or (min(len(title), len(want)) >= 3 and (title in want or want in title))):
        return "check" if non_latin else "wrong"
    main = clean(answer["artist"])
    if not main or re.search(rf"\b{re.escape(main)}\b", clean(summary.get("artist"))):
        return "right"
    return "check"


def outcome(summary: dict | None, answer: dict) -> str:
    """right / wrong / check / missed (released) or no-match (original, none)."""
    if not summary or not summary["match"]:
        return "missed" if answer["music"] == "released" else "no-match"
    if answer["music"] == "released" or answer["title"]:
        return same_song(summary, answer)  # an original sound with a title is a remix of that song
    if "possible remix" in answer["notes"]:
        return "check"  # listen and compare with the match by hand
    return "wrong"


def score() -> None:
    with ANSWERS.open(newline="") as f:
        answers = {row["clip"]: row for row in csv.DictReader(f)}
    latest = {(r["clip"], r["excerpt"], r["vendor"]): r for r in read_results()}
    clips = sorted({clip for clip, _, _ in latest})
    unfilled = [c for c in clips if (answers.get(c) or {}).get("music") not in ("released", "original", "none")]
    if unfilled:
        print(f"skipped, music type missing or unknown in answers.csv: {', '.join(unfilled)}\n")
    clips = [c for c in clips if c not in unfilled]

    def pick(clip: str, vendor: str, policy: str) -> tuple[dict | None, int]:
        start, middle = (latest.get((clip, e, vendor)) for e in ("start", "middle"))
        if policy == "start":
            return start and start["summary"], 1
        if policy == "middle":
            return middle and middle["summary"], 1
        if start and start["summary"]["match"]:
            return start["summary"], 1
        return middle and middle["summary"], 2

    print("per clip (fallback policy):")
    for clip in clips:
        a = answers[clip]
        cells = []
        for vendor in VENDORS:
            summary, _ = pick(clip, vendor, "fallback")
            shown = f"{summary.get('title')} / {summary.get('artist')}" if summary and summary["match"] else "-"
            cells.append(f"{vendor} {outcome(summary, a):<8} {shown[:40]:<40}")
        print(f"  {clip:<8} {a['music']:<8} " + "  ".join(cells))

    counts = {m: sum(answers[c]["music"] == m for c in clips) for m in ("released", "original", "none")}
    print(f"\nclips: {counts['released']} released, {counts['original']} original, {counts['none']} none")
    for policy in POLICIES:
        print(f"\npolicy: {policy}")
        for vendor in VENDORS:
            tally: dict[str, dict[str, int]] = {m: {} for m in counts}
            lookups = bonus = 0
            for clip in clips:
                summary, n = pick(clip, vendor, policy)
                lookups += n
                result = outcome(summary, answers[clip])
                tally[answers[clip]["music"]][result] = tally[answers[clip]["music"]].get(result, 0) + 1
                bonus += result == "right" and featured(summary)
            rel = tally["released"]
            wrong = sum(t.get("wrong", 0) for t in tally.values())
            right_rate = rel.get("right", 0) / counts["released"] if counts["released"] else 0
            wrong_rate = wrong / len(clips) if clips else 0
            print(f"  {vendor:<8} released {rel}  original {tally['original']}  none {tally['none']}")
            print(f"  {'':<8} right {right_rate:.0%} of released (exit >= 70%), wrong links {wrong}/{len(clips)} "
                  f"= {wrong_rate:.0%} (exit <= 5%), {lookups / len(clips):.2f} lookups per clip, "
                  f"bonus: {bonus} right matches also credit featured artists")
    for vendor in VENDORS:
        latencies = sorted(r["latency_s"] for (_, _, v), r in latest.items() if v == vendor)
        if latencies:
            p95 = latencies[min(len(latencies) - 1, round(0.95 * (len(latencies) - 1)))]
            print(f"\nlatency {vendor}: median {statistics.median(latencies):.2f}s, p95 {p95:.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["pilot", "test", "score"])
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    if args.command == "pilot":
        pilot(args.ffmpeg)
    elif args.command == "test":
        test(args.ffmpeg)
    else:
        score()


if __name__ == "__main__":
    main()

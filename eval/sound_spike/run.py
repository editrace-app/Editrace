"""Spike #11: send short audio excerpts to AudD and ACRCloud and record what each identifies.

Throwaway spike code, not merged. Audio is never written to disk: each excerpt is piped from ffmpeg into memory,
sent, and dropped (Key Decision: audio is discarded after analysis). Raw vendor responses go to
eval/sound_spike/results/ (kept out of git).

Usage (Python 3.10+, stdlib only; keys in .env.local):

    python3.11 eval/sound_spike/run.py pilot --ffmpeg PATH   # the 5 golden videos, start + middle excerpts
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "eval" / "golden"
RESULTS = Path(__file__).resolve().parent / "results"
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["pilot"])
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    pilot(args.ffmpeg)


if __name__ == "__main__":
    main()

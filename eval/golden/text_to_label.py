"""Merges hand-labeled on-screen text into a golden-set label (LABELING.md, "Text overlays"). The input is the
`vNN.text.json` saved by text_labeler.html: a list of items, each with first frame, end (exclusive), the text, a
normalized box, a kind and an `ambiguous` flag.

Kinds:
- `overlay`: text the editor added (titles, stickers, lower thirds). These become the label's `text` track, the answer
  key for the OCR worker (#13).
- `caption`: burned-in speech captions. Not overlays (the captions worker, #12, owns them), so they are not in the
  label's text track. They go in the manifest's `text_other`, so the scorer can tell "found a caption as an overlay"
  from "found something that isn't text at all".
- `scene`: text that is part of the footage (signs, shirts, screens). Also `text_other`.

Cuts, transitions and slots in the label, and everything else in the manifest, are left alone. The label's text track and
the manifest's `text_labeled`, `text_other` and `text_ambiguous` are replaced on every run. Anything wrong stops the run
before either file is written.

Usage: python3.11 text_to_label.py VIDEO_ID TEXT.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KINDS = {"overlay", "caption", "scene"}
MANUAL = {"confidence": 1, "provenance": {"detector": "manual", "version": "1"}}
TRACK_ID = "text-1"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"{path} not found")
    except json.JSONDecodeError as e:
        sys.exit(f"{path} isn't valid JSON: {e}")


def check_item(n: int, item: dict, duration: int) -> None:
    where = f"item {n} ({item.get('text', '?')!r})"
    if item.get("kind") not in KINDS:
        sys.exit(f"{where}: kind must be one of {sorted(KINDS)}")
    if not str(item.get("text", "")).strip():
        sys.exit(f"{where}: no text")
    if len(item["text"]) > 2000:
        sys.exit(f"{where}: text is longer than the schema allows (2000)")
    if not 0 <= item["start"] < item["end"] <= duration:
        sys.exit(
            f"{where}: frames {item['start']}-{item['end']} aren't inside the video (0-{duration})"
        )
    b = item["bbox"]
    if not (
        0 <= b["x"] <= 1
        and 0 <= b["y"] <= 1
        and b["w"] > 0
        and b["h"] > 0
        and b["x"] + b["w"] <= 1.0001
        and b["y"] + b["h"] <= 1.0001
    ):
        sys.exit(f"{where}: the box {b} isn't inside the frame")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("video_id")
    parser.add_argument("text_json", type=Path)
    args = parser.parse_args()

    label_path = ROOT / "labels" / f"{args.video_id}.json"
    label = read_json(label_path)
    manifest_path = ROOT / "manifest.json"
    manifest = read_json(manifest_path)
    entry = next((v for v in manifest["videos"] if v["id"] == args.video_id), None)
    if entry is None:
        sys.exit(f"{args.video_id} isn't in the manifest; run ingest.py for it first")
    saved = read_json(args.text_json)
    if saved["video_id"] != args.video_id:
        sys.exit(f"{args.text_json} is for {saved['video_id']}, not {args.video_id}")
    duration = label["source"]["duration"]
    if saved["frames"] != duration:
        sys.exit(
            f"labeler saw {saved['frames']} frames but the label says {duration}; was the right file and fps used?"
        )
    items = saved["items"]
    for n, item in enumerate(items, 1):
        check_item(n, item, duration)

    overlays = [i for i in items if i["kind"] == "overlay"]
    track = {
        "id": TRACK_ID,
        "kind": "text",
        "text_overlays": [
            {
                "id": f"text-{n}",
                "text": i["text"].strip(),
                "bbox": i["bbox"],
                "range": {"start": i["start"], "duration": i["end"] - i["start"]},
                **MANUAL,
            }
            for n, i in enumerate(overlays, 1)
        ],
    }
    label["tracks"] = [t for t in label["tracks"] if t["id"] != TRACK_ID] + [track]
    label_path.write_text(json.dumps(label, indent=2) + "\n", encoding="utf-8")

    entry["text_labeled"] = True
    entry["text_other"] = [
        {
            "kind": i["kind"],
            "text": i["text"].strip(),
            "start": i["start"],
            "duration": i["end"] - i["start"],
        }
        for i in items
        if i["kind"] != "overlay"
    ]
    entry["text_ambiguous"] = sorted({i["start"] for i in items if i.get("ambiguous")})
    if saved.get("labeling_minutes") is not None:
        entry["text_labeling_minutes"] = saved["labeling_minutes"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"{args.video_id}: {len(overlays)} overlays, {len(entry['text_other'])} captions/scene text, "
        f"{len(entry['text_ambiguous'])} unsure"
    )


if __name__ == "__main__":
    main()

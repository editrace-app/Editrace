"""Spike #5: rebuild the cut worker's detected timeline in DaVinci Resolve, then check Resolve kept every cut.

    build  run the cut worker (#7) on golden videos and write one OpenTimelineIO timeline per video: a clip per
           detected slot, with a marker on each cut flagged for review. --fill original takes each slot from the
           original video (plays like the original whatever the detector did, so it checks the import);
           --fill donors takes each from a cut-free shot of another golden video (so wrong cuts show)
    diff   read the timeline Resolve re-exports and compare its cut frames with the worker's, ±1 frame

Run from the repo root, with OpenTimelineIO added for the run (it isn't a project dependency yet; #76 adds it):

    uv run --extra cuts --with opentimelineio python eval/resolve_spike/rebuild.py build --out OUT [v03 v10 v15]
    uv run --extra cuts --with opentimelineio python eval/resolve_spike/rebuild.py diff --out OUT --video v03 RESOLVE_EXPORT.otio

The timelines reference the private golden videos by absolute path, so OUT stays outside the repo.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sys
from fractions import Fraction
from pathlib import Path

import opentimelineio as otio

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from workers.cuts.detect import REVIEW_BELOW, find_cuts, suspect_cuts, to_partial

GOLDEN = ROOT / "eval" / "golden"
TOLERANCE = 1  # #5's criterion: cuts within 1 frame


def source_of(vid: str) -> dict:
    """The video's `source` section from its golden label: frame rate and duration as labeled."""
    return json.loads((GOLDEN / "labels" / f"{vid}.json").read_text())["source"]


def check_video(vid: str) -> None:
    """Refuse a video whose file isn't the one the manifest recorded, so a swapped file can't give plausible but
    wrong evidence."""
    manifest = json.loads((GOLDEN / "manifest.json").read_text())
    expected = next(v["content_hash"] for v in manifest["videos"] if v["id"] == vid)
    h = hashlib.sha256()
    with (GOLDEN / "videos" / f"{vid}.mp4").open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if f"sha256:{h.hexdigest()}" != expected:
        raise SystemExit(f"{vid}.mp4 doesn't match manifest.json's content_hash")


def detect(vid: str) -> dict:
    """The worker's partial template for one golden video, as the product would produce it."""
    video = GOLDEN / "videos" / f"{vid}.mp4"
    fps = source_of(vid)["fps"]
    detection = find_cuts(video)
    return to_partial(
        detection, suspect_cuts(video, detection.cuts, fps["num"] / fps["den"])
    )


def rational_time(frames: int, rate: Fraction) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(frames, float(rate))


def media_of(vid: str) -> otio.schema.ExternalReference:
    source = source_of(vid)
    rate = Fraction(source["fps"]["num"], source["fps"]["den"])
    # A plain OS path, not a file:// URI: Resolve's OTIO import on Windows reports file:// media as not found.
    return otio.schema.ExternalReference(
        target_url=str((GOLDEN / "videos" / f"{vid}.mp4").resolve()),
        available_range=otio.opentime.TimeRange(
            rational_time(0, rate), rational_time(source["duration"], rate)
        ),
    )


def donor_shots(target: str, rate: Fraction) -> list[tuple[int, str, int]]:
    """Every labeled shot in the other golden videos at the target's frame rate, as (length, video, start): the
    stretch between two labeled cuts, clear of labeled transitions and the manifest's excluded ranges (end cards,
    blank frames). A slot filled from one of these has no cut inside it, so every shot change the reviewer sees
    is a detected cut (or a marked donor join)."""
    manifest = json.loads((GOLDEN / "manifest.json").read_text())
    entries = {v["id"]: v for v in manifest["videos"]}
    shots = []
    for label in sorted((GOLDEN / "labels").glob("*.json")):
        vid = label.stem
        if vid == target or not (GOLDEN / "videos" / f"{vid}.mp4").exists():
            continue
        data = json.loads(label.read_text())
        fps = data["source"]["fps"]
        if Fraction(fps["num"], fps["den"]) != rate:
            continue
        check_video(vid)
        track = data["tracks"][0]
        blocked = [
            (t["range"]["start"], t["range"]["start"] + t["range"]["duration"])
            for t in track.get("transitions") or []
        ] + [
            (e["start"], e["start"] + e["duration"])
            for e in entries.get(vid, {}).get("exclude", [])
        ]
        bounds = sorted(
            {0, data["source"]["duration"], *(c["frame"] for c in track["cuts"])}
        )
        for a, z in itertools.pairwise(bounds):
            # Trim the shot to its longest run of frames outside every blocked range.
            free, run = [], a
            for f in range(a, z + 1):
                if f == z or any(s <= f < e for s, e in blocked):
                    if f > run:
                        free.append((f - run, run))
                    run = f + 1
            if free:
                length, start = max(free)
                shots.append((length, vid, start))
    return shots


def fill_from_donors(
    target: str, slots: list[dict], rate: Fraction
) -> list[list[tuple[str, int, int]]]:
    """For each slot, the donor pieces (video, start, frames) that fill it. A slot takes one donor shot at least as
    long as itself, from a different video than the slot before, preferring shots not used yet; a slot longer than
    any donor shot takes several, joined. Deterministic, so a rebuild gives the same timeline."""
    shots = sorted(donor_shots(target, rate), key=lambda s: (s[1], s[2]))
    if not shots:
        raise SystemExit(
            f"no donor shots at {rate} fps for {target}: no other golden video on disk has that rate"
        )
    used: set[tuple[str, int]] = set()
    previous = None
    fills = []
    for slot in slots:
        need, pieces = slot["range"]["duration"], []
        while need:
            others = [s for s in shots if s[1] != previous]
            if not others:
                raise SystemExit(
                    f"{target} {slot['id']}: every donor shot is from {previous}, the previous slot's video"
                )
            fits = [s for s in others if s[0] >= need]
            pool = fits or [max(others, key=lambda s: s[0])]
            fresh = [s for s in pool if (s[1], s[2]) not in used] or pool
            length, vid, start = (
                min(fresh, key=lambda s: (s[0], s[1], s[2])) if fits else fresh[0]
            )
            take = min(length, need)
            used.add((vid, start))
            pieces.append((vid, start, take))
            need -= take
            previous = vid
        fills.append(pieces)
    return fills


def build_timeline(
    vid: str, partial: dict, fill: str = "original"
) -> tuple[otio.schema.Timeline, list[int]]:
    """One clip per slot. With fill "original", each slot takes the same frames from the original video: the
    rebuild then plays like the original whatever the detector did, so it checks the import, not the cuts. With
    fill "donors", each slot takes a cut-free shot from another golden video, the way a user fills a template with
    their own footage: a false cut shows as a shot change mid-shot, a missed cut as one shot running across the
    original's cut. Audio is the original's, unedited, for the review only: product exports never carry it (Key
    Decisions: no copyrighted audio). Returns the timeline and the frames where donor pieces join inside a slot."""
    source = source_of(vid)
    rate = Fraction(source["fps"]["num"], source["fps"]["den"])
    slots = partial["tracks"][0]["slots"]
    empty = [s["id"] for s in slots if s["range"]["duration"] <= 0]
    if empty:
        # A zero-length slot would make no clip and silently vanish from the timeline.
        raise SystemExit(f"{vid}: zero-length slots {empty}")
    if fill == "donors":
        fills = fill_from_donors(vid, slots, rate)
    else:
        fills = [[(vid, s["range"]["start"], s["range"]["duration"])] for s in slots]

    def marker(name: str, at: int, color: str) -> otio.schema.Marker:
        return otio.schema.Marker(
            name=name,
            marked_range=otio.opentime.TimeRange(
                rational_time(at, rate), rational_time(1, rate)
            ),
            color=color,
        )

    # A marker at each cut flagged for review (ADR 0003 keeps them in), so the reviewer can say whether they show.
    flagged = {
        c["frame"]
        for c in partial["tracks"][0]["cuts"]
        if c["confidence"] < REVIEW_BELOW
    }
    track = otio.schema.Track(name="V1 detected cuts", kind=otio.schema.TrackKind.Video)
    joins = []
    for slot, pieces in zip(slots, fills):
        at = slot["range"]["start"]
        for n, (donor, start, frames) in enumerate(pieces):
            clip = otio.schema.Clip(
                name=f"{vid} {slot['id']}"
                + (f" <- {donor}@{start}" if donor != vid else ""),
                media_reference=media_of(donor),
                source_range=otio.opentime.TimeRange(
                    rational_time(start, rate), rational_time(frames, rate)
                ),
            )
            clip.metadata["editrace"] = {
                "slot": slot["id"],
                "confidence": slot["confidence"],
                "donor": donor,
            }
            if n == 0 and at in flagged:
                clip.markers.append(
                    marker("flagged cut", start, otio.schema.MarkerColor.RED)
                )
            if n > 0:
                joins.append(at)
                clip.markers.append(
                    marker("donor join, not a cut", start, otio.schema.MarkerColor.BLUE)
                )
            track.append(clip)
            at += frames

    audio = otio.schema.Track(
        name="A1 original audio (review only)", kind=otio.schema.TrackKind.Audio
    )
    original = media_of(vid)
    audio.append(
        otio.schema.Clip(
            name=f"{vid} audio",
            media_reference=original,
            source_range=original.available_range,
        )
    )

    timeline = otio.schema.Timeline(
        name=f"editrace {vid} {'rebuild' if fill == 'original' else 'donors'}",
        global_start_time=rational_time(0, rate),
    )
    timeline.tracks.extend([track, audio])
    return timeline, joins


def cut_frames(timeline: otio.schema.Timeline, rate: Fraction) -> list[int]:
    """Cut frames on the first video track, in frames from the track's start: the start of every clip that doesn't
    start at 0. Gaps count as time, so a dropped or shifted clip shows up as moved cuts.

    `range_of_child()` is already relative to the track, so the timeline's start timecode (Resolve exports
    01:00:00:00 by default) must not be subtracted again."""
    track = next(t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Video)
    frames = []
    for item in track:
        if isinstance(item, otio.schema.Clip):
            at = round(
                track.range_of_child(item).start_time.value_rescaled_to(float(rate))
            )
            if at != 0:
                frames.append(at)
    return sorted(frames)


def compare(expected: list[int], got: list[int]) -> dict:
    """One-to-one match within TOLERANCE, greedy in frame order (cuts here are never closer than 1 frame)."""
    unmatched, offsets, missing = list(got), [], []
    for f in expected:
        near = [g for g in unmatched if abs(g - f) <= TOLERANCE]
        if near:
            g = min(near, key=lambda g: abs(g - f))
            unmatched.remove(g)
            offsets.append(g - f)
        else:
            missing.append(f)
    return {
        "expected": len(expected),
        "matched": len(offsets),
        "exact": sum(o == 0 for o in offsets),
        "off_by_one": sum(o != 0 for o in offsets),
        "missing": missing,
        "extra": unmatched,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument(
        "--fill",
        choices=["original", "donors"],
        default="original",
        help="slot footage: the original's own frames (checks the import) or cut-free shots from other golden"
        " videos (checks the cuts)",
    )
    b.add_argument("videos", nargs="*", default=["v03", "v10", "v15"])
    d = sub.add_parser("diff")
    d.add_argument(
        "--out",
        type=Path,
        required=True,
        help="the build's folder, holding the worker's cuts",
    )
    d.add_argument("export", type=Path, help="the .otio Resolve exported")
    d.add_argument("--video", required=True, help="golden id of the export, e.g. v03")
    args = parser.parse_args()

    if args.cmd == "build":
        if args.out.resolve().is_relative_to(ROOT):
            parser.error(
                "--out must be outside the repo: the timelines point at the private videos"
            )
        args.out.mkdir(parents=True, exist_ok=True)
        for vid in args.videos:
            check_video(vid)
            partial = detect(vid)
            (args.out / f"{vid}.cuts.json").write_text(json.dumps(partial, indent=1))
            timeline, joins = build_timeline(vid, partial, args.fill)
            (args.out / f"{vid}.joins.json").write_text(json.dumps(joins))
            otio.adapters.write_to_file(timeline, str(args.out / f"{vid}.otio"))
            rate = Fraction(source_of(vid)["fps"]["num"], source_of(vid)["fps"]["den"])
            # The written file must give back the worker's cuts (and any donor joins) exactly before Resolve is
            # involved.
            check = compare(
                sorted([c["frame"] for c in partial["tracks"][0]["cuts"]] + joins),
                cut_frames(
                    otio.adapters.read_from_file(str(args.out / f"{vid}.otio")), rate
                ),
            )
            assert check["exact"] == check["expected"] and not check["extra"], check
            flagged = sum(
                c["confidence"] < REVIEW_BELOW for c in partial["tracks"][0]["cuts"]
            )
            print(
                f"{vid}: {len(partial['tracks'][0]['cuts'])} cuts ({flagged} flagged), {len(joins)} donor joins,"
                f" at {rate} fps -> {args.out / f'{vid}.otio'}"
            )
        return

    vid = args.video
    source = source_of(vid)
    rate = Fraction(source["fps"]["num"], source["fps"]["den"])
    cuts_file = args.out / f"{vid}.cuts.json"
    if not cuts_file.exists():
        parser.error(
            f"{cuts_file} not found: run build for {vid} with this --out first"
        )
    partial = json.loads(cuts_file.read_text())
    joins_file = args.out / f"{vid}.joins.json"
    joins = json.loads(joins_file.read_text()) if joins_file.exists() else []
    expected = sorted([c["frame"] for c in partial["tracks"][0]["cuts"]] + joins)
    result = compare(
        expected, cut_frames(otio.adapters.read_from_file(str(args.export)), rate)
    )
    print(json.dumps({"video": vid, "fps": str(rate), **result}, indent=1))
    ok = result["matched"] == result["expected"] and not result["extra"]
    print(
        f"{vid}: {'PASS' if ok else 'FAIL'}, {result['matched']}/{result['expected']} cuts within ±{TOLERANCE} frame"
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

# 0006: Hand detected timelines to DaVinci Resolve as OTIO files with plain media paths (spike #5: go)

- **Status:** Proposed
- **Date:** 2026-11-07
- **Author:** @calvinkafaiyuen
- **Deciders:** @anthonythanpoovong
- **Related:** #5 (this spike), PR #352, #29 (live control spike), #76 (Template → OTIO core), #9 (editor exporters), #353 (v03 label fix), ADR 0002, ADR 0003

## Context

Phase 0's gate asks whether a timeline built from Editrace's detected cuts, rebuilt in an editor, feels like the original. Spike #5 tests the file-based route: the cut worker's output (#7, with ADR 0005's retune merged in #350) → an OpenTimelineIO file → imported into DaVinci Resolve. #29 tests the other route, driving Resolve through its scripting API.

Acceptance criteria from #5:
1. The timeline imports with cuts within 1 frame.
2. Editor feedback and a go/no-go are recorded here.

**Setup.** Three golden videos with different pacing: v03 (talking head over changing backgrounds, 30 detected cuts), v10 (fast cuts with flashes and fades, 55), v15 (talking head, 3). All are 30 fps, 720×1280. Resolve 21.2.0.6 (free) on Windows 11, using File → Import → Timeline. The spike script is `eval/resolve_spike/rebuild.py`.

The review ran in two rounds, because the first round couldn't fail:

- **Round 1, original footage.** Each slot plays the original video's own frames. This checks the import, but the rebuild plays like the original whatever the detector did: a false cut splits a shot seamlessly, and a missed cut plays the original's cut anyway.
- **Round 2, donor footage.** Each slot is filled with a cut-free shot from another golden video, the way a user fills a template with their own footage. The original audio plays underneath. Every detected cut shows as a change of picture, so a false cut shows as a shot change the original doesn't have, and a missed cut as one shot running across the original's cut. One v15 slot (213 frames) was longer than any donor shot, so it took two joined shots, marked in blue and ignored.

## Findings

**1. Import is frame-exact (AC 1: pass).** Resolve's OTIO export of each imported timeline was diffed against the worker's cuts. All **88 of 88 cuts were on the exact frame**, with none off by one. Source in-points also matched, but that was checked outside `rebuild.py`, whose `diff` compares cut positions only.

| Video | Detected cuts | Within ±1 frame | Exact | Source in-points match |
|---|---|---|---|---|
| v03 | 30 | 30 | 30 | 31/31 clips |
| v10 | 55 | 55 | 55 | 56/56 clips |
| v15 | 3 | 3 | 3 | 4/4 clips |

**2. Resolve on Windows can't find `file://` media.** The first import reported "clip not found" for media written as `file:///C:/...` URLs. It imported cleanly once the paths were plain OS paths (`C:\...\v03.mp4`).

**3. The rebuild feels like the original (AC 2: go on all three).** Reviewed by @calvinkafaiyuen, frame by frame against the original, in both rounds. Checked against the golden labels (with the #353 fix):

| | v03 | v10 | v15 | Total |
|---|---|---|---|---|
| Real cuts found | 27/27 | 52/52 | 2/2 | **81/81, 0 missed** |
| False cuts inside the edit | 1 (frame 364, flash) | 1 (frame 259, fade) | 0 | **2, both flagged; the fade one hurts the feel somewhat** |
| Cuts in the platform end card | 2 (931, 932) | 2 (608, 609) | 1 (363) | 5, only v10's flagged |

- **v03, frame 364:** a flash fooled the detector, giving a 7-frame extra shot just before the real cut at 371. It's visible with donor footage. Reviewer: "as long as the flag exists, it doesn't hurt the feel."
- **v10, frame 259:** the start of a fade in from black. The labeled cut into black at 257 is real, and the fade is a transition, not a cut. With donor footage, the two black frames become a 2-frame slot. Reviewer: this one **does hurt the feel somewhat**. It's flagged, so it's visible for review, but the real fix is detecting the fade (#23).
- **Flags did their job.** 6 cuts were flagged below 0.5 confidence. 2 were real but uncertain (v03 371, v10 257), 2 were the false cuts above, and 2 were v10's end card.

**4. The golden set had a missed cut.** v03 frame 164 is a background change (desk to rooftop) in the picture-in-picture section, which the labeling notes count as a cut. The detector found it at confidence 0.99, and it was missing from the label. #353 adds it. v03 precision goes from 0.929 to 0.964.

**5. Platform end cards become slots (known limitation).** The worker doesn't recognise Instagram's end card or its black frame, so it emits 1–2 extra slots at the end of a video. These are flagged in v10 but come through at high confidence in v03 (0.92, 0.72) and v15 (0.93). A user would have to delete them. Per the current priority, this is recorded as a limitation, not new work, until detection itself improves.

## Options considered

### Option A: OTIO file import (tested)
- Write an `.otio` file and import it with File → Import → Timeline.
- ✅ Frame-exact on all 88 cuts. Works in free Resolve. Fits the planned OTIO core (#76), since OTIO adapters cover other editors too.
- ❌ Manual import step. Media paths must be plain, absolute and reachable from the editor's machine.

### Option B: FCPXML or EDL import
- The fallback if OTIO import had lost frames.
- Not needed: OTIO was exact. EDL also drops metadata such as markers.

### Option C: Live control through Resolve's scripting API (#29)
- Builds the timeline in a running Resolve.
- Needs Resolve Studio since 21.1. #29 covers it, and doesn't block this route.

## Decision

**Go.** Editrace hands detected timelines to DaVinci Resolve as **OTIO files, with media referenced by plain OS paths**, not `file://` URLs. The import is frame-exact, and a donor-filled rebuild of v03, v10 and v15 kept the original's feel, with every false cut in the edit flagged for review. The one false cut that hurt the feel, a fade read as a cut, belongs to transition detection (#23), not to this route.

The import result is machine-checked and stands. The feel result was reviewed by the author alone, so it stays **provisional until a design partner repeats the donor review**.

## Consequences

- **Good:** Phase 0's file-based gate is answered on real videos. #76 and #9 can build on OTIO knowing Resolve keeps every frame.
- **Bad / accepted trade-offs:** templates still carry low-confidence false cuts and end-card slots, and the user has to review them. The flags make the false cuts in the edit visible. End cards aren't reliably flagged: v03's and v15's come through at 0.72–0.93 confidence, so #76 must not claim the review flags cover them.
- **One-way or two-way door:** two-way. The exporter could switch to FCPXML or live control later. The cuts are stored in the template, not in OTIO.
- **Follow-up work:** #76 needs a media-path writer per platform: "plain path, not `file://`" is Windows-only evidence. Share these findings with #29. #353 corrects the golden set. #23 (transition classification, Phase 2) doesn't yet cover fades to or from black, or absorbing a false cut inside a fade, as at v10 259; that's been suggested on #23.
- **Affected:** exporter design (#76, #9), golden labels (#353). No schema, database or MCP change.

## How we'll know

The exporter's own Resolve test (#9) keeps every cut within ±1 frame, the same diff `rebuild.py` runs. Revisit if a later Resolve version changes how it imports OTIO, or on macOS, which this spike didn't test.

## Open questions

- **Reviewer independence** (owner: @calvinkafaiyuen). The Phase 0 gate says an editor confirms the feel. This review was done by the author, not an outside editor, so the feel result is provisional (Decision) until a design partner repeats the donor review.
- **Coverage** (owner: @calvinkafaiyuen). There were no missed cuts in these three videos, so the review never had to judge one. A video with known misses would test that side.
- **Donor footage is a stand-in.** It's other creators' shots, not a user's own footage. Real footage might make wrong cuts more or less noticeable.
- **macOS and Premiere/Final Cut** weren't tested. #9 covers them.

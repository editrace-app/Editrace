# 0005: Retune AdaptiveDetector to threshold 1.7 and window 3 for recall, provisionally

- **Status:** Proposed
- **Date:** 2026-11-07
- **Author:** @calvinkafaiyuen
- **Deciders:** @anthonythanpoovong
- **Related:** #7 (cut worker), PR #350 (the change), #52 (golden set), #311 (precision 0.90, held-out check), #320 (Modal), ADR 0003
- **Supersedes:** 0003

## Context

[ADR 0003](0003-use-pyscenedetect-adaptive-for-cut-detection.md) built #7 on PySceneDetect's AdaptiveDetector at `adaptive_threshold` 2.0 (window 2, the default), kept every detected cut and flagged suspect ones for review. Its milestone-2 gate said: if t=2.0 doesn't reach recall ≥ 0.95 and precision ≥ 0.75 on #52's golden set, revisit this decision before #7 ships.

The gate tripped. On 14 labeled videos (v11's local file isn't the labeled video), t=2.0 gives **P 0.881 / R 0.925** (245 of 265 cuts, 33 false). The misses cluster in two videos: v12's flash burst (frames 265–281), where the rolling average rises and each cut stops standing out, and v14's 1-frame shots. A sweep of the detector's settings ([tag `tuning-7-recall`](https://github.com/editrace-app/Editrace/tree/tuning-7-recall/eval/cuts_tuning)) found settings that pass, but only on the videos they were chosen on.

Everything else in ADR 0003 stands: the detector, keep every cut, flag before filter, the regression floor, the golden-set roadmap and the production revisit check. This ADR changes only the settings, and adds a rule for when they stop being provisional.

## Options considered

All numbers on the same 14 videos, ±2 frames, end cards excluded. "Leave-one-out" picks settings on 13 videos and scores the 14th, for each video.

### Option A: threshold 1.7, window 3 (PR #350)
- ✅ In-sample **P 0.852 / R 0.958**: finds 9 more real cuts (v12's burst 15 → 21 of 27, v14's 1-frame shots, v13's only cut).
- ❌ Leave-one-out **R 0.932 / P 0.837**: about 2 more real cuts per 265 on unseen videos, for about 11 more false cuts. v13's lighting flashes go from 6 to 13 false cuts. The floor passes by 2 cuts (252 of 265 needed), within platform decoding noise of about 1 cut.
- Cost: two constants and a worker version bump.

### Option B: keep 2.0 and lower the recall floor to about 0.92 until there are held-out videos
- ✅ Honest about what the evidence supports; no new false cuts.
- ❌ Accepts more missed cuts, the expensive mistake per ADR 0003, and leaves v12/v14-style edits unfound.

### Option C: 2.0, plus any frame with content ≥ 60 counted as a cut
- In-sample P 0.834 / R 0.966. More false cuts than A for a little more recall; same overfitting risk.

### Option D: a second stage (TransNetV2), or comparing against the quieter side of the window
- TransNetV2 found no cuts the lower threshold missed, and deleted real fast and overlay cuts. The quieter-side ratio gives P 0.743, under the precision floor. Both rejected.

### Option E: change nothing until #52 has flash-burst and 1-frame-shot videos
- ✅ Tunes on evidence that can be held out.
- ❌ #7 ships under its own floor, with no date for the new videos.

## Decision

We will run AdaptiveDetector at **`adaptive_threshold` 1.7 and `window_width` 3**, **provisionally**, because ADR 0003's cost asymmetry still holds: a false cut costs a merge (and half of A's false cuts are flagged), while a missed cut costs finding the frame and another clip. On unseen videos the trade is roughly 2–3 real cuts for 10 or more merges; we judge a missed cut worth more than about 4 merges. The change is cheap to undo, so we take it now and let held-out videos confirm or reverse it.

## Consequences

- **Good:** #7's recall gap closes on the current set; v12- and v14-style edits are found.
- **Bad / accepted trade-offs:** about a third more false cuts (33 → 44), worst on lighting flashes over the same footage (v13), which become #311's problem. Every cut's `confidence` shifts for the same ratio (ratio 4: 0.75 → 0.79), and the flag rule's settings (`DISTINCT_MIN`, `PAIR_WINDOW_S`) were tuned at 2.0; the flag-rate gate still holds (37/254 real flagged, 22/44 false).
- **One-way or two-way door:** two-way. Two constants in `workers/cuts/detect.py`; the worker version (0.2.0) and `window_width` in its params keep cached 2.0 results from being served. Reverting is a revert plus a golden-set rerun.
- **Follow-up work:** held-out re-check when #52 adds flash-burst, 1-frame-shot and lighting-flash videos (#311); Modal latency, cost and sign-off numbers (#320).
- **Affected:** #7 worker and its tests. Schema, DB, RLS and MCP contracts unchanged.

## How we'll know

On #52's held-out videos (none are held out yet), score both 1.7/window 3 and 2.0/window 2:
- **Keep 1.7** and drop "provisional" if it finds at least N more real cuts than 2.0 and holds recall ≥ 0.95 and precision ≥ 0.75. Then confirm on Modal (#320), whose numbers count for sign-off.
- **Revert to 2.0** if 1.7 finds fewer than N more real cuts than 2.0: it would be paying precision for nothing.
- **Replace** with any setting, detector or second stage that beats the current one on held-out recall while staying at or above the precision floor.
- If neither setting reaches 0.95, revisit the floor itself (Option B).

ADR 0003's production revisit check (merges and adds per template) still applies.

## Open questions

- **N, the margin that counts as a real gain.** Proposed default: 2% of held-out labeled cuts, and at least 3 cuts, since decoding differs by about 1 cut between platforms. Owner: @anthonythanpoovong to confirm.
- **"A missed cut costs more than about 4 merges" is the author's reasoning, not user evidence**, as in ADR 0003; `docs/customers/` has nothing on cut review yet. Owner: @calvinkafaiyuen.
- **Latency and cost at the new settings are unmeasured.** More detections mean more `suspect_cuts()` decoding; local p95 was 23.8 s at 2.0. Measured on Modal in #320. Owner: @calvinkafaiyuen.
- **The production check needs review logging**, which waits on #18 and #343. Owner: @anthonythanpoovong (#18).

<details><summary>Grill scorecard</summary>

| Area | Verdict | Note |
|---|---|---|
| Problem & evidence | ⚠️ weak | ADR 0003's gate tripped (R 0.925); no customer evidence on missed vs. false cut cost |
| Smallest version | ✅ solid | Two constants and a version bump |
| Alternatives | ✅ solid | Lower floor, content ≥ 60, quieter-side ratio, TransNetV2 stage, wait for #52 |
| Fit with Key Decisions | ✅ solid | None touched; still deterministic |
| Data & schema | ✅ solid | No schema or DB change; cache key covers the new settings |
| Security & privacy | n/a | No change |
| Cost | ⚠️ weak | Unmeasured at 1.7 and on Modal (#320) |
| Failure modes | ✅ solid | More false cuts, half flagged; rollback is a revert |
| Reversibility | ✅ solid | Two-way door; keep / revert / replace rule above |
| Observability | ⚠️ weak | Production check waits on #18 / #343 |
| Success metric | ✅ solid | Held-out recall ≥ 0.95, precision ≥ 0.75, then Modal |
| Effort & ownership | ✅ solid | @calvinkafaiyuen; code in #350 |

Three ⚠️, one over the rule; the author overrode, since all three are inherited from ADR 0003 or wait on other issues.

</details>

# 0005: Retune AdaptiveDetector to threshold 1.7 and window 3 for recall, provisionally

- **Status:** Proposed
- **Date:** 2026-11-07
- **Author:** @calvinkafaiyuen
- **Deciders:** @anthonythanpoovong
- **Related:** #7 (cut worker), PR #350 (the change, merged), #52 (golden set), #311 (precision 0.90, held-out check), #320 (Modal), ADR 0003
- **Supersedes:** 0003

## Context

[ADR 0003](0003-use-pyscenedetect-adaptive-for-cut-detection.md) built #7 on PySceneDetect's AdaptiveDetector at `adaptive_threshold` 2.0 (window 2, the default), kept every detected cut and flagged suspect ones for review. Its milestone-2 gate said: if t=2.0 doesn't reach recall ≥ 0.95 and precision ≥ 0.75 on #52's golden set, revisit this decision before #7 ships.

The gate tripped. On 14 of the 15 labeled videos, t=2.0 gives **P 0.881 / R 0.925** (245 of 265 cuts, 33 false). The misses cluster in two videos: v12's flash burst (frames 265–281), where the rolling average rises and each cut stops standing out, and v14's 1-frame shots. A sweep of the detector's settings ([tag `tuning-7-recall`](https://github.com/editrace-app/Editrace/tree/tuning-7-recall/eval/cuts_tuning)) found settings that pass, but only on the videos they were chosen on.

**v11 is unscored** in every number here: the only local copy is a different file from the labeled video (639 frames, against 303 labeled), so the runs left it out by marking it unlabeled in a local, uncommitted manifest edit. It's scored once the labeled file is back.

This ADR changes only the settings, and adds a rule for when they stop being provisional. Everything else in ADR 0003 stays in force and is not superseded:
- **The detector and its rules:** AdaptiveDetector, `min_scene_len` 1, keep every detected cut, flag suspect ones, and flag before filter ([Decision](0003-use-pyscenedetect-adaptive-for-cut-detection.md#decision)).
- **The regression floor:** recall ≥ 0.95 and precision ≥ 0.75 at ±2 frames, end cards excluded; target precision 0.90 (#311) ([How we'll know](0003-use-pyscenedetect-adaptive-for-cut-detection.md#how-well-know)).
- **The golden-set roadmap:** 30 videos by Phase 1 exit, 10 held out and scored only at the end ([Consequences](0003-use-pyscenedetect-adaptive-for-cut-detection.md#consequences)).
- **The production revisit check**, at Phase 1 exit or 200 reviewed templates: revisit the detector if users add more than 5% as many cuts as were detected; consider automatic removal if 90% or more of flagged cuts are merged ([How we'll know](0003-use-pyscenedetect-adaptive-for-cut-detection.md#how-well-know)).

## Options considered

All numbers on the same 14 videos, ±2 frames, end cards excluded. "Leave-one-out" picks settings on 13 videos and scores the 14th, for each video.

### Option A: threshold 1.7, window 3 (PR #350)
- ✅ In-sample **P 0.852 / R 0.958**: finds 9 more real cuts (v12's burst 15 → 21 of 27, v14's 1-frame shots, v13's only cut).
- ❌ Leave-one-out **R 0.932 / P 0.837**: about 247 real cuts and 48 false, against 2.0's 245 and 33. On unseen videos that's **about 2 more real cuts for 15 more false ones**, 7–8 merges per cut recovered. (Leave-one-out re-picks the best setting in each round, so it estimates tuning on this data in general, not 1.7 alone; it's still the best held-out estimate there is.)
- ❌ In-sample, v13's lighting flashes go from 6 to 13 false cuts, and the floor passes by 2 cuts (252 of 265 needed), within platform decoding noise of about 1 cut.
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

We will run AdaptiveDetector at **`adaptive_threshold` 1.7 and `window_width` 3**, **provisionally, as a bet**: that held-out videos will look more like v12 and v14 (flash bursts, 1-frame shots) than the leave-one-out run suggests.

The current evidence doesn't support it on cost. ADR 0003's asymmetry still holds (a false cut costs a merge, and half of A's false cuts are flagged; a missed cut costs finding the frame and another clip), and we judge a missed cut worth about 4 merges. But on the leave-one-out estimate, 1.7 costs 7–8 merges per cut recovered, so it fails that test. We take the bet anyway because it's a two-way door: two constants, already merged in #350. The keep rule below reverts it automatically if held-out videos don't bear it out.

## Consequences

- **Good:** #7's recall gap closes on the current set; v12- and v14-style edits are found.
- **Bad / accepted trade-offs:** more false cuts: 33 → 44 in-sample, about 48 on the leave-one-out estimate, worst on lighting flashes over the same footage (v13), which become #311's problem. Every cut's `confidence` shifts for the same ratio (ratio 4: 0.75 → 0.79), and the flag rule's settings (`DISTINCT_MIN`, `PAIR_WINDOW_S`) were tuned at 2.0; the flag-rate gate still holds (37/254 real flagged, 22/44 false).
- **One-way or two-way door:** two-way. Two constants in `workers/cuts/detect.py`; the worker version (0.2.0) and `window_width` in its params keep cached 2.0 results from being served. Reverting is a revert plus a golden-set rerun.
- **Follow-up work:** held-out re-check when #52 adds flash-burst, 1-frame-shot and lighting-flash videos (#311); Modal latency, cost and sign-off numbers (#320).
- **Affected:** #7 worker and its tests. Schema, DB, RLS and MCP contracts unchanged.

## How we'll know

On #52's held-out videos (none are held out yet), score both 1.7/window 3 and 2.0/window 2:
- **Keep 1.7** and drop "provisional" only if all of these hold for 1.7 against 2.0:
  - recall ≥ 0.95 and precision ≥ 0.75;
  - extra real cuts × 4 ≥ extra false cuts (the cost rule in the Decision);
  - at least 3 extra real cuts (platform decoding differs by about 1 cut).

  Then confirm on Modal (#320), whose numbers count for sign-off.
- **Revert to 2.0** if any of them fails. On current evidence (+2 real, +15 false) **the keep test is expected to fail**.
- **Replace** with any setting, detector or second stage that beats the current one on held-out recall while staying at or above the precision floor.
- If neither setting reaches 0.95, revisit the floor itself (Option B).

ADR 0003's production revisit check (merges and adds per template) still applies.

## Open questions

- **"A missed cut is worth about 4 merges" is the author's reasoning, not user evidence**, as in ADR 0003, and the keep rule depends on it; `docs/customers/` has nothing on cut review yet. Owner: @calvinkafaiyuen.
- **Latency and cost at the new settings are unmeasured.** More detections mean more `suspect_cuts()` decoding; local p95 was 23.8 s at 2.0. Measured on Modal in #320. Owner: @calvinkafaiyuen.
- **The 0.95 recall floor was set before spike #4, without a derivation.** If neither setting reaches it on held-out videos, revisit the floor itself (Option B) rather than tuning further. Owner: @calvinkafaiyuen.
- **The production check needs review logging**, which waits on #18 and #343. Owner: @anthonythanpoovong (#18).

<details><summary>Grill scorecard</summary>

| Area | Verdict | Note |
|---|---|---|
| Problem & evidence | ⚠️ weak | ADR 0003's gate tripped (R 0.925); leave-one-out doesn't support 1.7 on cost, so it's a bet; no customer evidence on missed vs. false cut cost |
| Smallest version | ✅ solid | Two constants and a version bump |
| Alternatives | ✅ solid | Lower floor, content ≥ 60, quieter-side ratio, TransNetV2 stage, wait for #52 |
| Fit with Key Decisions | ✅ solid | None touched; still deterministic |
| Data & schema | ✅ solid | No schema or DB change; cache key covers the new settings |
| Security & privacy | n/a | No change |
| Cost | ⚠️ weak | Unmeasured at 1.7 and on Modal (#320) |
| Failure modes | ✅ solid | More false cuts, half flagged; rollback is a revert |
| Reversibility | ✅ solid | Two-way door; break-even keep rule, revert otherwise |
| Observability | ⚠️ weak | Production check waits on #18 / #343 |
| Success metric | ✅ solid | Held-out recall ≥ 0.95, precision ≥ 0.75, then Modal |
| Effort & ownership | ✅ solid | @calvinkafaiyuen; code in #350 |

Three ⚠️, one over the rule; the author overrode, since all three are inherited from ADR 0003 or wait on other issues.

</details>

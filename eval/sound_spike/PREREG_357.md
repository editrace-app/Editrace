# #357 pre-registration (2026-11-11, before any new lookup)

Fixed before running any experiment, so the dev results can't steer the held-out test. Spike code only
(`spike/357-acrcloud-thresholds`, not merged).

## Baseline (#11, unchanged)
ACRCloud, one 12 s excerpt centred on the clip's middle, top match shown: **16/20 released right, 4/30 wrong,
1 unresolved** (5/30 counting it as wrong). Saved #11 responses are reused, so start and middle cost no new calls.

## Data
- 30 rights-cleared clips from #11: 20 released, 8 original/remix, 2 no music. The clips, answer key and raw
  responses stay private (`.git/info/exclude`).
- Answer key `answers.csv`, SHA-256 `4f5c8c2628d78c7a2d47ceec2b51296aa86023a0de135f18493f74131728fa66`. It is
  frozen and agrees with the corrected golden manifest (#346).
- All 30 clips already informed #11's choice of ACRCloud middle, so none of them is an untouched holdout.
- **Split** (`split.json`, seed 357): 20 dev / 10 held-out, stratified by music type × #11 baseline outcome
  (right / wrong-or-unresolved / no match). The held-out set has 2 baseline wrong-or-unresolved clips and the
  dev set has 3. No song appears in more than one clip, so the split can't leak a song. The held-out set is
  **shared with #358**, and neither spike tunes on it.

## Lookups (ACRCloud only, blind)
All new lookups run on all 30 clips before any scoring, and they never read the answer key. The held-out
results stay unscored until the policy is fixed. That way the held-out run doesn't depend on the trial still
working later.

| Arm | What | Lookups |
|---|---|---|
| p25, p75 | 12 s excerpt centred at 25% / 75% of the clip | 60 |
| mid8, mid15 | middle excerpt, 8 s / 15 s | 60 |
| stem | middle 12 s with vocals removed by Demucs `htdemucs` (4.1.0), in memory | 30 |
| speed | middle 12 s resampled (speed and pitch together) at 0.93, 1.08, 0.87, 1.18, 0.80, **only where the saved middle lookup found nothing** (5 clips) | 25 |
| control | saved start and middle from #11 | 0 |

**Budget:** at most 250 new lookups (agreed 2026-11-11). 175 are planned, plus 2 access checks already made
(silence → 2004, noise → 1001). The harness stops at the cap, or on any vendor error other than "no result".
If the quota runs out partway, the arms that didn't finish are reported as untested.

## Policies (576, all scored on dev)
**Selection** (12):
- a single excerpt: start, middle, p25, p75, mid8, mid15 or stem
- middle, falling back to start when middle finds nothing
- middle, then the speed retries in the order above until one matches
- start and middle must agree on the title
- 2-of-4 and 3-of-4 title votes over start, p25, middle and p75

**Postprocessing filters on the shown match:** score threshold τ ∈ {none, 80, 100}, each combined with every
on/off combination of:
- ISRC present
- a Spotify, Deezer or YouTube ID present
- all returned candidates agree on the title
- no re-upload words in the title, album or artist (`sped, slowed, nightcore, reverb, tiktok, 8d, karaoke,
  instrumental, cover, tribute, lofi, mashup, remix, edit, version`)

A filtered-out match counts as an abstention: nothing is shown.

## Selection rule (dev only)
Among policies with **wrong ≤ 5% of shown suggestions** (unresolved counts as wrong), pick the one with the
**most released songs right**. Tie-breaks, in order: fewer lookups per clip, fewer filters, then name. If no
policy qualifies, the baseline is kept and the recommendation is reject or conditional. The chosen policy is
committed (`policy.json`) before the single held-out run.

## Reported (dev and held-out)
- released recall
- wrong per analysed clip AND per shown suggestion, with unresolved shown separately and counted as wrong
- coverage, abstentions and raw counts
- Wilson 95% intervals (held-out)
- lookups per analysis
- cost per analysis at $6 per 1k valid queries, where a no-result counts as half
- latency: the sum of lookup and preprocessing time; stem time includes loading the Demucs model per excerpt

## Known limits
- Tiny sample: the held-out set has 10 clips, 2 of them baseline-wrong.
- 576 policies are compared on 20 dev clips, so the dev winner's numbers are optimistic. The held-out run is
  the check.
- Trial-plan responses may differ from paid-plan responses.
- Any adoption still needs owner confirmation and the #21/#56 gates; this spike changes nothing in production.

# 0007: Use ACRCloud for sound suggestions that the owner confirms

- **Status:** Proposed
- **Date:** 2026-11-10
- **Author:** @sktchan
- **Deciders:** code owners; approval requires review and merge
- **Related:** [#11](https://github.com/editrace-app/Editrace/issues/11), #14, #18, #21, #56; ADR 0002

## Context

Sound identification can help a creator find the reference soundtrack without storing or exporting audio. The upload-first, no-audio-export and schema-driven Key Decisions still apply. No customer interviews validating demand have been logged.

The [scored spike](https://github.com/editrace-app/Editrace/issues/11#issuecomment-6550249894) compared 12-second start and middle excerpts with ACRCloud and AudD. On 2026-11-10, re-scoring the saved responses against the private answer key reproduced the findings; no new vendor calls were made.

| Policy | Released songs correct | Wrong matches / all clips |
|---|---|---|
| ACRCloud, middle | 16/20 (80%) | 4/30 (13%); 5/30 (17%) if the unresolved result is wrong |
| ACRCloud, start | 15/20 (75%) | 5/30 (17%), plus two unresolved |
| AudD, start | 16/20 (80%) | 5/30 (17%), plus one unresolved |
| AudD, middle | 14/20 (70%) | 7/30 (23%) |

The set contains 20 released tracks, 8 original sounds and 2 clips without music: **two fewer original sounds than planned**. Results are descriptive, not a held-out estimate. Shared catalog re-uploads caused wrong title/artist matches; vendor agreement and a high vendor score did not establish correctness. The best observed policy was selected on this same small set.

**Neither vendor passed the original ≤5% wrong-match exit.** @sktchan chose to propose a different product contract: a suggestion that the owner must confirm. This changes the exit after seeing results; it is not a claim that automatic identification passed, or that the team has already approved the change.

## Options considered

- **ACRCloud middle-excerpt suggestion:** best observed error rate; one lookup. Adds an external processor and confirmation UI; user confirmation has not been evaluated for accuracy.
- **AudD suggestion:** tied on released-song accuracy with its start excerpt, but more wrong matches overall; no observed advantage for this set.
- **Automatic identification / vendor agreement:** lower interaction cost, but fails the original safety bar; both vendors can return the same wrong match.
- **Defer sound ID, export beat markers only:** no recognition cost, new processor or confirmation work. Strong fallback and the original No outcome; loses suggested track discovery.

## Decision

Propose **one ACRCloud lookup on a centered excerpt of up to 12 seconds**, offering the result as an optional suggestion. Short clips use the available audio. The production excerpt policy must be tested before rollout; the spike numbers describe 12-second excerpts only.

1. Show “Sounds like: [title] — [artist]” with Confirm and Dismiss actions. It must be clear that identification can be wrong. Viewing or ignoring a suggestion is not confirmation.
2. Keep the candidate outside the template in private application state. Only the authenticated template owner can confirm it, through an ownership-checked server action. Confirming promotes the candidate to `sound_ref`; dismissal or no action leaves `sound_ref` absent. Reversal removes the confirmed reference. Updates must reject stale candidates.
3. Do not expose unconfirmed candidates as `sound_ref` through exports, MCP or downstream consumers. Templates and exports remain usable without a sound reference; audio is never included.
4. No match, timeout, vendor outage or disabled feature yields beat-only output and never fails template creation. Do not automatically retry the lookup or query a second vendor. Repeated requests for the same analysis reuse its result; a new candidate requires fresh confirmation.
5. Send the short excerpt from worker memory; do not persist audio, credentials or raw vendor responses in application logs. Keep keys server-side. Retain only the candidate metadata needed by the private flow, under the approved deletion policy.
6. **Production rollout is blocked on #21's written guidance**, reflected in #56, covering consent, processor terms, region, retention and display/link rights. The tested project was US West; production region is not decided. ACRCloud may retain recognition logs/derived data and gave no zero-retention commitment.

## Consequences

- **Good:** creators can accept useful suggestions while unreviewed matches never become template references. Beat analysis remains independent.
- **Trade-offs:** users may confirm a wrong match; confirmation changes interaction, not model accuracy. Confirmation, reversal, private persistence and access tests add work to #14/#18. Pilot demand and usefulness remain unvalidated.
- **Cost:** the [2026-10-22 vendor quote](https://github.com/editrace-app/Editrace/issues/11#issuecomment-6344668699) gives $0.006/match and $0.003/no-result at the entry tier: approximately $3–6 / $30–60 for 1k / 10k lookups. The vendor estimated $267–487 for 100k requests; tier boundaries were unspecified. These are historical quote figures, not a current billing guarantee. One lookup per analysis and the existing analysis quotas bound usage; verify pricing before activation. Observed ACRCloud latency: median 1.13 s, p95 4.47 s.
- **Two-way door:** disable suggestions and continue with beat markers; stop new vendor calls. Preserve previously confirmed references unless the owner removes them or their template is deleted.
- **Affected contracts:** `sound_ref` is already optional. Do not add confirmation fields to its closed object. No schema change is proposed here. Private candidate storage, RLS, confirmation audit and confirmation-aware provenance/confidence mapping must be specified and reviewed before implementation; a vendor score is not a calibrated probability, and confirmation does not make it 1.0.
- **Follow-up:** after approval, update #14 and #18 with the confirmation/failure paths and tests, and sync the plan/ADR index. This proposal does not authorize shipping or declare #11 complete.

## How we'll know

Before rollout, two-user tests must prove that only the owner can confirm/dismiss/reverse a candidate, and no unconfirmed candidate reaches `sound_ref` in template reads or exports. Test ignored/dismissed/no-match/outage states, stale confirmations, repeated analysis requests and audio non-persistence. Re-run these checks for MCP when it ships.

At the Sprint 4 partner pilot review (2026-11-25), report suggestions shown, confirmations, dismissals, reversals, lookup failures, latency and cost without logging audio or private content. Any unconfirmed candidate exposed as `sound_ref` blocks rollout. Track confirmed-but-wrong reports separately: confirmation rate is not identification accuracy. Revisit or disable suggestions if partners find them misleading or not useful; numerical adoption targets require partner evidence.

## Explicit review decision: sample deviation

The evaluated set has **20 released tracks, 8 original/remix cases and 2 no-music clips**, rather than the planned ≥10 original/remix cases. The numeric sample criterion is not met. No additional labeled clips are currently available locally.

Code-owner reviewers must explicitly choose in their review:
- **Accept the 20/8 sample deviation for this provisional, owner-confirmed suggestion contract**, without claiming the original exit or an original-sound accuracy target passed; dataset expansion remains tracked in #358; or
- **Require the two missing original/remix cases before merge**, followed by a separately reported evaluation without retuning on those cases.

An approval that does not address this question does not resolve the sample deviation. The proposed rationale for accepting it is the limited suggestion-only contract, beat-only fallback and explicit absence of original-sound performance guarantees; these are scope controls, not additional accuracy evidence. The ≤5% wrong-match exit still failed. Reviewer acceptance is pending and is not asserted by this document.

## Open questions

- **@anthonythanpoovong, #21/#56:** production region, vendor terms/pricing, consent, metadata retention/deletion and link rights. Required before activation.
- **@sktchan + #18's owner:** candidate persistence/API, reversal UX, audit and provenance/confidence mapping. Required before implementation; do not silently store candidates in `sound_ref`.
- **@sktchan:** expand the original-sound set and evaluate new data separately before making stronger accuracy claims.

<details><summary>Grill scorecard — Ready to draft a proposal; rollout remains gated</summary>

| Area | Verdict | Evidence / condition |
|---|---|---|
| Problem, demand, success metric | ⚠️ | No interviews; partner usefulness and targets unvalidated. Pilot review above. |
| Smallest version, alternatives, cost, failure modes | ✅ | One optional lookup; alternatives and beat-only fallback; historical quote bounded by quotas. |
| Key Decisions, data, privacy | ⚠️ | Owner chose confirmed-only sound_ref; private storage design and legal guidance required before implementation/rollout. |
| Reversibility, observability, ownership | ✅ | Disable calls without blocking templates; metrics and follow-ups assigned above. |

</details>

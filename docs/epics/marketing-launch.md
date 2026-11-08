# Epic proposal: Marketing launch (stories for #47)

- **Status:** Accepted (2026-09-30, via #113)
- **Date:** 2026-09-30
- **Author:** @anthonythanpoovong
- **Owner(s):** @anthonythanpoovong
- **Phase:** A = Phases 0–1 (pre-launch, organic) · B = Phase 2 (post-launch, paid)
- **Related:** #47 Discovery, legal & GTM (parent epic), #54 landing + waitlist, #20 interviews, #55 design partners, #56 terms/privacy, #19 billing
- **Epic issue:** Not a new epic. Stories under **#47**: #114 (1), #115 (2), #116 (3), #117 (4), #118 (5), #119 (6), #120 (7), #121 (8)

## Problem

Editrace has no brand, no social presence and no way to reach its audience. #47 covers the landing page, interviews and design partners, but not **how people find us**. We need an audience before launch (to fill interviews and the design-partner beta) and a path to paid acquisition after it.

**Evidence:** none logged yet (`docs/customers/` is empty). This work is partly how we get evidence: conversations it produces are logged with `/log-interview`.

## Goal

**Phase A (organic, $0):** CapCut creators and UGC agencies can find Editrace on TikTok and Instagram, join the waitlist, and book conversations, feeding interviews (#20) and design partners (#55).

**Phase B (paid, post-launch):** a Meta ads test that measures what a paying customer costs.

## Audience

1. **Solo creators** who edit in **CapCut** and post to **TikTok and Reels** (reach)
2. **UGC agencies** producing short-form ads for brands, mostly in CapCut (revenue: they pay as businesses)

## Exit criteria

- Brand kit and social accounts live, with attribution (UTM links + waitlist "how did you hear" field)
- 2 posts/week sustained on TikTok and Reels
- Meta Business, ad account and Pixel ready (no spend) after #56
- Phase B test plan ready to run when the MVP, billing and budget exist

## Success metric

By the **Phase 1 gate (MVP launch)**:
- **10 qualified conversations** (creators or UGC agencies) sourced from social or the waitlist, logged with `/log-interview`
- **5 design partners** (the #55 target) sourced from marketing channels

These match #47's exit criteria (10 interviews, 5+ design partners).

## Out of scope

- **Paid ads before launch.** The budget is $0 now. Phase B only.
- **Agency / freelance editors on Premiere, Resolve or FCP** as a primary audience. Later.
- **Promising "AI applies it in your editor" (MCP) to CapCut users** before Phase 2, since the CapCut adapter (#36) is Phase 2. Lead with the breakdown + CapCut export (#10, Phase 1).
- Product Hunt, newsletter, affiliate/referral programs: not now.

## Alternatives considered

- **Paid ads now:** rejected. There's no budget, and nothing to buy yet.
- **Every channel at once:** rejected. One person can sustain 2 channels plus outreach.
- **Do nothing until MVP:** rejected. Launching to no audience wastes the launch, and interviews and design partners need a source.

## Rules

- **Demo and post content use only footage we own, licensed stock, or clips from creators who've agreed in writing.** Never repost someone else's viral video or its audio. It's a copyright strike risk, and it contradicts the Key Decision against copyrighted audio.
- **The Meta Pixel and any tracking go live only after** the privacy policy (#56) and a consent banner.
- No personal data (names, handles, emails of prospects) in the repo. Outreach is tracked outside it.
- Anything shown before the MVP is labeled **"preview"**.

## Stories

### 1. Brand kit
- **Labels:** `story`, `type:build`, `area:marketing`, `product`
- **Owner:** @anthonythanpoovong · **Phase:** 0
- **Context:** every channel needs a consistent identity.
- **Scope:** logo (wordmark + icon), color palette, 1–2 fonts, profile picture and banner exports for TikTok, Instagram, X and LinkedIn. DIY in Figma (free).
- **Acceptance criteria:**
  - [ ] Logo in SVG + PNG (light and dark), icon legible at 32 px
  - [ ] Brand kit (colors, fonts, usage) stored in a shared place and linked from the wiki
- **Depends on:** —

### 2. Claim social accounts + attribution
- **Labels:** `story`, `type:build`, `area:marketing`, `product`
- **Owner:** @anthonythanpoovong · **Phase:** 0
- **Context:** the audience is on TikTok and Reels; agencies also use LinkedIn and X.
- **Scope:** claim `@editrace` (or the closest available, the same everywhere) on TikTok, Instagram, X and LinkedIn. Set up business/creator profiles with the brand kit. The UTM-tagged waitlist link in each bio is part of #54.
- **Acceptance criteria:**
  - [ ] Same handle (or documented variants) on all 4 platforms, with 2FA on and shared credentials in a password manager
- **Depends on:** story 1

### 3. UGC agency outreach
- **Labels:** `story`, `type:research`, `area:marketing`, `product`
- **Owner:** @anthonythanpoovong · **Phase:** 0
- **Context:** UGC agencies pay as businesses and are reachable directly.
- **Scope:** a list of 30 UGC agencies, an outreach script (DM + email), a follow-up cadence. Route replies to interviews (#20, logged with `/log-interview`) and design partners (#55). Track the pipeline outside the repo.
- **Acceptance criteria:**
  - [ ] 30 agencies contacted, reply rate recorded
  - [ ] Every conversation logged in `docs/customers/` (no personal data)
- **Depends on:** story 2

### 4. Creator and UGC communities
- **Labels:** `story`, `type:build`, `area:marketing`, `product`
- **Owner:** @anthonythanpoovong · **Phase:** 0
- **Context:** communities are free reach, and a source of honest feedback.
- **Scope:** join 5 communities where CapCut creators and UGC creators/agencies gather (e.g. r/CapCut, UGC creator groups and Discords). Read and follow each one's self-promotion rules. Share useful edit breakdowns and tips first, and link only where allowed.
- **Acceptance criteria:**
  - [ ] 5 communities joined, with their rules noted
  - [ ] At least 1 value-first post per community per month; signups from them tracked by UTM
- **Depends on:** story 2

### 5. Launch demo video (20 s)
- **Labels:** `story`, `type:build`, `area:marketing`, `product`
- **Owner:** @anthonythanpoovong · **Phase:** 1
- **Context:** one short video that shows what Editrace does, reused on the landing page (#54) and socials.
- **Scope:** upload → breakdown → CapCut export, in about 20 s, vertical (9:16) + horizontal cuts. **Owned or licensed footage only.** Labeled "preview" until the MVP ships. No MCP/"AI applies it" claim for CapCut.
- **Acceptance criteria:**
  - [ ] Every clip and audio track in it is owned or licensed, with sources recorded
  - [ ] Used on the landing page and pinned on TikTok and Instagram
- **Depends on:** story 1, #54

### 6. Content engine
- **Labels:** `story`, `type:build`, `area:marketing`, `product`
- **Owner:** @anthonythanpoovong · **Phase:** 1
- **Context:** organic reach needs a steady, sustainable cadence from one person.
- **Scope:** a content calendar and the first 10 posts. Formats: edit breakdowns (with permitted clips), CapCut tips, build-in-public updates. **2 posts/week** on TikTok and Reels.
- **Acceptance criteria:**
  - [ ] 10 posts published on schedule, all following the footage rule
  - [ ] Monthly review of views, profile clicks and waitlist signups per channel (UTM)
- **Depends on:** stories 1, 2, 5

### 7. Meta Business setup (no spend)
- **Labels:** `story`, `type:build`, `area:marketing`, `area:frontend`, `product`
- **Owner:** @anthonythanpoovong · **Phase:** 1
- **Context:** Phase B ads need an ad account and conversion tracking with history before any spend.
- **Scope:** Meta Business portfolio, ad account, the Instagram account connected. Meta Pixel on the landing page, with a signup conversion event, **behind a consent banner**.
- **Acceptance criteria:**
  - [ ] The Pixel fires only after consent, and the privacy policy (#56) covers it
  - [ ] The signup conversion shows in Events Manager; $0 spent
- **Depends on:** story 2, #54, #56

### 8. Meta ads launch test (Phase B)
- **Labels:** `story`, `type:research`, `area:marketing`, `product`, `risk:blocked`
- **Owner:** @anthonythanpoovong · **Phase:** 2
- **Context:** after launch, measure what a paying customer costs before scaling spend.
- **Scope:** a test plan: budget, audiences (CapCut creators, UGC agencies), 3 creatives from content that worked organically, a target cost per signup and per paying customer, and a stop/scale rule.
- **Acceptance criteria:**
  - [ ] Test plan approved with a budget before any spend
  - [ ] Results recorded: cost per signup, cost per paying customer, and the decision to stop or scale
- **Depends on:** story 7, #19, MVP gate (Phase 1), a budget

## Risks & costs

| Risk / cost | Likelihood / size | Mitigation |
|---|---|---|
| Copyright strike or ban from reposted content | High if unmanaged | Footage rule; sources recorded for every post |
| `@editrace` taken on some platforms | Medium | Check all platforms first, and use one consistent variant |
| One person can't sustain the cadence alongside product work | High | 2 posts/week max; batch-produce content |
| Marketing promises features before they ship (e.g. MCP for CapCut) | Medium | "Preview" labels; lead with Phase 1 features |
| Tracking without consent | Low with the rule | Pixel only after #56 + consent banner |

Cost: $0 in Phase A (free tools). Phase B budget: TBD in story 8.

## Security & data

No schema, DB or MCP changes. The waitlist gains a "how did you hear" field (#54). Prospects' personal data stays out of the repo. Tracking (the Pixel) only after #56 and consent.

## Open questions

- **Evidence:** no interviews yet. This epic is meant to produce them (metric b).
- **Capacity:** all stories are owned by one person who also owns product, MCP and RAG. Revisit if the cadence slips.
- **Phase B budget:** set when story 8 unblocks.

<details><summary>Grill scorecard</summary>

| Area | Verdict | Note |
|---|---|---|
| Problem & evidence | ⚠️ | No interviews logged; this epic generates them |
| Smallest valuable version | ✅ | Organic, 2 core channels + agency outreach; ads in Phase B |
| Alternatives | ✅ | Paid now, all channels, do nothing: all rejected |
| Fit with Key Decisions | ✅ | No-copyrighted-audio rule applied; no MCP promise to CapCut before Phase 2 |
| Data & schema | ✅ | Waitlist source field only |
| Security & privacy | ✅ | Pixel after #56 + consent; no personal data in the repo |
| Cost | ✅ | $0; time capped at 2 posts/week |
| Failure modes | ✅ | Copyright, handles, burnout, over-promising |
| Reversibility | ✅ | Two-way door |
| Observability | ✅ | UTMs + waitlist source |
| Success metric | ✅ | 10 conversations + 5 design partners from marketing by the Phase 1 gate |
| Effort & ownership | ⚠️ | All on one person |

</details>

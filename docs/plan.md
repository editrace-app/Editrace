# Editrace — Project Plan

## Overview

Editrace is a web app that reverse-engineers short-form videos into editable **templates** (cuts, pacing, motion, transitions, text overlays, captions, beat markers, color) and connects them to the user's editor through an **MCP tool**. Supported editors are DaVinci Resolve, Premiere Pro, Final Cut Pro and CapCut.

It has three surfaces:

1. **Web app.** Upload a video, get a template breakdown, export files.
2. **MCP integration.** Any MCP client (Claude, Cursor, …) can read templates and apply them directly in the user's editor.
3. **Template assistant.** A RAG chatbot on the breakdown page that answers "what tools are used here, and how do I do this in my editor?"

**Positioning:** "Rebuild the edit in minutes." It's a faithful reconstruction, not an exact copy. A finished MP4 doesn't contain its edit decisions, so we infer them and say how confident we are.

## Key Decisions

- **Upload-first.** No link downloading in MVP (TOS and legal risk, #21).
- **No copyrighted audio in exports.** Fingerprint and identify the sound, link to it, export beat and cut markers only. Audio is discarded after analysis.
- **Web app, not desktop.** Heavy processing runs on Modal GPU workers. The only local component is the optional Editrace Bridge.
- **Two-tier MCP.** A **remote MCP server** (hosted, OAuth) handles templates and exports. A **local Editrace Bridge** (stdio MCP server on the user's machine) drives desktop editors, because the cloud can't reach them.
- **Live where possible, file handoff otherwise.** Each editor gets the best mechanism it supports; capabilities are decided in spike #29.
- **Templates hold slots, not media.** Clips are placeholders for the user's own footage.
- **Schema-driven.** `schema/template.schema.json` is the single source of truth. TypeScript types, Pydantic models and MCP tool schemas (Zod) are generated from it, never hand-written.
- **Frames + rational fps, not float seconds.** Editors use rational time, and float drift causes off-by-one cuts.
- **Deterministic first, LLM second.** "Which tools are used" comes from the editor tool ontology (#31), not an LLM guess. The assistant explains and adds how-to guidance from docs, with citations.
- **One monorepo.** Docs live in `/docs` as Markdown; decisions go in `docs/decisions/` as ADRs.

## Architecture

```
                   ┌──────────────────────── Cloud ────────────────────────┐
 Browser ─────────▶│ Next.js web app ── Supabase (auth, Postgres+pgvector, │
                   │   │  breakdown UI     storage, realtime)               │
                   │   │  assistant  ───▶ Claude API + search_docs          │
                   │   ▼                                                    │
                   │ Job orchestrator ──▶ Modal workers (cuts, motion,      │
                   │                      captions, OCR, beats, color)      │
                   │ Exporters (OTIO→FCPXML/Premiere XML, CapCut draft)     │
                   │ Remote MCP server (Streamable HTTP, OAuth)             │
                   └───────────────▲────────────────────────────────────────┘
                                   │ HTTPS (token)
 MCP client (Claude, Cursor) ──stdio──▶ Editrace Bridge (local MCP server)
                                          ├─ Resolve adapter  (scripting API, live)
                                          ├─ Premiere adapter (UXP panel, live)
                                          ├─ FCP adapter      (FCPXML handoff)
                                          └─ CapCut adapter   (drafts folder)
```

**Analysis flow:** upload → `jobs` row → orchestrator fans out workers in parallel → each writes a partial template with confidence/provenance → merge → validate → breakdown page updates via Realtime.

**Editor flow:** the user asks their MCP client to "apply template X in Resolve". The client calls the Bridge's `apply_template`. The Bridge fetches the template from the Editrace API and passes it to the Resolve adapter, which builds the timeline and returns a report of applied, skipped and approximated elements.

## Template Schema (v0, #1)

v0 is in `schema/template.schema.json`; every field is documented in [`schema.md`](schema.md). Time and versioning follow [ADR 0002](decisions/0002-time-representation-and-schema-versioning.md).

| Section | Contents |
|---|---|
| `schema_version` | semver of the schema the document was written against |
| `source` | duration (frames), fps (rational), resolution, aspect ratio, content hash; VFR references are conformed (lossy, recorded) |
| `tracks` | ordered bottom to top, each with a `kind` |
| — `video` tracks | `slots` (range, source range, speed, zoom/pan keyframes, per-shot `color` with optional LUT name), `cuts`, `transitions` (type, range, from/to slot) |
| — `text` tracks | `text_overlays`: text, normalized bbox, style hints, range |
| — `captions` tracks | `captions`: lines of words with per-word timing |
| `beats`, `sound_ref` | bpm, beat/downbeat frames; sound id, title, artist, link (no audio) |
| every detected element | `confidence`, `provenance` (detector, version) |

## Editor Integrations

| Editor | Mode | Mechanism | Phase | Main risk |
|---|---|---|---|---|
| DaVinci Resolve | Live | Python scripting API via Bridge (#33); OTIO/FCPXML fallback (#9) | 1 | Free vs Studio scripting limits |
| Final Cut Pro | Handoff | FCPXML opened in FCP via Bridge (#34) | 1 | No live API: no incremental updates |
| Premiere Pro | Live | UXP panel ↔ Bridge over local WebSocket (#35); Premiere XML fallback | 2 | UXP API coverage for sequence edits |
| CapCut | Handoff | Draft written into local drafts folder (#36, #10) | 2 | Undocumented, version-sensitive draft format |

All four also get plain file downloads from the web app (#9, #10), so the Bridge is optional.

## MCP Design

**Remote MCP server (#27):** MCP TypeScript SDK, Streamable HTTP, OAuth backed by Supabase Auth, with personal tokens as a fallback (#37).

| Tool | Purpose |
|---|---|
| `list_templates` | Find the user's templates |
| `get_template` / `get_elements` | Full template or filtered by type/time range |
| `get_tools_used` | Editor features used, from the ontology |
| `export_template` | Signed URL for FCPXML / Premiere XML / CapCut draft |
| `ask_about_template` | Proxy to the assistant (Phase 2) |

**Editrace Bridge (#32):** local stdio MCP server with the tools `list_editors`, `apply_template`, `add_beat_markers` and `open_export`. The adapter interface is `detect / capabilities / plan (dry run) / apply → report`.

## Template Assistant (RAG)

Goal: answer questions about a breakdown, especially **which editor tools are used and how to reproduce them**, e.g. "What transition is at 0:07 and how do I do it in CapCut?"

Grounding sources, exposed to a Claude tool-use agent:

| Source | What it answers | Tool |
|---|---|---|
| Template JSON | What's in this video, when, and how confident we are | `get_template_elements` |
| Editor tool ontology (#31) | Which feature in editor X produces this element | `lookup_editor_tool`, `tools_used` |
| Editor docs index (#38) | How to use that feature, step by step | `search_docs` (pgvector + full-text hybrid, filtered by editor/version) |

Rules:
- Cite timestamps (clickable) and doc links.
- Flag low-confidence or inferred elements.
- Never present a guess as the exact effect used.
- Stay in scope.
- Enforce a per-plan message quota.

Quality is gated by the eval harness (#39): retrieval recall@5, citation support, tool-mapping accuracy and refusal correctness. The corpus approach is decided in spike #30, and doc licensing is cleared in #21.

## Stack

| Layer | Choice |
|---|---|
| App | Next.js + TypeScript |
| DB, auth, storage, realtime | Supabase (Postgres + pgvector) |
| Workers | Python on Modal (serverless GPU) |
| Exporters | OpenTimelineIO + CapCut draft writer |
| MCP | MCP TypeScript SDK (remote); Bridge language chosen in #29 |
| Assistant | Claude (Anthropic API, tool use) + hybrid retrieval; embedding model chosen in #30 |
| Billing | Stripe Billing |

## Team

| Role | Owner | Labels | Owns |
|---|---|---|---|
| CV 1: Visual timeline & editors | calvinkafaiyuen | `ml`, `platform` | Cuts, motion, transitions, exporters, editor adapters, ontology (visual) |
| CV 2: Signals & scoring | sktchan | `ml` | Audio, OCR/captions, color, Format Match Score, ontology (signals) |
| CV 3: Footage match & format benchmark | mikeleboss | `ml`, `platform` | Footage matching, format benchmarking |
| Product, MCP & assistant | anthonythanpoovong | `product` | Web app, auth, billing, job queue, MCP server + Bridge core, RAG assistant, GTM |

All four collaborators are code owners on every path, so any of them can approve any PR. There is no admin bypass on `main` (#132); as `/.github/` owners they can also grant coverage exemptions. See CONTRIBUTING.md.

## Epics

Work is organized as **Epic → Story → Subtask**, using GitHub sub-issues and the `epic` / `story` / `subtask` labels. Phases are GitHub milestones. Subtasks exist only where a story has separable, parallel parts (#9, #16, #18, #19, #22, #27, #31, #32); other stories use checklists.

**New work starts as a proposal, not an issue.** New epics and stories are drafted in `docs/epics/` (`/propose-epic`), and technical decisions in `docs/decisions/` as ADRs (`/propose-adr`). Both are questioned first by `/grill`, which looks for customer evidence in `docs/customers/` (logged with `/log-interview`) and sends open unknowns to a time-boxed `/spike`. Code-owner approval and merging the proposal PR is the approval; issues are created only after that. A few stories added to an existing epic, without changing its Goal or Exit criteria, can skip the proposal: `/draft-stories` drafts them and creates the issues only once the author approves the drafts. See the [Claude Code Skills](https://github.com/editrace-app/Editrace/wiki/Claude-Code-Skills) wiki page.

| Epic | Stories | Owner |
|---|---|---|
| #40 Engineering foundations | #1 schema, #2 codegen, #3 scaffold, #48 CI/CD + environments, #49 migrations + RLS tests, #50 observability + cost guardrails, #51 product analytics, #125–#132 repo hardening | anthonythanpoovong |
| #41 Video analysis pipeline | #52 golden dataset, #53 worker framework, #4, #7, #311, #8, #11, #12, #13, #14, #23, #24, #26, #99–#105 footage match & format benchmark | calvinkafaiyuen, sktchan |
| #42 Editor exports & tool ontology | #5, #6, #9, #10, #31 | calvinkafaiyuen |
| #43 MCP platform & Editrace Bridge | #29, #27, #32, #33, #34, #35, #36, #37 | anthonythanpoovong, calvinkafaiyuen |
| #44 Web app core experience | #15, #16, #17, #18 | anthonythanpoovong |
| #45 Template assistant (RAG) | #30, #38, #22, #39 | anthonythanpoovong |
| #46 Billing & accounts | #19, #25, #28 | anthonythanpoovong |
| #47 Discovery, legal & GTM | #20, #21, #54 landing + waitlist, #55 design partners, #56 terms/privacy/retention, #114–#121 marketing launch | anthonythanpoovong |

## Roadmap

### Phase 0: De-risk (spikes & foundation)
- Template JSON Schema v0 (#1, done), type generation (#2, done), monorepo scaffold (#3, done)
- Foundations: CI/CD + environments (#48, done: PR previews, staging on merge, manual promotion of a staged commit to prod, on a Vercel Pro team; moving off Vercel was considered and rejected in ADR 0001; `deploy.yml` applies Supabase migrations before the staging and prod deploys (#163; the Modal workspaces (staging and prod) and worker deploys are being added, see docs/environments.md), migrations + RLS test harness (#49, done)
- PR notifications in Discord (#140, #154, #156, #177, done): review request and ack, comment, approval, merge, re-review and `@mention` pings; weekly sync and sprint-due reminders (#184, done); unfinished board items carried into the running sprint automatically (#243, done); Armour reads its rules from the base branch (#159, done)
- Repo hardening: Armour skip notice (#126, done), changed files + coverage in reviews (#125, done), sharper rules (#127, done: untested risky code, missing tests or two-user RLS tests, missing `/sync-docs` notes), eval set (#128, done: 9 seeded bugs since #127, 9/9 with its rules), earlier findings carried forward until resolved (#239, done), reviews that ended without posting fixed (#233, done); 80% changed-line coverage gate (#129, done); CONTRIBUTING + templates (#130, done), pre-commit hooks (#131, done), teammates + review rules (#132, done)
- Pipeline: golden video dataset (#52; 5 of the 10 Phase 0 videos labeled, in `eval/golden/` from #290), worker framework (#53)
- CV1: cut detection spike (#4, decided: [ADR 0003](decisions/0003-use-pyscenedetect-adaptive-for-cut-detection.md) builds #7 on PySceneDetect's AdaptiveDetector and flags suspect cuts for review; no detector met the original bar), Resolve rebuild spike (#5), CapCut draft spike (#6)
- CV1: **live editor control spike (#29)**
- CV2: beat tracking + sound ID spike (#11)
- Product: **RAG corpus + retrieval spike (#30)**
- Product: customer interviews incl. MCP appetite and assistant questions (#20); legal consult incl. docs corpus and editor TOS (#21); landing page + waitlist (#54)
- Marketing (organic, $0): brand kit (#114, done: `brand/`), social accounts + attribution (#115), UGC agency outreach (#116), creator/UGC communities (#117)

**Gate:** An editor reviews a detected cut timeline rebuilt in Resolve and confirms it feels like the original. #29 shows at least one editor can be driven live.

### Phase 1: MVP (analyze → breakdown → editor via MCP)
**Visual timeline (CV1):** cut-detection worker (#7, signed off on #52's 10-video set per ADR 0003), cut precision to 0.90 (#311), motion estimation (#8), Resolve/Premiere/FCP exporter (#9), CapCut exporter (#10)
**Signals (CV2):** captions (#12), OCR (#13), beat + sound (#14)
**Ontology:** editor tool ontology (#31)
**Product:** auth (#15, done: email sign-up, login, logout and password reset, Google sign-in built, `profiles` table), upload + job queue (#16), history (#17), breakdown page with the Tools used panel (#18), Stripe Solo + trial (#19)
**MCP:** remote MCP server v1 (#27), settings page (#37), Editrace Bridge (#32), Resolve adapter (#33), FCP adapter (#34)
**Foundations:** observability + cost guardrails (#50), product analytics (#51, plan in [`analytics-plan.md`](analytics-plan.md))
**GTM:** design partner program (#55), terms/privacy/retention (#56), launch demo video (#118), content engine (#119), Meta Business + Pixel with no spend (#120)

**Gate:** A new user uploads a video, sees a breakdown, and from Claude Desktop applies it to Resolve or FCP in under 10 minutes. At Phase 1 exit (or after 200 reviewed templates, if sooner), run [ADR 0003](decisions/0003-use-pyscenedetect-adaptive-for-cut-detection.md)'s revisit check on the cut-edit logs from #7 and #18.

### Phase 2: Assistant & more editors
- Docs ingestion pipeline (#38), RAG assistant (#22), eval harness (#39); `ask_about_template` MCP tool
- Premiere adapter (#35), CapCut adapter (#36)
- Transition classification (#23)
- Marketing: Meta ads launch test (#121; needs billing #19 and a budget)

**Gate:** The assistant passes #39 thresholds; all 4 editors are reachable via the Bridge.

### Phase 3: Depth & scale
- Per-shot color / LUT (#24), Format Match Score v1 (#26), footage match & niche format benchmark (#99–#105)
- Team tier (#25), Enterprise: API, SSO, volume pricing (#28)

## Risks

| Risk | Mitigation |
|---|---|
| Editor APIs limited or version-sensitive (Resolve Free, UXP coverage, CapCut drafts) | Spike #29 before committing; file-handoff fallback for every editor; version pinning + tests |
| Users unwilling to install a local Bridge | Web downloads always work; measure appetite in #20 |
| Assistant gives wrong tool advice | Ontology-first answers, citations required, eval gate #39 |
| Docs licensing | #21 before ingestion; link-only mode as fallback |
| Detection quality below "feels like the original" | Phase 0 gate; confidence shown in the UI and passed to adapters |
| GPU cost from users' raw footage (footage match) | Score slotted clips only, trimmed to slot + 2 s, 2 fps / 720p, total ≤ source duration, 3-minute ceiling; per-plan quota (#19); cache by clip hash + slot; bounded retries with backoff (#102). Video-only: audio stripped at ingest (#101). |
| Wrong "re-film" advice costs a user a real reshoot | Flags show confidence and a reason, phrased as suggestions; validated on a labeled set (#101) |
| Copyright strike or account ban from marketing content | Only owned, licensed or permitted footage and audio in posts and demos; sources recorded (#118, #119) |
| Marketing over-promises features (e.g. MCP for CapCut before Phase 2) | "Preview" labels before the MVP; lead with the breakdown + CapCut export (#10), not the Bridge (#36) |
| One person can't sustain marketing alongside product work | Organic, 2 posts/week max, batch-produced (#119); revisit if the cadence slips |
| Armour skips silently (workflow-change PRs, errors, usage limits), and its accuracy is unmeasured | Visible skip notice from an always-run job (#126, done); eval set of seeded bugs, target ≥ 80% caught, re-run when rules or model change and scored before merge with `run.sh --base` (#128, #127, done); reviews that ended without posting (a permission rule broken by a `cd`) fixed (#233, done) |
| Untested code merges, or the coverage gate is bypassed | 80% changed-line coverage gate in `ci-ok` (#129). An exemption is only a `/coverage-exempt` PR review from a `/.github/` code owner (all four collaborators) on the current head commit; labels and statuses have no effect; fails closed. Write access is treated as trusted. |
| Armour eval token exposed to collaborators' branch workflows | The eval runs from a local script on a team member's machine, with no stored token (#128) |

## Metrics

- **Time to editor:** upload → template open in the user's editor (target < 10 min, MVP gate)
- **Editing time saved:** minutes to rebuild with Editrace vs manually
- **Format Match Score:** 1–10 with sub-scores (lighting, loudness/pitch, framing/angle, pacing); similarity to the original, not "virality"
- **Footage match lift:** average Format Match Score of recreations made with pre-edit footage feedback vs without; target +1.5 at Phase 3 exit (#105)
- **MCP adoption:** % of active users with a connected client; applies per week per editor
- **Assistant quality:** #39 scores; thumbs-up rate; % of answers with citations
- **Editor satisfaction:** qualitative feedback on template accuracy
- **Marketing (Phase A):** 10 qualified conversations (creators or UGC agencies) and 5 design partners sourced from marketing channels, by the Phase 1 gate; attributed by UTM + waitlist source (#115)
- **Repo hardening (Phase 0):** Armour's eval catches ≥ 80% of its seeded bugs (≥ 4/5 at first, 9 cases since #127: 9/9 on 2026-10-19), re-checked when its rules or model change (#128), and the first 5 code PRs pass the 80% changed-line coverage gate with no bypass, each Armour review showing the changed files and coverage (#125, #129), by the Phase 0 gate

## Open Questions

- Bridge language: Python (Resolve API is Python) or Node (MCP SDK, UXP)? Decided in #29.
- Windows support timing for the Bridge.
- Should the assistant be available to trial users, and with what quota?
- **Link input / "Send to Editrace"** (browser extension, Android share target, iOS Shortcut): parked. It needs written guidance from #21 on analyzing one public video on request without keeping it, then an ADR replacing upload-first. User demand is unvalidated; ask design partners (#55).
- **Trending / discovery feed:** parked. No scraping, because it conflicts with upload-first and platform terms. An embed-only (oEmbed) feed is the option to revisit, with no user evidence yet. Virality scoring was rejected (no legitimate ground truth); the niche format benchmark (#103) replaces it.
- **Marketing beyond Phase A:** paid ads wait for the MVP, billing (#19) and a budget (#121). Premiere/Resolve/FCP editors as a primary audience, Product Hunt, a newsletter and affiliate/referral programs are deferred (see `docs/epics/marketing-launch.md`).
- **Repo hardening, deferred:** Dependabot, dependency/security scanning (pip-audit, npm audit, Semgrep) and pinning actions to SHAs wait until dependencies exist; CodeQL needs a paid plan for private repos; 👍/👎 tracking of Armour findings is deferred (see `docs/epics/repo-hardening-phase-0.md`).

## Links

- GitHub: https://github.com/editrace-app/Editrace
- Project Board: https://github.com/orgs/editrace-app/projects/1 (views: Spikes, Blocked, Quick wins, per-area incl. MCP and RAG)
- Wiki (per-feature docs): https://github.com/editrace-app/Editrace/wiki
- Proposals: [`docs/epics/`](epics/README.md) (epics) · [`docs/decisions/`](decisions/README.md) (ADRs)

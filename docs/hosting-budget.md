# Hosting and tooling budget (#149)

- **Status:** Current, as of 2026-10-24
- **Author:** @mikeleboss
- **Related:** #149, #153, [`decisions/0001-host-web-app-on-cloudflare-workers.md`](decisions/0001-host-web-app-on-cloudflare-workers.md), [`analytics-plan.md`](analytics-plan.md), [`environments.md`](environments.md)

What we actually pay today, and the known next step up for each tool, so the team can see the real monthly number instead of reconstructing it from several docs. ADR 0001 covers *why* we're on Vercel; this doc covers *what it, and everything around it, costs*.

## Today's monthly cost

| Tool | Plan | Cost/mo | Why |
|---|---|---|---|
| Vercel | Pro | $20 (1 seat) + usage | Required once a second teammate needed to deploy or get preview URLs — Hobby has no collaborators and blocks commercial use (ADR 0001). Pro includes 1 TB fast data transfer and ~10M edge requests before overage. |
| Supabase | Free | $0 | Two projects (staging + prod), within the free plan's 2-project limit (`environments.md`). No paid compute add-on yet. |
| Modal | Starter (pay-as-you-go) | $0 base | No fixed monthly fee; GPU workers aren't deployed yet (`docs/plan.md`, #163). Once running, billed per second: roughly $2.50–$4/GPU-hour depending on GPU class (H100 ≈ $3.95/hr, A100 80GB ≈ $2.50/hr), plus CPU/memory. |
| Domain | — | $0 | No domain purchased yet; the site runs on `*.vercel.app` subdomains. |
| Analytics | In-house | $0 | Built on our own Supabase Postgres, no vendor (`analytics-plan.md`) — a deliberate choice to avoid a per-MAU bill and a second data processor. |
| Email (transactional) | Supabase Auth built-in | $0 | Password reset / sign-up emails ride Supabase Auth's own sender. No ESP (Resend/Postmark/SendGrid) chosen yet for that. |
| Email (waitlist team notification) | Gmail SMTP | $0 | #287: a new `waitlist_signups` row sends one email to `editrace.co@gmail.com` via that same Gmail account's SMTP and an app password -- free at this volume (a handful of signups/day), and separate from the ESP decision above, which is specifically about Supabase Auth's production email volume. |
| Stripe | Pay-as-you-go | $0 fixed | Transaction fees only (~2.9% + $0.30/charge) once billing (#19) ships. No monthly fee. |

**Today's fixed total: ~$20/month** (Vercel Pro only). Everything else is $0 until usage or a feature (billing, a bought domain, higher email volume, GPU workers) forces a paid tier.

## Known next steps up, and their trigger

| Tool | Next tier | Cost/mo | Trigger |
|---|---|---|---|
| Vercel | Additional seats | +$20/seat | Each extra teammate who needs to deploy or get SSO-protected preview URLs. |
| Supabase | Pro | $25 + usage | A third environment, >500 MB DB, or any paid compute tier (Micro compute included in the $25; Small/Medium/Large cost more). Needed once the free plan's 2-project cap or storage/bandwidth limits are hit. |
| Modal | Team | $250 + compute | Needed only past Starter's usage limits or for team-level features (shared billing, roles) — not a near-term trigger at current scale. |
| Domain | `.com`/`.app` registration | ~$12–20/yr (~$1–2/mo) | Whenever the team wants a real domain instead of `*.vercel.app`. Not yet decided or budgeted elsewhere. |
| Email (transactional) | A paid ESP (Resend/Postmark) | ~$0–20/mo at low volume | Supabase Auth's built-in email sender has a low rate limit meant for development; production auth volume needs a real SMTP provider sooner rather than later. Not yet chosen. |
| Analytics | Vendor (if reversed) | Rejected | `analytics-plan.md` rejected PostHog/Amplitude/Mixpanel/GA4 specifically to stay at $0 and keep events inside our RLS boundary. |

## Bottom line

Current fixed spend is **~$20/month**, all Vercel. The next predictable step is a domain purchase (~$1–2/month) and picking a transactional ESP before auth email volume outgrows Supabase's built-in limit — neither is budgeted elsewhere in the repo yet. Supabase Pro and Modal's Team plan are known future costs but have no near-term trigger at current usage. GPU worker cost is usage-based and unknown until #163 lands and real traffic sets the shape.

Pricing above is sourced from each vendor's public pricing page as of October 2026 and should be re-checked before being relied on for a real budget decision, since usage-based and third-party-reported numbers can be stale or change.

# 0004: Isolate Modal staging and prod with separate workspaces, not Team-plan service users

- **Status:** Proposed
- **Date:** 2026-10-28
- **Author:** @anthonythanpoovong
- **Deciders:** @anthonythanpoovong, @calvinkafaiyuen
- **Related:** #320, #302, #163, #300
- **Supersedes:**

## Context

Workers run on Modal (Key Decision: "Heavy processing runs on Modal GPU workers"). `deploy.yml` needs to deploy to staging automatically and to prod on a manual run, and a staging job must never be able to touch prod (#163, #320).

The constraint is the Modal **Starter** plan. It has no service users, so the only token is a user token that covers the whole workspace. A staging job holding it could deploy to `prod`, and the `staging` and `prod` environments we created don't change that. Scoped service users (Contributor on one environment) need the **Team** plan, $250/month plus compute (modal.com/pricing). PR #302 is written for service users and is on hold until this is decided.

What we've verified (2026-10-27/28):
- Modal's docs say API tokens are added per workspace, and that users can belong to several workspaces.
- Starter can create a second workspace once a payment method is added. The dashboard says it unlocks workspace creation and the full $30 credit, and charges $0.50 to verify the card, which is refunded.
- Modal's [budgets docs](https://modal.com/docs/guide/budgets) say a workspace **spend limit** is a hard stop on net out-of-pocket charges (after credits), settable by Owners and Managers. Environment-level budgets need Team. Not yet confirmed in our dashboards (see Open questions).
- We now own two workspaces, `anthonythanpoovong` and `editrace-app`, both as Owner.
- Cost is the main constraint. We have no users, no revenue and no worker deployed yet.

Why now: the first real worker deploy (#7) is blocked until the isolation layout is chosen.

## Options considered

### Option A: A separate Modal workspace per environment, one token each
- `editrace-app` is prod and a team workspace, `editrace-staging`, is staging. The personal `anthonythanpoovong` workspace is not used for either. Each GitHub environment (`staging`, `Production`) holds only its own workspace's token. In Modal terms the environments are the workspaces.
- ✅ Hard isolation. A staging token has no access to the prod workspace at all, not just a role boundary.
- ✅ Separate spend caps per workspace, so a staging runaway can't use the prod budget.
- ✅ $0 for the workspaces themselves.
- ❌ A card must be on file, so usage past the free credit can be billed.
- ❌ A third workspace must be created and a card attached to it. If Starter doesn't allow a third workspace, staging stays in `anthonythanpoovong` until it can move (see Open questions).
- ❌ #302 and `docs/environments.md` must be reworded, since they assume service users.
- Cost / effort: $0 plus compute; a small rewrite of #302.

### Option B: Upgrade to Team and use service users
- One workspace, `ci-staging` and `ci-prod` service users with Contributor on one environment each.
- ✅ Strong isolation, and #302 works as written.
- ❌ $250/month plus compute, about $3,000 a year, before any user exists.
- Cost / effort: $250/month.

### Option C: One user token for both environments
- ✅ Nothing to set up.
- ❌ No isolation. A leaked token or a bad staging job can deploy to prod. This fails the #163 criterion that a staging deploy never touches prod.
- Cost / effort: $0.

### Option D: Do nothing / defer
- ✅ No spend and no change.
- ❌ Blocks the first real worker deploy (#7) and leaves #163 open.

## Decision

We will **use a separate Modal workspace for each environment, with one API token per workspace**, because it gives hard isolation and separate spend caps for $0, while Team costs $250/month before we have a single user.

- **Prod** is `editrace-app`. **Staging** is `editrace-staging`, a team workspace created up front so staging spend stays off a personal card, teammates can get access for #7's timing job, and staging URLs never change. Fallback: if Starter doesn't allow a third workspace, staging is `anthonythanpoovong` for now and moves later.
- **Nothing is live yet.** No `MODAL_TOKEN_*` secrets are created or added to GitHub until we choose to start spending, so no job can deploy.
- Each workspace gets a **spend limit of $0**, so Modal stops any workload that would create an out-of-pocket charge. The free credit still runs. Raise a limit deliberately, per workspace, when we choose to spend.
- Each workspace uses its default Modal environment (`main`), and the deploy step passes `--env main` explicitly, as #302 does, so nothing else (such as a `MODAL_ENVIRONMENT` variable) can redirect a deploy. The workspace is the isolation boundary. The unused `prod` environment in `anthonythanpoovong` is deleted so a misdirected deploy can't land somewhere dead.
- Each `MODAL_TOKEN_*` pair lives only in its matching GitHub environment, never at repo level. Those environments only accept deployments from `main` (`staging`) and protected branches (`Production`), so a branch workflow can't read them (`docs/environments.md`). Required reviewers aren't available on our GitHub plan.
- @anthonythanpoovong creates both tokens. Starter tokens belong to a person, so they are rotated when anyone who created one leaves or loses access, and the Modal audit log shows that person for every deploy.

## Consequences

- **Good:** a staging token can't reach prod. Each environment has its own budget. No recurring cost now.
- **Bad / accepted trade-offs:** a card is on file, so overage past the credit is possible until the limits are confirmed. Secrets and images are duplicated per workspace. The isolation protects CI tokens, not people: the same login owns both workspaces, so anyone with it can deploy to either from a terminal. Whether the free credit is per workspace is unconfirmed, so we don't count a second credit as budget.
- **One-way or two-way door:** two-way. If we outgrow this, we upgrade to Team and move to service users, revoking the workspace tokens. The cost is a rewrite of the deploy step and docs, with no data migration. If staging has to start in the personal workspace (no third workspace on Starter), moving it later changes the workspace name in every staging endpoint URL, so keep any staging URL in configuration (env vars or Modal client lookups), never hardcoded in code, webhooks or docs.
- **Follow-up work:**
  - Convert #302 back to a draft now, then reword it. It already moves `MODAL_TOKEN_*` to environment secrets and passes `--env main`, so only the service-user wording and the staging workspace name change.
  - Create the `editrace-staging` workspace and attach a card (or record here that Starter doesn't allow it).
  - Update `docs/environments.md`: workspaces and one token per environment, and remove the duplicated `MODAL_TOKEN_*` row that says "repo secrets".
  - Set the spend limits and update the Modal row in `docs/observability.md`.
  - Add tokens only when we're ready to spend (#320).
- **Affected:** no schema, DB, RLS or MCP changes. Cost: $0 now. Docs: `environments.md`, `observability.md`, #302.

## How we'll know

- Before this ADR is accepted: a throwaway token created in `editrace-staging` cannot list or deploy in `editrace-app`, and the reverse. Post the result in this PR and delete the tokens afterwards. **Result: not run yet.**
- Before the first real deploy: the real staging token fails to deploy to the prod workspace.
- Net out-of-pocket Modal spend stays at $0 until launch. Usage inside the free credit is expected.
- Revisit if the team needs per-person roles inside one workspace, if Starter limits (seats, GPU or container concurrency, storage) block us, or if spend makes Team worthwhile.

## Open questions

- Can each workspace set a $0 spend limit on Starter, as the docs imply? Owner: @anthonythanpoovong, check **Usage & billing** in each, and record the answer here. **Unanswered.**
- Does `editrace-app` show the Starter plan and its own free credit, and is the credit per workspace or per account? Owner: @anthonythanpoovong. **Unanswered.**
- What are the Starter seat, GPU and container concurrency and storage limits for a four-person team? Owner: @anthonythanpoovong.
- Does Starter allow a third workspace (`editrace-staging`)? Owner: @anthonythanpoovong. **Unanswered.**
- Does the app call workers by URL or through the Modal client? Decides how much a staging move costs if staging starts in the personal workspace. Owner: @anthonythanpoovong.

<details><summary>Grill scorecard</summary>

| Area | Verdict | Note |
|---|---|---|
| Problem & evidence | ✅ solid | Starter has no service users (#320, #302); no customer evidence, as expected for infrastructure |
| Smallest valuable version | ✅ solid | Two workspaces, tokens added later |
| Alternatives | ✅ solid | Team, shared token and deferring all considered |
| Fit with Key Decisions | ✅ solid | Keeps Modal, changes only the account layout |
| Data and schema | ✅ solid | None affected |
| Security and privacy | ⚠️ weak | Isolation depends on tokens never being shared across environments; not yet tested |
| Cost | ⚠️ weak | $0 now, but a card is on file and the $0 spend limit is unverified in the dashboards |
| Failure modes | ✅ solid | Missing token fails the deploy job; no app means a notice only |
| Reversibility | ✅ solid | Two-way door |
| Observability | ⚠️ weak | Spend alerts not yet set up (`observability.md`) |
| Success metric | ✅ solid | Cross-deploy fails; spend stays $0 |
| Effort and ownership | ✅ solid | Owner @anthonythanpoovong, small rewrite of #302 |

</details>

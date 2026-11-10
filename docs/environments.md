# Environments and secrets

How Editrace's environments are deployed and where each secret lives (#48). The variable names are in [`.env.example`](../.env.example).

## Environments

| Environment | Purpose | How it deploys | URL |
|---|---|---|---|
| **dev** | Local development | `npm run dev` in `web/` with your own `.env.local` | localhost |
| **preview** | One per PR | `deploy.yml` deploys to the `editrace-np` Vercel project every same-repo PR with the owner's Vercel token and posts the URL in a PR comment | Per PR (needs a Vercel login, SSO protection is on) |
| **staging** | Latest `main` | `deploy.yml` runs on every push to `main`, deploys to the `staging` custom environment in `editrace-np` | `https://editrace-staging.vercel.app` (the environment's domain; repo variable `STAGING_ALIAS` holds the host) |
| **prod** | Live | Promote a commit that is live on staging: run `deploy.yml` by hand from `main` (Actions → Deploy → Run workflow); deploys to `editrace-prod` | `editrace-prod.vercel.app` (private: Vercel SSO protection is on, so only team members can open it) |

### Promotion: PR → staging → prod

1. A PR gets a preview in `editrace-np`.
2. Merging to `main` applies the commit's Supabase migrations to the staging project, then deploys staging (`editrace-np`). A failed migration stops the job before any deploy, so the commit gets no `deployed/staging` status and can't be promoted. When staging is live, the workflow marks the commit with a `deployed/staging` commit status.
3. Test on staging, then run **Deploy** from `main`. Leave `sha` blank to promote the newest commit live on staging, or paste a SHA. The run refuses a commit unless it has the `deployed/staging` status and is on `main`. The status alone isn't proof, since anyone with write access can post one, so the `main` check (protected: PR plus approval) is what keeps unreviewed code out of prod.
4. The `Production` job applies the commit's migrations to the prod project (unless `migrate` is unticked), then rebuilds that exact commit in `editrace-prod` with production's env vars. It can't reuse the staging build, because `NEXT_PUBLIC_*` values are baked in at build time and differ per project. The `Production` GitHub environment only accepts protected branches, which is why the run starts from `main`.

Roll back by running the workflow again with an older `sha` and **`migrate` unticked**: the older commit doesn't have the newer migrations, so the old app runs against the newer database. It must be on `main` and have been live on staging after this workflow landed, because earlier commits have no `deployed/staging` status. The app is built from that commit, but the deploy script comes from the `main` commit the run started from.

> **Don't leave `migrate` ticked on a rollback, and don't run `supabase migration repair`.** With `migrate` ticked, the job fails at the migration step, before any build or deploy, with `DbPushMissingLocalError` ("Remote migration versions not found in local migrations directory"). The error suggests `supabase migration repair`, which edits the migration history: don't. Untick `migrate` and run the rollback again. Checked on Supabase CLI 2.120.0 (the version `deploy.yml` pins) with `supabase db push --dry-run` from a commit older than the newest migration, against staging (#163). The error name is a CLI detail, so recheck it when the pin changes.

Repo variables (Settings → Secrets and variables → Actions → Variables): `VERCEL_PROJECT_ID_NP` and `VERCEL_PROJECT_ID_PROD` are the two Vercel project ids. They aren't secrets.

Vercel's own Git deploys are turned off for every branch in `web/vercel.json`, so nothing reaches prod without `deploy.yml`. Branches created before that file changed still carry the old setting until they merge `main`.

### Limitations (revisit on upgrade)

- Staging is a custom environment inside `editrace-np`, so it has its own env vars, separate from PR previews. Prod is a separate project, so its env vars, domains and protection settings can't leak into staging.
- **A blocked deploy never fails a collaborator's push.** Vercel marks a deployment `BLOCKED` when the commit's author isn't on the team, and the CLI would then wait forever. `.github/scripts/vercel-deploy.sh` polls instead: the preview job comments "No preview" and ends green, and the staging job leaves staging on its last build with a warning annotation. Only the manual prod run fails on `BLOCKED`, so run it from a commit authored by a Vercel team member. Staging therefore only advances when the latest commit on `main` is by a team member, if it happens again. It stopped on Pro (Oct 12), so this is defensive.
- Previews are deployed from Actions under the owner's token, not Vercel's Git integration, which refuses commits from anyone who isn't on the team. Collaborators who aren't Vercel team members can't open previews while SSO protection is on. Add them as Vercel members (a paid seat each) if they need to.
- GitHub required reviewers on environments need a paid plan for private repos, so `Production` has no approval gate. The `main` branch protection (PR plus 1 approval plus `ci-ok`) is the only gate.
- Supabase staging and prod projects exist and their keys are set in Vercel (`staging` target on `editrace-np`, Production on `editrace-prod`). The Preview target has no Supabase variables: the free Supabase plan allows two projects, and both are taken. Previews run without a database until we upgrade or point them at staging with the anon key only, never the service-role key. `deploy.yml` applies migrations to both projects with `supabase db push` (never `seed.sql`). Modal environments follow in #163.

## Supabase locally

Local development uses a Supabase stack on your own machine, not a cloud project. That's why the Preview target has no Supabase variables, and it needs no Supabase account or keys from a teammate.

The `supabase/` directory holds the config (`config.toml`), migrations (`supabase/migrations/`), seed data (`seed.sql`) and the RLS tests (`supabase/tests/database/`) (#49). Nothing in `web/` reads a Supabase variable yet.

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it, then install the Supabase CLI: `brew install supabase/tap/supabase`.
2. From the repo root, run `supabase start`. The first run pulls the images, which takes a few minutes.
3. Run `supabase status`. Copy the API URL into `NEXT_PUBLIC_SUPABASE_URL`, the anon (or publishable) key into `NEXT_PUBLIC_SUPABASE_ANON_KEY` and the service-role (or secret) key into `SUPABASE_SERVICE_ROLE_KEY`, in `web/.env.local`. The names are in [`.env.example`](../.env.example).
4. `supabase db reset` rebuilds the local database from the migrations and loads `seed.sql`, which creates two sign-in users, `alice@example.test` and `bob@example.test` (password `password123`), so you can check one can't see the other's rows. `supabase stop` shuts the stack down.

### RLS tests

`supabase test db` runs the pgTAP tests in `supabase/tests/database/`:

- `000_rls_guardrails.test.sql` fails if any table in `public` has RLS off, or has no policy and isn't commented `service-role only: <why>` (`comment on table <t> is '...'`).
- `001_two_user_probe.test.sql` is the pattern for a real table: user A can read, update and write their own row, user B and anonymous users can't, and B can't insert a row owned by A. Its throwaway table is rolled back, so nothing is left in the database. To test a new table, copy it to `<table>.test.sql` and point it at the real table.
- `.github/scripts/check-rls-tests.sh` fails if a table in `public` isn't named in any test file other than the guardrails.

CI's `supabase` job runs `supabase db reset`, `supabase test db` and the check on every PR, so a table can't merge without RLS and a two-user test. It doesn't start the API services, only the database.

Rules:

- The keys `supabase status` prints are for your local stack only. Never put a staging or prod service-role key in `.env.local`.
- Schema changes go through the `/db-migration` skill, which writes the migration together with its RLS policies and its two-user test.
- A deployed PR preview can't reach the database on your laptop. If a preview needs a database, see the Preview limitation above.

## Auth settings per Supabase project

The web app's sign-in (#15) needs these in the Supabase dashboard of **each** hosted project, because `supabase/config.toml` only configures the local stack. Missing redirect URLs don't error: Supabase quietly sends the user to the Site URL instead, so a reset link lands on the home page.

- **Authentication → URL Configuration → Site URL:** the environment's own URL.
- **Redirect URLs:** add `<that URL>/auth/callback**`. The `**` matters: the app adds `?next=...` to the callback, and the list matches exactly otherwise.
- **Authentication → Sign In / Providers → Email:** minimum password length 8, to match the app's own check.
- **Google:** turn the provider on with a Google OAuth client ID and secret, and add Supabase's callback URL to that Google client's authorised redirect URIs. Keep the Google client secret in Supabase only, never in this repo or Vercel.

## Where each secret lives

Each secret lives in **one** place. Don't copy values between systems except through the deploy workflow.

| Secret or variable | Lives in | Used by |
|---|---|---|
| `VERCEL_TOKEN`, `VERCEL_ORG_ID` | GitHub repo secrets | `deploy.yml` |
| `VERCEL_PROJECT_ID_NP`, `VERCEL_PROJECT_ID_PROD` | GitHub repo variables | `deploy.yml` |
| `SUPABASE_ACCESS_TOKEN` | GitHub environment secret, a different token in `staging` and `Production`. Each is scoped to that environment's one Supabase project, so the staging token can't reach prod | `deploy.yml` migration steps |
| `SUPABASE_DB_PASSWORD` | GitHub environment secret, a different value in `staging` and `Production` | `deploy.yml` migration steps |
| `SUPABASE_PROJECT_REF` | GitHub environment variable, a different value in `staging` and `Production` | `deploy.yml` migration steps |
| `STAGING_ALIAS` | GitHub repo variable | `deploy.yml` (staging host shown on the environment and in the commit status) |
| `CLAUDE_CODE_OAUTH_TOKEN` | GitHub repo secret | Armour review and `@claude` |
| `DISCORD_REVIEW_WEBHOOK` | GitHub repo secret | Discord review notifications |
| `DISCORD_ALERTS_WEBHOOK` | GitHub repo secret | Spend and error alerts in `#alerts` ([observability.md](observability.md#spend-alerts)) |
| `APP_ENV` | Vercel project env vars (`preview`, `staging`, `prod`) and Modal secrets, per environment | Logs and Sentry events ([observability.md](observability.md)) |
| `SENTRY_DSN`, `NEXT_PUBLIC_SENTRY_DSN` | Vercel project env vars (web DSN) and Modal secrets (workers DSN) | Error tracking |
| `SENTRY_AUTH_TOKEN` | GitHub repo secret | Source map upload in `deploy.yml`, once the web SDK is set up |
| `NEXT_PUBLIC_SUPABASE_*`, `SUPABASE_SERVICE_ROLE_KEY` | Vercel project env vars: `editrace-np` (Preview and `staging` environments), `editrace-prod` (Production) | Web app |
| `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` | GitHub repo secrets (deploys) and Modal (runtime) | Workers |
| `ANTHROPIC_API_KEY`, `STRIPE_*` | Vercel project env vars: `editrace-np` (Preview and `staging` environments), `editrace-prod` (Production) | Web app and assistant |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `WAITLIST_NOTIFY_EMAIL` | Vercel project env vars: `editrace-np` (`staging` environment), `editrace-prod` (Production). Not set on Preview, same as the Supabase variables, so the notification silently no-ops there | `/api/waitlist`'s team-signup notification (#287) |

Rules:

- Real values never go in git. `.env`, `.env.local` and `.env.*.local` are ignored, and gitleaks runs on every PR.
- The `staging` environment only accepts deployments from `main` (Settings → Environments → staging → Deployment branches), like `Production` accepts protected branches. A branch workflow can't read the staging secrets.
- Staging and prod use different Supabase projects, Stripe keys (test vs live) and Modal environments, so a staging mistake can't touch production data.
- Rotate a token by creating the new one first, updating the secret, then revoking the old one. If a token shows up in a chat, log or commit, revoke it straight away.

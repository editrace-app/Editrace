# Architecture Decision Records

One file per decision: `NNNN-kebab-title.md`, copied from [`0000-template.md`](0000-template.md).

## Lifecycle

```
Proposed ──(code owner approves + merges PR)──▶ Accepted ──▶ Superseded by NNNN
    └──(set Status: Rejected + reason, merge)──▶ Rejected
```

1. **Propose:** in Claude Code, run `/propose-adr`. It grills you on the decision, drafts the ADR, opens a **draft PR** labeled `proposal` + `adr`, and, if the **ADR** category exists in [Discussions](https://github.com/editrace-app/Editrace/discussions/categories/adr), starts a thread there that links to the PR (and the PR links back). If the thread can't be created, the skill says so and you open it by hand.
2. **Discuss:** in the Discussions thread or the PR. Mark the PR Ready for review when done. Approval happens only on the PR.
3. **Approve:** CODEOWNERS covers this folder, so the PR can't merge without code-owner approval. **Merged = Accepted.** A follow-up PR flips the `Status:` line.
4. **Change your mind later:** write a new ADR that supersedes the old one. Accepted ADRs are never rewritten.

To reject an ADR, set `Status: Rejected` with a reason on the PR branch and merge it; a closed PR never reaches `main`. Rejected ADRs stay in the folder so the same debate doesn't happen twice. Merging a Rejected file is a record, not an approval.

## Index

| # | Title | Status |
|---|---|---|
| 0001 | [Host the web app on Cloudflare Workers instead of Vercel](0001-host-web-app-on-cloudflare-workers.md) | Rejected |
| 0002 | [Represent template time as integer frames on one rational timebase, and version the schema with semver](0002-time-representation-and-schema-versioning.md) | Accepted |
| 0003 | [Detect cuts with PySceneDetect's AdaptiveDetector and flag suspect cuts for review](0003-use-pyscenedetect-adaptive-for-cut-detection.md) | Accepted |

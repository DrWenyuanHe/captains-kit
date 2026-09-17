# Source notes

## Source-derived ideas

Reviewed gstack 1.69.0.0 at commit
`ad8400543cd9ce8d07641362db48d44a95417e33` on 2026-09-16:

- [Review workflow](https://github.com/garrytan/gstack/blob/ad8400543cd9ce8d07641362db48d44a95417e33/review/SKILL.md.tmpl): scoped diff review, verification of safety claims, a second failure-oriented pass and handling findings through correction and verification.
- [Review checklist](https://github.com/garrytan/gstack/blob/ad8400543cd9ce8d07641362db48d44a95417e33/review/checklist.md): storage/concurrency risks, trust boundaries, tracing new values through consumers and suppressing unhelpful findings.

These are workflow designs, not evidence of a measured defect-detection rate.
The upstream project is [MIT licensed](https://github.com/garrytan/gstack/blob/ad8400543cd9ce8d07641362db48d44a95417e33/LICENSE), copyright 2026 Garry Tan.

## Captain's Kit synthesis

This is a locally authored workflow informed by those sources, not an imported
gstack skill. The checklist is an original summary and adaptation. It keeps the
evidence requirements while making report-only the default, repairs task-dependent,
and depth proportional to risk. It covers staged and untracked work explicitly and
can coexist with a standards/spec review.

The skill does not depend on gstack's runtime, reviewer scoring, global state,
comment automation or other installed skills. Source links support maintenance;
reading them does not invoke their workflows. Local registry tracking records
changes here; upstream changes are considered manually rather than auto-applied.

## Revisit upstream

- [Current review sources](https://github.com/garrytan/gstack/tree/main/review): inspect the workflow template, checklist, sections and specialists, following relevant shared-helper changes.
- [Review commit history](https://github.com/garrytan/gstack/commits/main/review)
- [Changes since the original baseline](https://github.com/garrytan/gstack/compare/ad8400543cd9ce8d07641362db48d44a95417e33...main)
- [Project changelog](https://github.com/garrytan/gstack/blob/main/CHANGELOG.md)

For an occasional review, choose a fixed candidate commit and compare it with the
latest applicable reviewed commit below. Append the date, full SHA, reviewed scope
and decision, even when no ideas are adopted. Keep the original source links and
earlier history. A partial review covers only its named scope. Record any resulting
skill edit and exercise changed behavior; `check-updates` does not track sources of
locally authored skills.

## Source-review history

| Date | Upstream commit | Scope and decision |
| --- | --- | --- |
| 2026-09-16 | `ad8400543cd9ce8d07641362db48d44a95417e33` | Initial review of the installed 1.69.0.0 review workflow and checklist. Adopted the evidence, risk and consumer-tracing ideas described above; retained local report/fix boundaries. Not a latest-upstream check. |

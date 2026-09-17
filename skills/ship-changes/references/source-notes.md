# Source notes

## Source-derived ideas

Reviewed gstack 1.69.0.0 at commit
`ad8400543cd9ce8d07641362db48d44a95417e33` on 2026-09-16:

- [Ship workflow](https://github.com/garrytan/gstack/blob/ad8400543cd9ce8d07641362db48d44a95417e33/ship/SKILL.md.tmpl): integrating changes before verification, coherent commits, documentation updates, fresh test evidence before push and creating or updating a PR.
- [Test workflow](https://github.com/garrytan/gstack/blob/ad8400543cd9ce8d07641362db48d44a95417e33/ship/sections/tests.md.tmpl): discovering testing infrastructure and distinguishing regressions from existing failures.
- [Coverage audit](https://github.com/garrytan/gstack/blob/ad8400543cd9ce8d07641362db48d44a95417e33/ship/sections/test-coverage.md.tmpl): identifying missing failure and boundary cases and matching existing test conventions.

These describe upstream procedures, not proof that a passing agent review guarantees
a safe release. The upstream project is [MIT licensed](https://github.com/garrytan/gstack/blob/ad8400543cd9ce8d07641362db48d44a95417e33/LICENSE), copyright 2026 Garry Tan.

## Captain's Kit synthesis

This locally authored workflow retains the sequencing and evidence ideas. It uses
the target's commands and release policy, preserves unrelated work, distinguishes
preparation from publication, and requires direct evidence before calling a failure
pre-existing. Verification follows the actual content, including configuration.

It deliberately omits gstack's runtime, global logs, mandatory version bumps,
numeric AI coverage gates, automatic inclusion of all dirty files and fixed test
commands. Review is possible without another skill installed. Merge and deployment
remain distinct outcomes with their own procedures and authorization.

The summaries and instructions are original adaptations, not an upstream package
import. Registry history tracks local maintenance; source links pin the design
reviewed here so future upstream changes can be considered deliberately.

## Revisit upstream

- [Current ship sources](https://github.com/garrytan/gstack/tree/main/ship): inspect the workflow template and sections, following relevant shared-helper changes.
- [Ship commit history](https://github.com/garrytan/gstack/commits/main/ship)
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
| 2026-09-16 | `ad8400543cd9ce8d07641362db48d44a95417e33` | Initial review of the installed 1.69.0.0 ship workflow, test/coverage sections, verification and PR handling. Adopted sequencing and verification ideas; kept project conventions and scoped publication. Not a latest-upstream check. |

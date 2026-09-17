# Source notes

## Source-derived ideas

Reviewed gstack 1.69.0.0 at commit
`ad8400543cd9ce8d07641362db48d44a95417e33` on 2026-09-16:

- [Document-release workflow](https://github.com/garrytan/gstack/blob/ad8400543cd9ce8d07641362db48d44a95417e33/document-release/SKILL.md.tmpl): map changed public surfaces to documentation coverage using reference, how-to, tutorial and explanation lenses; inspect architecture diagrams for drift.
- [Release-body section](https://github.com/garrytan/gstack/blob/ad8400543cd9ce8d07641362db48d44a95417e33/document-release/sections/release-body.md.tmpl): audit setup and contributor instructions, make supported factual corrections, preserve changelog content, and check cross-document consistency and discoverability.

These sources describe a workflow, not evidence of measured documentation quality.
The upstream project is [MIT licensed](https://github.com/garrytan/gstack/blob/ad8400543cd9ce8d07641362db48d44a95417e33/LICENSE), copyright 2026 Garry Tan.

## Captain's Kit synthesis

`docs-sync` is an original, locally authored adaptation, not an imported gstack
package. It supports a read-only audit and local updates before or after release,
including working-tree and already-merged changes. Coverage is based on reader
needs rather than requiring every documentation category for every feature.

Local additions include version-aware historical preservation, nested and generated
documentation discovery, evidence-based diagram corrections, and checks against
the final edits. Product intent is kept distinct from accidental implementation
behavior. The skill uses the target's tooling without depending on gstack's runtime,
logs or other skills. It omits automatic commits, pushes, PR edits, version bumps,
task-list cleanup and changelog scoring. Publication follows separately requested
scope rather than being a consequence of a documentation update.

Source links support maintenance; reading them does not invoke their workflows.
Local registry tracking records changes here. Upstream ideas are reviewed manually.

## Revisit upstream

- [Current document-release sources](https://github.com/garrytan/gstack/tree/main/document-release): inspect the template and sections, following relevant shared-helper changes.
- [Document-release commit history](https://github.com/garrytan/gstack/commits/main/document-release)
- [Changes since the original baseline](https://github.com/garrytan/gstack/compare/ad8400543cd9ce8d07641362db48d44a95417e33...main)
- [Project changelog](https://github.com/garrytan/gstack/blob/main/CHANGELOG.md)

Choose a fixed candidate commit and compare it with the latest applicable reviewed
commit below. Append the date, full SHA, scope and decision, including when nothing
is adopted. Preserve the baseline and earlier rows. A partial review covers only
its named scope. Record resulting skill edits and exercise changed behavior;
`check-updates` does not track sources of locally authored skills.

## Source-review history

| Date | Upstream commit | Scope and decision |
| --- | --- | --- |
| 2026-09-16 | `ad8400543cd9ce8d07641362db48d44a95417e33` | Initial review of the installed 1.69.0.0 document-release template and release-body section. Adopted public-surface coverage, factual auditing, diagram drift and consistency checks; adapted local update and version boundaries as described above. Not a latest-upstream check. |

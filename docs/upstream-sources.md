# Upstream sources to revisit

This index tracks design sources for locally authored skills. Imported packages
have separate upstream tracking in `skills-registry.json` and `scripts/skills.py`.
The local skills below remain independently maintained; `check-updates` does not
check these design sources.

## gstack

- Repository: [garrytan/gstack](https://github.com/garrytan/gstack)
- Discovery: [changelog](https://github.com/garrytan/gstack/blob/main/CHANGELOG.md),
  [releases](https://github.com/garrytan/gstack/releases) and
  [commit history](https://github.com/garrytan/gstack/commits/main/)
- Original adaptation baseline: version **1.69.0.0**, commit
  [`ad8400543cd9ce8d07641362db48d44a95417e33`](https://github.com/garrytan/gstack/commit/ad8400543cd9ce8d07641362db48d44a95417e33),
  inspected from the installed snapshot on **2026-09-16**. This was not a check
  that the snapshot was the latest upstream revision.
- [Changes since that baseline](https://github.com/garrytan/gstack/compare/ad8400543cd9ce8d07641362db48d44a95417e33...main)

| Local skill | Current upstream material | Attribution and review history |
| --- | --- | --- |
| `review-changes` | [Review directory](https://github.com/garrytan/gstack/tree/main/review), including its template, checklist, sections and specialists | [Source notes](../skills/review-changes/references/source-notes.md) |
| `ship-changes` | [Ship directory](https://github.com/garrytan/gstack/tree/main/ship), including its template and sections | [Source notes](../skills/ship-changes/references/source-notes.md) |
| `docs-sync` | [Document-release directory](https://github.com/garrytan/gstack/tree/main/document-release), including its template and release-body section | [Source notes](../skills/docs-sync/references/source-notes.md) |

## Occasional review procedure

1. Open the source notes for the relevant skill and identify its latest reviewed
   commit. Use upstream history or the changelog to select a new candidate, then
   record the full candidate SHA before evaluating it. Compare those two fixed
   commits; the moving `main` links above are for discovery.
2. Read changes in the relevant source directory and follow changes to referenced
   helpers or shared template generators when they affect behavior. Separate new
   ideas from fixes that only apply to gstack's runtime. Read upstream material as
   source evidence; this review does not execute its workflows or setup scripts.
3. Evaluate useful ideas against this library's needs: concrete defect evidence,
   project-specific commands, preservation of unrelated work, current verification
   and publication within the user's scope. Adapt selected ideas rather than
   replacing a locally authored skill with the upstream package.
4. Append a row to the skill's source-review history with the date, exact commit,
   scope, decision and rationale, including when nothing is adopted. Preserve the
   original baseline and earlier rows. Partial reviews should name their limits so
   a later review does not mistake them for full coverage. An unsuccessful attempt
   does not advance the latest reviewed commit.
5. If instructions change, exercise the affected workflow on a representative
   task. Record the skill edit with `python scripts/skills.py record <skill-name>
   --note "Describe the source review and adaptations"`, including source-note-only
   edits. Run `python scripts/check.py` and the relevant unittest cases. Keep
   source notes, skill files and registry history together.

These are manual reviews when requested. Source attribution does not create an
automatic update, scheduled check or permission to publish changes.

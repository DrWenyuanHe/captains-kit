---
name: docs-sync
description: Audit or update project documentation against a branch, PR or release change. Use to document changed features, sync docs with code or check release documentation; not for general prose polishing or publishing a release.
---

# Sync documentation

Make the documentation for the requested version agree with its public behavior.
Trace each correction to evidence and preserve the project's audience and intent.

## Scope and mode

1. Read the target's instructions, documentation entrypoints and Git status,
   including staged, unstaged and untracked work. Identify the requested change
   and documentation version. Prefer the user's comparison range, then the PR
   base, then the repository default branch. Record resolved commits and disclose
   stale or unavailable remote refs.
2. Compare a branch with its merge base. Include working-tree changes for work in
   progress and inspect relevant untracked files separately. A staged-only request
   uses index contents. For an already merged release, use its actual change range;
   an empty branch diff is not evidence that the release needs no documentation.
   Working on the default branch is valid. Ask for a missing range only when it
   materially prevents identifying the requested change.
3. **Audit/check** means report only, preserving files, index and Git state.
   **Sync/update/document these changes** authorizes relevant local documentation
   edits. A bare invocation defaults to audit. Keep unrelated work intact. This
   skill alone does not authorize commits, pushes, PR edits or release actions;
   honor any separately requested publication scope through the project's workflow.

Treat branch documentation as describing the proposed version. Released or
versioned documentation must match the specified release, not arbitrary current
HEAD. Distinguish upcoming behavior from behavior already available to users.

## Audit the change

- Extract changed public surfaces: commands, flags, API behavior, configuration,
  environment variables, capabilities, removals and contributor workflows.
  Read implementation and tests around them to establish actual behavior.
- Discover documentation through the project's navigation and repository search,
  including nested pages, examples, generated references and diagrams. Use the
  [documentation checklist](references/documentation-checklist.md) to map each
  affected surface to current coverage, a concrete mismatch, a useful gap or an
  explicit reason that no documentation change is needed.
- Verify contradictions before editing. Code establishes behavior; specifications
  and authored rationale establish intent. When these conflict, report the conflict
  instead of documenting an apparent bug as intended behavior.

Audit mode ends with evidence and proposed corrections. In update mode, read
[update and verify](references/update-and-verify.md), then complete supported
corrections and proportionate checks. Missing product decisions should block only
the affected edit; continue independent work already within scope.

## Report the result

Summarize the comparison range and intended documentation version, the specific
updates or audit findings, and checks actually performed. For each unresolved
gap, identify the affected surface, reader consequence and suggested destination.
Distinguish inspected/current, updated, and not verified. Show a coverage table
when it helps explain multiple surfaces; do not manufacture one for a single fix.

Claim currency only for the scope actually checked. A successful docs build does
not establish factual correctness, and unchanged files do not establish completeness.

For provenance or maintenance, see [source notes](references/source-notes.md).

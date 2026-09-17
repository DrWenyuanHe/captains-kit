---
name: review-changes
description: Review a branch, PR or working-tree change for behavioral defects, security risks and missing test cases. Use for defect-focused code review or review-and-fix requests; complements a separate standards/spec review.
---

# Review changes

Find actionable defects in the requested change. Ground each finding in a reachable
failure and the code that causes it; checklist matches alone are not findings.

## Establish scope

1. Read the target's instructions and Git status, including staged, unstaged and
   untracked files. Identify the requested change, base, requirements and checks.
   Prefer the user's base, then the PR base, then the repository default. Record
   the resolved base and HEAD commits; disclose stale or unavailable remote refs.
2. For a branch review, compare with the merge base. Include tracked working-tree
   edits when reviewing work in progress (`git diff <merge-base>`), and read relevant
   untracked files separately. For a committed PR, use its actual base/head pair.
   A staged-only request means the index, not the whole working tree. State the
   scope before reviewing; ask only if a material ambiguity remains.
3. Default to **report only**: preserve source files, index, commits and remote
   state. A request to review and fix authorizes relevant local repairs; read
   [fix and verify](references/fix-and-verify.md) for that mode. Review alone does
   not authorize posting comments, committing or publishing.

Being on the default branch does not rule out a working-tree review. If the
requested scope is empty, report that without substituting unrelated changes.

## Review the behavior

- Read the full scoped diff and enough surrounding code to trace changed inputs,
  outputs, callers and failure paths. Use the applicable rows of the
  [risk checklist](references/risk-checklist.md); omit domains absent from the change.
- Compare behavior with available requirements and repository rules. If another
  review already covered standards or specification compliance, reuse its evidence
  when it still matches this snapshot and investigate gaps instead of duplicating it.
- Before reporting a defect, inspect the possible guard, validator or test that
  could disprove it. Verify unfamiliar framework behavior against the version in
  use and primary documentation when needed. Separate unresolved questions from
  confirmed defects.
- For consequential changes, take a second pass through a concrete failure path
  such as a retry, concurrent request or partial write. Scale effort to the risk;
  small edits do not need a fixed team of reviewers or nested model calls.
- Run focused checks when they resolve uncertainty and can run without overwriting
  user work; use an isolated copy if necessary. Record commands and outcomes. A
  failing test is evidence to investigate, not automatically proof of a regression.

## Deliver the review

Present findings in impact order. For each, include the affected file and tight
line range, trigger, consequence, supporting evidence and a proposed correction.
Use the project's severity scale, or P1 for a serious defect, P2 for an ordinary
correctness issue and P3 for a minor actionable defect. Reserve P0 for an immediate,
demonstrated emergency. Deduplicate findings about the same underlying cause.

Keep documented standard violations, design preferences and unresolved questions
distinct from defects. Omit formatting already covered by tooling, speculative
edge cases ruled out by input constraints and issues already fixed in the diff.
Report fixes separately in fix mode.

End with the reviewed scope, checks actually run and material gaps. If no defects
were found, say so with those limits. Avoid numeric confidence or quality scores
that imply measured reliability.

For provenance or maintenance, see [source notes](references/source-notes.md).

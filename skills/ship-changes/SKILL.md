---
name: ship-changes
description: Prepare or publish a tested change as a pull request using the repository's own checks and conventions. Use when asked to ship a branch, prepare a PR or create/update a PR; distinguish preparation from authorized publication. Does not deploy services or merge PRs.
---

# Ship changes

Carry the requested change through verification and the requested PR boundary.
Use the target project's workflow rather than introducing a release system.

## Establish the requested outcome

- **Prepare / check readiness:** inspect, make authorized local corrections, run
  checks and draft the PR description. Leave commits and publication pending unless
  already requested. With no actionable request beyond the skill name, prepare.
- **Ship / create or update a PR:** complete the relevant local work, then commit,
  push and create or update the PR within the user's stated scope. Follow
  [publication](references/publication.md). An existing authorization needs no
  extra confirmation. Merge, deployment and package publication are separate actions.
- **Mixed request including deployment or merge:** complete this skill's applicable
  work, then use the project's established procedure for the remaining authorized
  action. A PR URL alone does not satisfy a deployment request.

## Prepare the change

1. Read project instructions, contribution guidance, manifests, CI and Git status.
   Identify the branch, base, destination, intended files/hunks, existing commits
   and any existing PR. Preserve unrelated staged, unstaged and untracked work.
   A dirty tree is an inventory to classify, not permission to ship everything.
2. Use the requested base, the existing PR base or the repository default, in that
   order. Record base and HEAD commits. In preparation mode, disclose remote
   freshness limits. Before publication, refresh the intended remote refs and
   inspect divergence. Integrate base updates only as required by the project's
   policy or to resolve a demonstrated conflict, using its merge/rebase convention.
   Resolve only unambiguous conflicts; preserve user work and surface decisions.
3. Review the scoped change for concrete behavioral defects, requirement gaps,
   unsafe input boundaries and missing cases. Read changed callers and consumers.
   Reuse a current review if it covers this content; otherwise perform a focused
   review. If `review-changes` is installed, it can provide the deeper checklist;
   it is optional, and its repair mode still follows the user's requested scope.
4. When repairs are authorized, correct established defects within scope; for a
   readiness-only assessment, report them. A compatibility or product decision
   needs the user's choice; a mechanical correction already authorized does not.
   Follow [verification](references/verification.md) to gather evidence against
   the final code. Record unrun checks and failed checks explicitly.
5. Update documentation, release notes or version files only when the change and
   repository policy require them. Use the existing format and versioning rules.
   Determine release impact from behavior and compatibility, not diff size.

In a skill library, shipping means validating the changed packages and registry,
not invoking those skills against another project. More generally, keep generated
setup, local reports and machine configuration in their owning project.

## Finish

For preparation, give the proposed PR title/body, scoped change summary, checks
and remaining blockers. For publication, verify the remote branch and PR refer to
the intended commit and base, and provide their URL and current check status.
Distinguish locally verified, remotely pending, blocked and completed work. Do not
claim CI passed or a deployment occurred based on local tests or PR creation.

For provenance or maintenance, see [source notes](references/source-notes.md).

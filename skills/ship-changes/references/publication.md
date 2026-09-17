# Publication

Use this reference when the requested outcome includes committing, pushing or
creating/updating a PR. Carry forward the user's destination and authorization.
If publication is not yet authorized, finish verification and prepare the exact
diff and PR text before asking about the remaining action.

## Scope and branch

Inspect the index as well as the working tree. Stage only task-owned files or
hunks; preserve unrelated staged content. If changes are entangled, isolate the
intended snapshot without resetting or stashing away someone else's work. Inspect
the staged diff immediately before committing. Make coherent commits that satisfy
the project's conventions; splitting commits is useful only when each remains valid.

When starting on the default branch, create a feature branch using the project's
naming convention before publishing. Keep existing work on its current feature
branch when appropriate. Inspect the complete base-to-head diff and outgoing
commits, not just the files edited during this session. Unrelated existing commits
may require an isolated branch; do not publish them incidentally.

Refresh the destination branch before pushing. If it advanced, inspect and
reconcile with the project's normal workflow, then repeat affected verification.
Use a normal push. An unexpected non-fast-forward result is a divergence to resolve,
not permission to force-push or rewrite published history.

## Publish and verify

Before writing remotely, inspect the final diff, outgoing commits and PR text for
unintended files, credentials and private local records. Use the project's existing
checks; keep credentials in its established authentication mechanism. If destination
or scope is still materially ambiguous, present the prepared result and ask only
for that missing decision.

Push the intended branch, then find an open PR by repository and head/base branches.
Create one if absent, or update the matching PR's affected sections. Preserve
human-authored context and unrelated edits in an existing body. Follow a PR
template when present; otherwise explain behavior, verification and material gaps.
Do not invent a version prefix or changelog requirement.

Use structured tool inputs for multiline PR text, or write the exact body to a
temporary UTF-8 file and pass it with the CLI's body-file option. This avoids shell
interpretation and preserves line breaks. Keep that file out of the shipped diff.

After an uncertain push or PR response, read the remote state before retrying.
Verify the remote head commit, PR base, title/body and URL. Report pending or failed
CI honestly; if the user requested waiting for CI, follow it through. PR creation
does not authorize merging, deployment, release tagging or messages/comments to
other people.

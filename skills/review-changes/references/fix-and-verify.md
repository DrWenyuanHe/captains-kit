# Fix and verify

Use after the user requests repairs as part of the review.

Apply a focused correction when the intended behavior is established. Preserve
unrelated edits, including other changes in the same file. If a fix requires a
product decision, compatibility break or ambiguous data migration, complete the
independent repairs and ask about that concrete decision with the affected behavior
and options. Existing authorization remains valid; do not ask again for routine fixes.

For a behavioral bug, add a regression check in the existing test framework where
practical. Verify it distinguishes the failure from the correction. Prefer observable
results and realistic boundaries over tests that repeat implementation details.
For low-impact prose or configuration edits, use the project's relevant validation.

Run the affected checks after the fix. If they fail, investigate whether the change
caused it; compare against the base in an isolated copy when that distinction matters.
Do not label a failure pre-existing merely because its test file was unchanged.

Inspect the final diff for collateral changes and recheck affected callers. Report
each resolved finding, the verification result and anything unresolved. Leave
committing, posting comments and publishing to the user's separately requested scope.

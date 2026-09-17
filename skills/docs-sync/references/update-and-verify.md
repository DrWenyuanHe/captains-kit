# Update and verify

## Apply supported corrections

- Make focused edits supported by the comparison and intended version. Preserve
  unrelated user edits, authored voice, rationale and project terminology.
- Add a short example, migration note or section when it is needed to document the
  changed behavior. Use existing pages and navigation when suitable. Broader new
  guides belong in a separate writing task when their scope or content is undecided.
- Update generated documentation through its source and established generator.
  Preserve hand-maintained content. If the generator cannot run, update the source
  when justified and report generated output as unverified or stale.
- Correct diagram labels and flows when the implementation makes the correction
  unambiguous. For uncertain architectural meaning, describe the discrepancy and
  the decision needed. Preserve or regenerate companion rendered assets according
  to the project's practice; do not claim a render you did not inspect.
- Preserve historical release entries. Update the appropriate upcoming entry when
  the project's release process calls for it; otherwise report a missing release
  note. Do not invent a release date, rewrite prior releases to describe current
  behavior, or bump versions as a side effect of syncing documentation.
- Keep existing positioning, policy, security promises and design rationale unless
  the requested change clearly resolves them. Ask only for material missing intent
  or authorization; section length alone is not a reason to stop.

Source or test defects found during this task belong in the report unless repairing
them is independently within the user's request. Do not change implementation just
to make a stale example work. Keep task tracking and unrelated prose cleanup outside
the documentation change. References to other skills do not activate them; preserve
any explicit-only invocation rules.

## Verify the result

Run the applicable checks identified during the audit against the final edits.
Validate changed examples and links, then search for stale identifiers again and
inspect remaining hits in their version context. Review the complete documentation
diff for accidental content loss, unsupported claims and contradictions.

Recheck status and the original work boundary. Preserve the index and existing
commits unless their modification is separately authorized. If a generator changes
files outside the intended scope, inspect and account for them rather than staging
or discarding them wholesale.

Report concrete corrections and any remaining gaps with their evidence. A blocked
render or unanswered product question does not prevent delivering the independently
verified local edits.

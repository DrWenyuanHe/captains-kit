# Agentic development readiness audit

- Target and revision/date: [actual target; note dirty state]
- Scope sampled: [features, entrypoints and workflows]
- Method: [static inspection and any non-mutating commands actually run]
- Conclusion: [what can be delegated, what blocks it, or what remains unknown]

## Evidence

Use Verified, Partial, Missing, Unverified or Not applicable. Explain applicability
and separate runtime claims from static evidence. Do not compute a total score.

| Dimension | Status | Evidence and limits |
| --- | --- | --- |
| Focused context | [status] | [path:line / observed navigation] |
| Repeatable environment | [status] | [implementation and runtime evidence separately] |
| Code boundaries | [status] | [enforced rule, example and blind spots] |
| Executable acceptance | [status] | [check/outcome and revision] |
| Runtime visibility | [status] | [reproduction, diagnostic evidence or applicability] |
| Continuity and outcomes | [status] | [handoff and measured task evidence separately] |

## Highest priority improvements

For each of up to three findings:

- Impact: [blocking / high priority; concrete symptom]
- Evidence: [observed source or command result]
- Smallest repair: [local change, reusing the existing pattern]
- Acceptance: [how to demonstrate the repair works]
- Uncertainty: [what has not been established]

## Verification log

| Command or inspection | Snapshot | Result | Limits |
| --- | --- | --- | --- |
| [actual check] | [revision/date] | [exit code or observation] | [scope/not run reason] |

## Next step

[One actionable recommendation or a small baseline experiment. No automatic repairs.]

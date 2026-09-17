# Risk checklist

Use this as a set of investigation prompts. A finding needs a plausible trigger,
observable impact and supporting code, not merely a pattern on this list.

| Changed area | Trace and verify |
| --- | --- |
| Persistence and concurrency | Read/check/write sequences, uniqueness enforced at the storage boundary, atomic transitions, transaction boundaries, retry behavior and partially completed work. Check both the winning and losing request. |
| Authentication and authorization | Identity and tenant scope at the actual read/write boundary, object-level access, new routes and bypass paths. A hidden UI control is not an access check. |
| Untrusted inputs and outputs | User and model output reaching queries, shell arguments, file paths, templates or network requests. Verify parameterization, type/shape validation, escaping for the destination and URL/network policy where relevant. |
| New states and contracts | Search for sibling enum/status values and read their consumers, including allowlists, serializers, defaults, storage and UI. Check compatibility with existing clients and stored data. |
| Async work and failures | Blocking work on an event loop, missing awaits, cancellation, timeouts, cleanup, duplicate delivery, swallowed errors and side effects that survive a failed operation. |
| Schema and migrations | Existing rows, nullability and defaults, deployment order, lock duration and compatibility between concurrently deployed versions. Exercise rollback only when the project promises it. |
| Performance | A concrete increase in queries, repeated scans, unbounded allocation or payload/bundle size on a reachable path. Establish input size and impact before recommending optimization. |
| Time and numeric boundaries | Time zones, inclusive/exclusive ranges, rounding, units, serialization and coercion across system boundaries. Check the consumers' expectations. |
| Packaging and CI | Artifact contents, supported platforms, permissions, secret handling, install paths, workflow inputs and consumers of renamed outputs. Follow the project's actual distribution method. |
| Tests | Changed behavior, failure and boundary cases, assertions that observe consequences, and relevant integration boundaries. Use measured coverage only if available; a list of missing scenarios is useful without a percentage. |

When a check finds a candidate defect, trace a counterexample before reporting it:
could an earlier validator reject the input, a database constraint prevent the
race, or an existing consumer intentionally handle the new state through a default?
Cite the code that settles the question.

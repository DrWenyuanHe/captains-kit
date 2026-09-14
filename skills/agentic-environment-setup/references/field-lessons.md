# General lessons from the motivating optimization work

The kit was informed by a review of several agentic-development optimization
tasks and their local design documentation. This page records generalized
decisions, not session transcripts, a production audit or measured productivity
results. Some work was local and uncommitted; some remained proposed. Private
application details and operational configuration are intentionally absent.

- **A useful map routes to a task.** Directory inventories help less than a short
  path to the relevant contract, example, boundary and verification command.
  Generated navigation and a reviewed explanation solve different problems.
- **Freshness is not truth.** Matching hashes and valid links can prove that a
  review record tracks a source snapshot. They cannot establish that the review
  was thoughtful or that the running deployment matches the snapshot.
- **Environment copying has consequences.** Preserving files can preserve the
  wrong backend. Readiness needs to distinguish source/preview identity from
  the mutable service target. Blanket copying is a poor default for a general kit.
  Prepare a dedicated development profile and verify each consumer's effective
  target; a global agent-tool connection can remain pointed elsewhere.
- **A dev server has an owner.** On-demand startup and a reported checkout-specific
  URL reduce accidental verification against another task's code. Shared-service
  lifecycle rules matter when multiple agents work concurrently.
- **Small lint rules can teach.** Useful errors identify the canonical repair.
  Legacy suppression counts can enable adoption but may miss a new violation
  replacing an old one at the same count. Explain that blind spot.
- **Diagnostics need an evidence boundary.** A route template and outcome timing
  may be enough to find the failing area without exposing record data. Timing to
  response headers does not measure complete rendering. Current Git metadata does
  not prove exactly which code a hot-reloading browser has executed.
- **Separate local implementation from readiness.** A passing focused test is
  useful evidence within its scope. It does not attest deployment, migration
  application, clean CI or successful delegation on representative tasks.
- **CI has several evidence levels.** A workflow can exist before a hosted run
  succeeds or required merge checks are enabled. Verify execution and enforcement
  independently, including skipped-check behavior, before claiming a gate works.
- **Faster feedback must retain its signal.** Measure comparable runs and confirm
  optimized checks still detect representative defects. A faster test file does
  not establish a full-suite improvement or a productivity gain.

These lessons inform the kit's defaults. Revalidate them against each project's
tools and constraints instead of copying the original implementation wholesale.

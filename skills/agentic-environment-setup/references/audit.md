# Audit playbook

Assess whether an agent can complete a representative change correctly with
reasonable independence. This rubric is a diagnostic aid, not a validated
industry benchmark. Do not award points for file counts, agents or tooling brands.

## Read-only procedure

1. Identify the target, revision, dirty state, project maturity and requested scope.
   Read applicable instructions, docs index, manifests and CI. Sample a real
   feature or public library path and its tests; use more samples if boundaries
   differ materially across the project.
2. Follow the same navigation a new agent would use. Locate the relevant domain
   terminology, implementation, contracts, canonical patterns and check commands.
   Compare documentation with current source instead of assuming it is fresh.
3. Inspect setup, environment selection, test tooling, diagnostics and CI before
   executing commands. During audit, do not install dependencies, edit files,
   start services, create worktrees, migrate/reset data or contact a production
   backend. A command called `doctor` or `test` is not necessarily read-only.
4. Run an existing non-mutating check only if its implementation and dependencies
   make that safe within the request. Use existing logs/results as historical
   evidence with their revision/date. When execution would mutate state or needs
   unavailable credentials, mark the runtime claim unverified and describe the
   separate authorized exercise needed. Continue the static audit.
5. Report the six dimensions below with concrete evidence. Rank up to three
   improvements by correctness risk, repeated intervention and unblock value.
   Do not expand the report with style-only debt or speculative infrastructure.

## Status definitions

| Status | Meaning |
| --- | --- |
| Verified | Relevant behavior was directly demonstrated for the stated scope/revision, or a purely static criterion was inspected in source. |
| Partial | Some required behavior is supported by evidence, but a material gap remains. |
| Missing | Inspection establishes that a needed capability is absent or fails. |
| Unverified | A capability is claimed or may exist, but available evidence cannot establish it. |
| Not applicable | The project/task does not need this capability; explain why. |

Separate static and runtime claims within a dimension when needed. For example,
"bootstrap implementation inspected; repeatability unverified" is more useful
than a single green check beside the existence of a setup script. Report
sampling limits: one passing route does not certify the entire application.

## Six dimensions

| Dimension | Inspect | Evidence that supports readiness | Common important gap |
| --- | --- | --- | --- |
| Focused context | Entrypoint, docs, area routing, real contracts/examples and freshness procedure | A fresh reader can find the sampled path and its checks; linked facts match current source | Critical knowledge lives only in chat; stale or conflicting instructions |
| Repeatable environment | Lockfile/runtime, bootstrap, readiness, previews and shared-state targets | A disposable checkout can be prepared twice while preserving chosen config; environment identity is known | Hidden setup steps; accidental shared backend; wrong preview checkout |
| Code boundaries | Public interfaces, dependency direction, typed external boundaries and canonical helpers | Important invariants are visible and enforced with useful failures; allowed cases pass | Bypassed adapter, guessed external shape, unchecked architectural drift |
| Executable acceptance | Task criteria, focused tests, representative workflows and required CI | Relevant checks detect the failure and confirm the outcome; evidence names the revision | Tests only mirror implementation; completion depends on agent self-report |
| Runtime visibility | Existing logs/browser/CLI evidence, identity, failure paths and redaction | A synthetic failure can be reproduced and diagnosed without leaking sensitive data | Runtime failures invisible; diagnostic evidence cannot identify the instance |
| Continuity and outcomes | Durable handoffs, decisions, last checks, baseline/follow-up task results | A fresh session resumes from artifacts; measured outcomes include quality and human effort | Repeated rediscovery; no result evidence; tooling success confused with task success |

For projects with persistent state, apply the
[state-isolation checklist](state-isolation.md) under environment and verification.
For environment-dependent projects, inspect the
[propagation map](environment-propagation.md): configuration sources, override
preservation and effective app/CLI/agent-tool targets. Check existing evidence
of repeat setup and target-conflict handling without creating a new environment.
Use [optional infrastructure guidance](optional-infrastructure.md) only where
CI/GitHub Actions or Docker/Supabase fit the project. Report CI configuration,
remote execution and merge enforcement separately; absence of an optional stack
is not itself a defect.
For a docs-only project, runtime previews and databases usually do not apply.
For an empty project without a stack, evaluate its starting instructions and
explicit next milestone; do not penalize nonexistent application architecture.

## Rank findings by practical impact

- **Blocking:** an agent cannot safely run or validate the intended task, or a
  boundary failure risks unauthorized state changes or material correctness loss.
- **High priority:** a demonstrated gap repeatedly requires human intervention,
  hides regressions or prevents reliable handoff.
- **Later:** useful optimization whose cost is not yet justified by evidence.

Each recommendation must include an observed symptom, file/command evidence,
the smallest repair, an acceptance check and remaining uncertainty. Existing
adequate solutions should be acknowledged rather than replaced to match this kit.
Missing measurement is not proof the project performs poorly.

## Deliver the report

Use [the report template](../assets/templates/audit-report.md) if useful. Include
target/revision, scope sampled, each dimension's status, evidence actually gathered,
the top recommendations and a concise verification log. A report can live in the
response; create a file only when the user asks for an artifact. Do not make
repairs under an audit-only request.

Conclude whether the sampled workflow can be delegated now, what prerequisite
blocks it, or which unverified behavior prevents a conclusion. Avoid an aggregate
percentage that conceals a critical failure. To test the productivity hypothesis,
propose the small experiment in [evaluate.md](evaluate.md).

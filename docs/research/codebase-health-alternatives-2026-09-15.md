# Codebase health skills: source review

Reviewed 2026-09-15. Here, health means codebase quality and maintainability.

## Verdict

**Our synthesis: gstack's `/health` is a useful dashboard recipe, but it is
not the strongest general codebase-health skill in this comparison.** Its
runner example can conceal failure, and the score needs more context about
what was actually checked. For Captain's Kit, start from a small adapted
`tech-debt-audit`; use Codelens for structural measurements or Desloppify for
sustained cleanup. CodeScene is another option when licensed tooling fits.

These recommendations reflect source design and implementation findings,
not a measured league table. The sections below give the evidence and limits.

## GitHub stars

Exact repository star counts retrieved from the public GitHub REST API on
2026-09-15 at 16:38 UTC. Counts cover each whole repository; for gstack and
other skill collections, they do not measure adoption of the health skill alone.

| Repository | Stars |
| --- | ---: |
| [garrytan/gstack](https://github.com/garrytan/gstack) | 133,177 |
| [millionco/react-doctor](https://github.com/millionco/react-doctor) | 14,849 |
| [cloudflare/security-audit-skill](https://github.com/cloudflare/security-audit-skill) | 4,860 |
| [peteromallet/desloppify](https://github.com/peteromallet/desloppify) | 3,124 |
| [ksimback/tech-debt-skill](https://github.com/ksimback/tech-debt-skill) | 594 |
| [codescene-oss/codescene-mcp-server](https://github.com/codescene-oss/codescene-mcp-server) | 64 |
| [DropFan/codelens](https://github.com/DropFan/codelens) | 2 |
| [lepinkainen/skills](https://github.com/lepinkainen/skills) | 1 |

Source field: `stargazers_count` from `GET /repos/{owner}/{repo}`. These counts
provide adoption context; the recommendations above are based on the source
review. In particular, Codelens has very limited public adoption evidence and
should be treated as an exploratory candidate.

## Method and limits

This is a static review of public, first-party skill files, implementation,
repository trees, and selected test/evaluation definitions at the commits below.
No external skill was installed or executed. Harmless local shell and arithmetic
experiments checked the gstack failure scenarios described below. The recommendations are our
synthesis, not upstream claims or results of a controlled comparison.
Test files and evaluation infrastructure show what maintainers can check; their
presence does not prove detection accuracy or that a skill is best. No shared
benchmark establishing a winner across these candidates was found in the
reviewed materials. Scores from different products are not interchangeable.

## Reviewed snapshots

| Repository | Ref | Reviewed commit | Main source |
| --- | --- | --- | --- |
| garrytan/gstack | main | `4a3c6a8a3cad82cfffdaa4d152e1c5ae5c4af659` | [Health template](https://github.com/garrytan/gstack/blob/4a3c6a8a3cad82cfffdaa4d152e1c5ae5c4af659/health/SKILL.md.tmpl) |
| peteromallet/desloppify | main | `3a7735d531a96b6a226bfbdc9fd662b14195f857` | [Skill](https://github.com/peteromallet/desloppify/blob/3a7735d531a96b6a226bfbdc9fd662b14195f857/docs/SKILL.md) |
| DropFan/codelens | rust | `3fb6634540b3f72673d7bdb0858c7824b194108e` | [Skill](https://github.com/DropFan/codelens/blob/3fb6634540b3f72673d7bdb0858c7824b194108e/skills/codelens/SKILL.md) |
| ksimback/tech-debt-skill | main | `5a15c1ca4a929b2759461c218478de391a8bda0f` | [Skill](https://github.com/ksimback/tech-debt-skill/blob/5a15c1ca4a929b2759461c218478de391a8bda0f/SKILL.md) |
| lepinkainen/skills | main | `35b1c6f88e3d84354a067a9d26755417035ad3dd` | [Skill](https://github.com/lepinkainen/skills/blob/35b1c6f88e3d84354a067a9d26755417035ad3dd/code-health/SKILL.md) |
| millionco/react-doctor | main | `922616f08db7d48463b1495e4dbdb699fc3d7c34` | [Skill](https://github.com/millionco/react-doctor/blob/922616f08db7d48463b1495e4dbdb699fc3d7c34/skills/react-doctor/SKILL.md) |
| cloudflare/security-audit-skill | main | `c1c8a8c1471069fb0e188eeaff69b8e8db6564a8` | [Skill](https://github.com/cloudflare/security-audit-skill/blob/c1c8a8c1471069fb0e188eeaff69b8e8db6564a8/skills/security-audit/SKILL.md) |
| codescene-oss/codescene-mcp-server | main | Branch sources accessed 2026-09-15; not commit-pinned | [Debt prioritization skill](https://github.com/codescene-oss/codescene-mcp-server/blob/main/skills/prioritizing-technical-debt/SKILL.md) |

## gstack health review

**Source facts.** `/health` uses project-configured commands or detects tools,
reports results without fixing application code, and saves history. It can
persist its detected command configuration with user consent. The core checks
cover types, lint, tests, dead code, and shell lint; GBrain contributes another
10% when available.
[Health template](https://github.com/garrytan/gstack/blob/4a3c6a8a3cad82cfffdaa4d152e1c5ae5c4af659/health/SKILL.md.tmpl).

**Our source-based findings:**

- **Failure reporting:** lines 125–126 capture `$?` after piping the checker
  into `tail`. Without `pipefail`, that is the tail process's status. An agent
  following the example can record a failed checker as successful.
- **Evidence loss:** counting diagnostics from the displayed tail can
  undercount errors. Parse complete output; truncate only the presentation.
- **Coverage of checks:** skipped dimensions lose their weights. A clean linter
  alone can produce 10/10. Missing checks should remain visible as unknown.
- **Meaning of the score:** test pass percentage does not measure coverage or
  assertion quality; local GBrain sync condition is a separate operational concern.
- **Trend reliability:** history has branch, scores, timestamp, and duration,
  but omits commit, command/version, configuration, and comparable-scope metadata.

These conclusions follow from the template's runner, rubric, and history schema.
[Runner and scoring](https://github.com/garrytan/gstack/blob/4a3c6a8a3cad82cfffdaa4d152e1c5ae5c4af659/health/SKILL.md.tmpl#L125),
[History](https://github.com/garrytan/gstack/blob/4a3c6a8a3cad82cfffdaa4d152e1c5ae5c4af659/health/SKILL.md.tmpl#L238).

### Local verification

The following synthetic Bash experiment exercises the pipeline semantics,
without installing gstack or running a project checker:

```bash
set +o pipefail
(exit 23) 2>&1 | tail -50
printf '%s\n' "$?"  # observed: 0
set -o pipefail
(exit 23) 2>&1 | tail -50
printf '%s\n' "$?"  # observed: 23
```

A second synthetic stream with 60 diagnostic lines followed by 20 context
lines left only 30 diagnostics after `tail -50`. Arithmetic using the published
weights produced 10.0 for a sole clean linter and 6.89 with all five code
categories present, a test score of zero, and all other scores at ten.
These are counterexamples to interpreting the composite as a release verdict;
they are not claims that every agent invocation will follow the example literally.

### Packaging and validation

The generated entrypoint calls gstack startup, slug, and completion helpers,
includes onboarding/telemetry integration, and supports a degraded mode when
startup is unavailable. Copying only the health directory therefore needs
adaptation to retain reliable standalone behavior.
[Generated skill](https://github.com/garrytan/gstack/blob/4a3c6a8a3cad82cfffdaa4d152e1c5ae5c4af659/health/SKILL.md).
The project documents native Codex installation as well as Claude; this is an
ecosystem-dependency concern, not a claim that gstack is Claude-only.
[Installation documentation](https://github.com/garrytan/gstack/blob/4a3c6a8a3cad82cfffdaa4d152e1c5ae5c4af659/README.md).

Gstack has substantial test/evaluation infrastructure. The inspected LLM-judge
suite checks the separate browser-QA health rubric; that does not validate the
codebase `/health` runner or calibrate its score. No comparative `/health`
benchmark was established by this review.
[Test commands](https://github.com/garrytan/gstack/blob/4a3c6a8a3cad82cfffdaa4d152e1c5ae5c4af659/package.json),
[Inspected evaluation suite](https://github.com/garrytan/gstack/blob/4a3c6a8a3cad82cfffdaa4d152e1c5ae5c4af659/test/skill-llm-eval.test.ts).

## Direct alternatives

### Desloppify: strongest candidate for sustained cleanup

**Source facts.** The workflow maintains findings and a queue through scanning,
subjective review, planning, fixing, and rescanning. Its documented blend is
25% mechanical findings and 75% subjective review; strict scoring keeps accepted
debt visible. Imports require evidence, suggestions, and confidence. The skill
includes dedicated-branch creation, commits, periodic pushes, and upstream issue
or PR creation; its default workflow goes well beyond an audit. It depends on
the Desloppify runtime and host-specific review runners/overlays.
[Skill](https://github.com/peteromallet/desloppify/blob/3a7735d531a96b6a226bfbdc9fd662b14195f857/docs/SKILL.md).

There is substantive scoring implementation and core/full test CI. Aggregation
exposes effective weights and pool contributions, and falls back to mechanical
scoring when no subjective pool exists.
[Aggregation](https://github.com/peteromallet/desloppify/blob/3a7735d531a96b6a226bfbdc9fd662b14195f857/desloppify/engine/_scoring/results/health.py),
[CI](https://github.com/peteromallet/desloppify/blob/3a7735d531a96b6a226bfbdc9fd662b14195f857/.github/workflows/ci.yml).

**Our assessment.** This is the strongest direct candidate here for a managed
debt-reduction program, but its subjective majority makes the score sensitive
to reviewer/model judgment. Explicitly constrain audit-only work. Source drift
also needs attention: the skill describes automatic score-anchoring resets,
while the implementation disables that penalty because of false positives.
[Integrity implementation](https://github.com/peteromallet/desloppify/blob/3a7735d531a96b6a226bfbdc9fd662b14195f857/desloppify/engine/_scoring/state_integration_subjective.py).

### Codelens: strongest candidate for repeatable structural metrics

**Source facts.** The Rust CLI skill combines health grades, churn hotspots,
change coupling, author concentration, git-ref comparisons, saved baselines,
and CI regression gates. It prefers an available MCP connection for simple
read queries. Snapshot/CI workflows write state or configuration. Runtime
installation is separate from the small skill.
[Skill](https://github.com/DropFan/codelens/blob/3fb6634540b3f72673d7bdb0858c7824b194108e/skills/codelens/SKILL.md).

The local scoring implementation weights complexity 25%, function size 20%,
comment ratio 10%, file size 20%, nesting 15%, and duplication 10%. Missing
duplication gets a separately named reduced model, with renormalization tests.
CI defines tests on Linux, macOS, and Windows.
[Scoring and unit tests](https://github.com/DropFan/codelens/blob/3fb6634540b3f72673d7bdb0858c7824b194108e/crates/codelens-core/src/insight/scoring/default.rs),
[CI](https://github.com/DropFan/codelens/blob/3fb6634540b3f72673d7bdb0858c7824b194108e/.github/workflows/ci.yml).

**Our assessment.** A good complement to ordinary tests/typechecking when the
question is where debt accumulates or whether structural metrics regressed.
The transparent score is still a chosen heuristic: comment density and file
size do not establish correctness, security, or architecture quality. Its score
must not replace a build/test gate or human assessment of change coupling.

### tech-debt-skill: strongest lightweight audit recipe

**Source facts.** This manually invoked Claude Code skill requires architectural
orientation, churn analysis, nine debt dimensions, file-and-line citations,
prioritized effort/severity, open questions, and explanations of suspicious
patterns that are actually appropriate. It writes a root audit report and
updates resolved/new findings on subsequent runs. It proposes stack-specific
tools, skips missing tools, and forbids unapproved global tool installation.
It references Claude's `TodoWrite` and Task tools. The inspected snapshot has
README and skill files, with no test or evaluation suite visible in its tree.
[Skill](https://github.com/ksimback/tech-debt-skill/blob/5a15c1ca4a929b2759461c218478de391a8bda0f/SKILL.md),
[Tree](https://github.com/ksimback/tech-debt-skill/tree/5a15c1ca4a929b2759461c218478de391a8bda0f).

**Our assessment.** Best starting recipe here for a small, independently
maintained Captain's Kit audit. Adapt the host-specific tools and preserve
manual invocation. Remove the suggested 30–80 finding target, which creates
pressure to pad despite the anti-padding instructions. Add explicit artifact
and execution boundaries, measurement metadata, and demonstrated workflow
checks before importing an adaptation. It provides no calibrated health score.

### lepinkainen code-health: useful small scripts, weaker audit depth

**Source facts.** The short skill runs a Python scanner and supplies optional
Go/Python/JS function-analysis helpers; JSON findings contain severity,
location, and a suggested action.
[Skill](https://github.com/lepinkainen/skills/blob/35b1c6f88e3d84354a067a9d26755417035ad3dd/code-health/SKILL.md).

Actual `health.py` is substantially narrower than its check descriptions:
`check_tests` implements Go and Python paths but no JS/TS path, so JS/TS test
counts remain zero; `check_dupes` searches Go comments for duplication hints;
`check_size` uses line thresholds, without the listed churn calculation.
`check_docs` tests whether the matched declaration line begins with a comment,
although grep context is requested separately. That can flag documented Go
exports. Per-check exceptions are collected, but `main` has no failing exit
status for them. No accompanying health tests were visible in the inspected
tree.
[Scanner](https://github.com/lepinkainen/skills/blob/35b1c6f88e3d84354a067a9d26755417035ad3dd/code-health/scripts/health.py),
[Tree](https://github.com/lepinkainen/skills/tree/35b1c6f88e3d84354a067a9d26755417035ad3dd).

**Our assessment.** Easy to understand and locally inspect, but not a leading
general health skill without implementation fixes and meaningful fixtures.
The listed issues are static source findings, not results from running it.

### CodeScene MCP: licensed analysis and focused health skills

**Source facts.** The public repository includes small skills for debt
prioritization, refactoring, safeguards, and risk-based testing. Prioritization
combines hotspots, churn, explicit goals, and file scores. Refactoring starts
with detailed findings and a baseline, takes small structural steps, and
remeasures. These skills depend on the CodeScene MCP tools.
[Prioritization skill](https://github.com/codescene-oss/codescene-mcp-server/blob/main/skills/prioritizing-technical-debt/SKILL.md),
[Refactoring skill](https://github.com/codescene-oss/codescene-mcp-server/blob/main/skills/guiding-refactoring-with-code-health/SKILL.md).

Local analysis uses an embedded CodeScene CLI and requires a valid license.
Project hotspots, goals, and ownership require CodeScene Core and API access.
The documented safeguard results include checked/eligible-file counts and
distinguish clean results from a run with no applicable changes.
[Tools and license boundaries](https://github.com/codescene-oss/codescene-mcp-server/blob/main/docs/tools.md).

**Our assessment.** Worth evaluating for tool-grounded review and team debt
prioritization when licensing is acceptable. The evidence-coverage metadata is
a better contract than an unexplained score. It adds setup and licensing
dependencies to a personal skill library; reviewing the open MCP wrapper and
skills does not independently validate the embedded engine or vendor benchmark
claims. This review did not execute the engine or assess pricing.

## Adjacent specialists

### React Doctor: React diagnostics and regression prevention

**Source facts.** The skill covers changed/full scans, design checks, and
runtime tracing. Cleanup edits source; its fetched triage playbook says it
does not commit or open PRs. Commands use `npx ...@latest`, and full triage
fetches a mutable first-party playbook, so pinning the skill alone does not
pin its behavior.
[Skill](https://github.com/millionco/react-doctor/blob/922616f08db7d48463b1495e4dbdb699fc3d7c34/skills/react-doctor/SKILL.md).

The numeric score is requested from an HTTP service. The request strips
`fileContext` and `fixGroupId`, scrubs paths, and submits diagnostic records
plus optional repository, SHA, framework, and tool metadata. Failures return
an unavailable score. This is not wholly local scoring.
[Request implementation](https://github.com/millionco/react-doctor/blob/922616f08db7d48463b1495e4dbdb699fc3d7c34/packages/core/src/request-score.ts).

The evaluation harness documents a pinned 2,000-repository corpus and explicit
handling of incomplete runs; it excludes previously measured slow/incomplete
repositories. That is useful regression infrastructure, with a selection
limit, rather than comparative proof of accuracy.
[Evaluation documentation](https://github.com/millionco/react-doctor/blob/922616f08db7d48463b1495e4dbdb699fc3d7c34/packages/evals/README.md).

**Our assessment.** Stronger fit than a generic checklist for React-specific
diagnostics. Treat it as a specialist complement; inspect network and runtime
version behavior before adopting it for private code or reproducible CI.

### Cloudflare security-audit: strongest security evidence discipline

**Source facts.** The skill separates guidance from full audits, maintains a
coverage ledger, requires a concrete trust-boundary violation, independently
verifies findings, and separates confirmed results from unresolved validation.
It records source ref and scope. Source stays read-only; audit artifacts and
bounded sandbox validation can write. It requires a multi-agent host, Node
validators, its companion files, and an OS-enforced sandbox before executing
target-controlled code. It provides no all-purpose health grade.
[Skill](https://github.com/cloudflare/security-audit-skill/blob/c1c8a8c1471069fb0e188eeaff69b8e8db6564a8/skills/security-audit/SKILL.md).

The repository includes validators and tests for findings and coverage
records. Those establish output-contract checks, not measured vulnerability
recall. Its README's repeated-run discovery observation is a first-party
claim about its own trials, not a head-to-head benchmark.
[Repository documentation](https://github.com/cloudflare/security-audit-skill/blob/c1c8a8c1471069fb0e188eeaff69b8e8db6564a8/README.md),
[Findings validator tests](https://github.com/cloudflare/security-audit-skill/blob/c1c8a8c1471069fb0e188eeaff69b8e8db6564a8/skills/security-audit/validate-findings.test.cjs).

**Our assessment.** Best specialist here when the central question is whether
a suspected vulnerability is real and adequately evidenced. It has greater
orchestration cost and execution requirements than a daily health dashboard.
Its uncertainty and coverage reporting are useful design lessons for any
general audit skill.

## Synthesis for Captain's Kit

Keep distinct purposes distinct: an operational health dashboard runs existing
checks; a debt audit explains design problems; structural tooling measures
specific proxies; security and React tools deepen selected domains. Prefer
one small manually invoked audit entrypoint with optional adapters over a
universal score assembled from unrelated measurements. Before adopting an
upstream skill, exercise missing tools, failed commands, partial coverage,
false-positive rejection, and repeated runs on a representative repository.
These are proposed maintenance criteria, not completed validations here.

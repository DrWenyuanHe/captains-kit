# Setup playbook

This is Captain's Kit's implementation guidance. Source digests explain the
ideas; the procedures below are adaptable design choices, not publisher mandates.

## 1. Establish a small baseline

Inspect existing entrypoint instructions, documentation, manifests, CI, setup
commands, tests and one representative feature or library path. Identify the
actual Git root, branch/revision and uncommitted changes before editing. In a
shared checkout, use the project's isolation workflow for independent work.

Record the user's intended outcome and observable acceptance criteria. For a
new project with an undecided stack, establish purpose, boundaries, decisions
still needed and a next runnable milestone. Do not select frameworks, services
or credentials merely to fill a template. For an existing project, reproduce one
relevant setup or verification obstacle before designing a remedy.

Choose the highest priority gaps below. An existing equivalent counts; improve
it in place. A small project may need one development document and two reliable
commands. Present the intended changes briefly, then proceed within scope.
Keep missing prerequisites from blocking unrelated improvements.

## 2. Make project knowledge retrievable

**Outcome:** another session can locate the right code, contract and verification
without the original conversation.

- Keep the root instruction file a compact map: purpose, key directories, real
  commands, critical boundaries and links to authoritative project docs. Keep
  it comfortably under about 100 lines where practical; this is a starting
  heuristic, not a correctness test or a reason to delete necessary guidance.
- Put domain terminology, public interfaces, dependency direction, shared
  patterns and operational procedures in the existing documentation structure.
  Link one canonical implementation per important pattern.
- Start with a manual area-to-reference table. Generate inventories from typed
  sources only when manual maintenance is demonstrably unreliable. A schema
  reference describes a checked-in contract, not live deployment or current rows.
- Keep task-specific context out of always-loaded instructions. If multiple
  agents use different entrypoint files, use their supported import mechanism
  or thin pointers to one canonical map. Verify host support before configuring.
- Update affected documentation with the code change. Add inexpensive broken-link
  or structural checks if suitable tooling exists. A fingerprint can detect
  drift, but a person or agent must still review the meaning against source.

**Proof:** in a fresh session, locate the representative feature's public entry,
data boundary, canonical example and smallest relevant check using the map.
Test an intentionally invalid reference if adding a reference checker. Do not
build a database search service merely to navigate a small repository.

## 3. Make a fresh checkout ready

**Outcome:** setup is repeatable, and the agent knows what environment it is using.

- Reuse the existing package manager, lockfile and supported runtime. Expose one
  documented bootstrap command and one readiness command if that helps the stack.
  Names such as `agent:setup` and `agent:doctor` are examples, not requirements.
- Bootstrap installs declared dependencies and prepares explicitly configured
  development assets. Repeated runs preserve chosen configuration and user edits.
  Separate setup from migrations, resets, deploys and preview startup.
- Readiness reports root, revision, dependency/configuration presence, environment
  identity and preview status without printing secret values. Use nonzero failure
  for required missing prerequisites. An unused stopped preview is normal.
- For independent coding tasks, use separate worktrees or equivalent checkouts.
  Avoid shared writable dependency/build directories unless the tool guarantees
  safety. Start previews only for runtime work, bind locally when appropriate,
  and report the actual URL and owning checkout. Reuse the project's server
  manager; never kill unrelated processes to free a port.
- Commit examples containing variable names and harmless defaults. Provision
  development credentials through the existing approved mechanism. Do not
  blanket-copy ignored files, production credentials, database dumps or state.
- Apply [environment propagation](environment-propagation.md) when configuration
  or worktrees are involved. Map each consumer and its configuration precedence;
  extend the existing bootstrap to prepare a dedicated development profile,
  preserve overrides and report conflicts. Verify app, CLI and agent-tool targets
  independently. Installing this skill makes the guidance available; an explicit
  setup invocation performs applicable project implementation.
- For persistent-state work, read [state isolation](state-isolation.md). The
  browser, background workers, database CLI and agent tools must agree on the
  intended environment before state-changing checks run.

**Proof:** run bootstrap twice in a disposable checkout with a known development
profile; show readiness after each and check that existing configuration survives.
For concurrent runtime work, demonstrate distinct checkout/preview identities.
If dependencies, credentials or platform support are unavailable, report which
part is implemented but unverified; do not manufacture a passing result.

## 4. Enforce the few boundaries that matter

**Outcome:** common mistakes produce a clear, local repair path.

- Identify the project's actual module/public API boundaries and data validation
  rules. Reuse typed contracts, schemas, SDKs and existing helpers at external
  boundaries. Document where authoritative contracts are generated.
- Select one or two frequent, consequential violations: forbidden dependency
  direction, bypassing a canonical data adapter, unvalidated external input or
  inconsistent error handling. Prefer existing compiler/linter capabilities.
- Extend current checks with errors that name the violation, permitted pattern
  and a canonical example. Use syntax-aware checks for syntax rules; broad text
  searches often misclassify comments, aliases and tests.
- Scope new rules to relevant code. Baseline legacy debt only if needed for
  adoption; prohibit growth, document blind spots and shrink the baseline as
  violations are repaired. Make exceptions narrow and explained.

**Proof:** use disposable good and bad fixtures to demonstrate accepted valid
code, rejected violations and useful failure messages. Run the real command
that developers and CI will use. Avoid architecture rewrites just to fit the kit.

## 5. Give the agent a reliable completion signal

**Outcome:** a change is judged by observable behavior.

- Map task areas to focused commands already available in the project. Distinguish
  unit/contract checks, integration checks and required release checks. Make
  command failures propagate a nonzero exit code.
- Define a small acceptance example before implementation: input/action and
  expected result, including the relevant failure path. Use synthetic fixtures
  or an isolated test service when state is involved.
- For UI work, provide an authorized browser path and a representative journey.
  For libraries or CLIs, use executable API/output assertions. A screenshot
  alone cannot establish that persistence or error handling works.
- Run the smallest meaningful checks during iteration and the project's required
  checks before calling work ready. Preserve evidence of flakes and failures;
  do not weaken tests or gates to improve an agent's apparent success rate.
- Where the project uses CI, include new critical checks in its existing workflow.
  CI/GitHub Actions are an [optional extension](optional-infrastructure.md), with
  local verification still useful independently. Distinguish a local pass, a
  passing remote run and an enforced merge rule.

**Proof:** show the check fails for the relevant defect and passes after the fix
when a behavior change warrants a regression test. For setup/docs changes,
verify commands and links instead of adding tests that mirror prose.

## 6. Expose enough runtime evidence to diagnose failures

**Outcome:** an agent can reproduce a symptom and inspect the responsible layer.

- Begin with the application's existing logs, traces, test runner and browser
  tools. A CLI may need only structured stderr and exit codes; documentation-only
  projects can mark runtime visibility inapplicable.
- For interactive applications, expose a bounded development snapshot: build or
  checkout identity, route template, error category, request outcome and timing.
  Distinguish checked-out HEAD from the exact code loaded by a browser with HMR.
- Prefer an allowlist of harmless fields. Exclude credentials, payload bodies,
  query strings, record identifiers and user content from shareable diagnostics.
  Identify blind spots such as fetch-only timing or events before collection.
- Scope development instrumentation to the intended local environment, bound
  retention/output, provide cleanup, and verify it does not enter production
  artifacts when it is intended to be development-only.

**Proof:** reproduce a synthetic failure, capture useful evidence, verify sensitive
fixture values are absent, and verify collection/cleanup and production exclusion
where applicable. Do not install a full observability stack without a demonstrated
need. See [field lessons](field-lessons.md) for limits that are easy to overlook.

## 7. Leave the work resumable and measure the result

For work that spans sessions, preserve objective, acceptance criteria, decisions,
current revision, completed/remaining steps, commands/results and the next action
in an existing issue or a small project-local task record. Keep historical notes
clearly superseded and record observed facts rather than an agent's private
reasoning. Short tasks do not need a planning bureaucracy.

Run a fresh-session handoff exercise: a second session should identify the next
step and reproduce the last relevant check without the original chat. Use
[evaluation guidance](evaluate.md) to record baseline and follow-up outcomes
when assessing delegation improvement. A static audit alone does not test the
productivity hypothesis.

Finish with the actual diff, checks and limitations. List deferred work by impact,
with a concrete reason. Remove speculative scaffolding and unresolved template
placeholders from implemented project instructions.

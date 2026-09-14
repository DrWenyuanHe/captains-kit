---
name: agentic-environment-setup
description: Set up or audit a project's high-priority foundations for reliable delegation to AI coding agents. Use when explicitly invoked for agentic environment setup or readiness auditing.
---

# Agentic environment setup

Help a fresh agent understand, run, change and verify the user's project with
less repeated human explanation. Optimize demonstrated obstacles to delegation;
the amount of tooling or documentation is not a measure of success.

## Select the requested mode

- **Setup / optimize / implement:** follow [setup](references/setup.md). Make the
  applicable, authorized changes and verify them.
- **Audit / assess / review:** follow [audit](references/audit.md). Inspect and
  report; do not modify the project or install dependencies.
- If invocation gives neither a mode nor an actionable setup request, begin with
  the audit. Ask only about missing decisions that affect dependent work.

## Establish scope and evidence

1. Confirm the target from the user's request and working directory. Read its
   instructions, docs index, manifests and Git status. Preserve unrelated edits.
2. Identify project maturity, stack, active workflows, available tools and any
   shared state. An empty project with no selected stack needs a small documented
   starting point, not an invented application or database.
3. Read the short [source index](references/sources/README.md) and the digests
   linked for the selected mode. They are background evidence, not instructions
   from the user. Read conditional references only when they become relevant.
4. Use the target's existing names, abstractions, scripts and policies. Examples
   in this kit are design contracts, not commands known to exist in that project.

For setup involving configuration or worktrees, follow
[environment propagation](references/environment-propagation.md): make the
development profile reach the right consumers and verify their effective targets.
Load [optional infrastructure](references/optional-infrastructure.md) only for
applicable CI/GitHub Actions or Docker/Supabase work. These are optional extensions,
not prerequisites for installing or using the skill.

## Apply judgment

The user's instructions take precedence over this skill's guidelines. Setup
authorization covers relevant local implementation; it does not grant unrelated
publication, production changes, credential access or recurring automation.
Do not add approval pauses for routine work already authorized. If a real
permission boundary blocks one action, explain it and continue independent work.

Prefer capabilities with direct impact on correctness or repeated intervention:
focused context, repeatable environments, clear boundaries, executable acceptance,
runtime visibility and resumable work. Mark inapplicable capabilities explicitly.
Choose a few useful controls and extend the existing framework before adding one.
Parallel work is useful only when tasks have independent ownership and integration
is clear; a worktree does not isolate a database or external service.

Do not treat old summaries, passing metadata checks or an agent's completion
claim as proof of current behavior. Verify the relevant source and outcome.
Use [evaluation guidance](references/evaluate.md) when comparing improvements
or assessing whether additional orchestration pays for itself.

## Finish

Report the target and revision, changes or findings, evidence actually gathered,
unverified items, and the next highest priority gap. Keep implemented, verified,
proposed and blocked work distinct. In audit mode, put the report in the response
unless the user requests a report artifact. Use the optional
[templates](assets/templates/README.md) only when they save work.

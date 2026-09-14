# Test the delegation hypothesis

This is a small project experiment, not a universal readiness score. Read the
[evals digest](sources/anthropic-agent-evals.md) and, when considering extra
orchestration, the [harness-design digest](sources/anthropic-harness-design.md).

## Start with observable tasks

Select a few representative tasks from actual work: for example a reproducible
bug, a bounded feature and a change across a real data boundary. Use the same
task in isolated baseline/candidate copies where feasible. Freeze acceptance
criteria, expected behaviors, fixtures and the time/tool budget before the run.
Keep evaluation assertions outside the agent's editable solution when practical;
otherwise inspect changes to them independently.

Record baseline revision, environment/tool configuration, model/version when
available, prompt, permitted actions and verification. Repeat on a candidate
that changes the setup, holding task inputs and resources comparable. Each run
starts a fresh session without the baseline solution in context. Do not perform
real purchases, production changes or external communications for an experiment.

## Record outcomes rather than activity

| Field | Record |
| --- | --- |
| Task and setup | Task ID, code revision, environment, agent/model, prompt and budget |
| Correctness | Acceptance pass/fail, independent review and remaining defects |
| Human effort | Number and nature of interventions; minutes spent assisting/reviewing |
| Completion | Completed, partial, failed or blocked; reason and stopping point |
| Time/resources | Wall time, tool calls and token/cost usage only when actually exposed |
| Failure evidence | Failed commands, environment/tool errors, incorrect assumptions and rework |
| Handoff | Whether a fresh session identified the next step and reproduced the last check |

Use "unavailable" for missing usage data. Token counts are not proxies for
correctness. Keep completion assertions separate from verified output, and
inspect the action record when a result looks suspicious. A second model's
opinion can complement deterministic checks and human judgment; it is not ground
truth by itself.

## Interpret cautiously

Run multiple independent trials for noisy tasks before claiming an improvement.
Report per-task results and sample size, including failures; compare correctness
before speed. Account for model/tool changes, task familiarity, cached setup
and flaky infrastructure. A small favorable sample is suggestive, not causal proof.

Change one costly addition at a time when possible. Keep a change when outcomes
or human effort improve enough to justify its maintenance cost. Simplify or remove
it when no benefit appears. Recheck after a model or workflow change. Do not
install an eval platform or schedule recurring runs unless the project needs them
and the user has requested that ongoing work.

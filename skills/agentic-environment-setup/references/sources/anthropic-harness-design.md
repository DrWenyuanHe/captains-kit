# Harness design for long-running application development

- Publisher: Anthropic
- Source: [Original article](https://www.anthropic.com/engineering/harness-design-long-running-apps)
- Published: 2026-03-24
- Accessed: 2026-09-14 UTC
- Type: Engineering article; not a peer-reviewed paper

## Core ideas

The reported experiments combine planning, implementation, and independent evaluation for demanding application builds. Concrete acceptance criteria make review actionable. An evaluator that interacts with the running application can expose missing behavior that an implementer overlooks.

Separate evaluation can reduce overly generous self-assessment, but it still requires calibration against human judgment. Review execution records and correct recurring evaluator weaknesses; another agent is not automatically a reliable judge.

Durable handoffs and bounded work helped earlier models maintain continuity. Later model improvements allowed some context resets, sprint structure, and frequent evaluations to be removed. Simplifying one component at a time made its contribution easier to assess.

## Limits and application

These selected experiments have model-dependent results, substantial runtime and cost, and remaining defects. They do not establish that larger agent teams consistently outperform simpler workflows under comparable budgets.

For repository setup, prioritize testable requirements and usable feedback. Add specialized review or orchestration only for an observed weakness, and re-evaluate that cost when the model or task changes.

This original offline digest is not full text. The source's planning examples do not authorize expanding user scope.

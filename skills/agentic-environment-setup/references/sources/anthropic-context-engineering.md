# Effective context engineering for AI agents

- Publisher: Anthropic
- Source: [Original article](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Published: 2025-09-29
- Accessed: 2026-09-14 UTC
- Type: Engineering article; not a peer-reviewed paper

## Core ideas

Agent reliability depends on selecting useful information throughout a run, including instructions, tool results, examples, and history. Larger context windows do not eliminate retrieval degradation or irrelevant material.

Keep initial guidance clear and sufficient. Avoid both vague expectations and elaborate conditional rules. Present a few representative examples, and expose tools with distinct purposes, clear parameters, and compact results.

Use paths, links, and other lightweight references to retrieve details when needed. Useful naming and directory structure help the agent discover relevant information incrementally. Some upfront context can coexist with this approach when it saves exploration time.

For extended work, preserve decisions and unresolved issues through carefully evaluated summaries or durable notes. Delegate bounded exploration when separate contexts and condensed findings help the coordinator.

## Limits and application

These observations are not universal performance guarantees. Retrieval takes time; excessive compression loses details. Begin with a navigation map and task-specific references, then adapt to observed failures.

This original offline digest is not a full-text backup.

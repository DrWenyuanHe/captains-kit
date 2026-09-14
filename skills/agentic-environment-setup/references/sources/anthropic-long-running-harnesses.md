# Effective harnesses for long-running agents

- Publisher: Anthropic
- Source: [Original article](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- Published: 2025-11-26
- Accessed: 2026-09-14 UTC
- Type: Engineering article; not a peer-reviewed paper

## Core ideas

Long coding tasks can fail through excessive scope, lost state between sessions, or premature completion claims. Conversation compression alone did not resolve these failures in the reported experiments.

Separate initial environment preparation from ongoing implementation. Establish a reproducible startup command, explicit feature requirements with observable checks, version history, and a concise progress record. Subsequent sessions can use these artifacts to recover their bearings.

Implement a manageable increment, verify its behavior, and leave accurate progress notes and a recoverable working state. Completion should depend on evidence: for web applications, exercise relevant interactions through a running browser instead of trusting code inspection or isolated tests alone.

At resumption, inspect recent history and notes, verify a basic working path, and select the next unfinished requirement. This reduces repeated environment discovery and accidental work on broken foundations.

## Limits and application

The demonstration targets full-stack web development and particular Claude tooling. Its filenames, JSON preference, and single-feature sessions are implementation choices. Browser automation also misses some bugs. Adapt the continuity and verification principles to the project's workflow.

This original offline digest is not an article copy.

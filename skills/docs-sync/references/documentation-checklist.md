# Documentation checklist

Use the rows relevant to the change. Follow the project's actual doc structure;
the filenames below are examples, not required files.

## Map reader needs

For each affected public surface, find its existing documentation and decide what
the change requires:

| Reader need | Look for | Useful check |
| --- | --- | --- |
| Reference | API tables, CLI help, configuration schema, compatibility notes | Names, defaults, types, constraints and removal or migration status match the intended version. |
| How-to | Usage examples, runbooks, task guides | A reader can accomplish the affected task with the documented commands and prerequisites. |
| Tutorial | Getting-started or first-run guide | The change has not broken the newcomer path or hidden a new prerequisite. |
| Explanation | Architecture, design rationale, operational model | Described components and flows still match; preserve rationale unless evidence establishes a change of intent. |

These are coverage lenses, not four mandatory pages per feature. Missing coverage
is actionable when it leaves a reader unable to understand or use the change.
An internal refactor may need no user-facing docs. A removed capability may need
a migration note rather than a new tutorial. Prioritize broken instructions and
undocumented breaking behavior over optional explanatory material.

## Find affected documentation

Search for old and new identifiers, commands, paths and configuration names across
the relevant documentation sources, example files and navigation configuration.
Follow entrypoint links beyond the top-level directory; include formats the project
uses, such as Markdown, MDX, reStructuredText and source-based API docs. Exclude
vendor trees, caches and generated output from authoritative source discovery.
Treat generated output separately when checking the final rendered result.

| Surface | Checks |
| --- | --- |
| README or overview | Capabilities, installation, quickstart examples, limitations and troubleshooting still apply. |
| Contributor guide | Setup prerequisites and changed build/test commands agree with manifests and CI; exercise the affected first-run path when feasible. |
| Project agent instructions | Paths, structure and commands agree with the change. Preserve independent policy and invocation requirements. |
| Reference and examples | Renamed options are updated consistently; defaults, output examples and migration instructions reflect compatibility behavior. |
| Architecture and diagrams | Trace renamed, moved, split or removed components and changed data flows into Mermaid, ASCII and diagram sources. Separate a verifiable rename from a design decision. |
| Navigation and links | New or moved docs are reachable through the project's existing index; relative links and anchors resolve from their actual file location. |
| Release notes | The relevant upcoming entry describes the user-visible change, impact and migration when needed; historical entries retain their original release context. |

Read each affected file before proposing edits. Check cross-document consistency
after individual files: setup and contributor commands, repeated examples, names
in diagrams, and the release/version each page describes. Search hits in historical
notes or explicitly old-version guides are not automatically stale.

## Evidence and limitations

Use implementation, tests, manifests, schema, CLI help and the repository's existing
checks to verify claims. Confirm version-dependent external facts with primary
documentation when necessary. Do not infer that a named option exists from prose
alone or infer a security guarantee from an implementation detail.

Choose checks proportionate to the change: doc build/lint, link checks, example
execution, CLI help, or a safe local walkthrough. Inspect what a command does before
running it. Use local fixtures for examples that normally write data or call services;
deployment and production mutations need their own task authorization. Audit mode
uses an isolated destination for checks that generate files.

Inspect relevant rendered output when navigation or diagrams changed and tooling
is available. Report missing tools and unexecuted examples accurately; distinguish
source inspection from a successful render or end-to-end walkthrough.

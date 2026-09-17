# Working on Captain's Kit

Read [README.md](README.md) first. This repository is a personal skill library.
For skill packaging and maintenance, read [docs/maintaining.md](docs/maintaining.md).
Editing the library does not invoke its skills against other projects.

- Keep skill entrypoints short and independently installable. Mode-specific detail
  belongs in the owning skill's `references/`.
- Preserve each skill's invocation policy. For `agentic-environment-setup`, preserve
  manual invocation, read-only audit and adaptation to existing project conventions.
  For `unslop`, preserve its explicit-only description, HARD GUARD preamble and
  `policy.allow_implicit_invocation: false`. Ordinary writing tasks and references
  from other skills must not activate it. Keep this protection during upstream merges.
- Link source-derived claims and label our synthesis separately. Source digests
  are concise original summaries, not copied articles or universal evidence.
- Publish no private task transcripts, application records, credentials or
  machine-specific configuration.
- Installer changes must preserve existing destinations and stay project-local.
- Every skill needs an entry in `skills-registry.json`. Import external skills with
  `scripts/skills.py import`; register authored skills with `register --local`.
  Record skill edits with `record --note` and keep registry changes with the files.
- Preserve import dates and tracking history. `check-updates` records check results
  without replacing skills; updates use an explicitly reviewed upstream commit.
- Run `python scripts/check.py` and the relevant unittest cases after changes.
  Workflow changes also need a realistic exercise of the affected skill.
- Add guidance for demonstrated workflow needs. Keep target-project configuration
  and generated setup in the target project; this library maintains reusable recipes.

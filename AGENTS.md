# Working on Captain's Kit

Read [README.md](README.md) first. This repository distributes one setup/audit
skill; editing the kit does not invoke it against a different project.

- Keep the skill entrypoint short. Mode-specific detail belongs in `references/`.
- Preserve manual invocation and a read-only audit mode.
- Adapt to existing project conventions; do not prescribe one application stack.
- Link source-derived claims and label our synthesis separately. Source digests
  are concise original summaries, not copied articles or universal evidence.
- Publish no private task transcripts, application records, credentials or
  machine-specific configuration.
- Installer changes must preserve existing destinations and stay project-local.
- Run `python scripts/check.py` and the relevant unittest cases after changes.
  Behavioral changes also need a realistic setup or audit exercise.
- Keep scope on demonstrated delegation obstacles. Avoid accumulating tools,
  boilerplate, scoring systems or mandatory agent orchestration.

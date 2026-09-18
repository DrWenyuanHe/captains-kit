# Source notes

## Source-derived behavior

Reviewed on 2026-09-18:

- [OpenAI: Build skills](https://learn.chatgpt.com/docs/build-skills) documents
  Codex's project and personal discovery roots, symbolic-link support, duplicate
  names, optional UI metadata and the implicit-invocation setting.
- [Anthropic: Extend Claude with skills](https://code.claude.com/docs/en/skills)
  documents personal and project scope, supporting files, symlinked skill folders,
  plugin namespaces and `disable-model-invocation`.
- [Anthropic: Claude directory](https://code.claude.com/docs/en/claude-directory)
  describes the personal configuration directory and `CLAUDE_CONFIG_DIR` override.

These are original summaries of client behavior, not copies of those manuals.
Check current documentation and the installed client before adopting a different
discovery root or relying on a specific discovery API.

## Captain's Kit synthesis

This is a locally authored workflow distilled from installing the same library
for multiple local clients. Staged copies, exact source identification, separate
host hashes, private receipts, backup verification and guarded recovery are this
library's operational conventions; the client manuals do not mandate that format.

The workflow retains existing invocation rules, treats local adaptations as merge
inputs, handles shared targets and package-manager ownership explicitly, and
distinguishes actual client discovery from checking files on disk. A sandbox's
profile mismatch and plugin-qualified display names are diagnostic cases, not
reasons to force global configuration changes.

No private installation receipt, account name, absolute machine path, task
transcript or host configuration is part of this reusable skill. Source links are
maintenance references and do not invoke another skill or installer. Client scope
and publication authority remain those of the user's current request.

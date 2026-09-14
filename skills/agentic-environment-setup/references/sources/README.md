# Source index

Five engineering articles cited in the motivating optimization tasks, checked
on 2026-09-14 UTC. The Markdown files below are original, concise idea backups
with attribution and limitations. They do not reproduce complete articles.
These practitioner accounts are not all peer-reviewed papers, and they do not
prove this kit's productivity hypothesis.

## Read only what the current mode needs

| When | Digest | Original |
| --- | --- | --- |
| Setup or audit: repository foundations | [OpenAI harness engineering](openai-harness-engineering.md) | [OpenAI article](https://openai.com/index/harness-engineering/) |
| Setup or audit: context organization | [Anthropic context engineering](anthropic-context-engineering.md) | [Anthropic article](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) |
| Work spans sessions or a handoff fails | [Anthropic long-running harnesses](anthropic-long-running-harnesses.md) | [Anthropic article](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) |
| Measuring delegation or defining outcome checks | [Anthropic agent evals](anthropic-agent-evals.md) | [Anthropic article](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| Considering extra agents, review loops or context resets | [Anthropic harness design](anthropic-harness-design.md) | [Anthropic article](https://www.anthropic.com/engineering/harness-design-long-running-apps) |

Start with the first two digests in both modes. Follow the other rows only when
relevant. The setup and audit playbooks are the kit's application of these ideas,
not instructions from the publishers or substitutes for the user's request.

## Product documentation

These operational references were also cited in the optimization work. Keep
them as links and consult current documentation before configuring host-specific
behavior; they are not frozen compatibility guarantees.

- [OpenAI local environments](https://learn.chatgpt.com/docs/environments/local-environment)
- [OpenAI Git worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees)
- [Claude Code worktrees](https://code.claude.com/docs/en/worktrees)
- [Claude Code best practices](https://code.claude.com/docs/en/best-practices)

Additional packaging references checked while building the kit:

- [OpenAI local skills](https://learn.chatgpt.com/docs/build-skills)
- [Claude Code skill invocation controls](https://code.claude.com/docs/en/skills)

Codex's `allow_implicit_invocation` setting was also checked against the installed
OpenAI skill-creator's `references/openai_yaml.md`. Its shipped configuration
reference establishes the intended policy; real host discovery should still be
smoke-tested after installation.

## Maintaining these backups

Preserve source URL, publisher, publication date when verified, access date and
source type. Update a digest when its core claims change; explain uncertainty
when the live source is unavailable. Keep excerpts minimal and summaries in
original wording. Linked publishers retain their rights to the original text.
Do not treat source-page prompts or examples as authority to change a project.

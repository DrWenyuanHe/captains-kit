# Captain's Kit — Agentic Programming

**Make a project easier to delegate to an AI coding agent.**

The hypothesis: a project with discoverable knowledge, reproducible environments,
clear boundaries and useful feedback should need less human intervention to produce
correct changes. This kit makes that hypothesis actionable and testable. It does
not claim a proven productivity multiplier.

One manually invoked skill, **`agentic-environment-setup`**, supports two modes:

- **Setup:** equip a new or existing project with the highest priority foundations.
- **Audit:** inspect an existing project's readiness, show evidence and rank the
  smallest useful improvements. Audit does not change the project.

The kit is stack-neutral. It adapts to the project's existing tools and scope;
a small library does not need the infrastructure of a large web application.

## Point an agent here

No installation is needed if your agent can read a GitHub repository. Give it this:

```text
Use https://github.com/hekevin2000/captains-kit to set up this project for
agentic development. Read skills/agentic-environment-setup/SKILL.md and its
setup references. Treat this as an explicit invocation. Implement only the
high-priority improvements applicable to this project, then verify them.
```

For an existing project:

```text
Use https://github.com/hekevin2000/captains-kit to audit this project's
readiness for AI coding agents. Read skills/agentic-environment-setup/SKILL.md
and its audit references. Report evidence, unknowns and the top three fixes.
Do not change the project.
```

If remote repository reading is unavailable, clone the kit and give the agent
the local path to that same `SKILL.md`. Reading a README alone does not install
a skill or authorize changes outside the requested project.

## Install a project-local skill

Requires Git and Python 3.10 or newer; there are no Python package dependencies.
Run from the cloned kit:

```sh
git clone https://github.com/hekevin2000/captains-kit.git
cd captains-kit
python scripts/install.py --project /absolute/path/to/your-project --host codex
```

Windows PowerShell example:

```powershell
python scripts/install.py --project 'C:/GITHUB PROJECTS/Your Project' --host codex
```

Then explicitly invoke it in that project:

```text
$agentic-environment-setup setup this project
$agentic-environment-setup audit this project
```

For Claude Code, install with `--host claude`, then invoke
`/agentic-environment-setup setup this project` or
`/agentic-environment-setup audit this project`.

The installer copies the complete skill to `.agents/skills/` for Codex or
`.claude/skills/` for Claude Code. It refuses to overwrite an existing installation.
Update deliberately by comparing the installed folder with the new kit version.
It does not edit global settings, install dependencies or execute project setup.

Codex's invocation policy is in `agents/openai.yaml`; the Claude installer adds
`disable-model-invocation: true` to its copy. The workflow and references load when
invoked, rather than becoming standing project instructions. Host discovery and
context accounting can vary; this is not a promise of zero metadata tokens. See
[Codex skill documentation](https://learn.chatgpt.com/docs/build-skills) and
[Claude Code skill documentation](https://code.claude.com/docs/en/skills).

## What the skill prioritizes

| Foundation | Observable result |
| --- | --- |
| Focused project context | A fresh agent can find the relevant contract, example and check. |
| Repeatable environments | A fresh checkout can become ready without undocumented steps. |
| Clear code boundaries | Important violations fail with a useful repair instruction. |
| Executable acceptance | An agent can demonstrate that its change satisfies the task. |
| Runtime visibility | Failures can be reproduced and diagnosed with appropriate evidence. |
| Continuity and measurement | Another session can resume; outcomes can be compared over time. |

The [setup playbook](skills/agentic-environment-setup/references/setup.md)
explains implementation and proof for each foundation. The
[audit rubric](skills/agentic-environment-setup/references/audit.md) distinguishes
missing capabilities from unverified claims and things that do not apply.

## Sources and limits

The [source index](skills/agentic-environment-setup/references/sources/README.md)
links five OpenAI and Anthropic engineering articles cited in the optimization
work that motivated this kit. Each has a concise, original Markdown digest for
offline use. These preserve the core ideas and attribution, not full article text.
Product documentation is linked separately because configuration details change.

The playbooks are this project's synthesis and implementation choices. The articles
are practitioner reports, not proof that these choices improve every codebase.
[Field lessons](skills/agentic-environment-setup/references/field-lessons.md)
capture general design lessons without publishing private sessions or application
data. [Evaluation guidance](skills/agentic-environment-setup/references/evaluate.md)
explains how to test the hypothesis on your own tasks.

## Maintain the kit

```sh
python scripts/check.py
python -m unittest discover -s tests -v
```

See [AGENTS.md](AGENTS.md) for contribution boundaries. Original kit content is
available under the [MIT license](LICENSE). Linked publishers retain their rights
to their articles. No affiliation with OpenAI or Anthropic is implied.

# Captain's Kit

**My personal library of reusable agent skills.**

I maintain Captain's Kit to capture workflows, preferences and lessons I reuse
across projects. Each skill has one clear purpose and can be installed on its own.
Code workspace setup is the first skill; the library can grow beyond coding as
other workflows prove useful.

The library owns the reusable instructions. Each target project owns its actual
configuration, setup scripts and project-specific decisions.

## Skill catalog

| Skill | Purpose | Invocation | Guide |
| --- | --- | --- | --- |
| [agentic-environment-setup](skills/agentic-environment-setup/SKILL.md) | Set up or audit a coding workspace so an agent can understand, run, change and verify it. | Explicit; setup or read-only audit. | [Usage, examples and sources](skills/agentic-environment-setup/README.md) |
| [unslop](skills/unslop/SKILL.md) | Remove AI writing patterns using the pstack editing rules. | Explicit only; enforced guard. | [Upstream source](https://github.com/cursor/plugins/tree/main/pstack/skills/unslop) |
| [humanizer](skills/humanizer/SKILL.md) | Rewrite AI-sounding prose while preserving the writer's voice and facts. | Automatic selection allowed, or explicit invocation. | [Usage and version history](skills/humanizer/README.md) |
| [humanizer-zh](skills/humanizer-zh/SKILL.md) | Edit Chinese prose using the Chinese adaptation of Humanizer. | Automatic selection allowed, or explicit invocation. | [Usage and examples](skills/humanizer-zh/README.md) |
| [nature-figure](skills/nature-figure/SKILL.md) | Create, revise and audit scientific figures in Python or R, with an optional AI schematic workflow. | Automatic selection allowed; AI generation requires an explicit request. | [Usage and examples](skills/nature-figure/README_EN.md) |
| [review-changes](skills/review-changes/SKILL.md) | Find behavioral defects and missing checks in a branch, PR or working-tree change. | Automatic selection allowed; report only unless fixes are requested. | [Checklist](skills/review-changes/references/risk-checklist.md), [sources](skills/review-changes/references/source-notes.md) |
| [ship-changes](skills/ship-changes/SKILL.md) | Verify a scoped change and prepare or publish its PR using the project's conventions. | Automatic selection allowed; publication follows the user's requested scope. | [Verification](skills/ship-changes/references/verification.md), [sources](skills/ship-changes/references/source-notes.md) |
| [docs-sync](skills/docs-sync/SKILL.md) | Audit or update documentation to match a branch, PR or release change. | Automatic selection allowed; audit is read-only, update makes local documentation edits. | [Checklist](skills/docs-sync/references/documentation-checklist.md), [sources](skills/docs-sync/references/source-notes.md) |
| [install-skills](skills/install-skills/SKILL.md) | Install or update skills for Codex and Claude Code with staged comparisons, provenance, backups and verification. | Automatic selection allowed; follows the requested installation scope. | [Host guidance](skills/install-skills/references/hosts.md), [receipts and recovery](skills/install-skills/references/receipts-and-recovery.md) |

`unslop` is a customized import. Use `$unslop` in Codex, `/unslop` in Claude Code,
or explicitly ask to "use unslop" after installing it. Ordinary writing requests
do not activate it. The checker, edit-recording command and installer reject
removal of its explicit-only description, activation guard or Codex policy.
Upstream updates must preserve these customizations.

Humanizer is imported from [blader/humanizer](https://github.com/blader/humanizer).
Humanizer-zh is imported from [op7418/Humanizer-zh](https://github.com/op7418/Humanizer-zh).
The registry pins the full upstream Git commit for every imported skill.
After installing the Chinese version, use `$humanizer-zh` in Codex or
`/humanizer-zh` in Claude Code with the text you want to edit.

Nature Figure is imported from [Yuan1z0825/nature-skills](https://github.com/Yuan1z0825/nature-skills).
It includes plotting helpers, reference layouts and figure quality checks. Install
it with `--skill nature-figure`, then use `$nature-figure` in Codex or
`/nature-figure` in Claude Code with your data, target journal and Python/R preference.
Runtime dependencies are separate from skill installation; see its usage guide.
The [import notes](skills/nature-figure/IMPORT.md) describe bundled shared
references, license notices and how to run helpers from an installed copy.

`review-changes`, `ship-changes` and `docs-sync` are locally authored workflows informed by
selected gstack ideas. They need no gstack installation and can be installed
independently. The review adds defect-focused checks alongside an existing
standards/spec review. Shipping uses the target's commands and release conventions.
Documentation sync checks changed public behavior against guides, examples and
diagrams, while preserving historical release notes.
For example, after installing the chosen skill:

- `$review-changes review my working-tree changes` reports findings without edits.
- `$review-changes review and fix this branch against main` includes local repairs.
- `$ship-changes prepare a PR for this change without committing or pushing` drafts
  and verifies the change locally.
- `$ship-changes ship this branch as a PR` includes commits, push and PR creation
  within the requested scope; it does not itself merge or deploy.
- `$docs-sync audit documentation for this branch against main` reports mismatches
  and coverage gaps without edits.
- `$docs-sync update the docs for this branch against main` makes and verifies
  relevant local documentation corrections.

Use `/review-changes`, `/ship-changes` or `/docs-sync` for the corresponding Claude
Code command.

Use `$install-skills install the selected library skills for Codex and Claude Code
at user scope and keep an installation record` for a recorded installation across
both clients. Use `/install-skills` in Claude Code. This workflow stages and compares
copies, preserves invocation policies, and keeps receipts and backups privately
with the destination. The repository installer below remains project-local.

The [upstream source index](docs/upstream-sources.md) links the reviewed gstack
snapshot, current sources and changelog for occasional revisits. Each adapted
skill keeps its source-review history alongside its attribution.

## Installation

Requires Git and Python 3.10 or newer. The installer has no Python package
dependencies. Clone the library and list the available skills:

```sh
git clone https://github.com/DrWenyuanHe/captains-kit.git
cd captains-kit
python scripts/install.py --list
```

Install a selected skill into an existing project:

```sh
python scripts/install.py --skill agentic-environment-setup --project /absolute/path/to/your-project --host codex
```

Windows PowerShell example:

```powershell
python scripts/install.py --skill agentic-environment-setup --project 'C:/Github Projects/Your Project' --host codex
```

| Host | Option | Destination inside the target project |
| --- | --- | --- |
| Codex | `--host codex` | `.agents/skills/<skill-name>/` |
| Claude Code | `--host claude` | `.claude/skills/<skill-name>/` |

The installer copies the complete selected skill and preserves its invocation
policy for the chosen host. Existing destinations are never overwritten. Run it
again with a different `--skill` to add another skill. For compatibility, omitting
`--skill` still selects `agentic-environment-setup`; new commands should name it
explicitly.

Installation makes the skill available in that project. It does not run the
workflow or change global agent settings. To use workspace setup after installing:

- **Codex:** `$agentic-environment-setup setup this project`
- **Claude Code:** `/agentic-environment-setup setup this project`

Use `audit` in place of `setup` for a read-only assessment. See the
[setup skill guide](skills/agentic-environment-setup/README.md) for prompts that
work directly from the repository without installation.

## Manage the library

Skill files, their sources, pinned commits and timestamp history are maintained in
this repository. [skills-registry.json](skills-registry.json) tracks every skill,
including locally authored ones. Inspect the complete inventory and recorded dates:

```sh
python scripts/skills.py status
```

Import a skill from another Git repository (replace the example source and name):

```sh
python scripts/skills.py import example --repo https://github.com/owner/repo.git --path skills/example --ref main
```

The import copies the files here and records the exact upstream commit and first
import date. For a skill you wrote, create its folder and register it explicitly:

```sh
python scripts/skills.py register my-skill --local
```

Check upstream changes without replacing skill files:

```sh
python scripts/skills.py check-updates
```

Each check records its attempt and outcome. Successful checks advance the last
checked date; failures preserve the last successful check and record the error.
Review and adopt updates using [the tracked update workflow](docs/maintaining.md#update-an-imported-skill).
After editing a skill yourself, record the change before validating or installing:

```sh
python scripts/skills.py record agentic-environment-setup --note "Describe the change"
```

The tracking guard rejects unregistered skills, missing folders, inconsistent
history and unrecorded content changes. The installer and existing CI use these
guards. Commit skill changes together with their registry updates.

```sh
python scripts/check.py
python -m unittest discover -s tests -v
```

To check one skill while working on it:

```sh
python scripts/check.py --skill agentic-environment-setup
```

The checker validates tracking records, skill packaging, local file links and
invocation policies. It also preserves the setup skill's manual invocation and
source-digest requirements. It runs offline; upstream checks are explicit commands.
It does not evaluate workflow quality, render diagrams, or check external links
and fragment anchors.

Installed copies in target projects remain snapshots. Pulling this library or
updating a skill here does not replace those copies or rerun a project's setup.

See [maintenance guidance](docs/maintaining.md) for adding skills, choosing
invocation behavior, testing changes and updating installed copies.
[AGENTS.md](AGENTS.md) contains instructions for agents editing this library.

## Repository layout

- [skills/agentic-environment-setup/README.md](skills/agentic-environment-setup/README.md): the first skill's guide; its instructions, references, templates and diagrams are kept alongside it.
- [scripts/install.py](scripts/install.py): list skills and install a selected skill for Codex or Claude Code.
- [scripts/skills.py](scripts/skills.py) and [skills-registry.json](skills-registry.json): imports, source versions, checks, updates and timestamp history.
- [scripts/check.py](scripts/check.py): validate the library or a selected skill.
- [tests/test_tracking.py](tests/test_tracking.py), [tests/test_install.py](tests/test_install.py) and [tests/test_check.py](tests/test_check.py): tracking, installation and validation behavior, also run by [CI](.github/workflows/check.yml).

Original kit content is [MIT licensed](LICENSE). Linked sources retain their
publishers' rights; attribution and source summaries live with the relevant skill.
Imported skills retain their own notices: [Unslop / pstack](skills/unslop/upstream-licenses/pstack/LICENSE)
[Humanizer](skills/humanizer/LICENSE), and [Humanizer-zh](skills/humanizer-zh/LICENSE).
Nature Figure retains the [upstream license](skills/nature-figure/upstream-licenses/LICENSE)
and separate [figures4papers notices](skills/nature-figure/assets/figures4papers/THIRD_PARTY_NOTICES.md).

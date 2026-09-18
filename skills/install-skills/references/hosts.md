# Resolve hosts and stage complete copies

## Host locations

Verify supported paths against the installed client and its current documentation
when changing scope or migrating older installations. Resolve paths from the
consumer's account; an agent's execution sandbox can have a different home.

| Client | Project scope | Personal scope |
| --- | --- | --- |
| Codex | `.agents/skills/<name>/` within the target project | `~/.agents/skills/<name>/` |
| Claude Code | `.claude/skills/<name>/` within the target project | `~/.claude/skills/<name>/` |

The [Codex documentation](https://learn.chatgpt.com/docs/build-skills) describes
repository and user discovery, supported symlinked directories and duplicate skill
names. Older environments or installer helpers may use `~/.codex/skills`; inspect
existing copies and the client's discovery results before choosing a destination.
A `CODEX_HOME` setting can affect configuration and legacy skill locations; do not
assume it changes the documented `~/.agents/skills` root.

The [Claude skill documentation](https://code.claude.com/docs/en/skills) describes
personal scope across local projects, symlink discovery and frontmatter controls.
The [Claude directory guide](https://code.claude.com/docs/en/claude-directory)
documents `CLAUDE_CONFIG_DIR`; account for it when resolving personal paths.
Local personal installation does not install skills in remote or cloud runtimes.

Choose the existing compatible location when that satisfies the request. A
migration to another discovery root needs a reviewed plan for the old copy and
its owner. After a migration, verify there is one intended active installation per
client, while retaining backups outside discovery roots. Do not change `.system`
skills or unrelated client settings as part of a library installation.

## Stage a Captain's Kit selection

These commands apply when the source is a Captain's Kit checkout. Locate the
checkout through the user's context; an installed copy of this skill does not
include the library's installer. For another source, inspect its own interface.

1. Run `python scripts/install.py --list` and `python scripts/check.py` from the
   selected checkout. Confirm the requested skills have valid registry entries.
2. Create an empty staging project in the task's permitted temporary storage.
   Use an absolute path; the installer requires an existing project directory.
3. For each selected skill and host, run the following pattern, substituting the
   resolved source checkout, staging project and skill name:

   ```text
   python /source/kit/scripts/install.py --skill example --project /absolute/staging-project --host codex
   python /source/kit/scripts/install.py --skill example --project /absolute/staging-project --host claude
   ```

4. Inspect the resulting `.agents/skills/` and `.claude/skills/` copies. The kit
   adds `disable-model-invocation: true` to Claude copies only when Codex's
   `policy.allow_implicit_invocation` is false. Preserve the other files and policies.
5. For a new project installation, the installer can target that project directly.
   For an existing destination, it deliberately refuses replacement; compare the
   staged copy and preserve local adaptations before making an authorized update.
6. For personal installation, promote reviewed staged copies separately into the
   resolved personal roots. Do not pass a home directory as the project, alter the
   installer to bypass its guard, or present promotion as built-in global support.

Prefer the source commit's bytes for a committed installation. If selected files
come from the working tree, record that fact and their exact hashes. Never label
uncommitted content as the clean base commit. Preserve snapshot semantics when
using Git archives, including committed attributes and required companion files.

## Discover and validate

Use a client's existing skill listing or documented read-only discovery interface.
If an interface is unfamiliar, inspect its local help or current protocol first.
Do not start a model task or invoke the installed skill merely to check its name.

Codex's app-server may expose `skills/list`; use the installed version's schema and
force a reload when supported. A query launched in a sandbox can see that sandbox's
profile instead of the user's. Resolve this environment mismatch before treating
an empty result as an installation failure.

A skill bundled with plugin metadata can appear under a qualified display name.
Confirm the returned path points to the intended installation and that it is
enabled. Do not rename files or strip packaging metadata to force a short name.
For a client without a usable listing, report structural verification and the
remaining discovery check, with appropriate session refresh instructions.

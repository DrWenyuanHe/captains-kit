# Maintain Captain's Kit

## Add a skill

Add a skill when a recurring workflow benefits from instructions you can maintain
and exercise on real tasks. Keep each skill independently usable.

1. Create `skills/<skill-name>/SKILL.md`. Use 1-63 lowercase letters, digits and
   single hyphens for the folder name, with no leading or trailing hyphen.
2. Give it YAML frontmatter with a matching `name` and a nonempty `description`
   explaining what it does and when to use it. Single-line values and indented
   YAML literal (`|`) or folded (`>`) description blocks are supported. These are the kit's
   supported metadata conventions; the checker is not a general YAML validator.
3. Keep the entrypoint focused. Add `references/`, `assets/` or `scripts/` only
   when the workflow needs them. Local links must stay inside that skill's folder
   so a selected installation is complete on its own.
4. Choose invocation behavior as described below. Add a catalog row to the root
   [README](../README.md) with its purpose and any useful usage guide.
5. Register your own skill with `python scripts/skills.py register <skill-name> --local`.
   Import third-party skills with the command below so their upstream provenance
   is recorded. Copying a folder alone leaves it unregistered and fails validation.
6. Run `python scripts/check.py --skill <skill-name>` and exercise a representative
   request. Verify the resulting behavior, including whether the skill is selected
   for the intended task. Then run the library checks before committing.

Templates may contain fill-in markers. Operational instructions in `SKILL.md`
and `references/` must be complete. Attribute borrowed material and distinguish
your own conclusions from source claims. Keep private records and machine-specific
configuration in their owning projects, outside this library.

## Import a skill

```sh
python scripts/skills.py import example --repo https://github.com/owner/repo.git --path skills/example --ref main
```

Replace the example values. `--path .` imports a skill at the repository root.
The destination name must match its `SKILL.md` name. Choose the branch or tag to
follow with `--ref`; the exact resolved commit is recorded separately.

Imports copy committed files into `skills/<name>/` and add an entry to the
repository's [registry](../skills-registry.json). Common license and notice files
from the repository root and each parent folder are retained under the skill's
`upstream-licenses/` folder, preserving their paths. Review any additional
attribution required by the source. Imports
must pass the kit's packaging checks; a failed import leaves no partial skill or
tracking entry. Existing skills are never overwritten by import.

`nature-figure` also bundles four required files from upstream `nature-shared`
under its own `references/nature-shared/`. The importer includes their untouched
bytes in the source checksum, then rewrites the figure's shared-reference paths
and records that adaptation as a separate edit. A change to any bundled reference
therefore appears in `check-updates` and `diff`. Preserve these standalone paths
when reviewing an update with `--accept-merged`. If upstream introduces another
shared dependency, review and extend `NATURE_FIGURE_SHARED` in `scripts/skills.py`.

Some upstream skills carry Claude's `disable-model-invocation` in `SKILL.md`.
When their `agents/openai.yaml` policy already states the same behavior, import
removes the flag and records the adaptation as a separate edit; the Claude
installer adds it back. A mismatched or missing policy stops the import for review.
Later updates of such a skill go through `update --accept-merged`.

The `unslop` import applies this library's explicit-only guard before installing
the candidate. Its untouched upstream checksum remains the source baseline, and
the adaptation gets a separate edit event. Upstream checks compare raw snapshots;
every candidate entering the library must pass the local policy guards.

Use HTTPS or SSH Git URLs and your existing Git authentication. Portable paths
such as `./vendor/source-repo` are also supported for Git repositories stored here.
Skill management never writes a global skill lockfile or depends on a separate
package manager. Temporary Git downloads and the operation lock live in the ignored
`.captains-kit-cache/` folder inside this repository. Imported scripts are not run.
Downloads request a filtered Git snapshot so unrelated repository assets are not
needed; Git servers without filter support may send a complete snapshot. Archives
retain the original commit context and committed ancestor and skill-local
`.gitattributes`, including `export-ignore` and `export-subst` behavior.

## Tracking dates and guards

Every skill has a content checksum and history in `skills-registry.json`:

| Field | Meaning |
| --- | --- |
| `added_at` | When tracking began in this library. For a pre-existing local skill, this is its registration date. |
| `first_imported_at` | The first successful import from upstream; null for locally authored skills. |
| `last_check_attempt_at` | Most recent explicit upstream check, including failures. |
| `last_checked_at` | Most recent successful upstream check. |
| `last_updated_at` | Most recent recorded content change or adopted upstream merge, after the initial import/registration. |

Dates are UTC. Null means the event has not occurred; local skills have no upstream
checks. Import establishes a snapshot; the first later `check-updates` or `diff`
records its update-check date. No-op edits or updates leave the update date alone.
The original import date stays fixed, and the event history records subsequent
checks, edits, upstream updates and accepted manual merges.

```sh
python scripts/skills.py status
python scripts/skills.py status --json
python scripts/skills.py check-updates
python scripts/skills.py check-updates example
```

`status` is offline and reports the last observed upstream state with its date.
`check-updates` downloads the tracked source and writes check metadata, while
preserving skill files. It compares the skill and retained license files; unrelated
repository commits do not count as skill updates. Unavailable sources produce an
error result, not a current status. One failure does not prevent checking other
selected skills.

Validation and installation reject missing tracking records, orphaned records,
malformed history and checksums that no longer match the skill files. Existing CI
checks these guards on pushed commits and pull requests. These are command and CI
guards, not a filesystem watcher: adding a folder manually requires registration
or import before it passes.

## Record your own edits

After changing a local or imported skill, run:

```sh
python scripts/skills.py record example --note "Explain the change"
```

This records the new content checksum and update date. For an imported skill, it
preserves the original upstream base so your customizations remain identifiable.
Commit the skill files and registry change together. Keep files in the repository's
LF line-ending format so content checksums remain portable between checkouts.

## Update an imported skill

First inspect the changes:

```sh
python scripts/skills.py diff example
```

`diff` records a fresh check and prints the complete upstream commit ID. It compares
your current files to that candidate. Adopt the reviewed commit explicitly:

```sh
python scripts/skills.py update example --commit <full-commit-from-diff>
```

The command requires the commit from the latest successful check. It fetches that
exact snapshot, verifies its checksum, replaces an uncustomized skill, and records
the update. A newer upstream commit that appears after your review is not adopted.

If the skill has local customizations, update stops before replacing them. Merge
the desired changes into your local files, then explicitly record that completed
merge against the reviewed commit:

```sh
python scripts/skills.py update example --commit <full-commit-from-diff> --accept-merged --note "Describe the merge and retained customizations"
```

`--accept-merged` is your assertion that the desired changes have been integrated.
It preserves your files and advances the recorded upstream base; it does not perform
or prove a semantic merge. Run the skill's relevant checks before recording it.

File replacement rolls back if writing the registry fails. Operations use an
exclusive lock to prevent concurrent imports, checks or updates. If a process is
forcibly interrupted, inspect the operation directory and lock before recovery.

## Revisit sources of locally authored skills

Use the [upstream source index](upstream-sources.md) for occasional checks of gstack
and other design sources. It links the original reviewed snapshots, current source
directories and per-skill review history. These skills retain `origin: local`;
the imported-skill `check-updates` and `update` commands do not track or replace them.
Follow the index's review procedure and record source-note edits with the skill.

## Choose invocation behavior per skill

Omitting `agents/openai.yaml`, or omitting its invocation setting, allows automatic
selection in Codex. For a skill intended for explicit invocation, use this
block in `agents/openai.yaml`:

```yaml
policy:
  allow_implicit_invocation: false
```

Use `true` to allow automatic selection explicitly. The kit accepts a single
block-style `policy` with a two-space-indented, unquoted boolean setting.
UI metadata and tool dependencies may live alongside it. See the
[official skill documentation](https://learn.chatgpt.com/docs/build-skills).

Keep `disable-model-invocation` out of shared `SKILL.md` frontmatter. When installing
for Claude Code, the installer adds `disable-model-invocation: true` to the copied
entrypoint only if that skill disables implicit invocation. The source is preserved.
Other host-specific UI metadata and dependencies are not translated.

The workspace setup skill retains its explicit invocation and read-only audit
mode. Its five source digests are part of its existing contract. These requirements
apply to that skill; other skills can use their own invocation policy and sources.

`unslop` also has a protected explicit-only contract. Its description and opening
HARD GUARD block are defined in [scripts/install.py](../scripts/install.py), and
its Codex policy must be false. The checker and installer enforce all three;
`record` and `update --accept-merged` run the same packaging guard. A normal update
refuses to overwrite these local customizations. Merge upstream editing rules
while preserving the guard, then record the reviewed merge. The shared source
omits the Claude-only frontmatter flag; a Claude installation adds it.

Humanizer keeps its upstream invocation behavior, including automatic selection.

## Validate changes

```sh
python scripts/check.py
python -m unittest discover -s tests -v
```

The full check includes root documentation and every direct skill folder. A
selected check covers that skill's files and tracking entry, and validates the
registry schema. Installer tests exercise complete
copies, independent selection, policy translation and preservation of existing
destinations. For workflow changes, also exercise the relevant skill on a realistic
task; packaging checks cannot establish that the instructions work well.
Tracking tests use a disposable Git repository to exercise imports, updates,
timestamps, failed checks, local customizations and rollback.

The setup guide's Mermaid sources and SVGs live together in its `assets/diagrams/`
folder. Keep each pair consistent when changing a diagram.

## Update an installed copy

Make maintained changes in this repository. The installer deliberately refuses to
overwrite any existing destination, including an empty one.

To adopt a newer version, install the selected skill into a temporary project,
compare it with the installed copy in the real project, and merge the changes you
want. Preserve local adaptations and use the target project's version history to
review and recover changes. Fetching or pulling this library never updates copies
automatically, and changing a skill does not rerun its workflow in target projects.

For a recorded installation across clients, the locally authored
[install-skills workflow](../skills/install-skills/SKILL.md) provides staging,
comparison, private receipts and recovery guidance. It uses the project installer
for staged host-specific copies and handles authorized personal installation as a
separate promotion step. Keep machine-specific receipts, backup paths and ownership
records outside the published library. This guidance does not add a global mode
or replacement behavior to `scripts/install.py`.

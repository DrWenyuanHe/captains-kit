---
name: install-skills
description: Install or update agent skills from a repository or local library for Codex and Claude Code with source tracking, backups and verification. Use for project or personal skill installation, including recorded installation across named agent clients. Not for installing application packages or running the installed workflows.
---

# Install skills with a record

Make the requested skills discoverable in the requested clients and preserve an
auditable path back to the previous installation. Use an existing installer or
package manager where it supports the requested scope; this skill supplies the
staging, provenance and recovery workflow around it.

## Establish the installation

1. Resolve the selected skills, source, target clients and project or personal
   scope from the request and prior context. "All skills" means the selected
   library; "all agents" means the named clients in context. Inventory other
   installed clients if the request includes them, rather than inventing targets.
   Ask only for a choice that cannot be resolved and materially affects placement.
2. Read the source's maintenance instructions and installer interface. Inventory
   existing destinations, links, invocation policies and ownership records. Resolve
   the actual consumer's user profile, configuration-directory overrides and
   project boundaries using [host guidance](references/hosts.md).
3. Pin a repository snapshot to its full commit, or identify an intentionally
   selected working-tree snapshot by its base commit and content hashes. Preserve
   local modifications; do not commit, pull or publish merely to obtain a clean
   source. Record upstream provenance separately from the library's version.

An installation request authorizes that installation. Honor existing authorization
without repeating permission questions. A plan or audit request remains read-only.
Publication, enabling unrelated plugins, changing agent prompts and running an
installed workflow are separate actions. Loading this skill does not authorize them.

## Stage and compare

- Validate source packaging and tracking before touching active installations.
  Stage complete skill folders, including references, scripts, assets and licenses,
  using the source's installer when available. Keep the candidate fixed through
  review and installation. For Captain's Kit, follow the commands in
  [host guidance](references/hosts.md); its installer stays project-local.
- Preserve each skill's invocation behavior. For Claude, translate explicit-only
  policy into supported frontmatter in the installed copy; keep the shared source
  unchanged. Automatic discovery of this installer never enables automatic use of
  a protected skill such as Unslop. Do not preload explicit-only skills into agents.
- Compare staged files with existing copies and any previous receipt. Classify
  new, unchanged, outdated and locally modified installations. Preserve local
  adaptations in the reviewed candidate; a backup alone is not a merge. Ask about
  unresolved behavioral conflicts, continuing independent installations when safe.
- Inspect links before following them. Retain a compatible, understood junction or
  symlink instead of copying through it blindly. Account for shared targets when
  ordering changes. Do not create duplicate active copies of the same skill to
  work around an existing destination or a refusal to overwrite it.

## Apply and verify

Read [receipts and recovery](references/receipts-and-recovery.md) before writing.
Create or extend a private installation record, verify the current destinations
still match the inspected state, and apply the staged changes within the authorized
scope. Back up replaced copies outside skill-discovery roots. Journal completed
steps so an interruption can be inspected and recovered without guessing.

Use the package manager's supported update procedure when it owns the destination.
If ownership must change, preserve the old record and reconcile only the affected
entry using a verified format. Do not silently leave two managers claiming the
same installation or replace another manager's whole lockfile with a new schema.

Verify installed file hashes, local references, translated policies and backup
integrity. Check client discovery when a supported read-only interface is available;
match the resolved path as well as the display name, which may be plugin-qualified.
Distinguish filesystem validation from an actual client-discovery check. Treat a
sandbox or service account's inventory as belonging to that account, not the user.

Finish the receipt with results and recovery instructions. Report installed skills,
destinations, source version, record location and any incomplete checks. Explain
client refresh requirements based on its current behavior. Runtime dependencies
are separate from skill files; install them only within the requested scope.

For the provenance of this workflow, see [source notes](references/source-notes.md).

# Receipts, updates and recovery

This is Captain's Kit's installation-record convention, not a cross-client
package-manager standard. Reuse an existing manager's records when they carry the
needed evidence; supplement gaps instead of creating competing ownership records.

## Store a private record

Choose a durable, timestamped record directory owned by the destination project or
user, outside active skill-discovery roots. An ignored project directory or a
dedicated user-local installation-record directory is suitable. Temporary staging
is not the sole durable record or backup. Do not publish machine-specific paths,
application records, credentials, private source files or task transcripts in a
reusable skill library.

Record only installation-relevant files and metadata. A useful layout is:

```text
installation-record/
  receipt.json
  validation.json
  recovery.md
  backups/
  source-registry.json
```

`source-registry.json` is optional when the source has no registry. If the exact
source is an uncommitted or unavailable snapshot, retain its selected skill files
in the private record as well. Omit directories that have no contents to preserve.

## Receipt contents

Use versioned, machine-readable JSON. Preserve previous receipts; later updates
create a new receipt referencing the previous one rather than rewriting history.

| Field or group | Record |
| --- | --- |
| Identity | Schema version, operation ID, UTC preparation/start/completion times, operation status. |
| Scope | Requested skill selection, clients, project or personal scope, and resolved destination roots. |
| Source | Credential-free source URL or local origin, selected ref, full resolved commit, and any working-tree deviations. Keep a library commit distinct from upstream skill commits. |
| Provenance | Source skill paths, source content hashes, available registry history and retained licenses. |
| Per installation | Skill name, host, absolute destination, link kind and target if applicable, action, before-state, backup location, candidate and installed hashes, host adaptations, invocation policy and step status. |
| Ownership | Existing manager, preserved metadata and any reviewed transfer; record unrelated entries that must be retained. |
| Validation | Checks run and their outcomes, discovery names and paths, errors, skipped checks and limitations. |
| Recovery | Exact original and backup locations, restoration order, and guards against overwriting later changes. |

For content integrity, store a map of skill-relative POSIX paths to SHA-256 hashes
of raw file bytes. Compare both the file set and hashes so missing and extra files
are detected. Record any deliberately excluded generated caches explicitly. Hash
source and installed copies separately: a host-specific frontmatter change should
change the installed hash without pretending that upstream changed.

If recording an aggregate hash, specify its algorithm and serialization or use the
source registry's existing algorithm. Retain per-file hashes for diagnosis. A hash
proves equality to the recorded bytes, not that the instructions are correct or
that a client has discovered them.

## Apply without losing changes

1. Inspect all selected destinations and stage the complete candidate before
   changing active files. For an unchanged installation, retain it and record the
   verification instead of manufacturing a reinstall.
2. Inspect symlink and junction targets explicitly. Do not recursively copy or
   remove unknown links. Before moving any directory, check its resolved absolute
   path is inside the approved source or destination root. Scope every operation
   to a named skill, never an entire personal skills root.
3. Recheck destination and manager-record hashes immediately before mutation. A
   change since inspection requires a new comparison; elapsed time is not consent
   to discard concurrent work. Avoid overlapping installation writers.
4. Prepare the receipt and pending copies, then back up each replaced destination.
   Keep backups outside discovery and verify them before promoting new copies.
   Prefer a same-filesystem rename for promoting a fully
   validated pending directory when supported; do not claim the whole multi-host
   operation is atomic. Leave the receipt incomplete until every selected step is
   accounted for.
5. Persist step status after each move or write. On interruption, inspect actual
   paths and hashes as well as the journal: a process can stop between a rename and
   its recorded completion. Do not blindly retry the whole installation.
6. Verify installed files and policies against the reviewed candidate. Verify
   backups, retained shared links and any owner-record change. Confirm discovery
   when supported, and mark completion only for the scope that succeeded.

A package manager's lockfile is not a generic installation ledger. Prefer its
supported commands for changes. If a transfer requires a manual edit, first verify
the format and ownership semantics, back up the exact original, and preserve every
unrelated field and entry. Do not fabricate its folder hash or reuse another
manager's schema for this receipt. If reconciliation is unresolved, leave the
affected installation untouched and report the conflict.

## Roll back or update

Before rollback, compare the active installation with the receipt. Preserve later
edits separately and resolve conflicts rather than restoring over them. Move the
current copy out of discovery, restore the recorded backup to the exact original
path, then validate its hashes. For a fresh installation with no predecessor,
moving it out of discovery is the rollback.

For a root migration, retire the new copy before restoring the legacy location so
both are not active. Restore shared targets and links in their recorded dependency
order. Restore manager metadata wholesale only if it still matches the recorded
post-install state; otherwise merge only the affected entry and preserve newer
changes. Keep a rollback event and its verification results with the receipt.

For updates, compare installed content with its last receipt and a newly staged,
reviewed source snapshot. Pulling a library does not itself update installed copies.
Do not rerun installed workflows or replace runtime environments as a side effect.

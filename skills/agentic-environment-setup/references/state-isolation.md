# Persistent-state work: conditional checklist

Read this only when setup or audit involves a database, queue, storage system or
another mutable service. These are practical implementation lessons, not a
requirement that every project adopt local containers or a particular provider.

Use [environment propagation](environment-propagation.md) to prepare and verify
the selected profile. Load [optional infrastructure](optional-infrastructure.md)
for applicable CI, container or Supabase implementation details.

## Identify the whole environment

Map the application's configuration, database CLI, migrations, workers/functions,
object storage and agent tool connections to explicit development targets.
Configuration presence alone does not establish that they agree. Never infer
"staging" or "production" solely from a hostname. Prefer the existing environment
registry or an explicit, non-secret identity check.

A worktree isolates source files and its index. A separate frontend port does
not isolate rows, users, storage or deployed functions. A copied environment file
can silently keep the original backend. Report frontend and backend isolation
separately.

## Choose the smallest supported isolation

Use the project's supported disposable local backend, dedicated development
instance or appropriately isolated test namespace. Document its limits: a schema
namespace may still share authentication, extensions or external services. If
the platform cannot isolate a required dependency, mark the limitation rather
than asserting full isolation. Do not provision paid services without scope.

Specify ownership, unique identifiers/ports, initialization, synthetic fixtures
and cleanup. A reset command must verify the actual disposable target and its
ownership before mutation, fail closed for an unknown/shared target, and affect
only that target. Test those guard cases with harmless fixtures. Keep production
and customer data out of seed datasets and diagnostic outputs.

## Verify both creation and evolution

For database-changing tasks, use disposable state to test:

1. Replaying the required migrations into a fresh supported database.
2. Upgrading the current base schema to the candidate schema.
3. Relevant contract, authorization and application behavior after migration.

Use the repository's actual migration conventions and required checks. A migration
ledger comparison shows recorded history, not full schema parity or application
compatibility. Record unavailable prerequisites explicitly. Setup itself should
not automatically reset state or apply migrations merely because dependencies
were installed.

During audit, inspect these controls and existing evidence only; preserve the
read-only boundary described in [audit.md](audit.md).

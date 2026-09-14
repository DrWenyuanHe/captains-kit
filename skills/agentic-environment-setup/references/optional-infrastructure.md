# Optional infrastructure: CI, containers and Supabase

Read only the sections that match the project. These are implementation choices
for reliable verification and isolated state, not prerequisites for using agents.
This playbook is Captain's Kit synthesis; product details link to official docs.
During audit, inspect configuration and existing evidence without starting jobs,
containers or database mutations. Record unavailable evidence as **Unverified**.

## CI / GitHub Actions

**Use when:** the project needs repeatable checks on proposed changes, especially
with multiple contributors or agents. Extend an adequate existing CI provider.

1. Inventory the local commands that establish correctness. Put the fast,
   deterministic checks in one documented entrypoint; invoke it locally and in
   CI. Avoid maintaining two subtly different definitions of success.
2. Add a workflow for the project's review events. Record the tested commit and,
   for comparison-based checks, the base revision. A pull request event can test
   a merge ref; do not label its result as a head-only test. Include merge queue
   events when the project's queue requires them. Consult GitHub's
   [event semantics](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).
3. Separate expensive backend or browser jobs from fast checks. Run them when
   their contracts can be affected, including changes to shared configuration,
   dependencies, migrations and test harnesses. If relevance cannot be determined
   reliably, run the job. A skipped backend job is not evidence that it passed.
4. Set timeouts, retain concise failure evidence and name the local reproduction
   command. Cache reproducible dependencies by the lockfile and runtime; never
   cache credentials or mutable test state as a shortcut to correctness.
5. Use minimum token permissions, normally `contents: read` for verification.
   Pin external actions to verified full commit SHAs. Run untrusted pull request
   code without deployment secrets or privileged workflow credentials. Keep
   deployment a separate, explicitly authorized workflow. See GitHub's
   [secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use).

**Illustrative check split:**

| Check | Evidence it should produce |
| --- | --- |
| Fast verification | Formatting/types/boundaries and focused tests at the tested revision. |
| Backend verification, when applicable | Migration replay, upgrade and access checks against disposable state. |
| Browser verification, when applicable | A synthetic user journey and sanitized failure diagnostics. |

**Acceptance:** exercise the workflow with a valid change and a known failing
fixture. Confirm the failure blocks the intended gate, provides a local repair
path and belongs to the expected revision. Verify required-check behavior for
skipped jobs and rerun after the candidate or comparison base changes. A workflow
file, a successful remote run and an enforced merge rule are three separate
claims. Inspect each before reporting a required gate as active. Reuse an artifact
only when its producing revision, inputs and successful checks are known.

## Docker / container-backed development

**Use when:** the application depends on services that benefit from reproducible
local instances. A small stateless tool may need no containers at all.

1. Reuse the project's container lifecycle. Record supported runtime versions,
   prerequisites, expected resources and a bounded readiness check. For Compose,
   use a stable unique project name per owned environment; Docker documents how
   [project names scope environments](https://docs.docker.com/compose/how-tos/project-name/).
2. Map actual containers, host ports, networks, volumes, bind mounts and external
   services to an owner. Inspect explicitly named or external volumes: distinct
   project names cannot make deliberately shared storage independent.
3. Propagate the selected development profile to every consumer that needs it.
   Confirm effective targets after precedence rules, inherited shell variables
   and process restarts. Report resource identity and configuration presence,
   never credential values. Follow [environment propagation](environment-propagation.md)
   and the [state-isolation checklist](state-isolation.md).
4. Provide lifecycle commands for ensure, status and cleanup. Repeated ensure
   should preserve valid state. Before resetting or removing resources, verify
   the current owner and exact target; refuse unknown or shared resources.
5. Start only the services the task needs. Bound concurrent instances by measured
   capacity. Reuse an owned instance only while its schema/profile and exclusive
   ownership remain valid; queued tasks are preferable to silently sharing rows.

**Acceptance:** prepare the same environment twice, exercise a synthetic write,
and prove it cannot affect a second task's state. Confirm cleanup removes only
the tested environment. Document restart costs and external dependencies still
shared with other tasks; a healthy container alone does not prove isolation.

## Supabase database, Auth and Storage

**Use when:** the project already uses Supabase and needs writable verification.
The local stack requires the CLI and a Docker-compatible container runtime; a
documented disposable hosted environment can be an alternative. Consult the
[local-development guide](https://supabase.com/docs/guides/local-development).

1. Discover the existing lifecycle and installed CLI commands before adapting
   them. Check the [changelog](https://supabase.com/changelog) for relevant changes.
   Keep local development distinct from operating a production self-hosted stack.
2. Define one owned backend profile. Confirm the frontend, server, migration CLI,
   Edge Functions, Storage clients and agent/MCP tools all resolve to that intended
   environment. Different host/container URLs may identify the same backend;
   compare identity, not just URL strings. Verify agent/MCP targets independently:
   preparing a local backend does not retarget a global connector. Leave tools
   with unverified or unsupported targets disconnected from writable tasks.
3. Derive local configuration from the owned environment, with synthetic accounts
   and fixtures. Do not copy primary or production environment files into task
   checkouts. Keep secret and service-role keys server-side; expose only the key
   type permitted for public clients. See [API keys](https://supabase.com/docs/guides/getting-started/api-keys).
4. Inspect migration and seed history before replay: historical data inserts can
   introduce real records even into a fresh database. Use the project's documented
   synthetic-data preparation and isolation rules; do not rewrite shared history
   merely to create a test fixture. For migration changes, verify two paths:
   replay the candidate history into fresh disposable state, then upgrade a
   separate disposable copy of the recorded base with representative synthetic
   data. Record base/candidate revisions and outcomes separately. When the schema
   changes, generate consumer types from the intended schema and check them for
   drift. See [migration guidance](https://supabase.com/docs/guides/deployment/database-migrations).
5. Test the application's access paths: allowed reads/writes, forbidden access
   and relevant cross-user boundaries. Use realistic synthetic sessions, not
   only privileged database queries. Verify grants and RLS separately because
   they control object access and row access respectively. See
   [Data API security](https://supabase.com/docs/guides/api/securing-your-api) and
   [database testing](https://supabase.com/docs/guides/database/testing).
6. Verify relevant Auth redirects, Storage buckets and Edge Functions as well as
   tables. Stub or disable outbound email, payments and production integrations
   in the test profile. Record any feature that remains untested locally.
7. Apply the container ownership and cleanup rules above. Reset only disposable
   state after checking its identity. Do not repair shared migration history or
   deploy migrations as an incidental part of environment setup.

**Acceptance:** a fresh task can identify its backend, authenticate as a synthetic
user, complete a relevant application journey and prove an unauthorized action
is rejected. For migration changes, both migration paths pass. Concurrent tasks
stay unaffected by one another's writes and cleanup.
Missing Docker, CLI support or required services are explicit limitations;
local success does not attest to a production deployment.

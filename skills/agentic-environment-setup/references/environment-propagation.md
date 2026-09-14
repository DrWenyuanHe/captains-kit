# Environment propagation

Use this part of setup when a project needs development configuration, worktrees
or more than one execution surface. The goal is for a new checkout to acquire the
configuration it needs through a repeatable, documented process. A project with
no environment-dependent behavior can mark this step not applicable.

This is the kit's implementation guidance. It is installed with the skill and
applied during an explicit **setup** invocation. Installing the skill alone does
not read credentials, copy environment files or configure the target project.

## 1. Map the sources and consumers

Inspect the existing bootstrap, example configuration and configuration loaders.
Record variable names, purpose, source, intended consumer and precedence; avoid
reading or displaying credential values when names and configuration structure
are sufficient. Identify each consumer's effective target rather than assuming
that every process inherits the agent terminal's environment.

An illustrative map for an application using a development backend:

| Consumer | Configuration it needs | Propagation and verification |
| --- | --- | --- |
| Browser application | Public endpoint and explicitly public client configuration | Build/dev-server configuration; inspect effective development target |
| Server or local functions | Server endpoint and required server-only credentials | Approved local secret mechanism; verify target and credential presence without values |
| Migration/test CLI | Disposable database connection | Explicit task profile; verify database identity and ownership before writes |
| Agent database tools / MCP | Independently configured tool connection | Inspect tool target separately; changing app configuration does not retarget it |
| CI job, if used | Job-specific test configuration | CI's own environment/secret mechanism; never depend on a workstation file |

Document which surfaces do not exist. A local hostname alone does not establish
ownership: two tasks can use different ports, databases or service instances.

## 2. Implement the smallest repeatable path

- Keep the setup recipe and harmless configuration examples in version control
  so new tasks receive them from their starting commit. Existing worktrees need
  that tooling revision before they can use it.
- Extend the project's current bootstrap. If host lifecycle hooks or environment
  setup actions are useful, route them to that same command, using the active
  checkout directory. Verify the installed host's supported behavior before
  adding a hook; keep an explicit command as a fallback.
- Use a dedicated development profile from an approved source, or generate it
  from an owned disposable service. Propagate only the required allowlisted
  fields/files. Do not treat the primary checkout's ignored `.env` as a safe
  template: it may point to production or contain privileged credentials.
- Keep server credentials out of browser configuration, tracked files, command
  output and diagnostics. Copying skill files is not permission to export secrets.
- Define precedence for process variables, ignored files and explicit overrides
  according to the actual framework. Preserve user-owned configuration on repeat
  setup. If an override selects a conflicting target, report the conflict and
  require an explicit supported selection before writable checks; never silently
  replace it or fall back to a shared backend.
- Keep runtime files and mutable state owned by their checkout/task. Do not copy
  dependency trees, database volumes, caches containing credentials or process
  identity files. Reuse services only when their isolation contract is adequate.
- Separate dependency preparation, profile activation and service startup when
  the project benefits from it. Docs and unit-test work should not require a
  running database. Restart/reload affected consumers when their configuration
  loader requires it, then check the configuration they actually use.

## 3. Make readiness useful

Report checkout identity, configuration source labels, required-field presence,
non-secret service identity and target agreement. Distinguish static configuration
inspection from a live connection/ownership check. Do not call the whole environment
ready just because dependency installation succeeded.

Example output from a fictional target project's readiness command:

```text
Dependencies: ready
Profile: task-local development (existing overrides preserved)
App target: orders-task-a
CLI target: orders-task-a
Agent database tool: target mismatch
Writable integration checks: blocked until targets agree
```

Use the target's actual commands and identities. Avoid credentials, full database
connection strings or sensitive hostnames in shareable output.

## 4. Verify propagation, not just file creation

During authorized setup, exercise a disposable checkout with a synthetic profile:

1. Prepare it twice; dependencies/configuration remain usable and chosen overrides
   survive. A missing required field produces an actionable readiness failure.
2. Confirm browser, server, CLI and any agent tools resolve the intended service,
   or explicitly report each unverified/unavailable surface.
3. Introduce a harmless conflicting test target. Confirm the writable test path
   refuses to proceed; restoring the correct target restores readiness.
4. For concurrent stateful tasks, prove their targets and test data are separate.
   See [state isolation](state-isolation.md) and the conditional
   [Docker/Supabase guidance](optional-infrastructure.md).
5. Check generated files stay untracked and diagnostic output omits synthetic
   secret sentinel values. Record the tested revision and actual limits.

During **audit**, inspect the implementation and existing evidence only; follow
the [read-only audit procedure](audit.md). Do not provision a profile or start a
service to turn an unverified finding into a pass.

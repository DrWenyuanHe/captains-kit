# Verification

## Choose the checks

Read the repository's commands from its instructions, scripts and CI. Run the
required validation plus focused checks for changed behavior. Use existing tools
and test conventions. If instructions and executable configuration disagree,
investigate and report the mismatch; do not invent a familiar test command.

For a behavioral defect, add a regression test when practical. For an uncovered
scenario, explain the input and expected outcome before deciding whether a test
adds useful evidence. Avoid bootstrapping a test framework during shipping unless
the user requests it. Report unavailable checks as gaps, not passing results.
Use measured coverage when the project requires it; never substitute an estimated
percentage or a quality score for tool output.

## Bind evidence to the content

For each required check retain its command, execution directory, exit status and
tested snapshot. Use an existing evidence mechanism if available; otherwise a
short local record of the commit plus scoped working-tree diff and relevant
untracked content is sufficient. Keep execution records in the target's ignored
scratch area or session, not in a reusable skill package.

Integrating the base, fixing code, adding tests or changing configuration after a
check can invalidate its result. Before publication compare the final content with
the tested content and rerun affected checks. Dependency manifests, lockfiles,
build settings and version files can affect behavior; classify actual changes
rather than exempting entire filenames. Reuse results when content and relevant
environment are unchanged. A green run on unrelated dirty files does not verify
the smaller snapshot being published; isolate the selected change if necessary.

## Resolve failures and gaps

Investigate failed checks and repair regressions within scope before publishing.
To claim a failure is pre-existing, reproduce it on the relevant base in an
isolated checkout with comparable inputs, or provide equally direct evidence.
An untouched test file does not establish that classification. If a baseline
cannot run, label the origin unknown.

Existing failures remain visible. Follow the repository's policy and the user's
existing decision about them. Where neither permits proceeding, report the blocker
after finishing independent work and seek a concrete decision. Do not silently
relax a required check or discard a failing regression test to obtain a green run.

Inspect the final diff and status for generated or unrelated changes. Completion
requires evidence for the intended published snapshot and an explicit account of
any unresolved failure or unavailable check.

# Captain's Kit import notes

Imported from [Yuan1z0825/nature-skills](https://github.com/Yuan1z0825/nature-skills/tree/2375e0abdf42158ef149256f2c64b1f759a0d274/skills/nature-figure)
at commit `2375e0abdf42158ef149256f2c64b1f759a0d274`. The registry preserves the
import date, upstream checksum and subsequent edit history.

## Local packaging adaptations

These are Captain's Kit packaging choices, not upstream scientific guidance:

- Keep the upstream automatic invocation policy and Python/R selection workflow.
- Bundle four references from the same upstream commit under
  `references/nature-shared/`: `core/nature-results-discussion.md`,
  `core/research-compliance.md`, `core/ethics.md` and
  `journal-formats/nature-machine-intelligence.md`.
- Rewrite shared-reference paths in the entrypoint, manifest and two figure
  references so installing only `nature-figure` is sufficient. Modified files
  carry a Captain's Kit notice. The bundled reference contents are unchanged.
- Include all four companion files in the tracked upstream checksum, so upstream
  checks also detect changes to those dependencies.
- Resolve the R alignment auditor beside its sourced helper, preserving its
  location after project-local installation or a working-directory change.
- Measure rendered R panel viewports in device coordinates rather than unresolved
  gtable units, which otherwise produce zero-width rectangles for normal patchwork
  layouts. Keep these local script changes when merging upstream updates.

The [upstream license](upstream-licenses/LICENSE) is Apache-2.0 at this snapshot.
The separate [figures4papers notice](assets/figures4papers/THIRD_PARTY_NOTICES.md)
is retained; those third-party assets are not covered by the repository license.
Older upstream prose mentioning MIT does not replace the retained license files.

## Running the installed skill

Use helper paths relative to the installed skill directory. Upstream examples
beginning with `skills/nature-figure/` assume a source-repository checkout;
substitute the installed directory when using a project-local copy.

Python plotting needs NumPy and Matplotlib. The PDF collision audit additionally
needs the packages in [requirements.txt](requirements.txt). The R workflow has
its own dependencies in [the R guide](references/r-workflow.md). Installing the
skill copies its files; it does not install runtimes or dependencies, save a
backend preference, or call an image-generation API.

Review updates with the library's `diff` command, preserve the bundled paths,
and record a reviewed merge with `update --accept-merged`. Source-derived
scientific guidance remains upstream material; verify current target-journal
requirements when preparing an actual submission.

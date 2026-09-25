# Figures

A figure change reaches the manuscript through a fixed route: frozen source data, a new build
per version, an audited PDF, a legend checked against the numbers, the author's approval, a
tracked picture swap, an index update and, at submission, journal files that meet the journal's
limits. This file covers that route. Drawing the figure belongs to the `nature-figure` skill.

## Who owns what

`nature-figure` is a separate skill, installed on its own. Refer to it by name and run its scripts
from wherever it is installed: the project's `.agents/skills/` or `.claude/skills/` folder first,
then the user-level skills folder. If it is not installed, say so and stop the figure step; never
copy or re-implement its auditors inside the project.

| Concern | Owner |
| --- | --- |
| Figure contract, design, archetype, palette, plotting code, Python/R backend | `nature-figure` |
| Panel-alignment gate, collision audit, glyph audit, source validation | `nature-figure` scripts, called by this skill |
| Figure folders, versions, `FIGURE_INDEX.json`, promotion | this skill |
| Whole-set gate run, evidence folders, WARN log, tile sign-off | this skill |
| Numbers-drawn CSVs and legend verification against data and manuscript text | this skill |
| Variant protocol and the author's pick | this skill (process), `nature-figure` (drawing) |
| Tracked picture swap and legend edits in the .docx | this skill (`msw.py build`) |
| Journal figure rules and exports | this skill, from the [journal profile](journal-profiles.md) |
| Prism extraction and statistics recomputation | this skill |

Pass the journal profile's `figures` block to `nature-figure` as its journal and export contract.
Nature-family journal rules stay in `nature-figure`; other journals come from the profile.

## Where figure files live

```
02_Figures/
  FIGURE_INDEX.json            source of record: versions, file hashes, embed PNGs, releases
  README.md                    written by `msw.py init`; its table is generated from the index
  Shared/
    MANUSCRIPT_FIGURE_CONTRACT.md   journal block, size, fonts, palette, export tiers, budgets
    figure_runtime.txt              pinned plotting packages, e.g. matplotlib==X.Y.Z
  Figure_05_Marker_response/
    START_HERE.md              written by `msw.py figure add`; generated block plus version notes
    FIGURE_CONTRACT.md         nature-figure contract plus the journal constraints
    LEGEND.md                  current and proposed legend, edit table, numbers verified
    source_data/               frozen CSV inputs; README.md gives each file's provenance
    build_figure05_v14_arm_colours.py
    out/v14/                   Figure_05_v14.{svg,pdf,tiff,png}, the 300 dpi embed PNG,
                               numbers-drawn CSVs, alignment JSON
  Journal_Exports/<journal_id>/V31/   journal files, previews and manifest.json
90_Agent_Work/Figure_QA/2026-09-24_Figure_05_v14/   one new folder per gate or review run
```

`figure add` creates the figure folder and `START_HERE.md`; everything else in the tree is made
by the figure build or by hand. Use the same layout and the same legend document name in every
figure folder; mixed layouts made the source project's figures harder to audit.

## Lifecycle

1. **Freeze source data.** Copy inputs into `source_data/` without numerical change and record
   where each came from (file, sheet or table, filter, date). Derived tables are written only if
   every derived row reproduces the locked values; enriched tables are asserted equal to the
   locked summary before rendering. A table that does not reproduce is investigated, not used.
2. **Contract.** Write or update the figure contract with `nature-figure`: core conclusion,
   question, panel roles, statistics, source-data map, integrity notes.
3. **Variants** when the design is open (protocol below).
4. **Build** a new version with a new script (rules below) and record its files with
   `msw.py figure register` (below).
5. **Gate** the exported PDF (below). Failures are fixed only as another new version.
6. **Verify the legend** against the numbers-drawn CSVs and the manuscript text.
7. **Show the author** the rendered figure and the proposed legend. Wait for approval.
8. **Swap** the picture and edit the legend as tracked changes in the next manuscript version.
9. **Promote** the version with `msw.py figure promote` and release the manuscript version that
   shows it with `--figures` (below).
10. **Export** journal files at submission, and re-run the gate on the whole indexed set.

## Build scripts

- One script per version, named for the change, never edited after it has produced outputs. Its
  docstring states what changed from the predecessor and that nothing else did.
- A `VERSION` value drives every output name. Check all output paths before writing and create
  them exclusively; a rerun of the same version is refused.
- Assert the pinned runtime (`figure_runtime.txt`) inside the script. A different plotting
  library version has moved geometry between versions before.
- Fixed canvas at the journal width (for example 180 mm), editable text (`pdf.fonttype` 42,
  `svg.fonttype` none), and no tight bounding box, which changes the physical width.
- Run in-memory checks before any write: expected annotation strings, no figure-level legend or
  methods text, text-to-text clearance, distance from the canvas edge. Then write the figure,
  then the CSVs, so a failed alignment gate leaves nothing behind to block the rerun.
- Do not import an older version's script by path; editing it would silently change every later
  version. Put shared code in a helper that is frozen once used.
- Export SVG, PDF, TIFF and PNG, and the 300 dpi embed PNG, from the same plotting backend.
  Rasterising the PDF with another tool breaks `nature-figure`'s backend rule.
- Write a numbers-drawn CSV per panel group: one row per printed number with `displayed_text`,
  the raw estimate and interval, the displayed P value, `source_file`, `source_filter` and
  `source_columns`, plus rows for axis ticks.

## The figure gate

`msw.py` has no figure gate: run the `nature-figure` auditors directly, into a new folder under
`90_Agent_Work/Figure_QA/`, for one candidate or the whole indexed set. For each figure:

- `audit_figure_collisions.py <pdf> --json-out ... --overlay-pdf ...` and
  `audit_pdf_text.py <pdf> --min-pt 5 --json` from `nature-figure`; the alignment JSON written by
  the build; `validate_figure.py` on the plotting source.
- Honour exit codes. `FIX BEFORE DELIVERY` or exit 1 blocks. `NOT AUDITABLE` or exit 2 also
  blocks: a PDF with text converted to outlines, or a missing PDF library, audits nothing, and a
  gate that only counts FAIL rows lets it through. A crashed auditor is a failed gate.
- `REVIEW REQUIRED`: judge every WARN at final size and record the reason an intentional overlay
  is acceptable in the run's WARN log.
- Near misses: the collision audit fails an overlap from 5% of the smaller text box. A 3.6%
  overlap once passed it and was caught only by eye. Look for near misses in the tiles.
- List glyphs between the 5 pt floor and the house body size (7 pt) for tile review.
- Render the full figure (about 1700 px wide) and zoom tiles (rows and columns about the size in
  mm divided by 62, at least 2 each, at about 420 dpi with a small overlap), for example with
  PyMuPDF or pypdfium2. Open every tile of a changed figure and record that you did. Automated
  audits do not replace this.
- For a partial change, prove nothing else moved: pixel diff of untouched panels against the
  predecessor, derived CSVs byte-identical, page size unchanged.
- Write `gate.json` and `gate.md`: per figure the verdicts, exit codes, tool versions and the
  index hash.

Review items that the auditors cannot see: no data point or confidence bound hidden behind
another (state any horizontal dodge in the legend), each label nearest its own axis, and legend
claims about visual encoding (colours, symbols) true of the rendered figure. Use one reviewer per
figure who opens every tile, a second reader for each unintended finding, and a reviewer who did
not build the figure. Brief reviewers even-handedly; do not tell them to default to refuting.

## Legend verification

`LEGEND.md` has a fixed shape:

1. Header: the figure change, the manuscript version read (accepted view), the verifier and logs.
2. `<!-- CURRENT_LEGEND_START -->` / `END` block with the exact accepted-view legend.
3. `<!-- PROPOSED_LEGEND_START -->` / `END` block.
4. Edit table `# | Location | Old (exact) | New | Reason`; each old span occurs once in the legend
   and once in the document, so it can become a `replace` edit.
5. Text removed from the figure, each item shown as already in the legend or added by an edit.
6. Style decisions (minus sign U+2212, "to" in signed intervals, P-value decimals as printed).
7. Numbers verified: `Quantity | CSV row and filter | Raw value | Printed`.
8. Other manuscript sentences that describe the figure: still matches, or needs a change
   (reported, not edited).

The verifier selects exactly one CSV row per quantity, reproduces the printed form, recomputes
derived identities (a ratio from its parts, a corrected P-value family), checks that every
number in the new spans is verified or a listed structural constant, and always writes a log,
including when it fails. A synthetic row:

```text
legend token   "-12.3% (95% CI -20.1 to -4.0; P=0.004)"
selector       results.csv: outcome=marker_q17, contrast=between_arm, visit=3
raw            ratio 0.8770 (0.7990 to 0.9600), p=0.00412
format         (ratio - 1) x 100, 1 dp, U+2212 minus, CI with "to", P to 3 dp
check          formatted(raw) == legend token -> ok
```

## Variants

When a design is open, draw 3 or 4 genuinely different options from the validated data, at the
size the panel will occupy. Show the rendered images with little prose, number them, recommend
one with a reason, and build only the one the author picks. Record the pick in `START_HERE.md`
and the index history. For a contested panel, add a short judge panel with named lenses (a
journal statistical reviewer, a clinician author, a figure editor) and record the ranking.

## FIGURE_INDEX.json

The `msw.py figure` commands and `release --figures` write this file (as `FIGURE_INDEX.json.new`,
swapped in atomically) and keep keys they do not use; never edit it by hand. A synthetic entry
after one release:

```json
{"schema_version": 2,
 "figures": [
  {"id": "Figure_05", "title": "Marker response", "folder": "02_Figures/Figure_05_Marker_response",
   "current": "v14",
   "versions": [
    {"version": "v14", "date": "2026-09-24", "registered_utc": "2026-09-24T10:02:11Z",
     "pdf": "02_Figures/Figure_05_Marker_response/out/v14/Figure_05_v14.pdf", "sha256": "…",
     "files": [
      {"kind": "pdf", "path": "02_Figures/Figure_05_Marker_response/out/v14/Figure_05_v14.pdf", "sha256": "…", "size": 48211},
      {"kind": "png", "path": "02_Figures/Figure_05_Marker_response/out/v14/Figure_05_v14.png", "sha256": "…", "size": 912344}],
     "script": {"path": "02_Figures/Figure_05_Marker_response/build_figure05_v14_arm_colours.py", "sha256": "…"},
     "qa": "90_Agent_Work/Figure_QA/2026-09-24_Figure_05_v14",
     "embed": {"path": "02_Figures/Figure_05_Marker_response/out/v14/Figure_05_v14_embed300.png",
               "sha256": "…", "size": 402113},
     "note": "arm colours match Figure 4",
     "used_in": ["V31"]}
   ]}
 ]}
```

`current` is the promoted version and `used_in` lists the manuscript versions released with it.
Old versions stay in `versions[]`; nothing is removed. `pdf` and `sha256` repeat the PDF entry of
`files`. Record what the index does not hold (the author's approval, the legend document, the
journal tier) in the version notes of the figure's `START_HERE.md`, outside the generated block.

## Registration and promotion

Every figure command writes a `pending` and then a `complete` ledger event (`figure_add`,
`figure_register`, `figure_promote`); an interrupted one is finished by running it again.

1. `msw.py figure add --id Figure_05 --title "Marker response"`, once per figure, adds the entry
   and creates the folder (default `02_Figures/<id>_<Title_words>/`; `--folder` must stay inside
   `02_Figures/`) and its `START_HERE.md` from the template if they are missing. A figure id is
   never reused. The generated block at the top of `START_HERE.md` and the table in
   `02_Figures/README.md` follow the index; notes outside the markers are kept.
2. After each build, `msw.py figure register --id Figure_05 --version v14 --file <pdf> --file <png>
   [--file <svg> --file <tif>] --script <build script> --qa <QA folder> --embed <embed PNG>
   --note "what changed"` records the SHA-256 of every file where the build wrote it. Nothing is
   copied or moved. It refuses a version that exists (v2 and v02 are the same version), files
   outside the project, in the package or in the archive, a file registered for any other
   version, more than one PDF or PNG among the `--file` values, an `--embed` that is not a PNG,
   and a build script whose bytes changed since an earlier version registered it.
3. When the author has approved the figure and the pass that swaps the picture has passed its
   gate, `msw.py figure promote --id Figure_05 --version v14` re-checks the version's files, sets
   `current` in the index and `figure_set` in `PROJECT_INDEX.json`, refreshes the generated
   blocks, and says whether the current manuscript already shows the embed PNG.
4. Release that pass with `msw.py release plan|apply --pass <pass> --figures Figure_05=v14`, or
   list the figures in the pass's `pass.json` as `"figures_changed": {"Figure_05": "v14"}`. The
   release refuses a figure that is not in the index, a version that is not registered or not
   promoted, one with no registered PDF or PNG, a registered file whose hash changed, and a build
   whose accepted view has no picture with the embed PNG's bytes (a version without an embed PNG
   is not checked). It copies the version's PDF and PNG into `00_CURRENT_DELIVERABLE/Figures/`
   (role `figure` in the package manifest), moves the copies they replace to
   `99_Archive/<date>/Package_Superseded_VNN/Figures/` after the twin check, adds VNN to
   `used_in` and sets `figure_set`. A promoted version whose embed PNG the build shows, but which
   the package does not hold yet, is brought in the same way even when the release does not name
   it, and the plan says so; a picture whose bytes match the promoted versions of two or more
   figures is refused until the release names one. Other figures keep their package copies. It
   warns when the build swaps a picture that no figure in the release accounts for.
5. `msw.py figure status` (read-only; it lists problems but always exits 0) re-hashes every
   registered file and reports, for each promoted version with an embed PNG, whether a picture in
   the current manuscript's accepted view has the same bytes: `MATCH` with the picture's name,
   `NOT SHOWN` (naming the version the manuscript still shows, if any) or `UNREADABLE`. It also
   flags a `figure_set` that disagrees with `current` and a missing `START_HERE.md`. Between
   promotion and the release that swaps the picture, `figure status` and `msw.py verify-project`
   report `NOT SHOWN` as a warning, not a failure (verify-project still passes with exit 0), and
   print the next step: a pass whose `edits.json` has a `swap_picture` with the embed PNG as
   `image`, then a release with `--figures Figure_05=v14`. A registered file whose bytes changed
   is `CHANGED` and fails verify-project.

Check by reading what the commands cannot see: the version's legend document and QA folder are
the ones reviewed, untouched figures are byte-identical to their predecessors, and no contract or
`START_HERE.md` prose still describes an older version (drift is common).

## Tracked picture swap

Swap only after the author approves the figure. Put the swap and the legend edits in one spec for
`msw.py build`; this one sits in the pass folder `90_Agent_Work/passes/V31_<slug>/`:

```json
{"edits": [
  {"id": "F5", "op": "swap_picture", "old_name": "Figure 5 v13", "new_name": "Figure 5 v14",
   "image": "../../../02_Figures/Figure_05_Marker_response/out/v14/Figure_05_v14_embed300.png",
   "media": "media/v31_figure_05_v14.png", "descr": "Figure 5",
   "purpose": "author-approved Figure 5 v14"},
  {"id": "L5", "op": "replace", "para": {"contains": "Figure 5: Marker Q-17"},
   "before": "shown in ", "old": "grey", "new": "blue", "after": " for the training arm",
   "purpose": "legend matches Figure 5 v14"}
]}
```

- `old_name` is the current picture's `docPr` name. `msw.py figure status` prints it for a
  promoted version whose embed PNG the manuscript shows, and `new-manuscript` names each picture
  after its legend heading (`Figure 5`). It must match exactly one picture.
- `image` is the version's registered embed PNG: the 300 dpi PNG of the final canvas from the same
  build. A relative path resolves against the folder of the spec file, not the working folder,
  hence the `../../../` from the pass folder to the project root; an absolute path also works and
  is the safe choice on Windows when the project root is long.
- `media` is the new part's file name; it must not exist in the package yet, ignoring case,
  because package part names are case-insensitive (without it the tool picks `msw_imageN`). `new_name` defaults to
  the old name plus ` (new)`. Omit `width_mm` (or `width_emu`) to keep the current width; the
  height follows the image's aspect ratio.
- The old run is wrapped in `w:del` and keeps its media part; the new run is in `w:ins`.
- The gate's `pictures` check compares the accepted view with the declared swaps and the image
  hash; the reject view must still equal the source. Without the build manifest, `verify` can
  only WARN about a swap.

## Journal exports

Read the tiers and limits from the journal profile's `figures.files` block and write each run to
a new folder. No command registers the exports or places them in the package: the run's
`manifest.json` is their hash record, the package copies are placed by hand, and
`verify-project` lists them as `UNLISTED` without hashing them
([release](release.md#package-contents)).

- Record a tier per figure: line art and graphs (often 1200 dpi), greyscale (600 dpi), colour
  (300 dpi, often CMYK). Exporting everything at the highest tier satisfies each rule but can
  break the upload limit.
- Choose the CMYK profile explicitly (for example FOGRA39 or SWOP) and record the author's choice.
  Converter defaults are a silent choice.
- Set near-black to solid black: pixels with C = M = Y = 0 and K at 200/255 or more become
  K = 255, so dark grey text does not print as a halftone. Count only pixels that changed.
- Verify every claim after export: vector or raster (EPS from transparent plots is a raster),
  colour mode, dpi tags, pixel size against the journal width.
- Enforce budgets as blocking checks: the per-file limit (house default 10 MB when the journal
  gives none) and the combined upload limit summed over every planned file, not only figures.
- Write `manifest.json`: tool versions, dpi, profile, near-black threshold, the effective
  manuscript version, and per file the source PDF hash, output hash, bytes, mode, pixels, size in
  mm, compression, embedded profile and changed-pixel count. Package copies are byte-identical.

## Prism reconstruction

A Prism `.prism` file is a zip: sheets and tables hold the data (`data.csv` per table), data sets
hold group titles and excluded cells, and analyses hold stored settings and results.

1. Data tables are ground truth. Stored analysis results are a cross-check and may be stale: a
   table edited after its analysis last ran will not reproduce it. Recompute every statistic from
   the tables and investigate every mismatch.
2. Record exclusions as data (a flag column or mask), never only in metadata, and report an
   all-recorded sensitivity analysis when exclusions exist.
3. Matching a reported value to the nearest candidate test only suggests its origin. Confirm the
   test from the stored analysis settings before naming it in Methods or a legend.
4. Freeze figure inputs as locked tables with provenance and assert equality on every rebuild.
5. Every printed number maps to one CSV row and selector.

## The author's figure preferences

- Rebuild figures in code with `nature-figure`, better than the originals, in tidy folders.
- Text-light figures: no legend or methods text inside the figure. Sources go in the legend and
  method mechanics in Methods. Short plain in-figure labels where the method must be clear.
- ±SEM error bars; significance brackets only for significant comparisons and nothing for
  non-significant ones; no within-arm P values when the between-arm test is primary.
- No overlapping text or objects. Panel labels visible, panels symmetric and equal in size.
- The rendered look decides, not the code. Show images first, with little prose.
- Scientific content in a figure is checked like text: sequences against the reference database,
  both species shown when both were used, all candidates shown.
- Read the figure itself before changing any panel letter or figure number in the text.
- Picture swaps and legend edits go into a new tracked version only after the author approves.

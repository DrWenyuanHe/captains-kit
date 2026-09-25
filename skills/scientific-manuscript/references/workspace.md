# Project workspace

The folder layout, names, index files and file-safety rules of a manuscript project.
`msw.py init` creates this tree; keep its names, which the author knows. The layout follows the
journal project's reorganized workspace (observed); the slimmer index set and the ledger are this
skill's synthesis, made to remove the redundancy that project accumulated.

## Folder tree

```text
<project>/
  README.md                human entry; its "current" block is generated between msw markers
  AGENTS.md                project policy; CLAUDE.md points to it
  CLAUDE.md
  manuscript.json          configuration
  PROJECT_INDEX.json       the one record of what is current
  .gitignore  .gitattributes
  01_Manuscripts/
    00_CURRENT_DELIVERABLE/  current + baseline, PACKAGE_MANIFEST.json, Change_Notes/, Figures/,
                             Proofs/; by hand: Supplementary/, Tables/, Journal_Figures/
    Versions/VNN/            released files, proofs and author saves of VNN, plus VNN_CHANGES.md
    Incoming/                files as received, never edited
    VERSION_INDEX.json       VERSION_INDEX.md is generated from it
  02_Figures/                FIGURE_INDEX.json, README.md, Shared/, Figure_NN_<Name>/
  03_Data/                   Original_Source/ (byte copies), Derived/
  04_Analysis/               one folder per analysis workstream
  05_References/             EndNote/, Journal_Guidelines/, Evidence/
  06_Project_Docs/           STATUS.md, LEDGER.jsonl, DECISIONS.md, Workflows/
  90_Agent_Work/             passes/VNN_<slug>/, passes/_claims/, QA/, Figure_QA/, Scratch/
  99_Archive/                YYYY-MM-DD/<Reason>/...
```

Older projects also have `90_Agent_Work/render_profiles/`, a LibreOffice profile that `proof` no
longer uses (it now keeps one per-user profile outside every project). It is left in place, stays
ignored by git and is skipped when `status`, `verify-project` and `archive` walk the tree.

| Folder | Holds | Rule |
| --- | --- | --- |
| Root | Navigation and configuration | Start at `README.md` and `PROJECT_INDEX.json`; no working files |
| `00_CURRENT_DELIVERABLE` | The assembled package: current version, its baseline, current change note, proof and released figure files | Agents change it only through `intake`, `author-save` and `release apply`, apart from the submission files placed by hand ([release](release.md#package-contents)); the author may save there in Word (then run `author-save`) |
| `Versions/VNN` | What was released as VNN, its proof, the author's saves of it, its change note | Add files; never change one |
| `Incoming` | Files from the author, co-authors and reviewers | Never edited; `intake --role incoming` registers .docx copies |
| `02_Figures/Figure_NN_*` | Scripts, source data and exports per figure version | A new figure version is new files; `figure register` records them where they are |
| `03_Data/Original_Source` | Source data exactly as received | Never edited; derived tables go to `Derived/` or `04_Analysis/` |
| `05_References` | EndNote library (`.enl` with `.Data`), guidelines, evidence | The author owns the library; agents never write to it |
| `06_Project_Docs` | Status, ledger, decisions, workflows | The ledger is append-only |
| `90_Agent_Work` | Passes, QA, renders, scratch | Machine-made or in progress; one new folder per run |
| `99_Archive` | Everything moved out of view | Move-only, with per-file hashes in the ledger |

Numbered folders `01`-`06` hold the scientific content in dependency order; `90` (agent work) and
`99` (archive) sort last.

## Names

Manuscript files follow the `naming` pattern in `manuscript.json`, by default
`{date} {initials} {venue} V{n} {slug} {state}.docx` with a two-digit `{n}`, for example
`2026-01-10 AB EXJ V07 methods wording tracked verified.docx`:

- the date is the day the pass was claimed or the file registered (`intake` and `author-save`
  take `--date`);
- initials and venue come from `manuscript.json`; the venue changes on a retarget
  (`msw.py retarget`), the version number does not;
- the slug is 2-6 words naming the pass (`pass new --slug`; keep it to a few short words on
  Windows); a project's first file has no slug and the state `baseline`
  (`... V01 baseline.docx`);
- version labels are zero-padded everywhere (`V07`), in folders and file names.

Take names from the tools. Inside a pass folder the names are short, to keep paths clear of
Windows' 260-character limit: `pass new` names the build `VNN tracked.docx` (stored as
`build_name` in `pass.json`) and prints the build and proof commands with it, plus the full name
the released copy will get. `release` computes that name from `pass.json` (claim date, version,
slug) and `manuscript.json` (initials, venue, naming), whatever the build file is called. The
other commands name what they write.

| State | Meaning | Written by |
| --- | --- | --- |
| `baseline` | The project's first file, the author's text unchanged | `intake` (default for the first file) |
| `tracked` | Build output, not yet released (`VNN tracked.docx`) | `build`, in the pass folder |
| `tracked verified` | Passed the gate and released; same bytes as the build | `release apply` |
| `author saved` | The author saved in Word and tracked changes remain | `author-save` (default when revisions remain) |
| `author accepted` | The author accepted every change and saved | `author-save` (default when none remain) |
| `<name> comments` | A supervisor's or co-author's returned tracked copy that the next pass builds on; keeps the version number | `intake --role author_saved --state "<name> comments" --set-current` (records `returned_by`) |
| `received` | A file from someone else | `intake --role incoming` |
| `accepted` | Clean copy with every change accepted | Only on request |

A name that is taken gets a ` 2` suffix for author saves and incoming files; a released name is
never reused.

Other names:

- change notes `VNN_CHANGES.md`; pass folders `90_Agent_Work/passes/VNN_<slug_with_underscores>/`;
  proofs one folder per render, `<pass>/proofs/p1`, `p2`, holding `accepted view.docx`,
  `accepted view.pdf`, `pageNN.png` and `proof.json` (the `proof` defaults); a release copies the
  PDF into `Versions/VNN/` and the package's `Proofs/` as `<released name without .docx> accepted view.pdf`;
- archive folders `99_Archive/YYYY-MM-DD/<Reason>/`: `Package_Superseded_VNN` for package files
  that a release or intake replaces and `Author_Save_VNN` for those an author save replaces (both
  become `_2`, `_3` when a destination is taken), and the `--reason` of `msw.py archive`;
- figure folders `Figure_NN_<Title_Words>`; figure versions `vK` (v2 and v02 are the same version;
  independent of manuscript versions); exports `Figure_NN_<Title>_vK.<pdf|png|svg|tif>`; pictures
  inside the .docx `media/vMM_figure_NN_vK.png`, where MM is the manuscript version that inserted
  it (the `media` key of `swap_picture`);
- revision ids: give each pass its own range so change notes can cite ids. The observed
  convention is `(N + 10) x 10000` for version N (V07 uses the 170000 range), passed as
  `build --id-base`. The base must not be below the source's largest id; without it, `build`
  starts at the next multiple of 10000 above that id.

Never reuse a name or a path for different content. A corrected file gets a new name.

## Configuration

`manuscript.json` holds the settings chosen at bootstrap ([bootstrap](bootstrap.md#1-ask-the-questionnaire)):
`schema` (`msw-project/1`), `title`, `venue`, `document_kind`, `journal_profile`, `author` (`name`, `file_initials`,
`revision_name`), `language` (`spelling`, `first_person`), `reviewer` (`tool`, `model`,
`effort`), `figures_backend`, `naming`, `word_count` (`method`) and `policy.science_changes`.
With `comments` (the default) science questions are Word comments for the author; with
`tracked_for_review` the science changes the author asks for are tracked edits for their review.
Numbers, statistics and directions change only with approval or a recomputation under either
setting. `init` writes the file,
`retarget` changes `venue` and `journal_profile` (keeping the previous file in the archive), and
the project's `AGENTS.md` allows other configuration edits by hand, recorded in `DECISIONS.md`.

## Index files

The `msw.py` project commands maintain these files: they write `<name>.new`, flush it and swap it
in with an atomic replace (retried for a few seconds while another program holds the file open),
all related indexes and generated blocks together. Before writing anything they check every
target for a leftover `<name>.new` from an interrupted run, and `status` lists the leftovers. If
`status` also lists an unfinished release, intake, author save or retarget, run that same command again:
it moves the leftovers into `99_Archive/<date>/Interrupted_Run/` and writes the indexes from its
pending ledger event ([release](release.md#if-it-stops-part-way)). Otherwise `intake`,
`author-save`, `release` (including `plan`), `retarget`, `outstanding` and the figure commands
stop with nothing written: move each leftover away with
`msw.py archive plan|apply --reason Interrupted_Run <file>` and run the command again. While
`PROJECT_INDEX.json`, `VERSION_INDEX.json` and `PACKAGE_MANIFEST.json` disagree about the current
file, `status` shows `INDEXES DISAGREE`, `verify-project` fails and `pass new` refuses. Never
hand-edit PROJECT_INDEX, VERSION_INDEX, FIGURE_INDEX, the package manifest or the ledger. Only
the commands that write them can change them (`retarget` for the venue and target journal); if
one is wrong in any other way, stop and report it to the author. The commands refuse to rewrite a
`PROJECT_INDEX.json` or `FIGURE_INDEX.json` that is not `schema_version` 2. Paths are relative to
the project root. The examples are synthetic, and `aaaa` stands for a full 64-character sha256.

**`PROJECT_INDEX.json`** is the only record of what is current; everything else links to it.
`init` writes it; `author-save`, `release apply` and an `intake` that makes its file current
(the first one, or `--set-current`) set `current` and `baseline`;
`figure promote` and `release --figures` set `figure_set`; `retarget` sets `venue` and
`target_journal`. `msw.py outstanding add --text "..."`
adds an open item (`O1`, `O2`, ... with the current version as `since`), and
`outstanding done --id O1` moves it to `resolved` with the version it was closed in; both rewrite
the README and STATUS blocks and add an `outstanding` ledger line. Items are never deleted. With
no open items the blocks say "Open items: see the latest change note"; the change notes hold the
per-version detail.

```json
{"schema_version": 2, "project": "Example cohort manuscript", "author_initials": "AB", "venue": "EXJ",
 "target_journal": "Example Journal",
 "current": {"version": "V07", "state": "tracked verified",
             "path": "01_Manuscripts/Versions/V07/2026-01-10 AB EXJ V07 methods wording tracked verified.docx",
             "sha256": "aaaa"},
 "baseline": {"version": "V06", "state": "author accepted",
              "path": "01_Manuscripts/Versions/V06/2026-01-08 AB EXJ V06 author accepted.docx", "sha256": "bbbb"},
 "package": "01_Manuscripts/00_CURRENT_DELIVERABLE",
 "figure_set": {"Figure_01": "v2", "Figure_02": "v5"},
 "outstanding": [{"id": "O2", "text": "confirm the sample size in the Figure 2 legend", "since": "V06"}],
 "resolved": [{"id": "O1", "text": "choose the multiplicity method", "since": "V05", "resolved_in": "V06",
               "resolved_utc": "2026-01-08T09:12:40Z"}],
 "updated": "2026-01-10T18:02:11Z",
 "last_release": {"version": "V07", "utc": "2026-01-10T18:02:11Z", "pass": "90_Agent_Work/passes/V07_methods_wording"}}
```

**`01_Manuscripts/VERSION_INDEX.json`** is a list of every registered file. `role` is `released`,
`author_saved`, `incoming` or `current`; the current entry keeps its own role in `base_role`.
Build checkpoints are not listed: the released bytes equal the build. Every entry has `version`,
`date`, `role`, `state`, `slug`, `path`, `sha256` and `source_name`; an intake adds its `note`
and, for a returned copy, `returned_by` (from `--returned-by` or a state such as "JD comments";
`VERSION_INDEX.md` then shows "current (returned by JD)"); a release adds the baseline, change
note and proof with their hashes, the gate status, the revision author and id range and
`released_utc`.

```json
[{"version": "V06", "date": "2026-01-08", "role": "author_saved", "state": "author accepted", "slug": "",
  "path": "01_Manuscripts/Versions/V06/2026-01-08 AB EXJ V06 author accepted.docx", "sha256": "bbbb",
  "source_name": "2026-01-06 AB EXJ V06 tracked verified.docx", "base_role": "author_saved"},
 {"version": "V07", "date": "2026-01-10", "role": "current", "base_role": "released", "state": "tracked verified",
  "slug": "methods wording", "path": "01_Manuscripts/Versions/V07/2026-01-10 AB EXJ V07 methods wording tracked verified.docx",
  "sha256": "aaaa", "baseline": "V06 author accepted", "baseline_sha256": "bbbb",
  "changes": "01_Manuscripts/Versions/V07/V07_CHANGES.md", "changes_sha256": "cccc",
  "proof": "01_Manuscripts/Versions/V07/2026-01-10 AB EXJ V07 methods wording tracked verified accepted view.pdf",
  "proof_sha256": "dddd", "gate": "PASS", "revision_author": "Given Surname", "revision_ids": [170001, 170006],
  "released_utc": "2026-01-10T18:02:11Z"}]
```

**`02_Figures/FIGURE_INDEX.json`** is flat: `{"schema_version": 2, "figures": [...]}`, one entry
per figure (`id`, `title`, `folder`, `current`) with a `versions` list, never a nested chain of
previous versions. The `msw.py figure` commands and `release --figures` write it; the entry
format is in [figures](figures.md#figure_indexjson).

**`00_CURRENT_DELIVERABLE/PACKAGE_MANIFEST.json`** is rewritten whenever the current file changes
(intake of a current file, author save, release): a flat `files` list, every entry hashed. Roles
are `current`, `baseline`, `change_note`, `proof` and `figure`. `verify-project` reports any other
file in the folder, such as a table placed by hand, as `UNLISTED` (a warning) and does not hash
it ([release](release.md#package-contents)).

```json
{"schema_version": 2, "version": "V07", "assembled": "2026-01-10", "files": [
  {"package_path": "2026-01-10 AB EXJ V07 methods wording tracked verified.docx",
   "source_path": "01_Manuscripts/Versions/V07/2026-01-10 AB EXJ V07 methods wording tracked verified.docx",
   "sha256": "aaaa", "size": 1234567, "role": "current", "version": "V07"},
  {"package_path": "Figures/Figure_01_Study_design_v2.pdf",
   "source_path": "02_Figures/Figure_01_Study_design/out/v2/Figure_01_Study_design_v2.pdf",
   "sha256": "eeee", "size": 45678, "role": "figure", "version": "V05",
   "figure": "Figure_01", "figure_version": "v2"}]}
```

**`06_Project_Docs/LEDGER.jsonl`** has one JSON object per line, append-only, with a monotonic
`seq` and a UTC time. Events written: `init`, `claim`, `intake`, `author_save`, `release`,
`move`, `retarget`, `figure_add`, `figure_register`, `figure_promote` and `outstanding`. `init`, `claim` and
`outstanding` (with `action` `add` or `done`) are single lines. The others first write
`"status": "pending"` with the planned copies and moves (for `intake`, `author_save` and
`release` also the index state they will write, `target`), then a `complete` or `failed` event
whose `ref` is the pending `seq`, so an interrupted operation is visible; a re-run that finishes
it adds `"resumed": true`. Each moved file carries its hash
before and after the move and, for package files, its byte-identical `twin`. A source outside the
project (an intake or author save from elsewhere) is recorded by its file name with
`"external": true`, never by its path on this machine.

```json
{"seq": 12, "utc": "2026-01-10T17:40:02Z", "event": "claim", "version": "V07", "slug": "methods wording", "pass": "90_Agent_Work/passes/V07_methods_wording", "source_version": "V06", "source_sha256": "bbbb"}
{"seq": 14, "utc": "2026-01-10T18:02:11Z", "event": "release", "status": "pending", "version": "V07", "slug": "methods wording", "pass": "90_Agent_Work/passes/V07_methods_wording", "build_sha256": "aaaa", "source_sha256": "bbbb", "copies": [], "moves": []}
{"seq": 15, "utc": "2026-01-10T18:02:12Z", "event": "move", "status": "complete", "ref": 14, "reason": "Package_Superseded_V07", "ops": [{"old": "01_Manuscripts/00_CURRENT_DELIVERABLE/2026-01-05 AB EXJ V05 figure 1 tracked verified.docx", "new": "99_Archive/2026-01-10/Package_Superseded_V07/2026-01-05 AB EXJ V05 figure 1 tracked verified.docx", "kind": "file", "sha256": "ffff", "twin": "01_Manuscripts/Versions/V05/2026-01-05 AB EXJ V05 figure 1 tracked verified.docx", "recorded_sha256": "ffff", "sha256_before": "ffff", "sha256_after": "ffff"}]}
{"seq": 16, "utc": "2026-01-10T18:02:13Z", "event": "release", "status": "complete", "ref": 14, "version": "V07", "output_sha256": "aaaa", "baseline_sha256": "bbbb", "path": "01_Manuscripts/Versions/V07/2026-01-10 AB EXJ V07 methods wording tracked verified.docx", "gate": "PASS", "changes": "01_Manuscripts/Versions/V07/V07_CHANGES.md", "proof": "01_Manuscripts/Versions/V07/accepted view.pdf"}
```

(The `copies` and `moves` lists of the pending event are shortened here.)

**Pass folder** `90_Agent_Work/passes/VNN_<slug>/`: `pass new` creates it with `pass.json`,
`edits.json` (`{"edits": []}`), the change-note template `VNN_CHANGES.md` and the empty folders
`build01/`, `qa/`, `proofs/` and `review/`. Later builds go to `build02/` and so on, each as
`VNN tracked.docx`; proofs go to `proofs/p1/`, `proofs/p2/`. The claim itself is
`90_Agent_Work/passes/_claims/VNN.json`, created exclusively.

```json
{"schema": "msw-pass/1", "version": "V07", "number": 7, "slug": "methods wording", "date": "2026-01-10",
 "claimed_utc": "2026-01-10T17:40:02Z",
 "source": {"version": "V06", "state": "author accepted",
            "path": "01_Manuscripts/Versions/V06/2026-01-08 AB EXJ V06 author accepted.docx", "sha256": "bbbb"},
 "author": "Given Surname", "build_name": "V07 tracked.docx",
 "edits": "edits.json", "changes": "V07_CHANGES.md", "figures_changed": {}}
```

`figures_changed` (`{"Figure_01": "v2"}`) names the promoted figure versions the release brings
in when `release --figures` is not given; see [release](release.md).

## No deletion; archive by move

- Nothing is deleted: no `rm`, `del`, `Remove-Item`, `rmdir`, `git clean`, `git rm`,
  delete-after-copy or overwrite. `.gitignore` means "not versioned", never "deleted".
- A file leaves view only by a move into `99_Archive/`. For anything outside the package, use
  `msw.py archive plan --reason <Name> <paths>` (show the list to the author), then
  `archive apply` with the same arguments. It moves each path by rename to
  `99_Archive/YYYY-MM-DD/<Name>/<its project-relative path>`.
- `archive` refuses the skeleton and core files, `.git`, anything already archived, any path an
  index names (or a folder holding one), overlapping paths, an existing destination and files
  with a Word or LibreOffice lock file.
- Files the package manifest lists leave only through `intake`, `author-save` and
  `release apply`, which move them to
  `99_Archive/YYYY-MM-DD/<Package_Superseded_VNN|Author_Save_VNN>/<path inside the package>` after
  checking that a byte-identical twin stays outside the package, usually in `Versions/`.
- Each moved file's sha256 is recorded before and after the move, one entry per file even when a
  folder moves. *Observed:* two archive passes (about 500 files) claimed "verified hashes" but kept
  none, so they can no longer be checked. The one exception is a LibreOffice profile folder
  (`render_profiles`, `lo_profile*`): it moves whole, and the ledger records the folder and its
  file count rather than a hash per cache file.
- Tools write new files exclusively and refuse existing destinations. The only replacements are
  the tool-owned index files and the generated blocks in `README.md`, `STATUS.md`,
  `VERSION_INDEX.md`, `02_Figures/README.md` and each figure's `START_HERE.md`.

## Git and LFS

```text
# .gitattributes (excerpt)
* -text
*.docx filter=lfs diff=lfs merge=lfs -text
*.pdf filter=lfs diff=lfs merge=lfs -text
*.png filter=lfs diff=lfs merge=lfs -text
```

`init` writes the same LFS line for `docm`, `dotx`, `doc`, `xlsx`, `xls`, `pptx`, `tif`, `tiff`,
`jpg`, `jpeg`, `emf`, `wmf`, `ai`, `psd`, `eps`, `zip`, `gz`, `tgz`, `tar`, `7z`, `xz`, `bz2`,
`enl`, `prism`, `pzfx`, `eds` and `sav` files.

- `* -text` turns off line-ending conversion, so every file keeps its exact bytes: recorded
  hashes stay valid on every checkout and scripts see literal line endings. *Observed:* the
  thesis repository, with `core.autocrlf=true`, no attributes and no LFS, grew to about 494 MB of
  loose objects, and commits then stopped.
- LFS keeps .docx, images and archives out of the object store. Text stays plain git: `json`,
  `jsonl`, `md`, `csv`, `py`, `R`, `svg`.
- Ignored: each pass's `build*/`, `qa/` and `proofs_work/` folders (the released bytes live in
  `Versions/`), each pass's proof records (`proofs/**/*.json`) and every LibreOffice log
  (`soffice_*.txt`), which hold machine paths, `90_Agent_Work/render_profiles/` and `lo_profile*`
  folders, Word and LibreOffice lock files, msw's own lock `06_Project_Docs/.msw_mutation.lock`
  (below), OS noise, Python caches and virtual environments. The proof PDFs, page images and view
  files stay versioned.
- Local config (`init --git`): `core.autocrlf=false`, `core.longpaths=true` (long synced paths
  with spaces) and `gc.auto=0` (never prune objects automatically).
- One commit per release, made after the author agrees; `release apply` prints the commands. The
  forbidden commands are listed in [preferences](preferences.md#files-git-and-word).

## Parallel sessions

The author often runs several sessions at once, for example one per figure. *Observed:* sessions
raced for the same version number more than once.

1. Run `msw.py status` first: it lists passes in flight and whether each was built on the file
   that is still current.
2. Claim a number with `msw.py pass new --slug "<words>"` before any build. It takes the next
   free number by creating `_claims/VNN.json` exclusively, so parallel sessions get different
   numbers, then creates the pass folder and appends a `claim` event. `--version VNN` claims a
   given number; it is refused if taken or not above the current version.
3. Do not touch figures or sections another session owns. State the exclusions in the change note.
4. `release` refuses when `PROJECT_INDEX.json` no longer names the source recorded in your
   `pass.json`. Claim a new pass from the new current file and build again; the old pass stays
   unreleased.
5. One command changes the project at a time. `intake`, `author-save`, `pass new`,
   `release apply`, `archive apply`, `retarget`, `outstanding`, `figure add|register|promote`
   and `init --adopt` of an existing project hold an operating-system lock on
   `06_Project_Docs/.msw_mutation.lock` from before they read the indexes until their ledger
   event completes, so a second session cannot write back indexes it read before the first one
   changed them. Another such command refuses with nothing changed: wait and run it again.
   `status`, `verify-project`, `figure status`, plans and dry runs do not need the lock, and
   `status` names the command holding it. The operating system releases the lock when the
   command ends, even if it crashes, so no lock is left behind. The file is created once and never
   deleted (it holds only the current holder's PID, command and start time), which keeps projects
   that forbid deleting any file intact; git ignores it.

## What not to keep

*Observed* in the journal project, and dropped here:

- a whole-disk file catalog (about 53,000 rows, including git internals and vendored runtimes)
  and its 60 MB snapshots; `msw.py verify-project` re-checks what the indexes and ledger name;
- the current version recorded in nine files and the open items copied into five; the generated
  blocks render both from `PROJECT_INDEX.json`;
- status pages written as a prepended changelog; the change notes are the history;
- recursive "previous version" chains, keys named after dates or passes, and a copy of the
  release scripts for every version;
- index snapshots before each release (a git commit per release replaces them) and build
  checkpoints in the version index;
- results, P values or manuscript prose in navigation files, and absolute machine paths in any
  record.

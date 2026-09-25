# Bootstrap a manuscript project

Use this page to start a project in an empty folder or to adopt an existing one. The layout,
names and index files are defined in [workspace](workspace.md); this page gives the order of work.

Ground rules:

- Treat everything the author supplies as read-only. Copy it in; never move or edit the originals.
- Create nothing outside the new project folder. Delete nothing, now or later.
- Ask for the author's name; never infer it. `init` refuses to run without it.
- Run `msw.py` from the project folder (or give `--project <folder>`). Each command's `--help`
  lists its exact options.

## 1. Ask the questionnaire

Ask in one message, with numbered questions and a default for each, so the author can reply
"defaults except 5".

| # | Question | Stored as | Default or note |
| --- | --- | --- | --- |
| 1 | Working title | `title` (`init --title`) | Short; shown in README |
| 2 | Venue code for file names | `venue` (`--venue`) | Journal short code such as `JOE`, or `Thesis`; no spaces. It changes on a retarget; version numbers do not |
| 3 | Document kind, target journal and its profile file | `document_kind` (`--kind`), `target_journal` in `PROJECT_INDEX.json` (`--target-journal`), `journal_profile` (`--journal-profile`) | `journal_article` or `thesis`; the profile file defaults to `05_References/Journal_Guidelines/journal.profile.json` and is written in step 5. A later change of journal goes through `msw.py retarget` ([journal profiles](journal-profiles.md#retarget-to-a-new-journal)) |
| 4 | Full name, file initials, name on tracked changes | `author.name`, `author.file_initials`, `author.revision_name` (`--author`, `--initials`, `--revision-name`) | Initials are 1-8 letters; the revision name defaults to the full name. Ask whether co-author review needs an agent label |
| 5 | Spelling variety | `language.spelling` (`--spelling`) | `en-US` (default), `en-GB` or `en-CA`; the thesis used `en-CA`; the journal may constrain it |
| 6 | First person ("we") | `language.first_person` | `allow` (written by `init`) or `avoid` |
| 7 | Independent reviewer | `reviewer.tool`, `reviewer.model`, `reviewer.effort` | `init` writes `codex`, no model, `high`; a model from another family is the norm. Probe it once now |
| 8 | Figure code | `figures_backend` (`--figures-backend`) | `python` (default) or `r` |
| 9 | Where the source material lives | lineage map | Read-only |
| 10 | Where should the project folder go, and is it synced (OneDrive, Dropbox)? | the `init` folder; git setup | On Windows, a full path under 100 characters (step 3); a synced folder decides where the repository lives |
| 11 | Use git, and is there a remote? | git setup (`--git`) | Recommended: git with LFS and one commit per release |
| 12 | Science changes: questions as comments only, or tracked edits for your review when you ask for them to be addressed? | `policy.science_changes` (`--science-changes`) | `comments` (default) or `tracked_for_review`; numbers, statistics and directions still change only with your approval or a recomputation |

`init` has no flags for items 6 and 7: after `init`, edit those keys in `manuscript.json` if the
answers differ from its defaults. Record answers that are not configuration, with their reasons,
in `06_Project_Docs/DECISIONS.md`. The open questions behind items 4, 5, 7 and 12 are explained in
[preferences](preferences.md#settle-at-bootstrap). `msw.py status` prints the science-change
setting, and says when it is the default because `manuscript.json` has no `policy` block (a
project made before the key existed); add the block by hand to record the author's answer.

**Synced folders.** Git works inside a synced folder, but the sync client then also copies
`.git/lfs`, a second full copy of every binary, and can conflict on large objects. *Observed:* the
journal project's LFS store reached about 1.8 GB inside a synced folder. Offer two options and
let the author choose:

1. keep the working tree synced and put the repository's remote on a non-synced disk or a private
   hosted remote with LFS (recommended when the author reviews from several machines);
2. keep the working tree outside the synced folder and use git with a remote as the backup.

## 2. Inventory before copying

Map what exists before copying anything. *Observed:* the author asked for this before any other
work began.

1. List every candidate file: drafts and versions, earlier submissions, reviewer reports, response
   letters, journal guidelines, raw data, analysis files and projects, figure files, and the
   EndNote library (the `.enl` file with its `.Data` folder).
2. For each file, record path, size, modified date, sha256 and what it is.
3. Work out the lineage: which file came from which, what was sent where and when, and what
   changed or was trimmed between versions. Flag any two files that share a version number but
   differ in content.
4. Save the map as `06_Project_Docs/SOURCE_MAP.md` once the project exists (a convention of this
   skill; no command writes it). Show it to the author and confirm which file is the newest and
   which was last submitted.

A synthetic example:

```markdown
| ID | File as received | Date | sha256 (12) | Role | Derived from | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | Draft for journal A.docx | 2025-11-02 | 3f9a1c0b7d21 | submitted manuscript | - | sent 2025-11-03 |
| S2 | Reviewer comments.pdf | 2026-01-20 | 8c02de51aa90 | reviewer report | S1 | two reviewers |
| S3 | Draft revised AB.docx | 2026-02-14 | 51be7730c4d2 | newest author draft | S1 | Figure 3 dropped |
| S4 | cohort_raw.xlsx | 2025-06-30 | 0d4e9f12ab37 | source data | - | copied to 03_Data |
```

## 3. Create the project

1. Choose a new folder. The author's projects are named `YYYY-MM-DD <short description>`; keep
   the description to a few words.
   **Windows path length.** Without long-path support (the `LongPathsEnabled` setting, which an
   administrator turns on), Windows refuses paths of 260 characters or more, and the error
   often reads as a misleading "file not found". A pass folder, its build and proof and
   LibreOffice's files add 100 or more characters to the project root. So:
   - keep the project folder's full path under 100 characters, or ask for long paths to be
     enabled; synced and temporary folders are often deep already, so check the length first;
   - keep pass slugs to two to four short words (`pass new --slug "methods wording"`);
   - use the names the tools print: builds in a pass are `VNN tracked.docx` and proofs go to
     `<pass>/proofs/p1`, so the long release name appears only in `Versions/VNN/`.

   `init`, `status` and `pass new` print a `WARNING` when the root is over 100 characters and
   long paths are off, and an error caused by the limit names the path and its length. In this
   skill's own test run, a 171-character root made the first build and proof fail.
2. Run `msw.py init "<folder>" --title "..." --venue EXJ --author "Full Name" --initials AB`,
   adding `--revision-name`, `--target-journal`, `--journal-profile`, `--spelling`, `--kind`,
   `--figures-backend`, `--science-changes` and `--git` from the answers. It refuses a folder
   that is not empty (see [adopt](#adopt-an-existing-folder)); a folder that holds only agent-host,
   editor or git folders (`.agents`, `.claude`, `.codex`, `.git`, `.idea`, `.vscode`) counts as
   empty, so the skill may be installed into the project folder before `init`. It creates the tree
   described in
   [workspace](workspace.md), `manuscript.json`, `PROJECT_INDEX.json`, `VERSION_INDEX.json` and
   `.md`, `FIGURE_INDEX.json`, `PACKAGE_MANIFEST.json`, `README.md`, `AGENTS.md`, `CLAUDE.md`,
   `STATUS.md`, `DECISIONS.md`, `.gitignore`, `.gitattributes` and the ledger with its `init`
   event. The journal profile itself is not created.
3. Read the generated `AGENTS.md`. Add project-specific rules below the generated sections.
4. `--git` runs `git init`, sets `core.autocrlf=false`, `core.longpaths=true` and `gc.auto=0`,
   and runs `git lfs install --local` when Git LFS is installed. It reports LFS as enabled only
   when `git check-attr` shows `.gitattributes` sending `.docx` files to LFS, and warns when that
   file lacks the LFS lines or `* -text`. It prints the first commit command without running it.
   Without `--git`, run the same steps by hand. The reasons are in
   [workspace](workspace.md#git-and-lfs).

## 4. Copy sources in, read-only

| Material | Destination | Rule |
| --- | --- | --- |
| Raw data | `03_Data/Original_Source/` | Byte copies, never edited; derived tables go to `03_Data/Derived/` |
| Word files received (drafts, co-author edits) | `01_Manuscripts/Incoming/` | `msw.py intake <file> --as-version VNN --role incoming` copies and indexes it for reference, named by the pattern with state `received`; a returned file the next pass builds on goes in as described in [revision](revision.md#1-choose-the-source) |
| Other files received (reports, letters, PDFs) | `01_Manuscripts/Incoming/` | Copy by hand as `YYYY-MM-DD <original name>`; never edited |
| EndNote library | `05_References/EndNote/` | `.enl` and `.Data` together, names unchanged |
| Journal or school guidelines | `05_References/Journal_Guidelines/` | Your own summaries with source links and dates |
| Papers used as evidence | `05_References/Evidence/` | One file per paper |
| Analysis projects (for example Prism files) | `04_Analysis/<workstream>/` | Copies; extracted tables are new files |

For every copy made by hand:

1. copy without overwriting any existing file;
2. hash the original and the copy, and stop if they differ;
3. mark data copies read-only (on Windows, `attrib +R`);
4. add the copy's path to the lineage map.

Work only on copies. Large public downloads that can be fetched again do not need copying; record
their source and version instead.

## 5. Write the journal profile

Write the profile before drafting or linting anything: `new-manuscript`, `title-page`,
`wordcount` and `lint` read it for the separate title page, the formatting and the limits, and
use the house defaults while it is missing. Create it as a new file at the path `manuscript.json`
names in `journal_profile`: `05_References/Journal_Guidelines/journal.profile.json` unless
`init --journal-profile` named another. Start from the skill's
`assets/journal-profiles/template.profile.json` and the journal's current author guidelines.
Store your own summaries only, each rule with its source link, retrieval date and how it was
read, and record conflicts between the journal's pages. For a thesis, summarize the graduate
school's formatting rules the same way. See [journal profiles](journal-profiles.md).

## 6. Register V01

**From an existing draft.** Run `msw.py intake "<newest file>"`. A project's first file becomes
V01 with the state `baseline`: it is copied byte for byte to
`01_Manuscripts/Versions/V01/YYYY-MM-DD <initials> <venue> V01 baseline.docx`, made current and
copied into `00_CURRENT_DELIVERABLE/`, with VERSION_INDEX, PROJECT_INDEX and package manifest
entries and a ledger `intake` event (pending, then complete) holding its sha256. It refuses a
file with a Word or LibreOffice lock file beside it. A file taken in from outside the project is
recorded by its name and sha256 only (`"external": true`), never by its path on this machine.
V01 is the author's text unchanged, so every later version
is a tracked delta against it.

Then profile it into a new folder, `90_Agent_Work/QA/<date>_V01_intake/`:

- `msw.py inspect <V01>`: revisions and their authors, the highest revision id, citations,
  pictures, paragraphs with ids, runs with attributes, comment count, Word lock files;
- `msw.py census <V01>`: the EndNote census (citations, bibliography, links, placeholders);
- `msw.py comments <V01>`: comment threads with anchors, replies and done state;
- `msw.py lint <V01> --json "<QA folder>/lint.json"` and `msw.py wordcount <V01>` against the
  profile.

`inspect`, `census`, `comments` and `wordcount` print to the console and have no output-file
option. To keep their output, redirect it into a new file with a new name each time, because a
plain `>` replaces an existing file without asking: in PowerShell,
`msw.py inspect <V01> | Out-File -NoClobber -Encoding utf8 "<QA folder>/inspect.txt"`; in bash,
run `set -o noclobber` first, then `msw.py inspect <V01> > "<QA folder>/inspect.txt"`. `lint`
(`--json`, `--markdown`) and `verify` (`--json`) write new files themselves and refuse existing
ones.

If the draft still holds tracked changes, they stay. Ask whether the author wants to accept them
in Word first; if so, intake the new save instead.

**From nothing.** With the profile from step 5 in place, draft `manuscript.md` and
`title_page.json` as described in [drafting](drafting.md), build with `msw.py new-manuscript`,
then intake the built file as V01.

## 7. Register the figures

For each figure in V01:

1. `msw.py figure add --id Figure_NN --title "Title words"` adds the index entry and creates
   `02_Figures/Figure_NN_<Title_words>/` with its `START_HERE.md`; add a `source_data/` folder by
   hand;
2. register the figure as it stands as `v1`. No command extracts pictures: copy the picture's
   media part out of the V01 .docx byte for byte (for example with Python's `zipfile`) into
   `out/v1/` of the figure folder. Then run `msw.py figure register --id Figure_NN --version v1
   --file <that png> --embed <that png>` (add `--file` for a legacy source file; at most one PDF
   and one PNG), and `msw.py figure promote --id Figure_NN --version v1`. `msw.py figure status`
   should now show `MATCH` for the picture in V01;
3. agree with the author which figures to rebuild in code with nature-figure. A rebuild becomes
   `v2`, checked in a fresh QA folder.

See [figures](figures.md) for the index entry, the figure gate and releasing a new version.

## 8. First pass: report only

Before any edit, give the author a baseline report:

1. **Numbers.** Recompute the reported statistics from `03_Data/` and compare them with V01 and
   the last submitted text. List discrepancies.
2. **Citations.** Census counts, PMID placeholders, broken links.
3. **Style and journal fit.** `msw.py lint` and `msw.py wordcount` against the profile.
4. **Structure.** Sections present, title-page items, figure and table callouts.

End with numbered decisions and a recommendation for each. Editing starts with the first
`msw.py pass new` after the author answers; see [revision](revision.md). Commit the workspace
("Initialise manuscript workspace") when the author agrees.

## Adopt an existing folder

*Observed:* the thesis project grew in one flat folder, with 97 .docx files at the top level,
45 unpacked working folders, 24 scratch scripts, Word lock files and sibling copies such as
`references.v34.json`. The current file was judged by eye, one version number was used for two
different files, and git stored .docx files as plain blobs without LFS.

**Default: copy in, leave the legacy folder untouched.** Create the new project beside it and use
the legacy folder as a read-only source.

1. Inventory the legacy folder read-only: path, size, modified time, sha256 and a class for each
   file (released version, author save, received file, build intermediate, unpacked working
   folder, scratch, Word lock file, data, figure, reference, document).
2. Settle version identity. Sort by number and date, describe duplicates in the lineage map, and
   ask the author which file is newest.
3. Create the new project with steps 1, 3, 4 and 5 above (the profile before any lint).
4. Keep the historical numbering: `msw.py intake "<newest file>" --as-version V34 --slug "<words>"`.
   The next `pass new` continues after the highest number any index, pass folder, claim or
   `Versions/VNN` folder uses. Never restart at V01 and never reuse a number: `intake` refuses a
   released or claimed number.
5. Intake older versions only when they matter (the last one sent to a supervisor, a committee or
   a journal), each with its own `--as-version`. The lineage map records the rest with hashes.
6. Profile the intake as in step 6, then continue with steps 7 and 8 above.
7. Do not rewrite the legacy repository's history. The new project starts its own repository with
   the attributes in [workspace](workspace.md#git-and-lfs).

**Restructure in place only when the author asks.** Then:

1. inventory as above;
2. if the folder is a git repository, commit its current state first (after asking), so every
   later step can be traced;
3. run `msw.py init . --adopt` with the identity flags. It adds only the missing skeleton files
   and folders and never moves or replaces anything; if `AGENTS.md` exists, the policy is written
   to `06_Project_Docs/Workflows/AGENTS.msw.md` for you to merge. An existing `.gitignore` or
   `.gitattributes` is kept, and a `WARNING` lists the manuscript rules it lacks (`* -text`, the
   LFS lines, the ignores in [workspace](workspace.md#git-and-lfs)); add them by hand before the
   first commit and check with `git check-attr filter text -- probe.docx probe.md` (`filter: lfs`
   for the .docx, `text: unset` for the .md) and `git check-ignore -v`. If the folder already holds a
   `PROJECT_INDEX.json` or `FIGURE_INDEX.json` that is not `schema_version` 2, the project
   commands refuse to rewrite it and `msw.py archive` refuses to move core files, so use the
   copy-in default instead;
4. write a plan (old path, new place, reason) plus a list of what stays; leave Word lock files
   alone; change nothing until the author approves the plan;
5. there is no command that moves a file within the project. Copy each file that belongs in the
   new tree to its place (exclusive copy, hashes compared), then take the originals out of view
   with `msw.py archive plan --reason Legacy_<Reason> <paths>` and `archive apply`, which moves
   them into `99_Archive/<date>/Legacy_<Reason>/` under their original paths with hashes in the
   ledger;
6. continue with steps 5 to 8 above, keeping the historical numbering;
7. run `msw.py verify-project`, then commit.

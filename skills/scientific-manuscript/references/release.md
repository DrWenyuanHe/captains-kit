# Release a version

The release ritual turns an edit spec into a verified, indexed and committed version. Every step
writes new files, and a failed step stops the ritual. Run `msw.py` from the project folder; each
command's `--help` lists its exact options. The ritual consolidates the per-version scripts of the
journal project (observed) into one set of commands (our synthesis).

## The ritual

| Step | Command | Result |
| --- | --- | --- |
| 0 | `msw.py status` | Current file, unregistered Word saves, Word locks, passes in flight |
| 1 | `msw.py pass new --slug "<words>"` | Version claimed; `90_Agent_Work/passes/VNN_<slug>/` with `pass.json`, `edits.json`, `VNN_CHANGES.md` |
| 2 | Write `edits.json` | The edit spec ([revision](revision.md)) |
| 3 | `msw.py build` | `build01/VNN tracked.docx` and its manifest, written only on gate PASS |
| 4 | `msw.py verify` | Independent gate report in `qa/` |
| 5 | `msw.py proof` | PDF and page images of the accepted view in `proofs/p1/` |
| 6 | `msw.py word-qa` | Optional (Windows with Word): native read-only checks in `qa/` |
| 7 | Independent review | Reviewer verdict in `review/` |
| 8 | Complete `VNN_CHANGES.md` | Change note in the pass folder |
| 9 | `msw.py figure promote` | Only when the pass swaps a figure ([figures](figures.md)) |
| 10 | `msw.py release plan`, then `release apply` | `Versions/VNN/`, package, indexes, generated blocks, ledger |
| 11 | `msw.py verify-project`, then a git commit | A clean project check and one commit per release |
| 12 | Report | Newest path, checks, Word steps, decisions |

On any FAIL, fix the cause and build again into a new folder (`build02/`). Never edit a build
output. Never change a checker to make a build pass without saying so in the change note.
*Observed:* one gate was edited after it had failed on the very build it was judging.

## Steps

**0. Check the starting point.** Run `msw.py status`. If it reports a changed package copy or an
unregistered Word file newer than the current one, rebase first (below). Word or LibreOffice lock
files (`~$...`, `.~lock.<name>#`) mean the author has a file open: wait, or work from a copy and
say so. If it shows `INDEXES DISAGREE` or an unfinished release, intake, author save or retarget,
run that command again first ([if it stops part way](#if-it-stops-part-way)). If another pass is in
flight, see [parallel sessions](workspace.md#parallel-sessions).

**1. Claim the version.** `msw.py pass new --slug "methods wording"` claims the next free number,
creates the pass folder exclusively, appends a ledger `claim` event and writes `pass.json` (the
source version, path and hash, the revision author and the build name `VNN tracked.docx`), an
empty `edits.json`, the change-note template and the folders `build01/`, `qa/`, `proofs/` and
`review/`. It prints the build, proof and release commands and the full name the released copy
will get, and warns when the project root is long enough to hit Windows' path limit (keep slugs
short). It refuses while the package copy of the current file has changed (register that save
with `author-save` first) and while the indexes disagree about the current file (finish the
interrupted command first). It warns when another package copy, such as the baseline, has
changed: that save is not the current file ([rebase](#rebase-on-an-author-save), step 3).

**2. Write the spec.** `edits.json` holds the tracked edits, comments, closed comment threads,
picture swaps and intended replacements; see [revision](revision.md). Classify every change first
([preferences](preferences.md#comments-or-edits)).

**3. Build.** Run the printed command, adding the pass's id range
([workspace](workspace.md#names)):

```text
msw.py build "<current path>" "<pass>/edits.json" --out "<pass>/build01/V08 tracked.docx" --id-base 180000
```

The revision author is `--author`, else the `MSW_AUTHOR` environment variable, else
`author.revision_name` in `manuscript.json`; use the configured name unless the author asks.
`build` creates the `--out` folder, resolves a relative `swap_picture` image path against the
spec file's folder, runs the gate and writes nothing on FAIL (`--write-failed` keeps a copy named
`... GATE FAILED.docx` for inspection, which release ignores). Its manifest,
`VNN tracked.manifest.json` next to the output, lists every edit, the revision ids and the gate
result.

**4. Verify.**
`msw.py verify "<source>" "<candidate>" --manifest "<manifest>" --json "<pass>/qa/gate.json"`
re-runs the gate from the two files and the manifest. PASS means every change is tracked (the
rejected view equals the source and no formatting changed untracked), the accepted text equals
the source plus exactly the declared edits, citations, fields, bookmarks, pictures, revision ids
and the package are intact, and new revisions carry the configured author. Read every WARN:
`science_guard` lists numbers, statistics terms, gene-like symbols and direction words gained or
lost in changed, inserted or deleted paragraphs (each needs the author's approval),
`citation_context` lists citations whose preceding words changed (punctuation ignored), and
`minimality` means a redline is wider than it needs to be. See [verification](verification.md).

**5. Proof.** `msw.py proof "<build>" --out "<pass>/proofs/p1" --manifest "<manifest>"` renders
the accepted view with LibreOffice into `accepted view.pdf` and page images. `--out` must be new
or empty, so a second proof goes to `proofs/p2`; the release then needs `--proof` to name the one
to keep. LibreOffice runs on one persistent per-user profile outside the project. Look at the
pages that hold edits and at every changed figure. This is a layout proof, not the submission
PDF.

**6. Word QA (optional).** On Windows with Word installed,
`msw.py word-qa "<build>" --out "<pass>/qa/word_qa" --manifest "<manifest>"` opens a read-only
copy in its own hidden Word instance, accepts and rejects in memory and never saves. It checks
that no revisions remain after Accept All and Reject All, that the edited text is found, that
headings have no defects, and that Word's accepted and rejected texts equal the file views. It
never touches a file the author has open. Where it cannot run, report "Word QA: not run" with the
reason.

**7. Independent review.** Give the reviewer configured in `manuscript.json` the source and
candidate paths, the accepted and rejected views from `msw.py views`, `edits.json`, the gate
report, the proof pages and any changed figures. For each edit, ask whether it landed as
intended, is minimal and keeps the meaning (SAFE_WRITING or NEEDS_AUTHOR), and whether anything
changed that should not have. Ask for an overall PASS or FAIL citing the files it read. The
reviewer works read-only. Keep the prompt and verdict in `review/round01/`, fix, and run further
rounds as needed; each round re-reads the files.

**8. Complete the change note** (`<pass>/VNN_CHANGES.md`), following the template below, and
delete its first line (`<!-- msw: complete this note before release ... -->`); release warns while
that line remains.

**9. Figures.** If the pass swaps a figure, promote the figure version with
`msw.py figure promote` just before the release and name it with `--figures Figure_01=v2` (or in
`pass.json` as `"figures_changed": {"Figure_01": "v2"}`). See [figures](figures.md).

**10. Release.** Run `msw.py release plan --pass "<pass>"` (plus the same `--figures`) and read
the plan. Without `--build`, `--manifest` and `--proof` it uses the only build `.docx` in the
newest `build*/` folder that holds one, `<build stem>.manifest.json`, and the only PDF anywhere
under `proofs/` (with several, the one whose `proof.json` records this build). A promoted figure
version whose embed PNG the build shows, but which the package does not hold yet, is brought in as
if it were named with `--figures`, and the plan says so. The plan refuses, and nothing changes,
when:

1. VNN is already released;
2. the build or its manifest is missing, or the newest build folder holds more than one build;
3. the manifest's gate status is not PASS, or the build's hash differs from the manifest's;
4. the build was made from another file than the source in `pass.json`;
5. `PROJECT_INDEX.json` no longer names that source (claim a new pass), the current file no
   longer matches its recorded hash, or its package copy changed (run `author-save`);
6. Word or LibreOffice lock files sit in the package, the build folder, the pass folder or the
   current file's folder;
7. the change note is missing, `--proof` is not a PDF, `proofs/` holds several PDFs and not
   exactly one of them was rendered from this build while `--proof` is not given, or the proof's
   `proof.json` shows it was rendered from another file than the build;
8. a destination in `Versions/VNN/` or the package exists with other content;
9. a named figure is unknown, unregistered or not the promoted version, has no registered PDF or
   PNG, has a registered file that changed, or the build's accepted view has no picture with its
   embed PNG's bytes; or a picture in the build matches the promoted embed PNGs of two or more
   figures and the release names none of them;
10. a file leaving the package has no byte-identical twin outside the package and the archive;
11. a tool-owned index or generated document still has a `<name>.new` beside it from an
    interrupted run of another command ([if it stops part way](#if-it-stops-part-way)).

It warns, without refusing, when the change note still carries the template's first line, when
there is no proof PDF or no `proof.json` describes it, and when the build swaps a picture that no
figure in the release accounts for.

Run `msw.py release apply` with the same arguments only if the plan matches what you expect. In
order, it:

1. appends a `pending` ledger `release` event listing every copy and move and the index state
   the release will write (`target`);
2. copies the build into `Versions/VNN/` under its full name,
   `YYYY-MM-DD <initials> <venue> VNN <slug> tracked verified.docx` (claim date, same bytes as
   the build), with the change note and the proof PDF named after the release
   (`<released name without .docx> accepted view.pdf`);
3. moves the package files being replaced into `99_Archive/<date>/Package_Superseded_VNN/<path
   inside the package>` after the twin check, hashing each before and after, and appends a `move`
   event;
4. copies into the package the new current file, the baseline (the file the pass was built on)
   if it is not there, `Change_Notes/VNN_CHANGES.md`, `Proofs/<proof>` and, for each figure the
   release names or brings in, its registered PDF and PNG into `Figures/`; other figures keep
   their package copies;
5. writes `VERSION_INDEX.json`, `PACKAGE_MANIFEST.json`, `FIGURE_INDEX.json` (VNN added to
   `used_in`) and `PROJECT_INDEX.json` (`current`, `baseline`, `figure_set`, `last_release`),
   then the generated blocks (README, STATUS, VERSION_INDEX.md and, with figures, the figure
   README and `START_HERE.md` blocks). Each is written as `<name>.new`, flushed and swapped in;
   a swap blocked by a program that holds the file open is retried for about three seconds;
6. appends the `complete` event and prints the git commands.

If it stops, see [if it stops part way](#if-it-stops-part-way).

**11. Check and commit.** Run `msw.py verify-project`. It fails (exit 1) on a file that is
`MISSING`, `CHANGED` or `UNREADABLE` among those the indexes, package manifest and ledger moves
name; on a ledger line it cannot read; when `PROJECT_INDEX.json`, `VERSION_INDEX.json` and
`PACKAGE_MANIFEST.json` disagree about the current file (`INDEXES`); and on an index operation
(release, intake, author save, retarget or a figure command) that failed and never completed
(`LEDGER`, with the advice to run it again). It warns without failing about a promoted figure
whose embed PNG the current manuscript does not show yet (`NOT SHOWN`, with the next step),
unlisted package files, lock files and pending ledger operations. Then, with the author's
agreement, run the printed `git -C "<project>" add -A` and
`git -C "<project>" commit -m "Release VNN: <slug>"`. *Observed:* without an explicit commit
step, git fell several versions behind the work.

## If it stops part way

A release, intake, author save or retarget that stops (a file held open by another program, a
full disk, a crash) appends a `failed` event, deletes nothing and leaves its pending event open.
`status` lists it under unfinished ledger operations, and `verify-project` fails until it is
finished. Fix the cause and run the same command again with the same arguments (`release plan`,
or `--dry-run` for `intake`, `author-save` and `retarget`, shows what the rerun will do):

- copies and moves already made are recognised and skipped. An intake or author save whose own
  moves already took the file it was given into the archive makes its remaining copies and moves
  from the sources its pending event recorded, each SHA-256 checked;
- an intake stopped after `VERSION_INDEX.json` already listed its file is finished, not refused
  as a reused version number;
- a retarget whose `manuscript.json` or `PROJECT_INDEX.json` already name the new journal is
  finished (leftovers archived, generated blocks written), not reported as "Nothing to change";
- when every copy and move was done and only the index swap stopped, part-way or before it began,
  the rerun moves any leftover `<name>.new` files into `99_Archive/<date>/Interrupted_Run/` (by
  rename, hashed, recorded as a `move` under the pending event), then writes every index and
  generated block from the `target` in the pending event and completes it. The result is the
  same whether none, some or all of the indexes had been swapped;
- a rerun refuses when the current file has changed since the operation was planned (run
  `msw.py verify-project` and ask the author which file is current), and when a release is
  rerun with another build than the one it already copied (finish it with `--build` naming that
  build, and make later changes in a new pass).

While the indexes disagree about the current file, `status` shows `INDEXES DISAGREE`,
`verify-project` fails and `pass new` refuses. A rerun of `author-save` first finishes the save
that stopped, even when the finished moves have already put the released copy back at the path the
author named; it then says to run `author-save` again if that file holds a later save. A pending
event from an older ledger, written before targets were recorded, is finished only when the
indexes already name the new file. Every other command that rewrites indexes
refuses while a `<name>.new` exists; if no unfinished release, intake, author save or retarget
can take it over, move it away with `msw.py archive plan|apply --reason Interrupted_Run <files>`.

## Change note template

`pass new` writes the template; complete it and keep results and P values out of the summary
line. Describe what changed and where. A synthetic example:

```markdown
# V08: methods wording (2026-01-12)

`2026-01-12 AB EXJ V08 methods wording tracked verified.docx` (built as `V08 tracked.docx`): V07
author accepted plus 6 tracked revisions by <revision name>, ids 180001-180006, dated 2026-01-12.
Scope: wording in Methods 2.3 and 2.5. No result, figure, table or citation changed.

## Source: V07 author accepted
- File: `01_Manuscripts/Versions/V07/2026-01-11 AB EXJ V07 author accepted.docx`
- SHA-256: `51be7730c4d2`

## Manuscript (tracked)
- 180001-180002, Methods 2.3: "were stored" -> "were then stored".
- 180003-180005, Methods 2.5: two comma fixes; a comment anchored to "adjusted for age".
- 180006, title page: word count 4,812 -> 4,813 (house method).

## Verification
- Gate: build PASS; independent verify PASS (qa/gate.json). Citations 42 = 42.
- Reject view equals the source. Proof: pages 6-7 checked.
- Native Word QA: not run (no Word on this machine). Review: <tool, model, effort>, round 1 PASS.

## Open items for the author
1. Comment on "adjusted for age" (Methods 2.5) waits for the author.

## Not done
- EndNote, the table of contents and page numbers are refreshed by the author in Word.
```

Released notes are never edited. If a later pass finds an error in one, add a
`## Correction to VNN` section to the new note.

## Rebase on an author save

The author reviews in Word, replies to comments, accepts changes and saves, often in place in the
package. *Observed:* this happened repeatedly, and Word renumbered ids, comments and media each
time.

1. `msw.py status` reports the changed package copy or the unregistered file, or the author says
   they have edited a version.
2. Wait until the author has closed the file: `author-save` refuses a file with a Word or
   LibreOffice lock file.
3. Run `msw.py author-save "<saved file>"` (`--dry-run` first shows the plan). It copies the save
   to `Versions/VNN/` (VNN defaults to the current version) as `... VNN author saved.docx` when
   tracked changes remain or `... author accepted.docx` when none do (`--state` overrides), adds a
   VERSION_INDEX entry and a ledger `author_save` event, and makes the save current with the
   previous current file as its baseline. In the package, the file the author saved over and any
   other replaced copy move to `99_Archive/<date>/Author_Save_VNN/` after a twin check, and the
   released bytes are copied back into their slot. A file identical to the current one is not
   registered. It refuses a package copy that is not the current file, such as the baseline copy:
   registering it would make older text current. Keep that save with
   `msw.py intake "<file>" --role incoming --as-version <its version>` and carry its edits into
   the current version in a pass. A tracked copy returned by a supervisor or co-author goes in
   with `intake` instead ([revision](revision.md#1-choose-the-source)).
4. Profile the save: `msw.py inspect` (highest revision id, remaining revisions and authors),
   `msw.py comments` (threads with anchors, replies and done state) and `msw.py views` to compare
   its accepted view with the released one, rather than matching comment ids.
5. Claim the next pass: its source is now the save. A pass claimed before the save cannot be
   released (its source is no longer current); claim a new one and build again.
6. Act on the author's replies and edits as described in [revision](revision.md).

## Package contents

| Item | Location in `00_CURRENT_DELIVERABLE/` | Written by |
| --- | --- | --- |
| Current version | Top level | `release apply`, `author-save`, `intake` |
| Baseline: the version or author save it was built on | Top level | the same |
| Current change note | `Change_Notes/VNN_CHANGES.md` | `release apply` |
| Accepted-view proof (`<released name> accepted view.pdf`; the previous one is archived) | `Proofs/` | `release apply` |
| PDF and PNG of each figure version a release names or brings in | `Figures/` | `release apply` |
| Supplementary files, separate tables, journal figure files | `Supplementary/`, `Tables/`, `Journal_Figures/` | By hand (below) |
| Package manifest (every listed file hashed) | `PACKAGE_MANIFEST.json` | the same commands |

Anything listed leaves the package only through those commands' twin-checked moves. For other
clean-ups, run `msw.py archive plan`, show the list to the author, and run `archive apply` after
approval.

**Files placed by hand.** No command puts standalone tables, supplementary files or journal figure
files into the package, and `verify-project` checks only the files that the indexes, the package
manifest and the ledger name. It reports every other package file as `UNLISTED` (a warning) and
never hashes it, so a changed or stale copy there goes unnoticed. For each such file:

1. keep its source where a command records a hash: a table or supplementary `.docx` with
   `msw.py intake "<file>" --role incoming --as-version VNN --state "Table 2"` (copied into
   `Incoming/` and indexed), a figure's TIFF or SVG with `msw.py figure register --file` when its
   version is registered, and journal exports in the export run's `manifest.json`
   ([figures](figures.md#journal-exports));
2. copy it into the package without overwriting (hashes compared), and list its package path,
   source and sha256 in the change note or the submission record;
3. before upload, compare each package copy with its recorded hash;
4. take a superseded copy out with `msw.py archive plan|apply`, which moves it without a twin
   check, so confirm first that its recorded source still exists.

## Generated blocks

Commands rewrite only the text between their markers and keep everything else:
`<!-- msw:current:start -->` / `<!-- msw:current:end -->` in `README.md`, `msw:status` in
`06_Project_Docs/STATUS.md` and `msw:versions` in `VERSION_INDEX.md` (at each intake, author
save, figure promotion, release, retarget and `outstanding add|done`), `msw:figures` in
`02_Figures/README.md` and `msw:figure` in each figure's `START_HERE.md` (at each figure command
and figure release). They show the current version, the baseline, the package, the figure set
and the open items, rendered from the indexes. Record an item that stays open across versions
with `msw.py outstanding add --text "..."` and close it with `msw.py outstanding done --id O3`;
with none open the blocks say "Open items: see the latest change note". Write free prose outside
the markers. Keep results out of the blocks; link to the change note instead.

## Report to the author

A synthetic example:

```text
V08 is ready: 01_Manuscripts/00_CURRENT_DELIVERABLE/2026-01-12 AB EXJ V08 methods wording tracked verified.docx
Checks: gate PASS | citations 42 = 42 | rejected view = V07 author accepted | proof pages 6-7 looked at |
        Word QA not run (no Word) | reviewer PASS (round 1)
In Word: 1. update EndNote citations and bibliography  2. update the table of contents  3. check page numbers
Decisions:
1. Methods 2.5 says "adjusted for age", but the regression also adjusts for sex.
   A: add "and sex" (recommended). B: leave as is.
Waiting on you: 1 decision (the comment in Methods 2.5).
```

Explain any method or label the first time it appears, in plain words.

## Submission packaging

Prepare the files the journal profile asks for ([journal profiles](journal-profiles.md)) from the
current version, each as a new file:

1. **Clean manuscript.** The author accepts all changes in Word and saves; register the save with
   `msw.py author-save` (state `author accepted`), then check it against the profile with
   `msw.py lint` and `msw.py wordcount`.
2. **Separate title page** when `title_page.separate_file` is true: `msw.py title-page
   --title-page title_page.json --manuscript "<clean manuscript>" --out "<new>.docx"` (see
   [drafting](drafting.md)). Its items must match the portal entries exactly.
3. **Figures as separate files** in the formats, resolution, colour mode and size the profile
   sets, each exported from its source and listed with the source's hash; see
   [figures](figures.md#journal-exports). Check per-file and combined upload limits.
4. **Tables and supplementary files** as separate editable files when the profile asks. These and
   the journal figure files are placed and recorded by hand ([package contents](#package-contents)).
5. **PDF.** The submission PDF comes from Word, made by the author. The LibreOffice proof is a
   layout check only.
6. **A revision's manuscript copies.** Read the profile's `revision` block. With `markup`
   `track_changes` or `either`, send the tracked copy; with `highlight_or_colour`, send a
   highlighted copy, and the tracked copy too only when `track_changes_accepted` is `yes`. How to
   make each copy is in [reviewer response](reviewer-response.md#6-choose-tracked-or-highlighted-markup).
7. **Cover letter and response to reviewers.** Draft them from the skeletons in
   [reviewer response](reviewer-response.md): the author's courteous register, signed with the
   author's full name from `manuscript.json`, every point answered with its section, page and
   line. The author edits them.
8. **Final integrity check.** Before anything is uploaded or sent to a committee or supervisor,
   compare the approved or last-submitted version with each manuscript file to be sent:
   `msw.py drift "<approved>.docx" "<final>.docx" --json "90_Agent_Work/QA/<date>_final_drift/drift.json"`.
   The approved or last-submitted version is the one named in `SOURCE_MAP.md` or `DECISIONS.md`,
   not PROJECT_INDEX's `baseline` (the file the last pass was built on). `drift` compares the two
   accepted views across any number of versions and lists the changed paragraphs, the numbers,
   statistics terms, symbols and direction words gained or lost, and changes in the citation and
   picture counts. Confirm each item against `DECISIONS.md`, a change note's approved `purpose` or
   a recomputation; have the independent reviewer read the changed paragraphs for hedges and
   negations, which the lists do not catch; and report any unapproved item as a numbered decision
   before sending. Report the result in one line, for example
   `drift vs V05 submitted: 14 paragraphs changed, all approved`.

Keep an exact copy of what was uploaded in a new dated folder, for example
`01_Manuscripts/Submissions/YYYY-MM-DD_<journal>/` (a convention; no command creates or indexes
it), never edited afterwards, and note the date, journal and folder in `DECISIONS.md`.

### Thesis deposit

The deposit steps are the author's work in Word; the tools check files only. A checklist:

1. Run the final integrity check above against the version the committee approved.
2. Line numbers off: the author sets Line Numbers to None in Word. Review drafts keep them.
3. Page numbering: the author sets roman numbers for the front matter and arabic from the first
   chapter, with section breaks in Word; the builder writes one section only.
4. The author updates the table of contents in Word. The lists of tables, figures and
   abbreviations carry page numbers as plain text, taken from the author's Word PDF of the final
   file; no command measures the page of a heading or caption.
5. The final PDF comes from Word, made by the author and named as the graduate school requires;
   check any PDF/A or embedded-font rule it sets.
6. Keep the deposited files in a dated folder as for a submission, and record the deposit in
   `DECISIONS.md`.

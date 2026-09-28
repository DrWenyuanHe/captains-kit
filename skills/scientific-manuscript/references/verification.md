# Verification

A version reaches the author only after four things: the gate passes, the accepted view has been
rendered and every changed page looked at, native Word QA has passed or its absence is stated, and
an independent reviewer has read the files themselves. Report each result in one line.
`PASS` below stands for the pass folder, `90_Agent_Work/passes/VNN_<slug>/`.

## Order

1. `msw.py build` runs the gate itself and writes the file only on PASS ([revision](revision.md)).
2. `msw.py verify SOURCE CANDIDATE --manifest MANIFEST --json PASS/qa/gate.json` re-runs the gate
   from the two files and the manifest alone, and keeps the report.
3. `msw.py views CANDIDATE --out PASS/qa/views` writes `<stem> accepted view` and
   `<stem> rejected view` as .docx and .txt for reading and for the reviewer.
4. `msw.py proof` renders the accepted view; look at every changed page.
5. `msw.py word-qa` on Windows with Word; otherwise say "Word QA: not run" and why.
6. Independent reviewer, read-only, on the artifacts.
7. The report line.

A failure at any step means: fix the spec, build into the next `buildNN/` folder and start again
at step 1. Never adjust a check to make a build pass; if a check is wrong, fix it in the skill
with a test, separately from the manuscript.

These steps check one pass against its source. Before a submission, or a hand-off to a committee
or supervisor, one more check compares the file to send with the approved or last-submitted
version ([drift](#before-a-submission-drift)).

## The gate

`msw.py verify SOURCE CANDIDATE [--manifest M] [--author NAME] [--allow-part-changes] [--json F]`
compares a candidate with its source and prints each check, then a `SUMMARY:` line. Each check is
PASS, WARN (look at it and name it in the report), FAIL (blocks the version) or INFO (not scored).
The gate fails when any check fails; a check that cannot run fails with "could not run". The exit
code is 0 for PASS and 1 for FAIL. `--json` writes `{status, checks, summary}` as a new file.

The build manifest declares what should change: `expected_accept` (the accepted text of every
edited or inserted paragraph, with its position), optional `intended` replacements, and
`declared` citation, field and formatting changes, picture swaps and removals, added or changed
parts and removed comments. Always verify
a build with its manifest. Without one the gate is stricter in some checks and blind in others:

| Check | With the manifest | Without it |
| --- | --- | --- |
| `accepted_text` | FAIL on any difference from the declared text or `intended` | INFO: changed paragraphs listed, not scored |
| `pictures` | FAIL on any name or byte difference from the declared swaps | WARN on name or byte differences; FAIL only on a count change |
| `citations`, `links` | Declared citation changes turn a citation difference into WARN and allow `_ENREF` changes | Any citation or `_ENREF` change fails |
| `formatting` | Declared formatting turns a loss into WARN | Any loss fails |
| `package` | Declared new and changed parts pass | New parts other than media and comments fail |
| `new_revisions` | Author taken from the manifest | Author checked only with `--author` |

The checks, in report order:

| Check | Status and meaning | Fix |
| --- | --- | --- |
| `package` | FAIL: a part removed; a stray file packed (`~$`, `~WRL`, `.~lock`, `.bak`, `.tmp`, `.orig`, `__pycache__`, `Thumbs.db`, `.DS_Store`); an undeclared new part; an XML part not well-formed; a part without a content type; a relationship to a missing part; a relationship id used in a part (a picture's `r:embed`, a link's `r:id`) that its `.rels` does not define; styles, numbering, font table, theme, headers, footers or notes changed without declaration | Rebuild from the spec; never repack by hand. `--allow-part-changes` only for a file Word saved |
| `termination` | FAIL: `document.xml` does not end with `</w:document>` (truncated write) | Rebuild |
| `revision_markup` | FAIL: `w:t` or `w:instrText` in a deleted run (must be `delText`/`delInstrText`); `delText` outside a deletion or move; `w:ins` directly inside `w:ins`, or `w:del` inside `w:del` | Rebuild with the engine; the file was edited by another tool |
| `revision_ids` | FAIL: a new duplicate revision id in any story part; a comment id defined twice; a comment reference without its comment; an unpaired comment range. WARN: duplicates already in the source | Leave `--id-base` out or raise it; check the comments part |
| `bookmarks` | FAIL: a new duplicate bookmark id or name, or a start without an end (or the reverse). WARN: the same problems already in the source | Rebuild; an edit crossed a bookmark |
| `fields` | FAIL: unbalanced field markup in the raw, accepted or rejected view; the bibliography field contains pictures or headings (a runaway `EN.REFLIST`) | Rebuild; if the source already has it, ask the author to fix it in Word and refresh EndNote |
| `untracked_changes` | FAIL: the rejected view's text differs from the source's rejected view: something changed without a tracked revision | Find the listed paragraphs; every change must be a tracked op |
| `untracked_formatting` | FAIL: in the rejected view a paragraph's formatting differs from the source's: bold, italic, superscript, subscript, underline, strike, caps, small caps, highlight or hidden, or any other run property (font, size, colour, language, character style), the paragraph's properties or style, its paragraph-mark properties, a field instruction or an equation (its structure as well as its text). Run splits, property order and rsid attributes are ignored. Runs only when `untracked_changes` passes | Use `format` and `paragraph_format` ops; never restyle runs or edit fields and equations by hand |
| `accepted_text` | See the table above. Paragraphs are compared by position, so identical paragraphs are told apart: each declared paragraph must show its declared text at its recorded position, every other paragraph its source text, and only declared paragraphs may lose their paragraph mark. A paragraph count other than the source's plus the declared insertions, a manifest without paragraph positions (made by an older version: rebuild), or `intended` replacements that do not reproduce the accepted text fail | Correct the spec; an edit landed somewhere else |
| `citation_context` | WARN: the six words before a citation changed, so it may now support different text. Only words count: added or removed punctuation (`groups` to `groups;`) does not. Runs when `citations` passes | Confirm each listed citation still supports its sentence |
| `citations` | FAIL: a citation's field code, EndNote payload or displayed result changed, or the count changed, in either view (WARN when the build declared a citation change) | Restore the citation; citations change only in EndNote |
| `links` | FAIL: a new missing hyperlink or cross-reference target, or `_ENREF` bookmarks changed without a declared citation change. WARN: targets already missing in the source | Rebuild; bookmarks were lost |
| `pictures` | See the table above; pictures a `delete_paragraph` removed are declared in the manifest. Always FAIL: the rejected view's pictures (names and image bytes) differ from the source's, so rejecting a swap or deletion would not restore the original figure, or a picture no longer resolves to an image part | Check the `swap_picture` op and the image file; never replace media parts by hand |
| `sections` | FAIL: section breaks or section properties differ in the accepted view, for example because a deleted paragraph mark carried a section break | Change sections in Word; the engine never changes them |
| `formatting` | FAIL: a flag lost on characters the edit did not change (WARN when formatting was declared). WARN: a flag added on unchanged text, declared or not | Keep markup on unchanged words; confirm each addition is intended |
| `whitespace` | FAIL: a new text element starts or ends with a space but lacks `xml:space="preserve"`, so Word would drop the space. WARN: the same in the source | Rebuild with the engine; never write runs by hand |
| `new_revisions` | FAIL: new revisions by an author other than the expected one (continuations of an earlier author's insertion are allowed); new revisions wrapping field codes without a declared citation or field change (the EndNote rule). WARN: formatting-only revisions the manifest does not declare (builds declare their own `format`, `paragraph_format` and `insert_paragraph` property changes, so this appears mainly without a manifest); more than one new revision date | Set `author.revision_name`; keep edits outside fields |
| `minimality` | WARN: a new deletion and insertion share their first or last word, so the redline is wider than needed | Narrow `old` and `new`, or let `rewrite` compute the diff |
| `science_guard` | WARN: a changed, inserted or deleted paragraph gains or loses a number, a statistics term, an all-capitals symbol or a direction word, or keeps the same ones in a new order (values swapped between groups); neighbouring changed paragraphs are compared as one group ([revision](revision.md), section 7) | Confirm the author approved each item or it was recomputed |
| `word_locks` | WARN: the candidate's own lock file (Word's `~$<name>`, LibreOffice's `.~lock.<name>#`) or a Word `~WRL*.tmp` save file sits beside it, so it is open | Close it before release |

A build gates its in-memory result, so `word_locks` appears only in `msw.py verify`. Before the
first pass on a new source, `msw.py verify SOURCE SOURCE` must pass: it proves the file reads
cleanly and its fields balance.

## The report line

`msw.py verify` ends with `SUMMARY:` and every scored check, for example
`package: PASS (1 changed, 0 added, 0 removed) | termination: PASS (...) | ... | word_locks: PASS`.
Keep that in `qa/gate.json`. For the author, compress it into one line with the version checks:

```text
Checks: gate PASS (WARN: formatting, declared italics; science_guard, approved P value) | citations 3 = 3 |
        rejected view = V07 author accepted | proof pages 1, 4-5 looked at | Word QA PASS | reviewer PASS (round 1)
```

Name every WARN and why it is acceptable. A FAIL is never presented; say what was fixed instead.

## LibreOffice proof

`msw.py proof CANDIDATE --out PASS/proofs/p1 --manifest MANIFEST` writes the chosen view as a
new .docx, converts it to PDF with LibreOffice (headless, hard timeout, default 600 s) and records
the command, versions, attempts, timings and hashes in `proof.json`. `--out` must be a new or
empty folder, one proof per folder (`p1`, `p2`, ...). The file names are short so deep pass
folders stay under Windows' path limit: `accepted view.docx` and `accepted view.pdf` (with
`--view reject` or `as-is`, `rejected view` or `as-is view`); `--name` sets another stem.
`proof.json` names the source file, and when `--out` is inside a project its paths are relative
to the project root, so the record holds no machine path from the project.

With pypdfium2 or PyMuPDF installed (`--rasterizer auto`, the default, tries them in that order),
it also writes page PNGs named `pageNN.png` (default 110 dpi, `--dpi` 30 to 600): page 1 plus
every page that holds a probe. Probes are the first 60 characters of each edited paragraph's
accepted text in the manifest (paragraphs under 8 characters and deleted ones are skipped) plus
any `--probes TEXT...`; a probe not found whole is searched by its first 30 characters.
`--pages 3,5-7` replaces the default pages. Without a rasterizer the PDF is written and the probes
are "not searched".

LibreOffice is found through `--soffice`, then `MSW_SOFFICE`, then `PATH`, then the standard
install. It runs on one persistent profile per user, outside every project and never deleted:
`%LOCALAPPDATA%\msw\lo_profile` on Windows (else `$XDG_CACHE_HOME/msw/lo_profile` or
`~/.cache/msw/lo_profile`), the same inside and outside a project. `--profile-dir` sets another.
(LibreOffice writes files about 140 characters below its profile, so a profile inside a project
planted paths over Windows' limit.) `proof` refuses a profile whose `.lock` belongs to a running
LibreOffice, because a second instance hands the job to the first and exits without output. On a
timeout it stops only the process tree it started.

The exit code is 0 when a PDF was written, 1 when the conversion failed or timed out (the record is
still written) and 2 for a refusal or a file error, printed as `the proof stopped: ...`. Observed on
Windows with LibreOffice 26.2:

- A new profile sometimes crashes the first conversion (exit code 3221226505); `proof` retries once
  and notes it. If a proof still fails, run it again into a new folder.
- Windows' 260-character path limit: a failed conversion whose output path is that long gets a
  note to use a shorter `--out`, `proof.json` notes a `--profile-dir` over 150 characters, and an
  error from the limit names the path and its length. Keep the project root short
  ([bootstrap](bootstrap.md#3-create-the-project)).

Limits, printed with every proof:

- LibreOffice does not show a deletion nested inside an earlier, unaccepted insertion; check such
  edits in Word.
- It does not refresh EndNote fields: citations and the bibliography show the results stored in the
  file.
- Its line breaks, pagination and font substitution can differ from Word's. Page numbers from the
  proof are approximate; the submission PDF comes from Word, made by the author.

## Visual review of changed pages

Look at the rendered pages yourself; reviewer approval does not replace it. Cover:

- every page `proof` lists for an edited paragraph (a probe marked "not found" usually starts on a
  page break or near a field: find it by eye and render it with `--pages`);
- every page with a swapped picture, its legend and any changed table;
- the title page when the title, authors or word count changed.

On each page check: spaces kept around every edit (no joined words), italics on gene symbols and
Latin, superscripts and subscripts, citations still next to the words they support, pictures at
the right size and not cropped, legends on the same page as or after their figure, headings not
stranded at a page foot, and no text overlapping figures. Record the pages looked at for the
report line.

Reference-list spacing is not the manuscript's to fix: EndNote's Format Bibliography (Configure
Bibliography) Layout settings, Line spacing and Space after, control it and rewrite it at every
Update Citations and Bibliography. `msw.py lint`'s `format.reference-spacing` warning is a
reminder to check that setting against the profile's `formatting.references_line_spacing`; it
never becomes a tracked edit or a change to the Word style ([EndNote](endnote.md#the-authors-rule-hands-off)).

## Native Word QA

`msw.py word-qa CANDIDATE --out PASS/qa/word01 --manifest MANIFEST` runs the skill's
`scripts/word_qa.ps1` on a read-only copy in a new hidden Word instance with macros disabled. It
never saves and never uses the author's running Word. It runs only on Windows (elsewhere it exits
3 and points to `proof`); `--out` must be a new or empty folder. If the candidate is open in Word,
it warns and reads the copy. Probes come from the manifest as for `proof`, plus `--probes`.

| Check | Fails (or warns) when |
| --- | --- |
| `run_completed` | The run timed out (default `--timeout` 900 s), wrote no result or stopped with an error |
| `source_unchanged`, `copy_unchanged` | The candidate or the read-only copy changed during the run |
| `isolated_instance`, `opened_read_only` | The document window was not in this run's own Word process, or not read-only |
| `accepted_revisions_remaining`, `rejected_revisions_remaining` | Revisions remain after Accept All or Reject All |
| `undo_restored` | WARN only: Undo did not restore every revision (the reject pass then reopens the copy) |
| `heading_defects` | A heading is empty or ends in a space or a colon |
| `probes` | A probe is missing from Word's accepted text; WARN when one is found more than once |
| `accepted_text`, `rejected_text` | Word's text differs from the file's view (listed as word-level hunks); WARN when only field results or object display differ |
| `accepted_inline_shapes`, `rejected_inline_shapes` | WARN only: Word's picture count differs from the file's |
| `word_process_exited` | WARN only: this run's Word did not exit within `--exit-wait` (default 30 s). `--reap-own-process` stops that one process and no other |

The exit code is 1 when any check fails, 0 otherwise, and 2 for a refusal. Results go to
`word_qa.json` and `word_qa_report.txt`, which also gives Word's revision counts by type and
author and the accepted view's pages and words. Only the main story is compared, so text boxes
show as differences, and headings are found by Word's built-in Heading 1-9 styles. It reports the
total page count only, not the page of each heading or caption: page numbers written into a
thesis's front-matter lists come from the author's Word PDF of the final file.

## Before a submission: drift

The per-pass gate compares a build with its own source, and `science_guard` sees one pass at a
time. Across a whole revision round the question is different: what changed since the version the
author, a committee or a journal last approved? Answer it with
`msw.py drift "<approved>.docx" "<final>.docx" [--json OUT]` as the final check before anything is
sent ([release](release.md#submission-packaging)). It compares the two accepted views, however
many versions lie between them, and lists the changed paragraphs, the numbers, statistics terms,
symbols and direction words gained or lost, and changes in the citation and picture counts.
It exits 0 when nothing changed, 1 when it lists changes (to be confirmed, not a failure) and 2
when it refuses, for example because the `--json` file already exists. The JSON adds each
changed span with the 40 characters before it in the newer file, which is what a `format` op
needs to locate it.

- The approved or last-submitted version is named in `SOURCE_MAP.md` or `DECISIONS.md`; it is not
  PROJECT_INDEX's `baseline`, which is only the file the last pass was built on.
- Confirm every listed item against `DECISIONS.md`, a change note's approved `purpose` or a
  recomputation from the data. Any item without one is a numbered decision for the author.
- Give the drift report and both files to the independent reviewer, who reads the changed
  paragraphs for hedges, negations and claim strength, which the lists do not catch.
- Report it in one line: `drift vs V05 submitted: 14 paragraphs changed, all approved`.

## Independent reviewer

The reviewer is `reviewer` in `manuscript.json` (`tool`, `model`, `effort`); the author's default is
Codex. Run it read-only from the project root, one new folder per round:

```text
# PowerShell
Get-Content -Raw -Encoding UTF8 PASS/review/round01/prompt.md | codex exec -s read-only -m MODEL -c model_reasoning_effort=high -o PASS/review/round01/verdict.md -
# bash
codex exec -s read-only -m MODEL -c model_reasoning_effort=high -o PASS/review/round01/verdict.md - < PASS/review/round01/prompt.md
```

`MODEL` and the effort come from `reviewer.model` and `reviewer.effort`; omit `-m` when the model
is empty. Add `--skip-git-repo-check` if the project is not a Git repository. `-o` replaces an
existing file, so each round gets a new folder. Give the reviewer the paths of: the source and
candidate .docx, the accepted and rejected view .txt files, `edits.json`, the build manifest,
`qa/gate.json`, the proof PDF and page PNGs, changed figures and the draft change note. A
synthetic prompt:

```text
You are an independent reviewer of a tracked-change manuscript revision. Work read-only.
Open the files listed below yourself; do not rely on this prompt's description of them.
For each edit in edits.json: did it land as intended in the accepted view, is the redline minimal,
and is it SAFE_WRITING or NEEDS_AUTHOR (a number, statistic, gene symbol, citation or claim changed)?
Check every gate WARN in gate.json. Did anything change that no edit declares?
Do the proof pages show layout damage?
Be even-handed: report real defects and confirm what is correct, with evidence (file and paragraph).
End with PASS or FAIL and the list of files you opened. Say so if you could not open one.
```

Ask for an even-handed verdict, not a refute-by-default stance: a reviewer told to default to
refuting dismissed real defects. Require evidence from the files: an earlier "verified" report
rested on a reviewer that had only seen the agent's summary. A verdict that does not list the files
it opened is incomplete.

If the configured tool or model is unavailable (probe it at bootstrap), use an independent subagent
with a fresh context, the same prompt and read-only access, and say so: "reviewer: fallback
subagent (configured model unavailable)". After a FAIL, fix, rebuild, re-verify and run a new round
that reads the new files.

## Lessons from the legacy tools

The earlier pipeline's checks counted things: `ADDIN EN.CITE` strings, field begins and ends,
superscript runs and open and close revision tags. Counts miss what matters:

- A citation tracked-deleted with correct markup, moved to another clause, or given a new display
  number leaves every count unchanged. So does a spelling pass that edits a reference title inside a
  field code, stripped `_ENREF` bookmarks, or a package missing `styles.xml`.
- The counts were miscalibrated: nested `EN.CITE.DATA` fields doubled the citation count, and for
  parenthetical citations the superscript count measured unrelated formatting while blocking
  legitimate edits to superscript units.
- The documented tag-balance check failed on every real release, because paragraph-mark revisions
  are self-closing. A check that always fails teaches people to ignore it.
- Native Word QA recorded an inventory and asserted nothing, and one gate was edited after it had
  failed on the build it was judging.

The new gate compares views instead: the rejected view must equal the source, the accepted view
must equal the source plus exactly the declared edits, and citations are compared as fields. On 19
realistic corruptions of a released manuscript the legacy verifier and gate together caught 1 and
the new gate caught all 19; on 9 real release transitions it raised no false failure, where the
legacy gate failed all 9. See [source notes](source-notes.md) for the evaluation.

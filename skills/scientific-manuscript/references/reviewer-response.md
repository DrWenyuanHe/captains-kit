# Reviewer and examiner responses

Use this page when a decision letter, reviewer reports or a thesis examiner's report arrives. The
report is split into numbered points, each point gets a class and an action, the text changes go
through ordinary tracked passes ([revision](revision.md)), and the letters are drafted for the
author to edit. Items marked *Observed* come from the author's thesis and journal projects; the
triage table, the letter skeletons and the highlighted-copy procedure are this skill's synthesis.

## 1. File the report

1. Copy each letter and report into `01_Manuscripts/Incoming/` as `YYYY-MM-DD <original name>`
   and never edit it. A Word file holding an examiner's comments goes in with
   `msw.py intake "<file>" --role incoming --as-version VNN`
   ([bootstrap](bootstrap.md#4-copy-sources-in-read-only)); a tracked copy the next pass will
   build on goes in as a returned copy instead ([revision](revision.md#1-choose-the-source)). Add each file to
   `06_Project_Docs/SOURCE_MAP.md`.
2. Name the reviewed version: the file that was submitted or sent to the examiners, as recorded in
   the submission folder or `DECISIONS.md`. The letter's locations and the final drift check refer
   to it.
3. Read the journal profile's `revision` block (section 6) and the deadline in the decision
   letter, and record both in `DECISIONS.md`.
4. Keep the round's working files in one new folder, for example
   `06_Project_Docs/Responses/YYYY-MM-DD_<round>/` (a convention; no command creates it): the
   triage table, the analysis specs and the letter drafts.

## 2. Split the report into numbered points

- One point per request or question. A paragraph that asks for two things is two points; a remark
  with no request is a point whose action is to acknowledge it.
- Number by source and keep the numbers for the whole round: `E1` for the editor, `R1.1`, `R1.2`
  for reviewer 1, `R2.1` for reviewer 2, `X1.1` for examiner 1.
- Quote each point exactly, with where it sits in the report. Correct nothing in the quotation.
- When a point cites a page or line, find the paragraph in the reviewed version
  (`msw.py extract` gives paraIds) and record both.

## 3. Triage

Write one row per point into `TRIAGE.md` in the round folder, and show the table to the author
before any edit. A synthetic example:

| Point | Class | Action | Version and revision ids | Location | Status |
| --- | --- | --- | --- | --- | --- |
| R1.1 | WRITING | edit: define HPA at first use | V12, 220003-220004 | Introduction, paraId 3A1F0C22 | done |
| R1.2 | SCIENCE | comment: the reviewer asks to call the effect "modest" | V12, comment on "a large effect" | Discussion 4.1 | waiting on the author |
| R2.1 | ANALYSIS | new analysis: adjust the primary model for the outcome's baseline value | spec A1; V13 | Results 3.1, Table 2 | spec approved |
| R2.2 | REBUTTAL | letter only: the sample size came from the trial's power calculation | none | Methods 2.1 | drafted |

| Class | Covers | Action |
| --- | --- | --- |
| WRITING | Wording, clarity, missing definitions, typos, formatting the journal asks for | Minimal tracked edit ([preferences](preferences.md#comments-or-edits)) |
| SCIENCE | A claim, number, interpretation, limitation or framing the point asks to change | A comment for the author; a tracked edit for review only when the author approves it or `policy.science_changes` in `manuscript.json` is `tracked_for_review`. Numbers change only with approval or a recomputation |
| ANALYSIS | A new or changed analysis, table or figure | Section 5, then tracked edits to the text that reports it |
| REBUTTAL | A request the author declines, or answers without changing the text | The author decides; the letter explains why, with evidence |

- Classifying a SCIENCE, ANALYSIS or REBUTTAL point is the author's call. Propose a class, and
  list the doubtful ones as numbered decisions with a recommendation.
- Start each edit's `purpose` with its point (`"purpose": "R1.1: define HPA at first use"`). The
  build manifest then ties revision ids to points; copy them into the table after the release.
- A point may need edits in several places; give every location.
- Record a ruling on a whole category ("no further covariates") in `DECISIONS.md`.

## 4. Make the changes

Work in ordinary passes: `msw.py pass new --slug "reviewer writing"`, then build, verify, proof,
review and release ([release](release.md)). Keep writing points and approved science changes in
separate passes, so the author can review them by version.

**The examiner pattern.** *Observed* in the thesis's examiner round: each examiner item became a
tracked edit with a comment beside it, or a comment alone (about half of them). The comment
quotes the examiner's point and names who raised it, then states the action, starting with
"Accepted and corrected" for a fix; a comment-only item explains or rebuts. Use this form
whenever the readers will check each point inside the document: examiners, a committee or a
supervisor. The comment's anchor is matched against the source's text, so anchor it on the
phrase the edit changes or the sentence that holds it, never on the inserted words. A synthetic
pair of ops:

```json
{"id": "X1.4", "op": "replace", "para": "3A1F0C22", "before": "activity of the ", "old": "HPA axis",
 "new": "hypothalamic–pituitary–adrenal (HPA) axis", "after": " in", "purpose": "X1.4: define HPA at first use"},
{"id": "X1.4c", "op": "comment", "para": "3A1F0C22", "anchor": "activity of the HPA axis",
 "text": "[Examiner 1, point 4] \"Define HPA at first use.\"\nAccepted and corrected: HPA is now defined at its first use.",
 "purpose": "X1.4: record the action"}
```

When the author must decide a point, write the comment alone and end it with the question.

## 5. Analyses requested by reviewers

*Observed* in the journal project: the reviewer-response phase went from reading the report to
analysis specs, delegated literature searches, checks of what came back, recomputation, and then a
tracked version.

1. **Spec first.** For each ANALYSIS point write `A<n>.md` in the round folder: the point
   (quoted), the question in plain words, the data files with their hashes, the method (outcome,
   predictors, covariates, test, software), the outputs (which table, figure and numbers), and
   which sentences each possible result would change.
2. **Confirm the intent with the author** before running anything: restate the analysis in plain
   words and wait for a yes ([preferences](preferences.md#numbers-and-science)). *Observed:* twice
   an agent ran a different analysis from the one intended.
3. **Recompute from the data on disk** in a new folder, `04_Analysis/<workstream>/<date>_<point>/`,
   with the script and its outputs. Reproduce the reported result first, so a difference comes
   from the requested change and not from the pipeline.
4. **Validate handoffs.** When the author runs another model or tool (a literature search, a
   second analysis), write the spec and copy-paste prompts and do not launch your own agents for
   that work ([preferences](preferences.md#delegation-to-other-models)). Treat what comes back as
   unverified: recompute each number, check each cited paper with `msw.py refs pubmed` and the
   claim loop ([reference audit](reference-audit.md)), and list what could not be confirmed.
5. **Report, then edit.** Show the results and the proposed text changes. Numbers enter the
   manuscript only after approval, as tracked edits in their own pass; match every
   `science_guard` item to that approval. New or changed figures follow [figures](figures.md).

## 6. Choose tracked or highlighted markup

Read the `revision` block of the journal profile ([journal profiles](journal-profiles.md)):

| `markup` | Send |
| --- | --- |
| `track_changes` | The tracked copy |
| `either` | The tracked copy, unless the author prefers highlights |
| `highlight_or_colour` | A highlighted copy; add the tracked copy only when `track_changes_accepted` is `yes` |
| missing or `null` | The house default, `track_changes`; confirm it against the journal's revision instructions or with the author, and record the answer in `DECISIONS.md` |

`track_changes_accepted: "unverified"`, as in the shipped Journal of Endocrinology example, means
nobody has confirmed that the journal takes Word's tracked changes: send the highlighted copy and
ask the author whether to add the tracked one. `response_letter: "required"` makes the letter
mandatory. Add a clean copy (every change accepted, no highlights) when the journal asks for one.

**The tracked copy** shows every change since the reviewed version. If the author reviews the
round's passes without accepting them, the newest version is that copy. If changes were accepted
between passes, no file holds them all: the author makes the copy in Word with Review > Compare
(original: the reviewed version; revised: the final clean version) and saves it under a new name.
Check it before sending: `msw.py drift "<final clean>.docx" "<compared>.docx"` should report no
change, and `msw.py views` on it should give the reviewed text as the rejected view.

**The highlighted copy.** A `format` op matches the source's text, so text cannot be highlighted
in the pass that inserts it. The highlights go on in a separate pass after the author has
accepted the revision:

1. The author accepts every change in the final version and saves; register the save with
   `msw.py author-save` (state `author accepted`). This is the clean copy.
2. List what changed since the reviewed version:
   `msw.py drift "<reviewed>.docx" "<clean>.docx" --json "<round folder>/drift_for_highlight.json"`.
3. Claim a pass (`msw.py pass new --slug "highlight changes"`) and write one `format` op per
   changed span, located in the clean copy's text, with `"set": ["highlight"]` (yellow by default;
   `"highlight": "green"` picks another colour):
   `{"id": "H1", "op": "format", "para": "3A1F0C22", "before": "activity of the ", "text": "hypothalamic–pituitary–adrenal (HPA) axis", "set": ["highlight"], "purpose": "highlight: X1.4"}`.
   Highlight whole changed phrases, and split a span at a citation or other field, which a format
   op cannot cross. Deleted text leaves nothing to highlight; name substantial deletions in the
   response letter.
4. Build, verify, proof and release as usual; the gate's `formatting` WARN lists the declared
   highlights. The author opens the release in Word, accepts the formatting changes and saves it
   under a new name as the highlighted copy; register that save with `author-save`.
5. `msw.py lint` reports each highlight as `format.highlight`, which is expected on this copy
   only. Do not add the rule to the profile's `lint.disabled_rules`: it would hide stray
   highlights in every other version.
6. The next round starts without highlights: its first pass removes them with `format` ops
   carrying `"unset": ["highlight"]`, or the author clears them in Word and the save is
   registered.

## 7. The response letter

A synthetic skeleton; angle brackets mark what to fill in:

```text
Response to the reviewers
Manuscript <journal number>: "<title>"

We thank the editor and the reviewers for their careful reading and helpful comments, and have
revised the manuscript accordingly. Each comment is quoted below, followed by our response and the
location of any change. Page and line numbers refer to the <highlighted | clean> copy of the
revised manuscript.

Editor
E1. "<the editor's comment, quoted exactly>"
Response: <what was done, or why not>.
Change: <section>, page <p>, lines <a>-<b>.

Reviewer 1
R1.1. "<comment>"
Response: We agree and have defined the abbreviation at its first use.
Change: Introduction, page 3, lines 52-53: "hypothalamic–pituitary–adrenal (HPA) axis".
```

- Answer every point in the report's order, under its triage number, including those declined:
  thank the reviewer, explain with evidence or citations, and say what was done instead, if
  anything.
- Give the section first, then page and line in the copy named at the top. Take them from the
  line-numbered proof of that copy (`msw.py proof`; LibreOffice shows the line numbering the
  document sets), then have the author confirm them in their Word PDF, because LibreOffice's line
  breaks and pages can differ from Word's. Update them after the last pass.
- Keep revision ids and paraIds out of the letter; they stay in the triage table.
- Every number in the letter must match the manuscript and the recomputed outputs.
- Write in the author's register: courteous, conventional and specific, one thank-you rather than
  praise in every answer, no promotional words. Run the humanizer pass on the drafted prose
  ([QA passes](qa-passes.md#6-humanizer)). The author edits and sends it.

## 8. The cover letter

A synthetic skeleton for a revision:

```text
<date>

Dear Dr <editor's surname>,

Re: Manuscript <journal number>, "<title>"

Thank you for the opportunity to revise our manuscript for <journal>. We have addressed each
comment from the editor and the reviewers; our point-by-point response is attached. The main
changes are:
1. <main change, with its section>
2. <new analysis, and whether it changed any conclusion>

<statements the journal requires with a revision, from the profile's policies and declarations>

We hope that the revised manuscript is now suitable for publication in <journal>.

Yours sincerely,
<the author's full name from manuscript.json>
<affiliation and contact details from title_page.json>
```

- Sign with `author.name` from `manuscript.json`; never invent a name, degree or title.
  *Observed:* one draft signed the author with an invented surname.
- For a first submission, replace the change list with the study's question, main finding and fit
  with the journal, plus the statements the profile lists.
- For a thesis examiner round, the equivalent is a short note to the committee or graduate office
  that lists the points and where they were addressed; use the school's own form if it has one.

## 9. Package and report

Send the response letter, the cover letter and the manuscript copies chosen in section 6, with any
changed figures and tables, following [submission packaging](release.md#submission-packaging),
including its final drift check against the reviewed version. Report to the author: the triage
counts by class and status, the copies made with each check's result, and the numbered decisions
still open.

# The author's working contract

These are the author's standing rules for any agent working on a manuscript project. They were
distilled from the author's corrections and approvals over a thesis (about 90 versions) and a
journal article (about 30 versions). Items marked *Observed* record what happened in those
projects; the rest is this skill's synthesis. The project's own `AGENTS.md` and
`06_Project_Docs/DECISIONS.md` override this page.

## Change control

1. Make every manuscript change as a Word tracked change built by `msw.py build`. There is no
   untracked mode unless the author asks for one.
2. Keep diffs minimal. Change the smallest span that does the job; never delete and re-insert a
   sentence to change one word. When the gate reports redline noise, fix the spec.
3. Preserve what is there: earlier revisions, comments, citation fields, italics and spaces. The
   rejected view of the new version must equal the source.
4. Put tracked changes and comments under `author.revision_name` from `manuscript.json`. Never use
   a model or tool name, and never guess a name. *Observed:* the author corrected this twice,
   weeks apart, asking for every comment and tracked change to carry their own name.
5. Build on the author's newest save, registered with `msw.py author-save`. Word renumbers
   revision ids, comments and media when it saves, so re-measure ids with `msw.py inspect` and
   match comments by anchor text, thread and date (`msw.py comments`), never by id.
   *Observed:* matching by id once made an agent report far more author changes than the author
   had made.
6. Do not edit field results (table of contents, cross-references, page references) and do not
   insert new fields. Where a value such as a page number must appear, measure it and write it
   as plain text. *Observed:* the author asked for such values to be typed in rather than
   inserted as fields.
7. A supervisor or co-author receives a tracked copy unless the author says otherwise. A clean
   PDF is a separate export.

## Comments or edits

Classify every finding before acting on it.

| Class | Covers | Action |
| --- | --- | --- |
| WRITING | Spelling, grammar, punctuation, word choice, clarity, consistent terms, with no change in meaning | Minimal tracked edit; the comment that raised it goes away |
| SCIENCE | Numbers, statistics, test names, direction or strength of a claim, gene, protein or cell-line names, method facts, figure content | Comment anchored to the phrase; edit only with the author's approval or a recomputation from data |
| REFERENCE | Citations, bibliography, EndNote fields, reference-list comments | Hands off; PMID placeholders only; list for the author |
| AUTHOR-DECISION | Choices with more than one defensible answer: framing, scope, what to cut, which dataset | Ask with numbered options and a recommendation |

- The author wants to review only science and decision comments. *Observed:* the author asked
  for writing-only findings to be fixed as edits instead of being left as comments.
- Anchor each comment to the sentence or phrase it concerns, never a whole paragraph or section.
- When the author asks for an evaluation, report first and edit nothing until they pick.
- When the author widens the scope (for example, asking for the science comments to be addressed
  for their review), make those changes as tracked edits. Numbers, statistics and directions
  still change only with the author's approval or a recomputation.
- When the author rules out a whole category (for example, adding more parameters), close every
  comment in it and record the ruling in `DECISIONS.md`.

**The V60 to V61 lesson (observed).** In one thesis pass an agent applied about 39 "copyedits"
that changed scientific meaning: a direction word, a statistical-test name and a cell-line name.
The next version had to turn them back into comments. What worked afterwards was a double gate:

1. the reviewer model labels each proposed fix SAFE_WRITING or NEEDS_AUTHOR;
2. a deterministic guard reclassifies as SCIENCE any WRITING fix that changes a digit, a
   statistics term, a gene-like token or a negation or direction word.

Apply both before building. The build gate repeats the second check as its `science_guard` WARN,
which lists every changed, inserted or deleted paragraph where such a token was gained or lost;
each one needs the author's approval. It does not see negations and hedges, so the reviewer's
labels still matter. See [QA passes](qa-passes.md).

## Comment replies

1. Read every thread in the author's newest save (`msw.py comments <file>` lists each thread
   with its anchor, replies and done state). The author's replies are instructions.
2. Carry out each reply as tracked edits, in the same pass as other work when it fits.
3. Close the thread with a `resolve_comment` edit, one per thread, as [revision](revision.md)
   describes, and list any thread you could not close. The tools cannot write a reply into the
   author's thread; an answer goes in as a new comment on the same phrase, or in the report.
4. If a reply is ambiguous, ask with options; do not guess.
5. Do not re-raise an item the author has settled, including rulings to leave something as it
   is without comment.

## Citations and EndNote

- Hands off EndNote. Citation fields and the bibliography are atoms: never edit inside them, move
  a citation to another clause or regenerate the bibliography. The gate checks the citations.
- New citations go in as tracked in-text placeholders such as `[PMID: 12345678]`, each verified
  against PubMed. The author converts them in EndNote.
- Leave reference-formatting comments to the author.
- After every version, hand back the Word steps in this order: update EndNote citations and the
  bibliography, then the table of contents, then static page numbers. *Observed:* the author
  accepted this division every time.

Details: [EndNote](endnote.md) and [reference audit](reference-audit.md).

## Versions and the newest file

- Each pass produces the next integer version, claimed with `msw.py pass new`. Nothing is
  overwritten, and numbers keep counting across journal retargets.
- Finding the newest version was a recurring question. Answer it with `msw.py status`, and
  put the newest path at the top of every report. Items that stay open across versions go in
  with `msw.py outstanding add`, so `status` and the README show them.
- The author often runs several sessions at once. Claim the number before building; see
  [parallel sessions](workspace.md#parallel-sessions).

## Independent verification

- Before presenting a version, have the reviewer configured in `manuscript.json` (`reviewer.tool`,
  `reviewer.model`, `reviewer.effort`) check it. The author's default has been a model from a
  different family at high or maximum effort. If the configured model fails, use the next best,
  and say so in the report.
- The reviewer must read the artifacts: the .docx or its views, the proof pages, the figures and
  the edit spec. A review of the agent's own summary is not verification. *Observed:* one
  "verified" report rested on a reviewer that had seen only the agent's summary.
- Ask for an even-handed verdict backed by evidence. *Observed:* a reviewer told to default to
  refuting dismissed real defects.
- Reviewer approval does not replace looking at the proof or the figure yourself.
- Expect several rounds of re-checking. Each round re-reads the files.
- Treat handoffs from other models or sessions as unverified claims; recompute from disk.

Details: [verification](verification.md).

## Asking and reporting

- Ask with numbered options and mark the recommended one. The author often replies with just
  the option number, so keep the numbering stable within a report.
- End every report with the newest path, one line of check results, the author's Word steps,
  numbered decisions and a count of items waiting on the author. See the
  [report format](release.md#report-to-the-author).
- Use plain language. Define a method or label in one plain sentence when it first appears, and
  say what an analysis shows and what it does not. *Observed:* two method labels used without
  explanation in a figure legend each prompted a clarifying question.
- For figures, lead with the images and keep prose short.

## Figures

- Rebuild figures in code with the nature-figure skill; see [figures](figures.md).
- Keep figures text-light: the figure legend (caption), source notes and method text stay out of
  the artwork. Put the explanation in the legend and the procedure in Methods. Where the method
  must be visible, use a short plain label.
- Mark significance only where a comparison is significant; leave non-significant comparisons
  unmarked. Drop within-arm P values when the between-arm test is primary. Name the
  error-bar type (for example, SEM) in the legend.
- For a redesign, render 3 or 4 genuinely different variants at true size, give each a stable id
  (A, B, C), recommend one, and build only the one the author picks.
- No overlapping text or objects, visible panel labels, and panels of equal size where the layout
  allows. The rendered result is the gate, not the code.
- Check scientific content in a figure (sequences, species, candidates) against sources, and read
  the artwork before changing any panel letter.
- Figure changes reach the manuscript only after approval, as a tracked picture swap plus tracked
  legend edits; the figure version is then promoted and released with the pass
  (`release --figures`).

## Numbers and science

- Reproduce reported numbers from the data before rewriting them, and compare them with the last
  submitted text. Log discrepancies; never fix them silently.
- The author owns statistical framing: the primary hypothesis, multiplicity handling and which P
  values appear. Propose; the author decides. Do not add qualifiers the design does not call for,
  such as sentences saying a result did not survive correction.
- Before running an analysis, restate what it will do and get a yes. If a method changes, explain
  why in plain words. *Observed:* twice an agent ran a different analysis from the one intended.
- Stay within approved claims. No sample size, fraction, fold change or direction changes against
  the approved baseline without approval.

## Language and voice

- Spelling variety and first-person policy come from `manuscript.json`; see
  [house style](house-style.md).
- Humanize only new prose, as small tracked edits, and keep professional warmth: no quips or
  casual asides.
- Emails and letters are courteous and conventional and are signed with the author's full name
  from `manuscript.json`. *Observed:* one draft signed the author with an invented surname.

## Files, git and Word

- Never delete. Archive by moving into `99_Archive/<date>/<Reason>/` with `msw.py archive plan`
  and `archive apply`. See [workspace](workspace.md).
- Never run `git switch`, `git checkout`, `git restore`, `git reset --hard`, `git clean`,
  `git stash` or `git rm` in a manuscript folder. To move a branch pointer, use `git branch -f`,
  which leaves files alone. *Observed:* an interrupted branch switch removed about 33 deliverable
  files, which were then restored from the last commit.
- Commit only when the author agrees, once per release.
- Word is the author's review surface. Never drive the author's running Word. Read a locked file
  from a copy. Native Word checks run only through `msw.py word-qa`, which opens a read-only copy
  in its own hidden Word instance.
- Save guidelines, specs and notes as files in the project, not only in chat.

## Dictated input

Long messages are often dictated. Expect misheard gene and molecule names, misheard author-year
citations, misheard names of people and filler words.

1. Map each misheard term to the project vocabulary: the abbreviation table, the gene and protein
   names in the manuscript, and the reference library.
2. If the mapping is not obvious, ask, with the likely candidates as numbered options.
3. Never write a literal mishearing into the manuscript.

## Delegation to other models

- When the author says they will run another model themselves (for example, for a literature
  search), do not launch your own agents for that work. Write a detailed spec and copy-paste
  prompts, then audit what comes back.
- Ask before any expensive fan-out. Cap parallel agents and keep passes resumable after rate
  limits.

## Record decisions

Add each decision the author makes to the table in `06_Project_Docs/DECISIONS.md` (written by
`msw.py init`), newest at the top as the file says, so it is not argued again. Never rewrite an
old row; add a dated correction instead. A synthetic row:

```markdown
| 2026-01-12 | Multiplicity for the three predefined hypotheses: no adjustment in the main text; adjusted values in Supplementary Table 2 | 1 no adjustment (recommended), 2 Holm, 3 FDR | Author (chose 1) | Results 3.2, Table 2 |
```

## Settle at bootstrap

Ask these once, store the answers in `manuscript.json`, and record the reasons in `DECISIONS.md`
(see [bootstrap](bootstrap.md)):

1. Spelling variety (`language.spelling`): `en-US`, `en-GB` or `en-CA`. The thesis used Canadian
   spelling. No standard was ever stated for the journal manuscript, which had mixed spellings.
2. Revision author (`author.revision_name`, `init --revision-name`): the author's own name, or an
   agent label for co-author review. The author asked for their own name on the thesis.
3. Reviewer (`reviewer`): tool, model and effort (`init` writes `codex`, no model, `high`). Probe
   it once, so that a model that fails on the account is found early.
4. Scope for science changes (`policy.science_changes`, `init --science-changes`): `comments`
   (the default) or `tracked_for_review`, tracked edits for the author's review.

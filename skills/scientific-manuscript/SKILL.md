---
name: scientific-manuscript
description: Create, revise and release scientific manuscripts and theses in Word with EndNote citations. Bootstraps a manuscript project (even from an empty folder), drafts in the author's house style, makes versioned tracked-change revisions checked by a verification gate, runs QA passes and reference audits, adapts to a journal and prepares submission packages. Use for journal-article or thesis work in .docx; not for plotting alone (use nature-figure) or prose editing outside a manuscript project.
---

# Scientific manuscript

Produce and revise a scientific manuscript the way the author works: one project folder, numbered
versions that are never overwritten, every change as a minimal Word tracked change, EndNote fields
left intact, a gate that proves each version before anyone sees it, and a short report that ends
with the decisions only the author can make.

`msw.py` below and in every reference means `python <skill>/scripts/msw.py`, where `<skill>` is
this skill's folder. Run it from the project folder; every command has `--help`.

## Start every task here

1. Locate the project: walk up from the working folder to `manuscript.json`, then read the
   project's `AGENTS.md` and `PROJECT_INDEX.json`. With no project and a request to start one,
   follow [bootstrap](references/bootstrap.md). A project's own rules override this skill.
2. Run `msw.py status` before editing. It reports the current version and whether its files
   still match their hashes, Word files saved but not registered, Word lock files, passes in
   flight, open items and unfinished ledger operations; finish an interrupted command by running
   it again ([release](references/release.md#if-it-stops-part-way)). Register an author's Word
   save with `msw.py author-save` and build on it; re-measure revision ids after every Word save.
3. Apply the [author's working contract](references/preferences.md): tracked minimal edits under the
   configured name, writing fixes as edits and science questions as comments (tracked edits for
   review only when `policy.science_changes` is `tracked_for_review`), EndNote untouched,
   independent review before presenting, numbered options with a recommendation.

## Route the request

| Request | Read |
| --- | --- |
| Start a project, adopt an existing folder, organize files | [bootstrap](references/bootstrap.md), [workspace](references/workspace.md) |
| Write a new manuscript or section from data and results | [drafting](references/drafting.md), [house style](references/house-style.md) |
| Make the next version with edits, comments or replies | [revision](references/revision.md) |
| Answer reviewer or examiner reports; response and cover letters | [reviewer response](references/reviewer-response.md) |
| Grammar, spelling, humanizer, nomenclature, logic or reviewer-style QA | [QA passes](references/qa-passes.md) |
| PMIDs, EndNote library, Cite While You Write, bibliography problems | [EndNote](references/endnote.md) |
| Check that references exist and support the claims | [reference audit](references/reference-audit.md) |
| New or revised figures, legends, picture swaps, journal figure files | [figures](references/figures.md) |
| Retarget or check against a journal's rules | [journal profiles](references/journal-profiles.md) |
| Check a version, render a proof, run native Word QA, independent review | [verification](references/verification.md) |
| Release a version, update indexes, archive, package for submission | [release](references/release.md) |

## Non-negotiables

- Never overwrite or delete. Every build is a new file with the next version number; files leave
  view only by `msw.py archive` (a move into `99_Archive/<date>/<Reason>/` with hashes in the
  ledger). Never run `git switch`, `git checkout`, `git restore`, `git reset --hard`, `git clean`,
  `git stash` or `git rm` in a manuscript folder.
- Change a manuscript only with `msw.py build`. Never hand-edit `document.xml` or rewrite it with
  a general XML or docx library: they drop the CRLF inside EndNote payloads and the gate cannot
  prove what changed.
- Treat citation fields, the bibliography, pictures and other fields as atoms. Text may change
  around them, never inside them, and a citation may not move to a different clause.
- Present a version only after `msw.py build` (which gates itself) or `msw.py verify` reports PASS,
  the accepted view has been rendered and looked at, and an independent reviewer has read the
  artifacts themselves. State each check's result in the report.
- Numbers, statistics, gene names and the direction of any claim change only with the author's
  approval or a recomputation from the data; the gate's `science_guard` WARN lists every such
  change. When unsure, leave a comment instead of an edit.

## Tools

Standard library only (Python 3.10+). Commands write new files and refuse existing ones; only the
project commands rewrite their own index files and generated blocks. On Windows without long-path
support a path stops at 260 characters: keep the project root under 100 characters and slugs
short ([bootstrap](references/bootstrap.md#3-create-the-project)); `init`, `status` and
`pass new` warn about a longer root.

| Command | Use |
| --- | --- |
| `init`, `status`, `intake`, `author-save`, `pass new`, `release plan\|apply`, `archive plan\|apply`, `verify-project`, `outstanding add\|done`, `retarget` | Project lifecycle and open items ([workspace](references/workspace.md), [release](references/release.md)) |
| `figure add`, `figure register`, `figure promote`, `figure status` | Figure versions, hashes, promotion and the picture in the manuscript ([figures](references/figures.md)) |
| `new-manuscript`, `title-page` | Draft .docx from Markdown in house style ([drafting](references/drafting.md)) |
| `inspect`, `census`, `fields`, `views`, `comments` | Read a .docx: revisions, ids, citations, accepted and rejected views, comment threads |
| `extract`, `collect`, `build`, `verify` | Tracked edit passes and the gate ([revision](references/revision.md)) |
| `drift` | Final check of the file to send against the approved or last-submitted version ([release](references/release.md#submission-packaging)) |
| `wordcount`, `lint` | House-style and journal checks ([house style](references/house-style.md)) |
| `refs placeholders\|pubmed\|crossref\|census\|bib-audit\|claims\|claims-report\|library-map\|temp-citations` | PMIDs, PubMed and Crossref evidence, bibliography and claim-support audits, EndNote temporary citations ([reference audit](references/reference-audit.md), [EndNote](references/endnote.md)) |
| `proof`, `word-qa` | LibreOffice proof of the accepted view; native Word checks ([verification](references/verification.md)) |

## Report back

End each pass with: the new file's path; one line of check results (gate, citations, reject view
equals source, proof, reviewer); the steps left for the author in Word (refresh EndNote, then the
table of contents, then page numbers); and numbered decisions with a recommended option. Explain
any method or label in plain words the first time it appears.

For provenance and what is source-derived versus our synthesis, see
[source notes](references/source-notes.md).

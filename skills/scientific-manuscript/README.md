# Scientific Manuscript

**Write, revise and release a journal paper or thesis in Word without losing track of a single
change.**

This Captain's Kit skill runs a manuscript the way its author works: one project folder, numbered
versions that are never overwritten, every change a minimal Word tracked change under the
author's name, EndNote citations left alone, and a verification gate that proves each version
before anyone reads it. The agent follows [SKILL.md](SKILL.md) and its references; this guide
explains installation and use.

[Install](#install) · [Example prompts](#example-prompts) · [How a version is made](#how-a-version-is-made) ·
[Command line](#command-line) · [Requirements](#requirements) · [Guides](#guides)

## What it does

- **Bootstraps a project**, even in an empty folder: a numbered folder layout, `manuscript.json`,
  project rules for agents, indexes of versions and figures, and an append-only ledger. An
  existing folder is adopted by copying from it, leaving it untouched; it is restructured in place
  only when you ask and approve the plan.
- **Drafts** a manuscript or section from your data and results in the house style (title page,
  structure, statistics reporting, legends), written in a small Markdown dialect and built into
  a formatted .docx.
- **Makes the next version** from your latest Word save: writing fixes become tracked edits,
  science questions become comments, answered comment threads are closed, and each build is
  checked by the gate before it is written.
- **Runs QA passes**: grammar and spelling, the humanizer on new prose, nomenclature,
  abbreviations, logic and reviewer-style reads.
- **Handles citations** the way you do: new text cites `[PMID: n]`, verified against PubMed, and
  you convert in EndNote. When asked, it audits references for identity, metadata and claim
  support, and hands you a replacement table.
- **Manages figures** with the `nature-figure` skill: each figure version is registered with its
  file hashes, promoted when you approve it, swapped into the .docx as a tracked change and
  copied into the package by the release that names it.
- **Answers reviewers and examiners**: splits a report into numbered points, triages each one,
  makes the fixes as tracked edits (with a comment quoting the point when examiners check them in
  the document), runs requested analyses only after you confirm them, prepares the tracked or
  highlighted copy the journal asks for, and drafts the response and cover letters for you to
  edit.
- **Adapts to a journal** through a profile of its rules (word limits, abstract, title page,
  references, figures, declarations) and checks the manuscript against it.
- **Releases** a version: copies it into `Versions/VNN/` and the current-deliverable folder,
  updates the indexes and the ledger, and archives superseded package files by moving them,
  never deleting.

## Install

Requires Python 3.10 or newer. From a clone of Captain's Kit:

```sh
python scripts/install.py --skill scientific-manuscript --project /absolute/path/to/manuscript --host codex
python scripts/install.py --skill scientific-manuscript --project /absolute/path/to/manuscript --host claude
```

Install `nature-figure` the same way (`--skill nature-figure`) if the manuscript has figures. The
`humanizer` skill is recommended for the QA passes: the humanizer pass on new prose and on drafted
letters delegates its style judgement to it.

```sh
python scripts/install.py --skill humanizer --project /absolute/path/to/manuscript --host codex
python scripts/install.py --skill humanizer --project /absolute/path/to/manuscript --host claude
```

Without it, that pass applies the rule table in the [QA passes guide](references/qa-passes.md#6-humanizer)
and the report says the skill was missing. The installer copies each skill into the project
(`.agents/skills/` for Codex, `.claude/skills/` for Claude Code), never overwrites an existing copy
and does not run anything. Installing first is fine: a folder that holds only these host folders
still counts as empty when the skill bootstraps the project there.

## Example prompts

Use `$scientific-manuscript` in Codex or `/scientific-manuscript` in Claude Code:

```text
$scientific-manuscript bootstrap a manuscript project here for the Journal of X
$scientific-manuscript make V31 from the author's latest save with these edits: ...
$scientific-manuscript draft the Methods from these data and the analysis notes in 04_Analysis
$scientific-manuscript audit the references
$scientific-manuscript retarget to journal Y
$scientific-manuscript answer the reviewer reports in 01_Manuscripts/Incoming/, triage first
$scientific-manuscript release V31
```

Also useful:

```text
/scientific-manuscript which file is the newest, and did I save anything since V30?
/scientific-manuscript report only: check V31 against the journal profile, no edits yet
/scientific-manuscript show me 3 options for Figure 2B and recommend one
```

The skill ends each pass with the new file's path, one line of check results, the steps left for
you in Word (refresh EndNote, then the table of contents, then page numbers) and numbered
decisions with a recommended option, so you can answer by number.

## How a version is made

`msw.py` below means `python <skill folder>/scripts/msw.py`, run from the project folder.

1. `msw.py status` finds the current version and notices a Word save that is not registered yet;
   `msw.py author-save` registers it.
2. `msw.py pass new --slug "<words>"` claims the next version number and creates its pass folder.
3. The edits are written as a spec: replacements, insertions, comments, closed comment threads,
   rewrites, picture swaps.
4. `msw.py build` applies them as tracked changes and runs the gate. It writes the file only when
   the gate passes: the rejected view equals the source, every formatting change is tracked, the
   accepted view differs by exactly the declared edits, and citations, links, bookmarks, fields
   and pictures are intact. Changed numbers or statistics are flagged for your approval.
5. `msw.py proof` renders the accepted view to PDF to be looked at; `msw.py word-qa` compares
   Word's own accept and reject when Word is available.
6. An independent reviewer reads the files themselves, not a summary.
7. `msw.py release plan`, then `release apply`, records the version, updates the indexes and
   ledger, copies into the package the PDF and PNG of each figure version named with `--figures`
   (or in `figures_changed` in `pass.json`) and of any promoted version the build newly shows, and
   archives the superseded package files.
   `msw.py verify-project` checks the result, and one git commit per release follows with your
   agreement.

Before a submission, `msw.py drift` compares the file to send with the approved or last-submitted
version and lists every changed paragraph, number, statistic, symbol and direction word, and any
change in citation or picture counts, for you to confirm.

## Command line

Run `msw.py --help`; every command has its own `--help`. Commands write new files and refuse
existing ones.

| Commands | Use |
| --- | --- |
| `init`, `status`, `intake`, `author-save`, `pass new`, `release`, `archive`, `verify-project`, `retarget` | Project lifecycle |
| `outstanding add`, `outstanding done` | Open items shown in the project's README and status page |
| `figure add`, `figure register`, `figure promote`, `figure status` | Figure versions, hashes and promotion |
| `new-manuscript`, `title-page` | Build a .docx from `manuscript.md` and `title_page.json` |
| `inspect`, `census`, `fields`, `views`, `comments` | Read a .docx: revisions, ids, citations, accepted and rejected views, comment threads |
| `extract`, `collect`, `build`, `verify` | Tracked edit passes and the gate |
| `drift` | Final check against the approved or last-submitted version |
| `wordcount`, `lint` | House-style and journal checks |
| `refs placeholders`, `refs pubmed`, `refs crossref`, `refs census`, `refs bib-audit` | PMID placeholders, PubMed evidence, bibliography and payload metadata |
| `refs claims`, `refs claims-report`, `refs library-map`, `refs temp-citations` | Claim-support batches and review, EndNote library mapping, temporary citations |
| `proof`, `word-qa` | LibreOffice proof of the accepted view; native Word checks |

## Requirements

- **Python 3.10+**, standard library only.
- **A short project path on Windows.** Without long-path support Windows stops at 260
  characters, and pass folders, builds and proofs sit several levels deep. Keep the project folder's
  full path under 100 characters, or have an administrator enable long paths; `init`, `status`
  and `pass new` warn about a longer one.
- **LibreOffice** (optional) for PDF proofs of the accepted view, run on a persistent per-user
  profile outside the project. `pypdfium2` or PyMuPDF (optional) turns proofs into page images.
- **Microsoft Word** (optional, Windows) for native accept and reject comparisons through
  `word-qa`, which uses its own hidden Word instance and a read-only copy.
- **EndNote desktop** for Cite While You Write. The author converts `[PMID: n]` placeholders and
  refreshes the bibliography; the skill does not drive EndNote unless explicitly asked.
- **Network access** for the PubMed and Crossref checks. Set `MSW_CONTACT_EMAIL` to your contact
  address and, if you have one, `NCBI_API_KEY`.
- **`nature-figure`** for figure work, with its own plotting and PDF-audit dependencies.
- **`humanizer`** (recommended) for the humanizer QA pass on new prose and letters; installed as
  shown above. Without it the pass uses the skill's own rule table.
- **Git with Git LFS** (recommended) for one commit per release.
- An independent reviewer, such as another model's command-line tool, set in `manuscript.json`.

## What it will not do

- Overwrite or delete a file, or run commands that discard work in the manuscript folder.
- Edit inside EndNote fields or the bibliography, or regenerate it.
- Change numbers, statistics, gene names or the direction of a claim without your approval or a
  recomputation from the data.
- Commit without your agreement, or push or submit anything unless you ask.

## Guides

| Task | Guide |
| --- | --- |
| Start or adopt a project | [bootstrap](references/bootstrap.md), [workspace](references/workspace.md) |
| Draft from data | [drafting](references/drafting.md), [house style](references/house-style.md) |
| Make the next version | [revision](references/revision.md), [preferences](references/preferences.md) |
| Reviewer or examiner reports, response and cover letters | [reviewer response](references/reviewer-response.md) |
| QA passes | [QA passes](references/qa-passes.md) |
| Citations and EndNote | [EndNote](references/endnote.md), [reference audit](references/reference-audit.md) |
| Figures | [figures](references/figures.md) |
| Journal rules | [journal profiles](references/journal-profiles.md) |
| Checks and review | [verification](references/verification.md) |
| Release and submission | [release](references/release.md) |

Provenance, evaluation evidence and maintenance notes are in
[source notes](references/source-notes.md).

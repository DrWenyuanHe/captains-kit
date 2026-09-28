# Source notes

## Where the workflow comes from

This is a locally authored skill. It was distilled on 2026-09-24 from two of the author's own
projects and from the author's corrections recorded in agent sessions on them:

- **The author's thesis QA project**: tracked-change editing passes on a long thesis, the QA pass
  catalogue and voice rules, the humanizer passes, and the reference audits (bibliography metadata
  against Crossref, claim support against abstracts, replacement tables, follow-through).
- **A journal-manuscript rework project**: numbered versions and the release ritual, the workspace
  layout and indexes, the conversion of PMID placeholders to EndNote Cite While You Write,
  tracked wrappers around native citation fields, the figure pipeline built on `nature-figure`,
  the journal profile and journal figure exports, and Prism reconstruction.

The rules were distilled from the author's feedback and corrections in those sessions. They are
written as paraphrased rules: no session message is quoted, and no manuscript or thesis text,
results, participant data, titles, co-author names or library files are included. The folder
and file-naming patterns follow the projects' layout, with invented names in the examples.

The worked examples are invented studies with their own design and numbers: the example
manuscript and the house-style and drafting examples describe a balance-training trial in older
adults with the fictional serum marker L-38; other pages and the tests use "marker Q-17" and
"GENE1". Before publishing a change to this skill, compare its text with the private manuscript
versions and the thesis (seven-word runs, lowercased, punctuation removed). The only shared runs
allowed are standard reporting phrases, such as a significance-threshold sentence, the
significance key `*P < 0.05, **P < 0.01 and ***P < 0.001` or the journal's word-count wording
(excluding references and figure legends).

## Source-derived

These describe what was observed in the two projects. They are practice, not general evidence.

- The EndNote anatomy in [EndNote](endnote.md): the three payload forms, per-chunk decoding, the
  bibliography field spanning paragraphs, the two link forms before and after a Word save,
  bookmark numbering and temporary citations. Read from the projects' documents with read-only
  scripts.
- The failure modes F1 to F16. The file-side failures were reproduced from archived checkpoints.
  EndNote and Word GUI behaviour (Find Reference Updates missing locators, the matching dialog,
  Track Changes with CWYW, Instant Formatting rewriting tracked structure) comes from the
  project's own workflow notes and was not reproduced independently.
- The reference loops in [reference audit](reference-audit.md): identity with archived PubMed
  evidence, rendered-bibliography metadata checks, claim-support batches with tiers, the
  replacement table and follow-through.
- The figure route in [figures](figures.md): frozen source data, one build per version, the gate on
  `nature-figure` auditors, legend verification against numbers-drawn CSVs, tracked picture
  swaps, CMYK exports with near-black set to solid black, and the failures that shaped them.
- The author's working contract in [preferences](preferences.md).

## Our synthesis

- The standard-library engine, the verification gate and the `msw.py` command line, which replace
  many per-version scripts with fixed paths, counts and paragraph numbers.
- The generalised project layout, index schemas (version 2) and write-ahead ledger.
- The payload metadata audit, which turns "do not trust Find Reference Updates" into a file check.
- Escalating claim flags to full text before they reach the author.
- The journal profile schema; per-figure export tiers, an explicit CMYK profile and file-size
  budgets as blocking checks; honouring auditor exit codes including `NOT AUDITABLE`.
- Calling `nature-figure` by name instead of keeping copies of its scripts.
- A decoder that prefers nested chunks, falls back to the outer blob and reports mismatches,
  because the chunk layout was observed rather than documented.
- Windows path-length safety: short working names inside pass folders (`VNN tracked.docx`,
  `proofs/p1/accepted view.pdf`), a per-user LibreOffice profile outside the project,
  extended-length paths inside the tools and a warning for project roots over 100 characters.
  These came from this skill's own end-to-end test, where a 171-character root broke the first
  build and proof, not from the source projects.

## Evaluation evidence

Measured on the source projects' files during development; the files are not part of the skill,
and the results describe those files, not manuscripts in general.

- **Corruption benchmark.** 19 realistic corruptions were made in a copy of one released
  manuscript: a citation field tracked-deleted, a citation moved to another clause, an unescaped
  ampersand, colliding revision ids, a changed display number, an edited field code, stripped
  `_ENREF` bookmarks, missing and stray package parts, a lost superscript, a lost `xml:space`,
  duplicate and orphan bookmarks, a runaway bibliography field, an untracked word change, a
  paragraph and a picture removed without tracking, an orphan comment reference, a deletion
  across half a citation, and `w:t` inside a deletion. The legacy verifier together with the
  legacy gate as documented caught 1 of the 19; the new gate failed all 19 and passed a legitimate
  control edit.
- **Real releases.** On 9 real release transitions (each released version against its source)
  the new gate raised no false failure. The legacy gate, run verbatim as documented, failed all 9,
  because its tag-balance count does not allow for self-closing paragraph-mark revisions.
- **Rebuilds.** The engine rebuilt five released versions (V25 to V29) from their edit specs. Each
  rebuild's accepted and rejected texts were identical to the released file's, and the gate
  passed.
- The library's unit tests (`tests/test_msw_*.py` in the repository) use synthetic documents only.

## External sources

Reviewed 2026-09-24:

- [EndNote CWYW keyboard commands for Word](https://docs.endnote.com/docs/endnote/2025/v1/windows/en/content/appendices/cwyw_keyboard_commands.htm):
  default shortcuts (Alt+2 inserts the selected citation; Update Citations and Bibliography has
  none). Shortcuts can be reassigned.
- [Journal of Endocrinology author guidelines](https://journals.bioscientifica.com/joe/pages/author-guidelines)
  (Bioscientifica): the journal behind the worked journal-profile example. Profiles hold original
  summaries with the source link and retrieval date, never the guideline text. The live page
  controls.
- The `humanizer` skill, imported into Captain's Kit from
  [blader/humanizer](https://github.com/blader/humanizer), used by the QA passes for new prose.
- The `nature-figure` skill, imported into Captain's Kit from
  [Yuan1z0825/nature-skills](https://github.com/Yuan1z0825/nature-skills), which owns figure
  design, plotting and the figure auditors.

Both skills are installed separately and referred to by name; this skill does not bundle them.
Reading these sources does not invoke another skill.

## Maintenance

- Re-verify a journal's rules from its live pages before every submission, and update the
  profile's retrieval date and conflicts.
- Re-check the EndNote shortcuts and CWYW behaviour after a major EndNote or Word update.
- Gaps found in `nature-figure` (uppercase panel labels in the alignment auditor, a
  non-overwriting writer, a warning band below the overlap threshold, discovery of installed
  scripts) belong upstream or in its recorded Captain's Kit adaptations, not in project copies.
- After editing this skill, run `python scripts/check.py --skill scientific-manuscript` and the
  `test_msw_*` unit tests, exercise the changed workflow on a synthetic project, repeat the
  comparison with the private sources described above, and record the edit with
  `python scripts/skills.py record scientific-manuscript --note "<what changed>"`.

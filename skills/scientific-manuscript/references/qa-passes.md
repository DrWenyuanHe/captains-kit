# QA and edit passes

These passes were proven on the author's thesis (92 numbered versions) and are adapted here for
journal articles. Each pass runs as a revision ([revision](revision.md)): it claims a version, writes
`edits.json`, builds and verifies. Thesis counts below are measured from that project; journal
bands and the adaptations are our synthesis (see [source notes](source-notes.md)).

## Common protocol

1. Extract the source: `msw.py extract SOURCE.docx --out PASS/qa/<pass> --min-chars 20
   --exclude-style Heading EndNoteBibliography`. Split the records by section (Abstract,
   Introduction, Methods, Results, Discussion, legends).
2. Give one subagent per chunk the pass rubric below, the project's `language` settings
   (`spelling`, `first_person`), [house style](house-style.md) and the chunk's records. Rewrite
   passes use one subagent per paragraph through `in/` and `out/` files instead.
3. Findings come back as spec ops (`replace`, `insert`, `delete`, `format`, `comment`), never XML.
   Each op carries `purpose` = `<pass>: <rule>`, and each comment starts with the pass tag, such
   as `[Nomenclature]`, because every revision carries the author's name.
4. Only high-confidence writing fixes become edits. Medium or low confidence, anything needing
   scientific judgement, and any change to a number, statistic, symbol or claim direction become
   comments. The gate's `science_guard` warning lists such changes after the build
   ([revision](revision.md), section 7); each listed item needs the author's approval or goes back
   to a comment. A comment names the sentence it concerns and quotes at least 20 characters of
   evidence.
5. Merge the fragments into one `edits.json`, drop overlaps (the engine refuses them), build and
   verify ([verification](verification.md)).
6. Compare the count with the pass's band. Far above it, the rubric is over-firing: review a sample
   before building. Zero findings on a long section deserves a second look.
7. After any pass where a model wrote text, run `msw.py lint` and the italics scan (pass 11)
   again; model rewrites reintroduce spelling-variety and italic errors.

Where a pass lists `msw.py lint` rules, run the lint first and give its findings to the subagents
as candidates; the lint reads the accepted view and writes nothing to the manuscript.

Protected everywhere: citation and other fields, the bibliography, text inside another author's
pending revision (edit it only when the pass requires), quotations, journal names, units, Greek
letters, numbers, statistics and identifiers.

## Catalog

Passes 1-9 keep the thesis numbering; 10-13 came from later thesis versions.

| # | Pass | Mode | Unit | Thesis volume | Journal band |
| --- | --- | --- | --- | --- | --- |
| 1 | Nomenclature | edit + comment | chunk | 96 findings: 65 edits, 24 comments | 0-30 |
| 2 | Figure and table references | comment (+ small edits) | chunk | 23 findings, 0 edits | 0-15 |
| 3 | Abbreviations | edit + comment | whole text | not recorded | 0-20 |
| 4 | Locale spelling | edit | chunk | 47 findings: 34 edits | 0-30 |
| 5 | Grammar and clarity | edit + comment | chunk | 37 findings: 16 edits | 10-40 |
| 6 | Humanizer | edit (+ comment) | paragraph | 56-74 paragraphs changed per round | new prose only |
| 7 | Citation accuracy | comment | citation | 65 papers rechecked | per audit |
| 8 | Logic consistency | comment | whole text | 30-100 comments | 5-25 |
| 9 | Multi-persona review | comment | whole text | 26-58 findings per persona | 5-20 per persona |
| 10 | Spacing lint | edit | deterministic scan | not recorded | 0-20 |
| 11 | Italics consistency | edit | deterministic scan | not recorded | 0-30 |
| 12 | Targeted polish | edit | named paragraphs | 1 paragraph or the abstract | as requested |
| 13 | Final house-style lint | report, then edits | whole text | not recorded | 0 before submission |

## Run order

1. Mechanical passes, so later passes read clean text: nomenclature (1), figure and table
   references (2), abbreviations (3), locale spelling (4), spacing (10), italics (11).
2. Grammar and clarity (5), then the humanizer (6) where it applies.
3. `msw.py lint` and the italics scan again, since model passes reintroduce errors.
4. Comment-only passes: citation accuracy (7), logic (8), multi-persona review (9).
5. The author's reply round ([revision](revision.md)), targeted polish (12) as requested.
6. Final house-style lint (13) and the full verification before any hand-off.

The mechanical passes may share one version; grammar, humanizer and reviewer passes each get
their own, so the author can review them by version.

## 1. Nomenclature

- Rubric: gene, mRNA, allele, locus and promoter symbols are italic; proteins, antibody targets and
  immunostains are roman. Human *GENE* / GENE, mouse and rat *Gene* / GENE, zebrafish *gene* /
  Gene. Gene cues: mRNA, transcript, knockout, promoter, qPCR, RNA-seq. Protein cues: protein,
  antibody, Western blot, ELISA, serum levels. Anatomical nuclei, methods, pathways, complexes and
  statistics abbreviations are not genes. Pick one form per symbol by whole-document counts.
- Edit: italic toggles (`format` with `set`/`unset` `italic`), one agreed spelling of a symbol.
- Comment: gene or protein ambiguous, placeholder symbols (FAM###, C#orf#, LOC, raw Ensembl IDs),
  official names to confirm.
- Lint candidates: `nomenclature.gene-italic` (roman gene-like symbols beside gene-context words).
- Validate: every italic toggle lands on the symbol only, never a plural "s" or a hyphenated suffix.

## 2. Figure and table references

- Build an inventory from the legends: figure numbers and panel letters. Check every "Figure N",
  "Figure NA-C" and "Table N" call-out against it, and every figure is cited in order.
- Edit: en dash in panel ranges, capitalization, a no-break space in "Figure N". Add the
  no-break space with a `replace` of the space itself (`"before": "Figure", "old": " ",
  "new": "\u00a0", "after": "2"`) or in a `rewrite`. A `rewrite` keeps an existing no-break space
  that the agent retyped as a plain one, so removing one takes a `replace` or `"exact_spaces":
  true` ([revision](revision.md#6-how-the-changes-are-written)).
- Comment: missing figure or panel, panel out of range, vague references ("the figure above"),
  uncited figures. Read the figure itself before changing any panel letter.
- Lint candidates: `figures.order`, `figures.uncited`, `figures.gap`, `figures.missing-legend`,
  `callout.fig-abbreviation`.
- Validate: write the inventory to `PASS/qa/figref_audit.json` even when nothing changes; every
  call-out in the accepted view resolves to a legend.

## 3. Abbreviations

- Scope for journals: the abstract on its own, then the main text; legends are self-contained and
  end with their own `ABBR, definition; ...` list. Check the journal profile for the rule.
- Edit: add a missing first-use expansion "full form (ABBR)", remove a redundant re-expansion,
  align inconsistent full forms, keep the abbreviation table in step with the text.
- Comment: abbreviations used once or twice (suggest spelling out), symbol and abbreviation
  conflicts. Never expand universal forms (DNA, RNA, PCR, ELISA, SD, SEM, ANOVA, units).
- Lint candidates: `abbrev.before-definition`, `abbrev.undefined`, `abbrev.redefined`,
  `abbrev.single-use`, `abstract.abbreviation`.
- Validate: after the build, the lint shows no abbreviation used before its definition.

## 4. Locale spelling

- The locale comes from `language.spelling` (`en-US`, `en-GB`, `en-CA`); ask the author if it is
  unset. Canadian: -ize with -our, -re and doubled l; international biomedical forms (estrogen,
  fetal, pediatric).
- Edit: single-word spelling fixes, with word boundaries ("color" never inside "colorectal").
- Comment: British biomedical forms (oestrogen, haem-, paediatric) once at first mention; serial
  comma inconsistency. Quoted titles and names keep their published spelling.
- Lint candidates: `msw.py lint --spelling en-CA` (or the project's variety) reports
  `spelling.mixed` words.
- Validate: the lint's `spelling.variety` summary shows one variety after the build.

## 5. Grammar and clarity

- Rubric: subject-verb agreement, articles, parallelism, run-ons and comma splices, apostrophes,
  clean wordiness swaps. Tense by section: Methods and Results past, Discussion present for
  established knowledge and past for own findings.
- Edit: at most 5 words per edit. Keep passive voice in Methods; never strengthen or weaken hedges.
- Comment: anything larger than 5 words, topic sentences, argument flow.
- Batch by section; use the rewrite loop when many paragraphs need small fixes.
- Validate: the gate's `minimality` check passes, and a sample of edits read in the accepted view
  says the same thing as before.

## 6. Humanizer

Delegate style judgement to the `humanizer` skill, within these limits. It is a separate skill,
recommended at install ([README](../README.md#install)); look for it in the project's
`.agents/skills/` or `.claude/skills/` folder, then the user-level skills folder. If it is not
installed, apply the rule table below on its own and say in the report that the humanizer skill
was not available.

- Scope: new prose only (drafted sections, inserted paragraphs, response letters). The author's
  own sentences are humanized only when the author asks, and then through the table below.
- Methods and figure legends: extra conservative; fix filler and doubled hedges only.
- No casual asides, quips, opinions or first person the venue does not allow. Keep professional
  courtesy in letters.
- Keep the author's register: long, semicolon-dense sentences in Methods, Results and legends are
  the author's style; do not split them by reflex.
- One subagent per paragraph through the rewrite loop; at most about 5 words per change on the
  author's text. The agent re-reads its own output for the same tells before returning it.
- Validate: the thesis calibration was tens of edits across a whole thesis; more than 50 in one
  chunk meant the rules were over-firing. Have the reviewer check that no claim changed.

Rule table for scientific text (our synthesis of the thesis checklist, Pass 6, which adapts the
humanizer's categories):

| Treatment | Categories |
| --- | --- |
| Edit (small) | filler ("in order to" to "to", "due to the fact that" to "because", "it is important to note that" deleted); doubled hedges ("may potentially" to "may"); copula avoidance ("serves as" to "is"); promotional adjectives when deletion is clean; stock AI words with a 1:1 swap; mechanical bold; inconsistent Title Case headings; chatbot residue; signposting; em dashes used as sentence punctuation (not in ranges, IDs or compounds) |
| Comment | significance inflation; "-ing" tails that claim meaning; vague attributions (defer to the citation pass); formulaic challenges-and-future paragraphs; not-only-but-also; rule of three across a paragraph; synonym cycling; false ranges; inline-header lists; generic upbeat conclusions; warm-up sentence after a heading |
| Skip | notability claims; passive voice in Methods; curly versus straight quotes; the author's semicolons |

Synthetic example: "Notably, GENE1 plays a pivotal role in regulating feeding, highlighting its
importance as a therapeutic target." The edits delete "Notably," and "pivotal"; the "-ing" tail
gets a comment, because shortening it changes the claim.

## 7. Citation accuracy (comments only)

Follow [reference audit](reference-audit.md). Check that each cited paper exists, is the paper the
entry claims, and supports its sentence; flag important claims without a citation and
over-citation. Escalate abstract-only flags to full text before commenting: in the thesis about 80%
of abstract-level flags were supported once the full text was read. Output comments anchored to the
citing sentence; never edit fields or the bibliography. The lint's `citation.placeholder` (an
unconverted `[PMID: n]`, `[REF: key]` or `{Author, Year #n}`) and `citation.adjacent-groups`
findings go to the author as EndNote steps.
Validate: every comment names the citation's number and the paper it points to.

## 8. Logic consistency (comments only)

- Categories: internal contradictions; text versus figure or table (n, direction, test); text
  versus Methods (doses, time points, n, sex, age); argument flaws (correlation read as mechanism,
  unsupported "first to show"); order problems; numbers that do not add up; definitions that drift.
- Template: `[Logic] <category>: <location A> / <location B> / <concern> / <question for the
  author>`. The bar is high: a false positive costs the author time.
- One agent reads the whole text; a second agent checks each finding against the document and
  drops those it cannot confirm. Validate: every comment cites both locations.

## 9. Multi-persona review (comments only)

One agent per persona, each reading the whole manuscript, figures and legends:

- Adversarial: overclaiming, missing alternatives, cross-species or in vitro to in vivo leaps,
  correlation versus mechanism.
- Methodology: controls, replicates, power, multiplicity, effect sizes and intervals, blinding.
- Domain: missing landmark literature, novelty claims, field inconsistencies.
- Clarity: buried lede, sentences over 40 words that lose the reader, unclear antecedents.
- Synthesis: aims versus work done, Discussion versus Introduction, limitations, title and abstract
  fidelity.

Prefix comments `[Reviewer: <persona>]`; anchor section-level comments on the heading. Edits are
limited to rewording of at most 10 words at high confidence; everything else is a comment. Merge
duplicates across personas into one comment each before building, and report the count per
persona.

## 10. Spacing lint

Deterministic scan for double spaces, space before punctuation, space after "(" or before ")",
missing space after punctuation, and edge spaces. `msw.py lint` reports the first two
(`typo.double-space`, `typo.space-before-punctuation`); scan the rest from the extracted text. Only
U+0020 and tab count: no-break spaces, narrow no-break spaces and non-breaking hyphens are
deliberate. Ignore whitespace beside citation fields. Filter decimals, section numbers, initials,
URLs, DOIs, ratios and times. The fix is a `delete` of only the extra space. Validate: the gate's
`whitespace` check passes and the proof shows no joined words.

## 11. Italics consistency

Deterministic scan for Latin phrases (in vitro, in vivo, ex vivo, de novo, per se, ad libitum, post
hoc, et al.) and species binomials, plus gene symbols in gene context (window of about 40
characters with the cue lists from pass 1). Cell-line names and the abbreviation table are
skipped. Follow [house style](house-style.md) for *et al.* and *P*; the lint reports *P* style
(`stats.p-italic`) and roman gene-like symbols (`nomenclature.gene-italic`), but Latin phrases
and binomials need this scan. Edits are `format` ops. Validate: after the build, each phrase has
one form throughout the text.

## 12. Targeted polish

Grammar, flow and clarity for one named paragraph or the abstract, by paraId, usually as one
`rewrite`. Leave italic gene runs and comment ranges intact. For an abstract, respect the journal's
word limit and structure ([journal profiles](journal-profiles.md)). Validate: `limits.abstract-words`
and `abstract.*` lint rules pass, and the author sees the whole paragraph's accepted text in the
report.

## 13. Final house-style lint

`msw.py lint DOCX [--profile P] [--spelling V] [--article-type T] [--json OUT] [--markdown OUT]
[--verbose] [--limit N]` checks the accepted view against the house style and the journal profile
and writes nothing to the manuscript. The profile defaults to the project's (else the house
defaults), the spelling variety to `language.spelling` (else the dominant variety among the
profile's `formatting.language`), and the article type to `research`. Each finding has a rule id,
severity, paragraph index in the accepted view, paraId, snippet and suggestion; use the paraId,
because Word merges paragraphs whose marks are deleted. The console shows errors and warnings
(info notes only with `--verbose`, at most `--limit` findings, default 200, 0 for all); `--json`
and `--markdown` write the full report as new files. Keep it as `--json PASS/qa/lint.json`. The
exit code is 1 when any error remains, 2 when the lint cannot run (bad path or profile, or an
existing output file) and 0 otherwise.

A separate title-page file is recognised: no heading paragraph anywhere, plus at least one
`Keywords:`, `Running title:` (or `Short title:`), `Word count:` or `*Corresponding author` line.
It is linted with `scope: title_page` (printed, and in the JSON and Markdown): its first
non-empty paragraph is the title, the title, running-title and keyword limits run and the stated
word count is recorded, while the body checks (declarations, figures, abbreviations, gene
italics, word count, references, abstract) are skipped. On a main file without a title,
`limits.missing` says to lint the separate title-page file.

| Rule ids | Severity | Finds |
| --- | --- | --- |
| `stats.p-spacing`, `stats.p-case`, `stats.p-italic` | warn | *P* values against `statistics.p_style` and `p_italic`, else the document's majority |
| `stats.q-spacing`, `stats.n-spacing`, `stats.pm-spacing` | warn | spacing of *q*, `n =` and ± against `q_style`, `n_style` and `mean_sd_style`, else the majority |
| `typo.minus`, `typo.range-dash`, `typo.eponym-dash`, `typo.degree-sign` | warn | hyphen-minus as a minus sign; hyphen in a numeric range; a hyphenated eponym pair from a fixed list (Mann–Whitney, Benjamini–Hochberg and others); `º` for `°` |
| `typo.double-space`, `typo.space-before-punctuation` | warn | two ordinary spaces in a row; a space before `, . ; : ! ?` or a closing bracket |
| `spelling.mixed` | warn | a word spelt in another variety than the chosen or dominant one |
| `spelling.variety` | info | the variety used and the counts |
| `citation.placeholder` | error | an unconverted `[PMID: n]`, `[REF: key]` or `{Author, Year #n}` |
| `citation.adjacent-groups` | warn | separate adjacent groups such as `(4) (5)` |
| `heading.empty` | error | a heading paragraph with no text |
| `heading.trailing`, `heading.indent`, `heading.numbering` | warn | a heading ending in a colon or space; a direct first-line indent; typed numbers with a gap, repeat or wrong parent |
| `format.highlight`, `format.proofing-language`, `format.reference-spacing` | warn | residual highlighting (expected only on a highlighted revision copy, [reviewer response](reviewer-response.md#6-choose-tracked-or-highlighted-markup)); more than one proofing language; reference-list spacing other than `formatting.references_line_spacing`, a reminder to check EndNote's Layout setting, which controls it ([EndNote](endnote.md#the-authors-rule-hands-off)) |
| `format.proofing-variety` | info | the dominant proofing language differs from the spelling variety |
| `abbrev.before-definition`, `abbrev.undefined` | warn | an abbreviation used before its `long form (ABBR)` definition, or never defined (heuristic) |
| `abbrev.redefined`, `abbrev.single-use` | info | defined twice in the body; used at most once after its definition |
| `abstract.abbreviation`, `abstract.format` | warn | an abbreviation in the abstract when the profile forbids them; more than one paragraph when it asks for one |
| `abstract.citation` | error | a citation or placeholder in the abstract when the profile forbids citations (the house default) |
| `nomenclature.gene-italic` | info | a gene-like symbol in roman type beside a gene-context word |
| `callout.fig-abbreviation`, `figures.order`, `figures.gap`, `figures.missing-legend` | warn | `Fig.` in the text; figures first cited out of order; a skipped number; a cited figure without a legend |
| `figures.uncited` | error | a figure with a legend that the text never cites |
| `limits.title`, `limits.running-title`, `limits.keywords`, `limits.abstract-words` | error | over the profile's title or running-title characters, outside its keyword range, over its abstract words |
| `limits.word-count` | profile | over `word_limit.max`; severity from `word_limit.severity` (default warn; `hard` or `fail` count as error) |
| `limits.word-count-stated`, `limits.references`, `limits.figures` | warn | the title-page count differs from the computed one; more references or figures than recommended |
| `limits.missing` | info | a title-page item needed for a limit check was not found (common with a separate title page: lint that file too) |
| `declarations.missing`, `declarations.order` | warn | a declaration in `declarations.order` not found as a heading or `Label:` line, or out of order |

The typography and statistics rules skip the title page, the bibliography, EndNote, `ADDIN` and
hyperlink field results, URLs, DOIs and e-mail addresses. A profile's `lint.disabled_rules`
switches rules off by id or prefix (`"typo."`), and `lint.universal_abbreviations` adds
abbreviations that need no definition ([journal profiles](journal-profiles.md)). Caption
consistency, em dashes, Latin and species italics, units and number style are not in the lint:
read for them in passes 2, 6 and 11 and against [house style](house-style.md).

Run it after every model pass and before submission; turn findings into edits or comments in the
next pass. The target before submission is no errors, and each warning either fixed or recorded
as a decision.

## Rerun policy

- A version is never edited again. Re-running an edit pass means a new version built from the
  author's latest save, which keeps whatever the author accepted from the first run.
- Re-running an early edit pass after later passes starts from the newest version, not the old
  one; the later passes' accepted changes stay.
- If the first run's changes are still unaccepted in the source, ask the author to accept or
  reject them first; otherwise the rerun edits inside them and the redlines stack.
- Comment-only passes (7, 8, 9) can be re-run on any version without invalidating edit passes.
- When the author rejects a pass's changes, record why in `06_Project_Docs/DECISIONS.md` before
  re-running it, so the same edits do not come back.

# Drafting a new manuscript

Use this page to write a manuscript or thesis from data and results, starting from no Word file.
To change an existing file, use [revision](revision.md). Apply [house style](house-style.md) to
every section. Each `msw.py` command's `--help` lists its options. Write the journal profile
before building anything ([bootstrap](bootstrap.md#5-write-the-journal-profile)): the builder
reads its title-page, formatting and limit rules.

The route: recompute the evidence, fix the story in writing, build the figures, draft the text
as `manuscript.md` and `title_page.json`, build the .docx once, register it as V01, and make
every later change a tracked pass.

## 1. Evidence first

1. Inventory the data. Source files sit in `03_Data/Original_Source/` as byte copies and are
   never edited; derived tables go to `03_Data/Derived/` or `04_Analysis/<workstream>/`. For each
   planned result, note the file and table it comes from.
2. Recompute every statistic from the data tables with a script kept in `04_Analysis/`. Compare
   the results with any earlier text (a submitted version, a thesis chapter, a poster) and log
   each mismatch; never settle one silently.
3. GraphPad Prism files need care. A `.prism` file is a zip that holds the data tables, the
   excluded cells and the stored analysis results. *Observed:* a stored analysis did not match
   a recompute because its table was edited after the analysis last ran. Therefore:
   - treat the data tables as ground truth and stored results only as a cross-check;
   - carry excluded cells as data (a flag column), and report an all-recorded sensitivity
     analysis whenever values were excluded;
   - confirm which test produced a stored result from the analysis parameters before naming it
     in Methods; matching a reported P to the nearest candidate test only suggests a source.

   Extraction details are in [figures](figures.md#prism-reconstruction).
4. Freeze each figure's input as a locked table with a provenance note (source file, table,
   filter, date). Every number printed in the text, a legend or a figure maps to one row.
5. The author owns the statistical framing: the primary hypothesis, multiplicity handling and
   which P values appear. Restate an analysis in plain words and get a yes before running it.

## 2. Decide the story

Write the plan into `06_Project_Docs/DECISIONS.md` before any prose, and have the author confirm
it with numbered options and a recommendation. The core is one map from aims to Results
subsections to figure panels (synthetic):

| Aim | Results subsection | Figure panels | Inference class |
| --- | --- | --- | --- |
| (i) effect of balance training on L-38 | 3.1 | 1A | randomized primary |
| (ii) falls by arm over 12 months | 3.2 | 1B | randomized secondary |
| (iii) L-38 and gait speed within person | 3.3 | 1C | exploratory |
| (iv) baseline L-38 by fall history | 3.4 | 1D | cross-sectional, exploratory |

Record with it:

- the principal estimand and which analyses are secondary, exploratory or post hoc;
- the target journal and its profile, the word budget per section, and the counting scope;
- spelling variety and first-person policy if bootstrap did not settle them;
- what is deliberately left out.

Aims become the last Introduction paragraph, the Results subsections follow their order, and the
Figure 1 legend maps them to the analyses.

## 3. Figures before text

Build every figure in code with the `nature-figure` skill before writing Results; this skill never
draws. Give nature-figure the journal profile's `figures` block as its export contract, keep the
figures text-light, and keep legend and method text out of the artwork. For a new design,
render three or four variants and build the one the author picks. Draft each legend in the
figure folder's `LEGEND.md` from the same locked tables. Folder layout, the figure index, the
gate and journal exports are in [figures](figures.md).

## 4. Write in this order

| Order | Part | Why |
| --- | --- | --- |
| 1 | Methods | Fixed facts; sets the terms, abbreviations and analysis labels the rest uses |
| 2 | Results | Written from the recomputed tables and finished figures |
| 3 | Figure legends | From the same locked tables; panel order matches the figures |
| 4 | Discussion | Interprets Results that already exist |
| 5 | Introduction | Sets up only what the Results answer; ends with the aims |
| 6 | Abstract | Summarizes finished text with coarser rounding |
| 7 | Title page | Title and running title match the final claims; the word count comes from the built file |

Declarations, Limitations and the abbreviation table come after the Discussion. Draft the
declarations in the profile's order and wording; ask the author for contributions, funding and
interests rather than inferring them.

## 5. Source format

Keep the drafting sources in the project, for example `06_Project_Docs/Drafting/`. After V01
exists they are history.

### manuscript.md

A deliberately small dialect; no third-party Markdown library reads it. Errors name the file and
line.

- `# Heading` becomes Heading 1, `## Heading` Heading 2 and `### Heading` Heading 3; deeper
  headings are refused. Type the numbering into the heading text (`# 2. Methods`,
  `## 2.1 Participants`).
- Blank lines separate paragraphs; line breaks inside a paragraph become spaces. Body paragraphs
  get the Body Text style. The abstract, the paragraphs under back-matter headings
  (Contributions, Data availability, Acknowledgements, Declaration of interest, Funding and the
  like) and bold run-in declaration lines (`<b>Funding:</b> ...`) get Body Text No Indent;
  Conclusions and Limitations stay indented.
- Inline formatting uses tags: `<i>…</i>`, `<b>…</b>`, `<sup>…</sup>`, `<sub>…</sub>`,
  `<u>…</u>`, properly nested. Asterisks and underscores are literal text, so `*P < 0.05` stays as
  typed.
- Citations are placeholders `[PMID: 12345678]` or `[PMIDs: 1234, 5678]`, or `[REF: key]` for a
  source without a PMID. They stay literal until EndNote conversion.
- A `| a | b |` pipe table (header row, separator row such as `| --- | --- |`, equal cell counts;
  `\|` is a literal bar) becomes a borderless table with a bold, repeating header row; this is how
  the Abbreviations table is written. A paragraph directly above a table is its label: no indent,
  left-aligned, kept with the table.
- A picture line on its own becomes an embedded picture paragraph, centred, single-spaced and
  kept with the next paragraph. The path is relative to `manuscript.md`; PNG, JPEG and GIF are
  read; `{width=...}` takes `mm`, `cm` or `in` and is optional (default: the text width). A
  picture wider than the text or taller than the page is scaled down with a warning. The
  picture's name inside the .docx is its legend heading's label (`Figure 1`, `Supplementary
  Figure S1`), else `Picture n`; that is the `old_name` a later picture swap uses. The alt text
  becomes its description.

  ```text
  ![Alt text](figures/figure1.png){width=180mm}
  ```

- `\pagebreak` on its own line makes the next paragraph, heading or picture start a new page (a
  separate page-break paragraph only before a table or at the end).
- `# Figure Legends` starts a new section (a section break is inserted before it). Under it,
  each figure is a `## Figure N: Title.` heading, then its picture line, then the legend
  paragraph or paragraphs (Figure Legend style). From the second figure on, each figure heading
  whose block has a picture starts a new page.
- `# References` emits the heading and an empty EndNote Bibliography paragraph where EndNote will
  place the bibliography. Paragraphs written under it instead become a plain list in that style.
- The text of `# Abstract` is one paragraph; more than one gives a warning.
- Lines starting with `<!--` are skipped up to the closing `-->`, which suits notes to yourself.

A complete synthetic source ships with the skill: [manuscript.md](../assets/examples/manuscript.md)
with its figure, and [title_page.json](../assets/examples/title_page.json). Its end shows the
legend pattern:

```markdown
# References

# Figure Legends

## Figure 1: Serum L-38, falls and gait speed by randomized arm and fall history.

![Four panels: change in serum L-38 by randomized arm (A), cumulative falls by arm (B), ...](figures/figure1.png){width=150mm}

(A) Change in serum L-38 from baseline to week 16 in the stretching arm (gray, n = 44) and the balance-training arm (orange, n = 45); ... Panels A to D answer aims (i) to (iv) in order. P values are unadjusted. **<i>P</i> < 0.01.
```

Each aim has its Results subsection and panel, and the legend names each panel's inference
class and maps the panels to the aims. The example's declarations follow the Journal of
Endocrinology profile's order; linted with the house defaults instead, it gives one
`declarations.order` warning. Two declaration lines are bracketed on purpose: the conflict of
interest sentence comes from the journal's own wording, and the AI statement names the tool,
version or model and provider from the author's records.

### title_page.json

```json
{
  "title": "Serum marker L-38 and falls in older adults randomized to balance training",
  "running_title": "L-38 and falls after balance training",
  "authors": [
    {"name": "Given A. Surname", "affiliations": [1, 3], "corresponding": false},
    {"name": "Other Surname", "affiliations": [1, 2], "corresponding": true}
  ],
  "affiliations": [
    "Department of Example<sup>1</sup> and Example Clinic<sup>3</sup>, Example Hospital, City, Country",
    "Institute of Example<sup>2</sup>, Example University, City, Country"
  ],
  "corresponding": [{"name": "Other Surname", "address": "Institute of Example, Street 1, City, Country",
                     "email": "name@example.org", "phone": "", "orcid": "0000-0000-0000-0000"}],
  "keywords": ["marker", "older adults", "balance training", "falls"],
  "word_count": "auto"
}
```

Affiliations are written the way the title page shows them: each number as `<sup>n</sup>`
directly after its unit, several units on one line where they share an address, and the lines
in any order (the builder sorts them by their first number). The object form
`{"1": "Department ..., City, Country"}` still works; an entry without markers gets its key as a
leading superscript, and an entry with markers must include its key among them.

With `"word_count": "auto"`, the builder counts the built document by the configured method
(`--method`, else `word_count.method` in `manuscript.json`, else house; see
[house style](house-style.md#word-count)) and writes the number into the title page, with a
parenthesis that states what that method and the profile's `word_limit.excludes` leave out.
Automatic counting needs `# Abstract`, `# Introduction` and `# References` headings. A whole
number is stated as given, with a warning when it differs from the count.

The builder refuses a missing title or authors, an empty affiliation, an affiliation number that
no line defines or that two lines define, an affiliation no author uses, a list entry without a
`<sup>` marker, a `corresponding` block whose `name` is not exactly an author marked
`"corresponding": true`, and a `word_count` that is neither `"auto"` nor a whole number. It warns
about a missing corresponding-author block, keywords or running title, and about the profile's
title, running-title, keyword, abstract and word limits. A corresponding block may also carry
`"fax"`; empty contact items are left out. Author names, affiliations and contact details come
from the author; never invent or complete them.

## 6. Build and register V01

```text
msw.py new-manuscript --source manuscript.md --title-page title_page.json
    --out "90_Agent_Work/Scratch/<new folder>/draft.docx"
    [--profile <profile.json>] [--separate-title-page "<new folder>/title page.docx"]
    [--method house|strict] [--date YYYY-MM-DD]
msw.py intake "90_Agent_Work/Scratch/<new folder>/draft.docx"
```

- The outputs must not exist; the tool refuses to overwrite and creates missing folders.
  `manuscript.json` is found by walking up from `manuscript.md`: it supplies the author name for
  the document properties, the spelling variety for the proofing language and the word-count
  method. The profile is `--profile`, else the project's `journal_profile` if that file exists,
  else the house defaults; a key the profile leaves out or sets to null keeps the house value.
  Page size, font and size, body and reference line spacing and line numbers come from its
  `formatting` block. Track changes is off in the new file. The build reports paragraphs,
  headings, pictures, tables, sections, placeholders and the house and strict counts.
- The main .docx always opens with the title page. When the profile's `title_page.separate_file`
  is true, the body starts on a new page, and `--separate-title-page` also writes the title page
  as its own .docx (without it, a NOTE reminds you). `msw.py title-page --title-page
  title_page.json --manuscript <file.docx> --out <new.docx>` rebuilds the title page alone with
  that file's count (`--word-count N` states a number instead; `--method` words the parenthesis
  for either); keep it with the submission files
  ([release](release.md#submission-packaging)). If the journal wants the main file without a
  title page, remove it in a tracked pass.
- Check the build before registering it: `msw.py inspect` and `msw.py census` (placeholders
  counted, no citation fields yet), `msw.py wordcount`, `msw.py lint`, and a proof you look at
  (`msw.py proof`; see [verification](verification.md)).
- `msw.py intake` copies the file byte for byte to `01_Manuscripts/Versions/V01/` (a project's
  first file is V01 with the state `baseline`, `... V01 baseline.docx`, unless `--as-version`,
  `--slug` or `--state` say otherwise), makes it current and writes the index and ledger entries;
  see [bootstrap](bootstrap.md).

From V01 on, every change is a tracked pass (`msw.py pass new`, an edit spec, `msw.py build`) as
in [revision](revision.md). Never rebuild from Markdown to make V02: the author's Word edits and
comments would be lost, and the gate could not prove what changed.

## 7. Citations

- Put the placeholder at the claim it supports, before the sentence's final punctuation.
- `msw.py refs placeholders <file.docx>` lists every placeholder, `[REF: key]` included.
- Verify every PMID against PubMed: `msw.py refs pubmed --from-docx <file.docx> --out <new
  folder>` fetches the records with archived evidence. Confirm that each record's authors, year
  and title are the paper you meant, and that the paper supports the claim; read the full text
  before flagging one. See [reference audit](reference-audit.md).
- List `[REF: key]` sources (books, datasets, web pages) for the author with enough detail to
  find them in EndNote.
- The author converts placeholders with EndNote Cite While You Write and formats the
  bibliography; agents never insert EndNote fields. See [EndNote](endnote.md).
- Recount words after conversion: placeholders are longer than `(n)`.

## 8. Humanizer on new prose

Run the `humanizer` skill on prose you wrote, before building V01 or later as small tracked
edits ([QA passes](qa-passes.md#6-humanizer)). Never run it over the author's own text unless
the author asks.

- Keep the author's register: long, semicolon-rich sentences, calibrated hedges and a
  professional tone. No quips, asides or added opinions; no first person where the project
  avoids it.
- Keep every number, statistic, gene name, citation placeholder, hedge level, direction of
  effect and special space unchanged. Be extra conservative in Methods and legends.
- Run `msw.py lint` afterwards: rewrites reintroduce foreign spellings and dashes.

## 9. Self-review before presenting V01

1. `msw.py lint` and `msw.py wordcount` against the profile; fix or list every finding. Until
   the author converts the citations, `citation.placeholder` errors are expected.
2. Trace each number in the text and legends to its locked table row.
3. Check the story map: every aim has a Results subsection and figure panels; figures are called
   out in numerical order; every legend follows the house pattern.
4. Check claims against the [claim ladder](house-style.md#claim-ladder): no promotional words,
   within-arm P values, causal headings or unlabelled exploratory results.
5. Abbreviations are defined at first use in the abstract, body and each legend.
6. Independent review of the artifacts, then the report in the form the skill entrypoint
   describes, ending with numbered decisions (word budget, declarations, open data questions).

## Thesis variant

Set `document_kind` to `thesis` and write a profile from the graduate school's formatting rules
([journal profiles](journal-profiles.md)). Structure, captions and numbering are in
[house style](house-style.md#thesis-skeleton).

- **Chapters from published papers.** Start each chapter from the author's final accepted
  manuscript text, not the typeset PDF. Renumber headings into the chapter (`3.1`, `3.2`),
  renumber figures consecutively across the thesis, and re-cite references as `[PMID: n]`
  placeholders for the author to convert from the same EndNote library. Open the chapter with
  the statement of which published papers it draws on and a contributor statement using
  initials ([house style](house-style.md#thesis-skeleton)). Check the publisher's reuse terms and the graduate school's rules for including
  published work, and record them in `DECISIONS.md`.
- **New prose.** The general Introduction, the bridges between chapters and the General
  Discussion are new; write them in the same order (Discussion before Introduction).
- **Voice and spelling.** The precedent is no first person in the body and Canadian spelling;
  confirm both.
- **Layout.** Set the thesis values in the profile's `formatting` block (`font_size_pt: 12`,
  `line_spacing: "1.5"`) so the builder uses them. The builder is made for journal articles:
  its title page carries keyword, word-count and running-title lines, and it has no roman
  page-number section. Write the front matter as plain paragraphs separated by `\pagebreak`,
  with list entries carrying measured page numbers as plain text, and give the title page its
  thesis form in the first tracked pass. If the graduate school supplies a Word template, ask
  whether to start from it instead. The author sets the roman and arabic page-number sections
  and refreshes the table of contents in Word.
- **Deposit.** Line numbers stay in review drafts (`line_numbers: "continuous"`) and come out
  for deposit.

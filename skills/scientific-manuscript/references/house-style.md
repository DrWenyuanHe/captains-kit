# House style

The author's manuscript house style, written as rules to apply when drafting or checking. The
layouts, orders and sentence patterns are observed in the author's recent journal manuscript and
thesis ([source notes](source-notes.md)). Where the author's practice varied, this page picks one
default; those picks are this skill's synthesis. Apply each default throughout a document.

## Precedence

The project's `AGENTS.md` and `manuscript.json` (`language.spelling`, `language.first_person`,
`word_count.method`) come first, then the journal profile ([journal profiles](journal-profiles.md);
items marked **Profile** name the overriding key), then this page. In an existing manuscript, fix
deviations in a hygiene pass of minimal tracked edits ([revision](revision.md)), not in passing
during a content pass. Numbers, statistics, gene names and claim direction never change for
style.

## Document formatting

`msw.py new-manuscript` applies these to a new draft ([drafting](drafting.md)); check an existing
file in the proof.

| Setting | House default | Profile |
| --- | --- | --- |
| Page | US Letter; margins 1 in top and bottom, 1.25 in left and right | `formatting.page` |
| Base font | Times New Roman 11 pt, justified | `formatting.font`, `formatting.font_size_pt` |
| Body paragraphs | `Body Text` style: double spacing, 0.5 in first-line indent, 8 pt after; Introduction to Conclusions and Limitations | `formatting.line_spacing` |
| Abstract and back matter | `Body Text No Indent` (based on `Body Text`, no first-line indent): the abstract, Contributions, Data availability, Acknowledgements and the declaration lines; `Figure Legend` is based on it | `formatting.line_spacing` |
| Heading 1 | bold 16 pt, left, double spacing, 0 pt after | none |
| Heading 2 | bold 11 pt, double spacing, 8 pt after | none |
| Heading 3 | italic 11 pt; rarely needed | none |
| Line numbers | continuous, every line, never restarted, including title page, references and legends | `formatting.line_numbers` |
| Page numbers | `PAGE` field, centred in the footer | none |
| Sections | two: main text, then a section break before Figure Legends | none |
| References | `EndNote Bibliography` style, single-spaced, 12 pt after | `formatting.references_line_spacing` |
| Proofing language | one tag for the whole document: the project's spelling variety | `formatting.language` lists the varieties a journal accepts |

- Body layout lives in the paragraph style, so inserted paragraphs inherit it. In a document
  built with direct formatting, give each new paragraph its neighbour's properties.
- Headings use the Heading styles with typed numbering and no trailing colon, space or
  first-line indent. Spacing comes from styles, never from blank paragraphs.
- Each figure legend starts on a new page, by one mechanism throughout. Legends are
  double-spaced without first-line indent; pictures are centred and single-spaced.
- No highlighting, coloured text or mixed proofing languages in a deliverable.

## Title page

House order. **Profile:** `title_page.items`, `title_page.separate_file`, and the title,
short-title and keyword limits under `article_types.research` (`limits.*` lint rules).

1. **Title**: bold 12 pt, sentence case, no final period. Descriptive rather than causal: name
   the marker or exposure, the outcome, the design or setting and the population, with no verb
   that claims an effect. Synthetic: *Serum marker L-38 and falls in older adults randomized to
   balance training*.
2. **Author line**: one paragraph, names separated by commas with "and" before the last.
   Affiliation numbers are superscript after each name, comma-separated; the corresponding
   author's asterisk sits in the same superscript (`1,2*`).
3. **Affiliations**: one paragraph per line, with full address, city and country. Each number
   is a superscript inside the line, directly after the unit it identifies, and one line may
   carry several units (*Department of Example Geriatrics¹ and Example Falls Clinic³, Example
   University Hospital, ...*). Lines run in the order of their first number. In
   `title_page.json`, write the numbers as `<sup>n</sup>` in the text
   ([drafting](drafting.md#title_pagejson)); a plain entry without markers falls back to a
   leading superscript number.
4. **Corresponding author(s)**: after a blank line, an italic, underlined `*Corresponding
   author:` (or `authors:`) label, then per corresponding author a line `- Name. Department,
   institution, street address, postal code, city, country`, a line `Tel.: ...; E-mail
   address: ...` and a line `ORCID number: ...`.
5. **Keywords**: bold `Keywords:` label, then at least four lower-case keywords, comma-separated,
   no final period.
6. **Word count**: `Word count: 4,812 words (excluding references and figure legends)`, plain
   text, thousands comma. The parenthesis says what the number leaves out: the builder takes
   the items in the profile's `word_limit.excludes` that the counting method also leaves out,
   adds references and figure legends if the profile did not name them, and for the `strict`
   method opens with its scope (`abstract and main text to the end of Conclusions; excluding
   headings, Limitations, back matter, references and figure legends`). Where the journal
   excludes something the method counts (the abstract, for example), the build warns and the
   parenthesis does not claim that exclusion.
7. **Running title**: `Running title:` and the text, both bold. A noun phrase naming the marker
   and population, within the limit including spaces.

- The title page flows into the Abstract without a page break. When the profile sets
  `title_page.separate_file`, `new-manuscript` starts the Abstract on a new page, and
  `--separate-title-page` also writes the title page as its own file.
- Funding, interests and AI use are declarations, not title-page items. Names match exactly
  across the author line, contact blocks, Contributions and the submission system.

### Word count

`msw.py wordcount <file.docx>` counts the accepted view in both scopes and compares the
configured one (`--method`, else `manuscript.json` `word_count.method`, else `house`) with the
title page and the profile's limit.

| Scope | Counts | Excludes |
| --- | --- | --- |
| `house` | Abstract body (between the Abstract heading and the `Abbreviations` or `Keywords` paragraph, else the next Heading 1, so a structured abstract's subheadings count); every paragraph from the `1. Introduction` heading up to the `References` heading, including all headings, Limitations, Contributions, Data availability, Acknowledgements and the declaration lines | Title page, abbreviation table, references, figure and supplementary legends, the `Word count:` line |
| `strict` | Abstract body and the body paragraphs from Introduction through Conclusions | Headings, Limitations, all back matter, and everything the house scope excludes |

- Words are whitespace-separated tokens of the text Word renders: tabs and breaks separate
  words, non-breaking hyphens join them, and citation numbers such as `(12)` count. Typography
  changes the count (`P = 0.012` is three tokens, `P=0.012` one; `[PMID: n]` counts more than
  `(n)`), so recount after typography passes and EndNote conversion.
- A missing Abstract, Introduction or References Heading 1 is an error (exit 2), never a silent
  zero; a thesis needs the graduate school's own count. The command exits 1 when the stated count
  differs from the chosen count, or when the count is over a limit whose severity is `error`,
  `hard` or `fail`.
- Stamp the title page with `--stamp-spec <spec.json>`: it writes an edit spec that replaces
  the digits, which `msw.py build` turns into a tracked change. When the line's parenthesis
  describes another scope (after a switch to `--method strict`, say), the same edit rewords it;
  `wordcount` prints a NOTE when the two disagree. A count over the journal limit is a decision
  for the author; report it with the words each section contributes.

## Journal article skeleton

| Part | Form |
| --- | --- |
| Title page | as above |
| `Abstract` | Heading 1; one paragraph |
| `Abbreviations` | a plain paragraph (not a numbered heading), then a borderless two-column table (`Abbreviation`, `Definition`) with a bold header row, alphabetical; then a page break |
| `1. Introduction` | four paragraphs, no subsections |
| `2. Methods` | `2.1 Participants`, `2.2 ...`; one paragraph per subsection |
| `3. Results` | `3.1 ...`; subsections mirror the aims and the Methods blocks |
| `4. Discussion` | an unnumbered opening paragraph, then `4.1 ...` |
| `5. Conclusions` | one paragraph |
| `Limitations` | unnumbered Heading 1, after Conclusions |
| Declarations | unnumbered Heading 1 sections and bold run-in lines, in profile order (`declarations.order`) |
| `References` | EndNote bibliography |
| `Figure Legends` | after a section break; per figure a Heading 2 title, the picture, the legend |

- Top-level headings take a period after the number (`2. Methods`); subsections do not
  (`2.1 Participants`). Back matter is unnumbered.
- House declaration order: Contributions, Data availability, Acknowledgements, then bold
  run-in lines `Declaration of interest:`, `Funding:` and `Use of generative AI:`. A profile's
  `declarations.order` replaces it (the shipped example follows the Journal of Endocrinology
  order, so it warns `declarations.order` when linted without that profile). Take wording from
  `declarations.templates` when the profile has it.
- Contributions: one paragraph per author, `<b>Name:</b> Role, Role.`, CRediT role names
  ("Writing – review & editing", with an en dash), authors in title-page order.
- The AI statement names each tool, its version or model, and what it was used for.
- Figures are embedded under their legend titles in review versions; the submission package
  follows `figures.files`. Tables are separate files; the abbreviation table is the only
  in-text table.

## Thesis skeleton

| Setting | Thesis precedent |
| --- | --- |
| Page | US Letter; margins 1 in top and bottom, 1.25 in left and right |
| Body | Times New Roman 12 pt, 1.5 line spacing, 12 pt after, justified, no first-line indent; `en-CA` |
| Sections | front matter numbered i, ii, ... with no number on the title page; body numbered from 1; centred `PAGE` footer |
| Headings | typed numbering. Heading 1 `Chapter N: Title` (22 pt); Heading 2 `N.M` (16 pt, bold, centred); Heading 3 `N.M.K` (bold); Heading 4 for caption titles, kept out of the table of contents |
| Line numbers | in review drafts only; removed for deposit |
| Citations | EndNote superscript numbers directly after the word, before punctuation |
| Person | no first person in the body; the Acknowledgements may use it |

- **Front matter**, in order: title page (title, "by", name, degree statement, department,
  university, copyright line); abstract page (within the graduate school's limit, 350 words in
  the precedent); Acknowledgements; Table of Contents; Lists of Tables, Figures (illustrations
  first) and Supplemental Figures; List of Abbreviations (two-column table with thematic group
  rows). List entries are plain text with measured page numbers, never hand-built `PAGEREF`
  fields. The author refreshes the table of contents in Word.
- **Chapters**: an Introduction whose last two subsections state the research gaps and then the
  hypotheses and aims (labelled paragraphs `Aim 1:`, `Hypothesis 1.1:`, not bullets); Materials
  and Methods (shared methods, a subsection per data chapter, a key-resources table with `REAGENT or
  RESOURCE`, `SOURCE`, `IDENTIFIER`); data chapters `N.1 Abstract`, `N.2 Introduction`,
  `N.3 Results` (finding-statement headings), `N.4 Discussion`, `N.5 Conclusions`; a General
  Discussion (findings, implications, limitations, future directions, conclusions); then
  supplementary items and References. A chapter based on published work opens with a sentence
  saying which parts draw on which published papers, the references, and a contributor
  statement naming co-authors by initials; use the graduate school's required wording where it
  sets one.
- **Captions**: figures numbered consecutively across the thesis; schematics are
  `Illustration N`; `Supplemental Figure N` items sit at the end. The title is a Heading 4 line
  `Figure N. Sentence-case title.` above the image; the legend is a body paragraph after it.
  Tables use `Table N. Title.` above the table.

## Writing conventions

### Abstract

- One paragraph within `abstract.max_words` (house 250); structured only if the profile's
  `abstract.format` says so.
- Self-contained: no citations; spell terms out. If the journal allows abbreviations, define
  each one inside the abstract.
- Sentence order: (1) the gap; (2) design and measurement, including randomization, sampling
  times and inference methods; (3) the primary result with estimate, CI and P (in a trial, the
  randomized contrast); (4) secondary and exploratory results, each labelled, including nulls
  stated plainly; (5) a calibrated conclusion ending with the validation or trial still
  required.
- Coarser rounding than Results: two decimals for ratios and AUCs, P to two significant figures,
  and the CI in words with "to". Synthetic: *At week 16, L-38 was higher after balance training
  than after stretching (ratio of geometric means 1.16, 95% confidence interval 1.05 to 1.28;
  P = 0.004).*

### Introduction

Four paragraphs, about 300 to 350 words: (1) the clinical problem with a citation, current
practice and where it falls short, ending on a one-sentence need; (2) the marker or exposure,
what it can and cannot show; (3) the evidence gap, stated as uncertainty; (4) the aims as one
sentence: lower-case roman numerals in parentheses, semicolons between aims, "and" before the
last, no colon after "aimed to", each aim an infinitive phrase.

Synthetic: *This analysis aimed to (i) estimate the effect of 16 weeks of balance training on
serum L-38; (ii) compare the rate of falls over 12 months between arms; (iii) relate
within-person changes in L-38 to changes in gait speed; and (iv) describe baseline L-38 by fall
history in the preceding year.* The Figure 1 legend (or a study-design figure) maps aims (i) to
(iv) to the analyses and their inference class, and the Results subsections follow the same
order.

### Methods

- One dense paragraph per subsection, mostly past passive; "we" at most once or twice.
- Participants: cohort sentence (group n, sex split, age range); consent and ethics sentence
  naming the committee, institution, city and country, the approval reference and date, and
  the Declaration of Helsinki with its revision; inclusion and exclusion criteria; where
  controls came from; sample-size justification cited to the approved protocol.
- Intervention: prescription per arm, phases (`weeks 0–16`), visits and adherence monitoring.
  Measurements: pre-analytical handling (clotting time, temperature, g-force, storage), assays
  with brand and method type, derived indices "as previously reported", and instrument and
  run conditions in full (`10 min at 94 °C, followed by 35 cycles of ...`). Reagents:
  `Name (Manufacturer, City, ST, Country)`, catalogue numbers in square brackets. A blinding
  sentence closes the measurement methods (*Laboratory staff did not know the allocation, and
  each participant's samples were assayed on one plate in random order.*).
- Estimand and analysis classes first: the opening sentence of the analysis methods states what
  was fixed in advance and where, the principal estimand, its relation to the protocol primary
  outcome, and which analyses are secondary and exploratory. Synthetic: *The statistical
  analysis plan, finalized before the allocation was revealed, named the between-arm ratio of
  geometric mean L-38 at week 16, adjusted for baseline, as the principal estimand; the Short
  Physical Performance Battery score, which the protocol named as the trial's primary outcome, is
  reported separately, falls were a randomized secondary outcome, and the gait-speed analyses
  were exploratory.*
- Statistical analysis is one paragraph in this order: (1) descriptive conventions; (2) analysis
  populations; (3) software with versions, including R or Python and key package versions;
  (4) normality assessment; (5) tests with sidedness and variant; (6) sensitivity analyses and
  their rationale; (7) outlier rule and its handling; (8) missing data; (9) significance
  threshold; (10) multiplicity per analysis family; (11) which document "prespecified" refers
  to (usually the statistical analysis plan; name the trial protocol only when it is cited);
  (12) model specification in words; (13) sensitivity models; (14) internal validation recipe;
  (15) external datasets, with a sentence saying they give background and do not validate the
  findings.
- Public data and bioinformatics: database names with versions, thresholds, accession IDs,
  tests and corrections, and what was not done.

### Results

- Headings are neutral finding statements or topics ("3.1 L-38 by randomized arm"), never
  causal ("Effect of ...").
- Paragraph pattern: a "what was done" opener (*We first estimated ...*); findings in descending
  importance, numbers in parentheses after the claim: the named effect (ratio, difference, fold
  change, AUROC, ρ), 95% CI, P, q, and the call-out last: *(ratio of geometric means 1.16, 95% CI
  1.05–1.28; P = 0.004; Figure 1A)*; a closing caveat that sets the inference class: *Because
  fall history was not randomized, this comparison is descriptive.*
- Randomized inference is the between-arm contrast with CI and P. Within-arm changes get CIs
  only, never within-arm P values.
- Exact tests give the exact P, its sidedness and its floor (*exact two-sided P = 0.031, the
  smallest value possible with six pairs*: 2/64 when all six differences share a sign). P
  qualifiers are prefixed adjectives: exact, nominal, unadjusted, Holm-adjusted.
- State multiplicity in the sentence (*q = 0.03 after Benjamini–Hochberg adjustment across the
  six secondary markers*). Do not add "did not survive correction" sentences the analysis plan
  does not call for.
- Nulls: *was not statistically significant*, *no difference was detected*, *the estimate was
  imprecise*, *compatible with no effect*. About the study's own results, "significant" always
  carries "statistically".

### Discussion, Conclusions and Limitations

| Block | Role |
| --- | --- |
| Opening paragraph (no heading) | Principal findings, each with its inference class; the primary result stated plainly even when null |
| First subsections | Clinical and secondary effects against the literature, ending in a synthesis sentence and an open question |
| Method subsection | The methodological implication: prior practice, what was done here, confirmation still needed |
| Comparison subsection | A preface on heterogeneity, then per analyte: prior report (citations), our finding, qualifier; closing summary |
| Mechanism subsection | What the association could reflect and what it cannot show; screening hits as leads only |
| Last subsection | A bounded clinical implication and the study that would test it |

- No promotional words: novel, first, unique, uniquely, potent, pivotal, groundbreaking. Keep the
  author's hedges; remove only doubled hedges. A strengths sentence (randomization, blinded
  processing) is optional: offer it, never force it.
- Conclusions: one paragraph of two or three sentences that restate the findings with their
  labels and end on the validation required before clinical use. Limitations: one paragraph of
  limitation-plus-consequence pairs, with no "This study has several limitations" opener.

## Claim ladder

| Evidence class | Phrasing (synthetic) |
| --- | --- |
| Randomized primary contrast | "the ratio of geometric means between arms was R (95% CI a–b; P = p)"; if null, "the estimate was imprecise and compatible with no effect of training" |
| Secondary family | "After [correction], no secondary marker differed between arms (k tested)." |
| Exploratory subgroup | "Among the n participants with paired samples (exploratory), L-38 rose in every pair (exact two-sided P = p)." |
| Post-randomization grouping | "grouping by adherence was not randomized, so this comparison is descriptive", plus the effect it cannot estimate |
| Internal prediction | "discrimination in the same data was modest (AUROC a)"; "an exploratory, probably optimistic estimate" |
| External or public data | "public datasets give background; they do not validate these results" |
| Mechanism | "consistent with ..., but not proof of it"; "hypotheses for further work"; "cannot show whether ..." |
| Clinical translation | "a candidate marker for further study"; "not ready for clinical use until an independent cohort confirms it" |

## Tense, voice and person

| Part | Tense and voice |
| --- | --- |
| Abstract | past for methods and results; present for the gap and the conclusion |
| Introduction | present for background; past for the aims |
| Methods | past passive |
| Results | past; present only for inference caveats ("is associational") |
| Discussion | present for established knowledge and interpretation; past for this study's findings |
| Conclusions | past for findings, present for the implication |
| Legends | present for what is shown ("Points are participants"); past for what was done |

- `language.first_person: allow` (journal default): "we" sparingly; "our" only in the
  Discussion. `avoid` (thesis): rephrase impersonally, for example "This study used ...".
- Long, semicolon-rich sentences are the author's register. Do not split them by reflex.

## Statistics and typography

The Lint column names the `msw.py lint` rule that checks an item; "review" means no rule does
([rule list](qa-passes.md#13-final-house-style-lint)).

| Item | House default | Never | Lint |
| --- | --- | --- | --- |
| P value | italic capital *P*, spaced: `P = 0.012`, `P < 0.05` (**Profile:** `statistics.p_style`, `p_italic`) | `p=0.012`, `P=0.012` | `stats.p-spacing`, `stats.p-case`, `stats.p-italic` |
| P precision | two significant figures; `P < 0.001` below 0.001 unless an exact value matters, then `P = 2.1 × 10⁻⁷` | `P = 0.000` | review |
| q value | italic *q*, spaced: `q = 0.031` | `q=0.031` | `stats.q-spacing` (italics: review) |
| Estimates | two decimals, or three significant figures for small values; full precision stays in source data | four significant figures in prose | review |
| 95% CI | `95% CI 1.05–1.28` in Results and legends; `95% confidence interval 1.05 to 1.28` in the Abstract; `%` on both bounds of percentage CIs (`11%–28%`) | `1.05-1.28` | `typo.range-dash` for the hyphen; rest review |
| Minus sign | U+2212: `−0.42` | hyphen-minus as a minus | `typo.minus` |
| Ranges | unspaced en dash (U+2013): `0–3`, `Figure 5A–C`; "to" when a bound is negative or signed: `−0.42 to −0.10` | hyphen in a range | `typo.range-dash` (numeric ranges) |
| Eponym pairs | en dash: `Mann–Whitney`, `Benjamini–Hochberg`, `Kenward–Roger` | `Mann-Whitney` | `typo.eponym-dash` (a fixed list of pairs) |
| Mean ± SD | spaced: `12.4 ± 3.1`, in text and tables | `12.4±3.1` | `stats.pm-spacing` |
| Sample size | `n = 12`, lower case, spaced, roman, in text, legends and tables | `n=12`, `N = 12` for a group | `stats.n-spacing` (spacing only) |
| Temperature | degree sign U+00B0, spaced: `4 °C` | `4ºC`, `4 oC` | `typo.degree-sign` (the `º` form) |
| Units | SI; non-breaking space between number and unit; `µL`, `mL`, `min`, `h`; one solidus at most (`U/mL`), negative exponents for compound units (`mL min⁻¹ kg⁻¹`) | `µl` beside `µL`, `min` beside `minutes` with numbers | review |
| Percent | attached: `12%` | `12 %` | review |
| Thousands | comma from four digits: `1,500`, `5,000`; none in years, accessions or catalogue numbers | `5 000`, `5.000` | review |
| Decimals | leading zero: `0.05` | `.05` | review |
| Multiplication | `×` (U+00D7): `2,000 × g` with italic *g* | `x` | review |
| Dashes in sentences | none; use commas, semicolons, colons or parentheses | em dashes as punctuation | review |
| Spacing | single ordinary spaces; keep existing non-breaking spaces, narrow spaces and non-breaking hyphens | two spaces; a space before punctuation; normalizing special spaces in an edit | `typo.double-space`, `typo.space-before-punctuation` |

Numbers: spell out one to ten for counts without units; numerals from 11, and for anything with
a unit or statistic. Spell out a number that starts a sentence (*Twenty-two controls ...*).
Proportions are "9 of 30 participants (30%)" in prose and `9/30` in legends. (Review.)

Call-outs: `Figure 2A`, `Figure 4D,E`, `Figures 2 and 3`, `Table 1`, `Supplementary Table 1`,
`Supplementary Figure 1` (**Profile:** `supplementary.cite_as`). Never "Fig."
(`callout.fig-abbreviation`). Figures are cited in order, each one (`figures.order`,
`figures.gap`, `figures.uncited`, `figures.missing-legend`). Section cross-references read
"(Results 3.4)".

## Abbreviations

- Define at first use in each scope as `full form (ABBR)`, then use the abbreviation. The scopes
  are the abstract, the body and each legend. Statistics abbreviations (CI, SD, SEM, AUROC)
  count too; universally known ones (DNA, RNA, SI units) are not expanded.
- The table lists the abbreviations used in the body (a thesis groups them by theme); it does
  not replace first-use definition. Do not re-define in the body after first use.
- Legends are self-contained and end with their own list: `CI, confidence interval; SD, standard
  deviation.` Spell out a term used only once.
- The lint's `abbrev.*` and `abstract.abbreviation` rules are heuristic (gene symbols and
  database names show up as undefined); list true universal forms in `lint.universal_abbreviations`.

## Nomenclature

| Species | Gene, mRNA, allele (italic) | Protein (roman) |
| --- | --- | --- |
| Human | *GENE1* | GENE1 |
| Mouse, rat | *Gene1* | GENE1 |
| Zebrafish | *gene1* | Gene1 |

- Gene cues: mRNA, transcript, knockout, promoter, qPCR, RNA-seq. Protein cues: antibody,
  phosphorylation, Western blot, ELISA, serum level. Italicize the symbol, not a plural ending.
  Complexes, pathways and phospho-sites (Ser473) are roman; anatomical abbreviations and
  fluorophores are not genes. The lint notes roman gene-like symbols beside gene cues
  (`nomenclature.gene-italic`, info); the reader decides.
- Use one form of each symbol document-wide, chosen by document-wide counts, with official
  product, vendor and cell-line names. Leave a comment to confirm provisional symbols.
- Italic (review; the italics scan in [QA passes](qa-passes.md)): species names (*Escherichia
  coli*, then *E. coli*) and Latin phrases in body text: *in vitro*, *in vivo*, *in situ*, *ex
  vivo*, *de novo*, *ad libitum*, *post hoc*, *a priori*, *et al.* Roman: e.g., i.e., etc., vs,
  via, per. The bibliography follows its EndNote style.
- Sequence motifs are set in a fixed-width font (Courier New).

## Spelling variety

`manuscript.json` `language.spelling` sets `en-US`, `en-GB` or `en-CA`; ask at bootstrap. The
thesis precedent is Canadian: -ize and -yze verbs (characterize, analyze), -our and -re nouns
(behaviour, fibre), doubled l (labelled, modelling), grey, with international biomedical forms
(estrogen, fetal, pediatric). Whatever the choice, one variety document-wide. The lint reports
words in another variety (`spelling.mixed`) and proofing-language tags that disagree
(`format.proofing-language`, `format.proofing-variety`); run it after every rewriting pass,
because rewrites reintroduce foreign forms.

## Figure legends

1. Title line (Heading 2): `Figure N: Sentence-case title.`, a noun phrase or a statement
   title. A thesis uses `Figure N. Title.` (see the thesis skeleton).
2. The picture, centred.
3. One legend paragraph, starting with `(A)`; panels run on as `(B) ...`, `(C) ...`. A scope
   sentence may precede `(A)` when panels summarize published evidence.
4. For each panel: what is plotted; groups with symbols or colours and n; what the marks mean
   (*Points are participants; horizontal lines show medians*); error bars or whiskers defined
   (SEM, SD or 95% CI); the test with sidedness and variant; adjustment status; the inference
   class. Disclose display artefacts: jitter or beeswarm spreading, offsets for legibility,
   axis ranges that differ between panels.
5. Then the significance key, `*P < 0.05, **P < 0.01 and ***P < 0.001.` with italic P, and last
   the abbreviation list.

Supplementary figures follow the same pattern (`Supplementary Figure 1: Title.`), not a separate
legend heading. Legend content is a review item. Figure style itself (text-light panels,
significance brackets only where significant) belongs to [figures](figures.md).

## Tables

- One editable file per table, portrait or landscape as needed, Arial 8 to 10 pt (**Profile:**
  `tables.separate_editable_files`). No internal lines and no shading (**Profile:**
  `tables.internal_rules`).
- The title sits above: `Table N: Title.` (thesis: `Table N. Title.`), one descriptive sentence
  naming the population, groups and time points.
- Header cells `Group (n = 18)` and a `P value` column; cells `12.4 ± 3.1 (n = 17)` when n varies
  by outcome.
- A paragraph after the table starts with bold `Table N legend.` and gives the summary statistic
  ("mean ± sample standard deviation"), data provenance, tests and abbreviations.
- Terms match the text exactly (not "gait speed" in the table and "walking speed" in the text).

## What the lint covers

`msw.py lint` checks the accepted view against the items marked in the sections above, plus
citation placeholders and adjacent groups, headings (empty, trailing colon or space, indent,
numbering), highlighting, and the profile's limits and declaration order; the rule list with
severities is in [QA passes](qa-passes.md#13-final-house-style-lint). It caught most
inconsistencies observed in a released draft of the author's. Review by reading: P precision,
estimates and CI wording; units, percent, thousands, decimals and multiplication signs; em
dashes; number words; Latin and species italics; blank-paragraph spacers; caption and
table-title separators across main and supplementary items; declaration wording and bold labels;
legend and table content; author names across the title page, contact blocks and Contributions;
and the writing conventions, claim ladder and tense tables.

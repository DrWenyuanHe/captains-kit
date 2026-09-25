# Journal profiles

A journal profile is a JSON summary of one journal's author guidelines, in this skill's own words,
with a link and retrieval date for every source. `msw.py new-manuscript`, `title-page`,
`wordcount` and `lint` read parts of it; the figure export step ([figures](figures.md)) and people
read the rest. Use this page to write a profile, to read one, and to retarget a manuscript to a
new journal.

## Where profiles live

- In a project: the file `manuscript.json` names in `journal_profile`, which `init` sets to
  `05_References/Journal_Guidelines/journal.profile.json` unless `--journal-profile` gives
  another path. `init` and `retarget` keep that path relative to the project and refuse a
  profile outside it. The tools load it by default (house defaults while it does not exist);
  `--profile <file>` overrides it for one command.
- In this skill: a [template](../assets/journal-profiles/template.profile.json) to copy, and a
  worked [Journal of Endocrinology example](../assets/journal-profiles/journal-of-endocrinology.profile.json).
  The example records when and how it was read; reverify it before relying on it.
- A profile is merged over the house defaults (`HOUSE_DEFAULTS` in `scripts/mswlib/profile.py`)
  key by key. A key left out or set to `null` keeps the house default. Any other value replaces
  it, including an empty list or string, which means "no rule": `"forbid": []` allows citations
  in the abstract, `"order": []` switches the declaration check off, and `"p_style": ""` with
  `"p_italic": ""` makes the lint follow the document's majority P style. To turn line numbers
  off, write `"line_numbers": "none"`.
- The shipped template leaves most keys `null` (house defaults) but has empty lists. Fill or
  delete `abstract.forbid` and `declarations.order`, or the house citation-in-abstract and
  declaration checks are off. An empty `formatting.language` falls back to en-US and en-GB.
- A file without `"schema": "journal-profile/1"` is refused.

## A thesis profile

Write it the same way, from the graduate school's formatting rules. The tools were made for
journal articles, so a thesis gets less from them. What the profile does control:

- `formatting`: page size, font, body size, body and reference line spacing, line numbers and
  spelling variety, applied by `new-manuscript` and checked by `lint`.
- `article_types.research`, `declarations` and `statistics`, read by `lint` and `wordcount`. Set
  `"keywords": {"min": 0}` and leave `word_limit.max` `null` unless the school sets a limit;
  otherwise the builder and the lint warn about the keywords a thesis does not have.

What stays manual, done in tracked passes or by the author in Word:

- Body indent and spacing after, heading sizes and alignment: the builder writes its journal
  styles (first-line indent, 8 pt after, left-aligned headings) whatever the profile says. The
  author adjusts the styles in Word, or the thesis starts from the school's Word template.
- The title page: the builder writes the journal form with keyword, word-count and running-title
  lines; give it the thesis form in the first tracked pass.
- Roman then arabic page numbering: the builder writes one section; the author sets the sections
  in Word.
- Front-matter lists with page numbers: no command measures the page of a heading or caption
  (`word-qa` reports only total pages, and the LibreOffice proof's pages are approximate). Take
  the numbers from the author's Word PDF and write them as plain text.
- Deposit: see [release](release.md#thesis-deposit).

## Schema

A synthetic profile showing every block (values are illustrative, not any journal's rules):

```json
{
  "schema": "journal-profile/1",
  "journal": {
    "id": "exj", "name": "Example Journal", "publisher": "Example Press",
    "sources": [
      {"kind": "author_guidelines", "url": "https://example.org/exj/authors", "retrieved": "2026-01-05", "method": "live"},
      {"kind": "submission_checklist", "url": "https://example.org/exj/checklist.pdf", "retrieved": "2026-01-05", "method": "user_file"}
    ],
    "reverify_before_submission": true,
    "conflicts": [
      {"topic": "citation_style", "a": "checklist: author-date", "b": "guidelines: numbered",
       "resolved_to": "b", "reason": "the journal-specific page controls"}
    ]
  },
  "article_types": {
    "research": {
      "word_limit": {"max": 5000, "excludes": ["references", "figure_legends"], "counting_method": "house",
                     "severity": "warn"},
      "abstract": {"max_words": 250, "format": "single_paragraph", "forbid": ["abbreviations", "citations"],
                   "required_elements": ["objective", "methods", "results", "conclusions"]},
      "title": {"max_chars": 120},
      "short_title": {"max_chars": 46, "counts_spaces": true},
      "keywords": {"min": 4, "max": 6},
      "figures": {"recommended_max": 8},
      "references": {"recommended_max": 50},
      "sections": ["title_page", "abstract", "introduction", "methods", "results", "discussion",
                   "declarations", "references"]
    }
  },
  "title_page": {"separate_file": true,
                 "items": ["full_title", "authors", "affiliations", "corresponding_author",
                           "keywords", "word_count", "short_title"]},
  "declarations": {"order": ["declaration_of_interest", "funding", "author_contributions",
                             "acknowledgements", "ai_disclosure"],
                   "templates": {},
                   "guidance": {"ai_disclosure": "name each tool with its version or model and its use"},
                   "credit_statement": "expected"},
  "formatting": {"page": "a4", "font": "Times New Roman", "font_size_pt": 12, "line_spacing": "double",
                 "references_line_spacing": "double", "line_numbers": "continuous",
                 "language": ["en-GB", "en-US"]},
  "references": {"system": "numbered", "order": "citation", "et_al_after_authors": 3,
                 "endnote_style": "Vancouver"},
  "statistics": {"p_style": "P = 0.012", "p_italic": true, "q_style": "q = 0.03", "n_style": "n = 12",
                 "mean_sd_style": "12.4 ± 3.1", "require_threshold_statement": true,
                 "outlier_removal": "discouraged_justify"},
  "figures": {"panel_labels": {"case": "upper", "position": "top_left"},
              "max_width_mm": 180, "max_width_assumed": true,
              "font": {"family": ["Arial", "Helvetica"], "min_pt": 6},
              "files": {"separate_upload": true, "formats_preferred": ["tiff", "eps"],
                        "tiers": {"line_graph": {"dpi": 1200}, "greyscale": {"dpi": 600},
                                  "colour": {"dpi": 300, "colour_mode": "CMYK"}},
                        "per_file_max_mb": 10, "combined_upload_max_mb": 40}},
  "tables": {"separate_editable_files": true, "internal_rules": "forbidden"},
  "supplementary": {"cite_as": ["Supplementary Table {n}", "Supplementary Figure {n}"], "number_prefix": ""},
  "revision": {"markup": "highlight_or_colour", "response_letter": "required",
               "track_changes_accepted": "unverified"},
  "policies": {"ethics_committee_named": true, "consent_type_stated": true, "ai_disclosure": "required"},
  "lint": {"disabled_rules": [], "universal_abbreviations": ["ELISA"]}
}
```

| Block | What it holds | Read by |
| --- | --- | --- |
| `schema` | Always `journal-profile/1` | all tools |
| `notes` | Free text per block: what the summary covers and any assumption | people |
| `journal` | `id` (short, used in names and reports), `name`, `sources` (each with `kind`, `url`, `retrieved` as an ISO date, and `method`: `live`, `user_paste`, `user_file` or `search_snippet`), `reverify_before_submission`, and `conflicts` (both readings, `resolved_to` and `reason`) | `lint` report header (`name`, `id`, `sources`), `wordcount` (`id`); people |
| `article_types` | One entry per article type; `research` is the default and the fallback. `word_limit` (`max`, `excludes`, `severity`); `abstract` (`max_words`, `format` `single_paragraph` or `structured`, `forbid` with `abbreviations`, `citations` or `references`, `required_elements`); `title` (`max_chars`); `short_title` (`max_chars`, `counts_spaces`, default true); `keywords` (`min`, `max`); `figures` and `references` (`recommended_max`); `word_limit.counting_method`; `sections` | `lint` (all but `required_elements`, `counting_method` and `sections`; `--article-type` picks the type); `wordcount` and `new-manuscript` (`word_limit.max`, `abstract.max_words`; `new-manuscript` also title, running-title and keyword limits, as warnings); `wordcount`, `new-manuscript` and `title-page` (`word_limit.excludes`, which words the title page's word-count parenthesis, with a warning where the journal excludes something the method counts) |
| `title_page` | `separate_file`, `items` in the order the journal lists them, `must_match_submission_system` | `new-manuscript` (`separate_file`: the Abstract starts on a new page, and a note asks for `--separate-title-page`); people |
| `declarations` | `order` of the declaration keys (`author_contributions`, `data_availability`, `acknowledgements`, `declaration_of_interest`, `funding`, `ai_disclosure`, `ethics`, `consent`, `preprint`); `templates`, the journal's required sentences, filled in by the author from the live page; `guidance`, your paraphrase of what each statement must contain; `credit_statement` | `lint` (`order`: presence and order, found as headings or `Label:` lines); drafting |
| `formatting` | `page`, `font`, `font_size_pt`, `line_spacing`, `references_line_spacing`, `line_numbers`, `language` (accepted varieties); values below | `new-manuscript` and `title-page`; `lint` (`references_line_spacing`, `language`) |
| `references` | `system` (`numbered`, `author_date`), `order`, `et_al_after_authors`, `endnote_style`, and notes on unpublished, web-only and in-press sources | the author's EndNote style choice; reference audit |
| `statistics` | Example strings that fix formats: `p_style` (spacing and the case of P), `q_style`, `n_style`, `mean_sd_style` (spacing), `p_italic` (true or false); plus reporting rules (`require_threshold_statement`, `outlier_removal`) | `lint` (the style keys); people |
| `figures` | `numbering`, `legend_must_state`, `panel_labels`, `max_width_mm` with `max_width_assumed`, `font` (`family`, `min_pt`), and `files`: formats, dpi tiers, colour mode, CMYK profile, per-file and combined size limits | no `msw.py` command; the `nature-figure` contract and the journal export step ([figures](figures.md)) |
| `tables` | `separate_editable_files`, `internal_rules`, shading, title and footnote rules | people |
| `supplementary` | `cite_as` patterns with `{n}`, `number_prefix` (`""` or `"S"`), the upload label | people; call-out checks by eye |
| `revision` | `markup` (`track_changes`, `highlight_or_colour` or `either`), `response_letter`, `track_changes_accepted` (`yes`, `no`, `unverified`) | people, when choosing the revised manuscript's copies ([reviewer response](reviewer-response.md#6-choose-tracked-or-highlighted-markup)) |
| `policies` | Ethics, consent, data deposition, preprint and AI-disclosure requirements | people |
| `lint` | Local tuning: `disabled_rules` (rule ids or prefixes as printed with each finding, such as `typo.double-space` or `nomenclature.`) and extra `universal_abbreviations` | `lint` |

Values the tools accept:

| Key | Accepted values |
| --- | --- |
| `formatting.page` | `letter` (also `us letter`) or `a4`; anything else gives Letter |
| `formatting.font_size_pt` | a positive number |
| `formatting.line_spacing`, `references_line_spacing` | `single`, `1.15`, `1.5`, `one_and_half`, `double`, or a number of lines (at least 1). The lint compares the reference list only for `single`, `1.5` and `double` |
| `formatting.line_numbers` | `continuous`; restart each page: `page`, `each_page`, `per_page`, `newpage`; restart each section: `section`, `each_section`, `per_section`, `newsection`; off: `none`, `off`, `no`, `false` or JSON `false`. An unknown value gives `continuous` without a warning |
| `formatting.language` | `en-US`, `en-GB`, `en-CA` (`en-AU` for `new-manuscript` only). `new-manuscript` uses the first when `manuscript.json` has no `language.spelling`; the lint picks the dominant variety among them |
| `word_limit.severity` | `error`, `warn` or `info`; `hard` and `fail` count as `error` for the lint, and `wordcount` exits 1 over the limit with `error`, `hard` or `fail` |

`wordcount` takes its method from `--method` or `manuscript.json` `word_count.method`, not from
`counting_method`, which records the journal's wording for people. Blocks the tools do not read
(the example also has `package`, `nomenclature` and `image_integrity`) are notes for people. When
a journal is silent on a value the tools need, leave it out or `null` (the house default) and say
so in `notes`; for figure width, set `max_width_assumed`.

## Create a profile from a guideline page

1. **Find the sources.** The journal's current author guidelines, the page for the article type,
   and any submission checklist. Record each URL.
2. **Read them.** Fetch the live page. If a bot check blocks it, ask the author to paste the text
   or save the page into `05_References/Journal_Guidelines/` (method `user_paste` or
   `user_file`). Use search snippets only as a last resort, mark them `search_snippet`, and set
   `reverify_before_submission`. *Observed:* one publisher's page sat behind a bot check, and
   several rules were first read from snippets.
3. **Keep the author's copy private.** The author wants the guideline saved in the project; a
   dated copy in `05_References/Journal_Guidelines/` stays there and never enters this library.
4. **Start from the template.** Copy
   [template.profile.json](../assets/journal-profiles/template.profile.json) as a new file to the
   path `journal_profile` names, `05_References/Journal_Guidelines/journal.profile.json` by
   default. For a retarget the old profile stays as a record: write the new journal's profile as
   a new file, for example `05_References/Journal_Guidelines/<journal_id>.profile.json`.
5. **Summarize, do not copy.** Enter limits as numbers and lists, and describe required content
   in your own words in `guidance` and `notes`. Where the journal mandates exact wording (a
   standard conflict-of-interest sentence, for example), the author copies it from the live
   page into `templates` and the manuscript. Keys the journal is silent on stay `null` or go;
   replace the template's empty lists as described above.
6. **Record provenance.** Each source gets `kind`, `url`, `retrieved` (ISO date) and `method`.
7. **Record conflicts.** When two pages disagree, keep both readings, the resolution and the
   reason. The journal-specific page beats a generic publisher checklist; a newer page beats an
   older one. *Observed:* a checklist said author-date citations and a flat resolution, the
   journal page said numbered citations and tiered resolution; the project followed the journal
   page and marked it for re-verification.
8. **Try it.** Run `msw.py lint <current.docx> --profile <profile>` and
   `msw.py wordcount <current.docx> --profile <profile>`. A malformed profile is refused (exit 2);
   a surprising finding usually means a misread rule.
9. **Register it.** At bootstrap `manuscript.json` already names the file. For a profile under
   another name (a retarget), `msw.py retarget --journal-profile <path>` names it (below). Record
   the choice in `06_Project_Docs/DECISIONS.md`.
10. **Reverify before submission.** Re-read the live pages, update `retrieved`, and change any
    rule that moved.

## Retarget to a new journal

A retarget is a normal tracked pass; version numbers keep counting and only the venue code in
file names changes. *Observed:* before reformatting, the author asked for a detailed comparison
of the journal's requirements with the current text, and for the guideline to be saved in the
project.

1. Write the new profile as a new file (above). With the author's agreement, run
   `msw.py retarget --venue JOE --target-journal "<full journal name>" --journal-profile
   05_References/Journal_Guidelines/<journal_id>.profile.json --dry-run`, read the plan, then run
   it again without `--dry-run`. It copies the previous `manuscript.json` into
   `99_Archive/<date>/Retarget_from_<old venue>/`, sets `venue` and `journal_profile` there and
   `venue` and `target_journal` in `PROJECT_INDEX.json`, refreshes the README and STATUS blocks,
   and writes a `retarget` ledger event (pending, then complete); if it stops part way, run the
   same command again to finish it ([release](release.md#if-it-stops-part-way)). Released files keep their names;
   new ones use the new venue code. It lists the hand-written lines of `AGENTS.md`, `CLAUDE.md`,
   `README.md` and `06_Project_Docs/Workflows/AGENTS.msw.md` that still name the old venue code:
   update them by hand, and record the retarget and its reason in `DECISIONS.md`.
2. Compare requirement against text and report first: a table of requirement, current state,
   proposed action and who decides, saved in the pass folder. Cuts to meet a word limit are the
   author's decision; offer numbered options that show where the words go.
3. Work through the checklist:

| Area | Check |
| --- | --- |
| Title and abstract | Title and running-title characters (and whether spaces count), keyword count, abstract words, structured or single paragraph, required elements, abbreviations and citations allowed |
| Length | Word limit, what it excludes, which counting scope matches the journal's wording; recommended maximum figures and references |
| Structure | Section names (`Methods` or `Materials and methods`) and order; title page inside the manuscript or a separate file |
| Declarations | Order and required content: interests, funding, contributions (CRediT), acknowledgements, AI use, data availability, ethics and consent |
| References | The EndNote output style (numbered or author-date; authors before "et al."). The author switches the style in EndNote; agents never reformat the bibliography |
| Figures | Separate files, formats, dpi tier per figure, colour mode and CMYK profile, width, fonts, panel-label case and position, per-file and combined size limits |
| Tables | Separate editable files, no internal lines or shading, one-sentence titles, abbreviations in footnotes |
| Supplementary | Naming (`Supplementary Figure 1` or `S1`), file format, upload label |
| Formatting | Line spacing including references and legends, line numbers, page size, spelling variety |
| Statistics | Threshold statement, outlier policy, multiple comparisons, individual points over bars |
| Revision | Tracked changes or highlighted text, response letter |

4. Make the changes as tracked passes ([revision](revision.md)), then recount, lint, proof and
   review. Journal files for figures come from the export run in [figures](figures.md).

## What the tools check

| Requirement | Checked by | Mode |
| --- | --- | --- |
| Word limit, stated title-page count, abstract words | `msw.py wordcount`, `msw.py lint` | automatic; severity from `word_limit.severity` |
| Title, running-title and keyword limits | `msw.py lint` (also warned by `new-manuscript`) | automatic; with a separate title page, lint that file too: a file with no headings and a `Keywords:`, `Running title:`, `Word count:` or `*Corresponding author` line is linted as a title page (`scope: title_page`), with only these limit checks |
| Abstract as one paragraph; abbreviations or citations in it | `msw.py lint` | automatic (abbreviations heuristic) |
| Declarations present and in profile order | `msw.py lint` | automatic by heading and label; wording by hand |
| Reference-list spacing, reference count, figure count, figure call-out order | `msw.py lint` | automatic; the spacing finding is a reminder, because EndNote's Layout settings control the spacing ([EndNote](endnote.md#the-authors-rule-hands-off)) |
| P style; q, n and ± spacing; minus, en dash, degree sign; spelling variety; leftover placeholders | `msw.py lint` | automatic ([rule list](qa-passes.md#13-final-house-style-lint)) |
| Bibliography numbering, duplicate or missing DOIs, metadata against PubMed or Crossref | `msw.py refs bib-audit` | automatic after the author's EndNote refresh |
| Figure fonts, overlaps and panel alignment | `nature-figure` auditors in the figure gate | automatic, plus tile review |
| Figure dpi, colour mode, width and file-size budgets | the journal export run ([figures](figures.md#journal-exports)) | scripted in the export run, blocking; no `msw.py` command |
| Page size, font, spacing and line numbers of a new draft | `msw.py new-manuscript` applies them | automatic at build; `msw.py proof` to look |
| Required statement wording, ethics and consent details, AI statement content | review | manual |
| Abstract required elements, section names, legend content (organism, n, tests, symbols) | review | manual |
| Table rules in separate table files, supplementary naming | review | manual |
| Revision markup: tracked, highlighted or clean copies | the author, from `revision` ([reviewer response](reviewer-response.md#6-choose-tracked-or-highlighted-markup)) | manual |
| Title-page data matching the submission system, image integrity | the author | manual |

List every manual item in the report as done or open, so nothing rests on the tools alone.

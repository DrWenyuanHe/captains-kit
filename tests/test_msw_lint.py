"""Tests for the scientific-manuscript lint (house style and journal profiles).

Every document is synthetic: a fictional serum marker "L-38" in a fictional
balance-training trial in older adults, built with the shared fixture helpers.
"""

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "scientific-manuscript"
sys.path.insert(0, str(SKILL / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from msw_fixtures import AUTHOR_OTHER, NS, cite_inline, make_docx, para, record, run  # noqa: E402
from mswlib import lint as L  # noqa: E402
from mswlib import template  # noqa: E402
from mswlib.profile import HOUSE_DEFAULTS, load_profile  # noqa: E402

PROFILES = SKILL / "assets" / "journal-profiles"
JOE = PROFILES / "journal-of-endocrinology.profile.json"
EXAMPLES = SKILL / "assets" / "examples"
SUP = '<w:vertAlign w:val="superscript"/>'
I = "<w:i/>"
C1 = record(1, "Alpha", 2020, "First fixture paper", "10000001")
C2 = record(2, "Beta", 2021, "Second fixture paper", "10000002")
SECT = ('<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1800" w:bottom="1440" '
        'w:left="1800" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>')


def P(value="0.012", op="=", italic=True, spaced=True, letter="P"):
    """An italic (or roman) P value as runs."""
    gap = " " if spaced else ""
    return run(letter, I if italic else "") + run(f"{gap}{op}{gap}{value}")


class Doc:
    """Collects paragraphs (w:p XML) and wraps them in a document part."""

    def __init__(self):
        self.parts = []
        self.number = 0x20000000

    def next_id(self):
        self.number += 1
        return f"{self.number:08X}"

    def p(self, content, style=None, ppr=""):
        self.parts.append(para(content, self.next_id(), style, ppr))
        return self

    def t(self, text, style=None):
        return self.p(run(text), style)

    def h1(self, text):
        return self.t(text, "Heading1")

    def h2(self, text):
        return self.t(text, "Heading2")

    def raw(self, xml):
        self.parts.append(xml)
        return self

    def table(self, rows):
        cells = "".join("<w:tr>" + "".join(f'<w:tc><w:tcPr><w:tcW w:w="3000" w:type="dxa"/></w:tcPr>'
                                           f"{para(run(cell), self.next_id())}</w:tc>" for cell in row) + "</w:tr>"
                        for row in rows)
        return self.raw('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr><w:tblGrid>'
                        '<w:gridCol w:w="3000"/><w:gridCol w:w="6000"/></w:tblGrid>' + cells + "</w:tbl>")

    def bibliography(self):
        self.h1("References")
        self.p('<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> ADDIN '
               'EN.REFLIST </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
               '<w:bookmarkStart w:id="100" w:name="_ENREF_1"/><w:r><w:t>1.</w:t></w:r><w:r><w:tab/>'
               '<w:t>Alpha A. First fixture paper. J Fixture. 2020;1:100-9.</w:t></w:r><w:bookmarkEnd w:id="100"/>',
               "EndNoteBibliography")
        self.p('<w:bookmarkStart w:id="101" w:name="_ENREF_2"/><w:r><w:t>2.</w:t></w:r><w:r><w:tab/>'
               '<w:t>Beta B. Second fixture paper. J Fixture. 2021;1:100-9.</w:t></w:r><w:bookmarkEnd w:id="101"/>'
               '<w:r><w:fldChar w:fldCharType="end"/></w:r>', "EndNoteBibliography")
        return self

    def xml(self):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
                f"<w:document {NS}><w:body>" + "".join(self.parts) + SECT + "</w:body></w:document>").encode()

    def docx(self):
        return make_docx(document=self.xml())


def manuscript(word_count="0", results_extra=(), abstract=None, headings_extra=(), declarations=None,
               legends=None, callouts=("1A", "1B", "2")):
    """A complete, house-style manuscript; `results_extra` adds paragraphs (w:p content) to the Results."""
    d = Doc()
    d.p(run("Serum marker L-38 and falls in older adults after balance training", '<w:b/><w:sz w:val="24"/>'))
    d.p(run("Given A. Surname") + run("1", '<w:vertAlign w:val="superscript"/>') + run(" and Other Surname")
        + run("1,2*", '<w:vertAlign w:val="superscript"/>'))
    d.t("Department of Example, Example University, City, Country")
    d.p(run("Keywords:", "<w:b/>") + run(" marker, older adults, balance, falls"))
    d.t(f"Word count: {word_count} words (excluding references and figure legends)")
    d.p(run("Running title: L-38 and falls in older adults", "<w:b/>"))
    d.h1("Abstract")
    if abstract is None:
        d.p(run("Older adults were randomized to 16 weeks of balance training or stretching, and serum marker L-38 was "
                "measured before and after. Values were log-transformed and analyzed with two-sided tests. At week "
                "16 L-38 was higher after training; the ratio of geometric means was 1.16 (95% confidence interval "
                "1.05 to 1.28; ") + P("0.004") + run("). Its value for predicting falls needs a larger trial."))
    else:
        d.p(abstract)
    d.t("Abbreviations")
    d.table([["Abbreviation", "Definition"], ["SPPB", "Short Physical Performance Battery"]])
    d.h1("1. Introduction")
    d.p(run("Falls are common in older adults ") + cite_inline(1, C1)
        + run(". The Short Physical Performance Battery (SPPB) follows function, but SPPB scores change slowly. "
              "This analysis aimed to (i) characterize the L-38 response to training and (ii) relate it to falls."))
    d.h1("2. Methods")
    d.h2("2.1 Participants")
    d.t("Ninety-six adults took part (n = 48 per arm), aged 70\u201389 years. Serum was stored at \u221280 \xb0C and "
        "assays were run at 4 \xb0C.")
    d.h2("2.2 Statistical analysis")
    d.p(run("Values are mean \xb1 SD (12.1 \xb1 3.4). Arms were compared with Mann\u2013Whitney tests, and the "
            "Benjamini\u2013Hochberg procedure controlled the false discovery rate. A two-sided ") + P("0.05", "<")
        + run(" was taken as statistically significant."))
    for extra in headings_extra:
        d.raw(extra)
    d.h1("3. Results")
    d.h2("3.1 Marker levels")
    first, second, third = (list(callouts) + ["", "", ""])[:3]
    d.p(run(f"L-38 was higher after training (ratio 1.16, 95% CI 1.05\u20131.28; ") + P("0.004")
        + run(f"; Figure {first}) and correlated inversely with sway area (\u03c1 = \u22120.45; ") + P("0.003")
        + run(f"; Figure {second}). Fewer falls were recorded in the training arm (Figure {third})."))
    for extra in results_extra:
        d.p(extra)
    d.h2("3.2 Falls by arm")
    d.p(run("Falls were fewer after training, but the estimate was imprecise (") + P("0.12")
        + run("; Figure 2)."))
    d.h1("4. Discussion")
    d.p(run("L-38 rose with training, and sway area fell in the same participants ") + cite_inline(2, C2)
        + run("."))
    d.h1("5. Conclusions")
    d.t("L-38 is a candidate marker of training response that needs external validation.")
    d.h1("Limitations")
    d.t("The trial was small.")
    blocks = declarations if declarations is not None else [
        ("h", "Contributions"), ("label", "Given A. Surname:", " Conceptualization and writing."),
        ("h", "Data availability"), ("text", "Deidentified data can be requested from the corresponding author."),
        ("h", "Acknowledgements"), ("text", "We thank the participants."),
        ("label", "Declaration of interest:", " None declared."),
        ("label", "Funding:", " Example Foundation grant 123."),
        ("label", "Use of generative AI:", " No generative tool was used.")]
    for block in blocks:
        if block[0] == "h":
            d.h1(block[1])
        elif block[0] == "label":
            d.p(run(block[1], "<w:b/>") + run(block[2]))
        else:
            d.t(block[1])
    d.bibliography()
    d.h1("Figure Legends")
    for number, title, legend in legends if legends is not None else [
            (1, "Serum L-38 by arm.", run("(A) Individual values in the stretching and training arms "
                                          "(n = 48 each). (B) Association with sway area. Points are "
                                          "participants; two-sided tests. *") + P("0.05", "<") + run(".")),
            (2, "Falls by arm.", run("Cumulative falls with 95% CIs over 12 months. CI, confidence interval."))]:
        d.h2(f"Figure {number}: {title}")
        d.p(legend)
    return d


def clean_docx(**kwargs):
    """The house-style manuscript with the correct title-page word count."""
    draft = L.lint(manuscript(**kwargs).docx())
    words = draft.measures["word_count"]["words"]
    return manuscript(word_count=f"{words:,}", **kwargs).docx()


def with_results(*paragraph_contents, **kwargs):
    return manuscript(results_extra=paragraph_contents, **kwargs).docx()


def rules_of(report, severity=None):
    return {f.rule for f in report.findings if severity is None or f.severity == severity}


def findings(report, rule):
    return [f for f in report.findings if f.rule == rule]


class CleanDocumentTest(unittest.TestCase):
    def test_clean_manuscript_has_no_errors_or_warnings(self):
        report = L.lint(clean_docx())
        problems = [f.to_dict() for f in report.findings if f.severity in ("error", "warn")]
        self.assertEqual(problems, [])
        self.assertEqual(report.counts["error"], 0)
        self.assertEqual(report.spelling["variety"], "en-US")
        self.assertEqual(report.measures["figures"], 2)
        self.assertEqual(report.measures["references"], 2)
        self.assertEqual(report.measures["keywords"], 4)
        self.assertEqual(report.measures["stated_word_count"], report.measures["word_count"]["words"])

    def test_clean_manuscript_against_joe_profile_has_no_errors(self):
        report = L.lint(clean_docx(), profile=PROFILES / "journal-of-endocrinology.profile.json")
        self.assertEqual([f.to_dict() for f in report.errors], [])
        # House declaration order differs from the journal's; references are single-spaced.
        self.assertIn("declarations.order", rules_of(report))
        self.assertIn("format.reference-spacing", rules_of(report))

    def test_every_finding_is_well_formed(self):
        report = L.lint(with_results(run("Mann-Whitney  test ,") + P("0.03", spaced=False, italic=False)
                                     + run(" [PMID: 12345678] (4) (5) 10-20 min at 4\xbaC")))
        self.assertTrue(report.findings)
        for finding in report.findings:
            self.assertIn(finding.rule, L.RULES)
            self.assertIn(finding.severity, L.SEVERITIES)
            self.assertLessEqual(len(finding.snippet), 80)
            self.assertTrue(finding.message)
            if finding.paragraph is not None:
                self.assertTrue(finding.para_id)


class StatisticsTest(unittest.TestCase):
    def test_p_spacing_case_and_italics(self):
        report = L.lint(with_results(run("Unspaced (") + P("0.03", spaced=False) + run(")."),
                                     run("Lower case (") + P("0.04", letter="p") + run(")."),
                                     run("Roman (") + P("0.02", italic=False) + run(").")))
        self.assertEqual(len(findings(report, "stats.p-spacing")), 1)
        self.assertEqual(len(findings(report, "stats.p-case")), 1)
        self.assertEqual(len(findings(report, "stats.p-italic")), 1)
        self.assertIn("P = ", findings(report, "stats.p-spacing")[0].suggestion)

    def test_profile_can_ask_for_roman_unspaced_p(self):
        profile = load_profile()
        profile["statistics"]["p_style"] = "P=0.012"
        profile["statistics"]["p_italic"] = False
        report = L.lint(clean_docx(), profile=profile)
        self.assertTrue(findings(report, "stats.p-spacing"))
        self.assertTrue(findings(report, "stats.p-italic"))

    def test_q_n_and_plus_minus_spacing_follow_the_majority(self):
        report = L.lint(with_results(run("Tests gave q = 0.02, q = 0.03 and q=0.04."),
                                     run("Groups had n = 12, n = 14 and n=15."),
                                     run("Values were 1.2 \xb1 0.3, 2.2 \xb1 0.4 and 3.1\xb10.2.")))
        self.assertEqual([f.snippet.count("q=0.04") for f in findings(report, "stats.q-spacing")], [1])
        self.assertEqual(len(findings(report, "stats.n-spacing")), 1)
        self.assertEqual(len(findings(report, "stats.pm-spacing")), 1)


class TypographyTest(unittest.TestCase):
    def test_hyphen_minus_before_numbers(self):
        report = L.lint(with_results(run("The correlation was negative (\u03c1 = -0.45) and the change was -1.2 units; "
                                         "marker L-38 and range 12\u201317 are fine.")))
        self.assertEqual(len(findings(report, "typo.minus")), 2)
        self.assertIn("U+2212", findings(report, "typo.minus")[0].suggestion)

    def test_eponym_pairs_need_an_en_dash(self):
        report = L.lint(with_results(run("We used Mann-Whitney, Benjamini-Hochberg, Kenward-Roger, "
                                         "D'Agostino-Pearson and Kruskal-Wallis procedures.")))
        found = findings(report, "typo.eponym-dash")
        self.assertEqual(len(found), 5)
        self.assertIn("Mann\u2013Whitney", found[0].suggestion)

    def test_masculine_ordinal_used_for_degrees(self):
        report = L.lint(with_results(run("Samples were kept at 4\xbaC and at 20 \xba C.")))
        self.assertEqual(len(findings(report, "typo.degree-sign")), 2)

    def test_range_hyphen_between_numbers(self):
        report = L.lint(with_results(run("Sessions lasted 10-20 min on 2026-09-24 for marker L-38 (ORCID "
                                         "0000-0002-1825-0097) ") + cite_inline("2-3", C1) + run(".")))
        found = findings(report, "typo.range-dash")
        self.assertEqual(len(found), 1, [f.snippet for f in found])
        self.assertIn("10\u201320", found[0].suggestion)

    def test_double_space_and_space_before_punctuation(self):
        report = L.lint(with_results(run("Two  spaces here and a space before a comma , here; but P\xa0 = 0.5 "
                                         "keeps an NBSP and 10\xa0% is fine.")))
        self.assertEqual(len(findings(report, "typo.double-space")), 1)
        self.assertEqual(len(findings(report, "typo.space-before-punctuation")), 1)

    def test_fig_abbreviation(self):
        report = L.lint(with_results(run("As shown in Fig. 1, values rose.")))
        self.assertEqual(len(findings(report, "callout.fig-abbreviation")), 1)


class SpellingTest(unittest.TestCase):
    SENTENCE = "Values were normalized, normalized again, normalized once more and normalised at the end."

    def test_dominant_variety_and_minority_occurrence(self):
        report = L.lint(with_results(run(self.SENTENCE)))
        found = findings(report, "spelling.mixed")
        self.assertEqual([f.suggestion for f in found], ["write 'normalized'"])
        self.assertEqual(report.spelling["variety"], "en-US")
        self.assertIn("spelling.variety", rules_of(report))

    def test_chosen_variety_reports_every_deviation(self):
        report = L.lint(with_results(run(self.SENTENCE)), spelling="en-GB")
        self.assertEqual(report.spelling["source"], "--spelling")
        # three "normalized" in the extra paragraph plus the US forms of the clean text
        # (randomized, analyzed and characterize)
        suggestions = sorted(f.suggestion for f in findings(report, "spelling.mixed"))
        self.assertEqual(suggestions, ["write 'analysed'", "write 'characterise'"] + ["write 'normalised'"] * 3
                         + ["write 'randomised'"])

    def test_canadian_uses_ize_with_our_re_and_double_l(self):
        text = ("Signals were normalized and analyzed; the colour of the centre panel showed signalling in "
                "pediatric samples.")
        report = L.lint(with_results(run(text)), spelling="en-CA")
        self.assertEqual(findings(report, "spelling.mixed"), [])
        report = L.lint(with_results(run(text + " The color changed.")), spelling="en-CA")
        self.assertEqual([f.suggestion for f in findings(report, "spelling.mixed")], ["write 'colour'"])

    def test_manuscript_json_spelling_is_used(self):
        report = L.lint(with_results(run("The colour changed.")), config={"language": {"spelling": "en-GB"}})
        self.assertEqual(report.spelling["source"], "manuscript.json")
        self.assertEqual(report.spelling["variety"], "en-GB")


class CitationTest(unittest.TestCase):
    def test_unconverted_placeholders_are_errors(self):
        report = L.lint(with_results(run("Earlier work [PMID: 12345678] and {Alpha, 2020 #1} and [REF: smith2020] "
                                         "agree.")))
        found = findings(report, "citation.placeholder")
        self.assertEqual(len(found), 3)
        self.assertTrue(all(f.severity == "error" for f in found))

    def test_adjacent_citation_groups(self):
        report = L.lint(with_results(run("Two reports agree (4) (5).")))
        found = findings(report, "citation.adjacent-groups")
        self.assertEqual(len(found), 1)
        self.assertIn("(4, 5)", found[0].suggestion)

    def test_citation_in_abstract_is_an_error(self):
        report = L.lint(manuscript(abstract=run("An abstract that cites a paper ") + cite_inline(1, C1)
                                   + run(".")).docx())
        self.assertIn("abstract.citation", rules_of(report, "error"))


class HeadingTest(unittest.TestCase):
    def test_heading_hygiene(self):
        extra = (para("", "30000001", "Heading2"),
                 para(run("2.3 Trailing colon:"), "30000002", "Heading2"),
                 para(run("2.4 Trailing space\xa0"), "30000003", "Heading2"),
                 para(run("2.5 Trailing tab\t"), "30000004", "Heading2"),
                 para(run("2.7 Skipped number"), "30000005", "Heading2"),
                 para(run("2.8 Indented"), "30000006", "Heading2", '<w:ind w:firstLine="720"/>'))
        report = L.lint(manuscript(headings_extra=extra).docx())
        self.assertEqual(len(findings(report, "heading.empty")), 1)
        self.assertEqual(findings(report, "heading.empty")[0].severity, "error")
        messages = [f.message for f in findings(report, "heading.trailing")]
        self.assertEqual(len(messages), 3)
        self.assertTrue(any("colon" in m for m in messages) and any("tab" in m for m in messages)
                        and any("non-breaking" in m for m in messages))
        numbering = findings(report, "heading.numbering")
        self.assertEqual(len(numbering), 1)
        self.assertIn("2.6", numbering[0].message)
        self.assertEqual(len(findings(report, "heading.indent")), 1)


class FormattingTest(unittest.TestCase):
    def test_residual_highlighting(self):
        report = L.lint(with_results(run("A highlighted clause", '<w:highlight w:val="yellow"/>') + run(" stays.")))
        found = findings(report, "format.highlight")
        self.assertEqual(len(found), 1)
        self.assertIn("highlighted clause", found[0].snippet)

    def test_mixed_proofing_languages(self):
        report = L.lint(with_results(run("Spanish-tagged words here.", '<w:lang w:val="es-ES"/>'),
                                     run("English-tagged words here.", '<w:lang w:val="en-US"/>')))
        found = findings(report, "format.proofing-language")
        self.assertEqual(len(found), 1)
        self.assertIn("es-ES", found[0].message)


class AbbreviationTest(unittest.TestCase):
    def test_undefined_and_late_definitions(self):
        report = L.lint(with_results(run("XYZ levels were high."),
                                     run("ABC rose first; the acid buffer capacity (ABC) was measured later, "
                                         "and ABC stayed high while ABC recovered.")))
        undefined = findings(report, "abbrev.undefined")
        self.assertEqual([f.snippet.count("XYZ") > 0 for f in undefined], [True])
        early = findings(report, "abbrev.before-definition")
        self.assertEqual(len(early), 1)
        self.assertIn("ABC", early[0].message)

    def test_table_only_abbreviation_is_reported(self):
        report = L.lint(with_results(run("GGT stayed flat.")))
        undefined = [f for f in findings(report, "abbrev.undefined") if "GGT" in f.message]
        self.assertEqual(len(undefined), 1)

    def test_abstract_abbreviations_when_profile_forbids_them(self):
        docx = manuscript(abstract=run("Insulin resistance by HOMA-IR fell after training.")).docx()
        joe = L.lint(docx, profile=PROFILES / "journal-of-endocrinology.profile.json")
        self.assertEqual(len(findings(joe, "abstract.abbreviation")), 1)
        house = L.lint(docx)
        self.assertEqual(findings(house, "abstract.abbreviation"), [])

    def test_gene_symbol_in_roman_is_a_hint(self):
        roman = L.lint(with_results(run("FOXO1 expression rose after training.")))
        found = findings(roman, "nomenclature.gene-italic")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "info")
        italic = L.lint(with_results(run("FOXO1", I) + run(" expression rose after training.")))
        self.assertEqual(findings(italic, "nomenclature.gene-italic"), [])


class FigureTest(unittest.TestCase):
    def test_callout_order(self):
        report = L.lint(manuscript(callouts=("2", "1A", "1B")).docx())
        self.assertEqual(len(findings(report, "figures.order")), 1)

    def test_uncited_figure_is_an_error(self):
        legends = [(1, "One.", run("Legend one.")), (2, "Two.", run("Legend two.")),
                   (3, "Three.", run("Legend three."))]
        report = L.lint(manuscript(legends=legends).docx())
        found = findings(report, "figures.uncited")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "error")
        self.assertIn("Figure 3", found[0].message)

    def test_cited_figure_without_legend(self):
        report = L.lint(with_results(run("A third result (Figures 2 and 3).")))
        self.assertIn("Figure 3", findings(report, "figures.missing-legend")[0].message)


class LimitsTest(unittest.TestCase):
    def write_profile(self, folder, research):
        path = Path(folder) / "tiny.profile.json"
        path.write_text(json.dumps({"schema": "journal-profile/1",
                                    "journal": {"id": "tiny", "name": "Tiny Journal", "sources": []},
                                    "article_types": {"research": research}}), encoding="utf-8")
        return path

    def test_profile_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = self.write_profile(folder, {
                "title": {"max_chars": 10}, "short_title": {"max_chars": 5}, "keywords": {"min": 6},
                "abstract": {"max_words": 5}, "word_limit": {"max": 10, "severity": "error"},
                "references": {"recommended_max": 1}, "figures": {"recommended_max": 1}})
            report = L.lint(clean_docx(), profile=profile)
        errors = rules_of(report, "error")
        for rule in ("limits.title", "limits.running-title", "limits.keywords", "limits.abstract-words",
                     "limits.word-count"):
            self.assertIn(rule, errors)
        self.assertIn("limits.references", rules_of(report, "warn"))
        self.assertIn("limits.figures", rules_of(report, "warn"))

    def test_word_limit_severity_comes_from_the_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = self.write_profile(folder, {"word_limit": {"max": 10, "severity": "warn"}})
            report = L.lint(clean_docx(), profile=profile)
        self.assertEqual(findings(report, "limits.word-count")[0].severity, "warn")

    def test_stated_word_count_mismatch(self):
        report = L.lint(manuscript(word_count="999").docx())
        found = findings(report, "limits.word-count-stated")
        self.assertEqual(len(found), 1)
        self.assertIn("999", found[0].message)


class DeclarationTest(unittest.TestCase):
    def test_missing_declaration(self):
        blocks = [("h", "Contributions"), ("label", "Given A. Surname:", " Writing."),
                  ("h", "Data availability"), ("text", "Available on request."),
                  ("h", "Acknowledgements"), ("text", "We thank the participants."),
                  ("label", "Declaration of interest:", " None."),
                  ("label", "Use of generative AI:", " None.")]
        report = L.lint(manuscript(declarations=blocks).docx())
        missing = findings(report, "declarations.missing")
        self.assertEqual(len(missing), 1)
        self.assertIn("funding", missing[0].message)

    def test_order_against_joe(self):
        report = L.lint(clean_docx(), profile=PROFILES / "journal-of-endocrinology.profile.json")
        found = findings(report, "declarations.order")
        self.assertEqual(len(found), 1)
        self.assertIn("declaration_of_interest, funding, author_contributions", found[0].message)


TITLE = "Serum marker L-38 and falls in older adults after balance training"


def title_page(title=TITLE, running="L-38 and falls in older adults",
               keywords=("marker", "older adults", "balance", "falls")):
    """A separate title-page file: no headings, title first, the house label lines."""
    d = Doc()
    d.p(run(title, '<w:b/><w:sz w:val="24"/>'))
    d.p(run("Given A. Surname") + run("1", SUP) + run(" and Other Surname") + run("1,2*", SUP))
    d.p(run("1", SUP) + run("Department of Example, Example University, City, Country"))
    d.t("")
    d.p(run("*Corresponding author:", '<w:i/><w:u w:val="single"/>'))
    d.t("E-mail address: name@example.org")
    d.p(run("Keywords:", "<w:b/>") + run(" " + ", ".join(keywords)))
    d.t("Word count: 3,210 words (excluding references and figure legends)")
    d.p(run(f"Running title: {running}", "<w:b/>"))
    return d.docx()


class TitlePageTest(unittest.TestCase):
    BODY_ONLY = ("declarations.", "figures.", "abbrev.", "limits.missing", "limits.word-count", "limits.references",
                 "abstract.")

    def test_separate_title_page_limits_are_checked(self):
        report = L.lint(title_page(title="A" * 130, running="R" * 50, keywords=("one", "two", "three")),
                        profile=JOE)
        self.assertEqual(report.scope, "title_page")
        self.assertEqual(rules_of(report, "error"), {"limits.title", "limits.running-title", "limits.keywords"})
        self.assertEqual(findings(report, "limits.title")[0].paragraph, 0)
        self.assertEqual(report.measures, {"title_chars": 130, "running_title_chars": 50, "keywords": 3,
                                           "stated_word_count": 3210})
        self.assertFalse([f.rule for f in report.findings if f.rule.startswith(self.BODY_ONLY)])

    def test_title_page_within_limits_is_clean(self):
        report = L.lint(title_page(), profile=JOE)
        self.assertEqual([f.to_dict() for f in report.findings], [])
        self.assertEqual(report.measures["title_chars"], len(TITLE))
        self.assertEqual(report.to_dict()["scope"], "title_page")

    def test_shipped_example_title_page(self):
        profile = load_profile(JOE)
        built = template.build_title_page(EXAMPLES / "title_page.json", word_count=1394, profile=profile)
        report = L.lint(built.blob, profile=JOE)
        self.assertEqual(report.scope, "title_page")
        self.assertEqual([f.to_dict() for f in report.findings], [])
        self.assertEqual(report.measures, {"title_chars": 74, "running_title_chars": 37, "keywords": 5,
                                           "stated_word_count": 1394})

    def test_document_without_headings_or_labels_is_still_a_manuscript(self):
        d = Doc().t("A letter to the editor without heading styles.").t("It names no title-page items.")
        report = L.lint(d.docx())
        self.assertEqual(report.scope, "manuscript")
        self.assertIn("declarations.missing", rules_of(report))

    def test_main_file_without_title_points_to_the_title_page_file(self):
        d = Doc().h1("Abstract").t("Serum marker L-38 was measured in older adults.").h1("1. Introduction")
        report = L.lint(d.docx(), profile=JOE)
        self.assertEqual(report.scope, "manuscript")
        missing = [f for f in findings(report, "limits.missing") if "title paragraph" in f.message]
        self.assertEqual(len(missing), 1)
        self.assertIn("separate title-page file", missing[0].suggestion)


class ExampleTest(unittest.TestCase):
    def test_shipped_example_meets_the_joe_profile_except_placeholders(self):
        profile = load_profile(JOE)
        built = template.build_manuscript(EXAMPLES / "manuscript.md", EXAMPLES / "title_page.json", profile=profile)
        report = L.lint(built.blob, profile=JOE)
        self.assertEqual({f.rule for f in report.errors}, {"citation.placeholder"})
        self.assertEqual(len(report.errors), 5)
        self.assertEqual([f.to_dict() for f in report.findings if f.severity == "warn"], [])
        found = report.measures["declarations"]           # key -> paragraph index, keys in profile order
        self.assertEqual(list(found), profile["declarations"]["order"])
        self.assertEqual(list(found.values()), sorted(found.values()), "declarations appear in the JOE order")


class TrackedChangesTest(unittest.TestCase):
    def test_accepted_view_is_linted(self):
        tracked = (run("Tests were ") + f'<w:del w:id="901" w:author="{AUTHOR_OTHER}" w:date="2026-01-01T00:00:00Z">'
                   "<w:r><w:delText>Mann-Whitney</w:delText></w:r></w:del>"
                   + f'<w:ins w:id="902" w:author="{AUTHOR_OTHER}" w:date="2026-01-01T00:00:00Z">'
                   + run("Kruskal-Wallis", rsid=False) + "</w:ins>" + run(" tests."))
        report = L.lint(with_results(tracked))
        found = findings(report, "typo.eponym-dash")
        self.assertEqual(len(found), 1)
        self.assertIn("Kruskal", found[0].snippet)


class ProfileAssetTest(unittest.TestCase):
    def test_joe_profile(self):
        profile = load_profile(PROFILES / "journal-of-endocrinology.profile.json")
        journal = profile["journal"]
        self.assertEqual(journal["id"], "joe")
        self.assertTrue(journal["reverify_before_submission"])
        self.assertEqual([s["url"] for s in journal["sources"]],
                         ["https://joe.bioscientifica.com/page/authors",
                          "https://journals.bioscientifica.com/joe/pages/author-guidelines"])
        for source in journal["sources"]:
            self.assertEqual((source["retrieved"], source["method"]), ("2026-08-23", "user_paste"))
        research = profile["article_types"]["research"]
        self.assertEqual(research["word_limit"]["max"], 5000)
        self.assertEqual(research["abstract"]["max_words"], 250)
        self.assertIn("abbreviations", research["abstract"]["forbid"])
        self.assertEqual(research["title"]["max_chars"], 120)
        self.assertEqual(research["short_title"]["max_chars"], 46)
        self.assertEqual(research["keywords"]["min"], 4)
        self.assertEqual(research["references"]["recommended_max"], 60)
        self.assertEqual(research["figures"]["recommended_max"], 10)
        self.assertEqual(profile["declarations"]["order"][0], "declaration_of_interest")

    def test_template_profile(self):
        path = PROFILES / "template.profile.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        blocks = [key for key in raw if key not in ("schema", "notes")]
        for block in blocks:
            self.assertIn(block, raw["notes"], f"notes lack an explanation of {block}")
        for key in ("journal", "article_types", "title_page", "declarations", "formatting", "references",
                    "statistics", "figures", "tables", "supplementary", "revision", "policies"):
            self.assertIn(key, raw)
        report = L.lint(clean_docx(), profile=path)
        self.assertEqual([f.to_dict() for f in report.errors], [])

    def test_template_notes_match_the_merge_rules(self):
        path = PROFILES / "template.profile.json"
        notes = json.loads(path.read_text(encoding="utf-8"))["notes"]
        self.assertNotIn("switches a house default off", notes["how_to_use"])
        self.assertIn("null or left out keeps the house default", notes["how_to_use"])
        self.assertIn("empty list or empty string, unlike null", notes["how_to_use"])
        for value in ("continuous", "per_page", "per_section", "none"):
            self.assertIn(value, notes["formatting"])
        # what the notes say is what load_profile does with the template's values
        profile = load_profile(path)
        self.assertEqual(profile["statistics"]["p_style"], HOUSE_DEFAULTS["statistics"]["p_style"])
        self.assertEqual(profile["formatting"]["line_numbers"], "continuous")
        self.assertEqual(profile["article_types"]["research"]["abstract"]["forbid"], [])
        self.assertEqual(profile["declarations"]["order"], [])

    def test_profiles_are_utf8_lf_without_bom(self):
        for path in PROFILES.glob("*.profile.json"):
            data = path.read_bytes()
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"), path.name)
            self.assertNotIn(b"\r\n", data, path.name)


class CommandLineTest(unittest.TestCase):
    def run_cli(self, argv):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        L.register(sub)
        args = parser.parse_args(["lint", *argv])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
            code = args.func(args)
        return code, buffer.getvalue()

    def test_exit_codes_and_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            clean = folder / "clean.docx"
            clean.write_bytes(clean_docx())
            broken = folder / "broken.docx"
            broken.write_bytes(with_results(run("Earlier work [PMID: 12345678] agrees.")))
            code, output = self.run_cli([str(clean)])
            self.assertEqual(code, 0, output)
            self.assertIn("SUMMARY: 0 error(s)", output)
            json_out, md_out = folder / "lint.json", folder / "lint.md"
            code, output = self.run_cli([str(broken), "--json", str(json_out), "--markdown", str(md_out)])
            self.assertEqual(code, 1)
            output.encode("ascii")
            data = json_out.read_bytes()
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r\n", data)
            report = json.loads(data.decode("utf-8"))
            self.assertEqual(report["schema"], "msw-lint/1")
            self.assertEqual(report["view"], "accepted")
            self.assertIn("citation.placeholder", {f["rule"] for f in report["findings"]})
            self.assertIn("citation.placeholder", md_out.read_text(encoding="utf-8"))

    def test_existing_outputs_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / "m.docx"
            source.write_bytes(clean_docx())
            existing = folder / "lint.json"
            existing.write_text("keep", encoding="utf-8")
            code, _ = self.run_cli([str(source), "--json", str(existing)])
            self.assertEqual(code, 2)
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")

    def test_title_page_scope_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / "title page.docx"
            source.write_bytes(title_page(running="R" * 50))
            json_out, md_out = folder / "lint.json", folder / "lint.md"
            code, output = self.run_cli([str(source), "--profile", str(JOE), "--json", str(json_out),
                                         "--markdown", str(md_out)])
            self.assertEqual(code, 1)
            self.assertIn("scope: separate title page", output)
            self.assertIn("limits.running-title", output)
            self.assertNotIn("declarations.missing", output)
            self.assertEqual(json.loads(json_out.read_text(encoding="utf-8"))["scope"], "title_page")
            self.assertIn("- Scope: separate title page", md_out.read_text(encoding="utf-8"))

    def test_spelling_option(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "m.docx"
            source.write_bytes(with_results(run("The colour changed.")))
            code, output = self.run_cli([str(source), "--spelling", "en-GB"])
        self.assertEqual(code, 0)
        self.assertIn("spelling.mixed", output)
        self.assertIn("en-GB (--spelling)", output)


if __name__ == "__main__":
    unittest.main()

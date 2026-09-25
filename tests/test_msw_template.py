"""new-manuscript, title-page and wordcount (scientific-manuscript skill)."""

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "scientific-manuscript"
sys.path.insert(0, str(SKILL / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import msw_fixtures as fx  # noqa: E402
from mswlib import template, wordcount  # noqa: E402
from mswlib.docx import Package  # noqa: E402
from mswlib.edit import build  # noqa: E402
from mswlib.endnote import census  # noqa: E402
from mswlib.gate import verify  # noqa: E402
from mswlib.views import view_doc, view_texts  # noqa: E402
from mswlib.wordml import formatted_segments, iter_paragraphs, paragraph_style, paragraph_text  # noqa: E402

EXAMPLES = SKILL / "assets" / "examples"
SOURCE = EXAMPLES / "manuscript.md"
TITLE_PAGE = EXAMPLES / "title_page.json"
FIGURE = EXAMPLES / "figures" / "figure1.png"
AUTHOR = "Test Author"
DATE = "2026-01-02T03:04:05Z"


def cli(*argv):
    """Run template/wordcount commands through their own registration (independent of other plug-ins)."""
    parser = argparse.ArgumentParser(prog="msw")
    sub = parser.add_subparsers(dest="command", required=True)
    template.register(sub)
    wordcount.register(sub)
    args = parser.parse_args([str(a) for a in argv])
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = args.func(args)
    return code, out.getvalue(), err.getvalue()


def paragraphs(package):
    doc = package.document
    return [(p, paragraph_text(doc, p)) for p in iter_paragraphs(doc)]


def body_children(package):
    doc = package.document
    body = doc.root.find("w:body")
    return doc, [c for c in body.elements()]


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write_json(self, name, data):
        path = self.dir / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def title_page(self, **changes):
        data = json.loads(TITLE_PAGE.read_text(encoding="utf-8"))
        data.update(changes)
        return data


class ExampleBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.built = template.build_manuscript(SOURCE, TITLE_PAGE, date=DATE)
        cls.path = template.write_exclusive(cls.dir / "example.docx", cls.built.blob)
        cls.package = Package(cls.path)
        cls.paras = paragraphs(cls.package)
        cls.texts = [t for _, t in cls.paras]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def para(self, text):
        hits = [p for p, t in self.paras if t == text]
        self.assertEqual(len(hits), 1, text)
        return hits[0]

    def test_package_parts_and_settings(self):
        names = set(self.package.names())
        for name in ("[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/_rels/document.xml.rels",
                     "word/styles.xml", "word/settings.xml", "word/footer1.xml", "word/fontTable.xml",
                     "docProps/core.xml", "docProps/app.xml", "word/media/image1.png"):
            self.assertIn(name, names)
        for name in names - {"[Content_Types].xml"}:
            self.assertIsNotNone(self.package.content_type(name), name)
        template.validate_package(self.built.blob)
        settings = self.package.parts["word/settings.xml"]
        self.assertNotIn(b"trackRevisions", settings)
        self.assertNotIn(b"evenAndOddHeaders", settings)
        self.assertIn(b'<w:defaultTabStop w:val="720"/>', settings)
        self.assertIn(b"compatibilityMode", settings)
        root = self.package.document.root
        for prefix in ("w", "r", "wp", "a", "pic", "w14", "wp14", "mc"):
            self.assertIn(f"xmlns:{prefix}", root.attrs)
        self.assertEqual(root.get("mc:Ignorable"), "w14 wp14")
        self.assertIn(DATE.encode(), self.package.parts["docProps/core.xml"])

    def test_every_paragraph_has_a_unique_para_id(self):
        ids = []
        for part in self.package.story_parts():
            for paragraph in iter_paragraphs(self.package.xml(part)):
                pid = paragraph.get("w14:paraId")
                self.assertRegex(pid or "", r"^[0-9A-F]{8}$")
                self.assertLess(int(pid, 16), 0x80000000)
                ids.append(pid)
        self.assertEqual(len(ids), len(set(ids)))

    def test_styles(self):
        styles = self.package.parts["word/styles.xml"].decode("utf-8")
        for style_id in ("Normal", "BodyText", "Heading1", "Heading2", "Heading3", "FigureLegend",
                         "EndNoteBibliography", "Footer", "ManuscriptTable"):
            self.assertIn(f'w:styleId="{style_id}"', styles)
        body = re.search(r'w:styleId="BodyText">.*?</w:style>', styles).group(0)
        self.assertIn('w:line="480"', body)
        self.assertIn('w:firstLine="720"', body)
        self.assertIn('w:after="160"', body)
        no_indent = re.search(r'w:styleId="BodyTextNoIndent">.*?</w:style>', styles).group(0)
        self.assertIn('<w:name w:val="Body Text No Indent"/>', no_indent)
        self.assertIn('<w:basedOn w:val="BodyText"/>', no_indent)
        self.assertIn('w:firstLine="0"', no_indent)
        legend = re.search(r'w:styleId="FigureLegend">.*?</w:style>', styles).group(0)
        self.assertIn('<w:basedOn w:val="BodyTextNoIndent"/>', legend)
        normal = re.search(r'w:styleId="Normal">.*?</w:style>', styles).group(0)
        self.assertIn('w:ascii="Times New Roman"', normal)
        self.assertIn('<w:sz w:val="22"/>', normal)
        self.assertIn('<w:jc w:val="both"/>', normal)
        heading = re.search(r'w:styleId="Heading1">.*?</w:style>', styles).group(0)
        self.assertIn("<w:b/>", heading)
        self.assertIn('<w:sz w:val="32"/>', heading)
        self.assertIn('w:after="0"', heading)
        self.assertIn('<w:jc w:val="left"/>', heading)
        self.assertIn("<w:i/>", re.search(r'w:styleId="Heading3">.*?</w:style>', styles).group(0))
        references = re.search(r'w:styleId="EndNoteBibliography">.*?</w:style>', styles).group(0)
        self.assertIn('w:line="240"', references)   # house default; journal profiles may ask for double
        self.assertIn("<w:tblBorders>", re.search(r'w:styleId="ManuscriptTable">.*?</w:style>', styles).group(0))

    def test_headings_and_body_use_styles(self):
        self.assertEqual(paragraph_style(self.para("Abstract")), "Heading1")
        self.assertEqual(paragraph_style(self.para("1. Introduction")), "Heading1")
        self.assertEqual(paragraph_style(self.para("2.1 Participants")), "Heading2")
        self.assertEqual(paragraph_style(self.para("Figure Legends")), "Heading1")
        legend_title = self.para("Figure 1: Serum L-38, falls and gait speed by randomized arm and fall history.")
        self.assertEqual(paragraph_style(legend_title), "Heading2")
        legend = [p for p, t in self.paras if t.startswith("(A) Change in serum L-38")]
        self.assertEqual(paragraph_style(legend[0]), "FigureLegend")

    def test_abstract_and_back_matter_are_not_indented(self):
        def style_of(start):
            hits = {paragraph_style(p) for p, t in self.paras if t.startswith(start)}
            self.assertEqual(len(hits), 1, start)
            return hits.pop()
        for start in ("Roughly a third", "This analysis aimed", "We first estimated", "In this randomized trial, 16",
                      "In this randomized trial, balance", "Participants were volunteers"):
            self.assertEqual(style_of(start), "BodyText", start)          # Introduction to Limitations
        for start in ("Falls are frequent in later life", "[The journal's standard sentence",
                      "The Example Research Fund", "Avery A. Author:", "Deidentified data",
                      "We thank the participants", "Use of generative AI:"):
            self.assertEqual(style_of(start), "BodyTextNoIndent", start)  # abstract and back matter
        references = self.texts.index("References")
        self.assertEqual(self.texts[references + 1], "")
        self.assertEqual(paragraph_style(self.paras[references + 1][0]), "EndNoteBibliography")

    def test_title_page_order(self):
        count = wordcount.analyse(self.path)["house"]["total"]
        expected = [
            "Serum marker L-38 and falls in older adults randomized to balance training",
            "Avery A. Author1,3, Blake B. Writer1,2* and Casey C. Scholar2",
            "Department of Example Geriatrics1 and Example Falls Clinic3, Example University Hospital, Example City, "
            "Example Country",
            "Institute of Example Exercise Science2, Example University, Example City, Example Country",
            "",
            "*Corresponding author:",
            "- Blake B. Writer. Institute of Example Exercise Science, Example University, 1 Example Street, "
            "Example City, Example Country",
            "E-mail address: writer@example.org",
            "ORCID number: 0000-0000-0000-0000",
            "",
            "Keywords: marker L-38, balance training, falls, older adults, gait speed",
            f"Word count: {count:,} words (excluding references and figure legends)",
            "",
            "Running title: L-38 and falls after balance training",
            "Abstract",
        ]
        self.assertEqual(self.texts[:len(expected)], expected)
        abstract = self.para("Abstract")
        self.assertNotIn("pageBreakBefore", {e.tag.split(":")[-1] for e in abstract.iter()})

    def test_title_page_formatting(self):
        doc = self.package.document
        title = self.paras[0][0]
        self.assertEqual([f for _, f in formatted_segments(doc, title)], [frozenset({"bold"})])
        self.assertIn(b'<w:sz w:val="24"/>', doc.raw(title))
        authors = formatted_segments(doc, self.paras[1][0])
        self.assertEqual([t for t, f in authors if "superscript" in f], ["1,3", "1,2*", "2"])
        self.assertEqual([t for t, f in authors if not f], ["Avery A. Author", ", Blake B. Writer",
                                                           " and Casey C. Scholar"])
        affiliation = formatted_segments(doc, self.paras[2][0])     # inline markers after each unit
        sup = frozenset({"superscript"})
        self.assertEqual(affiliation[:4], [("Department of Example Geriatrics", frozenset()), ("1", sup),
                                           (" and Example Falls Clinic", frozenset()), ("3", sup)])
        self.assertEqual(formatted_segments(doc, self.paras[3][0])[1], ("2", sup))
        label = formatted_segments(doc, self.paras[5][0])
        self.assertEqual(label, [("*Corresponding author:", frozenset({"italic", "underline"}))])
        keywords = formatted_segments(doc, self.paras[10][0])
        self.assertEqual(keywords[0], ("Keywords:", frozenset({"bold"})))
        self.assertEqual(keywords[1][1], frozenset())
        self.assertFalse(keywords[1][0].endswith("."))
        self.assertEqual([f for _, f in formatted_segments(doc, self.paras[11][0])], [frozenset()])
        running = formatted_segments(doc, self.paras[13][0])
        self.assertEqual(running, [("Running title: L-38 and falls after balance training", frozenset({"bold"}))])
        for paragraph, _ in self.paras[:14]:
            self.assertIn(b'w:line="480"', doc.raw(paragraph))

    def test_sections_line_numbers_and_footer(self):
        doc, children = body_children(self.package)
        sects = list(doc.iter("w:sectPr"))
        self.assertEqual(len(sects), 2)
        for sect in sects:
            numbers = sect.find("w:lnNumType")
            self.assertEqual((numbers.get("w:countBy"), numbers.get("w:restart")), ("1", "continuous"))
            size = sect.find("w:pgSz")
            self.assertEqual((size.get("w:w"), size.get("w:h")), ("12240", "15840"))
            margins = sect.find("w:pgMar")
            self.assertEqual([margins.get(f"w:{k}") for k in ("top", "bottom", "left", "right")],
                             ["1440", "1440", "1800", "1800"])
            footer = sect.find("w:footerReference")
            rels = {r["Id"]: r["Target"] for r in self.package.relationships("word/document.xml")}
            self.assertEqual(rels[footer.get("r:id")], "footer1.xml")
        self.assertIs(children[-1], sects[1])
        first_section_end = sects[0].parent.parent
        self.assertEqual(first_section_end.tag, "w:p")
        index = [p for p, _ in self.paras].index(first_section_end)
        self.assertEqual(self.texts[index + 1], "Figure Legends")
        self.assertEqual(self.texts[index - 2:index], ["References", ""])
        footer = self.package.parts["word/footer1.xml"]
        self.assertIn(b" PAGE ", footer)
        self.assertIn(b'<w:jc w:val="center"/>', footer)

    def test_picture_embedded_with_correct_aspect(self):
        doc = self.package.document
        drawings = list(doc.iter("w:drawing"))
        self.assertEqual(len(drawings), 1)
        extent = next(drawings[0].iter("wp:extent"))
        cx, cy = int(extent.get("cx")), int(extent.get("cy"))
        self.assertEqual(cx, 150 * 36000)
        png = FIGURE.read_bytes()
        width, height = struct.unpack(">II", png[16:24])
        self.assertEqual(cy, round(cx * height / width))
        self.assertEqual(next(drawings[0].iter("wp:docPr")).get("name"), "Figure 1")
        blip = next(drawings[0].iter("a:blip"))
        rels = {r["Id"]: r["Target"] for r in self.package.relationships("word/document.xml")}
        self.assertEqual(self.package.parts["word/" + rels[blip.get("r:embed")]], png)
        owner = next(a for a in drawings[0].ancestors() if a.tag == "w:p")
        self.assertIn(b'<w:jc w:val="center"/>', doc.raw(owner))
        self.assertIn(b'w:line="240"', doc.raw(owner))

    def test_placeholders_and_table(self):
        data = census(self.package.document)
        texts = [item["text"] for item in data["placeholders"]]
        self.assertIn("[PMID: 12345678]", texts)
        self.assertIn("[PMIDs: 23456789, 34567890]", texts)
        self.assertEqual(data["citation_fields_live"], 0)
        self.assertTrue(any("[REF: guideline-2022]" in t for t in self.texts))
        doc = self.package.document
        tables = list(doc.iter("w:tbl"))
        self.assertEqual(len(tables), 1)
        self.assertEqual(next(tables[0].iter("w:tblStyle")).get("w:val"), "ManuscriptTable")
        rows = tables[0].findall("w:tr")
        self.assertEqual(len(rows), 4)
        header = [formatted_segments(doc, p) for p in rows[0].iter("w:p")]
        self.assertEqual(header, [[("Abbreviation", frozenset({"bold"}))], [("Definition", frozenset({"bold"}))]])
        label = self.para("Abbreviations")
        self.assertNotIn(b"firstLine", doc.raw(label))
        self.assertIn(b"<w:keepNext/>", doc.raw(label))
        introduction = self.para("1. Introduction")
        self.assertIn(b"<w:pageBreakBefore/>", doc.raw(introduction))

    def test_title_page_word_count_equals_house_count(self):
        result = wordcount.analyse(self.path)
        self.assertEqual(result["stated"], result["house"]["total"])
        self.assertTrue(result["matches"])
        self.assertEqual(self.built.word_count, result["house"]["total"])
        self.assertEqual(result["abstract"]["words"], self.built.counts["abstract"])
        self.assertGreater(result["house"]["total"], result["strict"]["total"])
        accepted = view_texts(self.package.document, "accept")
        self.assertEqual(accepted, self.texts)


class CommandTests(Workspace):
    def test_new_manuscript_and_separate_title_page(self):
        out, title = self.dir / "draft.docx", self.dir / "title page.docx"
        code, stdout, stderr = cli("new-manuscript", "--source", SOURCE, "--title-page", TITLE_PAGE, "--out", out,
                                   "--separate-title-page", title)
        self.assertEqual(code, 0, stderr)
        self.assertIn("2 section(s)", stdout)
        stated = wordcount.analyse(out)["stated"]
        texts = [t for _, t in paragraphs(Package(title))]
        self.assertIn(f"Word count: {stated:,} words (excluding references and figure legends)", texts)
        self.assertNotIn("Abstract", texts)
        self.assertEqual(texts[0], "Serum marker L-38 and falls in older adults randomized to balance training")
        code, stdout, stderr = cli("title-page", "--title-page", TITLE_PAGE, "--out", self.dir / "tp2.docx",
                                   "--manuscript", out)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(Package(self.dir / "tp2.docx").parts["word/document.xml"],
                         Package(title).parts["word/document.xml"])

    def test_refuses_existing_destination(self):
        out = self.dir / "exists.docx"
        out.write_bytes(b"keep me")
        code, _, stderr = cli("new-manuscript", "--source", SOURCE, "--title-page", TITLE_PAGE, "--out", out)
        self.assertEqual(code, 2)
        self.assertIn("already exists", stderr)
        self.assertEqual(out.read_bytes(), b"keep me")
        with self.assertRaises(FileExistsError):
            template.write_exclusive(out, b"new")
        code, _, stderr = cli("title-page", "--title-page", TITLE_PAGE, "--out", out, "--word-count", "10")
        self.assertEqual(code, 2)
        self.assertEqual(out.read_bytes(), b"keep me")

    def test_title_page_needs_a_count(self):
        code, _, stderr = cli("title-page", "--title-page", TITLE_PAGE, "--out", self.dir / "tp.docx")
        self.assertEqual(code, 2)
        self.assertIn("--word-count", stderr)
        self.assertFalse((self.dir / "tp.docx").exists())

    def test_journal_profile_changes_layout_and_limits(self):
        profile = self.write_json("journal.profile.json", {
            "schema": "journal-profile/1",
            "journal": {"id": "EXJ", "name": "Example Journal"},
            "title_page": {"separate_file": True},
            "formatting": {"references_line_spacing": "double", "page": "a4"},
            "article_types": {"research": {"word_limit": {"max": 500, "severity": "warn"},
                                           "short_title": {"max_chars": 30}}},
        })
        out = self.dir / "journal.docx"
        code, stdout, stderr = cli("new-manuscript", "--source", SOURCE, "--title-page", TITLE_PAGE, "--out", out,
                                   "--profile", profile)
        self.assertEqual(code, 0, stderr)
        self.assertIn("separate title-page file", stderr)
        self.assertIn("running title has 37 characters (journal maximum 30)", stderr)
        self.assertIn("exceeds the journal limit 500", stderr)
        package = Package(out)
        styles = package.parts["word/styles.xml"].decode("utf-8")
        references = re.search(r'w:styleId="EndNoteBibliography">.*?</w:style>', styles).group(0)
        self.assertIn('w:line="480"', references)
        abstract = next(p for p, t in paragraphs(package) if t == "Abstract")
        self.assertIn(b"<w:pageBreakBefore/>", package.document.raw(abstract))
        self.assertIn(b'w:w="11906"', package.parts["word/document.xml"])
        code, stdout, stderr = cli("wordcount", out, "--profile", profile, "--json")
        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["limit"]["status"], "over")
        self.assertEqual(result["limit"]["over_by"], result["house"]["total"] - 500)
        strict = self.write_json("strict.profile.json", {
            "schema": "journal-profile/1", "article_types": {"research": {"word_limit": {"max": 500,
                                                                                         "severity": "error"}}}})
        code, stdout, _ = cli("wordcount", out, "--profile", strict)
        self.assertEqual(code, 1)
        self.assertIn("OVER by", stdout)


class MarkdownAndTitlePageTests(Workspace):
    def build(self, markdown, title_page=None, **options):
        source = self.dir / "m.md"
        source.write_text(markdown, encoding="utf-8")
        return template.build_manuscript(source, title_page or self.title_page(), **options)

    def test_parse_errors_name_the_line(self):
        cases = [("# Abstract\n\nSome <i>open text.\n", "m.md:3: unclosed <i>"),
                 ("# Abstract\n\n| a | b |\n| c | d |\n", "m.md:3: a table needs a header row"),
                 ("#### Deep\n", "m.md:1: only #, ## and ### headings"),
                 ("# Abstract\n\nText </b> here.\n", "m.md:3: closing </b>")]
        for markdown, message in cases:
            with self.subTest(message=message), self.assertRaises(template.TemplateError) as caught:
                self.build(markdown)
            self.assertIn(message, str(caught.exception))

    def test_missing_headings_block_auto_word_count(self):
        with self.assertRaises(template.TemplateError) as caught:
            self.build("# Abstract\n\nOne two three.\n\n# References\n")
        self.assertIn("Introduction", str(caught.exception))
        built = self.build("# Abstract\n\nOne two three.\n\n# References\n", self.title_page(word_count=1200))
        self.assertEqual(built.word_count, 1200)
        self.assertTrue(any("word count not checked" in w for w in built.warnings))
        texts = [t for _, t in paragraphs(Package(built.blob))]
        self.assertIn("Word count: 1,200 words (excluding references and figure legends)", texts)

    def test_dialect(self):
        markdown = ("<!-- a note\n  on two lines -->\n# Abstract\n\nAbstract text\ncontinues here.\n\n"
                    "# 1. Introduction\n\nThe <i>GENE1</i> level was 31 kg/m<sup>2</sup> and CO<sub>2</sub> "
                    "was <b><u>high</u></b>; *P < 0.05 and a_b stay literal [PMID: 1234567].\n\n\\pagebreak\n\n"
                    "## 1.1 Next\n\nText.\n\n# References\n\nManual reference one.\n\n# Figure Legends\n\n"
                    "## Figure 1: One.\n\n![first](fig.png)\n\nLegend one.\n\n## Figure 2: Two.\n\n"
                    "![second](fig.png){width=2in}\n\nLegend two.\n")
        (self.dir / "fig.png").write_bytes(fx.png(40, 20))
        package = Package(self.build(markdown).blob)
        doc = package.document
        paras = paragraphs(package)
        texts = [t for _, t in paras]
        self.assertIn("Abstract text continues here.", texts)
        self.assertNotIn("a note", " ".join(texts))
        body = next(p for p, t in paras if t.startswith("The GENE1"))
        segments = formatted_segments(doc, body)
        self.assertIn(("GENE1", frozenset({"italic"})), segments)
        self.assertIn(("2", frozenset({"superscript"})), segments)
        self.assertIn(("2", frozenset({"subscript"})), segments)
        self.assertIn(("high", frozenset({"bold", "underline"})), segments)
        self.assertIn("*P < 0.05 and a_b stay literal [PMID: 1234567].", texts[texts.index(paragraph_text(doc, body))])
        heading = next(p for p, t in paras if t == "1.1 Next")
        self.assertIn(b"<w:pageBreakBefore/>", doc.raw(heading))
        manual = next(p for p, t in paras if t == "Manual reference one.")
        self.assertEqual(paragraph_style(manual), "EndNoteBibliography")
        self.assertEqual(texts[texts.index("References") + 1], "Manual reference one.")
        second = next(p for p, t in paras if t == "Figure 2: Two.")
        first = next(p for p, t in paras if t == "Figure 1: One.")
        self.assertIn(b"<w:pageBreakBefore/>", doc.raw(second))
        self.assertNotIn(b"<w:pageBreakBefore/>", doc.raw(first))
        extents = [(int(e.get("cx")), int(e.get("cy"))) for e in doc.iter("wp:extent")]
        self.assertEqual(extents, [(5486400, 2743200), (1828800, 914400)])
        self.assertEqual([e.get("name") for e in doc.iter("wp:docPr")], ["Figure 1", "Figure 2"])
        self.assertEqual(sum(1 for n in package.names() if n.startswith("word/media/")), 1)

    def test_run_in_declaration_lines_are_not_indented(self):
        markdown = ("# Abstract\n\nAbstract text.\n\n# 1. Introduction\n\nIntro text.\n\n# 5. Conclusions\n\n"
                    "Conclusion text.\n\n# Limitations\n\nLimitation text.\n\n<b>Funding:</b> Example grant.\n\n"
                    "<b>Not a label</b> in body text.\n\n# Ethics approval\n\nApproved.\n\n# References\n")
        package = Package(self.build(markdown).blob)
        styles = {t: paragraph_style(p) for p, t in paragraphs(package)}
        self.assertEqual(styles["Abstract text."], "BodyTextNoIndent")
        self.assertEqual(styles["Intro text."], "BodyText")
        self.assertEqual(styles["Conclusion text."], "BodyText")
        self.assertEqual(styles["Limitation text."], "BodyText")
        self.assertEqual(styles["Funding: Example grant."], "BodyTextNoIndent")
        self.assertEqual(styles["Not a label in body text."], "BodyText")
        self.assertEqual(styles["Approved."], "BodyTextNoIndent")

    def test_title_page_validation(self):
        bad_name = self.title_page()
        bad_name["corresponding"][0]["name"] = "B. Writer"
        with self.assertRaises(template.TemplateError) as caught:
            template.check_title_page(bad_name)
        self.assertIn("must use exactly the name", str(caught.exception))
        bad_affiliation = self.title_page()
        bad_affiliation["authors"][0]["affiliations"] = [1, 4]
        with self.assertRaises(template.TemplateError) as caught:
            template.check_title_page(bad_affiliation)
        self.assertIn("affiliation 4 is not in 'affiliations'", str(caught.exception))
        self.assertIn("affiliation(s) 3 are not used by any author", str(caught.exception))
        with self.assertRaises(template.TemplateError):
            template.check_title_page(self.title_page(word_count="many"))

    def test_affiliation_forms_and_refusals(self):
        authors = [{"name": "A. One", "affiliations": [1, 6]}, {"name": "B. Two", "affiliations": [2]}]
        sup = frozenset({"superscript"})

        def lines(affiliations):
            tp = self.title_page(authors=authors, corresponding=[], affiliations=affiliations)
            package = Package(template.build_title_page(tp, word_count=10).blob)
            doc = package.document
            return [formatted_segments(doc, p) for p, _ in paragraphs(package)[2:4]]

        # list form, lines given out of order: sorted by their first number; markers stay inline
        inline = lines(["Institute B<sup>2</sup>, City, Country",
                        "Department A<sup>1</sup> and Clinic C<sup>6</sup>, Hospital, City, Country"])
        self.assertEqual(inline[0][:4], [("Department A", frozenset()), ("1", sup), (" and Clinic C", frozenset()),
                                         ("6", sup)])
        self.assertEqual(inline[1][:2], [("Institute B", frozenset()), ("2", sup)])
        # object form with inline markers: the key must be one of them; same output
        self.assertEqual(lines({"2": "Institute B<sup>2</sup>, City, Country",
                                "1": "Department A<sup>1</sup> and Clinic C<sup>6</sup>, Hospital, City, Country"}),
                         inline)
        # plain object form: the key becomes a leading superscript
        plain = lines({"1": "Department A, City", "2": "Institute B, City", "6": "Clinic C, City"})
        self.assertEqual(plain[0], [("1", sup), ("Department A, City", frozenset())])
        refusals = [
            (["Department A, City", "Institute B<sup>2</sup>"], "affiliation line 1 has no <sup>n</sup> marker"),
            ({"1": "   ", "2": "Institute B"}, "affiliation 1 must be non-empty text"),
            ({"1": "Department A<sup>6</sup>", "2": "Institute B"}, "do not include its key 1"),
            (["Dept A<sup>1</sup>", "Dept B<sup>1</sup>", "Inst<sup>2</sup>", "Clinic<sup>6</sup>"],
             "affiliation 1 is defined more than once"),
            ("Department A", "'affiliations' must be an object"),
        ]
        for affiliations, message in refusals:
            with self.subTest(message=message), self.assertRaises(template.TemplateError) as caught:
                template.check_title_page(self.title_page(authors=authors, corresponding=[],
                                                          affiliations=affiliations))
            self.assertIn(message, str(caught.exception))

    def test_word_count_scope_follows_method_and_profile(self):
        default = "excluding references and figure legends"
        strict = ("abstract and main text to the end of Conclusions; excluding headings, Limitations, back matter, "
                  "references and figure legends")
        self.assertEqual(wordcount.scope_text("house"), (default, []))
        self.assertEqual(wordcount.scope_text("strict"), (strict, []))
        rules = {"word_limit": {"excludes": ["references", "figure_legends", "tables", "abstract"]}}
        text, warnings = wordcount.scope_text("house", rules)
        self.assertEqual(text, "excluding references, figure legends and tables")
        self.assertEqual(len(warnings), 1)
        self.assertIn("excludes abstract, but the house count includes it", warnings[0])
        text, warnings = wordcount.scope_text("house", {"word_limit": {"excludes": ["references"]}})
        self.assertEqual(text, default)
        self.assertIn("leaves out figure legends, which the journal's word limit does not exclude", warnings[0])
        # the built title page states the chosen method's scope, and warns about the mismatch
        built = self.build(SOURCE.read_text(encoding="utf-8").replace("figures/figure1.png", str(FIGURE)),
                           method="strict")
        texts = [t for _, t in paragraphs(Package(built.blob))]
        self.assertIn(f"Word count: {built.word_count:,} words ({strict})", texts)
        profile = template.load_profile()
        profile["article_types"]["research"]["word_limit"]["excludes"] = ["references", "figure_legends", "abstract"]
        built = self.build(SOURCE.read_text(encoding="utf-8").replace("figures/figure1.png", str(FIGURE)),
                           profile=profile)
        self.assertTrue(any("excludes abstract, but the house count includes it" in w for w in built.warnings))
        texts = [t for _, t in paragraphs(Package(built.blob))]
        self.assertIn(f"Word count: {built.word_count:,} words ({default})", texts)

    def test_image_sizes(self):
        self.assertEqual(template.image_info(fx.png(30, 10)), ("png", 30, 10))
        jpeg = (b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
                + b"\xff\xc0" + struct.pack(">HBHH", 17, 8, 480, 640) + b"\x03" + b"\x00" * 9 + b"\xff\xd9")
        self.assertEqual(template.image_info(jpeg), ("jpeg", 640, 480))
        with self.assertRaises(template.TemplateError):
            template.image_info(b"II*\x00not an image")


def fixture_with_sections(stated=99):
    extra = "".join([
        fx.para(fx.run("1. Introduction"), "20000001", "Heading1"),
        fx.para(fx.run("Alpha beta gamma."), "20000002"),
        fx.para(fx.run("5. Conclusions"), "20000003", "Heading1"),
        fx.para(fx.run("Delta epsilon."), "20000004"),
        fx.para(fx.run("Acknowledgements"), "20000005", "Heading1"),
        fx.para(fx.run("Thanks to all."), "20000006"),
    ])
    docx = fx.make_docx(extra_paragraphs=extra)
    line = fx.para(fx.run(f"Word count: {stated} words (excluding references and figure legends)"), "20000000")
    return fx.replace_document(docx, lambda data: data.replace(b"</w:p>", b"</w:p>" + line.encode(), 1))


class WordCountFixtureTests(Workspace):
    def test_counts_on_the_accepted_view_of_a_tracked_document(self):
        result = wordcount.analyse(Package(fixture_with_sections()))
        # Abstract body: 15 + 12 + 8 (co-author insertion kept, deletion dropped) + 10 (merged mark)
        # + 6 + 0 (picture) + 12 (legend) = 63. House body: headings and all paragraphs up to References.
        self.assertEqual(result["abstract"]["words"], 63)
        self.assertEqual(result["house"]["total"], 63 + 13)
        self.assertEqual(result["strict"]["total"], 63 + 5)
        self.assertEqual(result["stated"], 99)
        self.assertFalse(result["matches"])
        # title (10) + word-count line (9) + Abstract (1) + abstract body + sections + References (1) + 3 x 9
        self.assertEqual(result["document"]["total"], 10 + 9 + 1 + 63 + 13 + 1 + 27)
        self.assertIn("no 'Abbreviations' or 'Keywords' paragraph", result["house"]["scope"])
        self.assertIn("5. Conclusions", result["strict"]["scope"])

    def test_tokens(self):
        self.assertEqual(wordcount.words("P=0.012 and P = 0.012"), 5)
        self.assertEqual(wordcount.words("Follow\u2011up\tvisit\nnext\fpage"), 4)
        self.assertEqual(wordcount.words("\ufffc (12)"), 1)

    def test_missing_boundaries_are_errors(self):
        with self.assertRaises(wordcount.WordCountError) as caught:
            wordcount.analyse(Package(fx.make_docx()))
        self.assertIn("Introduction", str(caught.exception))
        self.assertIn("'Abstract'", str(caught.exception))
        path = self.dir / "plain.docx"
        path.write_bytes(fx.make_docx())
        code, _, stderr = cli("wordcount", path)
        self.assertEqual(code, 2)
        self.assertIn("1. Introduction", stderr)

    def test_stamp_spec_builds_and_passes_the_gate(self):
        path = self.dir / "fixture.docx"
        path.write_bytes(fixture_with_sections())
        code, stdout, _ = cli("wordcount", path, "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout)["house"]["total"], 76)
        code, stdout, _ = cli("wordcount", path, "--method", "strict")
        self.assertEqual(code, 1)
        for line in ("house:  76 words", "strict: 68 words", "whole document:", "title page states 99: DOES NOT "
                     "MATCH the strict count (68)", "limit: none in the profile"):
            self.assertIn(line, stdout)
        spec_path = self.dir / "edits.json"
        code, _, stderr = cli("wordcount", path, "--stamp-spec", spec_path)
        self.assertEqual(code, 0, stderr)
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        self.assertEqual(len(spec["edits"]), 1)
        edit = spec["edits"][0]
        self.assertEqual((edit["op"], edit["para"], edit["old"], edit["new"]), ("replace", "20000000", "99", "76"))
        code, _, stderr = cli("wordcount", path, "--stamp-spec", spec_path)
        self.assertEqual(code, 2)
        self.assertIn("already exists", stderr)
        source = Package(path)
        result = build(source, spec, author=AUTHOR, date=DATE)
        report = verify(source, Package(result.blob), result.manifest)
        self.assertEqual(report.status, "PASS", report.text())
        stamped = Package(result.blob)
        after = wordcount.analyse(stamped)
        self.assertEqual((after["stated"], after["matches"]), (76, True))
        self.assertIn("Word count: 99 words (excluding references and figure legends)",
                      view_texts(stamped.document, "reject"))
        self.assertIsNone(wordcount.stamp_spec(stamped, 76))

    def test_stamp_spec_on_a_built_manuscript(self):
        wrong = self.title_page(word_count=1234)
        built = template.build_manuscript(SOURCE, wrong)
        self.assertTrue(any("states 1,234 words" in w for w in built.warnings))
        source = Package(built.blob)
        before = wordcount.analyse(source)
        self.assertEqual(before["stated"], 1234)
        self.assertFalse(before["matches"])
        spec = wordcount.stamp_spec(source, before["house"]["total"])
        self.assertEqual(spec["edits"][0]["old"], "1,234")
        result = build(source, spec, author=AUTHOR, date=DATE)
        report = verify(source, Package(result.blob), result.manifest)
        self.assertEqual(report.status, "PASS", report.text())
        after = wordcount.analyse(Package(result.blob))
        self.assertTrue(after["matches"])
        self.assertEqual(after["house"]["total"], before["house"]["total"])
        accepted = view_doc(Package(result.blob).document, "accept")
        self.assertEqual(sum(1 for _ in accepted.iter("w:ins")), 0)

    def test_stamp_spec_rewords_the_scope_when_the_method_changes(self):
        path = template.write_exclusive(self.dir / "house.docx", template.build_manuscript(SOURCE, TITLE_PAGE).blob)
        source = Package(path)
        before = wordcount.analyse(source, method="strict")
        self.assertFalse(before["matches"])
        self.assertIs(before["scope"]["matches"], False)
        code, stdout, _ = cli("wordcount", path, "--method", "strict")
        self.assertEqual(code, 1)
        self.assertIn("NOTE: the title page's parenthesis", stdout)
        strict = before["strict"]["total"]
        spec = wordcount.stamp_spec(source, strict, method="strict")
        edit = spec["edits"][0]
        self.assertEqual(edit["old"], f"{before['stated']:,} words (excluding references and figure legends)")
        self.assertEqual(edit["new"], f"{strict:,} words ({before['scope']['expected']})")
        result = build(source, spec, author=AUTHOR, date=DATE)
        report = verify(source, Package(result.blob), result.manifest)
        self.assertEqual(report.status, "PASS", report.text())
        after = wordcount.analyse(Package(result.blob), method="strict")
        self.assertEqual((after["matches"], after["scope"]["matches"]), (True, True))
        self.assertIsNone(wordcount.stamp_spec(Package(result.blob), strict, method="strict"))
        rejected = view_texts(Package(result.blob).document, "reject")
        self.assertIn(f"Word count: {before['stated']:,} words (excluding references and figure legends)", rejected)
        # the house wording is left alone when only the digits differ
        self.assertNotIn("(", wordcount.stamp_spec(source, 5, method="house")["edits"][0]["old"])

    def test_title_page_command_words_the_scope_for_its_method(self):
        out = self.dir / "strict title.docx"
        code, stdout, stderr = cli("title-page", "--title-page", TITLE_PAGE, "--out", out, "--word-count", "321",
                                   "--method", "strict")
        self.assertEqual(code, 0, stderr)
        self.assertIn("strict method", stdout)
        texts = [t for _, t in paragraphs(Package(out))]
        scope, _ = wordcount.scope_text("strict")
        self.assertIn(f"Word count: 321 words ({scope})", texts)


def _soffice():
    candidates = [os.environ.get("MSW_SOFFICE"), shutil.which("soffice"), shutil.which("libreoffice"),
                  "C:/Program Files/LibreOffice/program/soffice.exe",
                  "/Applications/LibreOffice.app/Contents/MacOS/soffice"]
    return next((c for c in candidates if c and Path(c).is_file()), None)


@unittest.skipUnless(_soffice(), "LibreOffice (soffice) is not installed")
class LibreOfficeRenderTests(Workspace):
    def test_example_converts_to_pdf(self):
        docx = template.write_exclusive(self.dir / "example.docx",
                                        template.build_manuscript(SOURCE, TITLE_PAGE).blob)
        profile = (self.dir / "lo-profile").resolve().as_uri()
        completed = subprocess.run([_soffice(), f"-env:UserInstallation={profile}", "--headless", "--convert-to",
                                    "pdf", "--outdir", str(self.dir), str(docx)],
                                   capture_output=True, timeout=300)
        pdf = self.dir / "example.pdf"
        self.assertTrue(pdf.is_file(), completed.stderr.decode(errors="replace"))
        data = pdf.read_bytes()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertGreater(len(data), 5000)


if __name__ == "__main__":
    unittest.main()

"""Engine behaviour added after the first review: comment threads, paragraph styles, guards."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "scientific-manuscript" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import msw_fixtures as fx  # noqa: E402
from mswlib.comments import list_threads  # noqa: E402
from mswlib.docx import Package  # noqa: E402
from mswlib.edit import EditError, build  # noqa: E402
from mswlib.endnote import census  # noqa: E402
from mswlib.gate import verify  # noqa: E402
from mswlib.pathutil import fs  # noqa: E402
from mswlib.wordml import formatted_segments  # noqa: E402
from mswlib.views import view_doc  # noqa: E402
from mswlib.wordml import iter_paragraphs, paragraph_style, paragraph_text  # noqa: E402

DATE = "2026-09-24T12:00:00Z"


def check(report, name):
    return next(c for c in report.checks if c["name"] == name)


class CommentThreadTests(unittest.TestCase):
    def setUp(self):
        self.source = Package(fx.make_docx())
        first = build(self.source, {"edits": [
            {"op": "comment", "para": "10000003", "anchor": "compared between groups", "text": "Which test?"},
            {"op": "comment", "para": "1000000A", "anchor": "error bars", "text": "Define SEM."}]},
            author="Reviewer A", date=DATE)
        self.commented = Package(first.blob)

    def test_threads_list_anchor_and_text(self):
        threads = list_threads(self.commented)
        self.assertEqual([(t["anchor"], t["text"]) for t in threads],
                         [("compared between groups", "Which test?"), ("error bars", "Define SEM.")])

    def test_resolve_removes_one_thread_and_its_markers(self):
        result = build(self.commented, {"edits": [{"op": "resolve_comment", "comment_text": "Which test?"}]},
                       author="Author", date=DATE)
        package = Package(result.blob)
        self.assertEqual([t["text"] for t in list_threads(package)], ["Define SEM."])
        self.assertEqual(package.document.data.count(b"commentRangeStart"), 1)
        self.assertEqual(package.document.data.count(b"commentReference"), 1)
        report = verify(self.commented, package, result.manifest)
        self.assertEqual(report.status, "PASS", report.text())
        self.assertEqual(result.manifest["declared"]["comments_removed"], ["0"])

    def test_resolve_needs_a_unique_comment(self):
        with self.assertRaises(EditError):
            build(self.commented, {"edits": [{"op": "resolve_comment", "comment_text": "e"}]}, author="A")
        with self.assertRaises(EditError):
            build(self.source, {"edits": [{"op": "resolve_comment", "comment": "0"}]}, author="A")


class ParagraphStyleTests(unittest.TestCase):
    def setUp(self):
        self.source = Package(fx.make_docx())

    def styles(self, blob, mode):
        doc = view_doc(Package(blob).document, mode)
        return [(paragraph_style(p), paragraph_text(doc, p)) for p in iter_paragraphs(doc)]

    def test_text_after_a_heading_takes_the_body_style_and_rejects_cleanly(self):
        result = build(self.source, {"edits": [{"op": "insert_paragraph", "after": "10000002", "text": "New body."}]},
                       author="A", date=DATE)
        accepted = self.styles(result.blob, "accept")
        index = [t for _, t in accepted].index("New body.")
        self.assertEqual(accepted[index - 1], ("Heading1", "Abstract"))
        self.assertNotEqual(accepted[index][0], "Heading1")
        self.assertEqual(self.styles(result.blob, "reject"), self.styles(fx.make_docx(), "reject"))
        self.assertEqual(verify(self.source, Package(result.blob), result.manifest).status, "PASS")

    def test_explicit_style(self):
        result = build(self.source, {"edits": [{"op": "insert_paragraph", "after": "10000003", "text": "Heading.",
                                                "style": "Heading1"}]}, author="A", date=DATE)
        accepted = self.styles(result.blob, "accept")
        self.assertIn(("Heading1", "Heading."), accepted)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.source = Package(fx.make_docx())

    def gate(self, edits):
        result = build(self.source, {"edits": edits}, author="A", date=DATE)
        return verify(self.source, Package(result.blob), result.manifest)

    def test_wording_change_does_not_trip_the_science_guard(self):
        report = self.gate([{"para": "10000003", "old": "compared", "new": "contrasted"}])
        self.assertEqual(check(report, "science_guard")["status"], "PASS")

    def test_numbers_and_directions_trip_the_science_guard(self):
        report = self.gate([{"para": "10000004", "old": "31", "new": "32"},
                            {"para": "10000003", "old": "rose", "new": "fell"}])
        self.assertEqual(check(report, "science_guard")["status"], "WARN")
        self.assertEqual(report.status, "PASS")

    def test_changed_words_before_a_citation_warn(self):
        report = self.gate([{"para": "10000003", "before": "expression rose after ", "old": "training",
                             "new": "exercise"}])
        self.assertEqual(check(report, "citation_context")["status"], "WARN")

    def test_ref_placeholders_are_counted(self):
        extra = fx.para(fx.run("A dataset [REF: nhanes] and a paper [PMID: 12345678]."), "1000000F")
        found = [p["text"] for p in census(Package(fx.make_docx(extra_paragraphs=extra)).document)["placeholders"]]
        self.assertEqual(found, ["[REF: nhanes]", "[PMID: 12345678]"])


class MoreEngineTests(unittest.TestCase):
    def setUp(self):
        self.source = Package(fx.make_docx())

    def flags_of(self, blob, word):
        doc = view_doc(Package(blob).document, "accept")
        for paragraph in iter_paragraphs(doc):
            for text, flags in formatted_segments(doc, paragraph):
                if word in text:
                    return flags
        return None

    def test_format_caps_smallcaps_and_highlight(self):
        result = build(self.source, {"edits": [
            {"op": "format", "para": "10000003", "text": "compared", "set": ["smallcaps"]},
            {"op": "format", "para": "10000004", "text": "cohort", "set": ["highlight"], "highlight": "green"}]},
            author="A", date=DATE)
        self.assertIn("smallcaps", self.flags_of(result.blob, "compared"))
        self.assertIn("highlight", self.flags_of(result.blob, "cohort"))
        self.assertIn(b'<w:highlight w:val="green"/>', Package(result.blob).document.data)
        self.assertEqual(verify(self.source, Package(result.blob), result.manifest).status, "PASS")

    def test_unknown_format_flag_is_refused(self):
        with self.assertRaisesRegex(EditError, "unsupported formatting"):
            build(self.source, {"edits": [{"op": "format", "para": "10000003", "text": "compared",
                                           "set": ["sparkle"]}]}, author="A")

    def test_mark_done_without_comments_extended_is_an_edit_error(self):
        commented = build(self.source, {"edits": [{"op": "comment", "para": "10000003", "anchor": "compared",
                                                   "text": "Why?"}]}, author="A", date=DATE)
        with self.assertRaisesRegex(EditError, "commentsExtended"):
            build(Package(commented.blob), {"edits": [{"op": "resolve_comment", "comment": "0", "mode": "done"}]},
                  author="A")

    def test_science_guard_scans_inserted_paragraphs(self):
        result = build(self.source, {"edits": [{"op": "insert_paragraph", "after": "10000004",
                                                "text": "Levels rose by 12% (P = 0.03)."}]}, author="A", date=DATE)
        report = verify(self.source, Package(result.blob), result.manifest)
        self.assertEqual(check(report, "science_guard")["status"], "WARN")

    def test_punctuation_before_a_citation_is_not_a_context_change(self):
        result = build(self.source, {"edits": [{"op": "insert", "para": "10000003", "before": "after training",
                                                "new": ","}]}, author="A", date=DATE)
        report = verify(self.source, Package(result.blob), result.manifest)
        self.assertEqual(check(report, "citation_context")["status"], "PASS")

    def test_a_code_repository_is_never_part_of_a_manuscript_project(self):
        import contextlib
        import io
        from mswlib import cli
        from mswlib.config import find_config
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "manuscript"
            (project / "04_Analysis" / "tool" / ".git").mkdir(parents=True)
            (project / "04_Analysis" / "tool" / "src").mkdir()
            (project / "manuscript.json").write_text("{}", encoding="utf-8")
            code = project / "04_Analysis" / "tool" / "src"
            self.assertEqual(find_config(project / "04_Analysis"), project / "manuscript.json")
            self.assertIsNone(find_config(code))           # the repository root ends the search
            before = sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob("*"))
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                status = cli.main(["status", "--project", str(code)])
                claim = cli.main(["pass", "new", "--slug", "code change", "--project", str(code)])
            self.assertEqual((status, claim), (2, 2), err.getvalue())
            self.assertIn("no manuscript.json", err.getvalue())
            self.assertEqual(sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob("*")), before)

    def test_records_name_files_without_machine_paths(self):
        from mswlib.config import record_path
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "project"
            (root / "01_Manuscripts").mkdir(parents=True)
            (root / "manuscript.json").write_text("{}", encoding="utf-8")
            inside = root / "01_Manuscripts" / "V02.docx"
            self.assertEqual(record_path(inside), "01_Manuscripts/V02.docx")
            self.assertEqual(record_path(Path(folder) / "elsewhere" / "export.xml"), "export.xml")
            self.assertEqual(record_path(Path("relative") / "file.json"), "relative/file.json")
            self.assertIsNone(record_path(None))

    @unittest.skipUnless(os.name == "nt", "the Windows path limit")
    def test_a_long_path_with_dot_dot_segments_is_normalized(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            image = Path(folder) / "figure.png"
            image.write_bytes(fx.png())
            deep = str(Path(folder, *(["a_long_folder_name_segment"] * 9)))
            raw = deep + (os.sep + "..") * 9 + os.sep + "figure.png"
            self.assertGreater(len(raw), 260)
            self.assertLess(len(os.path.abspath(raw)), 240)
            with open(fs(raw), "rb") as handle:        # the folders need not exist once normalized
                self.assertEqual(handle.read(), fx.png())

    def test_writes_to_a_path_longer_than_260_characters(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            self.addCleanup(shutil.rmtree, fs(folder), True)   # long paths need the extended form to clean up
            deep = Path(folder)
            while len(str(deep)) < 280:
                deep = deep / ("long_folder_name_" + str(len(str(deep))))
            os.makedirs(fs(deep), exist_ok=True)
            target = deep / "2026-01-01 AB EXJ V02 a descriptive slug tracked.docx"
            result = build(self.source, {"edits": [{"para": "10000003", "old": "compared", "new": "contrasted"}]},
                           author="A", date=DATE)
            Package(result.blob).write(target)
            self.assertEqual(Package(target).sha256, Package(result.blob).sha256)


class SymbolFontTests(unittest.TestCase):
    SYMBOL = '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/>'

    def setUp(self):
        extra = fx.para(fx.run("IL-1") + fx.run("b", self.SYMBOL) + fx.run(" rose by 5 ")
                        + fx.run("m", self.SYMBOL) + fx.run("g in both groups."), "1000000F")
        self.source = Package(fx.make_docx(extra_paragraphs=extra))

    def text(self, blob, mode):
        doc = view_doc(Package(blob).document, mode)
        return next(paragraph_text(doc, p) for p in iter_paragraphs(doc) if p.get("w14:paraId") == "1000000F")

    def test_symbol_glyphs_are_read_edited_and_kept(self):
        self.assertEqual(self.text(fx.make_docx(extra_paragraphs=fx.para(
            fx.run("IL-1") + fx.run("b", self.SYMBOL), "1000000F")), "accept"), "IL-1\u03b2")
        result = build(self.source, {"edits": [{"op": "rewrite", "para": "1000000F",
                                                "text": "IL-1\u03b2 rose by 6 \u03bcg in both groups."}]},
                       author="A", date=DATE)
        self.assertEqual(self.text(result.blob, "accept"), "IL-1\u03b2 rose by 6 \u03bcg in both groups.")
        self.assertEqual(self.text(result.blob, "reject"), "IL-1\u03b2 rose by 5 \u03bcg in both groups.")
        data = Package(result.blob).document.data
        self.assertNotIn("\u03b2".encode(), data)   # the Symbol run keeps its stored letter
        self.assertEqual(verify(self.source, Package(result.blob), result.manifest).status, "PASS")

    def test_inserted_text_never_inherits_a_symbol_font(self):
        result = build(self.source, {"edits": [{"op": "insert", "para": "1000000F", "before": "IL-1\u03b2",
                                                "new": " levels"}]}, author="A", date=DATE)
        self.assertEqual(self.text(result.blob, "accept"), "IL-1\u03b2 levels rose by 5 \u03bcg in both groups.")
        doc = view_doc(Package(result.blob).document, "accept")
        paragraph = next(p for p in iter_paragraphs(doc) if p.get("w14:paraId") == "1000000F")
        for run in paragraph.iter("w:r"):
            if "levels" in paragraph_text(doc, run):
                fonts = run.find("w:rPr").find("w:rFonts") if run.find("w:rPr") is not None else None
                self.assertIsNone(fonts)
        self.assertEqual(verify(self.source, Package(result.blob), result.manifest).status, "PASS")


class ParagraphGuardTests(unittest.TestCase):
    """delete_paragraph, insert_paragraph and paragraph_format on awkward paragraphs (review findings)."""

    LANDSCAPE = ('<w:sectPr><w:pgSz w:w="15840" w:h="12240" w:orient="landscape"/>'
                 '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" '
                 'w:footer="708" w:gutter="0"/></w:sectPr>')
    TABLE = ('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr><w:tblGrid><w:gridCol w:w="4000"/>'
             '</w:tblGrid><w:tr><w:tc><w:tcPr><w:tcW w:w="4000" w:type="dxa"/></w:tcPr>'
             + fx.para(fx.run("Cell text"), "1000001A") + '</w:tc></w:tr></w:tbl>')

    def gate(self, source, edits):
        result = build(source, {"edits": edits}, author="A", date=DATE)
        return result, verify(source, Package(result.blob), result.manifest)

    def test_a_section_break_paragraph_is_not_deleted(self):
        extra = fx.para("", "10000010", ppr_extra=self.LANDSCAPE) + fx.para(fx.run("After."), "10000011")
        source = Package(fx.make_docx(extra_paragraphs=extra))
        with self.assertRaisesRegex(EditError, "ends a section"):
            build(source, {"edits": [{"op": "delete_paragraph", "para": "10000010"}]}, author="A")

    def test_the_gate_fails_a_lost_section(self):
        extra = fx.para(fx.run("Landscape table"), "10000010", ppr_extra=self.LANDSCAPE) \
            + fx.para(fx.run("After."), "10000011")
        blob = fx.make_docx(extra_paragraphs=extra)
        lost = fx.replace_document(blob, lambda xml: xml.replace(self.LANDSCAPE.encode(), b""))
        report = verify(Package(blob), Package(lost), None)
        self.assertEqual(check(report, "sections")["status"], "FAIL")

    def test_a_paragraph_without_a_follower_is_not_deleted(self):
        extra = fx.para(fx.run("Before the table."), "10000010") + self.TABLE
        source = Package(fx.make_docx(extra_paragraphs=extra))
        for key in ("10000010", "1000001A"):
            with self.assertRaisesRegex(EditError, "no following paragraph"):
                build(source, {"edits": [{"op": "delete_paragraph", "para": key}]}, author="A")

    def test_deleting_a_picture_paragraph_is_declared(self):
        source = Package(fx.make_docx())
        result, report = self.gate(source, [{"op": "delete_paragraph", "para": "10000009", "allow_fields": True}])
        self.assertEqual(report.status, "PASS", report.text())
        self.assertEqual([p["name"] for p in result.manifest["declared"]["pictures_removed"]], ["Figure 1"])

    def test_undeclared_picture_loss_still_fails(self):
        source = Package(fx.make_docx())
        result = build(source, {"edits": [{"op": "delete_paragraph", "para": "10000009", "allow_fields": True}]},
                       author="A", date=DATE)
        manifest = json.loads(json.dumps(result.manifest))
        manifest["declared"]["pictures_removed"] = []
        self.assertEqual(check(verify(source, Package(result.blob), manifest), "pictures")["status"], "FAIL")

    def test_self_closing_paragraphs_can_be_deleted_anchored_and_formatted(self):
        extra = '<w:p w14:paraId="10000010" w14:textId="77777777"/>' + fx.para(fx.run("After."), "10000011")
        source = Package(fx.make_docx(extra_paragraphs=extra))
        for edits in ([{"op": "delete_paragraph", "para": "10000010"}],
                      [{"op": "insert_paragraph", "after": "10000010", "text": "New text."}],
                      [{"op": "paragraph_format", "para": "10000010", "ppr_add": "<w:keepNext/>"}]):
            _, report = self.gate(source, edits)
            self.assertEqual(report.status, "PASS", report.text())

    def test_edits_that_end_at_a_comment_anchor(self):
        commented = Package(build(Package(fx.make_docx()), {"edits": [
            {"op": "comment", "para": "10000003", "anchor": "compared", "text": "Which test?"}]},
            author="Reviewer", date=DATE).blob)
        for edit in ({"para": "10000003", "old": "compared", "new": "contrasted"},
                     {"op": "delete", "para": "10000003", "before": "were ", "old": "compared"},
                     {"op": "format", "para": "10000003", "text": "compared", "set": ["italic"]}):
            _, report = self.gate(commented, [edit])
            self.assertEqual(report.status, "PASS", report.text())

    def test_no_break_spaces_can_be_added(self):
        source = Package(fx.make_docx())
        _, report = self.gate(source, [{"para": "10000004", "old": "31 kg", "new": "31\u00a0kg"}])
        self.assertEqual(report.status, "PASS", report.text())
        rewritten = "(A) Points are participants; error bars show the SEM; \u27e8i\u27e9P\u27e8/i\u27e9 <\u00a00.05."
        result, report = self.gate(source, [{"op": "rewrite", "para": "1000000A", "text": rewritten}])
        self.assertEqual(report.status, "PASS", report.text())
        self.assertIn("\u00a0".encode(), Package(result.blob).document.data)

    def test_identical_paragraphs_are_told_apart_by_position(self):
        twin = fx.run("Same sentence in two places.")
        extra = '<w:p>' + twin + '</w:p>' + fx.para(fx.run("Between."), "10000011") + '<w:p>' + twin + '</w:p>'
        source = Package(fx.make_docx(extra_paragraphs=extra))
        first, second = [{"edits": [{"para": {"index": index}, "old": "Same", "new": "One"}]} for index in (10, 12)]
        second_result = build(source, second, author="A", date=DATE)
        self.assertEqual(verify(source, Package(second_result.blob), second_result.manifest).status, "PASS")
        first_result = build(source, first, author="A", date=DATE)
        # the first copy was changed, but the manifest declares the second: the gate must notice
        report = verify(source, Package(first_result.blob), second_result.manifest)
        self.assertEqual(check(report, "accepted_text")["status"], "FAIL")

    def test_inserted_paragraph_after_a_text_box_paragraph(self):
        box = ('<w:r><w:pict><v:shape id="box1" style="width:100pt;height:40pt"><v:textbox><w:txbxContent>'
               + fx.para(fx.run("Inside the box."), "1000001B") + '</w:txbxContent></v:textbox></v:shape>'
               '</w:pict></w:r>')
        extra = fx.para(fx.run("Has a box ") + box, "10000010") + fx.para(fx.run("After."), "10000011")
        source = Package(fx.make_docx(extra_paragraphs=extra))
        result, report = self.gate(source, [{"op": "insert_paragraph", "after": "10000010", "text": "Added."}])
        self.assertEqual(report.status, "PASS", report.text())
        [item] = result.manifest["expected_accept"]
        self.assertEqual(item["new_index"], 12)   # after the anchor (10) and the paragraph in its text box (11)


def _rezip(blob: bytes, changes: dict) -> bytes:
    """A copy of a .docx with some parts replaced (bytes) or transformed (callables)."""
    import io
    import zipfile
    source = zipfile.ZipFile(io.BytesIO(blob))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for info in source.infolist():
            data = source.read(info.filename)
            change = changes.get(info.filename)
            archive.writestr(info, change(data) if callable(change) else (change if change is not None else data))
        for name, data in changes.items():
            if name not in source.namelist():
                archive.writestr(name, data)
    return buffer.getvalue()


class CodexReviewTests(unittest.TestCase):
    """Findings of the independent (Codex) review, each reproduced first."""

    def setUp(self):
        self.source = Package(fx.make_docx())

    def built(self, source, edits, **options):
        result = build(source, {"edits": edits}, author="A", date=DATE, **options)
        return result, verify(source, Package(result.blob), result.manifest)

    # -- gate --------------------------------------------------------------------------------
    def test_values_swapped_between_groups_warn(self):
        source = Package(fx.make_docx(extra_paragraphs=fx.para(fx.run("Treated n = 20; controls n = 30 in all."),
                                                               "1000000F")))
        _, report = self.built(source, [{"op": "rewrite", "para": "1000000F",
                                         "text": "Treated n = 30; controls n = 20 in all."}])
        guard = check(report, "science_guard")
        self.assertEqual(guard["status"], "WARN")
        self.assertIn("new order", guard["items"][0])

    def test_a_sentence_full_stop_is_not_part_of_a_number(self):
        source = Package(fx.make_docx(extra_paragraphs=fx.para(fx.run("The sample was n = 30."), "1000000F")))
        _, report = self.built(source, [{"para": "1000000F", "old": "30.", "new": "30 in all."}])
        self.assertEqual(check(report, "science_guard")["status"], "PASS")

    def test_a_structured_abstract_keeps_its_subheadings(self):
        from mswlib.wordcount import boundaries
        rows = [("Title", 0), ("Abstract", 1), ("Background", 2), ("Text one.", 0), ("Methods", 2),
                ("Text two.", 0), ("1. Introduction", 1), ("Body.", 0), ("References", 1)]
        records = [{"index": i, "text": text, "level": level, "words": len(text.split()), "paraId": None}
                   for i, (text, level) in enumerate(rows)]
        self.assertEqual(boundaries(records)["abstract_end"], 6)
        rows.insert(6, ("Keywords: balance; falls", 0))
        records = [{"index": i, "text": text, "level": level, "words": len(text.split()), "paraId": None}
                   for i, (text, level) in enumerate(rows)]
        self.assertEqual(boundaries(records)["abstract_end"], 6)

    def test_an_altered_original_picture_fails_after_a_swap(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "new.png"
            image.write_bytes(fx.png(colour=(10, 200, 10)))
            result = build(self.source, {"edits": [{"op": "swap_picture", "old_name": "Figure 1",
                                                    "new_name": "Figure 1 v2", "image": str(image)}]},
                           author="A", date=DATE)
        tampered = _rezip(result.blob, {"word/media/image1.png": fx.png(colour=(0, 0, 255))})
        report = verify(self.source, Package(tampered), result.manifest)
        self.assertEqual(check(report, "pictures")["status"], "FAIL")
        self.assertEqual(verify(self.source, Package(result.blob), result.manifest).status, "PASS")

    def test_untracked_properties_equations_and_field_codes_fail(self):
        field = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> REF _Ref1 \\h '
                 '</w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>Table 1</w:t></w:r>'
                 '<w:r><w:fldChar w:fldCharType="end"/></w:r>')
        extra = (fx.para(fx.run("See ") + field + fx.run("."), "1000000F")
                 + fx.para(fx.run("Where ") + "<m:oMath><m:r><m:t>x=2</m:t></m:r><m:sSub><m:e><m:r><m:t>y</m:t>"
                           "</m:r></m:e><m:sub><m:r><m:t>2</m:t></m:r></m:sub></m:sSub></m:oMath>", "10000010"))
        source = Package(fx.make_docx(extra_paragraphs=extra))
        result, report = self.built(source, [{"para": "10000003", "old": "compared", "new": "contrasted"}])
        self.assertEqual(report.status, "PASS", report.text())
        for label, old, new in (("font size", b'<w:sz w:val="24"/>', b'<w:sz w:val="4"/>'),
                                ("equation", b"<m:t>x=2</m:t>", b"<m:t>x=9</m:t>"),
                                ("equation structure", b"<m:sSub><m:e><m:r><m:t>y</m:t></m:r></m:e><m:sub>",
                                 b"<m:sSup><m:e><m:r><m:t>y</m:t></m:r></m:e><m:sup>"),
                                ("field code", b" REF _Ref1 ", b" REF _Ref2 "),
                                ("heading style", b'<w:pStyle w:val="Heading1"/></w:pPr><w:r w:rsidR="00A1B2C3" '
                                 b'w:rsidRPr="00D4E5F6"><w:t>Abstract', b'</w:pPr><w:r w:rsidR="00A1B2C3" '
                                 b'w:rsidRPr="00D4E5F6"><w:t>Abstract')):
            with self.subTest(label):
                def tamper(xml, old=old, new=new):
                    xml = xml.replace(old, new, 1)
                    if new.startswith(b"<m:sSup>"):
                        xml = xml.replace(b"</m:sub></m:sSub>", b"</m:sup></m:sSup>", 1)
                    return xml
                blob = fx.replace_document(result.blob, tamper)
                self.assertNotEqual(blob, result.blob)
                self.assertEqual(check(verify(source, Package(blob), result.manifest), "untracked_formatting")
                                 ["status"], "FAIL")

    def test_a_picture_relationship_that_does_not_exist_fails(self):
        result, _ = self.built(self.source, [{"para": "10000003", "old": "compared", "new": "contrasted"}])
        blob = fx.replace_document(result.blob, lambda xml: xml.replace(b'r:embed="rId10"', b'r:embed="rId99"'))
        report = verify(self.source, Package(blob), result.manifest)
        self.assertEqual(check(report, "package")["status"], "FAIL")
        self.assertEqual(check(report, "pictures")["status"], "FAIL")

    # -- engine ------------------------------------------------------------------------------
    def test_track_revisions_off_in_settings_is_turned_on(self):
        source = Package(_rezip(fx.make_docx(), {"word/settings.xml": lambda d: d.replace(
            b"<w:trackRevisions/>", b'<w:trackRevisions w:val="0"/>')}))
        result, report = self.built(source, [{"para": "10000003", "old": "compared", "new": "contrasted"}],
                                    track_revisions=True)
        self.assertIn(b"<w:trackRevisions/>", Package(result.blob).parts["word/settings.xml"])
        self.assertEqual(report.status, "PASS", report.text())

    def test_unsetting_italics_that_come_from_a_style(self):
        styles = lambda data: data.replace(b"</w:styles>", b'<w:style w:type="character" w:styleId="Emphasis">'
                                           b'<w:name w:val="Emphasis"/><w:rPr><w:i/></w:rPr></w:style></w:styles>')
        extra = fx.para(fx.run("An emphasised phrase here.", '<w:rStyle w:val="Emphasis"/>'), "1000000F")
        source = Package(_rezip(fx.make_docx(extra_paragraphs=extra), {"word/styles.xml": styles}))
        result, report = self.built(source, [{"op": "format", "para": "1000000F", "text": "emphasised",
                                              "unset": ["italic"]}])
        self.assertIn(b'<w:rStyle w:val="Emphasis"/><w:i w:val="0"/><w:rPrChange', Package(result.blob).document.data)
        self.assertEqual(report.status, "PASS", report.text())
        plain = Package(fx.make_docx())
        result, _ = self.built(plain, [{"op": "format", "para": "10000003", "text": "compared", "unset": ["italic"]}])
        self.assertNotIn(b'w:i w:val="0"', Package(result.blob).document.data)   # nothing inherited: no override

    def test_subscript_becomes_superscript(self):
        extra = fx.para(fx.run("CO") + fx.run("2", '<w:vertAlign w:val="subscript"/>'), "1000000F")
        source = Package(fx.make_docx(extra_paragraphs=extra))
        result, report = self.built(source, [{"op": "format", "para": "1000000F", "text": "2",
                                              "set": ["superscript"], "unset": ["subscript"]}])
        doc = view_doc(Package(result.blob).document, "accept")
        paragraph = next(p for p in iter_paragraphs(doc) if p.get("w14:paraId") == "1000000F")
        self.assertEqual(formatted_segments(doc, paragraph)[-1], ("2", frozenset({"superscript"})))
        self.assertEqual(report.status, "PASS", report.text())

    def test_superscript_replaces_a_subscript_that_comes_from_a_style(self):
        styles = lambda data: data.replace(b"</w:styles>", b'<w:style w:type="character" w:styleId="Sub">'
                                           b'<w:name w:val="Sub"/><w:rPr><w:vertAlign w:val="subscript"/></w:rPr>'
                                           b'</w:style></w:styles>')
        extra = fx.para(fx.run("CO") + fx.run("2", '<w:rStyle w:val="Sub"/>'), "1000000F")
        source = Package(_rezip(fx.make_docx(extra_paragraphs=extra), {"word/styles.xml": styles}))
        result, report = self.built(source, [{"op": "format", "para": "1000000F", "text": "2",
                                              "set": ["superscript"], "unset": ["subscript"]}])
        data = Package(result.blob).document.data
        self.assertIn(b'<w:vertAlign w:val="superscript"/>', data)
        self.assertNotIn(b'w:val="baseline"', data)
        self.assertEqual(report.status, "PASS", report.text())

    def test_track_revisions_is_added_to_an_empty_settings_part(self):
        empty = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n<w:settings xmlns:w="'
                 b'http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
        source = Package(_rezip(fx.make_docx(), {"word/settings.xml": empty}))
        result, report = self.built(source, [{"para": "10000003", "old": "compared", "new": "contrasted"}],
                                    track_revisions=True)
        self.assertIn(b"<w:trackRevisions/></w:settings>", Package(result.blob).parts["word/settings.xml"])
        self.assertEqual(report.status, "PASS", report.text())

    def test_comments_are_added_to_an_empty_comments_part(self):
        rel = (b'<Relationship Id="rId20" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
               b'comments" Target="comments.xml"/></Relationships>')
        override = (b'<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-'
                    b'officedocument.wordprocessingml.comments+xml"/></Types>')
        empty = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n<w:comments xmlns:w="'
                 b'http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
        source = Package(_rezip(fx.make_docx(), {
            "word/_rels/document.xml.rels": lambda d: d.replace(b"</Relationships>", rel),
            "[Content_Types].xml": lambda d: d.replace(b"</Types>", override), "word/comments.xml": empty}))
        result, report = self.built(source, [{"op": "comment", "para": "10000003", "anchor": "compared",
                                              "text": "Which test?"}])
        self.assertEqual(report.status, "PASS", report.text())
        self.assertEqual([t["text"] for t in list_threads(Package(result.blob))], ["Which test?"])

    def test_an_empty_rewrite_is_refused(self):
        with self.assertRaisesRegex(EditError, "rewrite is empty"):
            build(self.source, {"edits": [{"op": "rewrite", "para": "1000000A", "text": "  "}]}, author="A")

    def test_a_kept_no_break_space_survives_a_nearby_rewrite(self):
        source = Package(fx.make_docx(extra_paragraphs=fx.para(fx.run("Low\u00a0values were recorded."), "1000000F")))
        result, report = self.built(source, [{"op": "rewrite", "para": "1000000F",
                                              "text": "High readings were recorded."}])
        doc = view_doc(Package(result.blob).document, "accept")
        text = next(paragraph_text(doc, p) for p in iter_paragraphs(doc) if p.get("w14:paraId") == "1000000F")
        self.assertEqual(text, "High\u00a0readings were recorded.")
        self.assertEqual(report.status, "PASS", report.text())


if __name__ == "__main__":
    unittest.main()

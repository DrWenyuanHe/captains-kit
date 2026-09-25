"""Tests for the scientific-manuscript core engine.

Covers the byte-offset XML tree, the package writer, accept/reject views, the
EndNote field census, the tracked-change edit engine, the verification gate, the
word-level differ and the core command line. Every document is synthetic
(tests/msw_fixtures.py); nothing here needs Word, LibreOffice or a network.

Tests marked "Known defect" are expected failures that reproduce an engine defect.
When the defect is fixed they report an unexpected success: remove the decorator.
"""

import contextlib
import functools
import io
import json
import os
import re
import struct
import sys
import tempfile
import unittest
import warnings
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "scientific-manuscript" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import msw_fixtures as fx  # noqa: E402
from mswlib import cli, config, docx, edit, endnote, gate, textdiff, views, wordml, xmltree  # noqa: E402

MAIN = docx.MAIN_PART
AUTHOR = "Test Reviser"
DATE = "2026-09-01T00:00:00Z"
PRIOR = f'w:author="{fx.AUTHOR_OTHER}" w:date="2026-01-01T00:00:00Z"'
NEW = f'w:author="{AUTHOR}" w:date="{DATE}"'.encode()
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
COMMENTS_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
TEXT_TAGS = ("w:t", "w:delText", "w:instrText", "w:delInstrText")
REVISION_TAGS = wordml.RUN_REVISIONS | wordml.PROPERTY_CHANGES

# Exact fixture bytes that the corruption transforms anchor on.
MARKER_RUN = fx.run("Marker levels were compared between groups ").encode()
GENE_RUN = fx.run("GENE1", "<w:i/>").encode()
SUPERSCRIPT_RUN = fx.run("2", '<w:vertAlign w:val="superscript"/>').encode()
HEADING_RUN = fx.run("Abstract").encode()
FIELD_END_RUN = b'<w:r><w:fldChar w:fldCharType="end"/></w:r>'


# -- helpers ---------------------------------------------------------------------------

def swap_once(old: bytes, new: bytes):
    """A document transform that replaces bytes that must occur exactly once."""
    def transform(data):
        count = data.count(old)
        if count != 1:
            raise AssertionError(f"fixture bytes {old[:60]!r} found {count} times")
        return data.replace(old, new)
    return transform


def document(blob) -> xmltree.XMLDoc:
    return docx.Package(blob).document


def paragraph(doc, para_id):
    hits = [p for p in wordml.iter_paragraphs(doc) if p.get("w14:paraId") == para_id]
    if len(hits) != 1:
        raise AssertionError(f"paraId {para_id} found {len(hits)} times")
    return hits[0]


@functools.lru_cache(maxsize=128)
def _texts(blob: bytes, mode: str) -> tuple:
    return tuple(views.view_texts(document(blob), mode))


def texts(blob, mode):
    return list(_texts(bytes(blob), mode))


def view_paragraph_text(blob, mode, para_id):
    doc = views.view_doc(document(blob), mode)
    return wordml.paragraph_text(doc, paragraph(doc, para_id))


def char_flags(blob, mode, para_id, needle):
    """Formatting flags of every character of `needle` in a projected paragraph."""
    doc = views.view_doc(document(blob), mode)
    chars = [(c, set(flags)) for text, flags in wordml.formatted_segments(doc, paragraph(doc, para_id))
             for c in text]
    text = "".join(c for c, _ in chars)
    start = text.index(needle)
    return [flags for _, flags in chars[start:start + len(needle)]]


def run_build(source, edits, **options):
    options.setdefault("author", AUTHOR)
    options.setdefault("date", DATE)
    spec = {"edits": edits}
    if "intended" in options:
        spec["intended"] = options.pop("intended")
    return edit.build(source, spec, **options)


def verify(source, candidate, manifest=None, **options):
    return gate.verify(docx.Package(source), docx.Package(candidate), manifest, **options)


def statuses(report):
    return {check["name"]: check["status"] for check in report.checks}


def failing(report):
    return sorted(check["name"] for check in report.checks if check["status"] == "FAIL")


def new_revisions(blob, author=AUTHOR):
    return [e for e in document(blob).elements if e.tag in REVISION_TAGS and e.get("w:author") == author]


def rezip(blob, *, change=None, drop=(), add=()):
    change = change or {}
    source = zipfile.ZipFile(io.BytesIO(blob))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for info in source.infolist():
            if info.filename in drop:
                continue
            data = source.read(info)
            if info.filename in change:
                data = change[info.filename](data)
            archive.writestr(info, data)
        for name, data in add:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


def with_comments(blob):
    """The fixture plus a comments part holding one existing comment on the Abstract heading."""
    comments = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
                f'<w:comments xmlns:w="{W_NS}"><w:comment w:id="0" {PRIOR} w:initials="CA">'
                '<w:p><w:r><w:t>Existing note</w:t></w:r></w:p></w:comment></w:comments>').encode()
    override = f'<Override PartName="/word/comments.xml" ContentType="{COMMENTS_TYPE}"/>'.encode()
    rel = (b'<Relationship Id="rId20" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
           b'relationships/comments" Target="comments.xml"/>')
    anchored = (b'<w:commentRangeStart w:id="0"/>' + HEADING_RUN + b'<w:commentRangeEnd w:id="0"/>'
                b'<w:r><w:commentReference w:id="0"/></w:r>')
    return rezip(blob, change={
        "[Content_Types].xml": swap_once(b"</Types>", override + b"</Types>"),
        "word/_rels/document.xml.rels": swap_once(b"</Relationships>", rel + b"</Relationships>"),
        MAIN: swap_once(HEADING_RUN, anchored),
    }, add=[("word/comments.xml", comments)])


def plain_docx(extra=""):
    """A fresh manuscript: no tracked changes, bookmarks, fields or pictures (D4)."""
    body = (fx.para(fx.run("Plain first paragraph here."), "30000001")
            + fx.para(fx.run("Second paragraph."), "30000002") + extra)
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
           f'<w:document {fx.NS}><w:body>{body}<w:sectPr/></w:body></w:document>').encode()
    return fx.make_docx(document=xml)


def moves_docx():
    """Paragraphs with a tracked move written the way Word writes it (w:t inside w:moveFrom)."""
    extra = (fx.para(fx.run("Alpha ") + f'<w:moveFromRangeStart w:id="70" {PRIOR} w:name="move1"/>'
                     f'<w:moveFrom w:id="71" {PRIOR}>' + fx.run("moved words ", rsid=False)
                     + '</w:moveFrom><w:moveFromRangeEnd w:id="70"/>' + fx.run("beta."), "20000001")
             + fx.para(fx.run("Gamma ") + f'<w:moveToRangeStart w:id="72" {PRIOR} w:name="move1"/>'
                       f'<w:moveTo w:id="73" {PRIOR}>' + fx.run("moved words ", rsid=False)
                       + '</w:moveTo><w:moveToRangeEnd w:id="72"/>' + fx.run("delta."), "20000002"))
    return fx.make_docx(extra_paragraphs=extra)


def rich_docx():
    """Moves, a nested insertion/deletion, property changes and table-row revisions."""
    def cell(content, pid):
        return '<w:tc><w:tcPr><w:tcW w:w="2000" w:type="dxa"/></w:tcPr>' + fx.para(content, pid) + "</w:tc>"
    nested = (fx.run("Nested ") + f'<w:ins w:id="74" {PRIOR}>' + fx.run("added ", rsid=False)
              + '<w:del w:id="75" w:author="Third reader" w:date="2026-02-01T00:00:00Z"><w:r>'
              '<w:delText xml:space="preserve">dropped </w:delText></w:r></w:del></w:ins>' + fx.run("tail."))
    properties = (f'<w:r><w:rPr><w:b/><w:rPrChange w:id="76" {PRIOR}><w:rPr><w:i/></w:rPr></w:rPrChange></w:rPr>'
                  '<w:t>Formatted</w:t></w:r>')
    table = ('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr><w:tblGrid><w:gridCol w:w="2000"/></w:tblGrid>'
             '<w:tr>' + cell(fx.run("Row kept"), "20000005") + '</w:tr>'
             f'<w:tr><w:trPr><w:ins w:id="78" {PRIOR}/></w:trPr>'
             + cell(f'<w:ins w:id="79" {PRIOR}>' + fx.run("Row inserted", rsid=False) + "</w:ins>", "20000006")
             + '</w:tr>'
             f'<w:tr><w:trPr><w:del w:id="80" {PRIOR}/></w:trPr>'
             + cell(f'<w:del w:id="81" {PRIOR}><w:r><w:delText>Row deleted</w:delText></w:r></w:del>', "20000007")
             + '</w:tr></w:tbl>')
    moves = document(moves_docx())
    move_paragraphs = b"".join(moves.raw(paragraph(moves, pid)) for pid in ("20000001", "20000002")).decode()
    extra = (move_paragraphs + fx.para(nested, "20000003")
             + fx.para(properties, "20000004", ppr_extra=f'<w:jc w:val="center"/><w:pPrChange w:id="77" {PRIOR}>'
                       '<w:pPr><w:jc w:val="left"/></w:pPr></w:pPrChange>')
             + table)
    return fx.make_docx(extra_paragraphs=extra)


def runaway_bibliography(data: bytes) -> bytes:
    """Move the EN.REFLIST end past a heading and a picture paragraph (a runaway bibliography)."""
    position = data.rindex(FIELD_END_RUN)
    data = data[:position] + data[position + len(FIELD_END_RUN):]
    tail = (fx.para(fx.run("Figure Legends"), "40000001", "Heading1")
            + fx.para(fx.picture(name="Figure 2", docpr_id=5), "40000002")
            + fx.para(fx.run("Tail") + FIELD_END_RUN.decode(), "40000003")).encode()
    section = data.index(b"<w:sectPr>")
    return data[:section] + tail + data[section:]


def citation_run_span(data: bytes, number: int) -> tuple[int, int]:
    doc = xmltree.XMLDoc(data)
    item = endnote.citation_fields(doc)[number]
    return item.begin.parent.start, item.end.parent.end


def as_deleted(raw: bytes) -> bytes:
    """Runs rewritten as deleted content (w:delText, w:delInstrText), as Word writes a deletion."""
    raw = re.sub(rb"<w:t(\s[^>]*)?>", lambda m: b"<w:delText" + (m.group(1) or b"") + b">", raw)
    raw = re.sub(rb"<w:instrText(\s[^>]*)?>", lambda m: b"<w:delInstrText" + (m.group(1) or b"") + b">", raw)
    return raw.replace(b"</w:t>", b"</w:delText>").replace(b"</w:instrText>", b"</w:delInstrText>")


def edit_fld_data(position: int):
    """A transform that changes the first base64 quantum of the n-th w:fldData in document order."""
    def transform(data):
        doc = xmltree.XMLDoc(data)
        chunk = list(doc.iter("w:fldData"))[position]
        inner = doc.inner(chunk)
        replacement = b"QUFB" if inner[:4] != b"QUFB" else b"QkJC"
        return data[:chunk.open_end] + replacement + inner[4:] + data[chunk.close_start:]
    return transform


def jpeg(width=60, height=30) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def call_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


# -- xmltree -----------------------------------------------------------------------------

class XmlTreeTests(unittest.TestCase):
    def setUp(self):
        self.data = docx.Package(fx.make_docx(multi_chunk=True)).parts[MAIN]
        self.doc = xmltree.XMLDoc(self.data)

    def test_offsets_slice_the_original_bytes(self):
        root = self.doc.root
        self.assertEqual(root.tag, "w:document")
        self.assertTrue(self.data[:root.start].startswith(b"<?xml"))
        self.assertTrue(self.data[:root.start].endswith(b"\r\n"))
        for element in self.doc.elements:
            raw = self.doc.raw(element)
            self.assertTrue(raw.startswith(b"<" + element.tag.encode()), element)
            self.assertTrue(raw.endswith(b"/>") if element.self_closing else raw.endswith(b">"), element)
        legend = next(t for t in self.doc.iter("w:t") if self.doc.text(t) == " < 0.05.")
        self.assertEqual(self.doc.inner(legend), b" &lt; 0.05.")

    def test_attributes_unescape_both_quote_styles(self):
        doc = xmltree.XMLDoc(b"<a x='1 &amp; 2' y=\"&#x41;&#66;&quot;\"><b/></a>")
        self.assertEqual(doc.root.get("x"), "1 & 2")
        self.assertEqual(doc.root.get("y"), 'AB"')
        self.assertIsNone(doc.root.get("missing"))
        self.assertEqual(xmltree.escape_attr('R&D "x" <y>'), "R&amp;D &quot;x&quot; &lt;y&gt;")
        self.assertEqual(xmltree.escape_text("a < b & c > d"), "a &lt; b &amp; c &gt; d")

    def test_comments_pi_and_cdata_stay_opaque(self):
        data = b"<a><!-- <b> --><?pi <c>?><![CDATA[<d>]]><e/></a>"
        doc = xmltree.XMLDoc(data)
        self.assertEqual([e.tag for e in doc.root.elements()], ["e"])
        self.assertEqual(xmltree.apply_splices(data, []), data)

    def test_splice_round_trip_keeps_unedited_bytes_and_crlf_payloads(self):
        payloads = [self.doc.raw(f) for f in self.doc.iter("w:fldData")]
        self.assertGreaterEqual(len(payloads), 4)
        self.assertTrue(all(b"\r\n" in p for p in payloads), "fixture base64 must be CRLF-wrapped")
        self.assertEqual(xmltree.apply_splices(self.data, []), self.data)
        target = next(t for t in self.doc.iter("w:t") if "compared" in self.doc.text(t))
        new = xmltree.apply_splices(self.data, [(target.open_end, target.close_start, b"Marker levels differed")])
        self.assertEqual(new[:target.open_end], self.data[:target.open_end])
        self.assertEqual(new[-(len(self.data) - target.close_start):], self.data[target.close_start:])
        self.assertEqual(new.count(b"\r\n"), self.data.count(b"\r\n"))
        reparsed = xmltree.XMLDoc(new)
        self.assertEqual([reparsed.raw(f) for f in reparsed.iter("w:fldData")], payloads)
        ET.fromstring(new)

    def test_overlapping_splices_raise(self):
        with self.assertRaises(xmltree.XMLError):
            xmltree.apply_splices(b"0123456789", [(2, 5, b"x"), (4, 6, b"y")])
        self.assertEqual(xmltree.apply_splices(b"0123456789", [(6, 6, b"B"), (2, 4, b"A")]), b"01A45B6789")

    def test_malformed_input_raises(self):
        cases = {
            "mismatched end tag": b"<a><b></a></b>",
            "unclosed element": b"<a><b>",
            "end tag without start": b"</a>",
            "two roots": b"<a/><b/>",
            "text before the root": b"text<a/>",
            "trailing text": b"<a/>tail",
            "byte-order mark": b"\xef\xbb\xbf<a/>",
            "empty": b"",
        }
        for label, data in cases.items():
            with self.subTest(label), self.assertRaises(xmltree.XMLError):
                xmltree.XMLDoc(data)


# -- package -------------------------------------------------------------------------------

class PackageTests(unittest.TestCase):
    def setUp(self):
        self.blob = fx.make_docx()
        self.package = docx.Package(self.blob)
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_write_preserves_order_compression_and_untouched_bytes(self):
        changed = self.package.parts[MAIN].replace(b"were compared", b"were contrasted")
        out = self.folder / "new folder" / "out.docx"
        digest = self.package.write(out, {MAIN: changed}, [("customXml/item1.xml", b"<a/>")])
        self.assertEqual(digest, docx.sha256_file(out))
        original = zipfile.ZipFile(io.BytesIO(self.blob)).infolist()
        with zipfile.ZipFile(out) as archive:
            written = archive.infolist()
        self.assertEqual([i.filename for i in written], [i.filename for i in original] + ["customXml/item1.xml"])
        for before, after in zip(original, written):
            self.assertEqual((before.compress_type, before.date_time, before.external_attr),
                             (after.compress_type, after.date_time, after.external_attr), before.filename)
        self.assertEqual(written[[i.filename for i in written].index("word/media/image1.png")].compress_type,
                         zipfile.ZIP_STORED)
        reread = docx.Package(out)
        for name in self.package.names():
            expected = changed if name == MAIN else self.package.parts[name]
            self.assertEqual(reread.parts[name], expected, name)

    def test_write_refuses_an_existing_destination(self):
        out = self.folder / "existing.docx"
        out.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            self.package.write(out)
        self.assertEqual(out.read_bytes(), b"keep")

    def test_write_validates_xml_and_writes_nothing_on_error(self):
        out = self.folder / "bad.docx"
        with self.assertRaises(docx.PackageError):
            self.package.write(out, {MAIN: b"<w:document><w:body></w:document>"})
        with self.assertRaises(docx.PackageError):
            self.package.write(out, {MAIN: self.package.parts[MAIN].replace(b"were compared", b"R&D", 1)})
        with self.assertRaises(docx.PackageError):
            self.package.write(out, {"word/missing.xml": b"<a/>"})
        with self.assertRaises(docx.PackageError):
            self.package.write(out, additions=[("word/styles.xml", b"<a/>")])
        self.assertFalse(out.exists())

    def test_rejects_corrupt_or_foreign_packages(self):
        png = fx.png()
        position = self.blob.index(png) + 30
        corrupt = self.blob[:position] + bytes([self.blob[position] ^ 0xFF]) + self.blob[position + 1:]
        with self.assertRaises(docx.PackageError):
            docx.Package(corrupt)
        with self.assertRaises(docx.PackageError):
            docx.Package(rezip(self.blob, drop=[MAIN]))
        buffer = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(MAIN, b"<a/>")
                archive.writestr(MAIN, b"<b/>")
        with self.assertRaises(docx.PackageError):
            docx.Package(buffer.getvalue())

    def test_relationships_content_types_and_story_parts(self):
        rels = {r["Id"]: r for r in self.package.relationships(MAIN)}
        self.assertEqual(rels["rId10"]["Target"], "media/image1.png")
        self.assertEqual(self.package.resolve(MAIN, "media/image1.png"), "word/media/image1.png")
        self.assertEqual(self.package.content_type("word/media/image1.png"), "image/png")
        self.assertTrue(self.package.content_type(MAIN).endswith("document.main+xml"))
        self.assertEqual(self.package.story_parts(), [MAIN])
        self.assertEqual(docx.Package(with_comments(self.blob)).story_parts(), [MAIN, "word/comments.xml"])
        self.assertEqual(self.package.sha256, docx.sha256_bytes(self.blob))

    def test_lock_files_detect_word_owner_files(self):
        target = self.folder / "source.docx"
        target.write_bytes(self.blob)
        self.assertEqual(docx.lock_files(target), [])
        (self.folder / "~$other.docx").write_bytes(b"x")
        self.assertEqual(docx.lock_files(target), [])
        (self.folder / "~$urce.docx").write_bytes(b"x")
        (self.folder / "~WRL0001.tmp").write_bytes(b"x")
        self.assertEqual([Path(p).name for p in docx.lock_files(target)], ["~$urce.docx", "~WRL0001.tmp"])

    def test_lock_files_ignore_another_versions_owner_file(self):
        target = self.folder / "2026-09-23 AB EXJ V26 slug.docx"
        target.write_bytes(self.blob)
        (self.folder / "~$26-09-24 AB EXJ V26 slug.docx").write_bytes(b"x")   # the next day's copy
        (self.folder / "~$AB EXJ V26 slug.docx").write_bytes(b"x")            # a shorter name's suffix
        self.assertEqual(docx.lock_files(target), [])
        (self.folder / ".~lock.2026-09-23 AB EXJ V26 slug.docx#").write_bytes(b"x")
        self.assertEqual([Path(p).name for p in docx.lock_files(target)], [".~lock.2026-09-23 AB EXJ V26 slug.docx#"])


# -- wordml --------------------------------------------------------------------------------

class WordmlTests(unittest.TestCase):
    def test_paragraph_text_includes_tabs_breaks_and_symbols(self):
        xml = (f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:r><w:t>a</w:t><w:tab/><w:t>b</w:t><w:br/>'
               '<w:t>c</w:t><w:br w:type="page"/><w:t>d</w:t><w:noBreakHyphen/><w:t>e</w:t>'
               '<w:sym w:font="Symbol" w:char="F062"/></w:r><w:del w:id="1" w:author="x"><w:r>'
               '<w:delText>gone</w:delText></w:r></w:del><w:r><w:instrText> PAGE </w:instrText></w:r>'
               '</w:p></w:body></w:document>').encode()
        doc = xmltree.XMLDoc(xml)
        p = next(wordml.iter_paragraphs(doc))
        self.assertEqual(wordml.paragraph_text(doc, p), "a\tb\nc\fd\u2011e\u03b2")
        self.assertEqual(wordml.paragraph_text(doc, p, deleted=True), "a\tb\nc\fd\u2011e\u03b2gone")

    def test_symbol_font_text_reads_as_greek(self):
        xml = (f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:r><w:t xml:space="preserve">IL-1 </w:t></w:r>'
               '<w:r><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr><w:t>b</w:t></w:r>'
               '<w:r><w:t xml:space="preserve"> and 5 </w:t></w:r>'
               '<w:r><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr><w:t>m</w:t></w:r>'
               '<w:r><w:t>g</w:t></w:r></w:p></w:body></w:document>').encode()
        doc = xmltree.XMLDoc(xml)
        p = next(wordml.iter_paragraphs(doc))
        self.assertEqual(wordml.paragraph_text(doc, p), "IL-1 \u03b2 and 5 \u03bcg")
        self.assertEqual("".join(t for t, _ in wordml.formatted_segments(doc, p)), "IL-1 \u03b2 and 5 \u03bcg")

    def test_rpr_flags_respect_off_values(self):
        doc = xmltree.XMLDoc(f'<w:rPr xmlns:w="{W_NS}"><w:b w:val="0"/><w:i/><w:u w:val="none"/>'
                             '<w:vertAlign w:val="subscript"/><w:highlight w:val="yellow"/></w:rPr>'.encode())
        self.assertEqual(wordml.rpr_flags(doc.root), frozenset({"italic", "subscript", "highlight"}))
        self.assertEqual(wordml.rpr_flags(None), frozenset())

    def test_segments_styles_and_mark_revisions_on_the_fixture(self):
        doc = document(fx.make_docx())
        self.assertEqual([(t, sorted(f)) for t, f in wordml.formatted_segments(doc, paragraph(doc, "10000004"))],
                         [("The unit was 31 kg/m", []), ("2", ["superscript"]),
                          (", which is high (2) in this cohort.", [])])
        self.assertEqual(wordml.paragraph_style(paragraph(doc, "10000002")), "Heading1")
        self.assertEqual(wordml.mark_revision(paragraph(doc, "10000006")), "del")
        self.assertIsNone(wordml.mark_revision(paragraph(doc, "10000007")))

    def test_field_walker_reports_unbalanced_fields(self):
        doc = xmltree.XMLDoc(f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:fldSimple w:instr=" PAGE "><w:r>'
                             '<w:t>1</w:t></w:r></w:fldSimple><w:r><w:fldChar w:fldCharType="end"/></w:r>'
                             '</w:p></w:body></w:document>'.encode())
        fmap = wordml.map_fields(doc)
        self.assertEqual([f.kind for f in fmap.fields], ["PAGE"])
        self.assertEqual(len(fmap.errors), 1)


# -- views ---------------------------------------------------------------------------------

class ViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = rich_docx()
        cls.doc = document(cls.blob)

    def extra_texts(self, mode):
        found = texts(self.blob, mode)
        start = next(i for i, t in enumerate(found) if t.startswith("(A) Points")) + 1
        return found[start:found.index("References")]

    def test_projections_are_well_formed_and_free_of_revisions(self):
        for mode in ("accept", "reject"):
            with self.subTest(mode):
                data = views.project(self.doc, mode)
                ET.fromstring(data)
                projected = xmltree.XMLDoc(data)
                leftovers = [e.tag for e in projected.elements
                             if e.tag in wordml.REVISION_ID_TAGS or e.tag in ("w:delText", "w:delInstrText")]
                self.assertEqual(leftovers, [])

    def test_accept_view_texts(self):
        self.assertEqual(self.extra_texts("accept"), ["Alpha beta.", "Gamma moved words delta.", "Nested added tail.",
                                                      "Formatted", "Row kept", "Row inserted"])
        self.assertIn("Prior text inserted by a co-author and  remain.", texts(self.blob, "accept"))

    def test_reject_view_texts(self):
        self.assertEqual(self.extra_texts("reject"), ["Alpha moved words beta.", "Gamma delta.", "Nested tail.",
                                                      "Formatted", "Row kept", "Row deleted"])
        self.assertIn("Prior text  and deleted words remain.", texts(self.blob, "reject"))

    def test_deleted_paragraph_mark_merges_with_the_next_paragraph_on_accept(self):
        accepted = texts(self.blob, "accept")
        rejected = texts(self.blob, "reject")
        self.assertIn("First half of a merged paragraph and its second half.", accepted)
        self.assertEqual(len(accepted), len(rejected) - 1)
        merged = views.view_doc(self.doc, "accept")
        self.assertEqual(wordml.paragraph_text(merged, paragraph(merged, "10000007")),
                         "First half of a merged paragraph and its second half.")
        with self.assertRaises(AssertionError):
            paragraph(merged, "10000006")

    def test_property_changes_restore_old_properties_on_reject(self):
        self.assertEqual(char_flags(self.blob, "accept", "20000004", "F")[0], {"bold"})
        self.assertEqual(char_flags(self.blob, "reject", "20000004", "F")[0], {"italic"})
        for mode, expected in (("accept", "center"), ("reject", "left")):
            projected = views.view_doc(self.doc, mode)
            jc = paragraph(projected, "20000004").find("w:pPr").find("w:jc")
            self.assertEqual(jc.get("w:val"), expected, mode)

    def test_table_row_revisions(self):
        for mode, expected in (("accept", ["Row kept", "Row inserted"]), ("reject", ["Row kept", "Row deleted"])):
            projected = views.view_doc(self.doc, mode)
            rows = list(projected.iter("w:tr"))
            self.assertEqual([wordml.paragraph_text(projected, next(r.iter("w:p"))) for r in rows], expected)

    def test_self_closing_paragraph_survives_a_following_mark_deletion(self):
        extra = ("<w:p/>" + fx.para("", "20000008", ppr_extra=f'<w:rPr><w:del w:id="82" {PRIOR}/></w:rPr>')
                 + fx.para(fx.run("After"), "20000009"))
        blob = fx.make_docx(extra_paragraphs=extra)
        accepted, rejected = texts(blob, "accept"), texts(blob, "reject")
        a = accepted.index("After")
        r = rejected.index("After")
        self.assertEqual(accepted[a - 1:a + 1], ["", "After"])
        self.assertEqual(rejected[r - 2:r + 1], ["", "", "After"])
        self.assertEqual(len(accepted), len(rejected) - 2)

    def test_clean_document_projects_to_identical_bytes(self):
        blob = plain_docx()
        data = docx.Package(blob).parts[MAIN]
        for mode in ("accept", "reject"):
            self.assertEqual(views.project(document(blob), mode), data)

    def test_untouched_fields_keep_their_bytes_in_both_views(self):
        source = document(fx.make_docx(multi_chunk=True))
        payloads = [source.raw(f) for f in source.iter("w:fldData")]
        for mode in ("accept", "reject"):
            projected = views.project(source, mode)
            for payload in payloads:
                self.assertIn(payload, projected)

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            views.project(self.doc, "final")


# -- fields and census -----------------------------------------------------------------------

class FieldCensusTests(unittest.TestCase):
    def test_census_of_the_fixture(self):
        data = endnote.census(document(fx.make_docx(multi_chunk=True)))
        self.assertEqual(data["citation_fields_live"], 3)
        self.assertEqual(data["citation_fields_deleted"], 0)
        self.assertEqual(data["payload_forms"], {"inline": 1, "single_chunk": 1, "multi_chunk": 1, "none": 0})
        self.assertEqual(data["payload_parse_failures"], 0)
        self.assertEqual(data["cite_occurrences"], 4)
        self.assertEqual(data["distinct_records"], 3)
        self.assertEqual(data["library_db_ids"], [fx.DB])
        self.assertEqual(data["pmids"], ["10000001", "10000002", "10000003"])
        self.assertEqual(len(data["reflist"]), 1)
        span = data["reflist"][0]
        self.assertEqual((span["paragraph_count"], span["drawings_inside"], span["headings_inside"]), (3, 0, 0))
        self.assertEqual(data["enref_bookmarks"], 3)
        self.assertEqual(data["link_count"], 3)
        self.assertEqual(data["link_targets_missing"], [])
        self.assertEqual(data["field_errors"], [])
        self.assertEqual(data["placeholders"], [])

    def test_multi_chunk_payload_decodes_to_the_joined_record_xml(self):
        doc = document(fx.make_docx(multi_chunk=True))
        cites = endnote.citation_fields(doc)
        self.assertEqual(len(cites), 3)
        group = cites[2]
        self.assertEqual(len([c for c in group.children if c.kind == "EN.CITE.DATA"]), 2)
        expected = ("<EndNote>" + fx.record(3, "Gamma", 2022, "Third fixture paper " + "x" * 400, "10000003")
                    + fx.record(1, "Alpha", 2020, "First fixture paper", "10000001") + "</EndNote>").encode()
        self.assertEqual(wordml.endnote_payload(group), expected)
        parsed = endnote.parse_cites(wordml.endnote_payload(group))
        self.assertEqual([c["recnum"] for c in parsed], ["3", "1"])
        self.assertEqual(parsed[0]["pmid"], "10000003")
        self.assertEqual(parsed[1]["doi"], "10.1000/fixture.1")
        single = endnote.parse_cites(wordml.endnote_payload(cites[1]))
        self.assertEqual((single[0]["author"], single[0]["year"], single[0]["db_id"]), ("Beta", "2021", fx.DB))
        self.assertEqual(cites[0].result, "(1)")

    def test_bibliography_entries_and_enref_targets(self):
        doc = document(fx.make_docx())
        entries = endnote.bibliography_entries(doc)
        self.assertEqual([e["number"] for e in entries], [1, 2, 3])
        self.assertEqual([e["bookmark"] for e in entries], ["_ENREF_1", "_ENREF_2", "_ENREF_3"])
        names = endnote.bookmark_names(doc)
        targets = endnote.link_targets(doc, wordml.map_fields(doc).all_fields)
        self.assertEqual(sorted(targets), ["_ENREF_1", "_ENREF_2", "_ENREF_3"])
        self.assertTrue(all(t in names for t in targets))

    def test_placeholders_are_listed(self):
        extra = fx.para(fx.run("Rose sharply [PMID: 12345678] and fell {Alpha, 2020 #1}."), "20000010")
        data = endnote.census(document(fx.make_docx(extra_paragraphs=extra)))
        self.assertEqual([p["text"] for p in data["placeholders"]], ["[PMID: 12345678]", "{Alpha, 2020 #1}"])
        self.assertEqual({p["paraId"] for p in data["placeholders"]}, {"20000010"})

    def test_runaway_bibliography_is_detected(self):
        source = fx.make_docx()
        candidate = fx.replace_document(source, runaway_bibliography)
        span = endnote.census(document(candidate))["reflist"][0]
        self.assertEqual((span["drawings_inside"], span["headings_inside"]), (1, 1))
        report = verify(source, candidate)
        self.assertIn("fields", failing(report), report.text())

    def test_unclosed_bibliography_field_is_an_error_and_blocks_builds(self):
        def unclosed(data):
            position = data.rindex(FIELD_END_RUN)
            return data[:position] + data[position + len(FIELD_END_RUN):]
        blob = fx.replace_document(fx.make_docx(), unclosed)
        self.assertEqual(len(endnote.census(document(blob))["field_errors"]), 1)
        with self.assertRaises(edit.EditError):
            run_build(blob, [{"para": "10000003", "old": "compared", "new": "contrasted"}])


# -- edit engine -----------------------------------------------------------------------------

class EditEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = fx.make_docx()

    def assert_clean_build(self, result, source=None):
        source = source or self.source
        report = verify(source, result.blob, result.manifest, author=AUTHOR)
        self.assertEqual(report.status, "PASS", report.text())
        self.assertEqual(texts(result.blob, "reject"), texts(source, "reject"))
        return report

    def test_replace_insert_and_delete(self):
        cases = [
            ({"para": "10000003", "old": "compared", "new": "contrasted"},
             "Marker levels were contrasted between groups GENE1 expression rose after training (1) and fell later.",
             "compared", "contrasted"),
            ({"op": "insert", "para": "10000003", "before": "between ", "new": "the two ", "after": "groups"},
             "Marker levels were compared between the two groups GENE1 expression rose after training (1) and fell "
             "later.", "", "the two "),
            ({"op": "delete", "para": "10000003", "before": "levels ", "old": "were ", "after": "compared"},
             "Marker levels compared between groups GENE1 expression rose after training (1) and fell later.",
             "were ", ""),
        ]
        for spec, accepted, deleted, inserted in cases:
            with self.subTest(spec.get("op", "replace")):
                result = run_build(self.source, [spec])
                self.assert_clean_build(result)
                self.assertEqual(view_paragraph_text(result.blob, "accept", "10000003"), accepted)
                record = result.manifest["edits"][0]
                self.assertEqual((record["deleted"], record["inserted"]), (deleted, inserted))

    def test_edits_inside_runs_with_rsid_attributes(self):
        result = run_build(self.source, [{"para": "10000003", "old": "compared", "new": "contrasted"}])
        data = docx.Package(result.blob).parts[MAIN]
        self.assertRegex(data, rb'<w:del w:id="\d+" ' + re.escape(NEW) + rb'><w:r w:rsidR="00A1B2C3" '
                               rb'w:rsidRPr="00D4E5F6"><w:delText xml:space="preserve">compared</w:delText>')
        self.assertIn(b'<w:r w:rsidR="00A1B2C3" w:rsidRPr="00D4E5F6"><w:t xml:space="preserve">Marker levels were '
                      b'</w:t></w:r>', data)

    def test_build_leaves_bytes_outside_the_edited_paragraph_identical(self):
        source = fx.make_docx(multi_chunk=True)
        old = docx.Package(source).parts[MAIN]
        result = run_build(source, [{"para": "10000008", "old": "ends here", "new": "ends now"}])
        new = docx.Package(result.blob).parts[MAIN]
        target = paragraph(xmltree.XMLDoc(old), "10000008")
        self.assertEqual(new[:target.start], old[:target.start])
        self.assertEqual(new[len(new) - (len(old) - target.end):], old[target.end:])
        self.assertEqual([document(result.blob).raw(f) for f in document(result.blob).iter("w:fldData")],
                         [xmltree.XMLDoc(old).raw(f) for f in xmltree.XMLDoc(old).iter("w:fldData")])
        self.assertEqual(new.count(b"\r\n"), old.count(b"\r\n"))
        source_package = docx.Package(source)
        for name in source_package.names():
            if name != MAIN:
                self.assertEqual(docx.Package(result.blob).parts[name], source_package.parts[name], name)
        self.assert_clean_build(result, source)

    def test_rewrite_with_markup_and_atoms(self):
        source = fx.make_docx(multi_chunk=True)
        before = endnote.census(document(source))
        result = run_build(source, [{"op": "rewrite", "para": "10000008",
                                     "text": "A ⟨i⟩long⟨/i⟩ group citation ⟦F1⟧ ends now."}])
        self.assert_clean_build(result, source)
        self.assertEqual(view_paragraph_text(result.blob, "accept", "10000008"), "A long group citation (3) ends now.")
        self.assertEqual(char_flags(result.blob, "accept", "10000008", "long"), [{"italic"}] * 4)
        after = endnote.census(document(result.blob))
        self.assertEqual(after["payload_forms"], before["payload_forms"])
        self.assertEqual(after["cite_occurrences"], before["cite_occurrences"])

    def test_rewrite_equal_to_the_source_changes_nothing(self):
        text = edit.extract(self.source, select=lambda r: r["paraId"] == "10000003")[0]["text"]
        result = run_build(self.source, [{"op": "rewrite", "para": "10000003", "text": text}])
        self.assertEqual(docx.Package(result.blob).parts[MAIN], docx.Package(self.source).parts[MAIN])
        self.assertEqual(result.manifest["revision_ids"], [])

    def test_rewrite_with_apply_formatting_tracks_formatting(self):
        text = edit.extract(self.source, select=lambda r: r["paraId"] == "10000003")[0]["text"]
        result = run_build(self.source, [{"op": "rewrite", "para": "10000003", "apply_formatting": True,
                                          "text": text.replace("Marker levels", "⟨b⟩Marker⟨/b⟩ levels")}])
        self.assertTrue(result.manifest["declared"]["formatting"])
        self.assertEqual(char_flags(result.blob, "accept", "10000003", "Marker levels"),
                         [{"bold"}] * 6 + [set()] * 7)
        self.assertEqual(char_flags(result.blob, "reject", "10000003", "Marker"), [set()] * 6)
        self.assertEqual(len([e for e in new_revisions(result.blob) if e.tag == "w:rPrChange"]), 1)
        self.assert_clean_build(result)

    def test_format_set_unset_and_rpr_add(self):
        result = run_build(self.source, [
            {"op": "format", "para": "10000003", "text": "levels", "set": ["bold"]},
            {"op": "format", "para": "10000003", "text": "GENE1", "unset": ["italic"]},
            {"op": "format", "para": "10000004", "text": "high", "rpr_add": '<w:color w:val="C00000"/>'},
        ])
        self.assert_clean_build(result)
        self.assertTrue(result.manifest["declared"]["formatting"])
        self.assertEqual(char_flags(result.blob, "accept", "10000003", "levels"), [{"bold"}] * 6)
        self.assertEqual(char_flags(result.blob, "reject", "10000003", "levels"), [set()] * 6)
        self.assertEqual(char_flags(result.blob, "accept", "10000003", "GENE1"), [set()] * 5)
        self.assertEqual(char_flags(result.blob, "reject", "10000003", "GENE1"), [{"italic"}] * 5)
        changes = [e for e in new_revisions(result.blob) if e.tag == "w:rPrChange"]
        self.assertEqual(len(changes), 3)
        accepted = views.project(document(result.blob), "accept")
        self.assertRegex(accepted, rb'<w:rPr><w:color w:val="C00000"/></w:rPr><w:t xml:space="preserve">high</w:t>')
        self.assertNotIn(b"C00000", views.project(document(result.blob), "reject"))

    def test_paragraph_format_add_and_remove(self):
        result = run_build(self.source, [
            {"op": "paragraph_format", "para": "10000003", "ppr_add": '<w:jc w:val="both"/>'},
            {"op": "paragraph_format", "para": "10000009", "ppr_remove": ["w:jc"]},
        ])
        self.assert_clean_build(result)
        changes = [e for e in new_revisions(result.blob) if e.tag == "w:pPrChange"]
        self.assertEqual(len(changes), 2)

        def jc(mode, pid):
            projected = views.view_doc(document(result.blob), mode)
            ppr = paragraph(projected, pid).find("w:pPr")
            found = ppr.find("w:jc") if ppr is not None else None
            return found.get("w:val") if found is not None else None
        self.assertEqual((jc("accept", "10000003"), jc("reject", "10000003")), ("both", None))
        self.assertEqual((jc("accept", "10000009"), jc("reject", "10000009")), (None, "center"))
        with self.assertRaises(edit.EditError):
            run_build(self.source, [{"op": "paragraph_format", "para": "10000003"}])

    def test_comment_into_a_document_without_comments(self):
        result = run_build(self.source, [{"op": "comment", "para": "10000003", "anchor": "GENE1",
                                          "text": "Is this the <right> gene & isoform?\nSecond line."}])
        package = docx.Package(result.blob)
        self.assertEqual(package.content_type("word/comments.xml"), COMMENTS_TYPE)
        self.assertTrue(any(r["Type"].endswith("/comments") and r["Target"] == "comments.xml"
                            for r in package.relationships(MAIN)))
        comments = package.xml("word/comments.xml")
        [comment] = list(comments.iter("w:comment"))
        self.assertEqual((comment.get("w:id"), comment.get("w:author"), comment.get("w:date")), ("0", AUTHOR, DATE))
        self.assertEqual(comment.get("w:initials"), "TR")
        self.assertEqual([wordml.paragraph_text(comments, p) for p in comments.iter("w:p")],
                         ["Is this the <right> gene & isoform?", "Second line."])
        doc = package.document
        para = paragraph(doc, "10000003")
        self.assertEqual([e.tag for e in para.elements() if e.tag.startswith("w:comment")],
                         ["w:commentRangeStart", "w:commentRangeEnd"])
        self.assertEqual(len(list(para.iter("w:commentReference"))), 1)
        self.assertEqual(result.manifest["declared"]["parts_added"], ["word/comments.xml"])
        self.assert_clean_build(result)

    def test_comment_into_a_document_with_existing_comments(self):
        source = with_comments(self.source)
        self.assertEqual(verify(source, source).status, "PASS")
        result = run_build(source, [{"op": "comment", "para": "10000004", "anchor": "high", "text": "Why high?",
                                     "author": "Second Reader", "initials": "SR"}])
        comments = docx.Package(result.blob).xml("word/comments.xml")
        found = [(c.get("w:id"), c.get("w:author"), c.get("w:initials")) for c in comments.iter("w:comment")]
        self.assertEqual(found, [("0", fx.AUTHOR_OTHER, "CA"), ("1", "Second Reader", "SR")])
        self.assertIn(b"Existing note", docx.Package(result.blob).parts["word/comments.xml"])
        self.assertEqual(result.manifest["declared"]["parts_changed"], ["word/comments.xml"])
        self.assertEqual(result.manifest["declared"]["parts_added"], [])
        self.assert_clean_build(result, source)

    def test_insert_paragraph(self):
        result = run_build(self.source, [{"op": "insert_paragraph", "after": "10000004",
                                          "text": "A new ⟨i⟩GENE2⟨/i⟩ paragraph."}])
        self.assert_clean_build(result)
        accepted = texts(result.blob, "accept")
        self.assertEqual(accepted[accepted.index("The unit was 31 kg/m2, which is high (2) in this cohort.") + 1],
                         "A new GENE2 paragraph.")
        self.assertEqual(len(accepted), len(texts(self.source, "accept")) + 1)
        doc = document(result.blob)
        self.assertEqual(wordml.mark_revision(paragraph(doc, "10000004")), "ins")
        with self.assertRaises(edit.EditError):
            run_build(self.source, [{"op": "insert_paragraph", "after": "10000003", "text": "Cites ⟦F1⟧ again."}])

    def test_delete_paragraph(self):
        result = run_build(self.source, [{"op": "delete_paragraph", "para": "10000002"}])
        self.assert_clean_build(result)
        self.assertNotIn("Abstract", texts(result.blob, "accept"))
        self.assertEqual(wordml.mark_revision(paragraph(document(result.blob), "10000002")), "del")
        with self.assertRaises(edit.EditError):
            run_build(self.source, [{"op": "delete_paragraph", "para": "10000003"}])
        allowed = run_build(self.source, [{"op": "delete_paragraph", "para": "10000003", "allow_fields": True}])
        self.assertTrue(allowed.manifest["declared"]["citations"])
        report = verify(self.source, allowed.blob, allowed.manifest)
        self.assertEqual(report.status, "PASS", report.text())
        self.assertEqual(statuses(report)["citations"], "WARN")

    def test_swap_picture_with_a_new_extension(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "Figure 1 revised.jpg"
            image.write_bytes(jpeg(60, 30))
            result = run_build(self.source, [{"op": "swap_picture", "old_name": "Figure 1", "new_name": "Figure 1 v2",
                                              "image": str(image), "width_mm": 100, "descr": "Revised panel"}])
        package = docx.Package(result.blob)
        media = "word/media/msw_image1.jpg"
        self.assertEqual(package.parts[media], jpeg(60, 30))
        self.assertEqual(package.content_type(media), "image/jpeg")
        self.assertTrue(any(r["Target"] == "media/msw_image1.jpg" for r in package.relationships(MAIN)))
        info = zipfile.ZipFile(io.BytesIO(result.blob)).getinfo(media)
        self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
        [swap] = result.manifest["declared"]["pictures"]
        self.assertEqual((swap["new_emu"], swap["pixels"]), ([3600000, 1800000], [60, 30]))
        for mode, name in (("accept", "Figure 1 v2"), ("reject", "Figure 1")):
            projected = views.view_doc(package.document, mode)
            self.assertEqual([e.get("name") for e in projected.iter("wp:docPr")], [name])
        ids = [e.get("id") for e in package.document.iter("wp:docPr")]
        self.assertEqual(len(ids), len(set(ids)))
        self.assert_clean_build(result)
        without = verify(self.source, result.blob)
        self.assertNotEqual(statuses(without)["pictures"], "PASS")
        undeclared = json.loads(json.dumps(result.manifest))
        undeclared["declared"]["pictures"] = []
        self.assertIn("pictures", failing(verify(self.source, result.blob, undeclared)))

    def test_swap_picture_refusals(self):
        with tempfile.TemporaryDirectory() as temporary:
            text_file = Path(temporary) / "figure.txt"
            text_file.write_bytes(b"not an image")
            image = Path(temporary) / "figure.png"
            image.write_bytes(fx.png())
            for spec in ({"old_name": "Figure 1", "image": str(text_file)},
                         {"old_name": "Figure 9", "image": str(image)},
                         {"old_name": "Figure 1", "image": str(image), "media": "media/image1.png"},
                         {"old_name": "Figure 1", "image": str(image), "media": "IMAGE1.png"}):
                with self.subTest(spec), self.assertRaises(edit.EditError):
                    run_build(self.source, [dict(spec, op="swap_picture")])

    def test_nested_edit_keeps_the_prior_author_on_continuation_wrappers(self):
        result = run_build(self.source, [{"para": "10000005", "old": "by a", "new": "by one"}])
        self.assert_clean_build(result)
        self.assertEqual(view_paragraph_text(result.blob, "accept", "10000005"),
                         "Prior text inserted by one co-author and  remain.")
        para = paragraph(document(result.blob), "10000005")
        insertions = [e for e in para.elements() if e.tag == "w:ins"]
        prior = [e for e in insertions if e.get("w:author") == fx.AUTHOR_OTHER]
        self.assertGreaterEqual(len(prior), 2)
        self.assertEqual(prior[0].get("w:id"), "1")
        self.assertTrue(all(e.get("w:date") == "2026-01-01T00:00:00Z" for e in prior))
        nested = [d for e in prior for d in e.iter("w:del")]
        self.assertEqual([(d.get("w:author"), d.get("w:date")) for d in nested], [(AUTHOR, DATE)])
        ours = [e for e in insertions if e.get("w:author") == AUTHOR]
        self.assertEqual(len(ours), 1)
        self.assertEqual(verify(self.source, result.blob, result.manifest, author=AUTHOR).status, "PASS")

    def test_source_without_revisions_pictures_or_bookmarks(self):
        source = plain_docx()
        self.assertEqual(edit.existing_max_id(docx.Package(source)), 0)
        result = run_build(source, [
            {"para": "30000001", "old": "first", "new": "opening"},
            {"op": "comment", "para": "30000002", "anchor": "Second", "text": "Keep?"},
            {"op": "format", "para": "30000002", "text": "paragraph", "set": ["italic"]},
            {"op": "insert_paragraph", "after": "30000002", "text": "Third paragraph."},
        ])
        self.assert_clean_build(result, source)
        self.assertEqual(texts(result.blob, "accept"),
                         ["Plain opening paragraph here.", "Second paragraph.", "Third paragraph."])
        self.assertEqual(result.manifest["revision_ids"][0], 10001)

    def test_refusals(self):
        rewrite = "Marker levels were compared between groups ⟨i⟩GENE1⟨/i⟩ expression rose after training"
        cases = {
            "edit inside the bibliography (D1)": [{"para": {"contains": "Alpha A. First"}, "old": "First",
                                                   "new": "1st"}],
            "format inside the bibliography": [{"op": "format", "para": "1000000D", "text": "Beta B.",
                                                "set": ["bold"]}],
            "displayed citation number": [{"para": "10000003", "old": "(1)", "new": "(4)"}],
            "citation moved across an atom": [{"op": "rewrite", "para": "10000003",
                                               "text": rewrite + " and fell later ⟦F1⟧."}],
            "citation dropped": [{"op": "rewrite", "para": "10000003", "text": rewrite + " and fell later."}],
            "citation duplicated": [{"op": "rewrite", "para": "10000003",
                                     "text": rewrite + " ⟦F1⟧ and fell later ⟦F1⟧."}],
            "stale source hash": [{"op": "rewrite", "para": "10000003", "source_hash": "0000000000000000",
                                   "text": rewrite + " ⟦F1⟧ and fell sharply."}],
            "overlapping edits": [{"para": "10000003", "old": "levels were", "new": "values are"},
                                  {"para": "10000003", "old": "were compared", "new": "was contrasted"}],
            "ambiguous context": [{"para": "10000003", "old": "e", "new": "x"}],
            "missing context": [{"para": "10000003", "old": "absent words", "new": "x"}],
            "same text": [{"para": "10000003", "old": "compared", "new": "compared"}],
            "unknown paragraph": [{"para": "99999999", "old": "a", "new": "b"}],
            "unknown op": [{"op": "transmogrify", "para": "10000003"}],
            "no edits": [],
        }
        for label, edits in cases.items():
            with self.subTest(label), self.assertRaises(edit.EditError):
                run_build(self.source, edits)
        for author in ("", "   "):
            with self.subTest(author=author), self.assertRaises(edit.EditError):
                run_build(self.source, [{"para": "10000003", "old": "compared", "new": "contrasted"}], author=author)

    def test_allow_reattach_is_an_explicit_override(self):
        rewrite = ("Marker levels were compared between groups ⟨i⟩GENE1⟨/i⟩ expression rose after training"
                   " and fell later ⟦F1⟧.")
        result = run_build(self.source, [{"op": "rewrite", "para": "10000003", "text": rewrite,
                                          "allow_reattach": True}])
        self.assertEqual(view_paragraph_text(result.blob, "accept", "10000003"),
                         "Marker levels were compared between groups GENE1 expression rose after training and fell "
                         "later (1).")

    def test_superscript_is_preserved_P1(self):
        for spec in ({"op": "rewrite", "para": "10000004",
                      "text": "The unit was 31 kg/m⟨sup⟩2⟨/sup⟩ which is high ⟦F1⟧ in this cohort."},
                     {"para": "10000004", "old": "kg/m2,", "new": "kg/m2"},
                     {"para": "10000004", "old": "kg/m2", "new": "kg/cm⟨sup⟩2⟨/sup⟩"}):
            with self.subTest(spec):
                result = run_build(self.source, [spec])
                self.assert_clean_build(result)
                accepted = view_paragraph_text(result.blob, "accept", "10000004")
                digit = accepted.index("2")
                self.assertEqual(char_flags(result.blob, "accept", "10000004", accepted[digit - 1:digit + 1])[1],
                                 {"superscript"})

    def test_plain_replace_keeps_superscript_of_shared_characters(self):
        # "kg/m2" (m plain, 2 superscript) -> "kg/cm2" inserts only "c", so the superscript 2 is kept
        result = run_build(self.source, [{"para": "10000004", "old": "kg/m2", "new": "kg/cm2"}])
        self.assert_clean_build(result)
        self.assertEqual([r["inserted"] for r in result.manifest["edits"]], ["c"])
        accepted = view_paragraph_text(result.blob, "accept", "10000004")
        digit = accepted.index("cm2") + 2
        self.assertEqual(char_flags(result.blob, "accept", "10000004", accepted[digit - 1:digit + 1])[1],
                         {"superscript"})

    def test_italic_does_not_leak_into_inserted_text_P2(self):
        result = run_build(self.source, [{"op": "insert", "para": "10000003", "before": "GENE1", "new": " mRNA",
                                          "after": " expression"}])
        self.assert_clean_build(result)
        self.assertEqual(char_flags(result.blob, "accept", "10000003", "GENE1 mRNA expression"),
                         [{"italic"}] * 5 + [set()] * 16)
        renamed = run_build(self.source, [{"para": "10000003", "old": "GENE1", "new": "GENE2"}])
        self.assertEqual(char_flags(renamed.blob, "accept", "10000003", "GENE2"), [{"italic"}] * 5)

    def test_rprchange_ids_are_not_duplicated_P4(self):
        extra = fx.para(f'<w:r w:rsidR="00ABCDEF"><w:rPr><w:b/><w:rPrChange w:id="42" {PRIOR}><w:rPr/>'
                        '</w:rPrChange></w:rPr><w:t>alpha beta gamma</w:t></w:r>', "20000011")
        source = fx.make_docx(extra_paragraphs=extra)
        result = run_build(source, [{"para": "20000011", "old": "beta ", "new": ""}])
        changes = [e.get("w:id") for e in document(result.blob).iter("w:rPrChange")]
        self.assertEqual(len(changes), 3)
        self.assertEqual(len(set(changes)), 3)
        self.assertIn("42", changes)
        report = verify(source, result.blob, result.manifest)
        self.assertEqual(statuses(report)["revision_ids"], "PASS")
        self.assertEqual(report.status, "PASS", report.text())

    def test_every_emitted_text_element_preserves_space(self):
        source_data = docx.Package(self.source).parts[MAIN]
        result = run_build(self.source, [
            {"para": "10000003", "old": "compared", "new": "contrasted"},
            {"op": "insert", "para": "10000004", "before": "which is", "new": " very", "after": " high"},
            {"op": "delete", "para": "10000004", "before": "in this", "old": " cohort", "after": "."},
            {"op": "insert_paragraph", "after": "10000008", "text": " Leading and trailing spaces "},
            {"op": "comment", "para": "10000003", "anchor": "levels", "text": " spaced note "},
        ])
        doc = document(result.blob)
        emitted = [e for e in doc.elements if e.tag in TEXT_TAGS and doc.raw(e) not in source_data]
        self.assertGreater(len(emitted), 8)
        self.assertEqual([doc.raw(e) for e in emitted if e.get("xml:space") != "preserve"], [])
        comments = docx.Package(result.blob).xml("word/comments.xml")
        self.assertTrue(all(t.get("xml:space") == "preserve" for t in comments.iter("w:t")))
        report = self.assert_clean_build(result)
        self.assertEqual(statuses(report)["whitespace"], "PASS")

    def test_revision_ids_start_above_the_existing_maximum(self):
        package = docx.Package(self.source)
        self.assertEqual(edit.existing_max_id(package), 102)
        self.assertEqual(edit.default_id_base(package), 10000)
        result = run_build(self.source, [{"para": "10000003", "old": "compared", "new": "contrasted"},
                                         {"op": "insert_paragraph", "after": "10000004", "text": "New."}])
        used = [int(e.get("w:id")) for e in new_revisions(result.blob)]
        self.assertTrue(used and min(used) > 10000)
        self.assertEqual(sorted(used), list(range(result.manifest["revision_ids"][0],
                                                  result.manifest["revision_ids"][1] + 1)))
        high = fx.replace_document(self.source, lambda d: d.replace(b'w:id="102"', b'w:id="12345"'))
        self.assertEqual(edit.existing_max_id(docx.Package(high)), 12345)
        self.assertEqual(run_build(high, [{"para": "10000003", "old": "compared", "new": "contrasted"}])
                         .manifest["revision_ids"][0], 20001)
        explicit = run_build(self.source, [{"para": "10000003", "old": "compared", "new": "contrasted"}], id_base=500)
        self.assertEqual(explicit.manifest["revision_ids"], [501, 502])

    def test_colliding_id_base_is_refused(self):
        with self.assertRaisesRegex(edit.EditError, "below the largest existing"):
            run_build(self.source, [{"para": "10000003", "old": "compared", "new": "contrasted"}], id_base=0)

    def test_one_date_per_build(self):
        result = edit.build(self.source, {"edits": [
            {"para": "10000003", "old": "compared", "new": "contrasted"},
            {"op": "format", "para": "10000004", "text": "high", "set": ["bold"]},
            {"op": "insert_paragraph", "after": "10000004", "text": "New."},
            {"op": "comment", "para": "10000004", "anchor": "cohort", "text": "Check."},
        ]}, author=AUTHOR)
        date = result.manifest["date"]
        self.assertRegex(date, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        package = docx.Package(result.blob)
        dates = {e.get("w:date") for e in package.document.elements
                 if e.tag in REVISION_TAGS and e.get("w:author") == AUTHOR}
        dates |= {e.get("w:date") for e in package.xml("word/comments.xml").iter("w:comment")}
        self.assertEqual(dates, {date})

    def test_manifest_records_the_build(self):
        result = run_build(self.source, [{"id": "E1", "para": "10000003", "old": "compared", "new": "contrasted",
                                          "purpose": "word choice"}],
                           intended=[{"old": "were compared", "new": "were contrasted"}])
        manifest = result.manifest
        self.assertEqual(manifest["schema"], "msw-build/1")
        self.assertEqual(manifest["source"]["sha256"], docx.Package(self.source).sha256)
        self.assertEqual(manifest["output_sha256"], docx.sha256_bytes(result.blob))
        self.assertEqual((manifest["author"], manifest["date"]), (AUTHOR, DATE))
        self.assertEqual(manifest["intended"], [{"old": "were compared", "new": "were contrasted"}])
        [record] = manifest["edits"]
        self.assertEqual((record["id"], record["paraId"], record["purpose"]), ("E1", "10000003", "word choice"))
        [expected] = manifest["expected_accept"]
        self.assertEqual(expected["text"], view_paragraph_text(result.blob, "accept", "10000003"))
        json.dumps(manifest)

    def test_track_revisions_setting(self):
        off = run_build(self.source, [{"para": "10000003", "old": "compared", "new": "contrasted"}],
                        track_revisions=False)
        settings = docx.Package(off.blob).parts["word/settings.xml"]
        self.assertNotIn(b"trackRevisions", settings)
        self.assertEqual(off.manifest["declared"]["parts_changed"], ["word/settings.xml"])
        self.assertEqual(verify(self.source, off.blob, off.manifest).status, "PASS")
        kept = run_build(self.source, [{"para": "10000003", "old": "compared", "new": "contrasted"}],
                         track_revisions=True)
        self.assertEqual(docx.Package(kept.blob).parts["word/settings.xml"],
                         docx.Package(self.source).parts["word/settings.xml"])

    def test_extract_records_marked_text_atoms_and_hashes(self):
        records = {r["paraId"]: r for r in edit.extract(self.source)}
        record = records["10000003"]
        self.assertEqual(record["text"], "Marker levels were compared between groups ⟨i⟩GENE1⟨/i⟩ expression rose "
                                         "after training ⟦F1⟧ and fell later.")
        self.assertEqual(record["atoms"], [{"marker": "⟦F1⟧", "text": "(1)"}])
        self.assertRegex(record["source_hash"], r"^[0-9a-f]{16}$")
        self.assertEqual(records["10000009"]["text"], "⟦D1⟧")
        self.assertEqual((records["1000000C"]["text"], records["1000000C"]["editable_chars"]), ("⟦F1⟧", 0))
        self.assertEqual(records["10000004"]["text"],
                         "The unit was 31 kg/m⟨sup⟩2⟨/sup⟩, which is high ⟦F1⟧ in this cohort.")
        result = run_build(self.source, [{"op": "rewrite", "para": "10000003", "source_hash": record["source_hash"],
                                          "text": record["text"].replace("compared", "contrasted")}])
        self.assert_clean_build(result)

    def test_namespace_prefix_must_be_w(self):
        xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<x:document xmlns:x="{W_NS}"><x:body><x:p><x:r><x:t>Text</x:t></x:r></x:p></x:body></x:document>')
        with self.assertRaises(xmltree.XMLError):
            run_build(fx.make_docx(document=xml.encode()), [{"para": {"index": 0}, "old": "Text", "new": "Word"}])

    def test_two_paragraphs_inserted_after_one_anchor(self):
        """Regression (fixed): a second insert_paragraph after the same anchor splices a second <w:pPr> into the
        anchor (both carrying a mark insertion) and leaves the first new paragraph's mark untracked, so the
        reject view gains an empty paragraph. Refusing the spec is also an acceptable fix."""
        try:
            result = run_build(self.source, [{"op": "insert_paragraph", "after": "10000004", "text": "First new."},
                                             {"op": "insert_paragraph", "after": "10000004", "text": "Second new."}])
        except edit.EditError:
            return
        anchor = paragraph(document(result.blob), "10000004")
        self.assertEqual(len(anchor.findall("w:pPr")), 1)
        self.assert_clean_build(result)

    def test_paragraph_inserted_inside_the_bibliography_is_refused(self):
        """Regression (fixed, D1 for paragraphs): insert_paragraph after a paragraph inside the EN.REFLIST field puts
        a new paragraph inside the bibliography, which EndNote overwrites on the next refresh; build accepts it
        and the gate passes it."""
        try:
            result = run_build(self.source, [{"op": "insert_paragraph", "after": "1000000C",
                                              "text": "Injected entry."}])
        except edit.EditError:
            return
        self.assertEqual(verify(self.source, result.blob, result.manifest).status, "FAIL")


# -- gate ------------------------------------------------------------------------------------

class GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = fx.make_docx()
        cls.data = docx.Package(cls.source).parts[MAIN]

    def corrupt(self, transform):
        return fx.replace_document(self.source, transform)

    def assert_fails(self, candidate, *checks, manifest=None):
        report = verify(self.source, candidate, manifest)
        self.assertEqual(report.status, "FAIL", report.text())
        for check in checks:
            self.assertIn(check, failing(report), report.text())
        return report

    def test_identical_documents_pass(self):
        for source in (self.source, fx.make_docx(multi_chunk=True), plain_docx()):
            report = verify(source, source)
            self.assertEqual(report.status, "PASS", report.text())

    def test_legitimate_build_passes_with_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "new.png"
            image.write_bytes(fx.png(80, 40, (20, 90, 200)))
            result = run_build(self.source, [
                {"para": "10000003", "old": "compared", "new": "contrasted"},
                {"op": "rewrite", "para": "10000004",
                 "text": "The unit was 31 kg/m⟨sup⟩2⟨/sup⟩, which is elevated ⟦F1⟧ in this cohort."},
                {"para": "10000005", "old": "by a", "new": "by one"},
                {"op": "comment", "para": "10000008", "anchor": "group citation", "text": "Split?"},
                {"op": "insert_paragraph", "after": "1000000A", "text": "(B) A second ⟨i⟩panel⟨/i⟩ legend."},
                {"op": "delete_paragraph", "para": "10000002"},
                {"op": "swap_picture", "old_name": "Figure 1", "new_name": "Figure 1 v2", "image": str(image)},
            ])
        report = verify(self.source, result.blob, result.manifest, author=AUTHOR)
        self.assertEqual(report.status, "PASS", report.text())
        for name in ("package", "revision_markup", "revision_ids", "bookmarks", "fields", "untracked_changes",
                     "accepted_text", "citations", "links", "pictures", "formatting", "whitespace"):
            self.assertEqual(statuses(report)[name], "PASS", name)
        data = report.to_dict()
        self.assertEqual(data["status"], "PASS")
        self.assertEqual(data["summary"], report.summary_line())
        self.assertTrue(report.text().startswith("GATE PASS"))
        json.dumps(data)

    def test_C1_citation_tracked_deleted(self):
        def transform(data):
            start, end = citation_run_span(data, 0)
            return (data[:start] + b'<w:del w:id="900" ' + NEW + b">" + as_deleted(data[start:end])
                    + b"</w:del>" + data[end:])
        self.assert_fails(self.corrupt(transform), "citations", "new_revisions")

    def test_C2_citation_moved_untracked(self):
        def transform(data):
            start, end = citation_run_span(data, 0)
            citation, rest = data[start:end], data[:start] + data[end:]
            anchor = rest.index(b" and fell later.</w:t></w:r>") + len(b" and fell later.</w:t></w:r>")
            return rest[:anchor] + citation + rest[anchor:]
        self.assert_fails(self.corrupt(transform), "untracked_changes")

    def test_citation_moved_as_tracked_changes(self):
        def transform(data):
            start, end = citation_run_span(data, 0)
            citation = data[start:end]
            anchor = data.index(b" and fell later.</w:t></w:r>") + len(b" and fell later.</w:t></w:r>")
            return (data[:start] + b'<w:del w:id="900" ' + NEW + b">" + as_deleted(citation) + b"</w:del>"
                    + data[end:anchor] + b'<w:ins w:id="901" ' + NEW + b">" + citation + b"</w:ins>" + data[anchor:])
        self.assert_fails(self.corrupt(transform), "new_revisions")

    def test_C3_unescaped_ampersand(self):
        inserted = b'<w:ins w:id="900" ' + NEW + b'><w:r><w:t xml:space="preserve">R&D </w:t></w:r></w:ins>'
        self.assert_fails(self.corrupt(swap_once(MARKER_RUN, inserted + MARKER_RUN)), "package")

    def test_C4_duplicate_revision_ids_in_any_attribute_order(self):
        cases = {
            "reuses a deletion id": b'w:id="2" ' + NEW,
            "reuses the paragraph-mark deletion id": b'w:id="3" ' + NEW,
            "author before id": f'w:author="{AUTHOR}" w:id="1" w:date="{DATE}"'.encode(),
            "date first": f'w:date="{DATE}" w:author="{AUTHOR}" w:id="2"'.encode(),
        }
        for label, attrs in cases.items():
            with self.subTest(label):
                inserted = b"<w:ins " + attrs + b'><w:r><w:t xml:space="preserve">new </w:t></w:r></w:ins>'
                self.assert_fails(self.corrupt(swap_once(MARKER_RUN, inserted + MARKER_RUN)), "revision_ids")

    def test_C5_displayed_citation_number_changed(self):
        self.assert_fails(self.corrupt(swap_once(b"<w:r><w:t>1</w:t></w:r></w:hyperlink>",
                                                 b"<w:r><w:t>4</w:t></w:r></w:hyperlink>")),
                          "citations", "untracked_changes")

    def test_C6_field_code_edited(self):
        self.assert_fails(self.corrupt(swap_once(b"&lt;title&gt;First fixture paper&lt;",
                                                 b"&lt;title&gt;First fixture papers&lt;")), "citations")
        multi = fx.make_docx(multi_chunk=True)
        for position in (1, 3, 4):   # nested EN.CITE.DATA chunks (0 and 2 are the outer copies)
            with self.subTest(chunk=position):
                report = verify(multi, fx.replace_document(multi, edit_fld_data(position)))
                self.assertIn("citations", failing(report), report.text())

    def test_base64_line_endings_are_compared_semantically(self):
        # Design choice (core_docx_tools 4.3): the gate compares decoded payloads, so LF-only base64
        # is not a citation change; the edit engine itself never rewrites these bytes.
        multi = fx.make_docx(multi_chunk=True)

        def rewrap(data):
            doc = xmltree.XMLDoc(data)
            splices = [(c.open_end, c.close_start, doc.inner(c).replace(b"\r\n", b"\n")) for c in doc.iter("w:fldData")]
            return xmltree.apply_splices(data, splices)
        candidate = fx.replace_document(multi, rewrap)
        self.assertLess(docx.Package(candidate).parts[MAIN].count(b"\r\n"), 2)
        self.assertEqual(verify(multi, candidate).status, "PASS")

    def test_change_to_the_outer_fld_data_copy_fails(self):
        """Regression (fixed): the citation signature decodes only the nested EN.CITE.DATA chunks when they exist,
        so a change confined to the outer field's fldData copy (a field-code change) passes the gate."""
        multi = fx.make_docx(multi_chunk=True)
        for position in (0, 2):
            report = verify(multi, fx.replace_document(multi, edit_fld_data(position)))
            self.assertEqual(report.status, "FAIL", report.text())

    def test_C7_enref_bookmarks_stripped(self):
        stripped = self.corrupt(lambda d: re.sub(rb'<w:bookmark(?:Start|End) w:id="10[012]"(?: w:name="_ENREF_\d")?/>',
                                                 b"", d))
        self.assertEqual(endnote.census(document(stripped))["enref_bookmarks"], 0)
        self.assert_fails(stripped, "links")

    def test_C8_stray_and_missing_parts(self):
        cases = {
            "stale backup packed": dict(add=[("word/document.xml.bak", self.data)]),
            "Word lock file packed": dict(add=[("word/~$cument.xml", b"x")]),
            "styles removed": dict(drop=["word/styles.xml"]),
            "undeclared new part": dict(add=[("word/extra.xml", b"<a/>")]),
            "protected part changed": dict(change={"word/styles.xml": swap_once(b'w:val="32"', b'w:val="36"')}),
            "content types broken": dict(change={"[Content_Types].xml": lambda d: d.replace(b"</Types>", b"<Types>")}),
        }
        for label, options in cases.items():
            with self.subTest(label):
                self.assert_fails(rezip(self.source, **options), "package")

    def test_C9_superscript_lost_on_unchanged_text(self):
        replacement = (b'<w:del w:id="900" ' + NEW + b'><w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr>'
                       b'<w:delText>2</w:delText></w:r></w:del><w:ins w:id="901" ' + NEW + b"><w:r><w:t>2</w:t>"
                       b"</w:r></w:ins>")
        self.assert_fails(self.corrupt(swap_once(SUPERSCRIPT_RUN, replacement)), "formatting")
        untracked = SUPERSCRIPT_RUN.replace(b'<w:rPr><w:vertAlign w:val="superscript"/></w:rPr>', b"")
        self.assert_fails(self.corrupt(swap_once(SUPERSCRIPT_RUN, untracked)), "formatting")

    def test_missing_space_preserve_on_an_edge_space_insertion(self):
        inserted = b'<w:ins w:id="900" ' + NEW + b"><w:r><w:t> two</w:t></w:r></w:ins>"
        self.assert_fails(self.corrupt(swap_once(MARKER_RUN, inserted + MARKER_RUN)), "whitespace")
        preserved = inserted.replace(b"<w:t>", b'<w:t xml:space="preserve">')
        self.assertEqual(verify(self.source, self.corrupt(swap_once(MARKER_RUN, preserved + MARKER_RUN))).status,
                         "PASS")

    def test_bookmark_damage(self):
        cases = {
            "duplicate bookmark id": b'<w:bookmarkStart w:id="100" w:name="_Other"/><w:bookmarkEnd w:id="100"/>',
            "duplicate bookmark name": b'<w:bookmarkStart w:id="300" w:name="_ENREF_1"/><w:bookmarkEnd w:id="300"/>',
            "orphan bookmark start": b'<w:bookmarkStart w:id="301" w:name="_Orphan"/>',
            "orphan bookmark end": b'<w:bookmarkEnd w:id="302"/>',
        }
        for label, markup in cases.items():
            with self.subTest(label):
                self.assert_fails(self.corrupt(swap_once(MARKER_RUN, markup + MARKER_RUN)), "bookmarks")

    def test_comment_markup_damage(self):
        cases = {
            "reference without comment": b'<w:r><w:commentReference w:id="7"/></w:r>',
            "unpaired comment range": b'<w:commentRangeStart w:id="8"/>',
        }
        for label, markup in cases.items():
            with self.subTest(label):
                self.assert_fails(self.corrupt(swap_once(MARKER_RUN, markup + MARKER_RUN)), "revision_ids")

    def test_untracked_text_change(self):
        self.assert_fails(self.corrupt(swap_once(b"were compared", b"were contrasted")), "untracked_changes")
        self.assert_fails(self.corrupt(swap_once(b"<w:tab/><w:t>Beta B.", b"<w:tab/><w:t>Beta X.")),
                          "untracked_changes")

    def test_revision_markup_errors(self):
        cases = {
            "w:t inside a deletion": b'<w:del w:id="900" ' + NEW + b'><w:r><w:t xml:space="preserve">x </w:t>'
                                     b"</w:r></w:del>",
            "delText outside a deletion": b'<w:r><w:delText xml:space="preserve">x </w:delText></w:r>',
            "insertion nested in an insertion": b'<w:ins w:id="900" ' + NEW + b'><w:ins w:id="901" ' + NEW
                                                + b'><w:r><w:t xml:space="preserve">x </w:t></w:r></w:ins></w:ins>',
        }
        for label, markup in cases.items():
            with self.subTest(label):
                self.assert_fails(self.corrupt(swap_once(MARKER_RUN, markup + MARKER_RUN)), "revision_markup")

    def test_truncated_document(self):
        report = self.assert_fails(self.corrupt(lambda d: d[:-30]), "package", "termination")
        self.assertGreater(len(failing(report)), 3)

    def test_formatting_only_change_undeclared(self):
        tracked_loss = GENE_RUN.replace(b"<w:rPr><w:i/></w:rPr>", b'<w:rPr><w:rPrChange w:id="900" ' + NEW
                                        + b"><w:rPr><w:i/></w:rPr></w:rPrChange></w:rPr>")
        candidate = self.corrupt(swap_once(GENE_RUN, tracked_loss))
        report = self.assert_fails(candidate, "formatting")
        self.assertEqual(statuses(report)["new_revisions"], "WARN")
        declared = verify(self.source, candidate, {"declared": {"formatting": True}})
        self.assertEqual(declared.status, "PASS", declared.text())
        self.assertEqual(statuses(declared)["formatting"], "WARN")
        tracked_gain = MARKER_RUN.replace(b"<w:t ", b'<w:rPr><w:b/><w:rPrChange w:id="900" ' + NEW
                                          + b"><w:rPr/></w:rPrChange></w:rPr><w:t ")
        gain = verify(self.source, self.corrupt(swap_once(MARKER_RUN, tracked_gain)))
        self.assertIn(statuses(gain)["formatting"], ("WARN", "FAIL"), gain.text())
        self.assertIn(statuses(gain)["new_revisions"], ("WARN", "FAIL"), gain.text())

    def test_untracked_formatting_addition_fails(self):
        """Regression (fixed): the gate compares only text in the reject view, so bold added to unchanged text
        without a w:rPrChange (an untracked change the author cannot see or reject in Word) only WARNs."""
        untracked_gain = MARKER_RUN.replace(b"<w:t ", b"<w:rPr><w:b/></w:rPr><w:t ")
        self.assert_fails(self.corrupt(swap_once(MARKER_RUN, untracked_gain)))

    def test_self_closing_paragraph_mark_revisions_do_not_cause_false_failures(self):
        self.assertRegex(self.data, rb'<w:rPr><w:del w:id="3" [^>]*/></w:rPr>')
        result = run_build(self.source, [{"op": "insert_paragraph", "after": "10000004", "text": "Inserted."},
                                         {"op": "delete_paragraph", "para": "10000002"}])
        marks = [e for e in document(result.blob).elements if e.tag in ("w:ins", "w:del") and e.self_closing]
        self.assertGreaterEqual(len(marks), 3)
        report = verify(self.source, result.blob, result.manifest)
        self.assertEqual(report.status, "PASS", report.text())
        self.assertEqual(statuses(report)["minimality"], "PASS")

    def test_manifest_declarations_are_enforced(self):
        first = run_build(self.source, [{"para": "10000003", "old": "compared", "new": "contrasted"}])
        both = run_build(self.source, [{"para": "10000003", "old": "compared", "new": "contrasted"},
                                       {"para": "10000004", "old": "high", "new": "elevated"}])
        self.assertIn("accepted_text", failing(verify(self.source, both.blob, first.manifest)))
        tampered = json.loads(json.dumps(first.manifest))
        tampered["expected_accept"][0]["text"] = tampered["expected_accept"][0]["text"].replace("contrasted", "x")
        self.assertIn("accepted_text", failing(verify(self.source, first.blob, tampered)))
        good = {"intended": [{"old": "were compared", "new": "were contrasted"}]}
        self.assertEqual(statuses(verify(self.source, first.blob, good))["accepted_text"], "PASS")
        wrong = {"intended": [{"old": "were compared", "new": "were matched"}]}
        self.assertIn("accepted_text", failing(verify(self.source, first.blob, wrong)))
        self.assertEqual(statuses(verify(self.source, first.blob))["accepted_text"], "INFO")

    def test_new_revisions_must_carry_the_expected_author(self):
        inserted = (b'<w:ins w:id="900" w:author="Someone Else" w:date="' + DATE.encode()
                    + b'"><w:r><w:t xml:space="preserve">new </w:t></w:r></w:ins>')
        candidate = self.corrupt(swap_once(MARKER_RUN, inserted + MARKER_RUN))
        self.assertIn("new_revisions", failing(verify(self.source, candidate, author=AUTHOR)))
        self.assertEqual(verify(self.source, candidate, author="Someone Else").status, "PASS")

    def test_word_lock_file_beside_the_candidate_warns(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "source.docx").write_bytes(self.source)
            (folder / "candidate.docx").write_bytes(self.source)
            (folder / "~$ndidate.docx").write_bytes(b"x")
            report = gate.verify(folder / "source.docx", folder / "candidate.docx")
        self.assertEqual(statuses(report)["word_locks"], "WARN")
        self.assertEqual(report.status, "PASS")

    def test_moved_text_written_by_word_passes_the_gate(self):
        """Regression (fixed): Word writes moved-from runs with w:t (w:delText is used only for deleted text that
        is not a move), but check_revision_markup reports w:t inside w:moveFrom as an error, so
        verify(X, X) fails for any document that carries a tracked move."""
        source = moves_docx()
        report = verify(source, source)
        self.assertEqual(report.status, "PASS", report.text())


# -- textdiff --------------------------------------------------------------------------------

class TextDiffTests(unittest.TestCase):
    def changes(self, old, new, **options):
        return [(old[c.old_start:c.old_end], new[c.new_start:c.new_end])
                for c in textdiff.diff(old, new, **options)]

    def test_atoms_are_enforced(self):
        cases = [("a ⟦F1⟧ b", "a b"), ("⟦F1⟧ x ⟦F2⟧", "⟦F2⟧ x ⟦F1⟧"), ("a b", "a ⟦F1⟧ b"),
                 ("a ⟦F1⟧", "a ⟦F1⟧ ⟦F1⟧"), ("a ⟦F1⟧", "a ⟦D1⟧")]
        for old, new in cases:
            with self.subTest(old=old, new=new), self.assertRaises(textdiff.AtomError):
                textdiff.diff(old, new)

    def test_changes_stay_between_atoms(self):
        self.assertEqual(self.changes("A ⟦F1⟧ b c ⟦F2⟧ d", "A ⟦F1⟧ b x ⟦F2⟧ d"), [("c", "x")])
        old = "rose ⟦F1⟧ fell"
        for change in textdiff.diff(old, "rose sharply ⟦F1⟧ fell"):
            self.assertNotIn("⟦", old[change.old_start:change.old_end])

    def test_one_word_fixes_stay_minimal(self):
        self.assertEqual(self.changes("Samples was stored at minus eighty degrees in the freezer.",
                                      "Samples were stored at minus eighty degrees in the freezer."),
                         [("was", "were")])
        self.assertEqual(self.changes("Levels rose in the treated group and in the control group.",
                                      "Levels fell in the treated group and in the placebo group."),
                         [("rose", "fell"), ("control", "placebo")])

    def test_semantic_cleanup_merges_fragmented_rewrites(self):
        self.assertEqual(self.changes("The results of training were clear.", "The findings of exercise were clear."),
                         [("results of training", "findings of exercise")])
        for merge in (True, False):
            with self.subTest(merge_whitespace_gaps=merge):
                self.assertEqual(
                    self.changes("the results were broadly similar across the two groups today",
                                 "the findings differed markedly between both cohorts today",
                                 merge_whitespace_gaps=merge),
                    [("results were broadly similar across the two groups",
                      "findings differed markedly between both cohorts")])

    def test_special_spaces_are_added_but_never_silently_removed(self):
        plain, special = "P < 0.05 in the test", "P\u00a0<\u202f0.05\u2007in the test"
        self.assertEqual(textdiff.diff(special, plain), [])      # retyped as plain spaces: kept
        self.assertEqual(self.changes(plain, special), [(" ", "\u00a0"), (" ", "\u202f"), (" ", "\u2007")])
        self.assertEqual(len(textdiff.diff(special, plain, spaces="exact")), 3)
        self.assertEqual(self.changes("a  b", "a b"), [("  ", " ")])
        self.assertEqual(self.changes("a\tb", "a b"), [("\t", " ")])

    def test_markup_round_trip(self):
        text, flags = textdiff.parse_markup("a⟨i⟩b⟨b⟩c⟨/b⟩⟨/i⟩d")
        self.assertEqual(text, "abcd")
        self.assertEqual(flags, [frozenset(), {"italic"}, {"italic", "bold"}, frozenset()])
        for marked in ("x⟨sup⟩2⟨/sup⟩ ⟨i⟩GENE1⟨/i⟩ ⟦F1⟧", "⟨b⟩⟨i⟩both⟨/i⟩⟨/b⟩ H⟨sub⟩2⟨/sub⟩O ⟨u⟩u⟨/u⟩"):
            with self.subTest(marked):
                self.assertEqual(textdiff.render_markup(*textdiff.parse_markup(marked)), marked)


# -- config ----------------------------------------------------------------------------------

class ConfigTests(unittest.TestCase):
    def test_revision_author_precedence_and_config_lookup(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ):
            os.environ.pop("MSW_AUTHOR", None)
            root = Path(temporary).resolve()
            nested = root / "01_Manuscripts" / "Versions"
            nested.mkdir(parents=True)
            self.assertIsNone(config.find_config(nested))
            self.assertIsNone(config.revision_author(None, nested))
            (root / "manuscript.json").write_text(json.dumps({"author": {"revision_name": "Config Name"}}),
                                                  encoding="utf-8")
            self.assertEqual(config.find_config(nested / "file.docx"), root / "manuscript.json")
            self.assertEqual(config.load_config(nested)["_root"], str(root))
            self.assertEqual(config.revision_author(None, nested), "Config Name")
            os.environ["MSW_AUTHOR"] = "Env Name"
            self.assertEqual(config.revision_author(None, nested), "Env Name")
            self.assertEqual(config.revision_author("Explicit Name", nested), "Explicit Name")

    def test_write_json_exclusive(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "sub" / "report.json"
            config.write_json_exclusive(target, {"name": "Ωmega"})
            raw = target.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r\n", raw)
            self.assertEqual(json.loads(raw.decode("utf-8")), {"name": "Ωmega"})
            with self.assertRaises(FileExistsError):
                config.write_json_exclusive(target, {})


# -- command line ----------------------------------------------------------------------------

class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.source = self.folder / "source.docx"
        self.source.write_bytes(fx.make_docx(multi_chunk=True))
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("MSW_AUTHOR", None)

    def tearDown(self):
        self.temp.cleanup()

    def test_read_only_commands(self):
        code, out, _ = call_cli("inspect", self.source)
        self.assertEqual(code, 0)
        profile = json.loads(out)
        self.assertEqual((profile["paragraphs"], profile["pictures"], profile["max_w_id"]), (14, 1, 102))
        self.assertEqual(profile["revision_authors"], {fx.AUTHOR_OTHER: 3})
        self.assertTrue(profile["track_revisions_on"])
        self.assertGreater(profile["document_xml_crlf"], 3)
        self.assertEqual(profile["citations"]["payload_forms"]["multi_chunk"], 1)
        code, out, _ = call_cli("census", self.source)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["citation_fields_live"], 3)
        code, out, _ = call_cli("fields", self.source)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["by_kind"], {"EN.CITE": 3, "EN.CITE.DATA": 3, "EN.REFLIST": 1})

    def test_broken_fields_give_a_nonzero_census(self):
        def unclosed(data):
            position = data.rindex(FIELD_END_RUN)
            return data[:position] + data[position + len(FIELD_END_RUN):]
        unclosed_file = self.folder / "unclosed.docx"
        unclosed_file.write_bytes(fx.replace_document(self.source.read_bytes(), unclosed))
        self.assertEqual(call_cli("census", unclosed_file)[0], 1)
        self.assertEqual(call_cli("fields", unclosed_file)[0], 1)

    def test_drift_lists_changes_since_the_approved_version(self):
        spec = self.folder / "e.json"
        spec.write_text(json.dumps({"edits": [{"para": "10000004", "old": "31", "new": "32"}]}), encoding="utf-8")
        later = self.folder / "V3.docx"
        self.assertEqual(call_cli("build", self.source, spec, "--out", later, "--author", AUTHOR)[0], 0)
        self.assertEqual(call_cli("drift", self.source, self.source)[0], 0)
        report = self.folder / "drift.json"
        code, out, _ = call_cli("drift", self.source, later, "--json", report)
        self.assertEqual(code, 1)
        self.assertIn("removed ['31'] added ['32']", out)
        result = json.loads(report.read_text(encoding="utf-8"))
        [change] = result["changes"]
        self.assertEqual((change["paraId"], change["spans"]), ("10000004", [{"before": "The unit was ",
                                                                             "old": "31", "new": "32"}]))
        self.assertEqual(call_cli("drift", self.source, later, "--json", report)[0], 2)   # never overwritten

    def test_extract_never_turns_a_paraid_into_a_path(self):
        odd = self.folder / "odd.docx"
        odd.write_bytes(fx.replace_document(self.source.read_bytes(),
                                            lambda xml: xml.replace(b'w14:paraId="10000003"',
                                                                    b'w14:paraId="../../ESCAPED"')))
        work = self.folder / "ext"
        self.assertEqual(call_cli("extract", odd, "--out", work)[0], 0)
        self.assertFalse(list(self.folder.rglob("ESCAPED*")))
        self.assertIn("P0002.txt", [p.name for p in (work / "in").iterdir()])
        (work / "out" / "P0002.txt").write_text((work / "in" / "P0002.txt").read_text(encoding="utf-8")
                                                .replace("compared", "contrasted"), encoding="utf-8")
        self.assertEqual(call_cli("collect", work, "--out", self.folder / "e.json")[0], 0)
        [edit] = json.loads((self.folder / "e.json").read_text(encoding="utf-8"))["edits"]
        self.assertEqual(edit["para"], {"index": 2})

    def test_collect_treats_an_empty_output_as_unchanged(self):
        work = self.folder / "empty"
        self.assertEqual(call_cli("extract", self.source, "--out", work, "--paragraphs", "1000000A")[0], 0)
        (work / "out" / "1000000A.txt").write_text("\n", encoding="utf-8")
        code, out, _ = call_cli("collect", work, "--out", self.folder / "none.json")
        self.assertEqual(code, 0)
        self.assertIn("0 rewritten paragraph(s), 1 unchanged", out)

    def test_drift_records_name_files_without_machine_paths(self):
        (self.folder / "manuscript.json").write_text("{}", encoding="utf-8")
        report = self.folder / "drift.json"
        call_cli("drift", self.source.resolve(), self.source.resolve(), "--json", report)
        result = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual((result["old"]["path"], result["new"]["path"]), ("source.docx", "source.docx"))

    def test_collect_keeps_a_final_line_break(self):
        extra = fx.para('<w:r><w:t>Jane Doe, PhD</w:t><w:br/></w:r>', "10000010")
        source = self.folder / "br.docx"
        source.write_bytes(fx.make_docx(extra_paragraphs=extra))
        work = self.folder / "brx"
        self.assertEqual(call_cli("extract", source, "--out", work, "--paragraphs", "10000010")[0], 0)
        text = (work / "in" / "10000010.txt").read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        (work / "out" / "10000010.txt").write_text(text, encoding="utf-8")
        code, out, _ = call_cli("collect", work, "--out", self.folder / "same.json")
        self.assertIn("0 rewritten", out)
        (work / "out" / "10000010.txt").write_text(text + "\n", encoding="utf-8")   # an editor's newline
        self.assertIn("0 rewritten", call_cli("collect", work, "--out", self.folder / "same2.json")[1])

    def test_a_taken_manifest_name_stops_the_build_before_anything_is_written(self):
        spec = self.folder / "e.json"
        spec.write_text(json.dumps({"edits": [{"para": "10000003", "old": "compared", "new": "contrasted"}]}),
                        encoding="utf-8")
        out = self.folder / "V2 tracked.docx"
        (self.folder / "V2 tracked.manifest.json").write_text("{}", encoding="utf-8")
        code, _, err = call_cli("build", self.source, spec, "--out", out, "--author", AUTHOR)
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertFalse(out.exists())

    def test_a_failed_build_names_its_own_manifest(self):
        bad = self.folder / "bad.json"
        bad.write_text(json.dumps({"edits": [{"para": "10000004", "old": "31", "new": "32"}]}), encoding="utf-8")
        out = self.folder / "V2 tracked.docx"
        with mock.patch.object(cli, "verify") as fake:
            fake.return_value = mock.Mock(status="FAIL", text=lambda: "GATE FAIL", to_dict=lambda: {"status": "FAIL"})
            code, _, _ = call_cli("build", self.source, bad, "--out", out, "--author", AUTHOR, "--write-failed")
        self.assertEqual(code, 1)
        self.assertTrue((self.folder / "V2 tracked GATE FAILED.manifest.json").exists())
        self.assertFalse((self.folder / "V2 tracked.manifest.json").exists())
        code, _, err = call_cli("build", self.source, bad, "--out", out, "--author", AUTHOR)
        self.assertEqual(code, 0, err)
        self.assertTrue((self.folder / "V2 tracked.manifest.json").exists())

    def test_os_errors_are_refusals_not_gate_failures(self):
        folder = self.folder / "adir"
        folder.mkdir()
        for argv in (("inspect", folder), ("census", folder), ("extract", self.source, "--out", self.source)):
            code, _, err = call_cli(*argv)
            self.assertEqual(code, 2, (argv, err))
            self.assertIn("ERROR", err)

    def test_manuscript_json_with_a_byte_order_mark(self):
        (self.folder / "manuscript.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(
            {"author": {"revision_name": "Config Author"}}).encode("utf-8"))
        spec = self.folder / "e.json"
        spec.write_text(json.dumps({"edits": [{"para": "10000003", "old": "compared", "new": "contrasted"}]}),
                        encoding="utf-8")
        code, _, err = call_cli("build", self.source, spec, "--out", self.folder / "V2.docx")
        self.assertEqual(code, 0, err)
        manifest = json.loads((self.folder / "V2.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["author"], "Config Author")
        # inside a project, records name files relative to its root, never by machine path
        self.assertEqual((manifest["source"]["path"], manifest["spec"], manifest["output"]["path"]),
                         ("source.docx", "e.json", "V2.docx"))

    def test_extract_collect_build_verify_pipeline(self):
        work = self.folder / "rewrite"
        code, _, err = call_cli("extract", self.source, "--out", work, "--exclude-style", "Heading")
        self.assertEqual(code, 0, err)
        keys = sorted(p.stem for p in (work / "in").iterdir())
        self.assertIn("10000003", keys)
        self.assertNotIn("10000002", keys)
        records = [json.loads(line) for line in (work / "paragraphs.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual({r["key"] for r in records}, set(keys))
        self.assertEqual(json.loads((work / "source.json").read_text(encoding="utf-8"))["sha256"],
                         docx.sha256_file(self.source))
        self.assertEqual(call_cli("extract", self.source, "--out", work)[0], 2)

        original = (work / "in" / "10000003.txt").read_text(encoding="utf-8")
        rewritten = original.replace("compared", "contrasted")
        # A PowerShell 5.1 style file: UTF-8 BOM and CRLF line ending (P5).
        (work / "out" / "10000003.txt").write_bytes(b"\xef\xbb\xbf" + rewritten.encode("utf-8") + b"\r\n")
        (work / "out" / "10000004.txt").write_text((work / "in" / "10000004.txt").read_text(encoding="utf-8"),
                                                   encoding="utf-8")
        spec_path = self.folder / "edits.json"
        code, out, err = call_cli("collect", work, "--out", spec_path)
        self.assertEqual(code, 0, err)
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        [item] = spec["edits"]
        self.assertEqual((item["op"], item["para"], item["text"]), ("rewrite", "10000003", rewritten))
        self.assertNotIn("﻿", item["text"])

        out_path = self.folder / "V2 tracked.docx"
        code, _, err = call_cli("build", self.source, spec_path, "--out", out_path)
        self.assertEqual(code, 2)
        self.assertIn("revision author", err)
        self.assertFalse(out_path.exists())

        code, out, err = call_cli("build", self.source, spec_path, "--out", out_path, "--author", AUTHOR,
                                  "--date", DATE)
        self.assertEqual(code, 0, out + err)
        self.assertIn("GATE PASS", out)
        manifest_path = self.folder / "V2 tracked.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["gate"]["status"], "PASS")
        self.assertEqual(manifest["output"]["sha256"], docx.sha256_file(out_path))
        accepted = view_paragraph_text(out_path.read_bytes(), "accept", "10000003")
        self.assertEqual(accepted, "Marker levels were contrasted between groups GENE1 expression rose after "
                                   "training (1) and fell later.")
        self.assertNotIn("﻿", "".join(texts(out_path.read_bytes(), "accept")))

        before = docx.sha256_file(out_path)
        code, _, err = call_cli("build", self.source, spec_path, "--out", out_path, "--author", AUTHOR)
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertEqual(docx.sha256_file(out_path), before)

        report_path = self.folder / "verify.json"
        code, out, _ = call_cli("verify", self.source, out_path, "--manifest", manifest_path, "--author", AUTHOR,
                                "--json", report_path)
        self.assertEqual(code, 0, out)
        self.assertIn("SUMMARY:", out)
        self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["status"], "PASS")

        code, _, _ = call_cli("views", out_path, "--out", self.folder / "views")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in (self.folder / "views").iterdir()),
                         ["V2 tracked accepted view.docx", "V2 tracked accepted view.txt",
                          "V2 tracked rejected view.docx", "V2 tracked rejected view.txt"])
        rejected = (self.folder / "views" / "V2 tracked rejected view.txt").read_text(encoding="utf-8")
        self.assertEqual(rejected.splitlines(), texts(self.source.read_bytes(), "reject"))

    def test_build_refuses_to_write_a_failed_gate(self):
        spec_path = self.folder / "edits.json"
        # the declared intent does not match the edit, so the gate's accepted_text check fails
        spec_path.write_text(json.dumps({"edits": [{"para": "10000003", "old": "compared", "new": "contrasted"}],
                                         "intended": [{"old": "compared", "new": "matched"}]}), encoding="utf-8")
        out_path = self.folder / "V2 tracked.docx"
        code, out, _ = call_cli("build", self.source, spec_path, "--out", out_path, "--author", AUTHOR)
        self.assertEqual(code, 1)
        self.assertIn("GATE FAIL", out)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["edits.json", "source.docx"])
        code, _, _ = call_cli("build", self.source, spec_path, "--out", out_path, "--author", AUTHOR, "--write-failed")
        self.assertEqual(code, 1)
        self.assertTrue((self.folder / "V2 tracked GATE FAILED.docx").exists())
        self.assertFalse(out_path.exists())

    def test_build_reports_edit_errors_and_collect_rejects_unknown_files(self):
        spec_path = self.folder / "edits.json"
        spec_path.write_text(json.dumps({"edits": [{"para": "1000000C", "old": "First", "new": "1st"}]}),
                             encoding="utf-8")
        code, _, err = call_cli("build", self.source, spec_path, "--out", self.folder / "out.docx", "--author", AUTHOR)
        self.assertEqual(code, 2)
        self.assertIn("ERROR", err)
        work = self.folder / "rewrite"
        self.assertEqual(call_cli("extract", self.source, "--out", work)[0], 0)
        (work / "out" / "NOPE.txt").write_text("text", encoding="utf-8")
        self.assertEqual(call_cli("collect", work, "--out", self.folder / "spec.json")[0], 2)
        self.assertFalse((self.folder / "spec.json").exists())


if __name__ == "__main__":
    unittest.main()

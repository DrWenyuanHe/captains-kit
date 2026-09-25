"""Synthetic Word documents for the scientific-manuscript tests.

The fixture contains no manuscript text. It reproduces the structures the tools
must handle in real EndNote manuscripts: runs with rsid attributes, italic and
superscript runs, three EndNote payload forms (inline XML, one CRLF-wrapped
base64 chunk, several chunks), hyperlinked citation numbers, a bibliography field
spanning paragraphs with _ENREF bookmarks, earlier tracked changes by another
author (including a deleted paragraph mark), a picture and a figure legend.
"""

import base64
import io
import struct
import zipfile
import zlib

NS = ('xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
      'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
      'xmlns:o="urn:schemas-microsoft-com:office:office" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
      'xmlns:v="urn:schemas-microsoft-com:vml" '
      'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:w10="urn:schemas-microsoft-com:office:word" '
      'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
      'xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
      'mc:Ignorable="w14 wp14"')
DB = "fixturedb0000000000000000000000000000"
AUTHOR_OTHER = "Co-author"


def png(width=40, height=20, colour=(200, 30, 30)) -> bytes:
    raw = b"".join(b"\x00" + bytes(colour) * width for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def record(rec, author, year, title, pmid, pages="100-9", doi="10.1000/fixture.{rec}"):
    return (f"<Cite><Author>{author}</Author><Year>{year}</Year><RecNum>{rec}</RecNum>"
            f"<DisplayText>({rec})</DisplayText><record><rec-number>{rec}</rec-number>"
            f'<foreign-keys><key app="EN" db-id="{DB}">{rec}</key></foreign-keys>'
            f'<ref-type name="Journal Article">17</ref-type><contributors><authors><author>{author}, A.</author>'
            f"</authors></contributors><titles><title>{title}</title><secondary-title>J Fixture</secondary-title>"
            f"</titles><pages>{pages}</pages><volume>1</volume><dates><year>{year}</year></dates>"
            f"<accession-num>{pmid}</accession-num><electronic-resource-num>{doi.format(rec=rec)}"
            f"</electronic-resource-num></record></Cite>")


def _b64_lines(data: bytes) -> str:
    text = base64.b64encode(data).decode()
    return "\r\n".join(text[i:i + 76] for i in range(0, len(text), 76))


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def run(text, rpr="", rsid=True):
    attrs = ' w:rsidR="00A1B2C3" w:rsidRPr="00D4E5F6"' if rsid else ""
    space = ' xml:space="preserve"' if text != text.strip() else ""
    return f'<w:r{attrs}>{("<w:rPr>" + rpr + "</w:rPr>") if rpr else ""}<w:t{space}>{_escape(text)}</w:t></w:r>'


def _result(number):
    return (f'<w:r><w:t>(</w:t></w:r><w:hyperlink w:anchor="_ENREF_{number}" w:tooltip="Fixture, 2020 #{number}" '
            f'w:history="1"><w:r><w:t>{number}</w:t></w:r></w:hyperlink><w:r><w:t>)</w:t></w:r>')


def cite_inline(number, cites_xml):
    payload = _escape(f"<EndNote>{cites_xml}</EndNote>")
    return ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            f'<w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE {payload}</w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            + _result(number) + '<w:r><w:fldChar w:fldCharType="end"/></w:r>')


def cite_chunked(number, cites_xml, chunks=1):
    payload = f"<EndNote>{cites_xml}</EndNote>".encode() + b"\x00"
    size = -(-len(payload) // chunks)
    parts = [payload[i:i + size] for i in range(0, len(payload), size)]
    outer = _b64_lines(parts[-1])
    nested = "".join(
        f'<w:r><w:fldChar w:fldCharType="begin"><w:fldData xml:space="preserve">{_b64_lines(part)}</w:fldData>'
        '</w:fldChar></w:r><w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE.DATA </w:instrText></w:r>'
        '<w:r/><w:r><w:fldChar w:fldCharType="end"/></w:r>' for part in parts)
    return (f'<w:r><w:fldChar w:fldCharType="begin"><w:fldData xml:space="preserve">{outer}</w:fldData>'
            '</w:fldChar></w:r><w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE </w:instrText></w:r>'
            + nested + '<w:r/><w:r><w:fldChar w:fldCharType="separate"/></w:r>' + _result(number)
            + '<w:r><w:fldChar w:fldCharType="end"/></w:r>')


def para(content, pid, style=None, ppr_extra=""):
    ppr = ""
    if style or ppr_extra:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + ppr_extra + "</w:pPr>"
    return f'<w:p w14:paraId="{pid}" w14:textId="77777777" w:rsidR="00A1B2C3">{ppr}{content}</w:p>'


def picture(name="Figure 1", rid="rId10", docpr_id=1, cx=5486400, cy=2743200):
    return ('<w:r><w:rPr><w:noProof/></w:rPr><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0" '
            'wp14:anchorId="11111111" wp14:editId="22222222">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{docpr_id}" name="{name}" descr="{name}"/>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{docpr_id}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
            '</wp:inline></w:drawing></w:r>')


def document_xml(multi_chunk=False, extra_paragraphs=""):
    c1 = record(1, "Alpha", 2020, "First fixture paper", "10000001")
    c2 = record(2, "Beta", 2021, "Second fixture paper", "10000002")
    c3 = record(3, "Gamma", 2022, "Third fixture paper " + "x" * 400, "10000003")
    body = [
        para(run("A fixture title about marker Q-17 in a training study", "<w:b/><w:sz w:val=\"24\"/>"), "10000001"),
        para(run("Abstract"), "10000002", "Heading1"),
        para(run("Marker levels were compared between groups ") + run("GENE1", "<w:i/>")
             + run(" expression rose after training ") + cite_inline(1, c1) + run(" and fell later."), "10000003"),
        para(run("The unit was 31 kg/m") + run("2", '<w:vertAlign w:val="superscript"/>')
             + run(", which is high ") + cite_chunked(2, c2) + run(" in this cohort."), "10000004"),
        para(run("Prior text ") + f'<w:ins w:id="1" w:author="{AUTHOR_OTHER}" w:date="2026-01-01T00:00:00Z">'
             + run("inserted by a co-author", rsid=False) + "</w:ins>" + run(" and ")
             + f'<w:del w:id="2" w:author="{AUTHOR_OTHER}" w:date="2026-01-01T00:00:00Z"><w:r><w:delText>'
             "deleted words</w:delText></w:r></w:del>" + run(" remain."), "10000005"),
        para(run("First half of a merged paragraph"), "10000006",
             ppr_extra=f'<w:rPr><w:del w:id="3" w:author="{AUTHOR_OTHER}" w:date="2026-01-01T00:00:00Z"/></w:rPr>'),
        para(run(" and its second half."), "10000007"),
        para(run("Long group citation ") + (cite_chunked(3, c3 + c1, chunks=2) if multi_chunk else cite_inline(3, c3))
             + run(" ends here."), "10000008"),
        para(picture(), "10000009", ppr_extra='<w:jc w:val="center"/>'),
        para(run("(A) Points are participants; error bars show the SEM; ") + run("P", "<w:i/>") + run(" < 0.05."),
             "1000000A"),
        extra_paragraphs,
        para(run("References"), "1000000B", "Heading1"),
        para('<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> ADDIN EN.REFLIST '
             '</w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
             '<w:bookmarkStart w:id="100" w:name="_ENREF_1"/><w:r><w:t>1.</w:t></w:r><w:r><w:tab/>'
             '<w:t>Alpha A. First fixture paper. J Fixture. 2020;1:100-9.</w:t></w:r><w:bookmarkEnd w:id="100"/>',
             "1000000C", "EndNoteBibliography"),
        para('<w:bookmarkStart w:id="101" w:name="_ENREF_2"/><w:r><w:t>2.</w:t></w:r><w:r><w:tab/>'
             '<w:t>Beta B. Second fixture paper. J Fixture. 2021;1:100-9.</w:t></w:r><w:bookmarkEnd w:id="101"/>',
             "1000000D", "EndNoteBibliography"),
        para('<w:bookmarkStart w:id="102" w:name="_ENREF_3"/><w:r><w:t>3.</w:t></w:r><w:r><w:tab/>'
             '<w:t>Gamma C. Third fixture paper. J Fixture. 2022;1:100-9.</w:t></w:r><w:bookmarkEnd w:id="102"/>'
             '<w:r><w:fldChar w:fldCharType="end"/></w:r>', "1000000E", "EndNoteBibliography"),
    ]
    sect = ('<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1800" w:bottom="1440" '
            'w:left="1800" w:header="708" w:footer="708" w:gutter="0"/><w:lnNumType w:countBy="1" '
            'w:restart="continuous"/></w:sectPr>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
            f"<w:document {NS}><w:body>" + "".join(body) + sect + "</w:body></w:document>").encode("utf-8")


CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="xml" ContentType="application/xml"/>'
                 '<Default Extension="png" ContentType="image/png"/>'
                 '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                 'wordprocessingml.document.main+xml"/>'
                 '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                 'wordprocessingml.styles+xml"/>'
                 '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                 'wordprocessingml.settings+xml"/></Types>')
ROOT_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
             'officeDocument" Target="word/document.xml"/></Relationships>')
DOC_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'styles" Target="styles.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'settings" Target="settings.xml"/>'
            '<Relationship Id="rId10" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'image" Target="media/image1.png"/></Relationships>')
STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
          '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
          '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
          '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
          '<w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
          '<w:style w:type="paragraph" w:styleId="EndNoteBibliography"><w:name w:val="EndNote Bibliography"/>'
          '<w:basedOn w:val="Normal"/></w:style></w:styles>')
SETTINGS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
            '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:zoom w:percent="100"/><w:trackRevisions/><w:defaultTabStop w:val="720"/></w:settings>')


def make_docx(multi_chunk=False, extra_paragraphs="", document=None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        def add(name, data, stored=False):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
        add("[Content_Types].xml", CONTENT_TYPES)
        add("_rels/.rels", ROOT_RELS)
        add("word/document.xml", document if document is not None else document_xml(multi_chunk, extra_paragraphs))
        add("word/_rels/document.xml.rels", DOC_RELS)
        add("word/styles.xml", STYLES)
        add("word/settings.xml", SETTINGS)
        add("word/media/image1.png", png(), stored=True)
    return buffer.getvalue()


def replace_document(docx: bytes, transform) -> bytes:
    """Return a copy of `docx` whose document.xml is transform(old_bytes)."""
    source = zipfile.ZipFile(io.BytesIO(docx))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "word/document.xml":
                data = transform(data)
            archive.writestr(info, data)
    return buffer.getvalue()

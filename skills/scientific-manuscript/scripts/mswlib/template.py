"""Build a new manuscript .docx in the house style from manuscript.md and title_page.json.

The package is written from scratch with zipfile (no template file): content
types, relationships, document.xml with a w14:paraId on every paragraph, styles
(Normal, Body Text, Body Text No Indent, Heading 1-3, Figure Legend, EndNote
Bibliography, a borderless table style), settings (track changes off), a centred
PAGE footer and document properties. Section 1 holds the title page and the text;
section 2 starts at "# Figure Legends". The abstract and the back matter
(Contributions, Data availability, Acknowledgements, declarations) use Body Text
No Indent; the other body paragraphs use Body Text. The title-page word count is
computed from the built document with wordcount.py and written into the "Word
count:" line, whose parenthesis names what the chosen method leaves out.

manuscript.md dialect: # / ## / ### headings (numbering typed in the text);
blank-line separated paragraphs; <i> <b> <sup> <sub> <u> inline tags (asterisks
and underscores are literal); [PMID: n] placeholders kept as text; | pipe |
tables with a header and separator row; a line ![Alt](path.png){width=120mm};
\\pagebreak; full-line <!-- comments --> are skipped.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import load_config
from .docx import FIXED_DATE, Package, PackageError
from .endnote import census
from .profile import article_rules, load_profile
from .views import project
from .wordcount import METHODS, WordCountError, count_package, format_count, scope_text
from .wordml import check_namespaces, iter_paragraphs, map_fields
from .xmltree import XMLDoc, XMLError, escape_attr, escape_text

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DOC_NS = ('xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
          f'xmlns:r="{R_NS}" '
          'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
          'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
          f'xmlns:w="{W_NS}" '
          'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
          'xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" '
          'mc:Ignorable="w14 wp14"')
PART_NS = (f'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:r="{R_NS}" '
           f'xmlns:w="{W_NS}" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
           'mc:Ignorable="w14"')
DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
CT_WORD = "application/vnd.openxmlformats-officedocument.wordprocessingml."
EMU_PER_TWIP = 635
EMU = {"mm": 36000, "cm": 360000, "in": 914400}
WORD_COUNT_SENTINEL = "MSWWORDCOUNTSENTINEL"
PAGES = {"letter": (12240, 15840), "us letter": (12240, 15840), "a4": (11906, 16838)}
SPACING = {"single": 240, "1.15": 276, "1.5": 360, "one_and_half": 360, "double": 480}
RESTART = {"continuous": "continuous", "page": "newPage", "each_page": "newPage", "newpage": "newPage",
           "per_page": "newPage", "per_section": "newSection",
           "section": "newSection", "each_section": "newSection", "newsection": "newSection"}
LANGUAGES = {"en-US", "en-GB", "en-CA", "en-AU"}

_TAG = re.compile(r"<(/?)(i|b|sup|sub|u)>")
_FLAG = {"i": "italic", "b": "bold", "sup": "superscript", "sub": "subscript", "u": "underline"}
_HEADING = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")
_DEEP_HEADING = re.compile(r"^#{4,}\s")
_PICTURE = re.compile(r"^!\[([^\]]*)\]\(\s*<?([^)>]+?)>?\s*\)"
                      r"(?:\{\s*width\s*=\s*(\d+(?:\.\d+)?)\s*(mm|cm|in)\s*\})?\s*$")
_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FIGURE_LEGENDS = re.compile(r"^\s*figure\s+legends?\s*$", re.I)
_REFERENCES = re.compile(r"^\s*(?:\d+\.?\s*)?references\s*$", re.I)
_ABSTRACT = re.compile(r"^\s*abstract\s*$", re.I)
_FIGURE_LABEL = re.compile(r"^\s*((?:supplementary\s+)?(?:figure|fig\.?)\s*S?\d+[A-Za-z]?)", re.I)
# Back-matter headings and run-in declaration labels: their paragraphs take Body Text No Indent.
_BACK_MATTER = re.compile(
    r"^\s*(?:(?:authors?['’]?\s+)?contributions?|contributorship|credit\b|data\s+(?:availability|sharing)|"
    r"availability\s+of\s+data|acknowledge?ments?|declarations?\b|(?:conflicts?|competing)\s+(?:of\s+)?interests?|"
    r"disclosures?\b|funding|financial\s+support|(?:(?:use|statement|declaration)\s+of\s+)?(?:the\s+use\s+of\s+)?"
    r"(?:generative\s+)?(?:ai|artificial\s+intelligence)\b|ethics\b|(?:patient\s+)?consent\b|preprint\b)", re.I)
_KEY_SPLIT = re.compile(r"\s*,\s*")


class TemplateError(ValueError):
    """The source files cannot be built into a manuscript."""


# -- layout ---------------------------------------------------------------------------

@dataclass
class Layout:
    page_w: int = 12240
    page_h: int = 15840
    top: int = 1440
    bottom: int = 1440
    left: int = 1800
    right: int = 1800
    font: str = "Times New Roman"
    size: int = 22                    # half-points
    body_line: int = 480
    refs_line: int = 240
    line_numbers: str | None = "continuous"
    language: str = "en-US"
    separate_title_page: bool = False

    @property
    def text_width(self) -> int:      # twips
        return self.page_w - self.left - self.right

    @property
    def text_width_emu(self) -> int:
        return self.text_width * EMU_PER_TWIP

    @property
    def text_height_emu(self) -> int:
        return (self.page_h - self.top - self.bottom) * EMU_PER_TWIP


def _spacing(value, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(240, int(round(240 * float(value))))
    key = str(value).strip().lower()
    if key in SPACING:
        return SPACING[key]
    try:
        return max(240, int(round(240 * float(key))))
    except ValueError:
        return default


def layout_from(profile: dict, config: dict | None = None) -> Layout:
    formatting = profile.get("formatting") or {}
    layout = Layout()
    page = str(formatting.get("page", "letter")).strip().lower()
    layout.page_w, layout.page_h = PAGES.get(page, PAGES["letter"])
    layout.font = formatting.get("font") or layout.font
    size = formatting.get("font_size_pt")
    if isinstance(size, (int, float)) and size > 0:
        layout.size = int(round(size * 2))
    layout.body_line = _spacing(formatting.get("line_spacing"), 480)
    layout.refs_line = _spacing(formatting.get("references_line_spacing"), 240)
    numbers = formatting.get("line_numbers", "continuous")
    if numbers in (False, None) or str(numbers).strip().lower() in ("none", "off", "false", "no"):
        layout.line_numbers = None
    else:
        layout.line_numbers = RESTART.get(str(numbers).strip().lower().replace("-", "_"), "continuous")
    spelling = ((config or {}).get("language") or {}).get("spelling")
    languages = formatting.get("language") or []
    if spelling in LANGUAGES:
        layout.language = spelling
    elif languages and languages[0] in LANGUAGES:
        layout.language = languages[0]
    layout.separate_title_page = bool((profile.get("title_page") or {}).get("separate_file"))
    return layout


# -- inline text ------------------------------------------------------------------------

def parse_inline(text: str, where: str) -> list[tuple[str, frozenset]]:
    """Split text with <i> <b> <sup> <sub> <u> tags into (text, flags) segments."""
    text = _CONTROL.sub("", text)
    segments: list[tuple[str, frozenset]] = []
    active: list[str] = []
    position = 0
    for match in _TAG.finditer(text):
        if match.start() > position:
            segments.append((text[position:match.start()], frozenset(_FLAG[t] for t in active)))
        closing, name = match.group(1), match.group(2)
        if closing:
            if not active or active[-1] != name:
                raise TemplateError(f"{where}: closing </{name}> does not match "
                                    f"{('<' + active[-1] + '>') if active else 'an open tag'}")
            active.pop()
        else:
            if name in active:
                raise TemplateError(f"{where}: <{name}> is already open")
            active.append(name)
        position = match.end()
    if position < len(text):
        segments.append((text[position:], frozenset(_FLAG[t] for t in active)))
    if active:
        raise TemplateError(f"{where}: unclosed <{active[-1]}>")
    merged: list[list] = []
    for piece, flags in segments:
        if merged and merged[-1][1] == flags:
            merged[-1][0] += piece
        elif piece:
            merged.append([piece, flags])
    return [(t, f) for t, f in merged]


def plain(segments) -> str:
    return "".join(t for t, _ in segments)


def run_xml(text: str, flags=frozenset(), *, size: int | None = None) -> str:
    props = []
    if "bold" in flags:
        props.append("<w:b/><w:bCs/>")
    if "italic" in flags:
        props.append("<w:i/><w:iCs/>")
    if size:
        props.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    if "underline" in flags:
        props.append('<w:u w:val="single"/>')
    if "superscript" in flags:
        props.append('<w:vertAlign w:val="superscript"/>')
    elif "subscript" in flags:
        props.append('<w:vertAlign w:val="subscript"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    body = []
    for index, piece in enumerate(text.split("\t")):
        if index:
            body.append("<w:tab/>")
        if piece:
            space = ' xml:space="preserve"' if piece != piece.strip() or "  " in piece else ""
            body.append(f"<w:t{space}>{escape_text(piece)}</w:t>")
    return f"<w:r>{rpr}{''.join(body)}</w:r>" if body else ""


def runs_xml(segments, *, add=frozenset(), size: int | None = None) -> str:
    return "".join(run_xml(text, frozenset(flags) | add, size=size) for text, flags in segments)


# -- paragraphs -------------------------------------------------------------------------

class ParaIds:
    """Deterministic, unique w14:paraId values (8 hex digits below 0x80000000)."""

    def __init__(self, seed: str):
        self.seed = seed
        self.count = 0
        self.used: set[int] = set()

    def next(self) -> str:
        while True:
            self.count += 1
            value = int(hashlib.sha256(f"{self.seed}:{self.count}".encode()).hexdigest()[:8], 16) & 0x7FFFFFFF
            if value > 0x10 and value not in self.used:
                self.used.add(value)
                return f"{value:08X}"


def ppr_xml(*, style=None, keep_next=False, page_break=False, spacing=None, first_line=None, jc=None,
            sect=None) -> str:
    parts = []
    if style:
        parts.append(f'<w:pStyle w:val="{style}"/>')
    if keep_next:
        parts.append("<w:keepNext/>")
    if page_break:
        parts.append("<w:pageBreakBefore/>")
    if spacing:
        attrs = "".join(f' w:{key}="{value}"' for key, value in spacing.items())
        parts.append(f"<w:spacing{attrs}/>")
    if first_line is not None:
        parts.append(f'<w:ind w:firstLine="{first_line}"/>')
    if jc:
        parts.append(f'<w:jc w:val="{jc}"/>')
    if sect:
        parts.append(sect)
    return f"<w:pPr>{''.join(parts)}</w:pPr>" if parts else ""


def para_xml(ids: ParaIds, content: str, **ppr) -> str:
    return f'<w:p w14:paraId="{ids.next()}" w14:textId="77777777">{ppr_xml(**ppr)}{content}</w:p>'


def sect_xml(layout: Layout, footer_rid: str, *, next_page: bool = False) -> str:
    numbers = (f'<w:lnNumType w:countBy="1" w:restart="{layout.line_numbers}"/>' if layout.line_numbers else "")
    return (f'<w:sectPr><w:footerReference w:type="default" r:id="{footer_rid}"/>'
            + ('<w:type w:val="nextPage"/>' if next_page else "")
            + f'<w:pgSz w:w="{layout.page_w}" w:h="{layout.page_h}"/>'
            f'<w:pgMar w:top="{layout.top}" w:right="{layout.right}" w:bottom="{layout.bottom}" '
            f'w:left="{layout.left}" w:header="720" w:footer="720" w:gutter="0"/>'
            + numbers + '<w:cols w:space="720"/><w:docGrid w:linePitch="360"/></w:sectPr>')


# -- markdown ----------------------------------------------------------------------------

@dataclass
class Block:
    kind: str                  # heading | para | picture | table | pagebreak
    line: int
    level: int = 0
    text: str = ""
    rows: list = field(default_factory=list)
    alt: str = ""
    path: str = ""
    width_emu: int | None = None


def _split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", body)]


def parse_markdown(text: str, name: str = "manuscript.md") -> list[Block]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    buffer: list[str] = []
    start = 0
    in_comment = False

    def flush():
        nonlocal buffer
        if buffer:
            blocks.append(Block("para", start, text=" ".join(buffer)))
            buffer = []

    index = 0
    while index < len(lines):
        number = index + 1
        line = lines[index].strip()
        index += 1
        if in_comment:
            in_comment = "-->" not in line
            continue
        if line.startswith("<!--"):
            in_comment = "-->" not in line[4:]
            continue
        if not line:
            flush()
            continue
        match = _HEADING.match(line)
        if match:
            flush()
            blocks.append(Block("heading", number, level=len(match.group(1)), text=match.group(2)))
            continue
        if _DEEP_HEADING.match(line):
            raise TemplateError(f"{name}:{number}: only #, ## and ### headings are supported")
        if line == "\\pagebreak":
            flush()
            blocks.append(Block("pagebreak", number))
            continue
        match = _PICTURE.match(line)
        if match:
            flush()
            width = None
            if match.group(3):
                width = int(round(float(match.group(3)) * EMU[match.group(4)]))
            blocks.append(Block("picture", number, alt=match.group(1).strip(), path=match.group(2).strip(),
                                width_emu=width))
            continue
        if line.startswith("|"):
            flush()
            rows = [(number, line)]
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append((index + 1, lines[index].strip()))
                index += 1
            if len(rows) < 2 or not _SEPARATOR.match(rows[1][1]):
                raise TemplateError(f"{name}:{number}: a table needs a header row followed by a separator "
                                    "row such as | --- | --- |")
            header = _split_row(rows[0][1])
            body = []
            for row_number, row in rows[2:]:
                cells = _split_row(row)
                if len(cells) != len(header):
                    raise TemplateError(f"{name}:{row_number}: row has {len(cells)} cells; the header has "
                                        f"{len(header)}")
                body.append(cells)
            blocks.append(Block("table", number, rows=[header] + body))
            continue
        if not buffer:
            start = number
        buffer.append(line)
    flush()
    if in_comment:
        raise TemplateError(f"{name}: an <!-- comment is never closed")
    return blocks


# -- pictures ----------------------------------------------------------------------------

def image_info(data: bytes) -> tuple[str, int, int]:
    """(extension, width px, height px) for PNG, JPEG and GIF."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return "png", width, height
    if data[:6] in (b"GIF87a", b"GIF89a"):
        width, height = struct.unpack("<HH", data[6:10])
        return "gif", width, height
    if data[:2] == b"\xff\xd8":
        position = 2
        while position + 9 < len(data):
            if data[position] != 0xFF:
                position += 1
                continue
            marker = data[position + 1]
            if marker == 0xFF:
                position += 1
                continue
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                position += 2
                continue
            length = struct.unpack(">H", data[position + 2:position + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[position + 5:position + 9])
                return "jpeg", width, height
            position += 2 + length
    raise TemplateError("unsupported picture format: use PNG, JPEG or GIF")


CONTENT_TYPES_BY_EXT = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif"}


# -- the document -------------------------------------------------------------------------

def _back_matter(section: str, segments) -> bool:
    """A paragraph under a back-matter Heading 1, or a bold run-in declaration line ('Funding: ...')."""
    if section and _BACK_MATTER.match(section):
        return True
    if segments and "bold" in segments[0][1]:
        label = segments[0][0].strip()
        return label.endswith(":") and len(label) <= 70 and bool(_BACK_MATTER.match(label))
    return False


class BodyRenderer:
    def __init__(self, layout: Layout, ids: ParaIds, base_dir: Path, name: str):
        self.layout = layout
        self.ids = ids
        self.base = base_dir
        self.name = name
        self.media: list = []
        self.image_rels: list = []
        self.by_hash: dict = {}
        self.picture_names: set = set()
        self.warnings: list = []
        self.stats = {"headings": 0, "paragraphs": 0, "pictures": 0, "tables": 0, "sections": 1,
                      "abstract_paragraphs": 0}

    def where(self, block: Block) -> str:
        return f"{self.name}:{block.line}"

    # -- blocks -------------------------------------------------------------------------
    def render(self, blocks: list[Block], *, footer_rid: str, page_break_first: bool) -> str:
        out: list[str] = []
        layout = self.layout
        section = ""
        pending_break = page_break_first
        legend_headings = 0
        legend_label = ""
        references_filled = True
        in_legends = False
        body_spacing = {"after": "160", "line": str(layout.body_line), "lineRule": "auto"}
        for position, block in enumerate(blocks):
            kind = block.kind
            if kind != "para" and not references_filled:
                out.append(para_xml(self.ids, "", style="EndNoteBibliography"))
                references_filled = True
            if kind == "pagebreak":
                pending_break = True
                continue
            if kind == "heading":
                segments = parse_inline(block.text, self.where(block))
                text = plain(segments).strip()
                if not text:
                    raise TemplateError(f"{self.where(block)}: empty heading")
                self.stats["headings"] += 1
                page_break = pending_break
                pending_break = False
                if block.level == 1:
                    section = text
                    if _FIGURE_LEGENDS.match(text) and not in_legends:
                        in_legends = True
                        legend_headings = 0
                        out.append(para_xml(self.ids, "", spacing={"after": "0"},
                                            sect=sect_xml(layout, footer_rid)))
                        self.stats["sections"] = 2
                        page_break = False
                    if _REFERENCES.match(text):
                        references_filled = False
                elif in_legends:
                    legend_headings += 1
                    label = _FIGURE_LABEL.match(text)
                    legend_label = label.group(1) if label else ""
                    if legend_headings > 1 and self._group_has_picture(blocks, position):
                        page_break = True
                style = f"Heading{block.level}"
                out.append(para_xml(self.ids, runs_xml(segments), style=style, page_break=page_break))
                continue
            if kind == "picture":
                out.append(self._picture(block, legend_label, pending_break))
                pending_break = False
                continue
            if kind == "table":
                if pending_break:
                    out.append(para_xml(self.ids, '<w:r><w:br w:type="page"/></w:r>', spacing={"after": "0"}))
                    pending_break = False
                out.append(self._table(block))
                self.stats["tables"] += 1
                continue
            # paragraph
            segments = parse_inline(block.text, self.where(block))
            self.stats["paragraphs"] += 1
            page_break = pending_break
            pending_break = False
            upcoming = blocks[position + 1] if position + 1 < len(blocks) else None
            if _REFERENCES.match(section):
                references_filled = True
                out.append(para_xml(self.ids, runs_xml(segments), style="EndNoteBibliography",
                                    page_break=page_break))
            elif upcoming is not None and upcoming.kind == "table":
                # a paragraph directly above a table is its label: no indent, kept with the table
                out.append(para_xml(self.ids, runs_xml(segments), keep_next=True, page_break=page_break,
                                    spacing=body_spacing, jc="left"))
            elif in_legends:
                out.append(para_xml(self.ids, runs_xml(segments), style="FigureLegend", page_break=page_break))
            else:
                abstract = bool(_ABSTRACT.match(section))
                if abstract:
                    self.stats["abstract_paragraphs"] += 1
                style = "BodyTextNoIndent" if abstract or _back_matter(section, segments) else "BodyText"
                out.append(para_xml(self.ids, runs_xml(segments), style=style, page_break=page_break))
        if not references_filled:
            out.append(para_xml(self.ids, "", style="EndNoteBibliography"))
        if pending_break:
            out.append(para_xml(self.ids, '<w:r><w:br w:type="page"/></w:r>', spacing={"after": "0"}))
        if self.stats["abstract_paragraphs"] > 1:
            self.warnings.append(f"the Abstract has {self.stats['abstract_paragraphs']} paragraphs; the house "
                                 "style (and most journals) expect one")
        return "".join(out)

    @staticmethod
    def _group_has_picture(blocks, position) -> bool:
        for block in blocks[position + 1:]:
            if block.kind == "heading":
                return False
            if block.kind == "picture":
                return True
        return False

    # -- pictures -----------------------------------------------------------------------
    def _picture(self, block: Block, label: str, page_break: bool) -> str:
        path = Path(block.path)
        if not path.is_absolute():
            path = self.base / path
        try:
            data = path.read_bytes()
        except OSError as error:
            raise TemplateError(f"{self.where(block)}: cannot read picture {block.path}: {error}") from error
        try:
            ext, px_w, px_h = image_info(data)
        except TemplateError as error:
            raise TemplateError(f"{self.where(block)}: {block.path}: {error}") from error
        if not px_w or not px_h:
            raise TemplateError(f"{self.where(block)}: {block.path} has no size")
        width = block.width_emu or self.layout.text_width_emu
        if width > self.layout.text_width_emu:
            self.warnings.append(f"{self.where(block)}: {block.path} is wider than the text "
                                 f"({width / EMU['mm']:.0f} mm > {self.layout.text_width_emu / EMU['mm']:.0f} mm); "
                                 "scaled to the text width")
            width = self.layout.text_width_emu
        height = round(width * px_h / px_w)
        limit = self.layout.text_height_emu - 1440 * EMU_PER_TWIP
        if height > limit:
            self.warnings.append(f"{self.where(block)}: {block.path} is taller than the page allows; scaled down")
            width = round(width * limit / height)
            height = round(width * px_h / px_w)
        digest = hashlib.sha256(data).hexdigest()
        if digest in self.by_hash:
            rid = self.by_hash[digest]
        else:
            number = len(self.media) + 1
            target = f"media/image{number}.{ext}"
            rid = f"rId{100 + number}"
            self.media.append((f"word/{target}", data))
            self.image_rels.append((rid, target))
            self.by_hash[digest] = rid
        self.stats["pictures"] += 1
        docpr_id = self.stats["pictures"]
        name = label or f"Picture {docpr_id}"
        base_name, suffix = name, 2
        while name in self.picture_names:
            name = f"{base_name} ({suffix})"
            suffix += 1
        self.picture_names.add(name)
        descr = f' descr="{escape_attr(block.alt)}"' if block.alt else ""
        drawing = (
            '<w:r><w:rPr><w:noProof/></w:rPr><w:drawing>'
            '<wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{width}" cy="{height}"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
            f'<wp:docPr id="{docpr_id}" name="{escape_attr(name)}"{descr}/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{docpr_id}" name="{escape_attr(path.name)}"/><pic:cNvPicPr/>'
            f'</pic:nvPicPr><pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch>'
            f'</pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width}" cy="{height}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
            '</wp:inline></w:drawing></w:r>')
        return para_xml(self.ids, drawing, keep_next=True, page_break=page_break,
                        spacing={"after": "160", "line": "240", "lineRule": "auto"}, jc="center")

    # -- tables -------------------------------------------------------------------------
    def _table(self, block: Block) -> str:
        rows = [[parse_inline(cell, self.where(block)) for cell in row] for row in block.rows]
        columns = len(rows[0])
        total = self.layout.text_width
        lengths = [max(1, max(len(plain(row[c])) for row in rows)) for c in range(columns)]
        shares = [length / sum(lengths) for length in lengths]
        floor = min(0.15, 1.0 / columns)
        shares = [max(share, floor) for share in shares]
        shares = [share / sum(shares) for share in shares]
        widths = [int(total * share) for share in shares]
        widths[-1] += total - sum(widths)
        spacing = {"after": "0", "line": str(self.layout.body_line), "lineRule": "auto"}
        grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
        out = ['<w:tbl><w:tblPr><w:tblStyle w:val="ManuscriptTable"/>'
               f'<w:tblW w:w="{total}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
               '<w:tblLook w:val="0620" w:firstRow="1" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" '
               'w:noHBand="1" w:noVBand="1"/></w:tblPr>', f"<w:tblGrid>{grid}</w:tblGrid>"]
        for number, row in enumerate(rows):
            header = number == 0
            out.append("<w:tr>" + ("<w:trPr><w:tblHeader/></w:trPr>" if header else ""))
            for cell, width in zip(row, widths):
                content = runs_xml(cell, add=frozenset({"bold"}) if header else frozenset())
                out.append(f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/></w:tcPr>'
                           + para_xml(self.ids, content, spacing=spacing, jc="left") + "</w:tc>")
            out.append("</w:tr>")
        out.append("</w:tbl>")
        return "".join(out)


# -- title page ------------------------------------------------------------------------------

def load_title_page(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise TemplateError(f"{path}: cannot read title page JSON: {error}") from error
    return check_title_page(data, str(path))


def check_title_page(data: dict, name: str = "title_page.json") -> dict:
    if not isinstance(data, dict):
        raise TemplateError(f"{name}: expected a JSON object")
    problems = []
    if not str(data.get("title", "")).strip():
        problems.append("'title' is required")
    authors = data.get("authors")
    if not isinstance(authors, list) or not authors:
        problems.append("'authors' must be a non-empty list")
        authors = []
    lines, line_problems = affiliation_lines(data.get("affiliations") or {})
    problems.extend(line_problems)
    keys = {key for line in lines for key in line["keys"]}
    used = set()
    for number, author in enumerate(authors, 1):
        if not isinstance(author, dict) or not str(author.get("name", "")).strip():
            problems.append(f"author {number} needs a 'name'")
            continue
        for key in author.get("affiliations", []) or []:
            if str(key) not in keys:
                problems.append(f"author {author['name']!r}: affiliation {key} is not in 'affiliations'")
            used.add(str(key))
    unused = sorted(keys - used)
    if unused:
        problems.append(f"affiliation(s) {', '.join(unused)} are not used by any author")
    flagged = [a["name"] for a in authors if isinstance(a, dict) and a.get("corresponding")]
    blocks = data.get("corresponding") or []
    for block in blocks:
        if not isinstance(block, dict) or block.get("name") not in flagged:
            problems.append(f"corresponding-author block {block.get('name') if isinstance(block, dict) else block!r} "
                            "must use exactly the name of an author marked \"corresponding\": true "
                            f"(marked: {flagged or 'none'})")
    count = data.get("word_count", "auto")
    if not (count == "auto" or (isinstance(count, int) and not isinstance(count, bool) and count >= 0)):
        problems.append("'word_count' must be \"auto\" or a whole number")
    keywords = data.get("keywords", [])
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        problems.append("'keywords' must be a list of strings")
    if problems:
        raise TemplateError(f"{name}: " + "; ".join(problems))
    return data


def _marker_keys(segments) -> list[str]:
    keys = []
    for text, flags in segments:
        if "superscript" in flags:
            keys.extend(key for key in _KEY_SPLIT.split(text.strip()) if key)
    return keys


def affiliation_lines(affiliations) -> tuple[list[dict], list[str]]:
    """Affiliation lines in title-page order, and problems.

    'affiliations' is an object ({"1": "Department ..."}) or a list of lines. A line whose text
    carries <sup>n</sup> markers is inline: the numbers stay where they are written, after the unit
    names, and one line may define several numbers. In the object form the key must be one of the
    line's markers. A plain object entry (no markers) is the fallback: its key becomes a leading
    superscript. Lines are ordered by their first number. Each line is {"keys", "segments", "inline"}.
    """
    if isinstance(affiliations, dict):
        entries = [(str(key), value) for key, value in affiliations.items()]
    elif isinstance(affiliations, list):
        entries = [(None, value) for value in affiliations]
    else:
        return [], ["'affiliations' must be an object such as {\"1\": \"Department ...\"} or a list of lines "
                    "with <sup>n</sup> markers"]
    lines, problems = [], []
    for position, (key, value) in enumerate(entries, 1):
        label = f"affiliation {key}" if key is not None else f"affiliation line {position}"
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{label} must be non-empty text")
            continue
        try:
            segments = parse_inline(" ".join(value.split()), label)
        except TemplateError as error:
            problems.append(str(error))
            continue
        markers = _marker_keys(segments)
        if markers:
            if key is not None and key not in markers:
                problems.append(f"{label}: its <sup> markers ({', '.join(markers)}) do not include its key {key}")
            lines.append({"keys": markers, "segments": segments, "inline": True})
        elif key is None:
            problems.append(f"{label} has no <sup>n</sup> marker; in the list form every line writes its numbers "
                            "inline after the unit names")
        else:
            lines.append({"keys": [key], "segments": segments, "inline": False})
    seen: list[str] = []
    for line in lines:
        for key in line["keys"]:
            if key in seen:
                problems.append(f"affiliation {key} is defined more than once")
            else:
                seen.append(key)
    lines.sort(key=lambda line: (0, int(line["keys"][0]), 0) if line["keys"][0].isdigit()
               else (1, 0, seen.index(line["keys"][0])))
    return lines, problems


def title_page_xml(tp: dict, count_text: str, layout: Layout, ids: ParaIds,
                   scope: str = "excluding references and figure legends") -> tuple[str, list[str]]:
    """Title-page paragraphs in the house order, and warnings. `scope` fills the word-count parenthesis."""
    warnings = []
    line = str(layout.body_line)
    tight = {"after": "0", "line": line, "lineRule": "auto"}
    loose = {"after": "160", "line": line, "lineRule": "auto"}
    out = []

    def para(content, spacing=tight):
        out.append(para_xml(ids, content, spacing=spacing, jc="left"))

    title = " ".join(str(tp["title"]).split())
    para(runs_xml(parse_inline(title, "title"), add=frozenset({"bold"}), size=24), loose)
    lines, _problems = affiliation_lines(tp.get("affiliations") or {})
    pieces = []
    authors = tp["authors"]
    for number, author in enumerate(authors):
        if number:
            pieces.append(run_xml(" and " if number == len(authors) - 1 else ", "))
        pieces.append(runs_xml(parse_inline(" ".join(author["name"].split()), "author name")))
        marks = ",".join(str(k) for k in author.get("affiliations", []) or [])
        if author.get("corresponding"):
            marks += "*"
        if marks:
            pieces.append(run_xml(marks, frozenset({"superscript"})))
    para("".join(pieces), loose)
    for line in lines:
        lead = "" if line["inline"] else run_xml(line["keys"][0], frozenset({"superscript"}))
        para(lead + runs_xml(line["segments"]))
    blocks = tp.get("corresponding") or []
    if blocks:
        para("")
        label = "*Corresponding author:" if len(blocks) == 1 else "*Corresponding authors:"
        para(run_xml(label, frozenset({"italic", "underline"})))
        for block in blocks:
            address = " ".join(str(block.get("address", "")).split())
            para(run_xml(f"- {block['name']}." + (f" {address}" if address else "")))
            contact = []
            if block.get("phone"):
                contact.append(f"Tel.: {block['phone']}")
            if block.get("fax"):
                contact.append(f"Fax: {block['fax']}")
            if block.get("email"):
                contact.append(f"E-mail address: {block['email']}")
            if contact:
                para(run_xml("; ".join(contact)))
            if block.get("orcid"):
                para(run_xml(f"ORCID number: {block['orcid']}"))
    else:
        warnings.append("title page: no corresponding-author block")
    para("")
    keywords = [" ".join(k.split()) for k in tp.get("keywords") or [] if k.strip()]
    if keywords:
        text = ", ".join(keywords).rstrip(".")
        para(run_xml("Keywords:", frozenset({"bold"})) + run_xml(" " + text))
    else:
        warnings.append("title page: no keywords")
    para(run_xml(f"Word count: {count_text} words ({scope})"))
    running = " ".join(str(tp.get("running_title", "")).split())
    if running:
        para("")
        para(run_xml(f"Running title: {running}", frozenset({"bold"})))
    else:
        warnings.append("title page: no running title")
    return "".join(out), warnings


# -- package parts ------------------------------------------------------------------------------

def styles_xml(layout: Layout) -> str:
    font = escape_attr(layout.font)
    fonts = f'<w:rFonts w:ascii="{font}" w:eastAsia="{font}" w:hAnsi="{font}" w:cs="{font}"/>'
    size = f'<w:sz w:val="{layout.size}"/><w:szCs w:val="{layout.size}"/>'
    body = layout.body_line
    return (
        DECL + f'<w:styles {PART_NS}>'
        '<w:docDefaults><w:rPrDefault><w:rPr>' + fonts + size
        + f'<w:lang w:val="{layout.language}" w:eastAsia="en-US" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>'
        '<w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="240" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
        '</w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:jc w:val="both"/></w:pPr><w:rPr>' + fonts + size + '</w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
        '<w:next w:val="BodyText"/><w:uiPriority w:val="9"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/>'
        f'<w:spacing w:before="0" w:after="0" w:line="{body}" w:lineRule="auto"/><w:jc w:val="left"/>'
        '<w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:bCs/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr>'
        '</w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
        '<w:next w:val="BodyText"/><w:uiPriority w:val="9"/><w:unhideWhenUsed/><w:qFormat/><w:pPr><w:keepNext/>'
        f'<w:keepLines/><w:spacing w:before="0" w:after="160" w:line="{body}" w:lineRule="auto"/>'
        '<w:jc w:val="left"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:bCs/>' + size + '</w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/>'
        '<w:next w:val="BodyText"/><w:uiPriority w:val="9"/><w:unhideWhenUsed/><w:qFormat/><w:pPr><w:keepNext/>'
        f'<w:keepLines/><w:spacing w:before="0" w:after="160" w:line="{body}" w:lineRule="auto"/>'
        '<w:jc w:val="left"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:i/><w:iCs/>' + size + '</w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="BodyText"><w:name w:val="Body Text"/><w:basedOn w:val="Normal"/>'
        '<w:uiPriority w:val="1"/><w:qFormat/><w:pPr>'
        f'<w:spacing w:after="160" w:line="{body}" w:lineRule="auto"/><w:ind w:firstLine="720"/>'
        '<w:jc w:val="both"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:customStyle="1" w:styleId="BodyTextNoIndent">'
        '<w:name w:val="Body Text No Indent"/><w:basedOn w:val="BodyText"/><w:qFormat/><w:pPr>'
        '<w:ind w:firstLine="0"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:customStyle="1" w:styleId="FigureLegend"><w:name w:val="Figure Legend"/>'
        '<w:basedOn w:val="BodyTextNoIndent"/><w:qFormat/><w:pPr><w:ind w:firstLine="0"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:customStyle="1" w:styleId="EndNoteBibliographyTitle">'
        '<w:name w:val="EndNote Bibliography Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="0"/>'
        '<w:jc w:val="center"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:customStyle="1" w:styleId="EndNoteBibliography">'
        '<w:name w:val="EndNote Bibliography"/><w:basedOn w:val="Normal"/><w:pPr>'
        f'<w:spacing w:after="240" w:line="{layout.refs_line}" w:lineRule="auto"/><w:jc w:val="both"/></w:pPr>'
        '</w:style>'
        '<w:style w:type="paragraph" w:styleId="Footer"><w:name w:val="footer"/><w:basedOn w:val="Normal"/>'
        '<w:uiPriority w:val="99"/><w:unhideWhenUsed/><w:pPr><w:tabs><w:tab w:val="center" w:pos="4320"/>'
        '<w:tab w:val="right" w:pos="8640"/></w:tabs><w:spacing w:after="0" w:line="240" w:lineRule="auto"/>'
        '<w:jc w:val="center"/></w:pPr></w:style>'
        '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont">'
        '<w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/><w:semiHidden/><w:unhideWhenUsed/>'
        '</w:style>'
        '<w:style w:type="character" w:styleId="LineNumber"><w:name w:val="line number"/>'
        '<w:basedOn w:val="DefaultParagraphFont"/><w:uiPriority w:val="99"/><w:semiHidden/><w:unhideWhenUsed/>'
        '</w:style>'
        '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/>'
        '<w:uiPriority w:val="99"/><w:semiHidden/><w:unhideWhenUsed/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/>'
        '<w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>'
        '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
        '<w:style w:type="table" w:customStyle="1" w:styleId="ManuscriptTable"><w:name w:val="Manuscript Table"/>'
        '<w:basedOn w:val="TableNormal"/><w:uiPriority w:val="59"/><w:pPr>'
        f'<w:spacing w:after="0" w:line="{body}" w:lineRule="auto"/><w:jc w:val="left"/></w:pPr>'
        '<w:tblPr><w:tblBorders><w:top w:val="nil"/><w:left w:val="nil"/><w:bottom w:val="nil"/>'
        '<w:right w:val="nil"/><w:insideH w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders></w:tblPr>'
        '<w:tblStylePr w:type="firstRow"><w:rPr><w:b/><w:bCs/></w:rPr></w:tblStylePr></w:style>'
        '<w:style w:type="numbering" w:default="1" w:styleId="NoList"><w:name w:val="No List"/>'
        '<w:uiPriority w:val="99"/><w:semiHidden/><w:unhideWhenUsed/></w:style>'
        '</w:styles>')


def settings_xml(layout: Layout) -> str:
    # No w:trackRevisions (a new draft is not tracked) and no w:evenAndOddHeaders; schema order kept.
    return (DECL + f'<w:settings {PART_NS}><w:zoom w:percent="100"/><w:defaultTabStop w:val="720"/>'
            '<w:characterSpacingControl w:val="doNotCompress"/><w:compat>'
            '<w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/>'
            '<w:compatSetting w:name="overrideTableStyleFontSizeAndJustification" '
            'w:uri="http://schemas.microsoft.com/office/word" w:val="1"/>'
            '<w:compatSetting w:name="enableOpenTypeFeatures" w:uri="http://schemas.microsoft.com/office/word" '
            'w:val="1"/>'
            '<w:compatSetting w:name="doNotFlipMirrorIndents" w:uri="http://schemas.microsoft.com/office/word" '
            'w:val="1"/>'
            '<w:compatSetting w:name="differentiateMultirowTableHeaders" '
            'w:uri="http://schemas.microsoft.com/office/word" w:val="1"/></w:compat>'
            f'<w:themeFontLang w:val="{layout.language}"/><w:decimalSymbol w:val="."/><w:listSeparator w:val=","/>'
            '</w:settings>')


def footer_xml(ids: ParaIds) -> str:
    return (DECL + f'<w:ftr {PART_NS}><w:p w14:paraId="{ids.next()}" w14:textId="77777777"><w:pPr>'
            '<w:pStyle w:val="Footer"/><w:jc w:val="center"/></w:pPr><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> PAGE   \\* MERGEFORMAT </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:rPr><w:noProof/></w:rPr><w:t>1</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>')


def font_table_xml(layout: Layout) -> str:
    fonts = [("Times New Roman", "02020603050405020304", "roman"), ("Courier New", "02070309020205020404", "modern"),
             ("Arial", "020B0604020202020204", "swiss")]
    names = {name for name, _, _ in fonts}
    entries = [f'<w:font w:name="{name}"><w:panose1 w:val="{panose}"/><w:charset w:val="00"/>'
               f'<w:family w:val="{family}"/><w:pitch w:val="variable"/></w:font>' for name, panose, family in fonts]
    if layout.font not in names:
        entries.insert(0, f'<w:font w:name="{escape_attr(layout.font)}"><w:charset w:val="00"/>'
                          '<w:pitch w:val="variable"/></w:font>')
    return DECL + f'<w:fonts {PART_NS}>' + "".join(entries) + "</w:fonts>"


def core_xml(title: str, creator: str, stamp: str) -> str:
    return (DECL + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
            'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{escape_text(title)}</dc:title><dc:creator>{escape_text(creator)}</dc:creator>"
            f"<cp:lastModifiedBy>{escape_text(creator)}</cp:lastModifiedBy><cp:revision>1</cp:revision>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified></cp:coreProperties>')


def app_xml() -> str:
    return (DECL + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            "<Template>Normal.dotm</Template><TotalTime>0</TotalTime>"
            "<Application>scientific-manuscript msw new-manuscript</Application><DocSecurity>0</DocSecurity>"
            "<ScaleCrop>false</ScaleCrop><LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc>"
            "<HyperlinksChanged>false</HyperlinksChanged></Properties>")


def assemble(document: str, layout: Layout, footer: str, *, media=(), image_rels=(), title="", creator="",
             stamp: str) -> bytes:
    extensions = sorted({name.rsplit(".", 1)[-1] for name, _ in media})
    defaults = "".join(f'<Default Extension="{ext}" ContentType="{CONTENT_TYPES_BY_EXT[ext]}"/>' for ext in extensions)
    content_types = (
        DECL + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>' + defaults
        + f'<Override PartName="/word/document.xml" ContentType="{CT_WORD}document.main+xml"/>'
        f'<Override PartName="/word/styles.xml" ContentType="{CT_WORD}styles+xml"/>'
        f'<Override PartName="/word/settings.xml" ContentType="{CT_WORD}settings+xml"/>'
        f'<Override PartName="/word/fontTable.xml" ContentType="{CT_WORD}fontTable+xml"/>'
        f'<Override PartName="/word/footer1.xml" ContentType="{CT_WORD}footer+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.'
        'core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'extended-properties+xml"/></Types>')
    root_rels = (
        DECL + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{REL_TYPE}officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
        'core-properties" Target="docProps/core.xml"/>'
        f'<Relationship Id="rId3" Type="{REL_TYPE}extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>')
    images = "".join(f'<Relationship Id="{rid}" Type="{REL_TYPE}image" Target="{target}"/>'
                     for rid, target in image_rels)
    doc_rels = (
        DECL + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{REL_TYPE}styles" Target="styles.xml"/>'
        f'<Relationship Id="rId2" Type="{REL_TYPE}settings" Target="settings.xml"/>'
        f'<Relationship Id="rId3" Type="{REL_TYPE}fontTable" Target="fontTable.xml"/>'
        f'<Relationship Id="rId4" Type="{REL_TYPE}footer" Target="footer1.xml"/>' + images + '</Relationships>')
    parts = [("[Content_Types].xml", content_types), ("_rels/.rels", root_rels),
             ("word/document.xml", document), ("word/_rels/document.xml.rels", doc_rels),
             ("word/styles.xml", styles_xml(layout)), ("word/settings.xml", settings_xml(layout)),
             ("word/fontTable.xml", font_table_xml(layout)), ("word/footer1.xml", footer),
             ("docProps/core.xml", core_xml(title, creator, stamp)), ("docProps/app.xml", app_xml())]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in parts:
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, text.encode("utf-8"))
        for name, data in media:
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, data)
    return buffer.getvalue()


def document_xml(content: str, final_sect: str) -> str:
    return DECL + f"<w:document {DOC_NS}><w:body>{content}{final_sect}</w:body></w:document>"


def validate_package(blob: bytes) -> Package:
    """Parse the package and check XML, content types, relationships, fields and paragraph ids."""
    try:
        package = Package(blob)
    except (PackageError, zipfile.BadZipFile) as error:
        raise TemplateError(f"built package is not a valid .docx: {error}") from error
    problems = []
    names = set(package.names())
    for name in package.names():
        if name.endswith(".xml") or name.endswith(".rels"):
            try:
                ET.fromstring(package.parts[name])
                package.xml(name)
            except (ET.ParseError, XMLError) as error:
                problems.append(f"{name}: not well-formed: {error}")
                continue
        if name != "[Content_Types].xml" and package.content_type(name) is None:
            problems.append(f"no content type for {name}")
    for name in package.names():
        if not name.endswith(".rels"):
            continue
        owner = name.replace("_rels/", "")[:-5]
        for rel in package.xml(name).iter("Relationship"):
            target = rel.get("Target", "")
            target = target.lstrip("/") if name == "_rels/.rels" else package.resolve(owner, target)
            if target not in names:
                problems.append(f"{name}: {rel.get('Id')} target missing: {target}")
    if problems:
        raise TemplateError("built package failed validation: " + "; ".join(problems))
    doc = package.document
    check_namespaces(doc)
    for part in package.story_parts():
        fmap = map_fields(package.xml(part))
        if fmap.errors:
            raise TemplateError(f"{part}: unbalanced fields: {fmap.errors[:3]}")
    seen = set()
    for part in package.story_parts():
        for paragraph in iter_paragraphs(package.xml(part)):
            pid = paragraph.get("w14:paraId")
            if not pid or not re.fullmatch(r"[0-9A-F]{8}", pid) or int(pid, 16) >= 0x80000000 or pid in seen:
                raise TemplateError(f"{part}: missing, invalid or duplicate w14:paraId {pid!r}")
            seen.add(pid)
    XMLDoc(project(doc, "accept"))
    return package


# -- builds ----------------------------------------------------------------------------------------

@dataclass
class Built:
    blob: bytes
    word_count: int | None
    counts: dict | None
    stats: dict
    warnings: list
    layout: Layout


def _stamp(date: str | None) -> str:
    if date:
        return date if "T" in date else f"{date}T00:00:00Z"
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _creator(config: dict | None) -> str:
    author = (config or {}).get("author") or {}
    return str(author.get("name") or "")


def _profile_warnings(tp: dict, profile: dict, counts: dict | None, method: str) -> list[str]:
    rules = article_rules(profile)
    out = []
    title_max = (rules.get("title") or {}).get("max_chars")
    title = " ".join(str(tp.get("title", "")).split())
    if title_max and len(title) > title_max:
        out.append(f"title has {len(title)} characters (journal maximum {title_max})")
    short_max = (rules.get("short_title") or {}).get("max_chars")
    running = " ".join(str(tp.get("running_title", "")).split())
    if short_max and len(running) > short_max:
        out.append(f"running title has {len(running)} characters (journal maximum {short_max})")
    keywords_min = (rules.get("keywords") or {}).get("min")
    keywords = [k for k in tp.get("keywords") or [] if str(k).strip()]
    if keywords_min and len(keywords) < keywords_min:
        out.append(f"{len(keywords)} keyword(s) (journal minimum {keywords_min})")
    keywords_max = (rules.get("keywords") or {}).get("max")
    if keywords_max and len(keywords) > keywords_max:
        out.append(f"{len(keywords)} keywords (journal maximum {keywords_max})")
    if counts:
        abstract_max = (rules.get("abstract") or {}).get("max_words")
        if abstract_max and counts["abstract"] > abstract_max:
            out.append(f"abstract has {counts['abstract']} words (journal maximum {abstract_max})")
        limit = (rules.get("word_limit") or {}).get("max")
        total = counts[method]["total"]
        if limit and total > limit:
            out.append(f"{method} word count {format_count(total)} exceeds the journal limit {format_count(limit)}")
    return out


def build_manuscript(source, title_page, *, profile: dict | None = None, config: dict | None = None,
                     method: str = "house", date: str | None = None) -> Built:
    """Render manuscript.md + title_page.json into .docx bytes (not written)."""
    if method not in METHODS:
        raise TemplateError(f"method must be one of {METHODS}")
    source = Path(source)
    profile = profile if profile is not None else load_profile()
    tp = title_page if isinstance(title_page, dict) else load_title_page(title_page)
    tp = check_title_page(tp)
    try:
        text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise TemplateError(f"{source}: cannot read: {error}") from error
    blocks = parse_markdown(text, source.name)
    layout = layout_from(profile, config)
    ids = ParaIds("msw:" + hashlib.sha256((str(tp["title"]) + text).encode("utf-8")).hexdigest()[:16])
    footer_rid = "rId4"
    renderer = BodyRenderer(layout, ids, source.resolve().parent, source.name)
    body = renderer.render(blocks, footer_rid=footer_rid, page_break_first=layout.separate_title_page)
    scope, scope_warnings = scope_text(method, article_rules(profile))
    title, warnings = title_page_xml(tp, WORD_COUNT_SENTINEL, layout, ids, scope)
    warnings = warnings + scope_warnings + renderer.warnings
    footer = footer_xml(ids)
    final = sect_xml(layout, footer_rid, next_page=renderer.stats["sections"] == 2)
    draft = document_xml(title + body, final)
    stamp = _stamp(date)
    creator = _creator(config)

    def package_for(document: str) -> bytes:
        return assemble(document, layout, footer, media=renderer.media, image_rels=renderer.image_rels,
                        title=" ".join(str(tp["title"]).split()), creator=creator, stamp=stamp)

    counts = None
    try:
        counts = count_package(Package(package_for(draft)))
    except WordCountError as error:
        if tp.get("word_count", "auto") == "auto":
            raise TemplateError(f"cannot compute the {method} word count for the title page: {error}. Add the "
                                "missing heading, or set \"word_count\" in title_page.json to a number") from error
        warnings.append(f"word count not checked: {error}")
    stated = tp.get("word_count", "auto")
    if stated == "auto":
        stated = counts[method]["total"]
    elif counts and stated != counts[method]["total"]:
        warnings.append(f"title_page.json states {format_count(stated)} words; the {method} count is "
                        f"{format_count(counts[method]['total'])} (run msw.py wordcount --stamp-spec to correct it)")
    if draft.count(WORD_COUNT_SENTINEL) != 1:
        raise TemplateError("the word-count sentinel must appear exactly once (is it in the manuscript text?)")
    blob = package_for(draft.replace(WORD_COUNT_SENTINEL, format_count(stated)))
    package = validate_package(blob)
    if counts is not None:
        final_counts = count_package(package)
        if final_counts[method]["total"] != counts[method]["total"]:
            raise TemplateError("the word count changed after stamping the title page")
        if tp.get("word_count", "auto") == "auto" and final_counts["stated"]["value"] != stated:
            raise TemplateError("the title page does not state the computed word count")
    warnings += _profile_warnings(tp, profile, counts, method)
    stats = dict(renderer.stats)
    stats["placeholders"] = len(census(package.document)["placeholders"])
    stats["paragraphs_total"] = sum(1 for _ in iter_paragraphs(package.document))
    return Built(blob, stated, counts, stats, warnings, layout)


def build_title_page(title_page, *, word_count: int, profile: dict | None = None, config: dict | None = None,
                     date: str | None = None, method: str = "house") -> Built:
    """A standalone title-page .docx (for journals that want it as a separate file).

    `method` is the counting method behind `word_count`; it words the count's parenthesis."""
    if method not in METHODS:
        raise TemplateError(f"method must be one of {METHODS}")
    profile = profile if profile is not None else load_profile()
    tp = title_page if isinstance(title_page, dict) else load_title_page(title_page)
    tp = check_title_page(tp)
    layout = layout_from(profile, config)
    ids = ParaIds("msw-title:" + hashlib.sha256(str(tp["title"]).encode("utf-8")).hexdigest()[:16])
    scope, scope_warnings = scope_text(method, article_rules(profile))
    title, warnings = title_page_xml(tp, format_count(int(word_count)), layout, ids, scope)
    footer = footer_xml(ids)
    document = document_xml(title, sect_xml(layout, "rId4"))
    blob = assemble(document, layout, footer, title=" ".join(str(tp["title"]).split()), creator=_creator(config),
                    stamp=_stamp(date))
    validate_package(blob)
    warnings += scope_warnings + _profile_warnings(tp, profile, None, method)
    return Built(blob, int(word_count), None, {"paragraphs_total": title.count("<w:p ")}, warnings, layout)


def write_exclusive(path, blob: bytes) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as handle:
        handle.write(blob)
    return path


# -- command line ------------------------------------------------------------------------------------

def _context(start, profile_path):
    config = load_config(start)
    return config, load_profile(profile_path, config=config)


def _print_warnings(warnings):
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)


def cmd_new_manuscript(args) -> int:
    out = Path(args.out)
    separate = Path(args.separate_title_page) if args.separate_title_page else None
    for target in [out] + ([separate] if separate else []):
        if target.exists():
            print(f"ERROR: {target} already exists; choose a new file name", file=sys.stderr)
            return 2
    if separate and separate.resolve() == out.resolve():
        print("ERROR: --separate-title-page must differ from --out", file=sys.stderr)
        return 2
    try:
        config, profile = _context(Path(args.source).resolve().parent, args.profile)
        method = args.method or (config.get("word_count") or {}).get("method") or "house"
        method = method if method in METHODS else "house"
        built = build_manuscript(args.source, args.title_page, profile=profile, config=config, method=method,
                                 date=args.date)
        title = None
        if separate:
            title = build_title_page(args.title_page, word_count=built.word_count, profile=profile, config=config,
                                     date=args.date, method=method)
    except (TemplateError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    write_exclusive(out, built.blob)
    stats = built.stats
    print(f"wrote {out} ({len(built.blob):,} bytes)")
    print(f"  {stats['paragraphs_total']} paragraphs, {stats['headings']} headings, {stats['pictures']} picture(s), "
          f"{stats['tables']} table(s), {stats['sections']} section(s), {stats['placeholders']} citation "
          "placeholder(s)")
    if built.counts:
        counts = built.counts
        print(f"  word count on the title page: {format_count(built.word_count)} ({method}; house "
              f"{format_count(counts['house']['total'])}, strict {format_count(counts['strict']['total'])}, "
              f"abstract {counts['abstract']})")
    if title is not None:
        write_exclusive(separate, title.blob)
        print(f"wrote {separate} (separate title page)")
    elif built.layout.separate_title_page:
        print("NOTE: the journal profile asks for a separate title-page file; add --separate-title-page "
              "or run msw.py title-page", file=sys.stderr)
    _print_warnings(built.warnings)
    return 0


def cmd_title_page(args) -> int:
    out = Path(args.out)
    if out.exists():
        print(f"ERROR: {out} already exists; choose a new file name", file=sys.stderr)
        return 2
    try:
        config, profile = _context(Path(args.title_page).resolve().parent, args.profile)
        tp = load_title_page(args.title_page)
        count = args.word_count
        method = args.method or (config.get("word_count") or {}).get("method") or "house"
        method = method if method in METHODS else "house"
        if count is None and args.manuscript:
            count = count_package(Package(args.manuscript))[method]["total"]
        if count is None:
            stated = tp.get("word_count", "auto")
            if stated == "auto":
                raise TemplateError("give --manuscript (to count it) or --word-count, or set \"word_count\" in "
                                    "title_page.json")
            count = stated
        built = build_title_page(tp, word_count=count, profile=profile, config=config, date=args.date,
                                 method=method)
    except (TemplateError, WordCountError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    write_exclusive(out, built.blob)
    print(f"wrote {out} (title page; word count {format_count(count)}, {method} method)")
    _print_warnings(built.warnings)
    return 0


def register(subparsers):
    p = subparsers.add_parser("new-manuscript", help="build a house-style .docx from manuscript.md and "
                              "title_page.json (title page, sections, line numbers, word count)")
    p.add_argument("--source", required=True, help="manuscript.md (the drafting dialect)")
    p.add_argument("--title-page", required=True, help="title_page.json")
    p.add_argument("--out", required=True, help="new .docx (refused if it exists)")
    p.add_argument("--profile", help="journal profile JSON (default: the project's, else house defaults)")
    p.add_argument("--separate-title-page", metavar="DOCX", help="also write the title page as its own .docx")
    p.add_argument("--method", choices=METHODS, help="word-count method stamped on the title page "
                   "(default: manuscript.json word_count.method, else house)")
    p.add_argument("--date", help="ISO-8601 date for the document properties (default: now, UTC)")
    p.set_defaults(func=cmd_new_manuscript)

    p = subparsers.add_parser("title-page", help="build a standalone title-page .docx from title_page.json")
    p.add_argument("--title-page", required=True, help="title_page.json")
    p.add_argument("--out", required=True, help="new .docx (refused if it exists)")
    p.add_argument("--manuscript", help="take the word count from this manuscript .docx")
    p.add_argument("--word-count", type=int, help="state this word count instead")
    p.add_argument("--method", choices=METHODS, help="word-count method: counts --manuscript and words the "
                   "count's parenthesis (default: manuscript.json word_count.method, else house)")
    p.add_argument("--profile", help="journal profile JSON")
    p.add_argument("--date", help="ISO-8601 date for the document properties (default: now, UTC)")
    p.set_defaults(func=cmd_title_page)

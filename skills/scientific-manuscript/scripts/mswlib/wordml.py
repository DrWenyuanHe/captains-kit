"""WordprocessingML reading helpers: paragraphs, text, fields and formatting."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

from .xmltree import Element, XMLDoc, XMLError

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# Revision (tracked change) markup.
RUN_REVISIONS = {"w:ins", "w:del", "w:moveFrom", "w:moveTo"}
PROPERTY_CHANGES = {"w:rPrChange", "w:pPrChange", "w:sectPrChange", "w:tblPrChange", "w:trPrChange",
                    "w:tcPrChange", "w:tblGridChange", "w:tblPrExChange", "w:numberingChange"}
MOVE_RANGES = {"w:moveFromRangeStart", "w:moveFromRangeEnd", "w:moveToRangeStart", "w:moveToRangeEnd"}
CELL_REVISIONS = {"w:cellIns", "w:cellDel", "w:cellMerge"}
REVISION_ID_TAGS = RUN_REVISIONS | PROPERTY_CHANGES | MOVE_RANGES | CELL_REVISIONS | {
    "w:customXmlInsRangeStart", "w:customXmlInsRangeEnd", "w:customXmlDelRangeStart",
    "w:customXmlDelRangeEnd", "w:customXmlMoveFromRangeStart", "w:customXmlMoveFromRangeEnd",
    "w:customXmlMoveToRangeStart", "w:customXmlMoveToRangeEnd"}

# Zero-width markers that may sit between runs.
ZERO_WIDTH = {"w:bookmarkStart", "w:bookmarkEnd", "w:proofErr", "w:commentRangeStart",
              "w:commentRangeEnd", "w:permStart", "w:permEnd"} | MOVE_RANGES
SIMPLE_RUN_CHILDREN = {"w:rPr", "w:t", "w:tab", "w:br", "w:cr", "w:noBreakHyphen", "w:softHyphen",
                       "w:lastRenderedPageBreak", "w:sym"}
OBJECT_TAGS = {"w:drawing", "w:pict", "w:object", "mc:AlternateContent", "w:footnoteReference",
               "w:endnoteReference", "w:commentReference", "w:ptab", "w:fldChar", "w:instrText",
               "w:delInstrText", "w:annotationRef", "w:separator", "w:continuationSeparator",
               "w:footnoteRef", "w:endnoteRef", "w:pgNum", "w:dayShort", "w:monthShort", "w:yearShort",
               "w:dayLong", "w:monthLong", "w:yearLong", "w:contentPart", "w:ruby"}
OBJECT_CHAR = "￼"

FLAG_TAGS = {"w:b": "bold", "w:i": "italic", "w:strike": "strike", "w:dstrike": "strike",
             "w:caps": "caps", "w:smallCaps": "smallcaps", "w:vanish": "hidden"}


def check_namespaces(doc: XMLDoc):
    spaces = doc.namespaces()
    if spaces.get("w") != W_NS:
        raise XMLError("the WordprocessingML namespace is not bound to the prefix 'w'; "
                       "open and save the file in Word or LibreOffice first")


# -- text ---------------------------------------------------------------------------

# Symbol-font text stores Greek and math glyphs under Latin code points; decode them so readers,
# lint and the gate see the real characters. Dingbat fonts map to the private-use area instead.
SYMBOL_FONTS = {"symbol"}
PUA_FONTS = {"wingdings", "wingdings 2", "wingdings 3", "webdings", "zapf dingbats", "mt extra"}
_SYMBOL = dict(zip(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "\u03b1\u03b2\u03c7\u03b4\u03b5\u03c6\u03b3\u03b7\u03b9\u03d5\u03ba\u03bb\u03bc\u03bd\u03bf\u03c0\u03b8\u03c1"
    "\u03c3\u03c4\u03c5\u03d6\u03c9\u03be\u03c8\u03b6"
    "\u0391\u0392\u03a7\u0394\u0395\u03a6\u0393\u0397\u0399\u03d1\u039a\u039b\u039c\u039d\u039f\u03a0\u0398\u03a1"
    "\u03a3\u03a4\u03a5\u03c2\u03a9\u039e\u03a8\u0396"))
_SYMBOL.update({"\xb0": "°", "\xb1": "±", "\xa3": "≤", "\xb3": "≥", "\xb4": "×",
                "\xb8": "÷", "\xae": "→", "\xac": "←", "\xad": "↑", "\xaf": "↓",
                "\xb5": "∝", "\xb9": "≠", "\xbb": "≈", "\xa5": "∞", "\xd6": "√",
                "\xe5": "∑", "\xf2": "∫", "-": "−", "*": "∗", "\xb7": "•",
                "\xd7": "⋅", "\xa2": "′", "\xb2": "″", "\xba": "≡", "\xbe": "—"})


# Word's own text (Range.Text) reports Symbol-font characters in the private-use area
# (U+F020-U+F0FF); this table folds that form into the same Unicode the readers produce.
SYMBOL_PUA = {0xF000 + ord(k): v for k, v in _SYMBOL.items() if ord(k) < 256}


def font_kind(rpr: Element | None) -> str | None:
    """'symbol' or 'pua' when the run's direct font is a symbol or dingbat font."""
    if rpr is None:
        return None
    fonts = rpr.find("w:rFonts")
    if fonts is None:
        return None
    names = {(fonts.get(key) or "").strip().lower() for key in ("w:ascii", "w:hAnsi")} - {""}
    if names & SYMBOL_FONTS:
        return "symbol"
    if names & PUA_FONTS:
        return "pua"
    return None


def decode_font_text(text: str, kind: str | None) -> str:
    if kind == "symbol":
        return "".join(_SYMBOL.get(c, c) for c in text)
    if kind == "pua":
        return "".join(chr(0xF000 + ord(c)) if ord(c) < 256 else c for c in text)
    return text


def element_char(doc: XMLDoc, element: Element, *, deleted: bool, font: str | None = None) -> str | None:
    tag = element.tag
    if tag == "w:t" or (deleted and tag == "w:delText"):
        return decode_font_text(doc.text(element), font)
    if tag == "w:tab" or tag == "w:ptab":
        return "\t"
    if tag == "w:br":
        return "\f" if element.get("w:type") == "page" else "\n"
    if tag == "w:cr":
        return "\n"
    if tag == "w:noBreakHyphen":
        return "\u2011"
    if tag == "w:softHyphen":
        return "\u00ad"
    if tag == "w:sym":
        try:
            value = int(element.get("w:char", ""), 16)
        except ValueError:
            return OBJECT_CHAR
        low = value - 0xF000 if value >= 0xF000 else value
        if (element.get("w:font") or "").strip().lower() in SYMBOL_FONTS and 32 <= low < 256:
            return _SYMBOL.get(chr(low), chr(0xF000 + low))
        return chr(0xF000 + low) if 32 <= low < 256 else OBJECT_CHAR
    if tag in ("w:drawing", "w:pict", "w:object", "w:footnoteReference", "w:endnoteReference"):
        return OBJECT_CHAR
    return None


def paragraph_text(doc: XMLDoc, paragraph: Element, *, deleted: bool = False) -> str:
    """Visible text of one paragraph: field results but not field codes.

    Nested paragraphs (text boxes) and mc:Fallback copies are excluded. With
    `deleted=True`, w:delText is included (raw view of a tracked document).
    Symbol-font characters are decoded to Unicode.
    """
    out = []
    stack = [(child, None) for child in reversed(paragraph.elements())]
    while stack:
        node, font = stack.pop()
        tag = node.tag
        if tag in ("w:p", "mc:Fallback", "w:pPr", "w:rPr", "w:instrText", "w:delInstrText",
                   "w:rPrChange", "w:pPrChange", "w:fldData"):
            continue
        char = element_char(doc, node, deleted=deleted, font=font)
        if char is not None:
            out.append(char)
            continue
        if not deleted and tag in ("w:del", "w:moveFrom"):
            continue
        if tag == "w:r":
            font = font_kind(node.find("w:rPr"))
        stack.extend((child, font) for child in reversed(node.elements()))
    return "".join(out)


def iter_paragraphs(doc: XMLDoc):
    """All w:p in document order, excluding alternate-content fallbacks."""
    for element in doc.iter("w:p"):
        if not element.has_ancestor({"mc:Fallback"}):
            yield element


def paragraph_texts(doc: XMLDoc, *, deleted: bool = False) -> list[str]:
    return [paragraph_text(doc, p, deleted=deleted) for p in iter_paragraphs(doc)]


def para_id(paragraph: Element) -> str | None:
    return paragraph.get("w14:paraId")


def paragraph_style(paragraph: Element) -> str:
    ppr = paragraph.find("w:pPr")
    if ppr is not None:
        style = ppr.find("w:pStyle")
        if style is not None:
            return style.get("w:val", "")
    return ""


def mark_revision(paragraph: Element) -> str | None:
    """'ins' or 'del' when the paragraph mark itself is a tracked change."""
    ppr = paragraph.find("w:pPr")
    rpr = ppr.find("w:rPr") if ppr is not None else None
    if rpr is None:
        return None
    for child in rpr.elements():
        if child.tag in ("w:ins", "w:moveTo"):
            return "ins"
        if child.tag in ("w:del", "w:moveFrom"):
            return "del"
    return None


# -- formatting ----------------------------------------------------------------------

def _on(element: Element) -> bool:
    return element.get("w:val", "true").lower() not in ("0", "false", "off", "none")


def rpr_flags(rpr: Element | None) -> frozenset:
    """Character formatting flags that matter for scientific text."""
    if rpr is None:
        return frozenset()
    flags = set()
    for child in rpr.elements():
        tag = child.tag
        if tag in FLAG_TAGS and _on(child):
            flags.add(FLAG_TAGS[tag])
        elif tag == "w:vertAlign":
            value = child.get("w:val", "baseline")
            if value in ("superscript", "subscript"):
                flags.add(value)
        elif tag == "w:u" and child.get("w:val", "single") != "none":
            flags.add("underline")
        elif tag == "w:highlight" and child.get("w:val", "none") != "none":
            flags.add("highlight")
    return frozenset(flags)


def rpr_states(rpr: Element | None) -> dict:
    """Flags an rPr sets explicitly, on (True) or off (False)."""
    states = {}
    if rpr is None:
        return states
    for child in rpr.elements():
        tag = child.tag
        if tag in FLAG_TAGS:
            states[FLAG_TAGS[tag]] = _on(child)
        elif tag == "w:vertAlign":
            value = child.get("w:val", "baseline")
            states["superscript"], states["subscript"] = value == "superscript", value == "subscript"
        elif tag == "w:u":
            states["underline"] = child.get("w:val", "single") != "none"
        elif tag == "w:highlight":
            states["highlight"] = child.get("w:val", "none") != "none"
    return states


class StyleFlags:
    """Formatting flags a run inherits from the document defaults, its paragraph style and its
    character style (styles.xml, following basedOn). A later level overrides an earlier one; Word's
    rule that some flags toggle when both styles set them is not modelled."""

    def __init__(self, package):
        self.styles: dict = {}
        self.defaults: dict = {}
        self.default_paragraph = None
        data = package.parts.get("word/styles.xml") if package is not None else None
        if not data:
            return
        doc = XMLDoc(data)
        for defaults in doc.iter("w:rPrDefault"):
            self.defaults = rpr_states(defaults.find("w:rPr"))
        for style in doc.iter("w:style"):
            sid = style.get("w:styleId")
            based = style.find("w:basedOn")
            self.styles[sid] = (based.get("w:val") if based is not None else None, rpr_states(style.find("w:rPr")))
            if style.get("w:type") == "paragraph" and _on_value(style.get("w:default")):
                self.default_paragraph = sid

    def _resolve(self, sid, seen=()) -> dict:
        if not sid or sid not in self.styles or sid in seen:
            return {}
        based, states = self.styles[sid]
        return {**self._resolve(based, seen + (sid,)), **states}

    def inherited(self, paragraph_style: str | None, run_style: str | None) -> frozenset:
        states = {**self.defaults, **self._resolve(paragraph_style or self.default_paragraph),
                  **self._resolve(run_style)}
        return frozenset(flag for flag, on in states.items() if on)


def _on_value(value) -> bool:
    return value is not None and str(value).lower() not in ("0", "false", "off")


def run_flags(run: Element) -> frozenset:
    return rpr_flags(run.find("w:rPr"))


def formatted_segments(doc: XMLDoc, paragraph: Element) -> list[tuple[str, frozenset]]:
    """(text, flags) segments of visible text; adjacent equal flags are merged."""
    segments: list[list] = []
    for run in paragraph.iter("w:r"):
        owner = next((a for a in run.ancestors() if a.tag == "w:p"), None)
        if owner is not paragraph or run.has_ancestor({"w:del", "w:moveFrom", "mc:Fallback"}):
            continue
        flags = run_flags(run)
        font = font_kind(run.find("w:rPr"))
        text = "".join(c for c in (element_char(doc, e, deleted=False, font=font) for e in run.elements()) if c)
        if not text:
            continue
        if segments and segments[-1][1] == flags:
            segments[-1][0] += text
        else:
            segments.append([text, flags])
    return [(t, f) for t, f in segments]


# -- fields --------------------------------------------------------------------------

@dataclass(eq=False)
class Field:
    begin: Element | None = None
    separate: Element | None = None
    end: Element | None = None
    simple: Element | None = None
    instr: str = ""
    deleted_instr: bool = False
    data_chunks: list = field(default_factory=list)
    children: list = field(default_factory=list)
    parent: "Field | None" = None
    result: str = ""
    first_paragraph: int = -1
    last_paragraph: int = -1

    @property
    def kind(self) -> str:
        text = self.instr.strip()
        for prefix in ("ADDIN EN.CITE.DATA", "ADDIN EN.CITE", "ADDIN EN.REFLIST", "HYPERLINK", "PAGEREF",
                       "REF", "TOC", "SEQ", "PAGE", "NUMPAGES"):
            if text.upper().startswith(prefix):
                return prefix.replace("ADDIN ", "")
        return text.split(" ", 1)[0].upper() if text else ""

    @property
    def start(self) -> int:
        return (self.begin or self.simple).start

    @property
    def stop(self) -> int:
        return (self.end or self.simple).end


@dataclass
class FieldMap:
    fields: list            # top-level fields in document order
    all_fields: list        # every field, pre-order
    run_field: dict         # id(run element) -> top-level Field containing it
    errors: list


def map_fields(doc: XMLDoc) -> FieldMap:
    """Walk fldChar events over the whole part in document order."""
    paragraphs = {id(p): index for index, p in enumerate(iter_paragraphs(doc))}
    stack: list[Field] = []
    top: list[Field] = []
    every: list[Field] = []
    run_field: dict = {}
    errors: list = []
    simple_ranges: list = []   # (start, end, Field) for fldSimple
    current_paragraph = -1
    for element in doc.elements:
        tag = element.tag
        if tag == "w:p" and id(element) in paragraphs:
            current_paragraph = paragraphs[id(element)]
        if tag == "w:r":
            owner = stack[0] if stack else None
            if owner is None and simple_ranges:
                for start, end, simple in simple_ranges:
                    if start <= element.start < end:
                        owner = simple
                        break
            if owner is None and any(c.tag in ("w:fldChar", "w:instrText", "w:delInstrText") for c in element.elements()):
                owner = "pending"
            if owner is not None:
                run_field[id(element)] = owner
        if tag == "w:fldChar":
            kind = element.get("w:fldCharType")
            if kind == "begin":
                item = Field(begin=element, first_paragraph=current_paragraph,
                             data_chunks=[doc.text(d) for d in element.findall("w:fldData")])
                item.parent = stack[-1] if stack else None
                (stack[-1].children if stack else top).append(item)
                every.append(item)
                stack.append(item)
            elif kind == "separate":
                if not stack:
                    errors.append(f"field separator outside a field at byte {element.start}")
                else:
                    stack[-1].separate = element
            elif kind == "end":
                if not stack:
                    errors.append(f"field end outside a field at byte {element.start}")
                else:
                    item = stack.pop()
                    item.end = element
                    item.last_paragraph = current_paragraph
        elif tag in ("w:instrText", "w:delInstrText") and stack:
            stack[-1].instr += doc.text(element)
            if tag == "w:delInstrText":
                stack[-1].deleted_instr = True
        elif tag == "w:fldSimple":
            item = Field(simple=element, instr=element.get("w:instr", ""), first_paragraph=current_paragraph,
                         last_paragraph=current_paragraph)
            item.parent = stack[-1] if stack else None
            (stack[-1].children if stack else top).append(item)
            every.append(item)
            simple_ranges.append((element.start, element.end, item if not stack else stack[0]))
        elif tag in ("w:t", "w:delText") and stack:
            text = doc.text(element)
            for item in stack:
                if item.separate is not None:
                    item.result += text
    for item in stack:
        errors.append(f"unclosed {item.kind or 'field'} starting at byte {item.start} "
                      "(bibliography boundary or truncated field?)")
    # Runs that open a field belong to the field they open.
    by_begin = {id(f.begin): f for f in every if f.begin is not None}
    for element in doc.iter("w:r"):
        if run_field.get(id(element)) == "pending":
            owner = None
            for child in element.elements():
                if child.tag == "w:fldChar" and id(child) in by_begin:
                    owner = by_begin[id(child)]
                    while owner.parent is not None:
                        owner = owner.parent
                    break
            if owner is None:
                owner = top[-1] if top else None
            if owner is None:
                del run_field[id(element)]
            else:
                run_field[id(element)] = owner
    return FieldMap(top, every, run_field, errors)


# -- EndNote payloads ---------------------------------------------------------------

_WS = re.compile(r"\s+")


def endnote_payload(item: Field) -> bytes | None:
    """The <EndNote> XML of a top-level EN.CITE field (inline, single or multi-chunk)."""
    data_children = [c for c in item.children if c.kind == "EN.CITE.DATA"]
    try:
        if data_children and any(c.data_chunks for c in data_children):
            raw = b"".join(base64.b64decode(_WS.sub("", chunk)) for c in data_children for chunk in c.data_chunks)
        elif item.data_chunks:
            raw = b"".join(base64.b64decode(_WS.sub("", chunk)) for chunk in item.data_chunks)
        elif "<EndNote>" in item.instr:
            text = item.instr
            raw = text[text.index("<EndNote>"):text.rindex("</EndNote>") + len("</EndNote>")].encode("utf-8")
        else:
            return None
    except (ValueError, base64.binascii.Error):
        return None
    return raw.rstrip(b"\x00")

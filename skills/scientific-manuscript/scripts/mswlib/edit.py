"""Tracked-change edit engine.

A paragraph is modelled as a sequence of cells: editable characters from plain
runs (including runs inside an earlier, unaccepted insertion) and atoms that may
not be edited (fields such as EndNote citations, pictures, links, content
controls). Edits are expressed on the paragraph's accepted text and rebuilt as
minimal w:del/w:ins runs that carry the source run's properties. Everything
outside the edited runs keeps its original bytes.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import html
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import comments as comment_threads
from .docx import (COMMENTS_REL, IMAGE_REL, MAIN_PART, MEDIA_TYPES, Package, add_relationship,
                   ensure_content_type, sha256_bytes)
from .pathutil import fs
from .textdiff import ATOM, FORMAT_TAG, AtomError, diff, parse_markup, render_markup
from .wordml import (SIMPLE_RUN_CHILDREN, ZERO_WIDTH, StyleFlags, check_namespaces, decode_font_text,
                     element_char, font_kind, iter_paragraphs, map_fields, paragraph_style, rpr_flags)
from .xmltree import Element, Text, XMLDoc, XMLError, apply_splices, escape_attr, escape_text

RPR_ORDER = ["w:ins", "w:del", "w:moveFrom", "w:moveTo", "w:rStyle", "w:rFonts", "w:b", "w:bCs", "w:i", "w:iCs",
             "w:caps", "w:smallCaps", "w:strike", "w:dstrike", "w:outline", "w:shadow", "w:emboss", "w:imprint",
             "w:noProof", "w:snapToGrid", "w:vanish", "w:webHidden", "w:color", "w:spacing", "w:w", "w:kern",
             "w:position", "w:sz", "w:szCs", "w:highlight", "w:u", "w:effect", "w:bdr", "w:shd", "w:fitText",
             "w:vertAlign", "w:rtl", "w:cs", "w:em", "w:lang", "w:eastAsianLayout", "w:specVanish", "w:oMath",
             "w:rPrChange"]
FLAG_ELEMENTS = {"bold": ("w:b", "w:bCs"), "italic": ("w:i", "w:iCs"), "underline": ("w:u",),
                 "strike": ("w:strike",), "caps": ("w:caps",), "smallcaps": ("w:smallCaps",),
                 "highlight": ("w:highlight",), "superscript": ("w:vertAlign",), "subscript": ("w:vertAlign",)}
FLAG_MARKUP_XML = {"bold": b"<w:b/>", "italic": b"<w:i/>", "underline": b'<w:u w:val="single"/>',
                   "strike": b"<w:strike/>", "caps": b"<w:caps/>", "smallcaps": b"<w:smallCaps/>",
                   "superscript": b'<w:vertAlign w:val="superscript"/>',
                   "subscript": b'<w:vertAlign w:val="subscript"/>'}
# Explicit "off" values, written when a flag to be removed comes from a style rather than the run.
FLAG_OFF_XML = {"bold": ("w:b", b'<w:b w:val="0"/>'), "italic": ("w:i", b'<w:i w:val="0"/>'),
                "underline": ("w:u", b'<w:u w:val="none"/>'), "strike": ("w:strike", b'<w:strike w:val="0"/>'),
                "caps": ("w:caps", b'<w:caps w:val="0"/>'), "smallcaps": ("w:smallCaps", b'<w:smallCaps w:val="0"/>'),
                "highlight": ("w:highlight", b'<w:highlight w:val="none"/>'),
                "superscript": ("w:vertAlign", b'<w:vertAlign w:val="baseline"/>'),
                "subscript": ("w:vertAlign", b'<w:vertAlign w:val="baseline"/>')}
MANAGED_FLAGS = ("italic", "bold", "superscript", "subscript", "underline", "strike", "highlight")
FORMAT_FLAGS = MANAGED_FLAGS + ("caps", "smallcaps")
HIGHLIGHT_COLOURS = {"yellow", "green", "cyan", "magenta", "blue", "red", "darkBlue", "darkCyan", "darkGreen",
                     "darkMagenta", "darkRed", "darkYellow", "darkGray", "lightGray", "black"}
MARKUP_FLAGS = ("italic", "bold", "superscript", "subscript", "underline")


COMMENTS_PART = "word/comments.xml"


class EditError(ValueError):
    pass


# -- identifiers ---------------------------------------------------------------------

class Revisions:
    """Revision ids, author and date for one build."""

    def __init__(self, base: int, author: str, date: str):
        self.next_id = base
        self.author = author
        self.date = date
        self.used: list[int] = []

    def take(self) -> int:
        self.next_id += 1
        self.used.append(self.next_id)
        return self.next_id

    def attrs(self) -> bytes:
        return (f'w:id="{self.take()}" w:author="{escape_attr(self.author)}" '
                f'w:date="{self.date}"').encode("utf-8")


def existing_max_id(package: Package) -> int:
    top = 0
    for part in package.story_parts():
        for match in re.finditer(rb'\bw:id="(\d+)"', package.parts[part]):
            top = max(top, int(match.group(1)))
    return top


def default_id_base(package: Package) -> int:
    top = existing_max_id(package)
    return max(10000, (top // 10000 + 1) * 10000)


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- paragraph model -----------------------------------------------------------------

@dataclass(eq=False)
class Item:
    kind: str                     # run | atom | zero | invisible
    start: int
    end: int
    element: Element | None = None
    ctx: Element | None = None    # earlier, unaccepted w:ins that contains this run
    marker: str = ""
    display: str = ""
    owner: object = None          # field owner while classifying


@dataclass(eq=False)
class Cell:
    item: Item
    char: str
    child: Element | None = None
    offset: int = 0
    atom: bool = False
    flags: frozenset = frozenset()
    raw: str = ""                 # stored character when a symbol font displays it differently


def _simple_run(run: Element) -> bool:
    return all(c.tag in SIMPLE_RUN_CHILDREN for c in run.elements())


def _visible(doc: XMLDoc, element) -> str:
    if isinstance(element, Text):
        return ""
    out = []
    stack = [(element, None)]
    while stack:
        node, font = stack.pop()
        if node.tag in ("w:del", "w:moveFrom", "w:pPr", "w:rPr", "w:instrText", "w:delInstrText", "mc:Fallback",
                        "w:fldData", "w:p") and node is not element:
            continue
        char = element_char(doc, node, deleted=False, font=font)
        if char is not None:
            out.append(char)
            continue
        if node.tag == "w:r":
            font = font_kind(node.find("w:rPr"))
        stack.extend((child, font) for child in reversed(node.elements()))
    return "".join(out)


class ParagraphModel:
    def __init__(self, doc: XMLDoc, fmap, paragraph: Element, index: int):
        self.doc = doc
        self.paragraph = paragraph
        self.index = index
        self.items: list[Item] = []
        self.cells: list[Cell] = []
        self._classify(fmap)
        self._cells()

    # -- construction ---------------------------------------------------------------
    def _owner(self, fmap, element) -> object:
        if isinstance(element, Text):
            return None
        for run in element.iter("w:r"):
            owner = fmap.run_field.get(id(run))
            if owner is not None:
                return owner
        return None

    def _classify(self, fmap):
        raw: list[Item] = []
        for child in self.paragraph.children:
            if isinstance(child, Text):
                raw.append(Item("zero", child.start, child.end))
                continue
            tag = child.tag
            if tag == "w:pPr":
                continue
            owner = self._owner(fmap, child)
            if tag in ZERO_WIDTH:
                raw.append(Item("zero", child.start, child.end, child))
            elif owner is not None:
                raw.append(Item("field", child.start, child.end, child, owner=owner))
            elif tag == "w:r":
                raw.append(Item("run" if _simple_run(child) else "atom", child.start, child.end, child))
            elif tag == "w:ins" and all(isinstance(c, Element) and c.tag == "w:r" for c in child.children) \
                    and child.children:
                for run in child.children:
                    raw.append(Item("run" if _simple_run(run) else "atom", run.start, run.end, run, ctx=child))
            elif tag in ("w:del", "w:moveFrom"):
                raw.append(Item("invisible", child.start, child.end, child))
            else:
                raw.append(Item("atom", child.start, child.end, child))
        # A field becomes one atom spanning from its first to its last element in this paragraph.
        spans = {}
        for position, item in enumerate(raw):
            if item.kind == "field":
                first, _ = spans.get(id(item.owner), (position, position))
                spans[id(item.owner)] = (first, position)
        covered = {}
        for first, last in spans.values():
            for position in range(first, last + 1):
                covered[position] = (first, last)
        items: list[Item] = []
        position = 0
        while position < len(raw):
            if position in covered:
                first, last = covered[position]
                elements = [it for it in raw[first:last + 1]]
                display = "".join(_visible(self.doc, it.element) for it in elements
                                  if it.element is not None and it.kind != "invisible")
                items.append(Item("atom", elements[0].start, elements[-1].end, None, marker="F", display=display))
                position = last + 1
                continue
            item = raw[position]
            if item.kind == "atom":
                element = item.element
                item.display = _visible(self.doc, element)
                has_object = any(e.tag in ("w:drawing", "w:pict", "w:object") for e in element.iter())
                item.marker = "D" if has_object else "X"
            items.append(item)
            position += 1
        counters = {}
        for item in items:
            if item.kind == "atom":
                counters[item.marker] = counters.get(item.marker, 0) + 1
                item.marker = f"⟦{item.marker}{counters[item.marker]}⟧"
        self.items = items

    def _cells(self):
        for item in self.items:
            if item.kind == "atom":
                self.cells.append(Cell(item, item.display, atom=True))
            elif item.kind == "run":
                rpr = item.element.find("w:rPr")
                flags = rpr_flags(rpr)
                font = font_kind(rpr)
                for child in item.element.elements():
                    if child.tag == "w:t":
                        for offset, char in enumerate(self.doc.text(child)):
                            shown = decode_font_text(char, font)
                            self.cells.append(Cell(item, shown, child, offset, flags=flags,
                                                   raw=char if shown != char else ""))
                    elif child.tag in ("w:rPr", "w:lastRenderedPageBreak"):
                        continue
                    else:
                        char = element_char(self.doc, child, deleted=False)
                        if char is not None:
                            self.cells.append(Cell(item, char, child, 0, flags=flags))

    # -- views ----------------------------------------------------------------------
    @property
    def plain(self) -> str:
        return "".join(c.char for c in self.cells)

    def marked(self, markup: bool = True) -> str:
        text = []
        flags = []
        for cell in self.cells:
            piece = cell.item.marker if cell.atom else cell.char
            text.append(piece)
            flags.extend([frozenset() if cell.atom else cell.flags] * len(piece))
        joined = "".join(text)
        return render_markup(joined, flags) if markup else joined

    def marked_map(self) -> list[int]:
        """Cell index for every character of marked(markup=False), plus a final sentinel."""
        out = []
        for index, cell in enumerate(self.cells):
            out.extend([index] * len(cell.item.marker if cell.atom else cell.char))
        out.append(len(self.cells))
        return out

    def plain_map(self) -> list[int]:
        out = []
        for index, cell in enumerate(self.cells):
            out.extend([index] * len(cell.char))
        out.append(len(self.cells))
        return out

    def atoms(self) -> list[dict]:
        return [{"marker": c.item.marker, "text": c.item.display} for c in self.cells if c.atom]

    def text_hash(self) -> str:
        return hashlib.sha256(self.marked(markup=True).encode("utf-8")).hexdigest()[:16]


# -- operations ----------------------------------------------------------------------

@dataclass(eq=False)
class Op:
    start: int                     # first cell (deleted, formatted or anchored)
    end: int                       # cell after the range
    insert: str = ""
    insert_flags: list | None = None     # authoritative per-character flags for inserted text
    fmt_set: tuple = ()
    fmt_unset: tuple = ()
    comment: dict | None = None
    replacement_run: bytes | None = None  # picture swap: new run for an atom cell
    rpr_add: bytes = b""                  # raw run properties added to inserted or formatted text
    kind: str = "text"                    # text | format | comment | swap
    spec: dict = field(default_factory=dict)

    @property
    def is_format(self) -> bool:
        return self.kind == "format"


def _end_cell(pmap: list, start: int, end: int) -> int:
    """Cell after plain[start:end]. Taken from the range's last character, so an atom that shows no
    text (a comment reference) just after the range stays outside it."""
    return pmap[end - 1] + 1 if end > start else pmap[end]


def _check_boundary(model: ParagraphModel, pmap: list, position: int, where: str):
    """A plain-text position must not fall inside the displayed text of an atom."""
    if 0 < position < len(pmap) - 1 and pmap[position - 1] == pmap[position] and model.cells[pmap[position]].atom:
        cell = model.cells[pmap[position]]
        raise EditError(f"{where}: the edit falls inside {cell.item.marker} ({cell.item.display[:30]!r}); "
                        "fields, citations and the bibliography are not edited as text")


def _refine(model: ParagraphModel, start: int, end: int, text: str, flags):
    """Trim characters shared by the deleted and inserted text when the deleted span mixes formatting
    (for example "m" + superscript "2"), so "kg/m2" -> "kg/cm2" keeps the superscript 2."""
    cells = model.cells[start:end]
    visible = [c for c in cells if not c.atom and c.char.strip()]
    if len({c.flags for c in visible}) < 2 or not text:
        return start, end, text, flags
    old = "".join(c.char for c in cells)
    prefix = 0
    while prefix < min(len(old), len(text)) and old[prefix] == text[prefix]:
        prefix += 1
    suffix = 0
    while (suffix < min(len(old), len(text)) - prefix and old[len(old) - 1 - suffix] == text[len(text) - 1 - suffix]):
        suffix += 1
    new_text = text[prefix:len(text) - suffix]
    new_flags = flags[prefix:len(text) - suffix] if flags is not None else None
    return start + prefix, end - suffix, new_text, new_flags


def _check_editable(model: ParagraphModel, start: int, end: int, where: str):
    for cell in model.cells[start:end]:
        if cell.atom:
            raise EditError(f"{where}: the edit would change {cell.item.marker} ({cell.item.display[:30]!r}); "
                            "fields, citations and pictures are not edited as text")


def _locate(plain: str, spec: dict, where: str) -> tuple[int, int]:
    before, old, after = spec.get("before", ""), spec.get("old", ""), spec.get("after", "")
    needle = before + old + after
    if not needle:
        raise EditError(f"{where}: give 'before', 'old' or 'after' to locate the edit")
    count = plain.count(needle)
    if count != 1:
        raise EditError(f"{where}: context {needle[:60]!r} found {count} times in the paragraph (need exactly 1)")
    start = plain.index(needle) + len(before)
    return start, start + len(old)


def ops_for_replace(model: ParagraphModel, spec: dict, where: str) -> list[Op]:
    plain = model.plain
    start, end = _locate(plain, spec, where)
    new = spec.get("new", "")
    authoritative = bool(FORMAT_TAG.search(new))
    new_text, new_flags = parse_markup(new)
    old_text = plain[start:end]
    pmap = model.plain_map()
    ops = []
    _check_boundary(model, pmap, start, where)
    _check_boundary(model, pmap, end, where)
    for change in diff(old_text, new_text, spaces="exact"):
        _check_boundary(model, pmap, start + change.old_start, where)
        _check_boundary(model, pmap, start + change.old_end, where)
        cs = pmap[start + change.old_start]
        ce = _end_cell(pmap, start + change.old_start, start + change.old_end)
        _check_editable(model, cs, ce, where)
        inserted = new_text[change.new_start:change.new_end]
        flags = new_flags[change.new_start:change.new_end] if authoritative else None
        cs, ce, inserted, flags = _refine(model, cs, ce, inserted, flags)
        if cs == ce and not inserted:
            continue
        ops.append(Op(cs, ce, inserted, flags, rpr_add=spec.get("rpr_add", "").encode("utf-8"), spec=spec))
    if not ops:
        raise EditError(f"{where}: 'old' and 'new' are the same text")
    return ops


def ops_for_rewrite(model: ParagraphModel, spec: dict, where: str) -> list[Op]:
    expected_hash = spec.get("source_hash")
    if expected_hash and expected_hash != model.text_hash():
        raise EditError(f"{where}: the paragraph changed since it was extracted (stale rewrite)")
    old_marked = model.marked(markup=False)
    old_markup = model.marked(markup=True)
    new_text, new_flags = parse_markup(spec["text"])
    if not new_text.strip() and old_marked.strip():
        raise EditError(f"{where}: the rewrite is empty, which would delete all of the paragraph's text; use "
                        "delete_paragraph (or delete) to remove text on purpose")
    _, old_flags_markup = parse_markup(old_markup)
    try:
        changes = diff(old_marked, new_text, spaces="exact" if spec.get("exact_spaces") else "keep")
    except AtomError as error:
        raise EditError(f"{where}: {error}") from error
    if not spec.get("allow_reattach"):
        _check_reattachment(old_marked, new_text, changes, where)
    mmap = model.marked_map()
    ops = []
    for change in changes:
        cs, ce = mmap[change.old_start], mmap[change.old_end]
        _check_editable(model, cs, ce, where)
        inserted = new_text[change.new_start:change.new_end]
        cs, ce, inserted, flags = _refine(model, cs, ce, inserted, new_flags[change.new_start:change.new_end])
        if cs == ce and not inserted:
            continue
        ops.append(Op(cs, ce, inserted, flags, spec=spec))
    if spec.get("apply_formatting", False):
        ops.extend(_format_ops_from_markup(model, old_marked, old_flags_markup, new_text, new_flags, changes, spec))
    return ops


def _segment_of(text: str, position: int) -> int:
    return sum(1 for m in ATOM.finditer(text) if m.end() <= position)


def _check_reattachment(old: str, new: str, changes, where: str):
    """Refuse text moved from one side of an atom to the other (a citation re-attached to another clause)."""
    removed, added = [], []
    for change in changes:
        deleted = old[change.old_start:change.old_end].strip(" ,.;:")
        inserted = new[change.new_start:change.new_end]
        if deleted:
            removed.append((_segment_of(old, change.old_start), deleted))
        if inserted.strip():
            added.append((_segment_of(new, change.new_start), inserted))
    for segment, text in removed:
        if len(text) < 6 and len(text.split()) < 2:
            continue
        for other, inserted in added:
            if other != segment and text in inserted:
                raise EditError(f"{where}: the rewrite moves {text[:40]!r} across a citation or other atom, which "
                                "re-attaches the citation to different text; keep the text on the same side "
                                "(or set allow_reattach after checking the citation still supports it)")


def _format_ops_from_markup(model, old_text, old_flags, new_text, new_flags, changes, spec) -> list[Op]:
    """Formatting-only differences in unchanged stretches become tracked formatting changes."""
    ops = []
    mmap = model.marked_map()
    old_pos = new_pos = 0
    bounds = [(c.old_start, c.old_end, c.new_start, c.new_end) for c in changes] + [(len(old_text), len(old_text),
                                                                                       len(new_text), len(new_text))]
    for o1, o2, n1, n2 in bounds:
        length = o1 - old_pos
        run_start = None
        run_set = run_unset = None
        for k in range(length + 1):
            if k < length:
                a = frozenset(f for f in old_flags[old_pos + k] if f in MARKUP_FLAGS)
                b = frozenset(f for f in new_flags[new_pos + k] if f in MARKUP_FLAGS)
                cell = model.cells[mmap[old_pos + k]]
                differs = a != b and not cell.atom and old_text[old_pos + k].strip()
            else:
                differs = False
                a = b = frozenset()
            key = (tuple(sorted(b - a)), tuple(sorted(a - b))) if differs else None
            if run_start is not None and key != (run_set, run_unset):
                ops.append(Op(mmap[old_pos + run_start], mmap[old_pos + k], fmt_set=run_set, fmt_unset=run_unset,
                              kind="format", spec=spec))
                run_start = None
            if key is not None and run_start is None:
                run_start, (run_set, run_unset) = k, key
        old_pos, new_pos = o2, n2
    return ops


def ops_for_format(model: ParagraphModel, spec: dict, where: str) -> list[Op]:
    unknown = [f for f in list(spec.get("set", ())) + list(spec.get("unset", ())) if f not in FORMAT_FLAGS]
    if unknown:
        raise EditError(f"{where}: unsupported formatting {unknown}; use {', '.join(FORMAT_FLAGS)} or rpr_add")
    if spec.get("highlight", "yellow") not in HIGHLIGHT_COLOURS:
        raise EditError(f"{where}: highlight colour must be one of {sorted(HIGHLIGHT_COLOURS)}")
    if not spec.get("set") and not spec.get("unset") and not spec.get("rpr_add"):
        raise EditError(f"{where}: a format edit needs set, unset or rpr_add")
    start, end = _locate(model.plain, {"before": spec.get("before", ""), "old": spec["text"],
                                       "after": spec.get("after", "")}, where)
    pmap = model.plain_map()
    _check_boundary(model, pmap, start, where)
    _check_boundary(model, pmap, end, where)
    cs, ce = pmap[start], _end_cell(pmap, start, end)
    _check_editable(model, cs, ce, where)
    return [Op(cs, ce, fmt_set=tuple(spec.get("set", ())), fmt_unset=tuple(spec.get("unset", ())),
               rpr_add=spec.get("rpr_add", "").encode("utf-8"), kind="format", spec=spec)]


def ops_for_comment(model: ParagraphModel, spec: dict, where: str) -> list[Op]:
    start, end = _locate(model.plain, {"before": spec.get("before", ""), "old": spec["anchor"],
                                       "after": spec.get("after", "")}, where)
    pmap = model.plain_map()
    _check_boundary(model, pmap, start, where)
    _check_boundary(model, pmap, end, where)
    return [Op(pmap[start], _end_cell(pmap, start, end), comment={"text": spec["text"], "author": spec.get("author"),
                                                "initials": spec.get("initials")}, kind="comment", spec=spec)]


# -- run properties ------------------------------------------------------------------

def _rpr_children(doc: XMLDoc, rpr: Element | None) -> list[tuple[str, bytes]]:
    if rpr is None:
        return []
    return [(c.tag, doc.raw(c)) for c in rpr.elements()]


def _order(children: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    rank = {tag: i for i, tag in enumerate(RPR_ORDER)}
    return [c for _, c in sorted(enumerate(children), key=lambda p: (rank.get(p[1][0], len(RPR_ORDER) - 1), p[0]))]


def edit_rpr(doc: XMLDoc, rpr: Element | None, target: frozenset, *, managed=MANAGED_FLAGS,
             keep_change: bool = False, highlight: str | None = None,
             force_off: frozenset = frozenset()) -> list[tuple[str, bytes]]:
    children = [c for c in _rpr_children(doc, rpr) if c[0] not in ("w:ins", "w:del", "w:moveFrom", "w:moveTo")]
    if not keep_change:
        children = [c for c in children if c[0] != "w:rPrChange"]
    current = rpr_flags(rpr)
    # removals first: superscript and subscript share w:vertAlign, so adding one after removing
    # the other must not be undone by the removal
    ordered = [f for f in managed if f not in target and f in current] + \
              [f for f in managed if f in target and f not in current]
    for flag in ordered:
        tags = FLAG_ELEMENTS[flag]
        if flag in target and flag not in current:
            if flag == "highlight":
                if highlight is None:
                    continue   # inserted text never gains a highlight by inheritance
                children = [c for c in children if c[0] not in tags]
                children.append(("w:highlight", f'<w:highlight w:val="{highlight}"/>'.encode()))
                continue
            children = [c for c in children if c[0] not in tags]
            children.append((tags[0], FLAG_MARKUP_XML[flag]))
        elif flag not in target and flag in current:
            if flag in ("superscript", "subscript"):
                children = [c for c in children if c[0] != "w:vertAlign"]
            else:
                children = [c for c in children if c[0] not in tags]
    for flag in sorted(force_off):
        # the flag comes from a paragraph or character style: removing direct markup is not enough
        if flag in ("superscript", "subscript") and target & {"superscript", "subscript"}:
            continue   # the vertical alignment being set replaces the inherited one
        tag, off = FLAG_OFF_XML[flag]
        children = [c for c in children if c[0] not in FLAG_ELEMENTS[flag]] + [(tag, off)]
    return _order(children)


def merge_fragment(children: list[tuple[str, bytes]], fragment: bytes, order=None) -> list[tuple[str, bytes]]:
    """Add raw property elements (e.g. b'<w:rFonts w:ascii="Courier New"/>'), replacing same-tag children."""
    if not fragment:
        return children
    parsed = XMLDoc(b"<x>" + fragment + b"</x>")
    extra = [(e.tag, parsed.raw(e)) for e in parsed.root.elements()]
    tags = {t for t, _ in extra}
    merged = [c for c in children if c[0] not in tags] + extra
    if order is None:
        return _order(merged)
    rank = {tag: i for i, tag in enumerate(order)}
    return [c for _, c in sorted(enumerate(merged), key=lambda p: (rank.get(p[1][0], len(order) - 1), p[0]))]


def rpr_bytes(children: list[tuple[str, bytes]]) -> bytes:
    return b"<w:rPr>" + b"".join(c for _, c in children) + b"</w:rPr>" if children else b""


# -- rebuilding ----------------------------------------------------------------------

@dataclass
class Token:
    kind: str                 # raw | kept | del | ins | fmt
    ctx: Element | None
    data: bytes


class Rebuilder:
    def __init__(self, model: ParagraphModel, ops: list[Op], revs: Revisions, comments: list,
                 styles: StyleFlags | None = None):
        self.model = model
        self.styles = styles
        self.doc = model.doc
        self.ops = sorted(ops, key=lambda o: (o.start, o.end, o.is_format))
        self.revs = revs
        self.comments = comments
        self.state = {}
        self.after = {}
        self.before = {}
        self.run_pieces = {}
        self.prior_opened = {}
        self._plan()

    # -- planning ---------------------------------------------------------------------
    def _plan(self):
        cells = self.model.cells
        claimed = [None] * len(cells)
        for op in self.ops:
            if op.comment is not None:
                continue
            for index in range(op.start, op.end):
                if claimed[index] is not None:
                    raise EditError(f"paragraph {self.model.index + 1}: overlapping edits "
                                    f"({claimed[index].spec.get('id', '?')} and {op.spec.get('id', '?')})")
                claimed[index] = op
        for op in self.ops:
            if op.comment is not None:
                cid = op.comment["id"]
                start_token = Token("raw", None, f'<w:commentRangeStart w:id="{cid}"/>'.encode())
                end_token = Token("raw", None, (f'<w:commentRangeEnd w:id="{cid}"/><w:r><w:rPr><w:rStyle '
                                                f'w:val="CommentReference"/></w:rPr><w:commentReference '
                                                f'w:id="{cid}"/></w:r>').encode())
                self._before(op.start, start_token)
                self._after(op.end - 1 if op.end > op.start else op.start - 1, end_token, fallback=op.start)
                continue
            if op.replacement_run is not None:
                self.state[op.start] = ("swap", op)
                continue
            for index in range(op.start, op.end):
                self.state[index] = ("fmt" if op.is_format else "del", op)
            if op.is_format:
                continue
            if op.insert:
                anchor = op.end - 1 if op.end > op.start else op.start - 1
                self._after(anchor, ("ins", op), fallback=op.start)

    def _before(self, index, token):
        self.before.setdefault(index, []).append(token)

    def _after(self, index, token, fallback):
        if index >= 0:
            self.after.setdefault(index, []).append(token)
        else:
            self.before.setdefault(fallback, []).append(token)

    # -- emission ---------------------------------------------------------------------
    def build(self) -> bytes:
        tokens: list[Token] = []
        cells_by_item = {}
        for index, cell in enumerate(self.model.cells):
            cells_by_item.setdefault(id(cell.item), []).append(index)
        touched_prior = set()
        for item in self.model.items:
            indexes = cells_by_item.get(id(item), [])
            if item.ctx is not None and self._touched(indexes):
                touched_prior.add(id(item.ctx))
        emitted_prior = set()
        trailing = len(self.model.cells)
        for item in self.model.items:
            indexes = cells_by_item.get(id(item), [])
            if item.ctx is not None and id(item.ctx) not in touched_prior:
                if id(item.ctx) not in emitted_prior:
                    tokens.append(Token("raw", None, self.doc.raw(item.ctx)))
                    emitted_prior.add(id(item.ctx))
                continue
            if item.kind in ("zero", "invisible") or not indexes:
                if item.kind == "run" and not indexes:
                    tokens.append(Token("kept", item.ctx, self.doc.data[item.start:item.end]))
                else:
                    tokens.append(Token("raw", None, self.doc.data[item.start:item.end]))
                continue
            if item.kind == "atom":
                index = indexes[0]
                tokens.extend(self._hooks(self.before.get(index, [])))
                state = self.state.get(index)
                raw = self.doc.data[item.start:item.end]
                if state and state[0] == "swap":
                    op = state[1]
                    tokens.append(Token("del", item.ctx, raw))
                    tokens.append(Token("ins", None, op.replacement_run))
                elif state and state[0] == "del":
                    if re.search(rb"<w:(?:ins|del|moveFrom|moveTo)", raw):
                        raise EditError(f"paragraph {self.model.index + 1}: {item.marker} contains earlier tracked "
                                        "changes; accept or reject them before deleting it")
                    tokens.append(Token("del", item.ctx, _to_deleted(raw)))
                else:
                    tokens.append(Token("kept" if item.ctx is not None else "raw", item.ctx, raw))
                tokens.extend(self._hooks(self.after.get(index, [])))
                continue
            tokens.extend(self._run_tokens(item, indexes))
        if trailing in self.before:
            tokens.extend(self._hooks(self.before[trailing]))
        return self._wrap(tokens)

    def _touched(self, indexes) -> bool:
        return any(i in self.state or i in self.after or i in self.before for i in indexes)

    def _hooks(self, hooks) -> list[Token]:
        out = []
        for hook in hooks:
            if isinstance(hook, Token):
                out.append(hook)
            else:
                _, op = hook
                out.extend(self._insert_tokens(op))
        return out

    def _run_tokens(self, item: Item, indexes: list[int]) -> list[Token]:
        if not self._touched(indexes):
            return [Token("kept" if item.ctx is not None else "raw", item.ctx, self.doc.data[item.start:item.end])]
        tokens: list[Token] = []
        group: list[int] = []
        group_state = None

        def flush():
            nonlocal group, group_state
            if group:
                kind = group_state[0] if group_state else "kept"
                op = group_state[1] if group_state else None
                tokens.append(Token(kind, item.ctx, self._run_piece(item, group, kind, op)))
            group = []
            group_state = None

        for index in indexes:
            if index in self.before:
                flush()
                tokens.extend(self._hooks(self.before[index]))
            state = self.state.get(index)
            if group and state != group_state:
                flush()
            group.append(index)
            group_state = state
            if index in self.after:
                flush()
                tokens.extend(self._hooks(self.after[index]))
        flush()
        return tokens

    def _run_piece(self, item: Item, indexes: list[int], kind: str, op: Op | None) -> bytes:
        run = item.element
        doc = self.doc
        count = self.run_pieces.get(id(run), 0)
        self.run_pieces[id(run)] = count + 1
        rpr = run.find("w:rPr")
        if kind == "fmt":
            current = rpr_flags(rpr)
            target = (current | frozenset(op.fmt_set)) - frozenset(op.fmt_unset)
            has_change = rpr is not None and rpr.find("w:rPrChange") is not None
            force_off = frozenset()
            if op.fmt_unset and self.styles is not None:
                run_style = rpr.find("w:rStyle") if rpr is not None else None
                inherited = self.styles.inherited(paragraph_style(self.model.paragraph),
                                                  run_style.get("w:val") if run_style is not None else None)
                force_off = frozenset(op.fmt_unset) & inherited
            children = merge_fragment(edit_rpr(doc, rpr, target, keep_change=has_change, managed=FORMAT_FLAGS,
                                               highlight=op.spec.get("highlight", "yellow"), force_off=force_off),
                                      op.rpr_add)
            if has_change:
                rpr_data = self._reid_change(rpr_bytes(children), count)
            else:
                old = [c for c in _rpr_children(doc, rpr) if c[0] not in ("w:rPrChange",)]
                change = (b"<w:rPrChange " + self._attrs(op) + b"><w:rPr>" + b"".join(c for _, c in old)
                          + b"</w:rPr></w:rPrChange>")
                rpr_data = b"<w:rPr>" + b"".join(c for _, c in children) + change + b"</w:rPr>"
        else:
            rpr_data = self._reid_change(doc.raw(rpr), count) if rpr is not None else b""
        body = []
        position = 0
        cells = self.model.cells
        while position < len(indexes):
            cell = cells[indexes[position]]
            if cell.child is not None and cell.child.tag == "w:t":
                chars = []
                child = cell.child
                while position < len(indexes) and cells[indexes[position]].child is child:
                    chars.append(cells[indexes[position]].raw or cells[indexes[position]].char)
                    position += 1
                tag = b"w:delText" if kind == "del" else b"w:t"
                body.append(b"<" + tag + b' xml:space="preserve">' + escape_text("".join(chars)).encode("utf-8")
                            + b"</" + tag + b">")
            else:
                body.append(doc.raw(cell.child))
                position += 1
        return doc.open_tag(run) + rpr_data + b"".join(body) + b"</w:r>"

    def _reid_change(self, data: bytes, count: int) -> bytes:
        if count == 0 or b"w:rPrChange" not in data:
            return data
        return re.sub(rb'(<w:rPrChange\b[^>]*?\bw:id=")\d+', lambda m: m.group(1) + str(self.revs.take()).encode(), data)

    def _attrs(self, op: Op) -> bytes:
        return self.revs.attrs()

    def _insert_tokens(self, op: Op) -> list[Token]:
        flags = op.insert_flags if op.insert_flags is not None else [self._policy_flags(op)] * len(op.insert)
        base = self._base_run(op)
        rpr = base.find("w:rPr") if base is not None else None
        # Inserted text is written as Unicode, so it must never inherit a symbol or dingbat font.
        symbolic = font_kind(rpr) is not None
        pieces = []
        start = 0
        for position in range(1, len(op.insert) + 1):
            if position == len(op.insert) or flags[position] != flags[start]:
                target = frozenset(f for f in flags[start] if f in MANAGED_FLAGS)
                children = edit_rpr(self.doc, rpr, target)
                if symbolic:
                    children = [c for c in children if c[0] != "w:rFonts"]
                children = merge_fragment(children, op.rpr_add)
                pieces.append(_insert_run(op.insert[start:position], rpr_bytes(children)))
                start = position
        return [Token("ins", None, b"".join(pieces))]

    def _base_run(self, op: Op):
        """The run whose properties inserted text copies; runs in a symbol font are a last resort."""
        cells = self.model.cells
        deleted = [cells[i] for i in range(op.start, op.end) if not cells[i].atom]
        left = cells[op.start - 1] if op.start > 0 and not cells[op.start - 1].atom else None
        right = cells[op.start] if op.start < len(cells) and not cells[op.start].atom else None
        candidates = [c.item.element for c in deleted] if deleted else [c.item.element for c in (left, right) if c]
        candidates += [c.item.element for c in cells if not c.atom]
        for run in candidates:
            if font_kind(run.find("w:rPr")) is None:
                return run
        return candidates[0] if candidates else None

    def _policy_flags(self, op: Op) -> frozenset:
        cells = self.model.cells
        deleted = [cells[i] for i in range(op.start, op.end) if not cells[i].atom and cells[i].char.strip()]
        if deleted and len({c.flags for c in deleted}) == 1:
            return deleted[0].flags
        left = cells[op.start - 1] if op.start > 0 and not cells[op.start - 1].atom else None
        right = cells[op.end] if op.end < len(cells) and not cells[op.end].atom else None
        if left is not None and right is not None and left.item is right.item:
            return left.flags
        chars = [c for c in cells if not c.atom and c.char.strip()]
        out = set()
        for flag in MANAGED_FLAGS:
            if left is not None and right is not None:
                if flag in left.flags and flag in right.flags:
                    out.add(flag)
            elif (left or right) is not None and flag in (left or right).flags and chars:
                share = sum(1 for c in chars if flag in c.flags) / len(chars)
                if share >= 0.9:
                    out.add(flag)
        return frozenset(out)

    def _wrap(self, tokens: list[Token]) -> bytes:
        out = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.kind == "raw":
                out.append(token.data)
                index += 1
                continue
            end = index
            while end < len(tokens) and tokens[end].kind == token.kind and tokens[end].ctx is token.ctx:
                end += 1
            body = b"".join(t.data for t in tokens[index:end])
            ctx = token.ctx
            if token.kind in ("kept", "fmt"):
                out.append(body if ctx is None else self._prior_open(ctx) + body + b"</w:ins>")
            elif token.kind == "del":
                wrapped = b"<w:del " + self.revs.attrs() + b">" + body + b"</w:del>"
                out.append(wrapped if ctx is None else self._prior_open(ctx) + wrapped + b"</w:ins>")
            elif token.kind == "ins":
                out.append(b"<w:ins " + self.revs.attrs() + b">" + body + b"</w:ins>")
            index = end
        return b"".join(out)

    def _prior_open(self, ctx: Element) -> bytes:
        if id(ctx) not in self.prior_opened:
            self.prior_opened[id(ctx)] = True
            return self.doc.open_tag(ctx)
        author = ctx.get("w:author", "")
        date = ctx.get("w:date")
        attrs = f'w:id="{self.revs.take()}" w:author="{escape_attr(author)}"'
        if date:
            attrs += f' w:date="{escape_attr(date)}"'
        return f"<w:ins {attrs}>".encode("utf-8")


def _insert_run(text: str, rpr: bytes) -> bytes:
    body = []
    buffer = []

    def flush():
        if buffer:
            body.append(b'<w:t xml:space="preserve">' + escape_text("".join(buffer)).encode("utf-8") + b"</w:t>")
            buffer.clear()

    for char in text:
        special = {"\t": b"<w:tab/>", "\n": b"<w:br/>", "\f": b'<w:br w:type="page"/>',
                   "‑": b"<w:noBreakHyphen/>", "­": b"<w:softHyphen/>"}.get(char)
        if special:
            flush()
            body.append(special)
        else:
            buffer.append(char)
    flush()
    return b"<w:r>" + rpr + b"".join(body) + b"</w:r>"


def _to_deleted(raw: bytes) -> bytes:
    raw = re.sub(rb"<w:t(\s[^>]*)?>", lambda m: b"<w:delText" + (m.group(1) or b"") + b">", raw)
    raw = raw.replace(b"</w:t>", b"</w:delText>")
    raw = re.sub(rb"<w:instrText(\s[^>]*)?>", lambda m: b"<w:delInstrText" + (m.group(1) or b"") + b">", raw)
    return raw.replace(b"</w:instrText>", b"</w:delInstrText>")


# -- pictures ------------------------------------------------------------------------

def image_size(data: bytes) -> tuple[int, int]:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        position = 2
        while position < len(data):
            marker, length = data[position + 1], struct.unpack(">H", data[position + 2:position + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[position + 5:position + 9])
                return width, height
            position += 2 + length
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", data[6:10])
    raise EditError("unsupported image format (use PNG, JPEG or GIF for embedded pictures)")


def picture_run(doc: XMLDoc, run: Element, *, rid: str, docpr_id: int, name: str, descr: str | None,
                image: bytes, width_emu: int | None) -> tuple[bytes, dict]:
    raw = doc.raw(run)
    extent = re.search(rb'<wp:extent\b[^>]*\bcx="(\d+)"[^>]*\bcy="(\d+)"', raw)
    if extent is None:
        raise EditError("picture has no wp:extent")
    old_cx, old_cy = int(extent.group(1)), int(extent.group(2))
    px_w, px_h = image_size(image)
    cx = width_emu or old_cx
    cy = round(cx * px_h / px_w)
    new = re.sub(rb'\s+wp14:(?:anchorId|editId)="[^"]*"', b"", raw)
    new = re.sub(rb'(<wp:extent\b[^>]*\bcx=")\d+("[^>]*\bcy=")\d+', lambda m: m.group(1) + str(cx).encode()
                 + m.group(2) + str(cy).encode(), new)
    new = re.sub(rb'(<a:ext\b[^>]*\bcx=")\d+("[^>]*\bcy=")\d+', lambda m: m.group(1) + str(cx).encode()
                 + m.group(2) + str(cy).encode(), new)
    new = re.sub(rb'r:embed="[^"]*"', f'r:embed="{rid}"'.encode(), new)

    def props(match):
        tag = match.group(1)
        attrs = match.group(2)
        old_descr = re.search(rb'\bdescr="([^"]*)"', attrs)
        text = escape_attr(descr).encode("utf-8") if descr is not None else (old_descr.group(1) if old_descr else b"")
        rest = re.sub(rb'\s*\b(?:id|name|descr)="[^"]*"', b"", attrs)
        return (b"<" + tag + f' id="{docpr_id}" name="{escape_attr(name)}"'.encode("utf-8")
                + (b' descr="' + text + b'"' if text else b"") + rest)
    new = re.sub(rb"<(wp:docPr|pic:cNvPr)\b([^>]*?)(?=/?>)", props, new)
    return new, {"old_emu": [old_cx, old_cy], "new_emu": [cx, cy], "pixels": [px_w, px_h]}


# -- the build -----------------------------------------------------------------------

@dataclass
class BuildResult:
    blob: bytes
    manifest: dict


def _resolve_paragraph(paragraphs, models, doc, fmap, key, where):
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit() and len(key) < 8):
        index = int(key)
        if not 0 <= index < len(paragraphs):
            raise EditError(f"{where}: paragraph index {index} out of range")
        return index
    if isinstance(key, dict):
        if "index" in key:
            return _resolve_paragraph(paragraphs, models, doc, fmap, int(key["index"]), where)
        if "contains" in key:
            hits = [i for i, p in enumerate(paragraphs) if key["contains"] in _model(models, doc, fmap, paragraphs, i).plain]
            if len(hits) != 1:
                raise EditError(f"{where}: {len(hits)} paragraphs contain {key['contains'][:50]!r} (need 1)")
            return hits[0]
        key = key.get("paraId")
    hits = [i for i, p in enumerate(paragraphs) if p.get("w14:paraId") == key]
    if len(hits) != 1:
        raise EditError(f"{where}: {len(hits)} paragraphs have paraId {key!r} (need 1)")
    return hits[0]


def parts_of_comments(changes: dict) -> list[str]:
    return [name for name in changes if name.startswith("word/comments")]


def _comment_ids(package: Package, edit: dict, where: str) -> list[str]:
    """The comment named by id, or by a unique piece of its text, plus all replies below it."""
    if COMMENTS_PART not in package.parts:
        raise EditError(f"{where}: the document has no comments")
    if edit.get("comment") is not None:
        target = str(edit["comment"])
    else:
        needle = edit.get("comment_text", "")
        hits = []

        def walk(items):
            for item in items:
                if needle and needle in item["text"]:
                    hits.append(item["id"])
                walk(item["replies"])
        walk(comment_threads.threads(package))
        if len(hits) != 1:
            raise EditError(f"{where}: {len(hits)} comments contain {needle[:40]!r} (need 1)")
        target = hits[0]
    ids = comment_threads.thread_ids(package, target)
    if not ids:
        raise EditError(f"{where}: comment {target} not found")
    return ids


def _picture_paragraph(paragraphs, name, where) -> int:
    hits = []
    for index, paragraph in enumerate(paragraphs):
        for docpr in paragraph.iter("wp:docPr"):
            if docpr.get("name") == name and not docpr.has_ancestor({"w:del", "w:moveFrom"}):
                owner = next((a for a in docpr.ancestors() if a.tag == "w:p"), None)
                if owner is paragraph:
                    hits.append(index)
    if len(hits) != 1:
        raise EditError(f"{where}: visible picture {name!r} found {len(hits)} times (need 1)")
    return hits[0]


def _model(models, doc, fmap, paragraphs, index) -> ParagraphModel:
    if index not in models:
        models[index] = ParagraphModel(doc, fmap, paragraphs[index], index)
    return models[index]


def _expanded_open_tag(doc: XMLDoc, paragraph: Element) -> bytes:
    """Open tag of a self-closing <w:p .../> without its slash."""
    return doc.open_tag(paragraph)[:-2].rstrip() + b">"


def _next_paragraph(paragraph: Element) -> Element | None:
    """The sibling paragraph that a deleted paragraph mark merges into, if any."""
    siblings = paragraph.parent.children if paragraph.parent is not None else []
    position = next(i for i, child in enumerate(siblings) if child is paragraph)
    for child in siblings[position + 1:]:
        if isinstance(child, Text) or child.tag in ZERO_WIDTH:
            continue
        return child if child.tag == "w:p" else None
    return None


def _removed_pictures(package, doc: XMLDoc, paragraph: Element) -> list[dict]:
    """Pictures shown in the paragraph's accepted view: name and image digest (as the gate lists them)."""
    rels = {r.get("Id"): r.get("Target") for r in package.relationships(MAIN_PART)}
    out = []
    for drawing in paragraph.iter("w:drawing"):
        if drawing.has_ancestor({"w:del", "w:moveFrom"}):
            continue
        name = next((e.get("name") for e in drawing.iter("wp:docPr")), None)
        blip = next((e for e in drawing.iter("a:blip")), None)
        digest = None
        if blip is not None and blip.get("r:embed") in rels:
            target = package.resolve(MAIN_PART, rels[blip.get("r:embed")])
            if target in package.parts:
                digest = hashlib.sha256(package.parts[target]).hexdigest()
        out.append({"name": name, "sha256": digest})
    return out


def _mark_marker(doc: XMLDoc, paragraph: Element, marker: bytes) -> tuple[int, int, bytes]:
    """Splice that puts a paragraph-mark revision first in pPr/rPr."""
    ppr = paragraph.find("w:pPr")
    if ppr is None:
        body = b"<w:pPr><w:rPr>" + marker + b"</w:rPr></w:pPr>"
        if paragraph.self_closing:
            return paragraph.start, paragraph.end, _expanded_open_tag(doc, paragraph) + body + b"</w:p>"
        return paragraph.open_end, paragraph.open_end, body
    rpr = ppr.find("w:rPr")
    if rpr is None:
        anchor = next((c for c in ppr.elements() if c.tag in ("w:sectPr", "w:pPrChange")), None)
        position = anchor.start if anchor is not None else ppr.close_start
        if ppr.self_closing:
            return ppr.start, ppr.end, b"<w:pPr><w:rPr>" + marker + b"</w:rPr></w:pPr>"
        return position, position, b"<w:rPr>" + marker + b"</w:rPr>"
    if any(c.tag in ("w:ins", "w:del", "w:moveFrom", "w:moveTo") for c in rpr.elements()):
        raise EditError("paragraph mark already carries a tracked change")
    if rpr.self_closing:
        return rpr.start, rpr.end, b"<w:rPr>" + marker + b"</w:rPr>"
    return rpr.open_end, rpr.open_end, marker


def build(source, spec: dict, *, author: str, date: str | None = None, id_base: int | None = None,
          track_revisions: bool | None = None, base_dir=None) -> BuildResult:
    """Apply an edit spec to a .docx and return the new package bytes and a manifest.

    `base_dir` resolves relative image paths (normally the folder of the spec file).
    """
    if not author or not author.strip():
        raise EditError("a revision author is required (the project's configured author name)")
    package = source if isinstance(source, Package) else Package(source)
    doc = package.document
    check_namespaces(doc)
    fmap = map_fields(doc)
    if fmap.errors:
        raise EditError("the source has broken fields: " + "; ".join(fmap.errors[:3]))
    paragraphs = list(iter_paragraphs(doc))
    if id_base is not None and id_base < existing_max_id(package):
        raise EditError(f"id base {id_base} is below the largest existing w:id {existing_max_id(package)}")
    revs = Revisions(id_base if id_base is not None else default_id_base(package), author, date or utc_now())
    spans = [(f.first_paragraph, f.last_paragraph, f.kind) for f in fmap.fields
             if f.last_paragraph > f.first_paragraph >= 0]

    def in_field_span(index: int, *, after: bool = False) -> str | None:
        for first, last, kind in spans:
            if first <= index < last or (not after and index == last):
                return kind or "field"
        return None
    models: dict = {}
    styles = StyleFlags(package)
    by_paragraph: dict = {}
    splices = []
    changes: dict = {}
    additions: list = []
    records = []
    declared = {"citations": False, "fields": False, "formatting": False, "pictures": [], "parts_added": [],
                "parts_changed": []}
    comments_needed = []
    resolutions = []
    paragraph_inserts = []
    paragraph_deletes = set()
    edits = spec.get("edits", [])
    if not edits:
        raise EditError("the spec has no edits")

    for number, edit in enumerate(edits, 1):
        where = f"edit {edit.get('id', number)}"
        op = edit.get("op", "replace")
        if op == "resolve_comment":
            resolutions.append((_comment_ids(package, edit, where), edit.get("mode", "delete"), edit, where))
            continue
        if op == "insert_paragraph":
            index = _resolve_paragraph(paragraphs, models, doc, fmap, edit["after"], where)
            if in_field_span(index, after=True):
                raise EditError(f"{where}: cannot insert a paragraph inside the {in_field_span(index, after=True)} "
                                "field (the bibliography is rebuilt by EndNote)")
            paragraph_inserts.append((index, edit, where))
            continue
        if op == "swap_picture" and edit.get("para") is None:
            index = _picture_paragraph(paragraphs, edit["old_name"], where)
        else:
            index = _resolve_paragraph(paragraphs, models, doc, fmap, edit.get("para"), where)
        model = _model(models, doc, fmap, paragraphs, index)
        if op in ("paragraph_format", "delete_paragraph") and in_field_span(index):
            raise EditError(f"{where}: the paragraph lies inside the {in_field_span(index)} field; "
                            "EndNote owns the bibliography")
        if op in ("replace", "insert", "delete"):
            if op == "insert":
                edit = dict(edit, old="")
            if op == "delete":
                edit = dict(edit, new="")
            ops = ops_for_replace(model, edit, where)
        elif op == "rewrite":
            ops = ops_for_rewrite(model, edit, where)
            if edit.get("apply_formatting"):
                declared["formatting"] = True
        elif op == "format":
            ops = ops_for_format(model, edit, where)
            declared["formatting"] = True
        elif op == "comment":
            ops = ops_for_comment(model, edit, where)
            comments_needed.extend(ops)
        elif op == "delete_paragraph":
            paragraph = paragraphs[index]
            ppr = paragraph.find("w:pPr")
            if ppr is not None and ppr.find("w:sectPr") is not None:
                raise EditError(f"{where}: the paragraph ends a section (a section break, for example before a "
                                "landscape page); deleting it would remove the section. Delete its text instead, "
                                "or change sections in Word")
            if _next_paragraph(paragraph) is None:
                raise EditError(f"{where}: the paragraph is the last one before a table, in a table cell, in a "
                                "text box or in the document, so Word has no following paragraph to merge it "
                                "into; delete its text instead")
            if index in paragraph_deletes:
                raise EditError(f"{where}: the paragraph is already being deleted")
            paragraph_deletes.add(index)
            cells = model.cells
            if any(c.atom for c in cells) and not edit.get("allow_fields"):
                raise EditError(f"{where}: the paragraph contains fields or pictures; set allow_fields to delete them")
            if any(c.atom and c.item.marker.startswith("⟦F") for c in cells):
                declared["citations"] = True
            declared.setdefault("pictures_removed", []).extend(_removed_pictures(package, doc, paragraph))
            ops = [Op(0, len(cells), spec=edit)] if cells else []
        elif op == "paragraph_format":
            splices.append(_paragraph_format(doc, paragraphs[index], edit, revs, where))
            declared["formatting"] = True
            records.append({"id": edit.get("id", number), "op": op, "paragraph": index,
                            "paraId": paragraphs[index].get("w14:paraId"), "purpose": edit.get("purpose", "")})
            continue
        elif op == "swap_picture":
            if base_dir is not None and not Path(edit["image"]).is_absolute():
                edit = dict(edit, image=str(Path(base_dir) / edit["image"]))
            ops = [_swap_op(package, doc, model, edit, where, changes, additions, declared)]
        else:
            raise EditError(f"{where}: unknown op {op!r}")
        for item in ops:
            item.spec = dict(edit, id=edit.get("id", number))
            if item.rpr_add or (item.insert_flags and any(item.insert_flags)):
                declared["formatting"] = True
        by_paragraph.setdefault(index, []).extend(ops)

    # comments: ids and part
    if comments_needed:
        _prepare_comments(package, comments_needed, revs, changes, additions, declared)

    expected = []
    for index, ops in sorted(by_paragraph.items()):
        model = models[index]
        paragraph = paragraphs[index]
        first_id = revs.next_id
        new_inline = Rebuilder(model, ops, revs, comments_needed, styles).build()
        paragraph_ids = [first_id + 1, revs.next_id] if revs.next_id > first_id else []
        start, end = _inline_region(paragraph)
        if paragraph.self_closing:
            if ops:
                splices.append((paragraph.start, paragraph.end, _expanded_open_tag(doc, paragraph) + new_inline
                                + b"</w:p>"))
        else:
            splices.append((start, end, new_inline))
        text = _expected_text(model, ops)
        expected.append({"paraId": paragraph.get("w14:paraId"), "index": index, "text": text,
                         "deleted": index in paragraph_deletes})
        for op in ops:
            records.append({"id": op.spec.get("id"), "op": op.spec.get("op", "replace"), "paragraph": index,
                            "paraId": paragraph.get("w14:paraId"), "purpose": op.spec.get("purpose", ""),
                            "deleted": "".join(c.char for c in model.cells[op.start:op.end])
                            if not op.is_format and op.comment is None else "",
                            "inserted": op.insert, "paragraph_revision_ids": paragraph_ids})
        if index in paragraph_deletes:
            splices.append(_mark_marker(doc, paragraph, b"<w:del " + revs.attrs() + b"/>"))

    grouped: dict = {}
    for index, edit, where in paragraph_inserts:
        grouped.setdefault(index, []).append((edit, where))
    # New paragraphs follow the anchor and any paragraphs nested in it (text boxes); `slots` holds the
    # source position they are inserted before, so every paragraph's position in the output is known.
    slots = {}
    for index, group in grouped.items():
        end = paragraphs[index].end
        slot = index + 1
        while slot < len(paragraphs) and paragraphs[slot].start < end:
            slot += 1
        slots[index] = slot

    def new_position(source_index: int) -> int:
        return source_index + sum(len(grouped[a]) for a, s in slots.items() if s <= source_index)

    for item in expected:
        item["new_index"] = new_position(item["index"])
    for index, group in grouped.items():
        paragraph = paragraphs[index]
        where = group[0][1]
        if index in paragraph_deletes:
            raise EditError(f"{where}: cannot insert after a paragraph that is being deleted")
        ppr = paragraph.find("w:pPr")
        if ppr is not None and ppr.find("w:sectPr") is not None:
            raise EditError(f"{where}: the anchor paragraph ends a section; insert after another paragraph")
        # Word's form: the anchor's paragraph mark becomes the inserted one, every new paragraph but the
        # last also has an inserted mark, and the last new paragraph ends with the original mark.
        splices.append(_mark_marker(doc, paragraph, b"<w:ins " + revs.attrs() + b"/>"))
        following = paragraphs[index + 1] if index + 1 < len(paragraphs) else None
        pieces = []
        for position, (edit, where) in enumerate(group):
            last = position == len(group) - 1
            new_paragraph, text = _new_paragraph(doc, paragraph, edit, revs, mark_inserted=not last,
                                                 following=following, paragraphs=paragraphs, models=models,
                                                 fmap=fmap)
            pieces.append(new_paragraph)
            if b"<w:pPrChange" in new_paragraph:
                declared["formatting"] = True
            first = slots[index] + sum(len(grouped[a]) for a, s in slots.items() if s < slots[index])
            expected.append({"paraId": None, "index": None, "text": text, "inserted_after": index,
                             "new_index": first + position})
            records.append({"id": edit.get("id"), "op": "insert_paragraph", "after": index, "inserted": text,
                            "purpose": edit.get("purpose", "")})
        splices.append((paragraph.end, paragraph.end, b"".join(pieces)))

    try:
        new_document = apply_splices(doc.data, splices)
    except XMLError as error:
        raise EditError("two edits change the same paragraph properties (for example paragraph_format together "
                        "with insert_paragraph or delete_paragraph on one paragraph); make them in separate "
                        f"passes ({error})") from error
    removed_comments = []
    for ids, mode, edit, where in resolutions:
        if mode == "done":
            try:
                changes.update(comment_threads.mark_done(package, ids, changes))
            except ValueError as error:
                raise EditError(f"{where}: {error}") from error
        elif mode == "delete":
            new_document, parts = comment_threads.remove_threads(package, new_document, ids, changes)
            changes.update(parts)
            removed_comments.extend(ids)
        else:
            raise EditError(f"{where}: resolve_comment mode must be 'delete' or 'done'")
        declared["parts_changed"].extend(p for p in parts_of_comments(changes) if p not in declared["parts_changed"])
        records.append({"id": edit.get("id"), "op": "resolve_comment", "comments": ids, "mode": mode,
                        "purpose": edit.get("purpose", "")})
    declared["comments_removed"] = removed_comments
    changes[MAIN_PART] = new_document
    if track_revisions is not None:
        settings = _set_track_revisions(package, track_revisions)
        if settings is not None:
            changes["word/settings.xml"] = settings
            declared["parts_changed"].append("word/settings.xml")
    blob = _package_blob(package, changes, additions)
    manifest = {
        "schema": "msw-build/1",
        "source": {"path": str(package.path) if package.path else None, "sha256": package.sha256},
        "author": author, "date": revs.date,
        "revision_ids": [revs.used[0], revs.used[-1]] if revs.used else [],
        "declared": declared,
        "expected_accept": expected,
        "intended": spec.get("intended") or None,
        "edits": records,
        "output_sha256": sha256_bytes(blob),
    }
    return BuildResult(blob, manifest)


PPR_ORDER = ["w:pStyle", "w:keepNext", "w:keepLines", "w:pageBreakBefore", "w:framePr", "w:widowControl",
             "w:numPr", "w:suppressLineNumbers", "w:pBdr", "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku",
             "w:wordWrap", "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi",
             "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
             "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment", "w:textboxTightWrap",
             "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange"]


def _paragraph_format(doc: XMLDoc, paragraph: Element, edit: dict, revs: Revisions, where: str):
    """Tracked paragraph-property change: new properties plus a w:pPrChange holding the old ones."""
    fragment = edit.get("ppr_add", "").encode("utf-8")
    remove = set(edit.get("ppr_remove", []))
    if not fragment and not remove:
        raise EditError(f"{where}: paragraph_format needs ppr_add or ppr_remove")
    ppr = paragraph.find("w:pPr")
    children = [(c.tag, doc.raw(c)) for c in ppr.elements()] if ppr is not None else []
    if any(t == "w:pPrChange" for t, _ in children):
        raise EditError(f"{where}: the paragraph already has a tracked formatting change")
    if any(t == "w:sectPr" for t, _ in _fragment_children(fragment)):
        raise EditError(f"{where}: paragraph_format cannot add a section break; change sections in Word")
    old = [(t, b) for t, b in children if t not in ("w:rPr", "w:sectPr")]
    new = merge_fragment([(t, b) for t, b in children if t not in remove], fragment, PPR_ORDER)
    change = (b"<w:pPrChange " + revs.attrs() + b"><w:pPr>" + b"".join(b for _, b in old) + b"</w:pPr></w:pPrChange>")
    body = b"<w:pPr>" + b"".join(b for _, b in new) + change + b"</w:pPr>"
    if ppr is None:
        if paragraph.self_closing:
            return paragraph.start, paragraph.end, _expanded_open_tag(doc, paragraph) + body + b"</w:p>"
        return paragraph.open_end, paragraph.open_end, body
    return ppr.start, ppr.end, body


def _fragment_children(fragment: bytes) -> list[tuple[str, bytes]]:
    if not fragment:
        return []
    parsed = XMLDoc(b"<x>" + fragment + b"</x>")
    return [(e.tag, parsed.raw(e)) for e in parsed.root.elements()]


def _inline_region(paragraph: Element) -> tuple[int, int]:
    ppr = paragraph.find("w:pPr")
    start = ppr.end if ppr is not None else paragraph.open_end
    return start, paragraph.close_start


def _expected_text(model: ParagraphModel, ops: list[Op]) -> str:
    cells = model.cells
    deleted = set()
    inserts = {}
    for op in ops:
        if op.is_format or op.comment is not None or op.replacement_run is not None:
            continue
        deleted.update(range(op.start, op.end))
        anchor = op.end - 1 if op.end > op.start else op.start - 1
        inserts.setdefault(anchor, []).append(op.insert)
    out = ["".join(inserts.get(-1, []))]
    for index, cell in enumerate(cells):
        if index not in deleted:
            out.append(cell.char)
        out.extend(inserts.get(index, []))
    return "".join(out)


def _swap_op(package, doc, model, edit, where, changes, additions, declared) -> Op:
    name = edit["old_name"]
    targets = [c for c in model.cells if c.atom and c.item.element is not None and any(
        e.tag == "wp:docPr" and e.get("name") == name for e in c.item.element.iter())]
    if len(targets) != 1:
        raise EditError(f"{where}: picture {name!r} found {len(targets)} times in the paragraph")
    cell_index = model.cells.index(targets[0])
    run = targets[0].item.element
    if run.tag != "w:r":
        raise EditError(f"{where}: picture {name!r} is not a plain picture run")
    with open(fs(edit["image"]), "rb") as handle:
        image = handle.read()
    extension = edit["image"].rsplit(".", 1)[-1].lower()
    if extension not in MEDIA_TYPES:
        raise EditError(f"{where}: unsupported image type .{extension}")
    existing = {n.lower() for n in package.parts} | {n.lower() for n, _ in additions}   # OPC names ignore case
    if edit.get("media"):
        leaf = edit["media"].replace("\\", "/").rsplit("/", 1)[-1]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", leaf) or leaf.rsplit(".", 1)[-1].lower() != extension:
            raise EditError(f"{where}: media name must be a plain file name ending in .{extension}")
        media = "word/media/" + leaf
        if media.lower() in existing:
            raise EditError(f"{where}: media part {media} already exists (part names ignore case)")
    else:
        number = 1
        while f"word/media/msw_image{number}.{extension}".lower() in existing:
            number += 1
        media = f"word/media/msw_image{number}.{extension}"
    rid, rels = add_relationship(package, MAIN_PART, IMAGE_REL, media[len("word/"):], changes)
    changes[Package.rels_name(MAIN_PART)] = rels
    content_types = ensure_content_type(package, changes, extension=extension, content_type=MEDIA_TYPES[extension])
    if content_types is not None:
        changes["[Content_Types].xml"] = content_types
    additions.append((media, image))
    docpr_ids = [int(e.get("id", "0")) for part in package.story_parts() for e in package.xml(part).iter("wp:docPr")
                 if (e.get("id") or "0").isdigit()]
    docpr_id = max(docpr_ids + [0]) + 1 + len(declared["pictures"])
    width = edit.get("width_emu")
    if width is None and edit.get("width_mm"):
        width = round(float(edit["width_mm"]) * 36000)
    new_run, geometry = picture_run(doc, run, rid=rid, docpr_id=docpr_id, name=edit.get("new_name", name + " (new)"),
                                    descr=edit.get("descr"), image=image, width_emu=width)
    declared["pictures"].append({"old_name": name, "new_name": edit.get("new_name", name + " (new)"),
                                 "sha256": sha256_bytes(image), "media": media, **geometry})
    declared["parts_added"].append(media)
    return Op(cell_index, cell_index + 1, replacement_run=new_run, kind="swap", spec=edit)


def _prepare_comments(package, ops, revs, changes, additions, declared):
    name = "word/comments.xml"
    existing = package.parts.get(name)
    ids = []
    if existing is not None:
        ids = [int(m) for m in re.findall(rb'<w:comment\b[^>]*\bw:id="(\d+)"', existing)]
    next_id = max(ids + [-1]) + 1
    entries = []
    for op in ops:
        op.comment["id"] = next_id
        author = op.comment.get("author") or revs.author
        initials = op.comment.get("initials") or "".join(w[0] for w in author.split() if w[:1].isalpha())[:4]
        paragraphs = "".join(
            f'<w:p><w:pPr><w:pStyle w:val="CommentText"/></w:pPr>'
            + ('<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:annotationRef/></w:r>' if i == 0 else "")
            + f'<w:r><w:t xml:space="preserve">{escape_text(line)}</w:t></w:r></w:p>'
            for i, line in enumerate(op.comment["text"].split("\n")))
        entries.append(f'<w:comment w:id="{next_id}" w:author="{escape_attr(author)}" w:date="{revs.date}" '
                       f'w:initials="{escape_attr(initials)}">{paragraphs}</w:comment>'.encode("utf-8"))
        next_id += 1
    if existing is None:
        data = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n<w:comments xmlns:w="'
                b'http://schemas.openxmlformats.org/wordprocessingml/2006/main">' + b"".join(entries)
                + b"</w:comments>")
        additions.append((name, data))
        declared["parts_added"].append(name)
        rid, rels = add_relationship(package, MAIN_PART, COMMENTS_REL, "comments.xml", changes)
        changes[Package.rels_name(MAIN_PART)] = rels
        content_types = ensure_content_type(
            package, changes, part=name,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml")
        if content_types is not None:
            changes["[Content_Types].xml"] = content_types
    else:
        doc = XMLDoc(existing)
        root = doc.root
        if root.self_closing:     # an empty <w:comments .../> part
            opened = doc.open_tag(root)[:-2].rstrip() + b">"
            changes[name] = existing[:root.start] + opened + b"".join(entries) + b"</w:comments>" \
                + existing[root.end:]
        else:
            changes[name] = existing[:root.close_start] + b"".join(entries) + existing[root.close_start:]
        declared["parts_changed"].append(name)


def _ppr_parts(doc: XMLDoc, paragraph: Element | None) -> list[tuple[str, bytes]]:
    """pPr children without section properties, change records or paragraph-mark revision markers."""
    ppr = paragraph.find("w:pPr") if paragraph is not None else None
    out = []
    if ppr is None:
        return out
    for child in ppr.elements():
        if child.tag in ("w:sectPr", "w:pPrChange"):
            continue
        if child.tag == "w:rPr":
            inner = [c for c in child.elements() if c.tag not in ("w:ins", "w:del", "w:moveFrom", "w:moveTo",
                                                                  "w:rPrChange")]
            if inner:
                out.append(("w:rPr", b"<w:rPr>" + b"".join(doc.raw(c) for c in inner) + b"</w:rPr>"))
            continue
        out.append((child.tag, doc.raw(child)))
    return out


def _new_paragraph(doc: XMLDoc, anchor: Element, edit: dict, revs: Revisions, *, mark_inserted: bool,
                   following=None, paragraphs=None, models=None, fmap=None) -> tuple[bytes, str]:
    original = _ppr_parts(doc, anchor)
    if edit.get("like") is not None:
        index = _resolve_paragraph(paragraphs, models, doc, fmap, edit["like"], "insert_paragraph")
        parts = _ppr_parts(doc, paragraphs[index])
    elif paragraph_style(anchor).lower().startswith("heading") and following is not None \
            and not paragraph_style(following).lower().startswith("heading"):
        parts = _ppr_parts(doc, following)
    else:
        parts = list(original)
    if edit.get("style"):
        style = escape_attr(edit["style"]).encode("utf-8")
        parts = [(t, b) for t, b in parts if t != "w:pStyle"] + [("w:pStyle", b'<w:pStyle w:val="' + style + b'"/>')]
    rpr_parts = [b for t, b in parts if t == "w:rPr"]
    body_parts = [(t, b) for t, b in parts if t != "w:rPr"]
    rank = {tag: i for i, tag in enumerate(PPR_ORDER)}
    body_parts.sort(key=lambda p: rank.get(p[0], len(PPR_ORDER)))
    mark = b""
    if mark_inserted:
        inner = rpr_parts[0][len(b"<w:rPr>"):-len(b"</w:rPr>")] if rpr_parts else b""
        mark = b"<w:rPr><w:ins " + revs.attrs() + b"/>" + inner + b"</w:rPr>"
    elif rpr_parts:
        mark = rpr_parts[0]
    change = b""
    if not mark_inserted and [b for _, b in body_parts] != [b for t, b in original if t != "w:rPr"]:
        # the last new paragraph ends with the anchor's original mark: record its old properties
        old = b"".join(b for t, b in original if t != "w:rPr")
        change = b"<w:pPrChange " + revs.attrs() + b"><w:pPr>" + old + b"</w:pPr></w:pPrChange>"
    content = b"".join(b for _, b in body_parts) + mark + change
    new_ppr = b"<w:pPr>" + content + b"</w:pPr>" if content else b""
    text, flags = parse_markup(edit["text"])
    if ATOM.search(text):
        raise EditError("new paragraphs cannot contain atoms; add citations as [PMID: n] placeholders")
    base = next((r for r in anchor.iter("w:r") if r.find("w:t") is not None and not r.has_ancestor({"w:del"})), None)
    rpr = base.find("w:rPr") if base is not None else None
    runs = []
    start = 0
    for position in range(1, len(text) + 1):
        if position == len(text) or flags[position] != flags[start]:
            runs.append(_insert_run(text[start:position], rpr_bytes(edit_rpr(doc, rpr, flags[start]))))
            start = position
    body = b"<w:ins " + revs.attrs() + b">" + b"".join(runs) + b"</w:ins>" if runs else b""
    return b"<w:p>" + new_ppr + body + b"</w:p>", text


def _set_track_revisions(package: Package, enabled: bool):
    name = "word/settings.xml"
    data = package.parts.get(name)
    if data is None:
        return None
    doc = XMLDoc(data)
    existing = next(iter(doc.iter("w:trackRevisions")), None)
    if enabled and existing is None and doc.root.self_closing:
        opened = doc.open_tag(doc.root)[:-2].rstrip() + b">"
        return data[:doc.root.start] + opened + b"<w:trackRevisions/></w:settings>" + data[doc.root.end:]
    if enabled and existing is None:
        order_after = {"w:writeProtection", "w:view", "w:zoom", "w:removePersonalInformation",
                       "w:removeDateAndTime", "w:doNotDisplayPageBoundaries", "w:displayBackgroundShape",
                       "w:printPostScriptOverText", "w:printFractionalCharacterWidth", "w:printFormsData",
                       "w:embedTrueTypeFonts", "w:embedSystemFonts", "w:saveSubsetFonts", "w:saveFormsData",
                       "w:mirrorMargins", "w:alignBordersAndEdges", "w:bordersDoNotSurroundHeader",
                       "w:bordersDoNotSurroundFooter", "w:gutterAtTop", "w:hideSpellingErrors",
                       "w:hideGrammaticalErrors", "w:activeWritingStyle", "w:proofState", "w:formsDesign",
                       "w:attachedTemplate", "w:linkStyles", "w:stylePaneFormatFilter", "w:stylePaneSortMethod",
                       "w:documentType", "w:mailMerge", "w:revisionView"}
        position = doc.root.open_end
        for child in doc.root.elements():
            if child.tag in order_after:
                position = child.end
        return data[:position] + b"<w:trackRevisions/>" + data[position:]
    if enabled and existing is not None and existing.get("w:val", "true").lower() in ("0", "false", "off"):
        return data[:existing.start] + b"<w:trackRevisions/>" + data[existing.end:]
    if not enabled and existing is not None:
        return data[:existing.start] + data[existing.end:]
    return None


def _package_blob(package: Package, changes: dict, additions: list) -> bytes:
    import copy
    import io
    import zipfile
    from .docx import FIXED_DATE
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for info in package.infos:
            archive.writestr(copy.copy(info), changes.get(info.filename, package.parts[info.filename]))
        for name, data in additions:
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_STORED if name.startswith("word/media/") else zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


# -- extraction for rewriting agents ------------------------------------------------------

def extract(source, *, select=None) -> list[dict]:
    """Paragraph records for a rewriting agent: key, style, marked text, atoms and hash."""
    package = source if isinstance(source, Package) else Package(source)
    doc = package.document
    check_namespaces(doc)
    fmap = map_fields(doc)
    out = []
    for index, paragraph in enumerate(iter_paragraphs(doc)):
        model = ParagraphModel(doc, fmap, paragraph, index)
        if not model.cells:
            continue
        editable = sum(1 for c in model.cells if not c.atom and c.char.strip())
        record = {"index": index, "paraId": paragraph.get("w14:paraId"), "style": paragraph_style(paragraph),
                  "editable_chars": editable, "text": model.marked(markup=True), "atoms": model.atoms(),
                  "source_hash": model.text_hash()}
        if select is None or select(record):
            out.append(record)
    return out


def html_preview(text: str) -> str:
    return html.escape(text)

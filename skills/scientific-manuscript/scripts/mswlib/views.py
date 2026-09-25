"""Accept-all and reject-all projections of a tracked WordprocessingML part.

The projections follow Word's semantics for the markup found in manuscripts:
run insertions and deletions (nested ones included), moves, paragraph-mark
revisions (which merge a paragraph with the next one), table-row and cell
revisions, and property changes. The merged paragraph keeps the properties of
the following paragraph, as when a paragraph mark is deleted in Word.
"""

from __future__ import annotations

from .wordml import (CELL_REVISIONS, MOVE_RANGES, PROPERTY_CHANGES, RUN_REVISIONS, ZERO_WIDTH,
                     iter_paragraphs, mark_revision, paragraph_text)
from .xmltree import Element, Text, XMLDoc, apply_splices

_REPLACED_BY_OLD = {"w:tblPrChange": "w:tblPr", "w:trPrChange": "w:trPr", "w:tcPrChange": "w:tcPr",
                    "w:tblPrExChange": "w:tblPrEx", "w:sectPrChange": "w:sectPr", "w:tblGridChange": "w:tblGrid"}
_DIRTY_TAGS = RUN_REVISIONS | PROPERTY_CHANGES | MOVE_RANGES | CELL_REVISIONS | {
    "w:delText", "w:delInstrText", "w:customXmlInsRangeStart", "w:customXmlInsRangeEnd",
    "w:customXmlDelRangeStart", "w:customXmlDelRangeEnd", "w:customXmlMoveFromRangeStart",
    "w:customXmlMoveFromRangeEnd", "w:customXmlMoveToRangeStart", "w:customXmlMoveToRangeEnd"}
_DROP_ALWAYS = MOVE_RANGES | {"w:customXmlInsRangeStart", "w:customXmlInsRangeEnd", "w:customXmlDelRangeStart",
                              "w:customXmlDelRangeEnd", "w:customXmlMoveFromRangeStart",
                              "w:customXmlMoveFromRangeEnd", "w:customXmlMoveToRangeStart",
                              "w:customXmlMoveToRangeEnd", "w:numberingChange", "w:cellMerge"}


class _Projector:
    def __init__(self, doc: XMLDoc, mode: str, *, merge: bool = True):
        if mode not in ("accept", "reject"):
            raise ValueError("mode must be 'accept' or 'reject'")
        self.doc = doc
        self.data = doc.data
        self.mode = mode
        self.merge = merge
        self.dirty = set()
        for element in doc.elements:
            if element.tag in _DIRTY_TAGS:
                node = element
                while node is not None and id(node) not in self.dirty:
                    self.dirty.add(id(node))
                    node = node.parent
        self.removed = {"w:del", "w:moveFrom"} if mode == "accept" else {"w:ins", "w:moveTo"}
        self.unwrapped = {"w:ins", "w:moveTo"} if mode == "accept" else {"w:del", "w:moveFrom"}

    def run(self) -> bytes:
        root = self.doc.root
        out: list = [self.data[:root.start]]
        self.emit(root, out)
        out.append(self.data[root.end:])
        return b"".join(out)

    # -- helpers -------------------------------------------------------------------
    def is_marker(self, element: Element) -> bool:
        parent = element.parent
        if parent is None:
            return False
        if parent.tag == "w:rPr" and parent.parent is not None and parent.parent.tag == "w:pPr":
            return True
        return parent.tag in ("w:trPr", "w:tcPr")

    def open_tag(self, element: Element) -> bytes:
        raw = self.doc.open_tag(element)
        if self.mode == "reject" and element.tag in ("w:delText", "w:delInstrText"):
            name = b"w:t" if element.tag == "w:delText" else b"w:instrText"
            raw = b"<" + name + raw[len(element.tag) + 1:]
        return raw

    def close_tag(self, element: Element) -> bytes:
        if element.self_closing:
            return b""
        if self.mode == "reject" and element.tag in ("w:delText", "w:delInstrText"):
            return b"</w:t>" if element.tag == "w:delText" else b"</w:instrText>"
        return self.doc.close_tag(element)

    def row_removed(self, row: Element) -> bool:
        trpr = row.find("w:trPr")
        if trpr is None:
            return False
        return any(c.tag in self.removed for c in trpr.elements())

    def cell_removed(self, cell: Element) -> bool:
        tcpr = cell.find("w:tcPr")
        if tcpr is None:
            return False
        target = "w:cellDel" if self.mode == "accept" else "w:cellIns"
        return tcpr.find(target) is not None

    def merges_with_next(self, paragraph: Element) -> bool:
        if not self.merge:
            return False
        mark = mark_revision(paragraph)
        return (self.mode == "accept" and mark == "del") or (self.mode == "reject" and mark == "ins")

    # -- emission ------------------------------------------------------------------
    def emit(self, element: Element, out: list):
        if id(element) not in self.dirty and element.tag not in _DROP_ALWAYS:
            out.append(self.data[element.start:element.end])
            return
        tag = element.tag
        if tag in _DROP_ALWAYS or (tag in PROPERTY_CHANGES and tag not in _REPLACED_BY_OLD
                                   and tag not in ("w:rPrChange", "w:pPrChange")):
            return
        if tag in ("w:rPrChange", "w:pPrChange") or tag in _REPLACED_BY_OLD:
            return  # handled by the owning properties element, or dropped on accept
        if tag in self.removed:
            return
        if tag in self.unwrapped:
            if self.is_marker(element):
                return
            self.emit_children(element, out)
            return
        if tag in ("w:cellIns", "w:cellDel"):
            return
        if self.mode == "reject" and tag in ("w:rPr", "w:pPr") or (
                self.mode == "reject" and tag in _REPLACED_BY_OLD.values()):
            if self.emit_old_properties(element, out):
                return
        out.append(self.open_tag(element))
        self.emit_children(element, out)
        out.append(self.close_tag(element))

    def emit_old_properties(self, element: Element, out: list) -> bool:
        change_tag = {"w:rPr": "w:rPrChange", "w:pPr": "w:pPrChange"}.get(element.tag)
        if change_tag is None:
            change_tag = next(k for k, v in _REPLACED_BY_OLD.items() if v == element.tag)
        change = element.find(change_tag)
        if change is None:
            return False
        old = change.find(element.tag)
        old_children = old.elements() if old is not None else []
        old_tags = {c.tag for c in old_children}
        out.append(self.doc.open_tag(element) if not element.self_closing else self.doc.open_tag(element)[:-2] + b">")
        for child in old_children:
            self.emit(child, out)
        for child in element.elements():
            if child.tag == change_tag or child.tag in old_tags:
                continue
            if element.tag == "w:pPr" and child.tag not in ("w:rPr", "w:sectPr"):
                continue
            if element.tag == "w:sectPr" and child.tag not in ("w:headerReference", "w:footerReference"):
                continue
            if element.tag in ("w:rPr", "w:tblPr", "w:trPr", "w:tcPr", "w:tblPrEx", "w:tblGrid"):
                continue
            self.emit(child, out)
        out.append(b"</" + element.tag.encode() + b">")
        return True

    def emit_children(self, element: Element, out: list):
        children = element.children
        pending: list = []          # content of paragraphs merged into the next one
        index = 0
        while index < len(children):
            child = children[index]
            index += 1
            if isinstance(child, Text):
                out.append(self.data[child.start:child.end])
                continue
            tag = child.tag
            if tag == "w:tr" and self.row_removed(child):
                continue
            if tag == "w:tc" and self.cell_removed(child):
                continue
            if tag != "w:p":
                if pending and tag in ZERO_WIDTH:
                    pending.append(self.data[child.start:child.end] if id(child) not in self.dirty else b"")
                    continue
                if pending:
                    out.extend(pending)   # no following paragraph: keep the content
                    pending = []
                self.emit(child, out)
                continue
            follower = self.next_paragraph(children, index)
            if self.merges_with_next(child) and follower is not None:
                pending.extend(self.paragraph_content(child))
                continue
            if pending:
                out.append(self.doc.open_tag(child) if not child.self_closing else self.doc.open_tag(child)[:-2] + b">")
                ppr = child.find("w:pPr")
                if ppr is not None:
                    self.emit(ppr, out)
                out.extend(pending)
                pending = []
                out.extend(self.paragraph_content(child))
                out.append(b"</w:p>")
            else:
                self.emit(child, out)
        if pending:
            out.extend(pending)

    def next_paragraph(self, children, index):
        while index < len(children):
            child = children[index]
            index += 1
            if isinstance(child, Text) or child.tag in ZERO_WIDTH:
                continue
            return child if child.tag == "w:p" else None
        return None

    def paragraph_content(self, paragraph: Element) -> list:
        out: list = []
        for child in paragraph.children:
            if isinstance(child, Text):
                out.append(self.data[child.start:child.end])
            elif child.tag != "w:pPr":
                self.emit(child, out)
        return out


def project(doc: XMLDoc, mode: str) -> bytes:
    """Return the bytes of the accept-all or reject-all view of one part."""
    return _Projector(doc, mode).run()


def view_texts(doc: XMLDoc, mode: str) -> list[str]:
    """Paragraph texts of the projected part."""
    projected = XMLDoc(project(doc, mode))
    return [paragraph_text(projected, p) for p in iter_paragraphs(projected)]


def view_doc(doc: XMLDoc, mode: str) -> XMLDoc:
    return XMLDoc(project(doc, mode))


_RAW = "msw-raw"


def positional_view(doc: XMLDoc, mode: str) -> tuple[dict, set, int]:
    """Each paragraph of the part judged on its own, keyed by its position among the part's paragraphs.

    Returns ({position: text in the view}, {positions whose mark the view removes}, paragraph count).
    Paragraphs whose mark is removed are not merged with the next one, so a change can be pinned to
    the paragraph that carries it; paragraphs that vanish from the view (removed table rows) are absent.
    """
    paragraphs = list(iter_paragraphs(doc))
    splices = [(p.start + 4, p.start + 4, f' {_RAW}="{i}"'.encode()) for i, p in enumerate(paragraphs)]
    projected = XMLDoc(_Projector(XMLDoc(apply_splices(doc.data, splices)), mode, merge=False).run())
    texts = {int(p.get(_RAW)): paragraph_text(projected, p) for p in iter_paragraphs(projected)
             if p.get(_RAW) is not None}
    removed = "del" if mode == "accept" else "ins"
    marks = {i for i, p in enumerate(paragraphs) if mark_revision(p) == removed}
    return texts, marks, len(paragraphs)

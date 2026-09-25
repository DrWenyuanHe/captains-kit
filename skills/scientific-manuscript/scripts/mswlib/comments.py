"""Word comment threads: list them with their anchors and replies, and resolve them.

Threads come from comments.xml; replies are linked through commentsExtended.xml
(w15:paraIdParent on the reply's last paragraph). Resolving a thread either marks it
done (Word's Resolve) or removes the comment, its replies and their range markers.
Word does not track comment removal, so it is recorded in the build manifest instead.
"""

from __future__ import annotations

import re

from .docx import MAIN_PART, Package
from .wordml import iter_paragraphs, paragraph_text
from .xmltree import XMLDoc, apply_splices

COMMENTS = "word/comments.xml"
EXTENDED = "word/commentsExtended.xml"
IDS = "word/commentsIds.xml"
EXTENSIBLE = "word/commentsExtensible.xml"


def _comment_text(doc: XMLDoc, comment) -> str:
    return "\n".join(paragraph_text(doc, p) for p in comment.iter("w:p")).strip()


def _anchor_texts(document: XMLDoc) -> dict:
    """Comment id -> the text between its range start and end (current view, deletions excluded)."""
    out = {}
    open_ranges = {}
    for paragraph in iter_paragraphs(document):
        for element in paragraph.iter():
            if element.tag == "w:commentRangeStart":
                open_ranges[element.get("w:id")] = []
            elif element.tag == "w:commentRangeEnd":
                parts = open_ranges.pop(element.get("w:id"), None)
                if parts is not None:
                    out[element.get("w:id")] = "".join(parts)
            elif element.tag == "w:t" and not element.has_ancestor({"w:del", "w:moveFrom"}):
                text = document.text(element)
                for parts in open_ranges.values():
                    parts.append(text)
        for parts in open_ranges.values():
            parts.append("\n")
    return {k: v.strip() for k, v in out.items()}


def threads(package: Package) -> list[dict]:
    """Top-level comments with their replies, anchors, authors, dates and done state."""
    if COMMENTS not in package.parts:
        return []
    doc = package.xml(COMMENTS)
    comments = doc.root.findall("w:comment")
    by_para = {}
    records = []
    for comment in comments:
        paragraphs = list(comment.iter("w:p"))
        last = paragraphs[-1].get("w14:paraId") if paragraphs else None
        record = {"id": comment.get("w:id"), "author": comment.get("w:author"), "initials": comment.get("w:initials"),
                  "date": comment.get("w:date"), "text": _comment_text(doc, comment), "para_id": last,
                  "parent": None, "done": False, "replies": []}
        records.append(record)
        if last:
            by_para[last] = record
    if EXTENDED in package.parts:
        for entry in package.xml(EXTENDED).iter("w15:commentEx"):
            record = by_para.get(entry.get("w15:paraId"))
            if record is None:
                continue
            record["done"] = entry.get("w15:done") == "1"
            parent = entry.get("w15:paraIdParent")
            if parent and parent in by_para:
                record["parent"] = by_para[parent]["id"]
    anchors = _anchor_texts(package.document)
    by_id = {r["id"]: r for r in records}
    top = []
    for record in records:
        record["anchor"] = anchors.get(record["id"], "")
        if record["parent"] and record["parent"] in by_id:
            by_id[record["parent"]]["replies"].append(record)
        else:
            top.append(record)
    return top


def thread_ids(package: Package, comment_id: str) -> list[str]:
    """The comment and every reply below it."""
    found = []

    def walk(items):
        for item in items:
            if item["id"] == comment_id:
                collect(item)
                return True
            if walk(item["replies"]):
                return True
        return False

    def collect(item):
        found.append(item["id"])
        for reply in item["replies"]:
            collect(reply)

    walk(threads(package))
    return found


def remove_threads(package: Package, document: bytes, ids: list[str], pending: dict) -> tuple[bytes, dict]:
    """Remove comments `ids` from the comment parts and their markers from `document` bytes."""
    ids = set(ids)
    doc = XMLDoc(document)
    splices = []
    for element in doc.elements:
        if element.tag in ("w:commentRangeStart", "w:commentRangeEnd") and element.get("w:id") in ids:
            splices.append((element.start, element.end, b""))
        elif element.tag == "w:commentReference" and element.get("w:id") in ids:
            run = element.parent
            others = [c for c in run.elements() if c.tag not in ("w:rPr", "w:commentReference")] \
                if run is not None and run.tag == "w:r" else [None]
            target = run if not others else element
            splices.append((target.start, target.end, b""))
    new_document = apply_splices(doc.data, splices)
    changes = {}
    comments = XMLDoc(pending.get(COMMENTS, package.parts[COMMENTS]))
    removed_para_ids = set()
    comment_splices = []
    for comment in comments.root.findall("w:comment"):
        if comment.get("w:id") in ids:
            removed_para_ids.update(p.get("w14:paraId") for p in comment.iter("w:p") if p.get("w14:paraId"))
            comment_splices.append((comment.start, comment.end, b""))
    changes[COMMENTS] = apply_splices(comments.data, comment_splices)
    durable = set()
    if IDS in package.parts:
        part = XMLDoc(pending.get(IDS, package.parts[IDS]))
        cut = []
        for entry in part.iter("w16cid:commentId"):
            if entry.get("w16cid:paraId") in removed_para_ids:
                durable.add(entry.get("w16cid:durableId"))
                cut.append((entry.start, entry.end, b""))
        changes[IDS] = apply_splices(part.data, cut)
    if EXTENDED in package.parts:
        part = XMLDoc(pending.get(EXTENDED, package.parts[EXTENDED]))
        changes[EXTENDED] = apply_splices(part.data, [(e.start, e.end, b"") for e in part.iter("w15:commentEx")
                                                      if e.get("w15:paraId") in removed_para_ids])
    if EXTENSIBLE in package.parts and durable:
        part = XMLDoc(pending.get(EXTENSIBLE, package.parts[EXTENSIBLE]))
        changes[EXTENSIBLE] = apply_splices(part.data, [(e.start, e.end, b"")
                                                        for e in part.iter("w16cex:commentExtensible")
                                                        if e.get("w16cex:durableId") in durable])
    return new_document, changes


def mark_done(package: Package, ids: list[str], pending: dict) -> dict:
    """Set w15:done="1" on the threads (Word's Resolve); requires commentsExtended.xml."""
    if EXTENDED not in package.parts:
        raise ValueError("this document has no commentsExtended.xml; resolve by removing the thread instead")
    comments = package.xml(COMMENTS)
    para_ids = set()
    for comment in comments.root.findall("w:comment"):
        if comment.get("w:id") in set(ids):
            paragraphs = list(comment.iter("w:p"))
            if paragraphs and paragraphs[-1].get("w14:paraId"):
                para_ids.add(paragraphs[-1].get("w14:paraId"))
    part = XMLDoc(pending.get(EXTENDED, package.parts[EXTENDED]))
    splices = []
    for entry in part.iter("w15:commentEx"):
        if entry.get("w15:paraId") in para_ids:
            tag = part.open_tag(entry)
            if b'w15:done="' in tag:
                new = re.sub(rb'w15:done="[01]"', b'w15:done="1"', tag)
            else:
                new = tag[:-2].rstrip() + b' w15:done="1"/>' if tag.endswith(b"/>") else tag[:-1] + b' w15:done="1">'
            splices.append((entry.start, entry.open_end, new))
    return {EXTENDED: apply_splices(part.data, splices)}


def list_threads(source) -> list[dict]:
    package = source if isinstance(source, Package) else Package(source)
    return threads(package)


__all__ = ["threads", "thread_ids", "remove_threads", "mark_done", "list_threads", "MAIN_PART"]

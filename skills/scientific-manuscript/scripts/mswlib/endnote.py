"""EndNote Cite While You Write (CWYW) census and citation signatures.

A CWYW citation is a complex field whose instruction starts with ADDIN EN.CITE.
Its record data is inline <EndNote> XML (short citations), one base64 fldData
blob (usually repeated in one nested EN.CITE.DATA field), or several nested
EN.CITE.DATA chunks that must be decoded separately and joined as bytes. The
bibliography is one ADDIN EN.REFLIST field that spans many paragraphs. Linked
numbers are w:hyperlink anchors before Word saves the file and nested
HYPERLINK \\l fields afterwards.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET

from .wordml import Field, endnote_payload, iter_paragraphs, map_fields, paragraph_style, paragraph_text
from .xmltree import XMLDoc

PLACEHOLDER = re.compile(r"\[PMIDs?\s*:\s*[\d,;\s]+\]|\[REF\s*:\s*[^\]\n]{1,80}\]"
                         r"|\{[^{}\n]{1,200}?#\d+[^{}\n]*\}", re.I)
_HYPERLINK_TARGET = re.compile(r'\\l\s+"([^"]+)"')
_WS = re.compile(r"\s+")


def _text(element) -> str:
    return "" if element is None else "".join(element.itertext()).strip()


def parse_cites(payload: bytes) -> list[dict]:
    root = ET.fromstring(payload)
    cites = []
    for cite in root.iter("Cite"):
        record = cite.find("record")
        key = record.find("foreign-keys/key") if record is not None else None
        authors = [] if record is None else [_text(a) for a in record.iter("author")]
        cites.append({
            "author": _text(cite.find("Author")),
            "year": _text(cite.find("Year")),
            "recnum": _text(cite.find("RecNum")) or (_text(record.find("rec-number")) if record is not None else ""),
            "db_id": key.get("db-id", "") if key is not None else "",
            "title": _text(record.find("titles/title")) if record is not None else "",
            "journal": (_text(record.find("titles/secondary-title")) or _text(record.find("periodical/full-title")))
            if record is not None else "",
            "pmid": _text(record.find("accession-num")) if record is not None else "",
            "doi": _text(record.find("electronic-resource-num")) if record is not None else "",
            "pages": _text(record.find("pages")) if record is not None else "",
            "volume": _text(record.find("volume")) if record is not None else "",
            "ref_type": _text(record.find("ref-type")) if record is not None else "",
            "authors": authors,
        })
    return cites


def citation_fields(doc: XMLDoc, fields=None) -> list[Field]:
    fields = fields if fields is not None else map_fields(doc).fields
    return [f for f in fields if f.kind == "EN.CITE"]


def citation_signature(doc: XMLDoc, item: Field) -> tuple:
    """Identity of one citation field: instruction, decoded payload and cited records."""
    payload = endnote_payload(item)
    instr = _WS.sub(" ", item.instr).strip()
    if "<EndNote>" in instr:
        instr = instr[:instr.index("<EndNote>")].strip()
    keys = ()
    if payload:
        try:
            keys = tuple((c["db_id"], c["recnum"]) for c in parse_cites(payload))
        except ET.ParseError:
            keys = ("unparsed",)
    digest = hashlib.sha256(payload).hexdigest()[:16] if payload else ""
    blobs = hashlib.sha256()
    stack = [item]
    while stack:
        node = stack.pop()
        for chunk in node.data_chunks:
            blobs.update(_WS.sub("", chunk).encode("ascii", "replace") + b"|")
        stack.extend(node.children)
    return (instr, digest, keys, _WS.sub(" ", item.result).strip(), blobs.hexdigest()[:16])


def bookmark_names(doc: XMLDoc) -> dict:
    return {b.get("w:name"): b.get("w:id") for b in doc.iter("w:bookmarkStart")}


def link_targets(doc: XMLDoc, fields) -> list[str]:
    anchors = [h.get("w:anchor") for h in doc.iter("w:hyperlink") if h.get("w:anchor")]
    field_targets = []
    for item in fields:
        if item.kind == "HYPERLINK":
            match = _HYPERLINK_TARGET.search(item.instr)
            if match:
                field_targets.append(match.group(1))
    return anchors + field_targets


def census(doc: XMLDoc) -> dict:
    """Counts and checks for citations, bibliography, bookmarks and links."""
    fmap = map_fields(doc)
    cites = citation_fields(doc, fmap.fields)
    live = [f for f in cites if not f.deleted_instr]
    forms = {"inline": 0, "single_chunk": 0, "multi_chunk": 0, "none": 0}
    occurrences = 0
    pairs = set()
    db_ids = set()
    failures = 0
    pmids = set()
    records = {}
    for item in live:
        data_children = [c for c in item.children if c.kind == "EN.CITE.DATA"]
        if len(data_children) > 1:
            forms["multi_chunk"] += 1
        elif item.data_chunks or data_children:
            forms["single_chunk"] += 1
        elif "<EndNote>" in item.instr:
            forms["inline"] += 1
        else:
            forms["none"] += 1
        payload = endnote_payload(item)
        if not payload:
            failures += 1
            continue
        try:
            parsed = parse_cites(payload)
        except ET.ParseError:
            failures += 1
            continue
        occurrences += len(parsed)
        for cite in parsed:
            pairs.add((cite["db_id"], cite["recnum"]))
            db_ids.add(cite["db_id"])
            if cite["pmid"]:
                pmids.add(cite["pmid"])
            records.setdefault((cite["db_id"], cite["recnum"]), cite)
    reflists = [f for f in fmap.fields if f.kind == "EN.REFLIST"]
    names = bookmark_names(doc)
    targets = link_targets(doc, fmap.all_fields)
    paragraphs = list(iter_paragraphs(doc))
    placeholders = []
    for index, paragraph in enumerate(paragraphs):
        text = paragraph_text(doc, paragraph)
        for match in PLACEHOLDER.finditer(text):
            placeholders.append({"paragraph": index, "paraId": paragraph.get("w14:paraId"), "text": match.group(0)})
    spans = []
    for item in reflists:
        drawings = 0
        headings = 0
        for paragraph in paragraphs[max(item.first_paragraph, 0):item.last_paragraph + 1]:
            drawings += sum(1 for _ in paragraph.iter("w:drawing"))
            if paragraph_style(paragraph).lower().startswith("heading"):
                headings += 1
        spans.append({"first_paragraph": item.first_paragraph, "last_paragraph": item.last_paragraph,
                      "deleted": item.deleted_instr, "drawings_inside": drawings, "headings_inside": headings,
                      "paragraph_count": item.last_paragraph - item.first_paragraph + 1})
    return {
        "citation_fields_live": len(live),
        "citation_fields_deleted": len(cites) - len(live),
        "payload_forms": forms,
        "payload_parse_failures": failures,
        "cite_occurrences": occurrences,
        "distinct_records": len(pairs),
        "library_db_ids": sorted(db_ids),
        "pmids": sorted(pmids),
        "records_missing_pages": sorted(f"{k[1]}" for k, v in records.items()
                                        if v["ref_type"].startswith("17") and not v["pages"]),
        "records_missing_doi": sorted(f"{k[1]}" for k, v in records.items() if not v["doi"]),
        "reflist": spans,
        "enref_bookmarks": sum(1 for n in names if n and n.startswith("_ENREF")),
        "link_count": len(targets),
        "link_targets_missing": sorted({t for t in targets if t not in names}),
        "placeholders": placeholders,
        "field_errors": fmap.errors,
    }


def bibliography_entries(doc: XMLDoc) -> list[dict]:
    """Rendered bibliography entries (EndNote Bibliography style or inside EN.REFLIST)."""
    fmap = map_fields(doc)
    paragraphs = list(iter_paragraphs(doc))
    inside = set()
    for item in fmap.fields:
        if item.kind == "EN.REFLIST" and not item.deleted_instr:
            inside.update(range(max(item.first_paragraph, 0), item.last_paragraph + 1))
    entries = []
    number_re = re.compile(r"^\s*\[?(\d{1,4})[\].\t ]")
    for index, paragraph in enumerate(paragraphs):
        style = paragraph_style(paragraph).replace(" ", "").lower()
        if index not in inside and "endnotebibliography" not in style:
            continue
        text = paragraph_text(doc, paragraph).strip()
        if not text:
            continue
        match = number_re.match(text)
        doi = re.search(r"\b(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;])", text)
        bookmark = next((b.get("w:name") for b in paragraph.iter("w:bookmarkStart")
                         if (b.get("w:name") or "").startswith("_ENREF")), None)
        entries.append({"paragraph": index, "number": int(match.group(1)) if match else None,
                        "text": text, "doi": doi.group(1) if doi else "", "bookmark": bookmark})
    return entries

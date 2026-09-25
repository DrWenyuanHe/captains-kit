"""refs: citation placeholders, PubMed and Crossref evidence, EndNote payload audits,
bibliography and claim-support audits, library maps and temporary citations.

Sub-commands of `msw.py refs`:

  placeholders DOCX [--json [OUT]]      [PMID: n], [PMIDs: ...], [REF: key], {Author, Year #rec}
  pubmed PMID... | --from-docx DOCX --out DIR
                                        efetch XML with archived evidence -> DIR/records.json
  crossref DOI... | --from-docx DOCX --out DIR
                                        Crossref works -> DIR/crossref.json
  census DOCX [--json OUT]              EndNote census plus payload metadata audit
  bib-audit DOCX --out DIR [--records F]... [--crossref]
                                        rendered bibliography checks and metadata comparison
  claims DOCX --out DIR [--records F]... [--batch-size 40]
                                        claim-support batches for independent judges
  claims-report DIR                     findings -> DIR/CLAIM_AUDIT.md (tiers)
  library-map EXPORT.xml --out map.json EndNote XML export -> PMID to record number
  temp-citations DOCX --map map.json --out NEW.docx
                                        [PMID: n] -> {Author, Year #rec} on a clean copy

Network access goes only to PubMed E-utilities and Crossref through mswlib.http.
Every output is a new file; existing files are never replaced.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .config import load_config, record_path, write_json_exclusive
from .docx import MAIN_PART, Package, PackageError, lock_files, sha256_bytes
from .endnote import bibliography_entries, census as endnote_census, citation_fields, parse_cites
from .http import CACHE_ENV, Client, FetchError, HostNotAllowed, utc_now
from .views import view_doc
from .wordml import (CELL_REVISIONS, MOVE_RANGES, PROPERTY_CHANGES, RUN_REVISIONS, element_char,
                     endnote_payload, iter_paragraphs, map_fields, paragraph_style, paragraph_text)
from .xmltree import XMLDoc, XMLError, apply_splices, escape_text

EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
CROSSREF_WORKS = "https://api.crossref.org/works/"
MAX_EFETCH_BATCH = 200
MIN_ABSTRACT_CHARS = 3000
VERDICTS = ("SUPPORTED", "WEAK", "MISMATCH", "UNVERIFIED_NO_ABSTRACT")
ACTIONS = ("REUSE", "ADD", "TEXT", "NONE")
CONFIDENCE = ("high", "medium", "low")
TRACKED_TAGS = RUN_REVISIONS | PROPERTY_CHANGES | MOVE_RANGES | CELL_REVISIONS | {"w:delText", "w:delInstrText"}

# Tests replace this with a fake transport; None means urllib.
TRANSPORT = None


class RefsError(ValueError):
    """A refusal or unusable input; the message says what to do."""


# =====================================================================================
# Shared text helpers
# =====================================================================================

_SKIP_TEXT = ("w:p", "mc:Fallback", "w:pPr", "w:rPr", "w:instrText", "w:delInstrText", "w:rPrChange",
              "w:pPrChange", "w:fldData")
_ENTITY = re.compile(r"&(?:#x[0-9A-Fa-f]+|#[0-9]+|lt|gt|amp|quot|apos);")
_WS = re.compile(r"\s+")


def paragraph_segments(doc: XMLDoc, paragraph) -> list:
    """[(element, text)] in the order wordml.paragraph_text reads them (deletions excluded)."""
    out = []
    stack = list(reversed(paragraph.elements()))
    while stack:
        node = stack.pop()
        if node.tag in _SKIP_TEXT:
            continue
        char = element_char(doc, node, deleted=False)
        if char is not None:
            out.append((node, char))
            continue
        if node.tag in ("w:del", "w:moveFrom"):
            continue
        stack.extend(reversed(node.elements()))
    return out


def _spans(segments) -> list:
    spans, position = [], 0
    for element, text in segments:
        spans.append((element, position, position + len(text)))
        position += len(text)
    return spans


def _inside_one_wt(spans, start: int, end: int):
    for element, a, b in spans:
        if element.tag == "w:t" and a <= start and end <= b:
            return element, a
    return None


def _clean(text: str) -> str:
    text = _WS.sub(" ", text).strip()
    return re.sub(r" +([.,;:!?)\]])", r"\1", text).replace("( ", "(")


def _words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.findall(r"[a-z0-9]+", text)


def _itext(element) -> str:
    """Whole text of an element including <style> or markup children, whitespace collapsed."""
    return "" if element is None else _WS.sub(" ", "".join(element.itertext())).strip()


def normalize_doi(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I).rstrip(".,;")
    return value.lower() if re.match(r"^10\.\d{4,9}/\S+$", value) else ""


def _surname(author: str) -> str:
    """EndNote's first-author surname: text before the comma, or the whole corporate name."""
    author = (author or "").strip()
    if not author:
        return ""
    if author.endswith(","):
        return author[:-1].strip()
    if "," in author:
        return author.split(",", 1)[0].strip()
    return author.split()[-1]


def _write_text_exclusive(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def _refuse_existing(paths):
    existing = [str(p) for p in paths if Path(p).exists()]
    if existing:
        raise RefsError("refusing to replace existing file(s): " + ", ".join(existing)
                        + "; choose a new output folder or name")


# =====================================================================================
# EndNote settings (docVars)
# =====================================================================================

def endnote_docvars(package: Package) -> dict:
    """EN.Layout and EN.InstantFormat values from word/settings.xml (as dicts)."""
    if "word/settings.xml" not in package.parts:
        return {}
    out = {}
    for var in package.xml("word/settings.xml").iter("w:docVar"):
        name = var.get("w:name", "")
        if not name.startswith("EN."):
            continue
        value = var.get("w:val", "")
        if name == "EN.Libraries":
            out[name] = {"present": True, "chars": len(value)}
            continue
        try:
            root = ET.fromstring(value)
            out[name] = {child.tag: "".join(child.itertext()) for child in root}
        except ET.ParseError:
            out[name] = {m.group(1): m.group(2) for m in re.finditer(r"<(\w+)>([^<]*)</\1>", value)}
    return out


def layout_delimiters(package: Package) -> tuple[str, str]:
    layout = endnote_docvars(package).get("EN.Layout", {})
    return layout.get("LeftDelim") or "{", layout.get("RightDelim") or "}"


# =====================================================================================
# Placeholder inventory
# =====================================================================================

_PMID_GROUP = (r"\[(?P<kw>PMIDs?)\s*:\s*(?P<ids>\d{1,9}(?!\d)(?:(?:\s*[,;]\s*|\s+)\d{1,9}(?!\d))*)"
               r"\s*[,;]?\s*\]")
_REF = r"\[REF\s*:\s*(?P<ref>[^\[\]\n]{1,120}?)\s*\]"
_MALFORMED = re.compile(r"\[(?:PMIDs?|REF)\b[^\]\n]{0,80}\]?", re.I)


def placeholder_pattern(left: str = "{", right: str = "}", *, temporary: bool = True) -> re.Pattern:
    parts = [_PMID_GROUP, _REF]
    if temporary:
        opening, closing = re.escape(left), re.escape(right)
        body = f"(?:(?!{opening}|{closing})[^\\n])"
        parts.append(opening + f"(?P<temp>{body}{{1,600}}?#\\s*\\d+{body}{{0,200}}?)" + closing)
    return re.compile("|".join(parts), re.I)


def parse_temporary(body: str) -> list[dict]:
    """'Doe, 2020 #12;Roe, 2019 #7' -> [{author, year, recnum}] (backtick escapes honoured)."""
    pieces, current, escaped = [], [], False
    for char in body:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "`":
            escaped = True
        elif char == ";":
            pieces.append("".join(current))
            current = []
        else:
            current.append(char)
    pieces.append("".join(current))
    out = []
    for piece in pieces:
        match = re.match(r"^\s*(?P<author>.*?)\s*(?:,\s*(?P<year>\d{4}[a-z]?|n\.d\.))?\s*#\s*(?P<rec>\d+)\s*$", piece)
        if match:
            out.append({"author": match.group("author"), "year": match.group("year") or "",
                        "recnum": match.group("rec")})
        else:
            out.append({"unparsed": piece.strip()})
    return out


def _classify(match: re.Match) -> dict:
    if match.group("kw"):
        ids = re.findall(r"\d+", match.group("ids"))
        return {"kind": "pmids" if len(ids) > 1 or match.group("kw").lower() == "pmids" else "pmid", "pmids": ids}
    if match.groupdict().get("ref"):
        return {"kind": "ref", "key": match.group("ref").strip()}
    return {"kind": "temporary", "cites": parse_temporary(match.group("temp"))}


def placeholder_inventory(package: Package, *, context: int = 80) -> dict:
    """Every citation placeholder with paragraph index, paraId and context."""
    doc = package.document
    left, right = layout_delimiters(package)
    pattern = placeholder_pattern(left, right)
    items, malformed = [], []
    for index, paragraph in enumerate(iter_paragraphs(doc)):
        segments = paragraph_segments(doc, paragraph)
        text = "".join(t for _, t in segments)
        if "[" not in text and left not in text:
            continue
        spans = _spans(segments)
        taken = []
        for match in pattern.finditer(text):
            start, end = match.span()
            taken.append((start, end))
            item = {"paragraph": index, "paraId": paragraph.get("w14:paraId"), "text": match.group(0),
                    "start": start, "end": end}
            item.update(_classify(match))
            item["single_run"] = _inside_one_wt(spans, start, end) is not None
            item["context_before"] = text[max(0, start - context):start]
            item["context_after"] = text[end:end + context]
            items.append(item)
        for match in _MALFORMED.finditer(text):
            if not any(a <= match.start() < b for a, b in taken):
                malformed.append({"paragraph": index, "paraId": paragraph.get("w14:paraId"), "text": match.group(0),
                                  "context": text[max(0, match.start() - 40):match.end() + 40]})
    pmid_order, seen = [], set()
    for item in items:
        for pmid in item.get("pmids", []):
            if pmid not in seen:
                seen.add(pmid)
                pmid_order.append(pmid)
    kinds = {}
    for item in items:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    return {
        "source": record_path(package.path),
        "sha256": package.sha256,
        "delimiters": [left, right],
        "summary": {
            "placeholders": len(items), "by_kind": kinds,
            "pmid_occurrences": sum(len(i.get("pmids", [])) for i in items),
            "distinct_pmids": len(pmid_order),
            "ref_keys": sorted({i["key"] for i in items if i["kind"] == "ref"}),
            "temporary_citations": kinds.get("temporary", 0),
            "split_across_runs": sum(1 for i in items if not i["single_run"]),
            "malformed": len(malformed),
        },
        "pmids_in_order": pmid_order,
        "items": items,
        "malformed": malformed,
    }


def docx_pmids(package: Package) -> list[str]:
    """PMIDs from placeholders, then from live citation payloads, in document order."""
    inventory = placeholder_inventory(package)
    out = list(inventory["pmids_in_order"])
    seen = set(out)
    for entry in field_cites(package.document):
        for cite in entry["cites"]:
            pmid = cite.get("pmid", "").strip()
            if re.fullmatch(r"\d{1,9}", pmid) and pmid not in seen:
                seen.add(pmid)
                out.append(pmid)
    return out


def docx_dois(package: Package) -> list[str]:
    out, seen = [], set()
    candidates = [c.get("doi", "") for e in field_cites(package.document) for c in e["cites"]]
    candidates += [e["doi"] for e in bibliography_entries(package.document)]
    for value in candidates:
        doi = normalize_doi(value)
        if doi and doi not in seen:
            seen.add(doi)
            out.append(doi)
    return out


# =====================================================================================
# EndNote payload records and audit
# =====================================================================================

def field_cites(doc: XMLDoc, fields=None) -> list[dict]:
    """One entry per live EN.CITE field: its parsed <Cite> records, or an error."""
    out = []
    live = [f for f in citation_fields(doc, fields) if not f.deleted_instr]
    for index, item in enumerate(live):
        entry = {"field_index": index, "paragraph": item.first_paragraph, "field": item, "cites": [], "error": ""}
        payload = endnote_payload(item)
        if not payload:
            entry["error"] = "no EndNote payload (instruction without data)"
        else:
            try:
                entry["cites"] = parse_cites(payload)
            except ET.ParseError as error:
                entry["error"] = f"payload does not parse: {error}"
        out.append(entry)
    return out


def record_key(cite: dict) -> str:
    return f"{cite.get('db_id', '')}#{cite.get('recnum', '')}"


_ORG_WORDS = re.compile(
    r"\b(collaborat\w*|committee|cent(?:er|re)s?|consortium|group|association|organi[sz]ation|institutes?|society|"
    r"network|investigators|council|agency|department|board|foundation|programme|program|task force|working party|"
    r"panel|alliance|federation|statistics|survey|office|ministry|commission|registry|trialists|coalition|union|"
    r"academy|college|services?|administration|bureau|authority|world health)\b", re.I)


def payload_audit(doc: XMLDoc) -> dict:
    """Metadata EndNote will print, audited from the citation payloads (no GUI needed)."""
    entries = field_cites(doc)
    records: dict = {}
    occurrences = 0
    for entry in entries:
        for cite in entry["cites"]:
            occurrences += 1
            records.setdefault(record_key(cite), cite)

    def brief(key, cite):
        return {"key": key, "recnum": cite["recnum"], "db_id": cite["db_id"], "author": cite["author"],
                "year": cite["year"], "title": cite["title"]}

    journal = {k: c for k, c in records.items() if c.get("ref_type", "").strip() == "17"}
    missing_pages = [brief(k, c) for k, c in journal.items() if not c.get("pages")]
    missing_doi = [brief(k, c) for k, c in records.items() if not normalize_doi(c.get("doi", ""))]
    missing_pmid = [brief(k, c) for k, c in journal.items() if not re.fullmatch(r"\d{1,9}", c.get("pmid", "").strip())]
    corporate_ok, corporate_suspects, no_comma = [], [], []
    for key, cite in records.items():
        for author in cite.get("authors", []):
            if author.endswith(","):
                corporate_ok.append({"key": key, "author": author})
            elif _ORG_WORDS.search(author):
                corporate_suspects.append({"key": key, "author": author,
                                           "fix": "add a trailing comma in EndNote so the name is kept whole"})
            elif "," not in author and len(author.split()) >= 2:
                no_comma.append({"key": key, "author": author})
    by_author_year: dict = {}
    for key, cite in records.items():
        label = (_surname(cite.get("author") or (cite.get("authors") or [""])[0]).lower(), cite.get("year", ""))
        by_author_year.setdefault(label, []).append(key)
    collisions = [{"author": a, "year": y, "records": [brief(k, records[k]) for k in keys]}
                  for (a, y), keys in sorted(by_author_year.items()) if len(keys) > 1]
    by_recnum: dict = {}
    for key, cite in records.items():
        by_recnum.setdefault(cite["recnum"], set()).add(cite["db_id"])
    recnum_collisions = [{"recnum": r, "db_ids": sorted(d)} for r, d in sorted(by_recnum.items()) if len(d) > 1]
    duplicate_pmids, duplicate_dois = {}, {}
    for key, cite in records.items():
        pmid = cite.get("pmid", "").strip()
        if pmid:
            duplicate_pmids.setdefault(pmid, []).append(key)
        doi = normalize_doi(cite.get("doi", ""))
        if doi:
            duplicate_dois.setdefault(doi, []).append(key)
    duplicate_pmids = {k: v for k, v in duplicate_pmids.items() if len(v) > 1}
    duplicate_dois = {k: v for k, v in duplicate_dois.items() if len(v) > 1}
    db_ids = sorted({c["db_id"] for c in records.values()})
    warnings = []
    if len(db_ids) > 1:
        warnings.append(f"{len(db_ids)} EndNote libraries (db-ids) are cited; record numbers are only unique "
                        "inside one library, and temporary citations format against the open library")
    if missing_pages:
        warnings.append(f"{len(missing_pages)} journal article(s) without pages or article number (eLocator)")
    if missing_doi:
        warnings.append(f"{len(missing_doi)} record(s) without a DOI")
    if missing_pmid:
        warnings.append(f"{len(missing_pmid)} journal article(s) without a PMID in accession-num")
    if corporate_suspects:
        warnings.append(f"{len(corporate_suspects)} organisation-like author(s) without a trailing comma")
    if collisions:
        warnings.append(f"{len(collisions)} (first author, year) collision(s): temporary citations need "
                        "the record number")
    if recnum_collisions:
        warnings.append(f"{len(recnum_collisions)} record number(s) used by more than one library")
    if duplicate_pmids or duplicate_dois:
        warnings.append("the same paper appears under more than one record")
    return {
        "fields": len(entries), "payload_errors": [{"field_index": e["field_index"], "paragraph": e["paragraph"],
                                                     "error": e["error"]} for e in entries if e["error"]],
        "cite_occurrences": occurrences, "records": len(records), "journal_articles": len(journal),
        "db_id_count": len(db_ids), "db_ids": db_ids,
        "missing_pages_or_elocator": missing_pages, "missing_doi": missing_doi, "missing_pmid": missing_pmid,
        "corporate_authors_with_comma": corporate_ok, "corporate_author_suspects": corporate_suspects,
        "authors_without_comma": no_comma, "author_year_collisions": collisions,
        "recnum_collisions": recnum_collisions, "duplicate_pmids": duplicate_pmids,
        "duplicate_dois": duplicate_dois, "warnings": warnings,
    }


def census_with_audit(package: Package) -> dict:
    data = endnote_census(package.document)
    data["endnote_settings"] = {k: v for k, v in endnote_docvars(package).items()}
    data["payload_audit"] = payload_audit(package.document)
    return data


# =====================================================================================
# PubMed
# =====================================================================================

def _year_from(text: str) -> str:
    match = re.search(r"(1[89]\d\d|20\d\d)", text or "")
    return match.group(1) if match else ""


def _authors_pubmed(author_list) -> list[dict]:
    out = []
    if author_list is None:
        return out
    for author in author_list.findall("Author"):
        if author.get("ValidYN", "Y") == "N":
            continue
        collective = author.find("CollectiveName")
        if collective is not None:
            name = _itext(collective)
            out.append({"collective": name, "display": name})
            continue
        last = _itext(author.find("LastName"))
        initials = _itext(author.find("Initials"))
        fore = _itext(author.find("ForeName"))
        if not initials and fore:
            initials = "".join(part[0] for part in re.split(r"[\s\-]+", fore) if part)
        out.append({"last": last, "initials": initials, "fore": fore, "display": f"{last} {initials}".strip()})
    return out


def _abstract(element) -> str:
    if element is None:
        return ""
    parts = []
    for text in element.findall("AbstractText"):
        body = _itext(text)
        if not body:
            continue
        label = text.get("Label")
        parts.append(f"{label}: {body}" if label else body)
    return "\n".join(parts)


def _parse_pubmed_article(article) -> dict:
    citation = article.find("MedlineCitation")
    art = citation.find("Article") if citation is not None else None
    if art is None:
        return {"pmid": _itext(citation.find("PMID")) if citation is not None else "", "kind": "article",
                "error": "no Article element"}
    journal = art.find("Journal")
    issue = journal.find("JournalIssue") if journal is not None else None
    pubdate = issue.find("PubDate") if issue is not None else None
    year = ""
    if pubdate is not None:
        year = _itext(pubdate.find("Year")) or _year_from(_itext(pubdate.find("MedlineDate")))
    epub = art.find("ArticleDate")
    epub_date = ""
    if epub is not None:
        epub_date = "-".join(v for v in (_itext(epub.find("Year")), _itext(epub.find("Month")),
                                         _itext(epub.find("Day"))) if v)
    elocations = [{"type": e.get("EIdType", ""), "valid": e.get("ValidYN", "Y"), "value": _itext(e)}
                  for e in art.findall("ELocationID")]
    ids = {}
    for node in article.findall("PubmedData/ArticleIdList/ArticleId"):
        ids.setdefault(node.get("IdType", ""), _itext(node))
    doi = next((e["value"] for e in elocations if e["type"] == "doi" and e["valid"] != "N"), "") or ids.get("doi", "")
    pii = next((e["value"] for e in elocations if e["type"] == "pii" and e["valid"] != "N"), "") or ids.get("pii", "")
    authors = _authors_pubmed(art.find("AuthorList"))
    abstract = _abstract(art.find("Abstract"))
    return {
        "pmid": _itext(citation.find("PMID")), "kind": "article",
        "title": _itext(art.find("ArticleTitle")) or _itext(art.find("VernacularTitle")),
        "authors": authors, "author_display": [a["display"] for a in authors],
        "first_author": authors[0]["display"] if authors else "",
        "journal": _itext(journal.find("Title")) if journal is not None else "",
        "journal_iso": _itext(journal.find("ISOAbbreviation")) if journal is not None else "",
        "medline_ta": _itext(citation.find("MedlineJournalInfo/MedlineTA")),
        "year": year, "epub_date": epub_date, "epub_year": _year_from(epub_date),
        "volume": _itext(issue.find("Volume")) if issue is not None else "",
        "issue": _itext(issue.find("Issue")) if issue is not None else "",
        "pages": _itext(art.find("Pagination/MedlinePgn")),
        "start_page": _itext(art.find("Pagination/StartPage")),
        "elocation": elocations, "pii": pii, "doi": doi, "pmcid": ids.get("pmc", ""),
        "publication_types": [_itext(p) for p in art.findall("PublicationTypeList/PublicationType")],
        "language": _itext(art.find("Language")),
        "abstract": abstract, "abstract_available": bool(abstract),
    }


def _parse_pubmed_book(article) -> dict:
    document = article.find("BookDocument")
    book = document.find("Book") if document is not None else None
    authors = _authors_pubmed(document.find("AuthorList") if document is not None else None)
    abstract = _abstract(document.find("Abstract") if document is not None else None)
    ids = {n.get("IdType", ""): _itext(n) for n in (document.findall("ArticleIdList/ArticleId")
                                                     if document is not None else [])}
    return {
        "pmid": _itext(document.find("PMID")) if document is not None else "", "kind": "book",
        "title": _itext(document.find("ArticleTitle")) or _itext(book.find("BookTitle") if book is not None else None),
        "authors": authors, "author_display": [a["display"] for a in authors],
        "first_author": authors[0]["display"] if authors else "",
        "journal": _itext(book.find("BookTitle")) if book is not None else "", "journal_iso": "", "medline_ta": "",
        "year": (_itext(book.find("PubDate/Year")) or _year_from(_itext(book.find("PubDate/MedlineDate"))))
        if book is not None else "",
        "epub_date": "", "epub_year": "", "volume": "", "issue": "", "pages": "", "start_page": "",
        "elocation": [], "pii": "", "doi": ids.get("doi", ""), "pmcid": ids.get("pmc", ""),
        "publisher": _itext(book.find("Publisher/PublisherName")) if book is not None else "",
        "publication_types": [], "language": _itext(document.find("Language")) if document is not None else "",
        "abstract": abstract, "abstract_available": bool(abstract),
    }


def parse_pubmed_xml(data: bytes) -> list[dict]:
    """Parse an efetch (retmode=xml) response into records (itertext throughout)."""
    root = ET.fromstring(data)
    if root.tag != "PubmedArticleSet":
        message = _itext(root.find(".//ERROR")) or root.tag
        raise RefsError(f"unexpected efetch response ({message})")
    counters: dict = {}
    records = []
    for child in root:
        if child.tag not in ("PubmedArticle", "PubmedBookArticle"):
            continue
        counters[child.tag] = counters.get(child.tag, 0) + 1
        record = _parse_pubmed_article(child) if child.tag == "PubmedArticle" else _parse_pubmed_book(child)
        record["xpath"] = f"/PubmedArticleSet/{child.tag}[{counters[child.tag]}]"
        records.append(record)
    return records


def _valid_pmids(values) -> list[str]:
    out, bad, seen = [], [], set()
    for value in values:
        for token in re.split(r"[,;\s]+", str(value).strip()):
            if not token:
                continue
            token = re.sub(r"^PMID:?", "", token, flags=re.I)
            if re.fullmatch(r"\d{1,9}", token):
                if token not in seen:
                    seen.add(token)
                    out.append(token)
            else:
                bad.append(token)
    if bad:
        raise RefsError("not PMIDs: " + ", ".join(bad[:10]))
    return out


def fetch_pubmed(pmids, out_dir, client: Client, *, batch_size: int = MAX_EFETCH_BATCH, log=None) -> dict:
    """efetch in batches; archive raw XML, request JSON and SHA-256; write out_dir/records.json."""
    pmids = _valid_pmids(pmids)
    if not pmids:
        raise RefsError("no PMIDs to fetch")
    batch_size = max(1, min(int(batch_size), MAX_EFETCH_BATCH))
    out = Path(out_dir)
    evidence = out / "evidence"
    batches = [pmids[i:i + batch_size] for i in range(0, len(pmids), batch_size)]
    width = max(2, len(str(len(batches))))
    names = [f"pubmed_batch_{n:0{width}d}" for n in range(1, len(batches) + 1)]
    planned = [out / "records.json"] + [evidence / f"{n}{s}" for n in names for s in (".xml", ".request.json",
                                                                                          ".xml.sha256")]
    _refuse_existing(planned)
    evidence.mkdir(parents=True, exist_ok=True)
    records, status, errors, archive = {}, {}, [], []
    for number, (batch, name) in enumerate(zip(batches, names), 1):
        data = {"db": "pubmed", "id": ",".join(batch), "retmode": "xml", **client.ncbi_params()}
        try:
            response = client.post(EFETCH_URL, data)
        except FetchError as error:
            errors.append({"batch": number, "pmids": batch, "error": str(error)})
            for pmid in batch:
                status[pmid] = "fetch_error"
            if log:
                log(f"batch {number}/{len(batches)}: FETCH ERROR ({error.status or 'network'})")
            continue
        xml_path = evidence / f"{name}.xml"
        with open(xml_path, "xb") as handle:
            handle.write(response.body)
        with open(evidence / f"{name}.xml.sha256", "x", encoding="ascii", newline="\n") as handle:
            handle.write(f"{response.sha256}  {name}.xml\n")
        request = {"endpoint": EFETCH_URL, "batch": number, "pmids": batch, **response.record()}
        write_json_exclusive(evidence / f"{name}.request.json", request)
        archive.append({"batch": number, "file": f"evidence/{name}.xml", "sha256": response.sha256,
                        "request": f"evidence/{name}.request.json", "pmids": batch,
                        "from_cache": response.from_cache})
        try:
            parsed = parse_pubmed_xml(response.body)
        except (ET.ParseError, RefsError) as error:
            errors.append({"batch": number, "pmids": batch, "error": f"unparseable response: {error}"})
            for pmid in batch:
                status[pmid] = "fetch_error"
            continue
        wanted = set(batch)
        for record in parsed:
            pmid = record.get("pmid", "")
            if pmid not in wanted:
                continue
            record["evidence"] = {"file": f"evidence/{name}.xml", "sha256": response.sha256,
                                  "xpath": record.pop("xpath")}
            records[pmid] = record
            status[pmid] = "ok" if record.get("abstract_available") else "no_abstract"
        for pmid in batch:
            status.setdefault(pmid, "not_found")
        if log:
            found = sum(1 for p in batch if p in records)
            log(f"batch {number}/{len(batches)}: {found}/{len(batch)} record(s)"
                + (" (cache)" if response.from_cache else ""))
    counts = {}
    for value in status.values():
        counts[value] = counts.get(value, 0) + 1
    result = {
        "schema": "msw-refs-records/1", "source": "pubmed", "generated": utc_now(), "endpoint": EFETCH_URL,
        "contact_set": bool(client.contact), "api_key_set": bool(client.api_key),
        "requested": pmids, "counts": counts, "status": {p: status[p] for p in pmids},
        "records": {p: records[p] for p in pmids if p in records}, "evidence": archive, "errors": errors,
    }
    write_json_exclusive(out / "records.json", result)
    return result


# =====================================================================================
# Crossref
# =====================================================================================

def _strip_tags(text: str) -> str:
    return _WS.sub(" ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def parse_crossref_work(message: dict) -> dict:
    def first(value):
        if isinstance(value, list):
            return value[0] if value else ""
        return value or ""

    def year_of(key):
        parts = (message.get(key) or {}).get("date-parts") or []
        return str(parts[0][0]) if parts and parts[0] and parts[0][0] else ""

    authors = []
    for author in message.get("author") or []:
        if author.get("family"):
            given = author.get("given", "")
            initials = "".join(p[0] for p in re.split(r"[\s.\-]+", given) if p)
            authors.append({"family": author["family"], "given": given,
                            "display": f"{author['family']} {initials}".strip()})
        elif author.get("name"):
            authors.append({"collective": author["name"], "display": author["name"]})
    years = {year_of(k) for k in ("issued", "published-print", "published-online", "published")} - {""}
    return {
        "doi": (message.get("DOI") or "").lower(),
        "title": _strip_tags(first(message.get("title"))),
        "container": _strip_tags(first(message.get("container-title"))),
        "container_short": _strip_tags(first(message.get("short-container-title"))),
        "year": year_of("issued") or year_of("published-print") or year_of("published-online"),
        "years": sorted(years),
        "volume": message.get("volume", ""), "issue": message.get("issue", ""), "page": message.get("page", ""),
        "article_number": message.get("article-number", ""), "type": message.get("type", ""),
        "authors": authors, "author_display": [a["display"] for a in authors],
        "first_author": authors[0]["display"] if authors else "",
        "abstract": _strip_tags(message.get("abstract", "")), "url": message.get("URL", ""),
    }


def fetch_crossref(dois, out_dir, client: Client, *, log=None) -> dict:
    """Crossref /works/{doi} per DOI with evidence; writes out_dir/crossref.json."""
    wanted, bad = [], []
    for value in dois:
        doi = normalize_doi(value)
        if doi and doi not in wanted:
            wanted.append(doi)
        elif not doi:
            bad.append(value)
    if bad:
        raise RefsError("not DOIs: " + ", ".join(map(str, bad[:10])))
    if not wanted:
        raise RefsError("no DOIs to look up")
    out = Path(out_dir)
    evidence = out / "evidence"
    width = max(3, len(str(len(wanted))))
    names = [f"crossref_{n:0{width}d}" for n in range(1, len(wanted) + 1)]
    _refuse_existing([out / "crossref.json"] + [evidence / f"{n}{s}" for n in names
                                                 for s in (".json", ".request.json")])
    evidence.mkdir(parents=True, exist_ok=True)
    records, status, errors = {}, {}, []
    for doi, name in zip(wanted, names):
        try:
            response = client.get(CROSSREF_WORKS + urllib.parse.quote(doi, safe="/"))
        except FetchError as error:
            if error.status == 404:
                status[doi] = "not_found"
                errors.append({"doi": doi, "error": "not in Crossref (DataCite DOIs such as Zenodo or figshare "
                                                    "are not; check the DOI at doi.org)"})
            else:
                status[doi] = "fetch_error"
                errors.append({"doi": doi, "error": str(error)})
            continue
        with open(evidence / f"{name}.json", "xb") as handle:
            handle.write(response.body)
        write_json_exclusive(evidence / f"{name}.request.json", {"endpoint": CROSSREF_WORKS, "doi": doi,
                                                                  **response.record()})
        try:
            message = response.json().get("message") or {}
        except (ValueError, AttributeError) as error:
            status[doi] = "fetch_error"
            errors.append({"doi": doi, "error": f"unparseable response: {error}"})
            continue
        record = parse_crossref_work(message)
        record["evidence"] = {"file": f"evidence/{name}.json", "sha256": response.sha256}
        records[doi] = record
        status[doi] = "ok"
        if log:
            log(f"{doi}: ok" + (" (cache)" if response.from_cache else ""))
    counts = {}
    for value in status.values():
        counts[value] = counts.get(value, 0) + 1
    result = {"schema": "msw-refs-crossref/1", "source": "crossref", "generated": utc_now(),
              "endpoint": CROSSREF_WORKS, "contact_set": bool(client.contact), "requested": wanted,
              "counts": counts, "status": status, "records": records, "errors": errors}
    write_json_exclusive(out / "crossref.json", result)
    return result


# =====================================================================================
# Record index (records.json from pubmed, crossref.json from crossref)
# =====================================================================================

class RecordIndex:
    def __init__(self):
        self.by_pmid: dict = {}
        self.by_doi: dict = {}
        self.sources: list = []

    @classmethod
    def load(cls, paths) -> "RecordIndex":
        index = cls()
        for path in paths or []:
            raw = Path(path).read_bytes()
            data = json.loads(raw.decode("utf-8-sig"))
            schema = data.get("schema", "")
            if schema not in ("msw-refs-records/1", "msw-refs-crossref/1"):
                raise RefsError(f"{path}: not a records.json or crossref.json written by 'refs pubmed/crossref'")
            source = "pubmed" if schema == "msw-refs-records/1" else "crossref"
            index.sources.append({"path": record_path(path), "sha256": sha256_bytes(raw), "source": source,
                                  "records": len(data.get("records", {}))})
            for key, record in (data.get("records") or {}).items():
                record = dict(record, _source=source)
                if source == "pubmed":
                    index.by_pmid.setdefault(record.get("pmid") or key, record)
                doi = normalize_doi(record.get("doi", ""))
                if doi:
                    existing = index.by_doi.get(doi)
                    if existing is None or (source == "pubmed" and existing.get("_source") != "pubmed"):
                        index.by_doi[doi] = record
        return index

    def lookup(self, pmid: str = "", doi: str = ""):
        if pmid and pmid in self.by_pmid:
            return self.by_pmid[pmid]
        doi = normalize_doi(doi)
        return self.by_doi.get(doi) if doi else None

    def all_records(self) -> list:
        seen, out = set(), []
        for record in list(self.by_pmid.values()) + list(self.by_doi.values()):
            if id(record) not in seen:
                seen.add(id(record))
                out.append(record)
        return out


def _record_years(record: dict) -> set:
    years = set()
    for key in ("year", "epub_year"):
        if record.get(key):
            years.add(int(record[key]))
    for value in record.get("years", []) or []:
        if str(value).isdigit():
            years.add(int(value))
    return years


# =====================================================================================
# Bibliography audit
# =====================================================================================

STOPWORDS = frozenset(
    "a an and are as at be by for from in into is it its of on or that the their this to was were with "
    "without within than via vs versus after before between among during under over".split())


def title_coverage(title: str, text: str):
    words = [w for w in _words(title) if w not in STOPWORDS and len(w) > 1]
    if not words:
        return None
    present = set(_words(text))
    return round(sum(1 for w in words if w in present) / len(words), 3)


_YEAR = r"(1[89]\d\d|20\d\d)"
_YEAR_PATTERNS = {
    "author_date": re.compile(r"\(" + _YEAR + r"[a-z]?\)"),
    # 2020;12:1-9 and 2020 Jan 5;12:1-9 (a colon is not accepted: titles contain "2019: a review")
    "vancouver": re.compile(r"(?<!\d)" + _YEAR
                            + r"[a-z]?(?:\s+[A-Z][a-z]{2}(?:[-/][A-Z][a-z]{2})?(?:\s+\d{1,2})?)?\s*;"),
    "year_dot": re.compile(r"[.,]\s" + _YEAR + r"[a-z]?[.,]"),
}


def entry_year(text: str, doi: str = "", style: str = "") -> tuple[str, str]:
    """(year, pattern) from a rendered entry; pattern order follows the citation style."""
    body = text.replace(doi, " ") if doi else text
    body = re.sub(r"(?:https?://|doi:\s*|10\.\d{4,9}/)\S+", " ", body, flags=re.I)
    author_date = bool(re.search(r"apa|harvard|author|chicago|date", style or "", re.I))
    order = (["author_date", "year_dot", "vancouver"] if author_date else ["vancouver", "author_date", "year_dot"])
    for name in order:
        match = _YEAR_PATTERNS[name].search(body)
        if match:
            return match.group(1), name
    years = re.findall(r"(?<!\d)" + _YEAR + r"(?!\d)", body)
    return (years[-1], "fallback") if years else ("", "none")


def bibliography_audit(package: Package, index: RecordIndex | None = None, *, style: str = "",
                       fetch_errors: dict | None = None) -> dict:
    """Numbering, DOI and record checks of the rendered bibliography.

    fetch_errors maps a DOI whose lookup failed (network or HTTP error) to the error text. Such an
    entry without a record is FETCH_ERROR (not checked), never NO_RECORD (no record exists).
    """
    fetch_errors = {normalize_doi(d): e for d, e in (fetch_errors or {}).items()}
    entries = bibliography_entries(package.document)
    style = style or endnote_docvars(package).get("EN.Layout", {}).get("Style", "")
    numbers = [e["number"] for e in entries if e["number"] is not None]
    duplicates_numbers = sorted({n for n in numbers if numbers.count(n) > 1})
    gaps = sorted(set(range(1, max(numbers) + 1)) - set(numbers)) if numbers else []
    numbering = {
        "numbered_entries": len(numbers), "unnumbered_entries": len(entries) - len(numbers),
        "contiguous": bool(numbers) and numbers == list(range(1, len(numbers) + 1)),
        "in_order": numbers == sorted(numbers), "gaps": gaps, "duplicate_numbers": duplicates_numbers,
        "applies": bool(numbers),
    }
    by_doi: dict = {}
    for entry in entries:
        doi = normalize_doi(entry["doi"])
        if doi:
            by_doi.setdefault(doi, []).append(entry["number"] if entry["number"] is not None
                                              else f"paragraph {entry['paragraph']}")
    duplicate_dois = {d: n for d, n in by_doi.items() if len(n) > 1}
    rows = []
    for entry in entries:
        doi = normalize_doi(entry["doi"])
        year, pattern = entry_year(entry["text"], entry["doi"], style)
        row = {"number": entry["number"], "paragraph": entry["paragraph"], "doi": doi, "bib_year": year,
               "year_pattern": pattern, "issues": [], "text": entry["text"]}
        if not doi:
            row["issues"].append("NO_DOI")
        if doi in duplicate_dois:
            row["issues"].append("DUPLICATE_DOI")
        if index is not None:
            record = index.lookup(doi=doi) if doi else None
            matched_by = "doi" if record else ""
            if record is None and not doi:
                best, best_cov = None, 0.0
                for candidate in index.all_records():
                    coverage = title_coverage(candidate.get("title", ""), entry["text"]) or 0.0
                    if coverage > best_cov:
                        best, best_cov = candidate, coverage
                if best is not None and best_cov >= 0.9:
                    record, matched_by = best, "title"
            if record is None and doi in fetch_errors:
                row["issues"].append("FETCH_ERROR")
                row["fetch_error"] = fetch_errors[doi]
            elif record is None:
                row["issues"].append("NO_RECORD")
            else:
                row["matched_by"] = matched_by
                row["record"] = {"source": record.get("_source"), "pmid": record.get("pmid", ""),
                                 "doi": record.get("doi", ""), "title": record.get("title", ""),
                                 "year": record.get("year", ""), "first_author": record.get("first_author", "")}
                coverage = title_coverage(record.get("title", ""), entry["text"])
                row["title_coverage"] = coverage
                if coverage is not None and coverage < 0.5:
                    row["issues"].append("TITLE_MISMATCH")
                years = _record_years(record)
                if not year:
                    row["issues"].append("YEAR_NOT_FOUND")
                elif years and not any(abs(int(year) - y) <= 1 for y in years):
                    row["issues"].append("YEAR_MISMATCH")
                row["record_years"] = sorted(years)
        row["status"] = row["issues"][0] if row["issues"] else "OK"
        rows.append(row)
    counts = {}
    for row in rows:
        for issue in row["issues"] or ["OK"]:
            counts[issue] = counts.get(issue, 0) + 1
    return {"schema": "msw-bib-audit/1", "generated": utc_now(),
            "source": {"path": record_path(package.path), "sha256": package.sha256},
            "style": style, "entries": len(entries), "numbering": numbering, "duplicate_dois": duplicate_dois,
            "no_doi": [r["number"] if r["number"] is not None else f"paragraph {r['paragraph']}"
                       for r in rows if "NO_DOI" in r["issues"]],
            "compared": index is not None, "record_sources": index.sources if index else [],
            "fetch_errors": [{"doi": d, "error": e} for d, e in fetch_errors.items()],
            "counts": counts, "rows": rows}


def bib_audit_markdown(audit: dict) -> str:
    n = audit["numbering"]
    lines = ["# Bibliography audit", "",
             f"Source: `{Path(audit['source']['path']).name if audit['source']['path'] else 'bytes'}` "
             f"(sha256 {audit['source']['sha256'][:12]}), generated {audit['generated']}.", "",
             f"- Entries: {audit['entries']} (style: {audit['style'] or 'unknown'})"]
    if n["applies"]:
        state = "contiguous 1..N" if n["contiguous"] else "NOT contiguous"
        lines.append(f"- Numbering: {state}; gaps {n['gaps'] or 'none'}; duplicate numbers "
                     f"{n['duplicate_numbers'] or 'none'}; in order: {'yes' if n['in_order'] else 'no'}")
    else:
        lines.append("- Numbering: no numbered entries (author-date style or no bibliography)")
    lines.append(f"- Same DOI under two or more numbers: {len(audit['duplicate_dois'])}")
    for doi, numbers in audit["duplicate_dois"].items():
        lines.append(f"  - {doi}: {', '.join(map(str, numbers))}")
    lines.append(f"- Entries without a DOI: {len(audit['no_doi'])}"
                 + (f" ({', '.join(map(str, audit['no_doi']))})" if audit["no_doi"] else ""))
    if audit["compared"]:
        lines.append("- Compared with: " + ", ".join(f"{s['source']} ({Path(s['path']).name})"
                                                     for s in audit["record_sources"]))
    failed_lookups = audit.get("fetch_errors") or []
    if failed_lookups:
        lines.append(f"- Crossref lookups that failed: {len(failed_lookups)}; those entries (FETCH_ERROR) were "
                     "not checked. Rerun with a new --out when the network is back.")
    lines.append("- Status counts: " + ", ".join(f"{k} {v}" for k, v in sorted(audit["counts"].items())))
    flagged = [r for r in audit["rows"] if set(r["issues"]) - {"NO_DOI"} or (r["issues"] and not audit["compared"])]
    lines += ["", "## Entries needing attention", ""]
    if not flagged:
        lines.append("None.")
    else:
        lines += ["| # | Issues | Title coverage | Years (entry / record) | Entry (start) |", "|---|---|---|---|---|"]
        for row in flagged:
            text = row["text"][:110].replace("|", "/").replace("\t", " ")
            coverage = "" if row.get("title_coverage") is None else f"{row['title_coverage']:.2f}"
            years = f"{row['bib_year'] or '?'} / {','.join(map(str, row.get('record_years', []))) or '-'}"
            number = row["number"] if row["number"] is not None else f"p{row['paragraph']}"
            lines.append(f"| {number} | {', '.join(row['issues'])} | {coverage} | {years} | {text} |")
    lines += ["", "TITLE_MISMATCH means fewer than half of the record's title words appear in the entry. "
              "YEAR_MISMATCH allows one year of difference (print versus online-first). "
              "FETCH_ERROR means the lookup failed, so the entry was not compared; NO_RECORD means "
              "no record was found. "
              "Re-check every flag against the source before changing the EndNote record.", ""]
    return "\n".join(lines)


# =====================================================================================
# Claim-support batches
# =====================================================================================

ABBREVIATIONS = frozenset({"al", "e.g", "i.e", "eg", "ie", "fig", "figs", "vs", "cf", "approx", "ca", "no", "nos",
                           "eq", "eqs", "ref", "refs", "dr", "mr", "mrs", "ms", "prof", "suppl", "vol", "st",
                           "resp", "sp", "spp", "var", "ed", "eds", "tab", "sec", "ch", "p", "pp", "viz", "incl",
                           "min", "max", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
                           "nov", "dec"})
_BOUNDARY = re.compile(r"[.!?]+[\"'\u201d\u2019)\]]*(?=\s|$)")


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Sentence (start, end) spans; aware of et al., e.g., i.e., Fig., initials and decimals."""
    spans, start = [], 0
    for match in _BOUNDARY.finditer(text):
        end = match.end()
        punct = text[match.start()]
        if punct == "." and match.group(0).rstrip("\"'\u201d\u2019)]") == ".":
            before = re.search(r"(\S+)$", text[start:match.start()])
            word = before.group(1) if before else ""
            token = word.lstrip("([{\"'\u201c\u2018").lower()
            if token in ABBREVIATIONS or token.rstrip(".") in ABBREVIATIONS or re.fullmatch(r"[A-Za-z]", token):
                continue
        following = re.match(r"\s*(\S)", text[end:])
        if following and not (following.group(1).isupper() or following.group(1).isdigit()
                              or following.group(1) in "([\"'\u201c\u2018"):
            continue
        spans.append((start, end))
        start = end
    if start < len(text) and text[start:].strip():
        spans.append((start, len(text)))
    out = []
    for a, b in spans:
        while a < b and text[a].isspace():
            a += 1
        if a < b:
            out.append((a, b))
    return out


def _heading_texts(doc: XMLDoc, paragraphs) -> dict:
    current, out = "", {}
    for index, paragraph in enumerate(paragraphs):
        if paragraph_style(paragraph).lower().startswith("heading"):
            current = paragraph_text(doc, paragraph).strip()
        out[index] = current
    return out


def claim_items(package: Package, index: RecordIndex | None = None, *, max_abstract_chars: int = 0) -> list[dict]:
    """One item per live EN.CITE field of the accepted view, with claim sentence and cited records."""
    if max_abstract_chars and max_abstract_chars < MIN_ABSTRACT_CHARS:
        raise RefsError(f"--max-abstract-chars must be 0 (no limit) or at least {MIN_ABSTRACT_CHARS}")
    doc = view_doc(package.document, "accept")
    fmap = map_fields(doc)
    entries = field_cites(doc, fmap.fields)
    paragraphs = list(iter_paragraphs(doc))
    headings = _heading_texts(doc, paragraphs)
    by_paragraph: dict = {}
    for entry in entries:
        by_paragraph.setdefault(entry["paragraph"], []).append(entry)
    items = []
    for para_index, group in sorted(by_paragraph.items()):
        paragraph = paragraphs[para_index] if 0 <= para_index < len(paragraphs) else None
        chars = []   # (char, element start, owning entry position or None)
        if paragraph is not None:
            for element, text in paragraph_segments(doc, paragraph):
                owner = next((k for k, e in enumerate(group)
                              if e["field"].start <= element.start < e["field"].stop), None)
                chars.extend((c, element.start, owner) for c in text)
        orig = "".join(c for c, _, _ in chars)
        free = [i for i, (_, _, owner) in enumerate(chars) if owner is None]
        masked = "".join(chars[i][0] for i in free)
        sentences = sentence_spans(masked) or [(0, len(masked))]
        for k, entry in enumerate(group):
            field = entry["field"]
            own = [i for i, (_, _, owner) in enumerate(chars) if owner == k]
            position = sum(1 for i in free if chars[i][1] < field.start)
            chosen = 0
            for s_index, (a, _) in enumerate(sentences):
                if a < position:
                    chosen = s_index
            a, b = sentences[chosen]
            claim = _clean(masked[a:b])
            if free and a < len(free):
                start_orig = free[a]
                end_orig = free[min(b, len(free)) - 1] + 1 if b > a else start_orig
                if own:
                    start_orig, end_orig = min(start_orig, own[0]), max(end_orig, own[-1] + 1)
                claim_marked = _clean(orig[start_orig:end_orig])
            else:
                claim_marked = _clean(orig)
            before = _clean(masked[slice(*sentences[chosen - 1])]) if chosen > 0 else ""
            after = _clean(masked[slice(*sentences[chosen + 1])]) if chosen + 1 < len(sentences) else ""
            cited = []
            for cite in entry["cites"]:
                pmid = cite.get("pmid", "").strip()
                record = index.lookup(pmid=pmid, doi=cite.get("doi", "")) if index else None
                abstract = (record or {}).get("abstract", "") or ""
                truncated = bool(max_abstract_chars) and len(abstract) > max_abstract_chars
                cited.append({
                    "key": record_key(cite), "db_id": cite["db_id"], "recnum": cite["recnum"],
                    "author": cite["author"], "year": cite["year"],
                    "title": cite["title"] or (record or {}).get("title", ""), "journal": cite["journal"],
                    "pmid": pmid if re.fullmatch(r"\d{1,9}", pmid) else "", "doi": normalize_doi(cite.get("doi", "")),
                    "abstract": abstract[:max_abstract_chars] if truncated else abstract,
                    "abstract_available": bool(abstract), "abstract_truncated": truncated,
                    "abstract_source": (record or {}).get("_source", "") if abstract else "",
                })
            item = {"field_index": entry["field_index"], "paragraph": para_index,
                    "paraId": paragraph.get("w14:paraId") if paragraph is not None else None,
                    "section": headings.get(para_index, ""), "display": _clean("".join(chars[i][0] for i in own)),
                    "claim": claim, "claim_marked": claim_marked, "context_before": before, "context_after": after,
                    "cited": cited}
            if entry["error"]:
                item["payload_error"] = entry["error"]
            items.append(item)
    items.sort(key=lambda i: i["field_index"])
    return items


VERDICT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "msw-claim-findings/1",
    "title": "Claim-support findings for one batch",
    "description": "Write one file per batch to findings/batch_NN.json. One finding per item.",
    "type": "object",
    "required": ["batch", "findings"],
    "additionalProperties": False,
    "properties": {
        "batch": {"type": "integer", "minimum": 1},
        "findings": {"type": "array", "items": {"$ref": "#/$defs/finding"}},
    },
    "$defs": {
        "finding": {
            "type": "object",
            "required": ["field_index", "cited", "verdict", "reason", "suggested_action"],
            "additionalProperties": False,
            "properties": {
                "field_index": {"type": "integer", "minimum": 0,
                                "description": "field_index of the item judged"},
                "cited": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                          "description": "keys (db-id#recnum) of the cited records the verdict is about"},
                "verdict": {"enum": list(VERDICTS)},
                "reason": {"type": "string", "minLength": 1,
                           "description": "one or two sentences grounded in the abstract"},
                "suggested_action": {"enum": list(ACTIONS)},
                "confidence": {"enum": list(CONFIDENCE),
                               "description": "for MISMATCH: high = clearly the wrong paper (tier 1)"},
                "tier": {"enum": [1, 2, 3], "description": "optional explicit tier for MISMATCH"},
                "evidence": {"enum": ["abstract", "full_text", "both"],
                             "description": "what the verdict rests on; abstract-only flags need full-text checks"},
                "replacement": {"type": "string",
                                "description": "REUSE: existing reference number or key; ADD: PMID if known"},
                "notes": {"type": "string"},
            },
        }
    },
}

BATCH_INSTRUCTIONS = [
    "Judge each item on its own: does the abstract of each cited record support the claim sentence?",
    "SUPPORTED: the abstract states or directly implies the claim. WEAK: related or partial support, or the "
    "claim is more specific than the abstract. MISMATCH: off-topic, or contradicts the claim. "
    "UNVERIFIED_NO_ABSTRACT: no abstract to judge.",
    "suggested_action: REUSE (another reference already cited fits; name it in 'replacement'), ADD (a new "
    "reference is needed; give a PMID in 'replacement' if known), TEXT (the reference is right but the sentence "
    "misstates it), NONE.",
    "For MISMATCH give confidence: high = clearly the wrong paper; medium or low = probably wrong. When the paper "
    "is right but the sentence misstates it, use suggested_action TEXT.",
    "Copy the cited keys (db-id#recnum) from the item. Judge from the abstract only, never from the title alone.",
    "Write {\"batch\": N, \"findings\": [...]} to the findings_file named in this batch, following "
    "VERDICT_SCHEMA.json. The author re-verifies every flag before acting on it.",
]


def write_claim_batches(package: Package, out_dir, *, index: RecordIndex | None = None, batch_size: int = 40,
                        max_abstract_chars: int = 0) -> dict:
    if batch_size < 1:
        raise RefsError("--batch-size must be at least 1")
    out = Path(out_dir)
    batches_dir = out / "batches"
    if batches_dir.exists() and any(batches_dir.iterdir()):
        raise RefsError(f"{batches_dir} exists and is not empty; choose a new output folder")
    _refuse_existing([out / "VERDICT_SCHEMA.json", out / "claims_manifest.json"])
    items = claim_items(package, index, max_abstract_chars=max_abstract_chars)
    chunks = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    width = max(2, len(str(len(chunks))))
    source = {"path": record_path(package.path), "sha256": package.sha256, "view": "accepted"}
    listing = []
    for number, chunk in enumerate(chunks, 1):
        name = f"batch_{number:0{width}d}.json"
        findings = f"findings/{name}"
        write_json_exclusive(batches_dir / name, {
            "schema": "msw-claim-batch/1", "batch": number, "batches": len(chunks), "source": source,
            "verdict_schema": "VERDICT_SCHEMA.json", "findings_file": findings,
            "instructions": BATCH_INSTRUCTIONS, "items": chunk})
        listing.append({"file": f"batches/{name}", "findings_file": findings, "items": len(chunk),
                        "field_indexes": [chunk[0]["field_index"], chunk[-1]["field_index"]]})
    write_json_exclusive(out / "VERDICT_SCHEMA.json", VERDICT_SCHEMA)
    (out / "findings").mkdir(parents=True, exist_ok=True)
    cited = [c for item in items for c in item["cited"]]
    manifest = {
        "schema": "msw-claims/1", "generated": utc_now(), "source": source,
        "records": index.sources if index else [], "batch_size": batch_size, "fields": len(items),
        "cite_occurrences": len(cited), "distinct_records": len({c["key"] for c in cited}),
        "occurrences_without_abstract": sum(1 for c in cited if not c["abstract_available"]),
        "abstracts_truncated": sum(1 for c in cited if c["abstract_truncated"]),
        "max_abstract_chars": max_abstract_chars or None,
        "payload_errors": [{"field_index": i["field_index"], "error": i["payload_error"]}
                           for i in items if "payload_error" in i],
        "batches": listing,
    }
    write_json_exclusive(out / "claims_manifest.json", manifest)
    return manifest


# =====================================================================================
# Claims report
# =====================================================================================

def validate_finding(finding, known: dict | None = None) -> list[str]:
    """Errors for one finding against VERDICT_SCHEMA (and the batch items, when known)."""
    if not isinstance(finding, dict):
        return ["finding is not an object"]
    errors = []
    allowed = set(VERDICT_SCHEMA["$defs"]["finding"]["properties"])
    for key in VERDICT_SCHEMA["$defs"]["finding"]["required"]:
        if key not in finding:
            errors.append(f"missing '{key}'")
    for key in finding:
        if key not in allowed:
            errors.append(f"unknown property '{key}'")
    index = finding.get("field_index")
    if "field_index" in finding and (not isinstance(index, int) or isinstance(index, bool) or index < 0):
        errors.append("field_index must be a non-negative integer")
    cited = finding.get("cited")
    if "cited" in finding and (not isinstance(cited, list) or not cited or not all(isinstance(c, str) for c in cited)):
        errors.append("cited must be a non-empty list of strings")
    if "verdict" in finding and finding["verdict"] not in VERDICTS:
        errors.append(f"verdict must be one of {', '.join(VERDICTS)}")
    if "reason" in finding and (not isinstance(finding["reason"], str) or not finding["reason"].strip()):
        errors.append("reason must be a non-empty string")
    if "suggested_action" in finding and finding["suggested_action"] not in ACTIONS:
        errors.append(f"suggested_action must be one of {', '.join(ACTIONS)}")
    if "confidence" in finding and finding["confidence"] not in CONFIDENCE:
        errors.append(f"confidence must be one of {', '.join(CONFIDENCE)}")
    if "tier" in finding and finding["tier"] not in (1, 2, 3):
        errors.append("tier must be 1, 2 or 3")
    for key in ("replacement", "notes"):
        if key in finding and not isinstance(finding[key], str):
            errors.append(f"{key} must be a string")
    if known is not None and isinstance(index, int) and not isinstance(index, bool):
        item = known.get(index)
        if item is None:
            errors.append(f"field_index {index} is not an item of any batch")
        elif isinstance(cited, list):
            keys = {c["key"] for c in item["cited"]}
            unknown = [c for c in cited if isinstance(c, str) and c not in keys]
            if unknown:
                errors.append(f"cited key(s) not in field {index}: {', '.join(unknown)}")
    return errors


def finding_tier(finding: dict) -> str:
    verdict = finding["verdict"]
    if verdict == "MISMATCH":
        if finding.get("tier") in (1, 2, 3):
            return f"tier{finding['tier']}"
        if finding.get("suggested_action") == "TEXT":
            return "tier3"
        return "tier1" if finding.get("confidence") == "high" else "tier2"
    return {"WEAK": "weak", "UNVERIFIED_NO_ABSTRACT": "unverified", "SUPPORTED": "supported"}[verdict]


TIER_TITLES = [
    ("tier1", "Tier 1: clearly wrong reference",
     "The cited paper does not fit the claim, typically a mis-picked record. Repoint the citation (REUSE) or add "
     "the intended paper (ADD) in Word and EndNote."),
    ("tier2", "Tier 2: probably wrong reference",
     "Likely the wrong paper; confirm against the source the sentence was written from before changing it."),
    ("tier3", "Tier 3: the sentence misstates the paper",
     "The reference is right but the sentence says more or something else than the paper. Reword the sentence "
     "(TEXT); keep the citation."),
    ("weak", "WEAK: partial support",
     "Related but softer support, or detail not in the abstract. Check the full text or soften the wording."),
    ("unverified", "Unverified: no abstract",
     "No abstract was available. Check these against the full text."),
]


def _load_batches(folder: Path) -> tuple[dict, dict, dict]:
    manifest_path = folder / "claims_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    items, batch_of = {}, {}
    for path in sorted((folder / "batches").glob("batch_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data.get("items", []):
            items[item["field_index"]] = item
            batch_of[item["field_index"]] = data.get("batch")
    return manifest, items, batch_of


def claims_report(folder, out_path=None) -> dict:
    folder = Path(folder)
    out_path = Path(out_path) if out_path else folder / "CLAIM_AUDIT.md"
    _refuse_existing([out_path])
    manifest, items, batch_of = _load_batches(folder)
    if not items:
        raise RefsError(f"no batches found in {folder / 'batches'}; run 'refs claims' first")
    valid, invalid, seen = [], [], {}
    files = sorted((folder / "findings").glob("*.json"))
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as error:
            invalid.append({"file": path.name, "finding": None, "errors": [f"not JSON: {error}"]})
            continue
        if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
            invalid.append({"file": path.name, "finding": None,
                            "errors": ["file must be an object {\"batch\": N, \"findings\": [...]}"]})
            continue
        extra = set(data) - {"batch", "findings"}
        batch = data.get("batch")
        if extra or not isinstance(batch, int) or isinstance(batch, bool) or batch < 1:
            problems = []
            if not isinstance(batch, int) or isinstance(batch, bool) or batch < 1:
                problems.append("batch must be a positive integer")
            if extra:
                problems.append(f"unknown key(s) {sorted(extra)}")
            invalid.append({"file": path.name, "finding": None, "errors": ["file-level: " + "; ".join(problems)]})
        for finding in data["findings"]:
            errors = validate_finding(finding, items)
            if not errors and finding["field_index"] in seen:
                errors.append(f"duplicate finding for field {finding['field_index']} "
                              f"(also in {seen[finding['field_index']]})")
            if errors:
                invalid.append({"file": path.name, "finding": finding, "errors": errors})
                continue
            seen[finding["field_index"]] = path.name
            valid.append(dict(finding, _file=path.name, _tier=finding_tier(finding)))
    valid.sort(key=lambda f: f["field_index"])
    verdicts = {v: 0 for v in VERDICTS}
    tiers = {key: 0 for key, _, _ in TIER_TITLES}
    tiers["supported"] = 0
    for finding in valid:
        verdicts[finding["verdict"]] += 1
        tiers[finding["_tier"]] += 1
    not_judged = sorted(set(items) - set(seen))
    source = manifest.get("source") or {}
    lines = ["# Claim-support audit", ""]
    lines.append(f"Source: `{Path(source['path']).name if source.get('path') else 'unknown'}`"
                 + (f" (sha256 {source['sha256'][:12]}, accepted view)" if source.get("sha256") else "")
                 + f"; report generated {utc_now()}.")
    lines.append(f"Citation fields: {len(items)}; judged: {len(seen)}; not judged: {len(not_judged)}; "
                 f"invalid findings: {len(invalid)} (from {len(files)} findings file(s)).")
    lines += ["", "| Verdict | Count |", "|---|---|"] + [f"| {v} | {verdicts[v]} |" for v in VERDICTS]
    lines += ["", "| Group | Count |", "|---|---|"]
    lines += [f"| {title.split(':')[0]} | {tiers[key]} |" for key, title, _ in TIER_TITLES]
    lines += ["", "Every flag below comes from an automated reading of abstracts. Re-verify each one against the "
              "abstract or full text before acting; record false positives and drop them.", ""]
    for key, title, explanation in TIER_TITLES:
        group = [f for f in valid if f["_tier"] == key]
        lines += [f"## {title} ({len(group)})", "", explanation, ""]
        if not group:
            lines += ["None.", ""]
            continue
        for finding in group:
            lines += _finding_lines(finding, items.get(finding["field_index"], {}))
        lines.append("")
    if not_judged:
        lines += [f"## Not judged ({len(not_judged)})", "",
                  "No valid finding covers these fields: " + ", ".join(map(str, not_judged)), ""]
    if invalid:
        lines += [f"## Invalid findings ({len(invalid)})", "", "Fix these against VERDICT_SCHEMA.json and re-run "
                  "'refs claims-report' with a new --out name.", ""]
        for entry in invalid:
            where = entry["finding"].get("field_index", "?") if isinstance(entry["finding"], dict) else "-"
            lines.append(f"- `{entry['file']}` field {where}: {'; '.join(entry['errors'])}")
        lines.append("")
    actions = {}
    for finding in valid:
        if finding["_tier"] in ("tier1", "tier2", "tier3", "weak") and finding["suggested_action"] != "NONE":
            actions.setdefault(finding["suggested_action"], []).append(finding["field_index"])
    lines += ["## Quick actions", ""]
    if actions:
        for action in ("REUSE", "ADD", "TEXT"):
            if action in actions:
                lines.append(f"- {action}: fields {', '.join(map(str, actions[action]))}")
    else:
        lines.append("None.")
    lines += ["", "Citation changes are made by the author in Word and EndNote; sentence changes go through a "
              "tracked edit pass.", ""]
    _write_text_exclusive(out_path, "\n".join(lines))
    return {"report": str(out_path), "fields": len(items), "judged": len(seen), "not_judged": not_judged,
            "invalid": len(invalid), "verdicts": verdicts, "groups": tiers}


def _finding_lines(finding: dict, item: dict) -> list[str]:
    cited = {c["key"]: c for c in item.get("cited", [])}
    names = []
    for key in finding["cited"]:
        c = cited.get(key, {})
        label = f"{c.get('author', '?')} {c.get('year', '')}".strip()
        extra = f"PMID {c['pmid']}" if c.get("pmid") else (f"doi {c['doi']}" if c.get("doi") else "")
        names.append(f"{label} ({extra}; {key})" if extra else f"{label} ({key})")
    claim = item.get("claim", "")
    claim = claim if len(claim) <= 400 else claim[:397] + "..."
    where = f"paragraph {item.get('paragraph', '?')}" + (f", {item['section']}" if item.get("section") else "")
    confidence = f", confidence {finding['confidence']}" if finding.get("confidence") else ""
    action = finding["suggested_action"] + (f" -> {finding['replacement']}" if finding.get("replacement") else "")
    out = [f"- **Field {finding['field_index']}** ({where}; shown as {item.get('display') or '?'}): \"{claim}\"",
           f"  - Cited: {'; '.join(names)}",
           f"  - {finding['verdict']}{confidence}: {finding['reason'].strip()}",
           f"  - Suggested action: {action}"]
    if finding.get("notes"):
        out.append(f"  - Notes: {finding['notes'].strip()}")
    return out


# =====================================================================================
# Library map (EndNote XML export)
# =====================================================================================

def library_map(data: bytes, *, source: str | None = None) -> dict:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise RefsError(f"not an EndNote XML export: {error}") from error
    mapping, duplicates, without, non_pmid, labels, label_dupes = {}, {}, [], [], {}, {}
    db_ids, count = set(), 0
    for record in root.iter("record"):
        count += 1
        rec = _itext(record.find("rec-number"))
        key = record.find("foreign-keys/key")
        db_id = key.get("db-id", "") if key is not None else ""
        if db_id:
            db_ids.add(db_id)
        authors = [_itext(a) for a in record.findall("contributors/authors/author")]
        ref_type = record.find("ref-type")
        entry = {"rec_number": rec, "db_id": db_id, "title": _itext(record.find("titles/title")),
                 "author": authors[0] if authors else "", "surname": _surname(authors[0]) if authors else "",
                 "year": _itext(record.find("dates/year")),
                 "doi": normalize_doi(_itext(record.find("electronic-resource-num"))),
                 "ref_type": ref_type.get("name", "") if ref_type is not None else ""}
        accession = _itext(record.find("accession-num"))
        match = re.fullmatch(r"(?:PMID:?\s*)?(\d{1,9})", accession, flags=re.I)
        pmid, matched_by = (match.group(1), "accession-num") if match else ("", "")
        if not pmid:
            for url in record.iter("url"):
                found = re.search(r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/)(\d{1,9})", _itext(url))
                if found:
                    pmid, matched_by = found.group(1), "url"
                    break
        if accession and not match:
            non_pmid.append({"rec_number": rec, "accession": accession, "title": entry["title"]})
        label = _itext(record.find("label"))
        if label:
            if label in labels:
                label_dupes.setdefault(label, [labels[label]["rec_number"]]).append(rec)
            else:
                labels[label] = dict(entry)
        if not pmid:
            without.append({"rec_number": rec, "title": entry["title"], "accession": accession})
            continue
        entry["pmid_from"] = matched_by
        if pmid in mapping:
            duplicates.setdefault(pmid, [mapping[pmid]["rec_number"]]).append(rec)
            continue
        mapping[pmid] = entry
    for pmid, recs in duplicates.items():
        mapping[pmid]["duplicate_rec_numbers"] = recs
    return {"schema": "msw-library-map/1", "generated": utc_now(),
            "source": {"path": source, "sha256": sha256_bytes(data)}, "records": count,
            "db_ids": sorted(db_ids), "map": mapping, "duplicates": duplicates, "refs": labels,
            "duplicate_labels": label_dupes, "without_pmid": without, "non_pmid_accessions": non_pmid}


# =====================================================================================
# Temporary citations
# =====================================================================================

def tracked_changes(package: Package) -> dict:
    counts = {}
    for part in package.story_parts():
        for element in package.xml(part).elements:
            if element.tag in TRACKED_TAGS:
                label = f"{part}:{element.tag}"
                counts[label] = counts.get(label, 0) + 1
    return counts


def _escape_temporary(text: str) -> str:
    return re.sub(r"([;\\])", r"`\1", text)


def _char_offsets(raw: str) -> list[int]:
    offsets, i = [], 0
    while i < len(raw):
        offsets.append(i)
        match = _ENTITY.match(raw, i) if raw[i] == "&" else None
        i = match.end() if match else i + 1
    offsets.append(len(raw))
    return offsets


def _resolve(item: dict, mapping: dict, duplicates: dict, refs: dict, errors: list, where: str):
    pieces = []
    if item["kind"] == "ref":
        entries = [("REF " + item["key"], refs.get(item["key"]))]
    else:
        ids = []
        for pmid in item["pmids"]:
            if pmid in ids:
                errors.append(f"{where}: PMID {pmid} appears twice in one group")
                continue
            ids.append(pmid)
        entries = []
        for pmid in ids:
            if pmid in duplicates:
                errors.append(f"{where}: PMID {pmid} has several library records ({', '.join(duplicates[pmid])}); "
                              "keep one in EndNote and export again")
                continue
            entries.append((f"PMID {pmid}", mapping.get(pmid)))
    for label, entry in entries:
        if entry is None:
            errors.append(f"{where}: {label} is not in the library map")
            continue
        rec, surname, year = str(entry.get("rec_number", "")).strip(), entry.get("surname", "").strip(), \
            str(entry.get("year", "")).strip()
        if not rec.isdigit() or not surname or not year:
            errors.append(f"{where}: {label} lacks a record number, first-author surname or year in the map")
            continue
        pieces.append(f"{_escape_temporary(surname)}, {_escape_temporary(year)} #{rec}")
    return pieces


def temporary_citations(package: Package, map_data: dict, *, skip_unmapped: bool = False) -> tuple[bytes, dict]:
    """Return (new document.xml, ledger) with [PMID]/[REF] placeholders as EndNote temporary citations."""
    tracked = tracked_changes(package)
    if tracked:
        summary = ", ".join(f"{k} x{v}" for k, v in sorted(tracked.items()))
        raise RefsError(f"the document has tracked changes ({summary}); temporary citations go into a clean "
                        "working copy: accept or reject all changes in Word, save under a new name, and run "
                        "this again")
    if map_data.get("schema") != "msw-library-map/1":
        raise RefsError("the map is not a msw-library-map/1 file written by 'refs library-map'")
    mapping, duplicates, refs = map_data.get("map", {}), map_data.get("duplicates", {}), map_data.get("refs", {})
    if len(map_data.get("db_ids", [])) > 1:
        raise RefsError("the library map spans several EndNote libraries; export one library per manuscript")
    left, right = layout_delimiters(package)
    doc = package.document
    pattern = placeholder_pattern(temporary=False)
    splices, errors, skipped, rows, malformed = [], [], [], [], []
    expected_texts = {}
    paragraphs = list(doc.iter("w:p"))
    main_index = {id(p): i for i, p in enumerate(iter_paragraphs(doc))}
    for paragraph in paragraphs:
        segments = paragraph_segments(doc, paragraph)
        text = "".join(t for _, t in segments)
        if "[" not in text:
            continue
        spans = _spans(segments)
        index = main_index.get(id(paragraph))
        where = f"paragraph {index}" if index is not None else "alternate-content paragraph"
        new_text, last = [], 0
        taken = []
        for match in pattern.finditer(text):
            start, end = match.span()
            taken.append((start, end))
            item = _classify(match)
            owner = _inside_one_wt(spans, start, end)
            if owner is None:
                errors.append(f"{where}: '{match.group(0)}' is split across runs or formatting; retype it in "
                              "one piece in Word (or remove the formatting change inside it)")
                continue
            local_errors = []
            pieces = _resolve(item, mapping, duplicates, refs, local_errors, where)
            if local_errors:
                if skip_unmapped:
                    skipped.append({"paragraph": index, "text": match.group(0), "reasons": local_errors})
                    continue
                errors.extend(local_errors)
                continue
            citation = left + ";".join(pieces) + right
            element, seg_start = owner
            raw = doc.inner(element).decode("utf-8")
            offsets = _char_offsets(raw)
            a, b = start - seg_start, end - seg_start
            byte_a = element.open_end + len(raw[:offsets[a]].encode("utf-8"))
            byte_b = element.open_end + len(raw[:offsets[b]].encode("utf-8"))
            splices.append((byte_a, byte_b, escape_text(citation).encode("utf-8")))
            new_text.append(text[last:start] + citation)
            last = end
            rows.append({"paragraph": index, "paraId": paragraph.get("w14:paraId"), "old": match.group(0),
                         "new": citation, "kind": item["kind"], "pmids": item.get("pmids", []),
                         "key": item.get("key"), "in_alternate_content": index is None})
        for match in _MALFORMED.finditer(text):
            if not any(a <= match.start() < b for a, b in taken):
                malformed.append({"paragraph": index, "text": match.group(0)})
        new_text.append(text[last:])
        expected_texts[id(paragraph)] = "".join(new_text)
    if malformed and not skip_unmapped:
        errors.extend(f"paragraph {m['paragraph']}: malformed placeholder '{m['text']}'" for m in malformed)
    if errors:
        raise RefsError("temporary citations not written:\n  " + "\n  ".join(errors))
    if not rows:
        raise RefsError("no [PMID: n], [PMIDs: ...] or [REF: key] placeholders to replace")
    new_data = apply_splices(doc.data, splices)
    check = XMLDoc(new_data)
    new_paragraphs = list(check.iter("w:p"))
    if len(new_paragraphs) != len(paragraphs):
        raise RefsError("internal check failed: paragraph count changed")
    for old, new in zip(paragraphs, new_paragraphs):
        expected = expected_texts.get(id(old))
        if expected is None:
            continue
        actual = "".join(t for _, t in paragraph_segments(check, new))
        if actual != expected:
            raise RefsError("internal check failed: a paragraph's text is not the expected substitution")
    before, after = map_fields(doc), map_fields(check)
    if [f.kind for f in before.all_fields] != [f.kind for f in after.all_fields] or \
            len(after.errors) != len(before.errors):
        raise RefsError("internal check failed: field structure changed")
    if sum(1 for _ in doc.iter("w:drawing")) != sum(1 for _ in check.iter("w:drawing")):
        raise RefsError("internal check failed: picture count changed")
    records = {p for r in rows for p in r["pmids"]} | {f"REF {r['key']}" for r in rows if r["key"]}
    ledger = {
        "schema": "msw-temp-citations/1", "generated": utc_now(),
        "source": {"path": record_path(package.path), "sha256": package.sha256},
        "map": map_data.get("source", {}), "db_ids": map_data.get("db_ids", []), "delimiters": [left, right],
        "groups_replaced": len(rows), "citations": sum(len(r["pmids"]) or 1 for r in rows),
        "distinct_sources": len(records), "skipped": skipped, "malformed_left": malformed if skip_unmapped else [],
        "rows": rows,
    }
    return new_data, ledger


# =====================================================================================
# Command line
# =====================================================================================

def _fail(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return 2


def _log(message: str):
    print(message)


def _cache_dir(args, out: Path):
    if getattr(args, "no_cache", False):
        return None
    if getattr(args, "cache", None):
        return Path(args.cache)
    if os.environ.get(CACHE_ENV):
        return Path(os.environ[CACHE_ENV])
    config = load_config(out)
    if config.get("_root"):
        return Path(config["_root"]) / "05_References" / "Evidence" / "http_cache"
    return out / "http_cache"


def make_client(args, out: Path) -> Client:
    cache = _cache_dir(args, out)
    client = Client(cache_dir=cache, transport=TRANSPORT, max_retries=getattr(args, "max_retries", 4))
    if not client.contact:
        print("NOTE: MSW_CONTACT_EMAIL is not set; requests carry no contact address "
              "(NCBI and Crossref ask for one).", file=sys.stderr)
    return client


def _print_json(data):
    print(json.dumps(data, indent=2, ensure_ascii=True, default=str))


def _json_out(target, data):
    if target in (None, "-"):
        _print_json(data)
    else:
        write_json_exclusive(target, data)
        print(f"wrote {target}")


def cmd_placeholders(args) -> int:
    package = Package(args.docx)
    data = placeholder_inventory(package)
    if args.json:
        _json_out(args.json, data)
    else:
        s = data["summary"]
        print(f"{s['placeholders']} placeholder(s): " + ", ".join(f"{k} {v}" for k, v in sorted(s["by_kind"].items()))
              + f"; {s['distinct_pmids']} distinct PMID(s) in {s['pmid_occurrences']} occurrence(s)")
        for item in data["items"]:
            flag = "" if item["single_run"] else "  [SPLIT ACROSS RUNS]"
            print(f"  p{item['paragraph']} {item['paraId'] or '-'}: {item['text']}{flag}")
        for item in data["malformed"]:
            print(f"  p{item['paragraph']} MALFORMED: {item['text']}")
    return 1 if data["summary"]["malformed"] else 0


def cmd_pubmed(args) -> int:
    pmids = list(args.pmids)
    if args.from_docx:
        pmids += docx_pmids(Package(args.from_docx))
    if not _valid_pmids(pmids):
        raise RefsError("no PMIDs to fetch")
    out = Path(args.out)
    client = make_client(args, out)
    result = fetch_pubmed(pmids, out, client, batch_size=args.batch_size, log=_log)
    print(f"wrote {out / 'records.json'}: " + ", ".join(f"{k} {v}" for k, v in sorted(result["counts"].items())))
    for warning in client.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    return 1 if result["errors"] or result["counts"].get("not_found") else 0


def cmd_crossref(args) -> int:
    dois = list(args.dois)
    if args.from_docx:
        dois += docx_dois(Package(args.from_docx))
    invalid = [d for d in dois if not normalize_doi(d)]
    if invalid or not dois:
        raise RefsError("not DOIs: " + ", ".join(map(str, invalid[:10])) if invalid else "no DOIs to look up")
    out = Path(args.out)
    client = make_client(args, out)
    result = fetch_crossref(dois, out, client, log=_log)
    print(f"wrote {out / 'crossref.json'}: " + ", ".join(f"{k} {v}" for k, v in sorted(result["counts"].items())))
    for error in result["errors"]:
        print(f"WARNING: Crossref {error['doi']}: {error['error']}", file=sys.stderr)
    return 1 if result["errors"] else 0


def cmd_census(args) -> int:
    package = Package(args.docx)
    data = census_with_audit(package)
    if not args.full:
        data["pmids"] = len(data["pmids"])
    _json_out(args.json, data)
    audit = data["payload_audit"]
    for warning in audit["warnings"]:
        print(f"WARNING: {warning}", file=sys.stderr)
    failed = data["field_errors"] or data["link_targets_missing"] or audit["payload_errors"]
    return 1 if failed else 0


def cmd_bib_audit(args) -> int:
    package = Package(args.docx)
    out = Path(args.out)
    _refuse_existing([out / "bib_audit.json", out / "BIB_AUDIT.md"])
    record_files = list(args.records or [])
    fetch_errors: dict = {}
    looked_up = 0
    if args.crossref:
        index = RecordIndex.load(record_files)
        wanted = [d for d in (normalize_doi(e["doi"]) for e in bibliography_entries(package.document))
                  if d and index.lookup(doi=d) is None]
        if wanted:
            client = make_client(args, out)
            fetched = fetch_crossref(wanted, out, client, log=_log)
            record_files.append(out / "crossref.json")
            looked_up = len(fetched["status"])
            messages = {e["doi"]: e["error"] for e in fetched["errors"]}
            fetch_errors = {doi: messages.get(doi, "lookup failed")
                            for doi, state in fetched["status"].items() if state == "fetch_error"}
            for doi, message in fetch_errors.items():
                print(f"WARNING: Crossref lookup failed for {doi}: {message}", file=sys.stderr)
    index = RecordIndex.load(record_files) if record_files else None
    audit = bibliography_audit(package, index, style=args.style or "", fetch_errors=fetch_errors)
    write_json_exclusive(out / "bib_audit.json", audit)
    _write_text_exclusive(out / "BIB_AUDIT.md", bib_audit_markdown(audit))
    counts = ", ".join(f"{k} {v}" for k, v in sorted(audit["counts"].items()))
    print(f"{audit['entries']} bibliography entries; {counts}")
    print(f"wrote {out / 'bib_audit.json'} and {out / 'BIB_AUDIT.md'}")
    if looked_up and len(fetch_errors) == looked_up:
        return _fail(f"every Crossref lookup failed ({looked_up} of {looked_up}), so no entry was compared with "
                     "a record; the FETCH_ERROR rows are unchecked, not missing. Check the network (and "
                     "MSW_CONTACT_EMAIL), then rerun with a new --out.")
    bad = {"TITLE_MISMATCH", "YEAR_MISMATCH", "DUPLICATE_DOI", "FETCH_ERROR"}
    numbering = audit["numbering"]
    failed = any(bad & set(r["issues"]) for r in audit["rows"]) or \
        (numbering["applies"] and not numbering["contiguous"])
    return 1 if failed else 0


def cmd_claims(args) -> int:
    package = Package(args.docx)
    index = RecordIndex.load(args.records) if args.records else None
    manifest = write_claim_batches(package, args.out, index=index, batch_size=args.batch_size,
                                   max_abstract_chars=args.max_abstract_chars)
    print(f"{manifest['fields']} citation field(s) in {len(manifest['batches'])} batch(es); "
          f"{manifest['occurrences_without_abstract']} cited occurrence(s) without an abstract")
    print(f"wrote {Path(args.out) / 'batches'}, VERDICT_SCHEMA.json and claims_manifest.json; "
          f"judges write findings to {Path(args.out) / 'findings'}")
    return 1 if manifest["payload_errors"] else 0


def cmd_claims_report(args) -> int:
    result = claims_report(args.dir, args.out)
    groups = result["groups"]
    print(f"judged {result['judged']}/{result['fields']}; tier1 {groups['tier1']}, tier2 {groups['tier2']}, "
          f"tier3 {groups['tier3']}, weak {groups['weak']}, unverified {groups['unverified']}; "
          f"invalid {result['invalid']}")
    print(f"wrote {result['report']}")
    return 1 if result["invalid"] or result["not_judged"] else 0


def cmd_library_map(args) -> int:
    source = Path(args.export)
    data = library_map(source.read_bytes(), source=record_path(source))
    write_json_exclusive(args.out, data)
    print(f"{data['records']} record(s); {len(data['map'])} with a PMID; {len(data['duplicates'])} duplicated PMID(s); "
          f"{len(data['without_pmid'])} without a PMID; {len(data['refs'])} labelled (REF keys); "
          f"{len(data['db_ids'])} db-id(s)")
    print(f"wrote {args.out}")
    return 1 if data["duplicates"] or len(data["db_ids"]) > 1 else 0


def cmd_temp_citations(args) -> int:
    out = Path(args.out)
    ledger_path = Path(args.ledger) if args.ledger else out.with_name(out.stem + ".temp-citations.json")
    _refuse_existing([out, ledger_path])
    locks = lock_files(args.docx)
    if locks:
        print(f"WARNING: the source appears to be open in Word or LibreOffice: {locks}", file=sys.stderr)
    package = Package(args.docx)
    map_data = json.loads(Path(args.map).read_text(encoding="utf-8-sig"))
    new_document, ledger = temporary_citations(package, map_data, skip_unmapped=args.skip_unmapped)
    sha = package.write(out, {MAIN_PART: new_document})
    ledger["output"] = {"path": record_path(out), "sha256": sha}
    write_json_exclusive(ledger_path, ledger)
    print(f"replaced {ledger['groups_replaced']} placeholder group(s) ({ledger['citations']} citation(s), "
          f"{ledger['distinct_sources']} distinct source(s)); skipped {len(ledger['skipped'])}; "
          f"delimiters {ledger['delimiters'][0]} {ledger['delimiters'][1]}")
    print(f"wrote {out}")
    print(f"ledger: {ledger_path}")
    print("Next (in Word): open the new file with only this EndNote library open, run Update Citations and "
          "Bibliography, answer any Select Matching Reference prompts, then save under a new name.")
    return 1 if ledger["skipped"] else 0


def _guard(handler):
    def run(args):
        try:
            return handler(args)
        except (RefsError, FetchError, HostNotAllowed, PackageError, XMLError, FileNotFoundError,
                FileExistsError, zipfile.BadZipFile) as error:
            return _fail(str(error))
        except json.JSONDecodeError as error:
            return _fail(f"not valid JSON: {error}")
    return run


def _network_arguments(parser):
    parser.add_argument("--cache", help="HTTP cache folder (default: MSW_HTTP_CACHE, the project's "
                                        "05_References/Evidence/http_cache, or OUT/http_cache)")
    parser.add_argument("--no-cache", action="store_true", help="neither read nor write the HTTP cache")
    parser.add_argument("--max-retries", type=int, default=4)


def register(subparsers):
    refs = subparsers.add_parser("refs", help="citation placeholders, PubMed/Crossref evidence, EndNote payload, "
                                              "bibliography and claim-support audits, temporary citations")
    sub = refs.add_subparsers(dest="refs_command", required=True, metavar="SUBCOMMAND")

    p = sub.add_parser("placeholders", help="inventory [PMID: n], [PMIDs: ...], [REF: key] and {Author, Year #rec}")
    p.add_argument("docx")
    p.add_argument("--json", nargs="?", const="-", metavar="OUT",
                   help="print the inventory as JSON, or write it to OUT (a new file)")
    p.set_defaults(func=_guard(cmd_placeholders))

    p = sub.add_parser("pubmed", help="fetch PubMed records (efetch XML) with archived evidence -> OUT/records.json")
    p.add_argument("pmids", nargs="*", help="PMIDs (or use --from-docx)")
    p.add_argument("--from-docx", help="take PMIDs from the placeholders and citation payloads of this .docx")
    p.add_argument("--out", required=True, help="output folder (records.json and evidence/)")
    p.add_argument("--batch-size", type=int, default=MAX_EFETCH_BATCH, help="PMIDs per request (max 200)")
    _network_arguments(p)
    p.set_defaults(func=_guard(cmd_pubmed))

    p = sub.add_parser("crossref", help="look up DOIs in Crossref -> OUT/crossref.json")
    p.add_argument("dois", nargs="*")
    p.add_argument("--from-docx", help="take DOIs from the citation payloads and bibliography of this .docx")
    p.add_argument("--out", required=True)
    _network_arguments(p)
    p.set_defaults(func=_guard(cmd_crossref))

    p = sub.add_parser("census", help="EndNote census plus a payload metadata audit")
    p.add_argument("docx")
    p.add_argument("--json", metavar="OUT", help="write the JSON to OUT (a new file) instead of printing it")
    p.add_argument("--full", action="store_true", help="list the PMIDs instead of counting them")
    p.set_defaults(func=_guard(cmd_census))

    p = sub.add_parser("bib-audit", help="rendered bibliography: numbering, duplicate DOIs, missing DOIs, and "
                                         "title/year comparison with PubMed or Crossref records")
    p.add_argument("docx")
    p.add_argument("--out", required=True, help="output folder (bib_audit.json, BIB_AUDIT.md)")
    p.add_argument("--records", action="append", help="records.json or crossref.json to compare with (repeatable)")
    p.add_argument("--crossref", action="store_true", help="also look up entry DOIs in Crossref (cached)")
    p.add_argument("--style", help="citation style name for the year check (default: EN.Layout style)")
    _network_arguments(p)
    p.set_defaults(func=_guard(cmd_bib_audit))

    p = sub.add_parser("claims", help="claim-support batches (claim sentence + cited abstracts) for judges")
    p.add_argument("docx")
    p.add_argument("--out", required=True, help="new folder for batches/, findings/, VERDICT_SCHEMA.json")
    p.add_argument("--records", action="append", help="records.json from 'refs pubmed' (repeatable)")
    p.add_argument("--batch-size", type=int, default=40)
    p.add_argument("--max-abstract-chars", type=int, default=0,
                   help=f"0 = full abstracts (default); otherwise at least {MIN_ABSTRACT_CHARS}")
    p.set_defaults(func=_guard(cmd_claims))

    p = sub.add_parser("claims-report", help="validate findings/*.json and write CLAIM_AUDIT.md by tier")
    p.add_argument("dir")
    p.add_argument("--out", help="report path (default: DIR/CLAIM_AUDIT.md; must not exist)")
    p.set_defaults(func=_guard(cmd_claims_report))

    p = sub.add_parser("library-map", help="EndNote XML export -> PMID to record number map")
    p.add_argument("export")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_guard(cmd_library_map))

    p = sub.add_parser("temp-citations", help="replace [PMID: n] placeholders with EndNote temporary citations "
                                              "in a clean copy (CWYW phase 4)")
    p.add_argument("docx")
    p.add_argument("--map", required=True, help="map.json from 'refs library-map'")
    p.add_argument("--out", required=True, help="new .docx")
    p.add_argument("--ledger", help="ledger JSON (default: <out stem>.temp-citations.json)")
    p.add_argument("--skip-unmapped", action="store_true",
                   help="leave unresolved placeholders as text instead of refusing")
    p.set_defaults(func=_guard(cmd_temp_citations))

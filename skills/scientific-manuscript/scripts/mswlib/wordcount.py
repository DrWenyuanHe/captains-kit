"""Manuscript word counts on the accepted view, and the title-page word-count stamp.

House method (the author's title-page rule):
  abstract body (paragraphs after the "Abstract" Heading 1 and before the
  "Abbreviations" or "Keywords" paragraph, or before the next Heading 1 when
  there is none, so a structured abstract's subheadings stay inside it)
  + every paragraph from the "1. Introduction" Heading 1 (inclusive) up to the
  "References" Heading 1 (exclusive): headings, Limitations, Contributions,
  Data availability, Acknowledgements and declaration lines included.
  Excluded: title page, abbreviation table, reference list, figure legends and
  anything after References. Paragraphs starting "Word count:" never count.
Strict method: abstract body + Introduction through the end of Conclusions,
  body paragraphs only (no headings, no back matter or declarations).
Words are runs of non-whitespace (ASCII whitespace) in the accepted text; tabs,
line and page breaks separate words, non-breaking hyphens join them, pictures
and other objects are not words, field codes are not text but field results
(citation numbers) are.

The title page states what the number leaves out: scope_text() builds the
parenthesis from the method and the profile's word_limit.excludes, and warns
where the two disagree (for example, a journal that excludes the abstract,
which both methods count).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .config import load_config, write_json_exclusive
from .docx import Package
from .profile import article_rules, load_profile
from .views import view_doc
from .wordml import OBJECT_CHAR, iter_paragraphs, map_fields, paragraph_style, paragraph_text

METHODS = ("house", "strict")
TOKEN = re.compile(r"\S+", re.ASCII)
_ABSTRACT = re.compile(r"^\s*abstract\b", re.I)
_INTRODUCTION = re.compile(r"^\s*(?:\d+\.?\s*)?introduction\b", re.I)
_KEYWORDS = re.compile(r"^\s*key\s*words?\b", re.I)
_CONCLUSIONS = re.compile(r"^\s*(?:\d+\.?\s*)?conclusions?\b", re.I)
_REFERENCES = re.compile(r"^\s*(?:\d+\.?\s*)?(?:references|bibliography|literature cited)\b", re.I)
_NUMBERED = re.compile(r"^\s*\d")
_WORD_COUNT_LINE = re.compile(r"^\s*word count\s*:", re.I)
_STATED = re.compile(r"(word count\s*:\s*)(\d[\d,.\u00a0\u202f\u2009 ]*\d|\d)(\s*words?\b)?", re.I)
_HEADING_NAME = re.compile(r"^heading\s*(\d)$", re.I)
_SCOPE = re.compile(r"^(\s*words?\s*)\(([^()]*)\)")

# word_limit.excludes keys -> the words the title page uses for them
EXCLUSION_WORDS = {
    "references": "references", "figure_legends": "figure legends", "legends": "figure legends",
    "supplementary": "supplementary material", "supplementary_material": "supplementary material",
    "supplementary_legends": "supplementary legends", "tables": "tables", "title_page": "title page",
    "abbreviations": "abbreviations", "abstract": "abstract", "headings": "headings",
    "limitations": "Limitations", "back_matter": "back matter", "declarations": "declarations",
    "acknowledgements": "acknowledgements", "author_contributions": "author contributions",
    "funding": "funding", "data_availability": "data availability",
}
# What each method leaves out. Tables count as left out because the house layout keeps them in
# separate files (the abbreviation table, the only in-text table, is never counted).
_LEFT_OUT_BY_BOTH = {"references", "figure_legends", "legends", "supplementary", "supplementary_material",
                     "supplementary_legends", "tables", "title_page", "abbreviations"}
LEFT_OUT = {"house": _LEFT_OUT_BY_BOTH,
            "strict": _LEFT_OUT_BY_BOTH | {"headings", "limitations", "back_matter", "declarations",
                                           "acknowledgements", "author_contributions", "funding",
                                           "data_availability"}}
ALWAYS_NAMED = ("references", "figure_legends")      # visible parts both methods leave out
STRICT_NAMED = ("headings", "limitations", "back_matter")


class WordCountError(ValueError):
    """The document lacks a boundary the counting rule needs."""


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def scope_text(method: str = "house", rules: dict | None = None) -> tuple[str, list[str]]:
    """The title page's word-count parenthesis (without brackets) and warnings.

    It names the journal's word_limit.excludes that the method also leaves out, in the profile's
    order, then references and figure legends if the profile did not name them, so the statement
    stays true for the number. Strict adds its own scope. Without word_limit.excludes the house
    wording is "excluding references and figure legends"."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    excludes = ((rules or {}).get("word_limit") or {}).get("excludes")
    listed = ([str(item).strip().lower().replace(" ", "_").replace("-", "_") for item in excludes]
              if isinstance(excludes, list) else list(ALWAYS_NAMED))
    warnings = []
    names = list(STRICT_NAMED) if method == "strict" else []
    for key in listed:
        if key in LEFT_OUT[method]:
            names.append(key)
        elif key in EXCLUSION_WORDS:
            warnings.append(f"the journal's word limit excludes {EXCLUSION_WORDS[key]}, but the {method} count "
                            "includes it; the title page does not claim that exclusion")
        elif key:
            warnings.append(f"the journal's word limit excludes {key.replace('_', ' ')!r}, which the {method} "
                            "count does not know; check by hand whether the count includes it")
    for key in ALWAYS_NAMED:
        if key not in names and not (key == "figure_legends" and "legends" in names):
            names.append(key)
            if isinstance(excludes, list):
                warnings.append(f"the {method} count leaves out {EXCLUSION_WORDS[key]}, which the journal's word "
                                "limit does not exclude")
    words: list[str] = []
    for key in names:
        if EXCLUSION_WORDS[key] not in words:
            words.append(EXCLUSION_WORDS[key])
    text = "excluding " + _join(words)
    if method == "strict":
        text = "abstract and main text to the end of Conclusions; " + text
    return text, warnings


def words(text: str) -> int:
    return len(TOKEN.findall(text.replace(OBJECT_CHAR, " ")))


def format_count(number: int) -> str:
    return f"{number:,}"


def style_names(package: Package) -> dict:
    """styleId -> style name, from word/styles.xml (empty when absent)."""
    if "word/styles.xml" not in package.parts:
        return {}
    doc = package.xml("word/styles.xml")
    out = {}
    for style in doc.iter("w:style"):
        name = style.find("w:name")
        out[style.get("w:styleId", "")] = name.get("w:val", "") if name is not None else ""
    return out


def heading_level(paragraph, names: dict) -> int:
    style = paragraph_style(paragraph)
    if not style:
        return 0
    for candidate in (names.get(style, ""), style):
        match = _HEADING_NAME.match(candidate.replace("_", " ").strip())
        if match:
            return int(match.group(1))
    return 0


def paragraph_records(package: Package) -> list[dict]:
    """Accepted-view paragraphs: text, heading level, word count."""
    doc = view_doc(package.document, "accept")
    names = style_names(package)
    records = []
    for index, paragraph in enumerate(iter_paragraphs(doc)):
        text = paragraph_text(doc, paragraph)
        records.append({"index": index, "text": text, "level": heading_level(paragraph, names),
                        "words": words(text), "paraId": paragraph.get("w14:paraId")})
    return records


def _first(records, start, test):
    for record in records[start:]:
        if test(record):
            return record["index"]
    return None


def _preview(text, limit=60):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def boundaries(records: list[dict]) -> dict:
    """Locate the headings the counting rules need; raise WordCountError when one is missing."""
    level1 = [r for r in records if r["level"] == 1]
    found = ", ".join(repr(_preview(r["text"], 40)) for r in level1[:12]) or "none"
    abstract = _first(records, 0, lambda r: r["level"] == 1 and _ABSTRACT.match(r["text"]))
    if abstract is None:
        raise WordCountError("no Heading 1 starting 'Abstract' in the accepted view "
                             f"(Heading 1 paragraphs found: {found})")
    introduction = _first(records, abstract + 1, lambda r: r["level"] == 1 and _INTRODUCTION.match(r["text"]))
    if introduction is None:
        raise WordCountError("no Heading 1 '1. Introduction' after the Abstract heading "
                             f"(Heading 1 paragraphs found: {found})")
    references = _first(records, introduction + 1, lambda r: r["level"] == 1 and _REFERENCES.match(r["text"]))
    if references is None:
        raise WordCountError("no Heading 1 'References' after the Introduction heading "
                             f"(Heading 1 paragraphs found: {found})")
    abbreviations = _first(records[:introduction], abstract + 1,
                           lambda r: r["text"].strip().rstrip(":").strip().lower() == "abbreviations")
    keywords = _first(records[:introduction], abstract + 1, lambda r: _KEYWORDS.match(r["text"]))
    marks = [(i, rule) for i, rule in ((abbreviations, "the 'Abbreviations' paragraph"),
                                       (keywords, "the 'Keywords' paragraph")) if i is not None]
    if marks:
        abstract_end, abstract_rule = min(marks)
    else:
        # A structured abstract's subheadings (Background, Methods, ...) stay inside it.
        abstract_end = _first(records[:introduction], abstract + 1, lambda r: r["level"] == 1)
        if abstract_end is None:
            abstract_end = introduction
        abstract_rule = (f"the next Heading 1 ({_preview(records[abstract_end]['text'], 40)!r}); "
                         "no 'Abbreviations' or 'Keywords' paragraph")
    conclusions = _first(records[:references], introduction + 1,
                         lambda r: r["level"] == 1 and _CONCLUSIONS.match(r["text"]))
    if conclusions is not None:
        strict_end = _first(records[:references], conclusions + 1, lambda r: r["level"] == 1)
        strict_end = references if strict_end is None else strict_end
        strict_rule = f"end of {_preview(records[conclusions]['text'], 40)!r}"
    else:
        strict_end = _first(records[:references], introduction + 1,
                            lambda r: r["level"] == 1 and not _NUMBERED.match(r["text"]))
        strict_end = references if strict_end is None else strict_end
        strict_rule = (f"no Conclusions heading; stops at {_preview(records[strict_end]['text'], 40)!r} "
                       "(the first unnumbered Heading 1)")
    return {"abstract": abstract, "abstract_end": abstract_end, "abstract_rule": abstract_rule,
            "introduction": introduction, "conclusions": conclusions, "strict_end": strict_end,
            "strict_rule": strict_rule, "references": references}


def _sum(records, start, end, headings=True):
    total = 0
    for record in records[start:end]:
        if _WORD_COUNT_LINE.match(record["text"]):
            continue
        if not headings and record["level"]:
            continue
        total += record["words"]
    return total


def count_records(records: list[dict]) -> dict:
    marks = boundaries(records)
    abstract = _sum(records, marks["abstract"] + 1, marks["abstract_end"])
    house_body = _sum(records, marks["introduction"], marks["references"])
    strict_body = _sum(records, marks["introduction"], marks["strict_end"], headings=False)
    text = lambda key: _preview(records[marks[key]]["text"], 40)  # noqa: E731
    return {
        "abstract": abstract,
        "house": {"total": abstract + house_body, "abstract": abstract, "body": house_body,
                  "scope": f"abstract body (ends at {marks['abstract_rule']}) + {text('introduction')!r} "
                           f"up to {text('references')!r}, headings and declarations included"},
        "strict": {"total": abstract + strict_body, "abstract": abstract, "body": strict_body,
                   "scope": f"abstract body + {text('introduction')!r} to the {marks['strict_rule']}, "
                            "no headings or declarations"},
        "boundaries": {key: (value if not isinstance(value, int) else
                             {"paragraph": value, "text": _preview(records[value]["text"], 60)})
                       for key, value in marks.items() if value is not None},
    }


def _stated_scope(text: str) -> str | None:
    """The parenthesis after 'Word count: N words', or None."""
    match = _STATED.search(text)
    if match is None:
        return None
    scope = _SCOPE.match(text[match.end(2):])
    return " ".join(scope.group(2).split()) if scope else None


def stated_count(records: list[dict]) -> dict | None:
    """The title page's 'Word count: N words' line, if any."""
    for record in records:
        if _WORD_COUNT_LINE.match(record["text"]):
            match = _STATED.search(record["text"])
            number = int(re.sub(r"\D", "", match.group(2))) if match else None
            return {"paragraph": record["index"], "paraId": record["paraId"], "text": record["text"].strip(),
                    "value": number, "scope": _stated_scope(record["text"])}
    return None


def count_package(package: Package) -> dict:
    """House and strict counts of a package (raises WordCountError on missing boundaries)."""
    records = paragraph_records(package)
    result = count_records(records)
    result["stated"] = stated_count(records)
    return result


def analyse(source, *, profile: dict | None = None, method: str = "house") -> dict:
    """Counts, the title-page statement and the journal limits for one .docx."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    package = source if isinstance(source, Package) else Package(source)
    records = paragraph_records(package)
    counts = count_records(records)
    stated = stated_count(records)
    chosen = counts[method]["total"]
    rules = article_rules(profile or load_profile())
    limit = rules.get("word_limit") or {}
    maximum = limit.get("max")
    if maximum:
        over = chosen - int(maximum)
        limit_status = {"max": maximum, "severity": limit.get("severity", "warn"), "count": chosen,
                        "status": "over" if over > 0 else "within", "over_by": max(over, 0)}
    else:
        limit_status = {"max": None, "status": "no limit", "count": chosen}
    abstract_rule = rules.get("abstract") or {}
    abstract_max = abstract_rule.get("max_words")
    abstract_status = {"words": counts["abstract"], "max": abstract_max,
                       "status": ("over" if abstract_max and counts["abstract"] > abstract_max else
                                  "within" if abstract_max else "no limit")}
    expected_scope, scope_warnings = scope_text(method, rules)
    stated_scope = stated["scope"] if stated else None
    scope = {"expected": expected_scope, "stated": stated_scope,
             "matches": (stated_scope == expected_scope) if stated_scope is not None else None,
             "warnings": scope_warnings}
    return {
        "file": str(package.path) if package.path else None, "sha256": package.sha256, "method": method,
        "house": counts["house"], "strict": counts["strict"],
        "document": {"total": sum(r["words"] for r in records),
                     "scope": "every paragraph of the accepted main text (close to Word's own statistic)"},
        "boundaries": counts["boundaries"],
        "stated": stated["value"] if stated else None, "stated_line": stated["text"] if stated else None,
        "matches": (stated["value"] == chosen) if stated and stated["value"] is not None else None,
        "limit": limit_status, "abstract": abstract_status, "scope": scope,
        "journal": ((profile or {}).get("journal") or {}).get("id"),
    }


def stamp_spec(source, count: int, *, method: str = "house", rules: dict | None = None) -> dict | None:
    """Edit spec for the 'Word count:' line (None when it already states `count` and the scope).

    It replaces the digits; when the line's parenthesis differs from scope_text(method, rules), the
    same edit also rewords the parenthesis (the build turns the difference into minimal tracked
    changes). `rules` defaults to the house profile's research rules."""
    from .edit import ParagraphModel

    package = source if isinstance(source, Package) else Package(source)
    doc = package.document
    fmap = map_fields(doc)
    paragraphs = list(iter_paragraphs(doc))
    hits = []
    for index, paragraph in enumerate(paragraphs):
        if _WORD_COUNT_LINE.match(paragraph_text(doc, paragraph)):
            hits.append(index)
    if not hits:
        raise WordCountError("no 'Word count:' paragraph on the title page; add the line "
                             "'Word count: 0 words (excluding references and figure legends)' first")
    if len(hits) > 1:
        raise WordCountError(f"{len(hits)} paragraphs start with 'Word count:' (need exactly 1)")
    index = hits[0]
    model = ParagraphModel(doc, fmap, paragraphs[index], index)
    plain = model.plain
    match = _STATED.search(plain)
    if match is None:
        raise WordCountError(f"the word-count line has no number to update: {_preview(plain)!r}")
    old = match.group(2)
    new = format_count(count)
    scope, _warnings = scope_text(method, rules if rules is not None else article_rules(load_profile()))
    stated = _SCOPE.match(plain[match.end(2):])
    reword = stated is not None and " ".join(stated.group(2).split()) != scope
    if old == new and not reword:
        return None
    pid = paragraphs[index].get("w14:paraId")
    same_id = [p for p in paragraphs if pid and p.get("w14:paraId") == pid]
    para = pid if pid and len(same_id) == 1 else {"contains": match.group(1).strip()}
    if reword:
        edit = {"id": "word-count", "op": "replace", "para": para, "before": match.group(1),
                "old": plain[match.start(2):match.end(2) + stated.end()],
                "new": f"{new}{stated.group(1)}({scope})",
                "purpose": f"update the title-page word count to the {method} count ({old} -> {new}) and "
                           "state what that count leaves out"}
        return {"edits": [edit]}
    edit = {"id": "word-count", "op": "replace", "para": para, "before": match.group(1), "old": old, "new": new,
            "after": match.group(3) or "",
            "purpose": f"update the title-page word count to the {method} count ({old} -> {new})"}
    if not edit["after"]:
        del edit["after"]
    return {"edits": [edit]}


# -- command line ---------------------------------------------------------------------

def _resolve(args):
    config = load_config(Path(args.docx).resolve().parent)
    profile = load_profile(args.profile, config=config)
    method = args.method or (config.get("word_count") or {}).get("method") or "house"
    if method not in METHODS:
        method = "house"
    return profile, method


def cmd_wordcount(args) -> int:
    try:
        profile, method = _resolve(args)
        result = analyse(args.docx, profile=profile, method=method)
    except (OSError, ValueError) as error:          # WordCountError is a ValueError
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    chosen = result[method]["total"]
    limit = result["limit"]
    hard_over = limit["status"] == "over" and str(limit.get("severity", "")).lower() in ("error", "hard", "fail")
    mismatch = result["matches"] is False
    status = 1 if (mismatch or hard_over) else 0
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        house, strict = result["house"], result["strict"]
        print(f"wordcount: {args.docx}")
        print(f"  house:  {format_count(house['total'])} words (abstract {format_count(house['abstract'])} + "
              f"body {format_count(house['body'])}); {house['scope']}")
        print(f"  strict: {format_count(strict['total'])} words (abstract {format_count(strict['abstract'])} + "
              f"body {format_count(strict['body'])}); {strict['scope']}")
        print(f"  whole document: {format_count(result['document']['total'])} words (all accepted text)")
        if result["stated"] is None:
            line = result["stated_line"]
            print("  title page: no 'Word count: N words' line" if line is None else
                  f"  title page: word-count line has no number: {line!r}")
        else:
            verdict = "matches" if result["matches"] else "DOES NOT MATCH"
            print(f"  title page states {format_count(result['stated'])}: {verdict} the {method} count "
                  f"({format_count(chosen)})")
        scope = result["scope"]
        if scope["matches"] is False:
            print(f"  NOTE: the title page's parenthesis ({scope['stated']!r}) does not describe the {method} "
                  f"count; expected ({scope['expected']!r}); --stamp-spec rewords it")
        for warning in scope["warnings"]:
            print(f"  NOTE: {warning}")
        if limit["max"]:
            text = (f"OVER by {format_count(limit['over_by'])}" if limit["status"] == "over" else "within")
            print(f"  limit: {format_count(limit['max'])} words ({result['journal'] or 'profile'}, severity "
                  f"{limit['severity']}): {text} ({method} count)")
        else:
            print("  limit: none in the profile")
        abstract = result["abstract"]
        if abstract["max"]:
            print(f"  abstract: {abstract['words']} words (max {abstract['max']}): {abstract['status']}")
    if args.stamp_spec:
        out = Path(args.stamp_spec)
        if out.exists():
            print(f"ERROR: {out} already exists; choose a new file", file=sys.stderr)
            return 2
        try:
            spec = stamp_spec(args.docx, chosen, method=method, rules=article_rules(profile))
        except WordCountError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        if spec is None:
            print(f"stamp: the title page already states {format_count(chosen)} with the {method} scope; "
                  "no spec written", file=sys.stderr)
        else:
            write_json_exclusive(out, spec)
            edit = spec["edits"][0]
            print(f"stamp spec: {out} (replace {edit['old']!r} -> {edit['new']!r}; apply with msw.py build)",
                  file=sys.stderr)
            # The mismatch is being corrected by the spec; only a hard limit still fails.
            status = 1 if hard_over else 0
    return status


def register(subparsers):
    p = subparsers.add_parser("wordcount", help="house and strict word counts, the title-page statement and "
                              "journal limits; optionally write a tracked stamp spec")
    p.add_argument("docx")
    p.add_argument("--profile", help="journal profile JSON (default: the project's, else house defaults)")
    p.add_argument("--method", choices=METHODS, help="count compared with the title page and the limit "
                   "(default: manuscript.json word_count.method, else house)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.add_argument("--stamp-spec", metavar="OUT", help="write an edit spec that updates the digits of the "
                   "'Word count:' line, and its parenthesis when that describes another scope (build it with "
                   "msw.py build to get a tracked change)")
    p.set_defaults(func=cmd_wordcount)

"""House-style and journal-profile lint of a manuscript's accepted view.

    msw.py lint DOCX [--profile P] [--spelling en-US|en-GB|en-CA] [--json OUT] [--markdown OUT]

The lint reads the accept-all view, so tracked deletions are ignored and tracked
insertions are checked. Every finding carries a rule id, a severity (error, warn
or info), the paragraph index in the accepted view with its w14:paraId, a short
snippet (at most 80 characters) and a suggestion. Heuristic rules (abbreviations,
gene symbols, spelling variety) never raise errors. The exit code is 1 when any
error is found, 2 when the command cannot run, else 0.

Typography and statistics rules skip the title page, the rendered bibliography and
citation or hyperlink field results (EndNote writes "(13-17)" itself). Formats come
from the journal profile's `statistics` block when it states them and otherwise
from the document's own majority, so a consistent manuscript passes.

A separate title-page file (no heading paragraph anywhere, and at least one title-page
label line such as "Keywords:", "Running title:", "Word count:" or "*Corresponding
author") is linted as a title page: its first non-empty paragraph is the title, the
title, running-title and keyword limits are checked, and the body-only checks
(declarations, figure call-outs, abbreviations, word count, references) are skipped.
"""

from __future__ import annotations

import bisect
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from . import pathutil
from .config import load_config, write_json_exclusive
from .docx import Package
from .endnote import PLACEHOLDER, bibliography_entries, census
from .profile import article_rules, load_profile
from .views import view_doc
from .wordml import OBJECT_CHAR, element_char, iter_paragraphs, map_fields, paragraph_style, run_flags
from .xmltree import XMLDoc

SEVERITIES = ("error", "warn", "info")
VARIETIES = ("en-US", "en-GB", "en-CA")

# Rule catalogue: id -> (severity, what it checks). limits.word-count takes its severity from the profile.
RULES = {
    "stats.p-spacing": ("warn", "P values spaced or unspaced as the profile (or the document majority) sets"),
    "stats.p-case": ("warn", "capital P for P values"),
    "stats.p-italic": ("warn", "P in italics (or roman) as the profile (or the document majority) sets"),
    "stats.q-spacing": ("warn", "q values spaced consistently"),
    "stats.n-spacing": ("warn", "sample sizes (n = 12) spaced consistently"),
    "stats.pm-spacing": ("warn", "mean \xb1 SD spaced consistently"),
    "typo.minus": ("warn", "hyphen-minus used as a minus sign before a number (use U+2212)"),
    "typo.eponym-dash": ("warn", "eponym pairs joined by a hyphen instead of an en dash (Mann\u2013Whitney)"),
    "typo.degree-sign": ("warn", "masculine ordinal \xba used for degrees (use \xb0)"),
    "typo.range-dash": ("warn", "hyphen between the bounds of a numeric range (use an en dash)"),
    "typo.double-space": ("warn", "two or more ordinary spaces in a row (NBSP ignored)"),
    "typo.space-before-punctuation": ("warn", "ordinary space before , . ; : ! ? or a closing bracket"),
    "spelling.variety": ("info", "spelling variety summary (dominant or chosen variety and counts)"),
    "spelling.mixed": ("warn", "word spelt in a different variety from the chosen or dominant one"),
    "citation.placeholder": ("error", "unconverted temporary citation ([PMID: n], {Author, Year #n}, [REF: key])"),
    "citation.adjacent-groups": ("warn", "separate adjacent citation groups such as (4) (5)"),
    "heading.empty": ("error", "heading paragraph with no text"),
    "heading.trailing": ("warn", "heading ending in a colon, space, tab or non-breaking space"),
    "heading.indent": ("warn", "heading with a direct first-line indent"),
    "heading.numbering": ("warn", "typed heading numbers with a gap, repeat or wrong parent (2.1, 2.3)"),
    "format.highlight": ("warn", "residual highlighting"),
    "format.proofing-language": ("warn", "more than one proofing language (w:lang) in the text"),
    "format.proofing-variety": ("info", "dominant proofing language differs from the spelling variety"),
    "format.reference-spacing": ("warn", "reference list spacing differs from the profile"),
    "abbrev.before-definition": ("warn", "abbreviation used before its 'long form (ABBR)' definition"),
    "abbrev.undefined": ("warn", "abbreviation never defined in the text (heuristic)"),
    "abbrev.redefined": ("info", "abbreviation defined more than once in the body"),
    "abbrev.single-use": ("info", "abbreviation defined but used at most once afterwards"),
    "abstract.abbreviation": ("warn", "abbreviation in the abstract when the profile forbids them"),
    "abstract.citation": ("error", "citation in the abstract when the profile forbids them"),
    "abstract.format": ("warn", "abstract is not a single paragraph when the profile asks for one"),
    "nomenclature.gene-italic": ("info", "gene-like symbol in roman next to a gene-context word"),
    "callout.fig-abbreviation": ("warn", "'Fig.' abbreviation in the text"),
    "figures.order": ("warn", "figures not first cited in numerical order"),
    "figures.uncited": ("error", "figure with a legend that the text never cites"),
    "figures.gap": ("warn", "figure number skipped in the call-outs"),
    "figures.missing-legend": ("warn", "cited figure without a legend"),
    "limits.title": ("error", "title longer than the profile allows"),
    "limits.running-title": ("error", "running (short) title longer than the profile allows"),
    "limits.keywords": ("error", "keyword count outside the profile's range"),
    "limits.abstract-words": ("error", "abstract longer than the profile allows"),
    "limits.word-count": ("warn", "word count above the profile limit (severity from the profile)"),
    "limits.word-count-stated": ("warn", "title-page word count differs from the computed count"),
    "limits.references": ("warn", "more references than the profile recommends"),
    "limits.figures": ("warn", "more figures than the profile recommends"),
    "limits.missing": ("info", "title-page item needed for a limit check was not found"),
    "declarations.missing": ("warn", "declaration named in the profile was not found"),
    "declarations.order": ("warn", "declarations out of the profile's order"),
}

# -- patterns ---------------------------------------------------------------------------

SP = "[ \xa0\u2009\u202f\u200a]"          # spaces allowed around statistical operators
MASK = "\x00"                                # stands in for masked field results and URLs
MASK_FIELD_PREFIXES = ("EN.", "HYPERLINK", "ADDIN")

P_RE = re.compile(rf"(?<![A-Za-z0-9_\u0370-\u03ff])([Pp])({SP}*)([=<>\u2264\u2265\u2a7d\u2a7e])({SP}*)(?=[\u2212\-]?\.?\d)")
Q_RE = re.compile(rf"(?<![A-Za-z0-9_\u0370-\u03ff])(q)({SP}*)([=<>\u2264\u2265\u2a7d\u2a7e])({SP}*)(?=\.?\d)")
N_RE = re.compile(rf"(?<![A-Za-z0-9_\u0370-\u03ff])(n)({SP}*)(=)({SP}*)(?=\d)")
PM_RE = re.compile(rf"(?<=[\d%)])({SP}*)(\xb1)({SP}*)(?=[\d(\u2212\-.])")
STYLE_RE = re.compile(rf"({SP}*)[=<>\u2264\u2265\xb1]({SP}*)")
MINUS_RE = re.compile(r"(?:^|(?<=[\s(\[=<>\u2264\u2265\xb1~\u2248,;:/\x00]))[-\u2010](?=\.?\d)")
RANGE_RE = re.compile(rf"(?<![\w.,\-/\u2010\u2212\u2013])(\d+(?:\.\d+)?)({SP}?)[-\u2010]({SP}?)(\d+(?:\.\d+)?)"
                      r"(?![\w\-/\u2010]|\.\d)")
DOUBLE_SPACE_RE = re.compile(r" {2,}")
SPACE_PUNCT_RE = re.compile(r"(?<=[^\s\x00]) +(?=[,.;:!?)\]])")
DEGREE_RE = re.compile(rf"\d{SP}?\xba|\xba{SP}?[CF]\b")
FIG_ABBR_RE = re.compile(rf"\bFig(s?)\.(?={SP}*S?\d)")
URL_RE = re.compile(r"https?://\S+|\bwww\.\S+|\bdoi:\s*\S+|\b10\.\d{4,9}/\S+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
REF_PLACEHOLDER = re.compile(r"\[REFS?\s*:\s*[^\]\n]+\]", re.I)
BRACE_PLACEHOLDER = re.compile(r"\{[A-Z][^{}\n]{0,80}?,\s*(?:\d{4}[a-z]?|n\.d\.|in press)(?:\s*#\s*\d+)?"
                               r"(?:\s*;[^{}\n]*)?\}")
_GROUP = rf"\d{{1,4}}(?:{SP}*[,\u2013\-]{SP}*\d{{1,4}})*"
ADJACENT_RE = re.compile(rf"\(({_GROUP})\){SP}*\(({_GROUP})\)|\[({_GROUP})\]{SP}*\[({_GROUP})\]")

EPONYMS = [("Mann", "Whitney"), ("Benjamini", "Hochberg"), ("Benjamini", "Yekutieli"), ("Kenward", "Roger"),
           ("D['\u2019]Agostino", "Pearson"), ("Kruskal", "Wallis"), ("Kaplan", "Meier"), ("Bland", "Altman"),
           ("Kolmogorov", "Smirnov"), ("Shapiro", "Wilk"), ("Hosmer", "Lemeshow"), ("Mantel", "Haenszel"),
           ("Newman", "Keuls"), ("Tukey", "Kramer"), ("Games", "Howell"), ("Brown", "Forsythe"),
           ("Holm", "(?:\u0160id\xe1k|Sidak|Bonferroni)"), ("Michaelis", "Menten"), ("Cochran", "Armitage"),
           ("Wilcoxon", "Mann"), ("Welch", "Satterthwaite")]
EPONYM_RE = re.compile(r"\b(" + "|".join(f"{a}[-\u2010\u2011]{b}" for a, b in EPONYMS) + r")\b")

HEADING_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?(?=\s|$)")
ABSTRACT_HEAD = re.compile(r"^\s*(abstract|summary)\b", re.I)
REFERENCES_HEAD = re.compile(r"^\s*(?:\d+\.?\s*)?(references|bibliography|literature cited)\b", re.I)
LEGENDS_HEAD = re.compile(r"^\s*(figure legends?|legends? (to|for|of) figures|figure captions?|"
                          r"figures? and (their )?legends)\b", re.I)
CAPTION_RE = re.compile(r"^[\s\f]*Figure\s+(\d+)\s*[.:]")
CALLOUT_RE = re.compile(r"(?<![A-Za-z])(Supplementa(?:ry|l)\s+)?(Figs?\.|Figures?)\s*"
                        r"(\d+[A-Za-z]*(?:\s*(?:,|and|&|\u2013|\u2014|-|to)\s*\d+[A-Za-z]*)*)")
RUNNING_TITLE = re.compile(r"^\s*(?:running\s+(?:title|head)|short\s+title)\s*:\s*(.*)$", re.I | re.S)
KEYWORDS_LINE = re.compile(r"^\s*key\s?-?words?\s*:\s*(.*)$", re.I | re.S)
WORD_COUNT_LINE = re.compile(r"^\s*word count\s*:\s*(\d[\d,.\xa0\u202f ]*\d|\d)?", re.I)
LABELS = re.compile(r"^\s*(running\s+(title|head)|short\s+title|key\s?-?words?|word count|"
                    r"\*?\s*corresponding)\b", re.I)
ROMAN = re.compile(r"^X{0,2}(IX|IV|V?I{0,3})$")
ABBR_TOKEN = re.compile(r"(?<![\w\-\u2010.])([A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)(?![\w\-\u2010])")
ABBR_DEF_PAREN = re.compile(r"\(\s*([A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\s*(?:[;,][^()]*)?\)")
ABBR_DEF_REVERSE = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\s+\((?:i\.e\.,?\s*)?[a-z][^()]{3,}\)")
ABBR_DEF_LIST = re.compile(r"(?:^|[.;]\s+)([A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*),\s+[a-z]")
UNIVERSAL_ABBREVIATIONS = {
    "DNA", "RNA", "PCR", "qPCR", "SD", "SEM", "CI", "ANOVA", "BMI", "cDNA", "USA", "UK", "US", "EU", "ATP",
    "ADP", "UV", "ID", "ISO", "SI", "AI", "ORCID", "DOI", "PMID", "PMCID", "URL", "PDF", "SPSS", "SAS", "CO2", "H2O",
}
REGION_CODES = set("AL AK AZ AR CA CO CT DE FL GA HI IA ID IL IN KS KY LA MA MD ME MI MN MO MS MT NC ND NE NH NJ "
                   "NM NV NY OH OK OR PA RI SC SD TN TX UT VA VT WA WI WV WY DC AB BC MB NB NL NS NT NU ON PE QC "
                   "SK YT".split())
GENE_CUE_AFTER = re.compile(r"^[\s\-\u2010]*(gene|genes|mRNA|mRNAs|transcripts?|expression|knockout|knockdown|"
                            r"promoter|alleles?|locus|polymorphisms?|siRNA|shRNA|overexpression|deletion|"
                            r"silencing|null|deficient)\b")
GENE_CUE_BEFORE = re.compile(r"\b(expression|knockout|knockdown|overexpression|deletion|silencing|"
                             r"transcription|transcripts?) of\s+$")

DECLARATION_LABELS = {
    "declaration_of_interest": r"(declarations? of (competing )?interests?|conflicts? of interests?|"
                               r"competing interests?|disclosures?|duality of interest)",
    "funding": r"(funding( sources?| information| statement)?|financial support|sources? of funding|grant support)",
    "author_contributions": r"((authors?['\u2019]?|author) contributions?( statement)?|contributions|contributorship|"
                            r"credit( author)? statement)",
    "acknowledgements": r"(acknowledge?ments?)",
    "data_availability": r"(data availability( statement)?|availability of data( and materials?)?|"
                         r"data sharing( statement)?)",
    "ai_disclosure": r"((declaration of |use of |statement on )?(the )?(use of )?(generative )?"
                     r"(ai|artificial intelligence)( tools?)?( use| disclosure| statement)?"
                     r"( in (scientific )?writing)?)",
    "ethics": r"(ethics( approval| statement)?|ethical approval)",
    "consent": r"((patient )?consent( for publication| statement)?)",
    "preprint": r"(preprint( disclosure)?)",
}


# -- spelling pairs -----------------------------------------------------------------------
# category -> varieties that use the US form; the others use the UK form.

US_FORM_VARIETIES = {"ize": {"en-US", "en-CA"}, "yze": {"en-US", "en-CA"}, "ae": {"en-US", "en-CA"},
                     "our": {"en-US"}, "re": {"en-US"}, "ll": {"en-US"}, "misc": {"en-US"}}


def _spelling_table() -> dict:
    """lower-case word -> (category, form 'us'|'uk', the other form)."""
    table = {}

    def pair(category, us, uk):
        table[us] = (category, "us", uk)
        table[uk] = (category, "uk", us)

    suffixes = ("e", "ed", "es", "ing", "ation", "ations", "er", "ers", "able")
    for stem in ("normal", "random", "character", "summar", "organ", "util", "recogn", "minim", "maxim", "optim",
                 "standard", "hospital", "stabil", "visual", "categor", "general", "emphas", "real", "special",
                 "local", "immun", "steril", "neutral", "mobil", "harmon", "homogen", "polar", "apolog"):
        for suffix in suffixes:
            pair("ize", f"{stem}iz{suffix}", f"{stem}is{suffix}")
    for stem in ("anal", "paral", "hydrol", "catal", "dial", "electrol"):
        for suffix in ("e", "ed", "ing", "er", "ers"):
            pair("yze", f"{stem}yz{suffix}", f"{stem}ys{suffix}")
        table[f"{stem}yzes"] = ("yze", "us", f"{stem}yses")   # "-yses" is also the plural noun in both
    for stem in ("col", "fav", "behavi", "tum", "lab", "flav", "od", "vap", "neighb", "hon", "rum", "arm", "harb",
                 "endeav", "vig", "rig", "sav"):
        for prefix in ("", "un"):
            for suffix in ("", "s", "ed", "ing", "al", "ally", "ful", "less", "ite", "ites", "able", "ably",
                           "hood", "ist", "ists", "ism", "ation"):
                pair("our", f"{prefix}{stem}or{suffix}", f"{prefix}{stem}our{suffix}")
    for us, uk in (("center", "centre"), ("centers", "centres"), ("centered", "centred"), ("centering", "centring"),
                   ("epicenter", "epicentre"), ("fiber", "fibre"), ("fibers", "fibres"), ("liter", "litre"),
                   ("liters", "litres"), ("milliliter", "millilitre"), ("milliliters", "millilitres"),
                   ("microliter", "microlitre"), ("microliters", "microlitres"), ("caliber", "calibre"),
                   ("theater", "theatre"), ("theaters", "theatres")):
        pair("re", us, uk)
    for base in ("signal", "label", "model", "travel", "cancel", "fuel", "channel", "tunnel", "level", "counsel",
                 "total", "equal", "marvel", "dial", "pedal"):
        for suffix in ("ed", "ing", "er", "ers"):
            pair("ll", f"{base}{suffix}", f"{base}l{suffix}")
    for us, uk in (("pediatric", "paediatric"), ("pediatrics", "paediatrics"), ("pediatrician", "paediatrician"),
                   ("pediatricians", "paediatricians"), ("estrogen", "oestrogen"), ("estrogens", "oestrogens"),
                   ("estrogenic", "oestrogenic"), ("estradiol", "oestradiol"), ("estrone", "oestrone"),
                   ("estrous", "oestrous"), ("estrus", "oestrus"), ("fetal", "foetal"), ("fetus", "foetus"),
                   ("fetuses", "foetuses"), ("edema", "oedema"), ("edematous", "oedematous"),
                   ("esophagus", "oesophagus"), ("esophageal", "oesophageal"), ("diarrhea", "diarrhoea"),
                   ("orthopedic", "orthopaedic"), ("orthopedics", "orthopaedics"), ("gynecology", "gynaecology"),
                   ("gynecological", "gynaecological"), ("anesthesia", "anaesthesia"),
                   ("anesthetic", "anaesthetic"), ("anesthetics", "anaesthetics"), ("etiology", "aetiology"),
                   ("etiological", "aetiological"), ("cesarean", "caesarean"), ("aging", "ageing"),
                   ("dyspnea", "dyspnoea"), ("apnea", "apnoea"), ("celiac", "coeliac"), ("feces", "faeces"),
                   ("fecal", "faecal")):
        pair("ae", us, uk)
    for tail in ("oglobin", "oglobins", "atology", "atological", "atologist", "atologic", "orrhage", "orrhages",
                 "orrhagic", "atoma", "atomas", "olysis", "olytic", "odynamic", "odynamics", "ostasis", "ostatic",
                 "atocrit", "atopoiesis", "atopoietic", "atoxylin", "aturia", "ophilia"):
        pair("ae", f"hem{tail}", f"haem{tail}")
    for prefix in ("", "hyper", "hypo", "normo", "dys", "eu"):
        for stem in ("glyc", "lipid", "cholesterol", "insulin", "an", "leuk", "isch", "ur", "septic", "hypox",
                     "natr", "kal", "calc", "bacter", "vir", "tox", "uric", "triglycerid", "phosphat", "magnes"):
            for us_end, uk_end in (("emia", "aemia"), ("emias", "aemias"), ("emic", "aemic")):
                pair("ae", f"{prefix}{stem}{us_end}", f"{prefix}{stem}{uk_end}")
    for us, uk in (("gray", "grey"), ("grays", "greys"), ("grayish", "greyish"), ("grayscale", "greyscale"),
                   ("defense", "defence"), ("defenses", "defences"), ("catalog", "catalogue"),
                   ("catalogs", "catalogues"), ("mold", "mould"), ("molds", "moulds"), ("enroll", "enrol"),
                   ("enrolls", "enrols"), ("enrollment", "enrolment"), ("enrollments", "enrolments"),
                   ("fulfill", "fulfil"), ("fulfills", "fulfils"), ("fulfillment", "fulfilment"),
                   ("skillful", "skilful"), ("maneuver", "manoeuvre"), ("maneuvers", "manoeuvres")):
        pair("misc", us, uk)
    return table


SPELLING = _spelling_table()
WORD_RE = re.compile(r"[A-Za-z]+")


# -- data model -------------------------------------------------------------------------------

@dataclass
class Finding:
    rule: str
    severity: str
    paragraph: int | None
    para_id: str | None
    snippet: str
    message: str
    suggestion: str = ""
    section: str = ""

    def to_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity, "paragraph": self.paragraph, "paraId": self.para_id,
                "section": self.section, "snippet": self.snippet, "message": self.message,
                "suggestion": self.suggestion}


@dataclass(eq=False)
class Para:
    index: int
    element: object
    para_id: str | None
    style: str
    level: int
    text: str
    spans: list                 # (start, end, flags, field kind, lang)
    in_table: bool
    region: str = "body"        # title | abstract | front | body | references | legends | appendix
    section: str = ""
    is_bib: bool = False
    masked: str = ""

    def __post_init__(self):
        self._starts = [s[0] for s in self.spans]

    def span_at(self, pos: int):
        i = bisect.bisect_right(self._starts, pos) - 1
        if 0 <= i < len(self.spans) and self.spans[i][0] <= pos < self.spans[i][1]:
            return self.spans[i]
        return None

    def flags_at(self, pos: int) -> frozenset:
        span = self.span_at(pos)
        return span[2] if span else frozenset()

    def all_flag(self, start: int, end: int, flag: str) -> bool:
        return all(flag in self.flags_at(p) for p in range(start, end) if not self.text[p].isspace())

    @property
    def is_heading(self) -> bool:
        return self.level > 0


def _snippet(text: str, start: int, end: int, width: int = 80) -> str:
    start = min(max(0, start), len(text))
    end = min(max(end, start), len(text))
    core_room = width - 6
    if end - start > core_room:
        out = text[start:start + width - 3] + "..."
    else:
        room = core_room - (end - start)
        left = room // 2
        s = max(0, start - left)
        e = min(len(text), end + (room - (start - s)))
        out = ("..." if s > 0 else "") + text[s:e] + ("..." if e < len(text) else "")
    return re.sub(r"[\t\n\r\f\x00]", " ", out)


# -- document model -----------------------------------------------------------------------------

def _style_info(package: Package) -> tuple[dict, dict, int | None]:
    """styleId -> name, styleId -> (basedOn, spacing line), document default spacing line."""
    names, spacing = {}, {}
    default_line = None
    if "word/styles.xml" not in package.parts:
        return names, spacing, default_line
    doc = package.xml("word/styles.xml")
    for style in doc.iter("w:style"):
        sid = style.get("w:styleId", "")
        name = style.find("w:name")
        names[sid] = name.get("w:val", "") if name is not None else ""
        based = style.find("w:basedOn")
        ppr = style.find("w:pPr")
        line = _spacing_line(ppr)
        spacing[sid] = (based.get("w:val") if based is not None else None, line)
    for defaults in doc.iter("w:pPrDefault"):
        ppr = defaults.find("w:pPr")
        default_line = _spacing_line(ppr)
    return names, spacing, default_line


def _spacing_line(ppr) -> int | None:
    if ppr is None:
        return None
    node = ppr.find("w:spacing")
    if node is None or node.get("w:line") is None:
        return None
    if node.get("w:lineRule", "auto") not in ("auto", ""):
        return None
    try:
        return int(node.get("w:line"))
    except ValueError:
        return None


def _spacing_name(line: int) -> str:
    return {240: "single", 360: "1.5 lines", 480: "double"}.get(line, f"{line / 240:g} lines")


def _heading_level(style_id: str, names: dict) -> int:
    for candidate in (names.get(style_id, ""), style_id):
        match = re.fullmatch(r"\s*heading\s*([1-9])\s*", candidate.replace("_", " "), re.I)
        if match:
            return int(match.group(1))
    return 0


def _build_paragraphs(doc: XMLDoc, names: dict) -> list[Para]:
    fmap = map_fields(doc)
    hidden = {"w:del", "w:moveFrom", "mc:Fallback"}
    paragraphs = []
    for index, paragraph in enumerate(iter_paragraphs(doc)):
        parts, spans, pos = [], [], 0
        for run in paragraph.iter("w:r"):
            owner = next((a for a in run.ancestors() if a.tag == "w:p"), None)
            if owner is not paragraph or run.has_ancestor(hidden):
                continue
            text = "".join(c for c in (element_char(doc, e, deleted=False) for e in run.elements()) if c)
            if not text:
                continue
            rpr = run.find("w:rPr")
            lang_el = rpr.find("w:lang") if rpr is not None else None
            lang = (lang_el.get("w:val") or "") if lang_el is not None else ""
            item = fmap.run_field.get(id(run))
            kind = item.kind if item is not None and not isinstance(item, str) else ""
            spans.append((pos, pos + len(text), run_flags(run), kind, lang))
            parts.append(text)
            pos += len(text)
        text = "".join(parts)
        style = paragraph_style(paragraph)
        masked = list(text)
        for start, end, _flags, kind, _lang in spans:
            if kind and kind.upper().startswith(MASK_FIELD_PREFIXES):
                masked[start:end] = MASK * (end - start)
        masked = "".join(masked)
        masked = URL_RE.sub(lambda m: MASK * len(m.group(0)), masked)
        paragraphs.append(Para(index=index, element=paragraph, para_id=paragraph.get("w14:paraId"), style=style,
                               level=_heading_level(style, names), text=text, spans=spans,
                               in_table=paragraph.has_ancestor({"w:tbl"}), masked=masked))
    bib = set()
    for item in fmap.fields:
        if item.kind == "EN.REFLIST" and not item.deleted_instr:
            bib.update(range(max(item.first_paragraph, 0), item.last_paragraph + 1))
    for para in paragraphs:
        style_name = (names.get(para.style, "") + " " + para.style).lower().replace(" ", "")
        para.is_bib = para.index in bib or "bibliography" in style_name
    _assign_regions(paragraphs)
    return paragraphs


def is_title_page_only(paragraphs: list[Para]) -> bool:
    """A separate title-page file: no heading paragraph at all, and a title-page label line.

    The label requirement keeps a document without heading styles (a letter, or a manuscript
    whose headings are bold Normal text) from losing its body checks."""
    if any(p.is_heading for p in paragraphs):
        return False
    return any(LABELS.match(p.text) for p in paragraphs if p.text.strip())


def _assign_regions(paragraphs: list[Para]):
    if is_title_page_only(paragraphs):
        for para in paragraphs:
            para.region, para.section = "title", ""
        return
    first_heading = next((p.index for p in paragraphs if p.is_heading), None)
    region = "title" if first_heading else "body"
    section = ""
    for para in paragraphs:
        text = para.text.strip()
        if para.is_heading:
            section = " ".join(text.split())[:60]
            if ABSTRACT_HEAD.match(text) and region in ("title", "body") and para.level == 1:
                region = "abstract"
            elif REFERENCES_HEAD.match(text) and para.level == 1:
                region = "references"
            elif LEGENDS_HEAD.match(text) and para.level <= 2:
                region = "legends"
            elif para.level == 1 and region in ("title", "abstract", "front"):
                region = "body"
            elif para.level == 1 and region == "references":
                region = "appendix"
        elif region == "abstract" and (para.in_table or text.rstrip(":").strip().lower() == "abbreviations"):
            region = "front"
        para.region = region
        para.section = section


# -- helpers ------------------------------------------------------------------------------------

class _Collector:
    def __init__(self):
        self.findings: list[Finding] = []

    def add(self, rule, para: Para | None, start=0, end=0, message="", suggestion="", severity=None,
            snippet=None):
        severity = severity or RULES[rule][0]
        if para is None:
            self.findings.append(Finding(rule, severity, None, None, snippet or "", message, suggestion, ""))
            return
        if snippet is None:
            snippet = _snippet(para.text, start, end)
        self.findings.append(Finding(rule, severity, para.index, para.para_id, snippet[:80], message, suggestion,
                                     para.section))


def _typo_paragraphs(paragraphs):
    """Paragraphs whose prose the typography and statistics rules read."""
    return [p for p in paragraphs if p.region != "title" and not p.is_bib and p.text.strip()]


def _spacing_of(left: str, right: str) -> str:
    if left and right:
        return "spaced"
    if not left and not right:
        return "unspaced"
    return "uneven"


def _style_spacing(example) -> str | None:
    if not isinstance(example, str) or not example:
        return None
    match = STYLE_RE.search(example)
    return _spacing_of(match.group(1), match.group(2)) if match else None


def _majority(counts: dict, fallback):
    if not counts:
        return fallback
    best = max(counts.values())
    leaders = [k for k, v in counts.items() if v == best]
    return fallback if fallback in leaders else leaders[0]


# -- rules: statistics and typography --------------------------------------------------------

def _check_statistics(paragraphs, stats: dict, out: _Collector):
    paras = _typo_paragraphs(paragraphs)
    p_hits = [(p, m) for p in paras for m in P_RE.finditer(p.masked)]
    p_style = stats.get("p_style")
    spacing_counts = {}
    case_counts = {}
    italic_counts = {}
    for para, match in p_hits:
        kind = _spacing_of(match.group(2), match.group(4))
        if kind != "uneven":
            spacing_counts[kind] = spacing_counts.get(kind, 0) + 1
        case_counts[match.group(1)] = case_counts.get(match.group(1), 0) + 1
        italic = "italic" in para.flags_at(match.start(1))
        italic_counts[italic] = italic_counts.get(italic, 0) + 1
    expected_spacing = _style_spacing(p_style) or _majority(spacing_counts, "spaced")
    source = "the profile" if _style_spacing(p_style) else "this document's majority"
    expected_case = p_style.strip()[0] if isinstance(p_style, str) and p_style.strip()[:1] in ("P", "p") else \
        _majority(case_counts, "P")
    p_italic = stats.get("p_italic")
    expected_italic = p_italic if isinstance(p_italic, bool) else _majority(italic_counts, True)
    tally = f"spaced {spacing_counts.get('spaced', 0)}, unspaced {spacing_counts.get('unspaced', 0)}"
    for para, match in p_hits:
        letter, op = match.group(1), match.group(3)
        kind = _spacing_of(match.group(2), match.group(4))
        wanted = f"{expected_case} {op} " if expected_spacing == "spaced" else f"{expected_case}{op}"
        if kind == "uneven" or kind != expected_spacing:
            out.add("stats.p-spacing", para, match.start(), match.end() + 6,
                    f"P value written {kind}; {source} uses {expected_spacing} ({tally})",
                    f"write '{wanted}'")
        if letter != expected_case:
            out.add("stats.p-case", para, match.start(), match.end() + 6,
                    f"'{letter}' used for a P value; the house style uses '{expected_case}'",
                    f"use '{expected_case}'")
        italic = "italic" in para.flags_at(match.start(1))
        if italic != expected_italic:
            want = "italic" if expected_italic else "roman"
            origin = "the profile" if isinstance(p_italic, bool) else "this document's majority"
            out.add("stats.p-italic", para, match.start(), match.end() + 6,
                    f"P is {'italic' if italic else 'roman'}; {origin} sets it {want} "
                    f"(italic {italic_counts.get(True, 0)}, roman {italic_counts.get(False, 0)})",
                    f"set P in {want}")
    for rule, regex, key, label in (("stats.q-spacing", Q_RE, "q_style", "q value"),
                                    ("stats.n-spacing", N_RE, "n_style", "sample size")):
        hits = [(p, m) for p in paras for m in regex.finditer(p.masked)]
        counts = {}
        for _para, match in hits:
            kind = _spacing_of(match.group(2), match.group(4))
            if kind != "uneven":
                counts[kind] = counts.get(kind, 0) + 1
        fixed = _style_spacing(stats.get(key))
        expected = fixed or _majority(counts, expected_spacing)
        origin = "the profile" if fixed else "this document's majority"
        for para, match in hits:
            kind = _spacing_of(match.group(2), match.group(4))
            if kind == "uneven" or kind != expected:
                sign, op = match.group(1), match.group(3)
                wanted = f"{sign} {op} " if expected == "spaced" else f"{sign}{op}"
                out.add(rule, para, match.start(), match.end() + 4,
                        f"{label} written {kind}; {origin} is {expected} (spaced {counts.get('spaced', 0)}, "
                        f"unspaced {counts.get('unspaced', 0)})", f"write '{wanted}'")
    hits = [(p, m) for p in paras for m in PM_RE.finditer(p.masked)]
    counts = {}
    for _para, match in hits:
        kind = _spacing_of(match.group(1), match.group(3))
        if kind != "uneven":
            counts[kind] = counts.get(kind, 0) + 1
    fixed = _style_spacing(stats.get("mean_sd_style"))
    expected = fixed or _majority(counts, "spaced")
    for para, match in hits:
        kind = _spacing_of(match.group(1), match.group(3))
        if kind == "uneven" or kind != expected:
            out.add("stats.pm-spacing", para, match.start() - 4, match.end() + 4,
                    f"mean \xb1 SD written {kind}; {'the profile' if fixed else 'the document majority'} is "
                    f"{expected} (spaced {counts.get('spaced', 0)}, unspaced {counts.get('unspaced', 0)})",
                    "write '12.3 \xb1 4.5'" if expected == "spaced" else "write '12.3\xb14.5'")


def _check_typography(paragraphs, out: _Collector):
    for para in _typo_paragraphs(paragraphs):
        text = para.masked
        for match in MINUS_RE.finditer(text):
            before = text[:match.start()].rstrip(" \xa0")
            if before and before[-1].isdigit():
                continue
            out.add("typo.minus", para, match.start(), match.end() + 5,
                    "hyphen-minus used as a minus sign", "use the minus sign U+2212 (\u2212)")
        for match in EPONYM_RE.finditer(text):
            fixed = re.sub("[-\u2010\u2011]", "\u2013", match.group(0), count=1)
            out.add("typo.eponym-dash", para, match.start(), match.end(),
                    f"eponym pair '{match.group(0)}' joined by a hyphen", f"use an en dash: '{fixed}'")
        for match in DEGREE_RE.finditer(text):
            out.add("typo.degree-sign", para, match.start(), match.end(),
                    "masculine ordinal indicator \xba (U+00BA) used as a degree sign",
                    "use the degree sign \xb0 (U+00B0), e.g. '4 \xb0C'")
        if para.region != "title":
            for match in RANGE_RE.finditer(text):
                out.add("typo.range-dash", para, match.start(), match.end(),
                        f"hyphen in the numeric range '{match.group(0)}'",
                        f"use an en dash: '{match.group(1)}\u2013{match.group(4)}'")
        for match in DOUBLE_SPACE_RE.finditer(text):
            out.add("typo.double-space", para, match.start(), match.end(),
                    f"{len(match.group(0))} consecutive spaces", "use one space (or a non-breaking space where needed)")
        for match in SPACE_PUNCT_RE.finditer(text):
            nxt = text[match.end():match.end() + 2]
            if nxt[:1] == "." and (nxt[1:2].isdigit() or nxt == ".."):
                continue
            if nxt[:1] == ":" and match.start() > 0 and text[match.start() - 1].isdigit() \
                    and re.match(r":\s*\d", text[match.end():]):
                continue
            out.add("typo.space-before-punctuation", para, match.start(), match.end() + 1,
                    f"space before '{nxt[:1]}'", "remove the space")
        for match in FIG_ABBR_RE.finditer(text):
            if para.region == "references":
                continue
            out.add("callout.fig-abbreviation", para, match.start(), match.end() + 3,
                    f"'{match.group(0)}' abbreviation", "write 'Figure'" if not match.group(1) else "write 'Figures'")


def _check_citations(paragraphs, out: _Collector):
    for para in paragraphs:
        if para.is_bib:
            continue
        seen = []
        for regex in (PLACEHOLDER, REF_PLACEHOLDER, BRACE_PLACEHOLDER):
            for match in regex.finditer(para.text):
                if any(s <= match.start() < e or match.start() <= s < match.end() for s, e in seen):
                    continue
                seen.append((match.start(), match.end()))
                out.add("citation.placeholder", para, match.start(), match.end(),
                        f"unconverted citation placeholder {match.group(0)!r}",
                        "insert the EndNote citation (Cite While You Write) and update the bibliography")
        for match in ADJACENT_RE.finditer(para.text):
            groups = [g for g in match.groups() if g]
            merged = ", ".join(groups)
            out.add("citation.adjacent-groups", para, match.start(), match.end(),
                    f"separate adjacent citation groups {match.group(0)!r}",
                    f"cite them as one group ({merged}) by inserting both references in one EndNote citation")


# -- rules: spelling --------------------------------------------------------------------------

def _check_spelling(paragraphs, chosen: str | None, source: str, candidates: list, out: _Collector) -> dict:
    hits = []
    for para in paragraphs:
        if para.region in ("title", "references") or para.is_bib:
            continue
        text = para.masked
        for match in WORD_RE.finditer(text):
            word = match.group(0)
            entry = SPELLING.get(word.lower())
            if entry is None or (word.isupper() and len(word) > 1):
                continue
            if word[0].isupper():
                before = text[:match.start()].rstrip()
                if before and before[-1] not in ".!?:;\x00" and not para.is_heading:
                    continue            # a capitalised word mid-sentence is probably a proper name
            hits.append((para, match, entry))
    us = sum(1 for *_x, e in hits if e[1] == "us")
    uk = len(hits) - us

    def deviations(variety):
        return [(p, m, e) for p, m, e in hits
                if (e[1] == "us") != (variety in US_FORM_VARIETIES[e[0]])]

    if chosen:
        variety = chosen
    else:
        scored = [(len(deviations(v)), i, v) for i, v in enumerate(candidates)]
        variety = min(scored)[2] if scored else "en-US"
        source = "dominant"
    wrong = deviations(variety)
    summary = {"variety": variety, "source": source, "us_forms": us, "uk_forms": uk, "deviations": len(wrong),
               "candidates": candidates}
    if not hits:
        return summary
    origin = {"dominant": "the dominant variety", "--spelling": "the variety chosen with --spelling",
              "manuscript.json": "the variety set in manuscript.json"}.get(source, source)
    out.add("spelling.variety", None,
            message=f"spelling: {us} US-form and {uk} UK-form words; {variety} is {origin}; "
                    f"{len(wrong)} deviation(s)",
            suggestion="make every listed word follow one variety" if wrong else "")
    for para, match, (category, form, other) in wrong:
        word = match.group(0)
        suggestion = other[0].upper() + other[1:] if word[0].isupper() else other
        out.add("spelling.mixed", para, match.start(), match.end(),
                f"{'US' if form == 'us' else 'UK'} spelling '{word}' in an {variety} text ({origin}; "
                f"US forms {us}, UK forms {uk})", f"write '{suggestion}'")
    return summary


# -- rules: headings, formatting ----------------------------------------------------------

def _check_headings(paragraphs, out: _Collector):
    last: dict = {}
    current: tuple = ()
    for para in paragraphs:
        if not para.is_heading:
            continue
        text = para.text.replace(OBJECT_CHAR, "")
        if not text.strip(" \t\n\f\xa0\u202f\u2009"):
            out.add("heading.empty", para, 0, 0, f"empty Heading {para.level} paragraph",
                    "delete the empty heading (as a tracked change) or give it its text", snippet="")
            continue
        body = text.rstrip("\f\n")
        if body and (body[-1] == ":" or body[-1] in " \t\xa0\u202f\u2009"):
            names = {":": "colon", " ": "space", "\t": "tab", "\xa0": "non-breaking space",
                     "\u202f": "narrow non-breaking space", "\u2009": "thin space"}
            out.add("heading.trailing", para, max(0, len(body) - 60), len(body),
                    f"heading ends with a {names[body[-1]]}", "remove the trailing character")
        ppr = para.element.find("w:pPr")
        ind = ppr.find("w:ind") if ppr is not None else None
        if ind is not None:
            try:
                first_line = int(ind.get("w:firstLine", "0") or 0)
            except ValueError:
                first_line = 0
            if first_line > 0:
                out.add("heading.indent", para, 0, min(len(text), 60),
                        f"heading carries a direct first-line indent ({first_line} twips)",
                        "remove the indent so the heading matches its style")
        match = HEADING_NUMBER.match(text)
        if not match:
            continue
        numbers = tuple(int(n) for n in match.group(1).split("."))
        parent = numbers[:-1]
        if parent and current[:len(parent)] != parent:
            out.add("heading.numbering", para, 0, match.end(),
                    f"heading {match.group(1)} sits under section {'.'.join(map(str, current)) or '(none)'}",
                    f"renumber it under {'.'.join(map(str, current[:len(parent)])) or 'its section'}")
        expected = last.get(parent, 0) + 1
        if numbers[-1] != expected:
            wanted = ".".join(map(str, parent + (expected,)))
            problem = "repeats" if numbers[-1] < expected else "skips"
            out.add("heading.numbering", para, 0, match.end(),
                    f"heading number {match.group(1)} {problem} (expected {wanted})", f"number it {wanted}")
        last[parent] = numbers[-1]
        for key in [k for k in last if k[:len(numbers)] == numbers]:
            del last[key]
        current = numbers


def _check_formatting(package, paragraphs, profile, chosen_variety, spacing, out: _Collector):
    for para in paragraphs:
        pieces = [(s, e) for s, e, flags, _k, _l in para.spans if "highlight" in flags]
        if pieces:
            chars = sum(e - s for s, e in pieces)
            sample = " ".join(para.text[s:e] for s, e in pieces).strip()
            out.add("format.highlight", para, pieces[0][0], pieces[0][1],
                    f"highlighted text ({chars} characters) remains in the accepted view",
                    "remove the highlighting unless the journal asks for highlighted changes",
                    snippet=_snippet(sample, 0, min(len(sample), 60)))
    default_lang = None
    if "word/styles.xml" in package.parts:
        for element in package.xml("word/styles.xml").iter("w:rPrDefault"):
            rpr = element.find("w:rPr")
            lang = rpr.find("w:lang") if rpr is not None else None
            if lang is not None and lang.get("w:val"):
                default_lang = lang.get("w:val")
    chars: dict = {}
    where: dict = {}
    for para in paragraphs:
        for start, end, _flags, _kind, lang in para.spans:
            if not para.text[start:end].strip():
                continue
            key = lang or default_lang
            if not key:
                continue
            chars[key] = chars.get(key, 0) + (end - start)
            where.setdefault(key, [])
            if not where[key] or where[key][-1] is not para:
                where[key].append(para)
    if len(chars) > 1:
        dominant = max(chars, key=chars.get)
        for lang, count in sorted(chars.items(), key=lambda kv: -kv[1]):
            if lang == dominant:
                continue
            first = where[lang][0]
            span = next(s for s in first.spans if (s[4] or default_lang) == lang and first.text[s[0]:s[1]].strip())
            out.add("format.proofing-language", first, span[0], span[1],
                    f"proofing language {lang} on {count} characters in {len(where[lang])} paragraph(s); "
                    f"the dominant language is {dominant} ({chars[dominant]} characters)",
                    f"select the text and set the proofing language to {dominant} (Review > Language)")
    if chars and chosen_variety:
        dominant = max(chars, key=chars.get)
        if dominant.lower().startswith("en-") and dominant.lower() != chosen_variety.lower():
            out.add("format.proofing-variety", None, message=f"the dominant proofing language is {dominant} but the "
                    f"spelling variety is {chosen_variety}", suggestion=f"set the proofing language to "
                    f"{chosen_variety} so Word's speller agrees with the house spelling")
    wanted = str((profile.get("formatting") or {}).get("references_line_spacing") or "").lower()
    names, styles, default_line = spacing
    if wanted in ("double", "1.5", "single"):
        target = {"double": 480, "1.5": 360, "single": 240}[wanted]
        bib = [p for p in paragraphs if p.is_bib and p.text.strip()]
        for para in bib:
            line = _spacing_line(para.element.find("w:pPr"))
            style = para.style
            seen = set()
            while line is None and style and style not in seen:
                seen.add(style)
                based, style_line = styles.get(style, (None, None))
                line = style_line
                style = based
            if line is None:
                line = default_line or 240
            if abs(line - target) > 12:
                out.add("format.reference-spacing", para, 0, min(len(para.text), 60),
                        f"reference list line spacing is {_spacing_name(line)} (w:line={line}); the profile asks "
                        f"for {wanted}",
                        f"set the EndNote Bibliography style to {wanted} spacing")
                break


# -- rules: abbreviations and nomenclature --------------------------------------------------

def _abbr_core(token: str) -> str | None:
    core = token[:-1] if len(token) > 2 and token.endswith("s") and token[-2].isupper() else token
    alnum = re.sub(r"[^A-Za-z0-9]", "", core)
    letters = [c for c in alnum if c.isalpha()]
    if not 2 <= len(alnum) <= 6 or len(letters) < 2 or any(c.islower() for c in letters):
        return None
    if ROMAN.match(core):
        return None
    return core


def _abbr_tokens(para: Para):
    """(core, start, end) of all-caps abbreviation candidates in roman type."""
    text = para.masked
    for match in ABBR_TOKEN.finditer(text):
        core = _abbr_core(match.group(1))
        if core is None:
            continue
        if para.all_flag(match.start(1), match.end(1), "italic"):
            continue
        if core in REGION_CODES and text[:match.start(1)].endswith(", "):
            continue                    # "City, MA, USA" in a reagent address
        yield core, match.start(1), match.end(1)


def _definitions(para: Para, legends: bool):
    text = para.masked
    found = []
    for match in ABBR_DEF_PAREN.finditer(text):
        core = _abbr_core(match.group(1))
        before = text[:match.start()].rstrip()
        if core and before and before[-1].isalpha() and len(re.findall(r"[A-Za-z]{2,}", before[-40:])) >= 1:
            found.append((core, match.start(1)))
    for match in ABBR_DEF_REVERSE.finditer(text):
        core = _abbr_core(match.group(1))
        if core:
            found.append((core, match.start(1)))
    if legends:
        for match in ABBR_DEF_LIST.finditer(text):
            core = _abbr_core(match.group(1))
            if core:
                found.append((core, match.start(1)))
    return found


DECLARATION_ANY = re.compile("^(" + "|".join(DECLARATION_LABELS.values()) + ")$", re.I)


def _declaration_label(text: str) -> str:
    return re.sub(r"^\d+(\.\d+)*\.?\s*", "", text.strip()).rstrip(":. ").strip().lstrip("*").strip()


def _is_declaration(para: Para) -> bool:
    """Back-matter declarations (funders, grant numbers, author initials) are not prose to define."""
    if DECLARATION_ANY.match(_declaration_label(para.section)):
        return True
    head = para.text.strip()
    return ":" in head[:70] and bool(DECLARATION_ANY.match(_declaration_label(head.split(":", 1)[0])))


def _check_abbreviations(paragraphs, profile, rules, out: _Collector):
    universal = set(UNIVERSAL_ABBREVIATIONS) | set((profile.get("lint") or {}).get("universal_abbreviations") or [])
    in_table = {core for p in paragraphs if p.region == "front" or p.in_table for core, *_ in _abbr_tokens(p)}
    scope = [p for p in paragraphs if p.region in ("body", "legends", "appendix") and not p.is_heading
             and not p.is_bib and not p.in_table and p.text.strip() and not _is_declaration(p)]
    defs: dict = {}
    uses: dict = {}
    for para in scope:
        legend = para.region != "body"
        here = _definitions(para, legend)
        def_positions = {pos for _core, pos in here}
        for core, pos in here:
            defs.setdefault(core, []).append((para, pos, legend))
        placeholder_spans = [m.span() for regex in (PLACEHOLDER, REF_PLACEHOLDER, BRACE_PLACEHOLDER)
                             for m in regex.finditer(para.text)]
        for core, start, end in _abbr_tokens(para):
            if core in universal or start in def_positions:
                continue
            if any(a <= start < b for a, b in placeholder_spans):
                continue   # 'REF' in [REF: key] is a citation placeholder, not an abbreviation
            uses.setdefault(core, []).append((para, start, end, legend))
    for core in sorted(uses, key=lambda c: (uses[c][0][0].index, uses[c][0][1])):
        occurrences = uses[core]
        definitions = defs.get(core, [])
        first_para, start, end, _legend = occurrences[0]
        if not definitions:
            note = " (it appears only in the abbreviation table)" if core in in_table else ""
            out.add("abbrev.undefined", first_para, start, end,
                    f"{core} is used {len(occurrences)} time(s) but never defined as 'long form ({core})'{note}",
                    f"define it at first use, 'long form ({core})', or spell it out")
            continue
        body_uses = [u for u in occurrences if not u[3]]
        first_def = definitions[0]
        if body_uses and (body_uses[0][0].index, body_uses[0][1]) < (first_def[0].index, first_def[1]):
            para, start, end, _l = body_uses[0]
            out.add("abbrev.before-definition", para, start, end,
                    f"{core} is used before its definition in paragraph {first_def[0].index}",
                    f"move the definition 'long form ({core})' to this first use")
    for core, definitions in defs.items():
        body_defs = [d for d in definitions if not d[2]]
        if len(body_defs) > 1:
            para, pos, _l = body_defs[1]
            out.add("abbrev.redefined", para, pos, pos + len(core),
                    f"{core} is defined {len(body_defs)} times in the text (first in paragraph {body_defs[0][0].index})",
                    "keep the first definition and use the bare abbreviation afterwards")
        if body_defs:
            para, pos, _l = body_defs[0]
            later = [u for u in uses.get(core, []) if not u[3] and (u[0].index, u[1]) > (para.index, pos)]
            if len(later) <= 1 and core not in universal:
                out.add("abbrev.single-use", para, pos, pos + len(core),
                        f"{core} is defined but used {len(later)} time(s) afterwards",
                        "consider spelling it out instead of abbreviating")
    abstract_rules = rules.get("abstract") or {}
    forbid = {str(x).lower() for x in (abstract_rules.get("forbid") or [])}
    abstract = [p for p in paragraphs if p.region == "abstract" and not p.is_heading and p.text.strip()]
    if "abbreviations" in forbid:
        for para in abstract:
            for core, start, end in _abbr_tokens(para):
                out.add("abstract.abbreviation", para, start, end,
                        f"abbreviation {core} in the abstract; the profile forbids abbreviations there",
                        "spell it out in the abstract")
    if "citations" in forbid or "references" in forbid:
        for para in abstract:
            spans = [s for s in para.spans if s[3] == "EN.CITE"] or [s for s in para.spans if s[3].startswith("ADDIN")]
            placeholders = [m for m in PLACEHOLDER.finditer(para.text)]
            if spans or placeholders:
                start, end = (spans[0][0], spans[0][1]) if spans else placeholders[0].span()
                out.add("abstract.citation", para, start, end,
                        "citation in the abstract; the profile forbids citations there",
                        "remove the citation from the abstract (describe the source in words if needed)")


def _check_gene_italics(paragraphs, profile, out: _Collector):
    universal = set(UNIVERSAL_ABBREVIATIONS) | set((profile.get("lint") or {}).get("universal_abbreviations") or [])
    for para in paragraphs:
        if para.region in ("title", "references") or para.is_bib or not para.text.strip():
            continue
        text = para.masked
        for match in re.finditer(r"(?<![\w\-])([A-Z][A-Z0-9]{1,9})(?![\w])", text):
            token = match.group(1)
            if token in universal or ROMAN.match(token) or sum(c.isalpha() for c in token) < 2:
                continue
            if "italic" in para.flags_at(match.start(1)):
                continue
            if GENE_CUE_AFTER.match(text[match.end():match.end() + 30]) or \
                    GENE_CUE_BEFORE.search(text[max(0, match.start() - 40):match.start()]):
                out.add("nomenclature.gene-italic", para, match.start(), match.end() + 12,
                        f"{token} looks like a gene symbol in roman type next to a gene-context word",
                        f"if {token} names a gene or transcript, set it in italics (proteins stay roman)")


# -- rules: figures ---------------------------------------------------------------------------

def _callout_numbers(group: str) -> list[int]:
    numbers = []
    pending_range = False
    for number, sep in re.findall(r"(\d+)[A-Za-z]*|(,|and|&|\u2013|\u2014|-|to)", group):
        if number:
            value = int(number)
            if pending_range and numbers and value > numbers[-1] and value - numbers[-1] < 50:
                numbers.extend(range(numbers[-1] + 1, value + 1))
            else:
                numbers.append(value)
            pending_range = False
        elif sep in ("\u2013", "\u2014", "-", "to"):
            pending_range = True
    return numbers


def _check_figures(paragraphs, rules, out: _Collector) -> dict:
    captions: dict = {}
    caption_paras = set()
    for para in paragraphs:
        match = CAPTION_RE.match(para.text)
        style = para.style.lower()
        if match and (para.region == "legends" or para.is_heading or "caption" in style):
            captions.setdefault(int(match.group(1)), para)
            caption_paras.add(para.index)
    first: dict = {}
    order = []
    for para in paragraphs:
        if para.region not in ("abstract", "body") or para.index in caption_paras or para.is_bib:
            continue
        text = para.text
        for match in CALLOUT_RE.finditer(text):
            if match.group(1):
                continue
            for number in _callout_numbers(match.group(3)):
                if number not in first:
                    first[number] = (para, match.start(), match.end())
                    order.append(number)
    highest = 0
    for number in order:
        para, start, end = first[number]
        if number < highest:
            out.add("figures.order", para, start, end,
                    f"Figure {number} is first cited after Figure {highest}",
                    "renumber the figures in the order they are first cited")
        highest = max(highest, number)
    for number, para in sorted(captions.items()):
        if number not in first:
            out.add("figures.uncited", para, 0, min(len(para.text), 60),
                    f"Figure {number} has a legend but is never cited in the text",
                    f"cite Figure {number} in the Results (or remove the figure)")
    if captions:
        for number in sorted(first):
            if number not in captions:
                para, start, end = first[number]
                out.add("figures.missing-legend", para, start, end,
                        f"Figure {number} is cited but has no legend", f"add a 'Figure {number}:' legend")
    elif first:
        for number in range(1, max(first)):
            if number not in first:
                para, start, end = first[max(first)]
                out.add("figures.gap", para, start, end, f"Figure {number} is never cited (numbers skip it)",
                        "cite every figure, numbered in order of first citation")
    count = len(captions) or (max(first) if first else 0)
    recommended = (rules.get("figures") or {}).get("recommended_max")
    if recommended and count > int(recommended):
        out.add("limits.figures", None, message=f"{count} figures; the profile recommends at most {recommended}",
                suggestion="move figures to the supplementary material or combine panels")
    return {"figures": count, "cited": sorted(first), "legends": sorted(captions)}


# -- rules: journal limits and declarations ----------------------------------------------

def _local_word_count(paragraphs) -> dict:
    def words(text):
        return len(re.findall(r"\S+", text.replace(OBJECT_CHAR, " "), re.ASCII))
    abstract_head = next((p for p in paragraphs if p.level == 1 and ABSTRACT_HEAD.match(p.text)), None)
    start = abstract_head.index if abstract_head else -1
    intro = next((p for p in paragraphs if p.level == 1 and p.index > start and
                  re.match(r"^\s*(?:\d+\.?\s*)?introduction\b", p.text, re.I)), None)
    if intro is None:
        raise ValueError("no '1. Introduction' Heading 1 after the Abstract")
    refs = next((p for p in paragraphs if p.level == 1 and p.index > intro.index and REFERENCES_HEAD.match(p.text)),
                None)
    end = refs.index if refs else len(paragraphs)
    abstract = sum(words(p.text) for p in paragraphs if p.region == "abstract" and not p.is_heading
                   and p.index < intro.index)
    body = sum(words(p.text) for p in paragraphs[intro.index:end] if not WORD_COUNT_LINE.match(p.text))
    return {"words": abstract + body, "abstract": abstract, "method": "house (lint local count)"}


def _word_count(package, paragraphs, config) -> dict:
    method = (config.get("word_count") or {}).get("method") or "house"
    try:
        from . import wordcount
    except ImportError:
        wordcount = None
    if wordcount is not None and hasattr(wordcount, "count_package"):
        try:
            result = wordcount.count_package(package)
            chosen = result.get(method) or result["house"]
            stated = result.get("stated") or {}
            return {"words": int(chosen["total"]), "abstract": result.get("abstract"),
                    "method": f"{method if method in result else 'house'} (mswlib.wordcount)",
                    "stated": stated.get("value")}
        except (KeyError, TypeError, AttributeError):
            pass
        except ValueError as error:          # wordcount.WordCountError: a boundary is missing
            return {"words": None, "method": "house (mswlib.wordcount)", "error": str(error)}
    try:
        return _local_word_count(paragraphs)
    except ValueError as error:
        return {"words": None, "method": "house (lint local count)", "error": str(error)}


def _check_limits(package, paragraphs, rules, config, doc, out: _Collector, title_page: bool = False) -> dict:
    measures: dict = {}
    title_region = [p for p in paragraphs if p.region == "title" and p.text.strip()]
    if title_page:
        title = title_region[0] if title_region else None     # a title-page file starts with the title
    else:
        title = next((p for p in title_region if "title" == (p.style or "").lower()), None) or \
            next((p for p in title_region if not LABELS.match(p.text)), None)
    title_max = (rules.get("title") or {}).get("max_chars")
    where = "this title page" if title_page else "the title page (or lint the separate title-page file)"
    if title is not None:
        length = len(title.text.strip())
        measures["title_chars"] = length
        if title_max and length > int(title_max):
            out.add("limits.title", title, 0, len(title.text),
                    f"title has {length} characters; the profile allows {title_max}",
                    f"shorten the title by {length - int(title_max)} characters")
    elif title_max:
        out.add("limits.missing", None, message="no title paragraph found" if title_page else
                "no title paragraph found before the first heading",
                suggestion="put the title first" if title_page else
                "lint the separate title-page file too; the title limit is checked there")
    running = next(((p, RUNNING_TITLE.match(p.text)) for p in paragraphs if RUNNING_TITLE.match(p.text)), None)
    short = rules.get("short_title") or {}
    if running:
        para, match = running
        value = match.group(1).strip()
        length = len(value) if short.get("counts_spaces", True) else len(value.replace(" ", ""))
        measures["running_title_chars"] = length
        if short.get("max_chars") and length > int(short["max_chars"]):
            out.add("limits.running-title", para, match.start(1), match.end(1),
                    f"running title has {length} characters; the profile allows {short['max_chars']}",
                    f"shorten it by {length - int(short['max_chars'])} characters")
    elif short.get("max_chars"):
        out.add("limits.missing", None, message="no 'Running title:' line found",
                suggestion=f"add 'Running title: ...' to {where}")
    keywords_line = next(((p, KEYWORDS_LINE.match(p.text)) for p in paragraphs if KEYWORDS_LINE.match(p.text)), None)
    keyword_rules = rules.get("keywords") or {}
    if keywords_line:
        para, match = keywords_line
        items = [k.strip() for k in re.split(r"[,;]", match.group(1).strip().rstrip(".")) if k.strip()]
        measures["keywords"] = len(items)
        low, high = keyword_rules.get("min"), keyword_rules.get("max")
        if low and len(items) < int(low):
            out.add("limits.keywords", para, 0, len(para.text), f"{len(items)} keywords; the profile needs at least {low}",
                    f"add {int(low) - len(items)} keyword(s)")
        if high and len(items) > int(high):
            out.add("limits.keywords", para, 0, len(para.text), f"{len(items)} keywords; the profile allows {high}",
                    f"remove {len(items) - int(high)} keyword(s)")
    elif keyword_rules.get("min"):
        out.add("limits.missing", None, message="no 'Keywords:' line found",
                suggestion=f"add 'Keywords: ...' to {where}")
    if title_page:
        # The body lives in the main file: its lint computes the word count and compares this line.
        stated_line = next((p for p in paragraphs if WORD_COUNT_LINE.match(p.text)), None)
        if stated_line is not None:
            match = WORD_COUNT_LINE.match(stated_line.text)
            measures["stated_word_count"] = int(re.sub(r"\D", "", match.group(1))) if match.group(1) else None
        return measures
    abstract_rules = rules.get("abstract") or {}
    abstract = [p for p in paragraphs if p.region == "abstract" and not p.is_heading and p.text.strip()]
    if abstract:
        count = sum(len(re.findall(r"\S+", p.text.replace(OBJECT_CHAR, " "), re.ASCII)) for p in abstract)
        measures["abstract_words"] = count
        limit = abstract_rules.get("max_words")
        if limit and count > int(limit):
            out.add("limits.abstract-words", abstract[0], 0, 60,
                    f"abstract has {count} words; the profile allows {limit}",
                    f"cut {count - int(limit)} words")
        if abstract_rules.get("format") == "single_paragraph" and len(abstract) > 1:
            out.add("abstract.format", abstract[1], 0, 60,
                    f"abstract has {len(abstract)} paragraphs; the profile asks for one",
                    "merge the abstract into one unstructured paragraph")
    counted = _word_count(package, paragraphs, config)
    measures["word_count"] = counted
    limit = rules.get("word_limit") or {}
    if counted.get("words") is not None:
        maximum = limit.get("max")
        severity = str(limit.get("severity") or "warn").lower()
        severity = severity if severity in SEVERITIES else ("error" if severity in ("hard", "fail") else "warn")
        if maximum and counted["words"] > int(maximum):
            out.add("limits.word-count", None, severity=severity,
                    message=f"{counted['words']:,} words by the {counted['method']} method; the profile limit is "
                            f"{int(maximum):,} (excluding {', '.join(limit.get('excludes') or []) or 'nothing'})",
                    suggestion=f"cut {counted['words'] - int(maximum):,} words")
        stated_line = next((p for p in paragraphs if WORD_COUNT_LINE.match(p.text)), None)
        if stated_line is not None:
            match = WORD_COUNT_LINE.match(stated_line.text)
            stated = int(re.sub(r"\D", "", match.group(1))) if match.group(1) else None
            measures["stated_word_count"] = stated
            if stated != counted["words"]:
                out.add("limits.word-count-stated", stated_line, 0, len(stated_line.text),
                        f"title page states {stated if stated is not None else 'no number'}; the {counted['method']} "
                        f"count is {counted['words']:,}",
                        "update the number with 'msw.py wordcount --stamp-spec' and a tracked build")
    elif limit.get("max"):
        out.add("limits.missing", None, message=f"word count not computed: {counted.get('error')}",
                suggestion="give the document 'Abstract', '1. Introduction' and 'References' Heading 1 paragraphs")
    entries = bibliography_entries(doc)
    references = len(entries) or census(doc)["distinct_records"]
    measures["references"] = references
    recommended = (rules.get("references") or {}).get("recommended_max")
    if recommended and references > int(recommended):
        first = entries[0]["paragraph"] if entries else None
        out.add("limits.references", paragraphs[first] if first is not None else None, 0, 40,
                message=f"{references} references; the profile recommends at most {recommended}",
                suggestion="trim references that do not support a specific claim")
    return measures


def _check_declarations(paragraphs, profile, out: _Collector) -> dict:
    order = [str(k) for k in ((profile.get("declarations") or {}).get("order") or [])]
    if not order:
        return {}
    candidates = [p for p in paragraphs if p.region in ("body", "appendix", "front") and p.text.strip()
                  and not p.in_table]
    found: dict = {}
    for key in order:
        pattern = DECLARATION_LABELS.get(key, re.escape(key.replace("_", " ")))
        regex = re.compile(rf"^{pattern}$", re.I)
        for para in candidates:
            text = para.text.strip()
            if para.is_heading:
                label = re.sub(r"^\d+(\.\d+)*\.?\s*", "", text).rstrip(":. ").strip()
            elif ":" in text[:70]:
                label = text.split(":", 1)[0].strip().lstrip("*").strip()
            else:
                continue
            if regex.match(label):
                found[key] = para
                break
    for key in order:
        if key not in found:
            out.add("declarations.missing", None,
                    message=f"no '{key.replace('_', ' ')}' heading or bold-label line was found",
                    suggestion="add it in the profile's declaration order: " + ", ".join(order))
    present = [k for k in order if k in found]
    actual = sorted(present, key=lambda k: found[k].index)
    if actual != present:
        wrong = next(k for k, a in zip(present, actual) if k != a)
        para = found[actual[present.index(wrong)]] if wrong in present else None
        out.add("declarations.order", para, 0, min(len(para.text), 60) if para else 0,
                "declarations appear as " + ", ".join(actual) + "; the profile order is " + ", ".join(present),
                "reorder the declaration blocks to the profile order")
    return {key: found[key].index for key in present}


# -- entry points -------------------------------------------------------------------------------

@dataclass
class LintReport:
    file: str | None
    sha256: str
    profile: dict
    article_type: str
    spelling: dict
    measures: dict
    findings: list
    scope: str = "manuscript"       # or "title_page": a separate title-page file, body checks skipped

    @property
    def counts(self) -> dict:
        out = {s: 0 for s in SEVERITIES}
        for finding in self.findings:
            out[finding.severity] = out.get(finding.severity, 0) + 1
        return out

    @property
    def errors(self) -> list:
        return [f for f in self.findings if f.severity == "error"]

    def rules(self) -> set:
        return {f.rule for f in self.findings}

    def to_dict(self) -> dict:
        journal = self.profile.get("journal") or {}
        return {"schema": "msw-lint/1", "file": self.file, "sha256": self.sha256, "view": "accepted",
                "scope": self.scope,
                "profile": {"id": journal.get("id"), "name": journal.get("name"),
                            "sources": journal.get("sources") or [], "path": self.profile.get("_path")},
                "article_type": self.article_type, "spelling": self.spelling, "measures": self.measures,
                "summary": self.counts, "findings": [f.to_dict() for f in self.findings]}

    def scope_text(self) -> str:
        if self.scope == "title_page":
            return ("separate title page: title, running-title and keyword limits checked; declarations, "
                    "figures, abbreviations, word count and references are checked on the main file")
        return "manuscript"

    def markdown(self) -> str:
        journal = self.profile.get("journal") or {}
        counts = self.counts
        lines = [f"# Lint report: {Path(self.file).name if self.file else 'document'}", "",
                 f"- File: `{self.file}`" if self.file else "- File: (in memory)",
                 f"- SHA-256: `{self.sha256}`",
                 "- View: accepted (tracked deletions ignored, insertions checked)",
                 f"- Scope: {self.scope_text()}",
                 f"- Profile: {journal.get('name') or journal.get('id') or 'house defaults'}",
                 f"- Spelling: {self.spelling.get('variety')} ({self.spelling.get('source')}); "
                 f"US forms {self.spelling.get('us_forms', 0)}, UK forms {self.spelling.get('uk_forms', 0)}",
                 f"- Findings: {counts['error']} error(s), {counts['warn']} warning(s), {counts['info']} note(s)"]
        for source in journal.get("sources") or []:
            lines.append(f"- Profile source: {source.get('url')} (retrieved {source.get('retrieved')}, "
                         f"{source.get('method')})")
        lines.append("")
        wc = self.measures.get("word_count") or {}
        lines += ["## Measures", "", "| Measure | Value |", "| --- | --- |"]
        for key in ("title_chars", "running_title_chars", "keywords", "abstract_words", "stated_word_count",
                    "references", "figures"):
            if key in self.measures:
                lines.append(f"| {key.replace('_', ' ')} | {self.measures[key]} |")
        if wc:
            value = wc.get("words") if wc.get("words") is not None else f"not computed ({wc.get('error')})"
            lines.append(f"| word count ({wc.get('method')}) | {value} |")
        lines.append("")
        for severity in SEVERITIES:
            items = [f for f in self.findings if f.severity == severity]
            if not items:
                continue
            lines += [f"## {severity.capitalize()} ({len(items)})", "",
                      "| Rule | Para | paraId | Snippet | Message | Suggestion |", "| --- | --- | --- | --- | --- | --- |"]
            for f in items:
                snippet = f.snippet.replace("`", "'").replace("|", "/")
                cells = [f.rule, "" if f.paragraph is None else str(f.paragraph), f.para_id or "",
                         f"`{snippet}`" if snippet.strip() else "", _cell(f.message), _cell(f.suggestion)]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def lint(source, *, profile=None, spelling: str | None = None, article_type: str = "research",
         config: dict | None = None) -> LintReport:
    """Lint a .docx (path, bytes or Package) against a journal profile (dict or path); returns a LintReport."""
    package = source if isinstance(source, Package) else Package(source)
    config = config or {}
    if isinstance(profile, (str, Path)):
        path = Path(profile)
        profile = load_profile(path)
        profile["_path"] = str(path.resolve())
    elif profile is None:
        profile = load_profile(config=config)
    rules = article_rules(profile, article_type)
    names, styles, default_line = _style_info(package)
    doc = view_doc(package.document, "accept")
    paragraphs = _build_paragraphs(doc, names)
    out = _Collector()
    chosen, source_label = None, "dominant"
    if spelling:
        chosen, source_label = _normalise_variety(spelling), "--spelling"
    elif (config.get("language") or {}).get("spelling"):
        chosen, source_label = _normalise_variety(config["language"]["spelling"]), "manuscript.json"
    candidates = [v for v in (_normalise_variety(x) for x in (profile.get("formatting") or {}).get("language") or [])
                  if v] or ["en-US", "en-GB"]
    _check_statistics(paragraphs, profile.get("statistics") or {}, out)
    _check_typography(paragraphs, out)
    _check_citations(paragraphs, out)
    spelling_summary = _check_spelling(paragraphs, chosen, source_label, candidates, out)
    _check_headings(paragraphs, out)
    measured_variety = spelling_summary.get("variety") if (
        chosen or spelling_summary.get("us_forms") or spelling_summary.get("uk_forms")) else None
    _check_formatting(package, paragraphs, profile, measured_variety, (names, styles, default_line), out)
    title_page = is_title_page_only(paragraphs)
    if not title_page:      # a separate title-page file has no body: no abbreviations table, call-outs or declarations
        _check_abbreviations(paragraphs, profile, rules, out)
        _check_gene_italics(paragraphs, profile, out)
    measures = _check_limits(package, paragraphs, rules, config, doc, out, title_page=title_page)
    if not title_page:
        measures.update(_check_figures(paragraphs, rules, out))
        measures["declarations"] = _check_declarations(paragraphs, profile, out)
    disabled = [str(d) for d in ((profile.get("lint") or {}).get("disabled_rules") or [])]
    findings = [f for f in out.findings
                if not any(f.rule == d or f.rule.startswith(d.rstrip(".") + ".") for d in disabled)]
    order = {s: i for i, s in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: (f.paragraph if f.paragraph is not None else -1, order[f.severity], f.rule))
    return LintReport(str(package.path) if package.path else None, package.sha256, profile, article_type,
                      spelling_summary, measures, findings, "title_page" if title_page else "manuscript")


def _normalise_variety(value) -> str | None:
    text = str(value or "").strip().replace("_", "-").lower()
    return {"en-us": "en-US", "en-gb": "en-GB", "en-ca": "en-CA", "us": "en-US", "uk": "en-GB",
            "gb": "en-GB", "ca": "en-CA"}.get(text)


# -- command line -------------------------------------------------------------------------------

def _ascii(text: str) -> str:
    return text.encode("ascii", "backslashreplace").decode("ascii")


def cmd_lint(args) -> int:
    for target in (args.json, args.markdown):
        if target and os.path.exists(pathutil.fs(target)):
            print(f"ERROR: {target} already exists; choose a new file", file=sys.stderr)
            return 2
    try:
        config = load_config(Path(args.docx).resolve().parent)
        profile = load_profile(args.profile, config=config)
        if args.profile:
            profile["_path"] = str(Path(args.profile).resolve())
        report = lint(args.docx, profile=profile, spelling=args.spelling, article_type=args.article_type,
                      config=config)
    except (OSError, ValueError) as error:
        advice = pathutil.explain(error) if isinstance(error, OSError) else None
        print(_ascii(f"ERROR: {advice or error}"), file=sys.stderr)
        return 2
    counts = report.counts
    journal = report.profile.get("journal") or {}
    print(_ascii(f"lint: {args.docx}"))
    print(_ascii(f"  profile: {journal.get('name') or journal.get('id')}; spelling: "
                 f"{report.spelling.get('variety')} ({report.spelling.get('source')}); accepted view"))
    if report.scope != "manuscript":
        print(_ascii(f"  scope: {report.scope_text()}"))
    shown = 0
    for finding in report.findings:
        if finding.severity == "info" and not args.verbose:
            continue
        if args.limit and shown >= args.limit:
            print(f"  ... {len(report.findings) - shown} more (see --json/--markdown)")
            break
        where = f"p{finding.paragraph}" + (f" {finding.para_id}" if finding.para_id else "") \
            if finding.paragraph is not None else "document"
        line = f"  [{finding.severity.upper()}] {finding.rule} ({where}): {finding.message}"
        if finding.snippet:
            line += f" | {finding.snippet}"
        if finding.suggestion:
            line += f" -> {finding.suggestion}"
        print(_ascii(line))
        shown += 1
    print(f"SUMMARY: {counts['error']} error(s), {counts['warn']} warning(s), {counts['info']} note(s)")
    if args.json:
        write_json_exclusive(args.json, report.to_dict())
        print(_ascii(f"json: {args.json}"))
    if args.markdown:
        path = Path(args.markdown)
        os.makedirs(pathutil.fs(path.parent), exist_ok=True)
        with open(pathutil.fs(path), "x", encoding="utf-8", newline="\n") as handle:
            handle.write(report.markdown())
        print(_ascii(f"markdown: {args.markdown}"))
    return 1 if counts["error"] else 0


def register(subparsers):
    p = subparsers.add_parser("lint", help="house-style and journal-profile checks on the accepted view "
                              "(exit 1 when any error is found)")
    p.add_argument("docx")
    p.add_argument("--profile", help="journal profile JSON (default: the project's, else house defaults)")
    p.add_argument("--spelling", choices=VARIETIES,
                   help="spelling variety to enforce (default: manuscript.json language.spelling, else the "
                        "dominant variety among the profile's languages)")
    p.add_argument("--article-type", default="research", help="profile article type (default: research)")
    p.add_argument("--json", metavar="OUT", help="write the full report as JSON (new file)")
    p.add_argument("--markdown", metavar="OUT", help="write the report as Markdown (new file)")
    p.add_argument("--verbose", action="store_true", help="also print info-level notes")
    p.add_argument("--limit", type=int, default=200, help="print at most this many findings (default 200; 0 = all)")
    p.set_defaults(func=cmd_lint)

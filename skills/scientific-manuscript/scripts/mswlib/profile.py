"""Journal profiles: machine-readable summaries of a journal's author guidelines.

A profile is JSON (schema "journal-profile/1"). Missing keys fall back to the
house defaults below, which follow the author's own manuscript template.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

HOUSE_DEFAULTS = {
    "schema": "journal-profile/1",
    "journal": {"id": "house", "name": "House style (no journal selected)", "sources": [], "conflicts": []},
    "article_types": {
        "research": {
            "word_limit": {"max": None, "excludes": ["references", "figure_legends"], "severity": "warn"},
            "abstract": {"max_words": 250, "format": "single_paragraph", "forbid": ["citations"],
                         "required_elements": []},
            "title": {"max_chars": None},
            "short_title": {"max_chars": None},
            "keywords": {"min": 4, "max": None},
            "figures": {"recommended_max": None},
            "references": {"recommended_max": None},
        }
    },
    "title_page": {"separate_file": False, "items": ["full_title", "authors", "affiliations",
                                                     "corresponding_author", "keywords", "word_count",
                                                     "short_title"]},
    "declarations": {"order": ["author_contributions", "data_availability", "acknowledgements",
                               "declaration_of_interest", "funding", "ai_disclosure"], "templates": {}},
    "formatting": {"page": "letter", "font": "Times New Roman", "font_size_pt": 11, "line_spacing": "double",
                   "references_line_spacing": "single", "line_numbers": "continuous",
                   "language": ["en-US", "en-GB"]},
    "references": {"system": "numbered", "order": "citation", "et_al_after_authors": 6,
                   "endnote_style": "Vancouver"},
    "statistics": {"p_style": "P = 0.012", "p_italic": True, "require_threshold_statement": True,
                   "outlier_removal": "discouraged_justify"},
    "figures": {"panel_labels": {"case": "upper", "position": "top_left"}, "max_width_mm": 180,
                "font": {"family": ["Arial", "Helvetica"], "min_pt": 5}, "files": {}},
    "tables": {"separate_editable_files": True, "internal_rules": "forbidden"},
    "supplementary": {"cite_as": ["Supplementary Table {n}", "Supplementary Figure {n}"]},
    "revision": {"markup": "track_changes", "response_letter": "required"},
    "policies": {},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if value is None and key in out:
            continue   # a blank (null) entry keeps the house default
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_profile(path=None, *, config: dict | None = None) -> dict:
    """Load a profile by path, or the one named by manuscript.json; fall back to house defaults."""
    if path is None and config and config.get("journal_profile"):
        candidate = Path(config["_root"]) / config["journal_profile"] if config.get("_root") else \
            Path(config["journal_profile"])
        path = candidate if candidate.is_file() else None
    if path is None:
        return copy.deepcopy(HOUSE_DEFAULTS)
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if data.get("schema") != "journal-profile/1":
        raise ValueError(f"{path}: not a journal-profile/1 file")
    return _merge(HOUSE_DEFAULTS, data)


def article_rules(profile: dict, article_type: str = "research") -> dict:
    types = profile.get("article_types", {})
    return types.get(article_type) or types.get("research") or {}

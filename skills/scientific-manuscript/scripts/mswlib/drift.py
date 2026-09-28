"""Drift between two versions: what the accepted text gained or lost since an approved version.

The per-pass gate compares a build with its own source. `drift` answers the question asked before
a submission: what changed since the version the author, a committee or a journal last approved,
however many versions lie between them. It compares the two accepted views paragraph by
paragraph and reports the changed paragraphs, the numbers, statistics terms, symbols and
direction words gained or lost, and the citation and picture counts.
"""

from __future__ import annotations

import difflib
from collections import Counter

from .config import record_path
from .docx import Package
from .endnote import citation_fields
from .gate import science_delta
from .textdiff import AtomError, diff
from .views import view_doc
from .wordml import iter_paragraphs, paragraph_text

CONTEXT = 40


def _side(package: Package) -> tuple[dict, list]:
    doc = view_doc(package.document, "accept")
    paragraphs = [(p.get("w14:paraId"), paragraph_text(doc, p)) for p in iter_paragraphs(doc)]
    info = {"path": record_path(package.path.resolve()) if package.path else None, "sha256": package.sha256,
            "paragraphs": len(paragraphs), "citations": len(citation_fields(doc)),
            "pictures": sum(1 for _ in doc.iter("w:drawing"))}
    return info, paragraphs


def _terms(old: str, new: str) -> tuple[Counter, Counter, tuple | None]:
    return science_delta(old, new)


def _spans(old: str, new: str) -> list[dict] | None:
    """Word-level changes of one paragraph, located by the text before them in the newer version."""
    try:
        changes = diff(old, new, spaces="exact")
    except AtomError:
        return None
    return [{"before": new[max(0, c.new_start - CONTEXT):c.new_start], "old": old[c.old_start:c.old_end],
             "new": new[c.new_start:c.new_end]} for c in changes]


def _similarity(x: str, y: str) -> float:
    matcher = difflib.SequenceMatcher(None, x, y, autojunk=False)
    return matcher.ratio() if matcher.real_quick_ratio() >= 0.5 and matcher.quick_ratio() >= 0.5 else 0.0


def _align(a: list, b: list, olds: range, news: range) -> list[tuple[list, list]]:
    """Pair each old paragraph of a changed block with the most similar later new one (in order);
    paragraphs left over are reported as removed or added."""
    blocks = []
    start = news.start
    for i in olds:
        scores = [(_similarity(a[i][1], b[j][1]), j) for j in range(start, news.stop)]
        best = max(scores, default=(0.0, None))
        if best[0] < 0.5:
            blocks.append(([i], []))
            continue
        blocks.extend(([], [j]) for j in range(start, best[1]))
        blocks.append(([i], [best[1]]))
        start = best[1] + 1
    blocks.extend(([], [j]) for j in range(start, news.stop))
    return blocks


def drift(old, new) -> dict:
    """Changes in the accepted text from `old` (the approved version) to `new` (the file to send)."""
    old_package = old if isinstance(old, Package) else Package(old)
    new_package = new if isinstance(new, Package) else Package(new)
    old_info, a = _side(old_package)
    new_info, b = _side(new_package)
    matcher = difflib.SequenceMatcher(None, [t for _, t in a], [t for _, t in b], autojunk=False)
    changes = []
    removed_all, added_all = Counter(), Counter()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for olds, news in _align(a, b, range(i1, i2), range(j1, j2)):
            old_text = "\n".join(a[i][1] for i in olds)
            new_text = "\n".join(b[j][1] for j in news)
            removed, added, moved = _terms(old_text, new_text)
            removed_all += removed
            added_all += added
            kind = "changed" if olds and news else ("removed" if olds else "added")
            entry = {"kind": kind, "old_paragraphs": [i + 1 for i in olds], "new_paragraphs": [j + 1 for j in news],
                     "paraId": b[news[0]][0] if len(news) == 1 else None, "old": old_text, "new": new_text,
                     "terms_removed": sorted(removed.elements()), "terms_added": sorted(added.elements()),
                     "terms_reordered": {"old": moved[0], "new": moved[1]} if moved else None}
            if len(olds) == 1 and len(news) == 1:
                entry["spans"] = _spans(old_text, new_text)
            changes.append(entry)
    return {"schema": "msw-drift/1", "old": old_info, "new": new_info, "changed": len(changes),
            "changes": changes, "terms_removed": sorted(removed_all.elements()),
            "terms_added": sorted(added_all.elements())}


def _preview(text: str, limit: int = 160) -> str:
    text = text.replace("\n", " / ")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def report_text(result: dict) -> str:
    old, new = result["old"], result["new"]
    lines = [f"drift: {old['path'] or 'old'} -> {new['path'] or 'new'}",
             f"paragraphs {old['paragraphs']} -> {new['paragraphs']}; citations {old['citations']} -> "
             f"{new['citations']}; pictures {old['pictures']} -> {new['pictures']}",
             f"{result['changed']} change(s) in the accepted text"]
    for number, change in enumerate(result["changes"], 1):
        where = (f"paragraph {','.join(map(str, change['old_paragraphs'])) or '-'} -> "
                 f"{','.join(map(str, change['new_paragraphs'])) or '-'}")
        pid = f" (paraId {change['paraId']})" if change["paraId"] else ""
        lines.append(f"[{number}] {change['kind']} {where}{pid}")
        if change["old"]:
            lines.append(f"    - {_preview(change['old'])}")
        if change["new"]:
            lines.append(f"    + {_preview(change['new'])}")
        if change["terms_removed"] or change["terms_added"]:
            lines.append(f"    terms removed {change['terms_removed'][:12]} added {change['terms_added'][:12]}")
        elif change["terms_reordered"]:
            lines.append(f"    same terms in a new order {change['terms_reordered']['old'][:12]} -> "
                         f"{change['terms_reordered']['new'][:12]}")
    if result["terms_removed"] or result["terms_added"]:
        lines.append(f"numbers, statistics, symbols and direction words removed {result['terms_removed'][:40]}")
        lines.append(f"numbers, statistics, symbols and direction words added {result['terms_added'][:40]}")
    if (old["citations"], old["pictures"]) != (new["citations"], new["pictures"]):
        lines.append("citation or picture counts changed: confirm each against DECISIONS.md")
    return "\n".join(lines)

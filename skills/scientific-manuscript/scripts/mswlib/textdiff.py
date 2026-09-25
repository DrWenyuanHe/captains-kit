"""Minimal word-level differences between an old and a new paragraph text.

Text given to a rewriting agent uses two kinds of inline markup:
  ⟦F1⟧, ⟦D2⟧ ...   atoms (fields such as citations, pictures, links) that must be
                   kept byte for byte and in order;
  ⟨i⟩…⟨/i⟩, ⟨b⟩, ⟨sup⟩, ⟨sub⟩, ⟨u⟩   character formatting.
Differences are computed per stretch between atoms, so an edit can never move,
delete or duplicate a citation.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

ATOM = re.compile(r"⟦[A-Z]\d+⟧")
FORMAT_TAG = re.compile(r"⟨(/?)(i|b|sup|sub|u)⟩")
TOKEN = re.compile(r"⟦[A-Z]\d+⟧|\w+|[ \t   ]+|.", re.S)
MARKUP_FLAGS = {"i": "italic", "b": "bold", "sup": "superscript", "sub": "subscript", "u": "underline"}
FLAG_MARKUP = {v: k for k, v in MARKUP_FLAGS.items()}
_SPACE_CLASS = str.maketrans({" ": " ", " ": " ", " ": " "})


class AtomError(ValueError):
    """The rewrite changed, moved or dropped an atom (citation, picture, link)."""


@dataclass
class Change:
    old_start: int
    old_end: int
    new_start: int
    new_end: int


def parse_markup(text: str) -> tuple[str, list]:
    """Strip formatting tags. Returns (text, per-character frozenset of flags)."""
    out, flags = [], []
    active: list[str] = []
    pos = 0
    for match in FORMAT_TAG.finditer(text):
        chunk = text[pos:match.start()]
        current = frozenset(MARKUP_FLAGS[t] for t in active)
        out.append(chunk)
        flags.extend([current] * len(chunk))
        closing, name = match.group(1), match.group(2)
        if closing:
            if name in active:
                active.remove(name)
        else:
            active.append(name)
        pos = match.end()
    chunk = text[pos:]
    out.append(chunk)
    flags.extend([frozenset(MARKUP_FLAGS[t] for t in active)] * len(chunk))
    return "".join(out), flags


def render_markup(text: str, flags: list) -> str:
    """Inverse of parse_markup for display (atoms pass through unchanged)."""
    out = []
    current: frozenset = frozenset()
    order = ["bold", "italic", "underline", "superscript", "subscript"]
    for char, char_flags in zip(text, flags):
        char_flags = frozenset(f for f in char_flags if f in FLAG_MARKUP)
        if char_flags != current:
            for flag in reversed(order):
                if flag in current and flag not in char_flags:
                    out.append(f"⟨/{FLAG_MARKUP[flag]}⟩")
            for flag in order:
                if flag in char_flags and flag not in current:
                    out.append(f"⟨{FLAG_MARKUP[flag]}⟩")
            current = char_flags
        out.append(char)
    for flag in reversed(order):
        if flag in current:
            out.append(f"⟨/{FLAG_MARKUP[flag]}⟩")
    return "".join(out)


def _key(token: str) -> str:
    return token.translate(_SPACE_CLASS) if token[:1].isspace() or token[:1] in "   " else token


def _space_change(old: str, new: str) -> bool:
    """In "keep" mode a whitespace token counts as changed only when the new text asks for a special
    space (no-break, narrow no-break, figure) that the old text lacks; retyping a special space as a
    plain one keeps the special space."""
    return old != new and any(c in "   " for c in new)


def _tokens(text: str, offset: int = 0):
    return [(m.group(0), m.start() + offset, m.end() + offset) for m in TOKEN.finditer(text)]


def _split_at_atoms(text: str):
    """Segments between atoms: list of (start, end), plus the atom list."""
    segments, atoms = [], []
    pos = 0
    for match in ATOM.finditer(text):
        segments.append((pos, match.start()))
        atoms.append(match.group(0))
        pos = match.end()
    segments.append((pos, len(text)))
    return segments, atoms


def diff(old: str, new: str, *, merge_whitespace_gaps: bool = True, spaces: str = "keep") -> list[Change]:
    """Character spans that differ, computed on word tokens between atoms.

    spaces="exact" treats every space character as itself; "keep" (for whole-paragraph rewrites)
    lets a plain space stand for a special one already in the text, but still adds special spaces.
    """
    if spaces not in ("exact", "keep"):
        raise ValueError("spaces must be 'exact' or 'keep'")
    key = (lambda token: token) if spaces == "exact" else _key
    old_segments, old_atoms = _split_at_atoms(old)
    new_segments, new_atoms = _split_at_atoms(new)
    if old_atoms != new_atoms:
        raise AtomError(f"atoms changed: {old_atoms} -> {new_atoms}")
    changes: list[Change] = []
    for (os_, oe), (ns_, ne) in zip(old_segments, new_segments):
        a = _tokens(old[os_:oe], os_)
        b = _tokens(new[ns_:ne], ns_)
        matcher = difflib.SequenceMatcher(None, [key(t[0]) for t in a], [key(t[0]) for t in b], autojunk=False)
        segment_changes = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    if _space_change(a[i][0], b[j][0]):
                        segment_changes.append(Change(a[i][1], a[i][2], b[j][1], b[j][2]))
                continue
            o1, o2 = _span(a, i1, i2, os_)
            n1, n2 = _span(b, j1, j2, ns_)
            segment_changes.append(Change(o1, o2, n1, n2))
        if merge_whitespace_gaps:
            segment_changes = _merge(segment_changes, old, new)
        segment_changes = _semantic_cleanup(segment_changes, old, new)
        changes.extend(segment_changes)
    return changes


def _size(change: Change) -> int:
    return max(change.old_end - change.old_start, change.new_end - change.new_start)


def _semantic_cleanup(changes: list[Change], old: str, new: str) -> list[Change]:
    """Absorb an unchanged stretch that is no longer than the edits on both sides of it.

    Keeps one-word fixes minimal (their unchanged context is long) while a rewritten
    clause becomes one readable deletion and insertion instead of fragments aligned on
    small common words.
    """
    changes = list(changes)
    merged = True
    while merged and len(changes) > 1:
        merged = False
        for index in range(len(changes) - 1):
            left, right = changes[index], changes[index + 1]
            if _blank(left, old, new) or _blank(right, old, new):
                continue   # a spacing fix stays on its own
            equal = old[left.old_end:right.old_start]
            if equal != new[left.new_end:right.new_start]:
                continue   # the gap only looks equal (a kept no-break space); merging would replace it
            if len(equal) <= _size(left) and len(equal) <= _size(right):
                changes[index:index + 2] = [Change(left.old_start, right.old_end, left.new_start, right.new_end)]
                merged = True
                break
    return changes


def _blank(change: Change, old: str, new: str) -> bool:
    return not old[change.old_start:change.old_end].strip() and not new[change.new_start:change.new_end].strip()


def _span(tokens, i1, i2, segment_start):
    if i2 > i1:
        return tokens[i1][1], tokens[i2 - 1][2]
    if i1 < len(tokens):
        return tokens[i1][1], tokens[i1][1]
    point = tokens[-1][2] if tokens else segment_start
    return point, point


def _is_replacement(change: Change) -> bool:
    return change.old_end > change.old_start and change.new_end > change.new_start


def _merge(changes: list[Change], old: str, new: str) -> list[Change]:
    """Join replacements separated by one short whitespace gap into one phrase-level change,
    so a rewritten clause reads as one deletion and one insertion rather than a word salad."""
    merged: list[Change] = []
    for change in changes:
        if merged:
            last = merged[-1]
            gap_old = old[last.old_end:change.old_start]
            gap_new = new[last.new_end:change.new_start]
            if gap_old == gap_new and gap_old.strip() == "" and 0 < len(gap_old) <= 2 \
                    and _is_replacement(last) and _is_replacement(change):
                merged[-1] = Change(last.old_start, change.old_end, last.new_start, change.new_end)
                continue
        merged.append(change)
    return merged

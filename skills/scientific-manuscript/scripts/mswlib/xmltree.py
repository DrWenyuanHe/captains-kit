"""A small, byte-offset XML tree for OOXML parts.

The tree records where every element starts and ends in the original bytes, so a
caller can read structure with a parser and write changes as byte splices. Nothing
is re-serialized: untouched bytes, attribute order, namespace prefixes and CRLF
inside base64 payloads survive exactly.

Supported input: well-formed XML without a DTD, as written by Word, LibreOffice and
python-docx. Comments, processing instructions and CDATA are kept as opaque spans.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(
    rb"<(?:"
    rb"(?P<comment>!--.*?--)"
    rb"|(?P<pi>\?.*?\?)"
    rb"|(?P<cdata>!\[CDATA\[.*?\]\])"
    rb"|(?P<decl>![A-Z][^>]*)"
    rb"|(?P<close>/)?(?P<name>[A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?)"
    rb"(?P<attrs>(?:[^>\"']|\"[^\"]*\"|'[^']*')*?)(?P<empty>/)?"
    rb")>",
    re.S,
)
_ATTR = re.compile(rb"([A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
_ENTITY = re.compile(r"&(#x[0-9A-Fa-f]+|#[0-9]+|lt|gt|amp|quot|apos);")
_NAMED = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}


class XMLError(ValueError):
    """Raised when a part is not well-formed enough to edit safely."""


def unescape(text: str) -> str:
    def repl(match):
        value = match.group(1)
        if value.startswith("#x"):
            return chr(int(value[2:], 16))
        if value.startswith("#"):
            return chr(int(value[1:]))
        return _NAMED[value]
    return _ENTITY.sub(repl, text) if "&" in text else text


def escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(text: str) -> str:
    return escape_text(text).replace('"', "&quot;")


class Text:
    """Character data between tags (kept as a span of the original bytes)."""

    __slots__ = ("start", "end", "parent")
    tag = None

    def __init__(self, start: int, end: int, parent: "Element"):
        self.start, self.end, self.parent = start, end, parent


class Element:
    __slots__ = ("tag", "start", "open_end", "close_start", "end", "children", "parent",
                 "self_closing", "_attr_bytes", "_attrs", "order")

    def __init__(self, tag: str, start: int, open_end: int, attr_bytes: bytes,
                 self_closing: bool, parent: "Element | None"):
        self.tag = tag
        self.start = start
        self.open_end = open_end
        self.close_start = open_end
        self.end = open_end
        self.children: list = []
        self.parent = parent
        self.self_closing = self_closing
        self._attr_bytes = attr_bytes
        self._attrs = None
        self.order = 0

    # -- attributes ---------------------------------------------------------------
    @property
    def attrs(self) -> dict:
        if self._attrs is None:
            self._attrs = {
                m.group(1).decode("utf-8"): unescape((m.group(2) if m.group(2) is not None
                                                      else m.group(3)).decode("utf-8"))
                for m in _ATTR.finditer(self._attr_bytes)
            }
        return self._attrs

    def get(self, name: str, default=None):
        return self.attrs.get(name, default)

    # -- navigation ---------------------------------------------------------------
    def elements(self):
        return [c for c in self.children if isinstance(c, Element)]

    def find(self, tag: str):
        for child in self.children:
            if isinstance(child, Element) and child.tag == tag:
                return child
        return None

    def findall(self, tag: str):
        return [c for c in self.children if isinstance(c, Element) and c.tag == tag]

    def iter(self, tag: str | None = None):
        """Pre-order iteration over this element and its descendants."""
        stack = [self]
        while stack:
            node = stack.pop()
            if tag is None or node.tag == tag:
                yield node
            stack.extend(reversed([c for c in node.children if isinstance(c, Element)]))

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def has_ancestor(self, tags) -> bool:
        return any(a.tag in tags for a in self.ancestors())

    def __repr__(self):
        return f"<Element {self.tag} @{self.start}>"


class XMLDoc:
    """A parsed part. `data` is never modified; build new bytes with splices."""

    def __init__(self, data: bytes):
        if data.startswith(b"\xef\xbb\xbf"):
            raise XMLError("UTF-8 byte-order mark in an OOXML part is not supported")
        self.data = data
        self.root = None
        self.elements: list[Element] = []
        self._parse()

    def _parse(self):
        data = self.data
        stack: list[Element] = []
        pos = 0
        order = 0
        for match in _TOKEN.finditer(data):
            if match.start() > pos and stack:
                stack[-1].children.append(Text(pos, match.start(), stack[-1]))
            elif match.start() > pos and data[pos:match.start()].strip():
                raise XMLError(f"character data outside the root element at byte {pos}")
            pos = match.end()
            if match.group("comment") or match.group("pi") or match.group("cdata") or match.group("decl"):
                if stack:
                    stack[-1].children.append(Text(match.start(), match.end(), stack[-1]))
                continue
            name = match.group("name").decode("utf-8")
            if match.group("close"):
                if not stack or stack[-1].tag != name:
                    expected = stack[-1].tag if stack else "(none)"
                    raise XMLError(f"mismatched end tag </{name}> at byte {match.start()}; expected </{expected}>")
                element = stack.pop()
                element.close_start = match.start()
                element.end = match.end()
                continue
            parent = stack[-1] if stack else None
            element = Element(name, match.start(), match.end(), match.group("attrs"),
                              bool(match.group("empty")), parent)
            element.order = order
            order += 1
            self.elements.append(element)
            if parent is None:
                if self.root is not None:
                    raise XMLError("more than one root element")
                self.root = element
            else:
                parent.children.append(element)
            if not element.self_closing:
                stack.append(element)
        if stack:
            raise XMLError(f"unclosed element <{stack[-1].tag}> (document truncated?)")
        if self.root is None:
            raise XMLError("no root element")
        if data[pos:].strip():
            raise XMLError("trailing data after the root element")

    # -- byte access --------------------------------------------------------------
    def raw(self, node) -> bytes:
        return self.data[node.start:node.end]

    def inner(self, element: Element) -> bytes:
        return b"" if element.self_closing else self.data[element.open_end:element.close_start]

    def open_tag(self, element: Element) -> bytes:
        return self.data[element.start:element.open_end]

    def close_tag(self, element: Element) -> bytes:
        return b"" if element.self_closing else self.data[element.close_start:element.end]

    def text(self, element: Element) -> str:
        """Unescaped character content of an element that holds text only."""
        return unescape(self.inner(element).decode("utf-8"))

    def iter(self, tag: str | None = None):
        if tag is None:
            return iter(self.elements)
        return (e for e in self.elements if e.tag == tag)

    def namespaces(self) -> dict:
        """Prefix -> URI declared on the root element."""
        out = {}
        for key, value in self.root.attrs.items():
            if key.startswith("xmlns:"):
                out[key[6:]] = value
            elif key == "xmlns":
                out[""] = value
        return out


def apply_splices(data: bytes, splices) -> bytes:
    """Apply (start, end, replacement) splices. Overlapping splices are an error."""
    ordered = sorted(splices, key=lambda s: (s[0], s[1]))
    out = []
    pos = 0
    for start, end, replacement in ordered:
        if start < pos:
            raise XMLError(f"overlapping splices at byte {start}")
        out.append(data[pos:start])
        out.append(replacement)
        pos = end
    out.append(data[pos:])
    return b"".join(out)

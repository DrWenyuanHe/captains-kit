"""Read and write .docx packages without disturbing untouched entries.

Writing reuses each entry's original ZipInfo (order, compression, timestamps,
attributes), creates the output exclusively and re-reads it before returning.
"""

from __future__ import annotations

import copy
import hashlib
import io
import os
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .pathutil import fs
from .xmltree import XMLDoc, XMLError, escape_attr

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
IMAGE_REL = DOC_REL + "/image"
COMMENTS_REL = DOC_REL + "/comments"
MAIN_PART = "word/document.xml"
STRAY_NAMES = re.compile(r"(^|/)(~\$|~WRL|\.~lock)|\.(bak|tmp|orig)$|(^|/)(__pycache__|Thumbs\.db|\.DS_Store)(/|$)")
FIXED_DATE = (1980, 1, 1, 0, 0, 0)

MEDIA_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
    "tif": "image/tiff", "tiff": "image/tiff", "bmp": "image/bmp", "emf": "image/x-emf",
    "wmf": "image/x-wmf", "svg": "image/svg+xml",
}


class PackageError(ValueError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(fs(path), "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def owner_names(name: str) -> set[str]:
    """Lock files that mean the document `name` is open. Word writes "~$" plus the name, with one or
    two leading characters dropped from a longer name; LibreOffice writes ".~lock.<name>#"."""
    return {"~$" + name, "~$" + name[1:], "~$" + name[2:], f".~lock.{name}#"} - {"~$"}


def lock_files(path) -> list[str]:
    """Owner files of this document (matched by whole name) and Word's ~WRL*.tmp save files beside it."""
    path = Path(path)
    folder = path.parent if path.parent != Path("") else Path(".")
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    owners = {n.lower() for n in owner_names(path.name)}
    hits = []
    for name in names:
        upper = name.upper()
        if name.lower() in owners or (upper.startswith("~WRL") and upper.endswith(".TMP")):
            hits.append(str(folder / name))
    return sorted(hits)


class Package:
    def __init__(self, source):
        if isinstance(source, (bytes, bytearray)):
            self.path = None
            blob = bytes(source)
        else:
            self.path = Path(source)
            with open(fs(self.path), "rb") as handle:
                blob = handle.read()
        self.sha256 = sha256_bytes(blob)
        self.size = len(blob)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            bad = archive.testzip()
            if bad:
                raise PackageError(f"corrupt zip entry: {bad}")
            self.infos = archive.infolist()
            names = [info.filename for info in self.infos]
            if len(names) != len(set(names)):
                raise PackageError("duplicate zip entries")
            self.parts = {info.filename: archive.read(info) for info in self.infos}
        if MAIN_PART not in self.parts:
            raise PackageError("not a Word document: word/document.xml is missing")
        self._xml = {}

    # -- parts ------------------------------------------------------------------------
    def names(self) -> list[str]:
        return [info.filename for info in self.infos]

    def read(self, name: str) -> bytes:
        return self.parts[name]

    def xml(self, name: str) -> XMLDoc:
        if name not in self._xml:
            self._xml[name] = XMLDoc(self.parts[name])
        return self._xml[name]

    @property
    def document(self) -> XMLDoc:
        return self.xml(MAIN_PART)

    # -- content types and relationships ---------------------------------------------
    def content_types(self) -> tuple[dict, dict]:
        doc = self.xml("[Content_Types].xml")
        defaults = {e.get("Extension", "").lower(): e.get("ContentType") for e in doc.iter("Default")}
        overrides = {e.get("PartName", "").lstrip("/"): e.get("ContentType") for e in doc.iter("Override")}
        return defaults, overrides

    def content_type(self, name: str):
        defaults, overrides = self.content_types()
        if name in overrides:
            return overrides[name]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        return defaults.get(ext)

    @staticmethod
    def rels_name(part: str) -> str:
        folder, base = posixpath.split(part)
        return posixpath.join(folder, "_rels", base + ".rels")

    def relationships(self, part: str) -> list[dict]:
        name = self.rels_name(part)
        if name not in self.parts:
            return []
        doc = self.xml(name)
        return [dict(e.attrs) for e in doc.iter("Relationship")]

    def resolve(self, part: str, target: str) -> str:
        if target.startswith("/"):
            return target.lstrip("/")
        return posixpath.normpath(posixpath.join(posixpath.dirname(part), target))

    def story_parts(self) -> list[str]:
        """document.xml plus the headers, footers, notes and comments it references."""
        stories = [MAIN_PART]
        for rel in self.relationships(MAIN_PART):
            kind = rel.get("Type", "").rsplit("/", 1)[-1]
            if rel.get("TargetMode") == "External":
                continue
            if kind in ("header", "footer", "footnotes", "endnotes", "comments"):
                name = self.resolve(MAIN_PART, rel.get("Target", ""))
                if name in self.parts and name not in stories:
                    stories.append(name)
        return stories

    # -- writing ----------------------------------------------------------------------
    def write(self, out_path, changes: dict | None = None, additions: list | None = None) -> str:
        """Write a new package; refuses an existing destination. Returns its SHA-256."""
        changes = changes or {}
        additions = additions or []
        unknown = set(changes) - set(self.parts)
        if unknown:
            raise PackageError(f"cannot change missing parts: {sorted(unknown)}")
        existing = set(self.parts)
        for name, _ in additions:
            if name in existing:
                raise PackageError(f"part already exists: {name}")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for info in self.infos:
                data = changes.get(info.filename, self.parts[info.filename])
                archive.writestr(copy.copy(info), data)
            for name, data in additions:
                info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0
                archive.writestr(info, data)
        blob = buffer.getvalue()
        check = Package(blob)
        for name in list(changes) + [n for n, _ in additions]:
            if name.endswith(".xml") or name.endswith(".rels"):
                try:
                    ET.fromstring(check.parts[name])
                    check.xml(name)
                except (ET.ParseError, XMLError) as error:
                    raise PackageError(f"{name} is not well-formed after editing: {error}") from error
        out_path = Path(out_path)
        os.makedirs(fs(out_path.parent), exist_ok=True)
        with open(fs(out_path), "xb") as handle:
            handle.write(blob)
        return sha256_bytes(blob)


# -- helpers that build changed parts -----------------------------------------------

def add_relationship(package: Package, part: str, rel_type: str, target: str, pending: dict) -> tuple[str, bytes]:
    """Return (rId, new rels bytes). `pending` holds rels bytes already changed in this build."""
    name = Package.rels_name(part)
    data = pending.get(name, package.parts.get(name))
    if data is None:
        data = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
                b'<Relationships xmlns="' + REL_NS.encode() + b'"></Relationships>')
    doc = XMLDoc(data)
    used = {e.get("Id") for e in doc.iter("Relationship")}
    number = 1
    while f"rId{number}" in used:
        number += 1
    rid = f"rId{number}"
    entry = (f'<Relationship Id="{rid}" Type="{escape_attr(rel_type)}" '
             f'Target="{escape_attr(target)}"/>').encode("utf-8")
    root = doc.root
    if root.self_closing:
        new = data[:root.start] + data[root.start:root.open_end - 2] + b">" + entry + b"</Relationships>" + data[root.end:]
    else:
        new = data[:root.close_start] + entry + data[root.close_start:]
    return rid, new


def ensure_content_type(package: Package, pending: dict, *, extension: str | None = None,
                        part: str | None = None, content_type: str) -> bytes | None:
    """Return new [Content_Types].xml bytes if an entry is needed, else None."""
    name = "[Content_Types].xml"
    data = pending.get(name, package.parts[name])
    doc = XMLDoc(data)
    if extension is not None:
        if any(e.get("Extension", "").lower() == extension.lower() for e in doc.iter("Default")):
            return None
        entry = f'<Default Extension="{escape_attr(extension)}" ContentType="{escape_attr(content_type)}"/>'
    else:
        if any(e.get("PartName", "").lstrip("/") == part for e in doc.iter("Override")):
            return None
        entry = f'<Override PartName="/{escape_attr(part)}" ContentType="{escape_attr(content_type)}"/>'
    root = doc.root
    return data[:root.close_start] + entry.encode("utf-8") + data[root.close_start:]

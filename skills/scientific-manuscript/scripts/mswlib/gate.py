"""The verification gate: compare a candidate .docx with its source.

Every check reads the two files (and optionally the build manifest) only. The
central ideas: every change must be tracked (reject(new) == reject(source)),
the accepted text must equal the source's accepted text plus exactly the
declared changes, and citations, bookmarks, links, fields, revision ids and the
package itself must stay intact.
"""

from __future__ import annotations

import difflib
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from .docx import MAIN_PART, STRAY_NAMES, Package, PackageError, lock_files
from .endnote import bookmark_names, citation_fields, citation_signature, link_targets
from .views import positional_view, project
from .wordml import (PROPERTY_CHANGES, REVISION_ID_TAGS, RUN_REVISIONS, element_char, formatted_segments,
                     iter_paragraphs, map_fields, paragraph_style, paragraph_text)
from .xmltree import XMLDoc, XMLError

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
PROTECTED_PARTS = re.compile(r"^word/(styles|numbering|fontTable|theme/.*|header\d*|footer\d*|footnotes|endnotes)\.xml$")
RANGE_END_TAGS = {t for t in REVISION_ID_TAGS if t.endswith("RangeEnd")}
_EDGE_SPACE = re.compile(r"^[ \t\r\n]|[ \t\r\n]$")
_SCIENCE = re.compile(r"[-\u2212]?\d(?:[\d.,]*\d)?%?|\b(?:P|q|n|CI|OR|HR|RR|SD|SEM|AUROC|AUC)\b"
                      r"|\b[A-Z][A-Z0-9]{2,}[0-9A-Z-]*\b"
                      r"|(?i:\b(?:increas|decreas|higher|lower|greater|smaller|rose|fell|declin|reduc|elevat|"
                      r"positive|negative|more|less)\w*)")
SCIENCE_TERMS = _SCIENCE   # numbers, statistics terms, gene-like symbols and direction words
_CITATION_NUMBERS = re.compile(r"\(\d[\d,\s\u2013-]*\)")


class Report:
    def __init__(self):
        self.checks = []

    def add(self, name, status, detail="", items=None):
        self.checks.append({"name": name, "status": status, "detail": detail, "items": items or []})

    @property
    def status(self):
        return FAIL if any(c["status"] == FAIL for c in self.checks) else PASS

    def to_dict(self):
        return {"status": self.status, "checks": self.checks, "summary": self.summary_line()}

    def summary_line(self):
        parts = []
        for check in self.checks:
            if check["status"] == INFO:
                continue
            parts.append(f"{check['name']}: {check['status']}" + (f" ({check['detail']})" if check["detail"] else ""))
        return " | ".join(parts)

    def text(self):
        lines = [f"GATE {self.status}"]
        for check in self.checks:
            lines.append(f"  [{check['status']}] {check['name']}: {check['detail']}")
            for item in check["items"][:12]:
                lines.append(f"      - {item}")
            if len(check["items"]) > 12:
                lines.append(f"      - ... {len(check['items']) - 12} more")
        return "\n".join(lines)


def _preview(text, limit=110):
    text = text.replace("\n", "\\n").replace("\t", "\\t")
    return text if len(text) <= limit else text[:limit] + "..."


def _para_list(doc: XMLDoc):
    return [(p.get("w14:paraId"), paragraph_text(doc, p)) for p in iter_paragraphs(doc)]


class Gate:
    def __init__(self, source, candidate, manifest=None, *, author=None, allow_part_changes=False):
        self.source_path = Path(source) if not isinstance(source, Package) else source.path
        self.candidate_path = Path(candidate) if not isinstance(candidate, Package) else candidate.path
        self.base = source if isinstance(source, Package) else Package(source)
        self.new = candidate if isinstance(candidate, Package) else Package(candidate)
        self.manifest = manifest or {}
        declared = self.manifest.get("declared", {})
        self.declared = declared
        self.author = author or self.manifest.get("author")
        self.allow_part_changes = allow_part_changes
        self.report = Report()

    # -- orchestration -----------------------------------------------------------------
    def run(self) -> Report:
        steps = [self.check_package, self.check_termination, self.check_revision_markup, self.check_revision_ids,
                 self.check_bookmarks, self.check_fields, self.check_views, self.check_whitespace,
                 self.check_new_revisions, self.check_minimality, self.check_science_guard, self.check_locks]
        for step in steps:
            try:
                step()
            except (XMLError, PackageError, KeyError, ValueError) as error:
                self.report.add(step.__name__.replace("check_", ""), FAIL, f"could not run: {error}")
        return self.report

    # -- package -----------------------------------------------------------------------
    def check_package(self):
        problems = []
        base_names, new_names = set(self.base.names()), set(self.new.names())
        removed = sorted(base_names - new_names)
        added = sorted(new_names - base_names)
        declared_added = set(self.declared.get("parts_added", []))
        for name in removed:
            problems.append(f"part removed: {name}")
        folded = Counter(name.lower() for name in new_names)
        for name in sorted(n for n in new_names if folded[n.lower()] > 1):
            problems.append(f"part name differs from another only by case (OPC readers reject it): {name}")
        for name in added:
            if STRAY_NAMES.search(name):
                problems.append(f"stray file packed: {name}")
            elif name not in declared_added and not name.startswith("word/media/") and \
                    name not in ("word/comments.xml", "word/_rels/comments.xml.rels"):
                problems.append(f"undeclared new part: {name}")
        for name in self.new.names():
            if name.endswith("/"):
                continue
            if name.endswith(".xml") or name.endswith(".rels"):
                try:
                    ET.fromstring(self.new.parts[name])
                    self.new.xml(name)
                except (ET.ParseError, XMLError) as error:
                    problems.append(f"{name}: not well-formed: {error}")
                    continue
            if name != "[Content_Types].xml" and self.new.content_type(name) is None:
                problems.append(f"no content type for {name}")
        for name in self.new.names():
            if not name.endswith(".rels"):
                continue
            owner = name.replace("_rels/", "")[:-5]
            for rel in self.new.xml(name).iter("Relationship"):
                if rel.get("TargetMode") == "External":
                    continue
                target = self.new.resolve(owner if owner != "" else "", rel.get("Target", ""))
                if name == "_rels/.rels":
                    target = rel.get("Target", "").lstrip("/")
                if target not in new_names:
                    problems.append(f"{name}: {rel.get('Id')} target missing: {target}")
        dangling = _dangling_ids(self.new) - _dangling_ids(self.base)
        for part, rid in sorted(dangling):
            problems.append(f"{part}: relationship id {rid} is used but not defined (a picture or link Word "
                            "cannot resolve)")
        changed = sorted(n for n in base_names & new_names if self.base.parts[n] != self.new.parts[n])
        protected = [n for n in changed if PROTECTED_PARTS.match(n)
                     and n not in self.declared.get("parts_changed", [])]
        if protected and not self.allow_part_changes:
            problems.extend(f"protected part changed: {n}" for n in protected)
        status = FAIL if problems else PASS
        detail = f"{len(changed)} changed, {len(added)} added, {len(removed)} removed"
        self.report.add("package", status, detail, problems or [f"changed: {', '.join(changed)}"] if changed else problems)

    def check_termination(self):
        data = self.new.parts[MAIN_PART].rstrip()
        ok = data.endswith(b"</w:document>")
        self.report.add("termination", PASS if ok else FAIL, "document.xml ends with </w:document>" if ok else "truncated")

    # -- markup ------------------------------------------------------------------------
    def check_revision_markup(self):
        doc = self.new.document
        problems = []
        counts = Counter()
        for element in doc.elements:
            tag = element.tag
            if tag in RUN_REVISIONS or tag in PROPERTY_CHANGES:
                counts[tag] += 1
            if tag in ("w:t", "w:instrText"):
                nearest = next((a.tag for a in element.ancestors() if a.tag in RUN_REVISIONS), None)
                if nearest == "w:del" and element.parent is not None and element.parent.tag == "w:r":
                    problems.append(f"{tag} inside a deletion at byte {element.start} (must be delText/delInstrText)")
            elif tag in ("w:delText", "w:delInstrText"):
                if not element.has_ancestor({"w:del", "w:moveFrom"}):
                    problems.append(f"{tag} outside a deletion at byte {element.start}")
            elif tag in ("w:ins", "w:del") and element.parent is not None and element.parent.tag == tag:
                problems.append(f"{tag} directly nested in {tag} at byte {element.start}")
        detail = ", ".join(f"{k.split(':')[1]}={v}" for k, v in sorted(counts.items())) or "no revisions"
        self.report.add("revision_markup", FAIL if problems else PASS, detail, problems)

    def _ids(self, package):
        ids = Counter()
        where = {}
        for part in package.story_parts():
            try:
                doc = package.xml(part)
            except XMLError:
                continue
            for element in doc.elements:
                if element.tag in REVISION_ID_TAGS and element.tag not in RANGE_END_TAGS:
                    value = element.get("w:id")
                    if value is not None:
                        ids[value] += 1
                        where.setdefault(value, f"{part}:{element.tag}")
        return ids, where

    def check_revision_ids(self):
        ids, where = self._ids(self.new)
        duplicates = [f"w:id {k} used {v} times (first: {where[k]})" for k, v in ids.items() if v > 1]
        base_ids, _ = self._ids(self.base)
        base_dups = {k for k, v in base_ids.items() if v > 1}
        new_dups = [d for d in duplicates if d.split()[1] not in base_dups]
        problems = list(new_dups)
        # comments: ids referenced in the document must exist in comments.xml, and be unique there
        doc = self.new.document
        refs = {e.get("w:id") for e in doc.iter("w:commentReference")}
        starts = {e.get("w:id") for e in doc.iter("w:commentRangeStart")}
        ends = {e.get("w:id") for e in doc.iter("w:commentRangeEnd")}
        comment_ids = Counter()
        if "word/comments.xml" in self.new.parts:
            comment_ids = Counter(e.get("w:id") for e in self.new.xml("word/comments.xml").iter("w:comment"))
        problems += [f"comment id {k} defined {v} times" for k, v in comment_ids.items() if v > 1]
        problems += [f"comment reference {k} has no comment" for k in sorted(refs - set(comment_ids))]
        problems += [f"comment range {k} is unpaired" for k in sorted(starts ^ ends)]
        status = FAIL if problems else (WARN if duplicates else PASS)
        detail = f"{len(ids)} revision ids unique" if not duplicates else f"{len(duplicates)} duplicated"
        if duplicates and not new_dups:
            detail += " (already duplicated in the source)"
        self.report.add("revision_ids", status, detail, problems or duplicates)

    def check_bookmarks(self):
        doc = self.new.document
        problems = []
        starts = Counter(e.get("w:id") for e in doc.iter("w:bookmarkStart"))
        ends = Counter(e.get("w:id") for e in doc.iter("w:bookmarkEnd"))
        names = Counter(e.get("w:name") for e in doc.iter("w:bookmarkStart"))
        problems += [f"bookmark id {k} starts {v} times" for k, v in starts.items() if v > 1]
        problems += [f"bookmark name {k!r} used {v} times" for k, v in names.items() if v > 1]
        problems += [f"bookmark id {k} has no end" for k in sorted(set(starts) - set(ends))]
        problems += [f"bookmark end {k} has no start" for k in sorted(set(ends) - set(starts))]
        base_doc = self.base.document
        base_problems = set()
        base_starts = Counter(e.get("w:id") for e in base_doc.iter("w:bookmarkStart"))
        base_ends = {e.get("w:id") for e in base_doc.iter("w:bookmarkEnd")}
        base_problems |= {f"bookmark id {k} has no end" for k in set(base_starts) - base_ends}
        base_problems |= {f"bookmark end {k} has no start" for k in base_ends - set(base_starts)}
        base_problems |= {f"bookmark id {k} starts {v} times" for k, v in base_starts.items() if v > 1}
        new_problems = [p for p in problems if p not in base_problems]
        status = FAIL if new_problems else (WARN if problems else PASS)
        self.report.add("bookmarks", status, f"{sum(starts.values())} bookmarks", new_problems or problems)

    def check_fields(self):
        problems = []
        details = []
        for label, package in (("candidate", self.new),):
            for mode in ("raw", "accept", "reject"):
                doc = package.document if mode == "raw" else XMLDoc(project(package.document, mode))
                fmap = map_fields(doc)
                problems += [f"{label} {mode} view: {e}" for e in fmap.errors]
                if mode == "accept":
                    paragraphs = list(iter_paragraphs(doc))
                    for item in fmap.fields:
                        if item.kind != "EN.REFLIST":
                            continue
                        span = paragraphs[max(item.first_paragraph, 0):item.last_paragraph + 1]
                        drawings = sum(1 for p in span for _ in p.iter("w:drawing"))
                        headings = [paragraph_text(doc, p)[:40] for p in span
                                    if paragraph_style(p).lower().startswith("heading")]
                        details.append(f"bibliography spans {len(span)} paragraphs")
                        if drawings:
                            problems.append(f"bibliography field contains {drawings} picture(s): runaway EN.REFLIST")
                        if headings:
                            problems.append(f"bibliography field contains headings: {headings[:3]}")
        self.report.add("fields", FAIL if problems else PASS, "; ".join(details) or "balanced", problems)

    # -- views ----------------------------------------------------------------------------
    def check_views(self):
        base_accept = XMLDoc(project(self.base.document, "accept"))
        new_accept = XMLDoc(project(self.new.document, "accept"))
        base_reject = XMLDoc(project(self.base.document, "reject"))
        new_reject = XMLDoc(project(self.new.document, "reject"))

        # 1. every change tracked
        base_r = [t for _, t in _para_list(base_reject)]
        new_r = [t for _, t in _para_list(new_reject)]
        if base_r == new_r:
            self.report.add("untracked_changes", PASS, f"reject view equals source ({len(new_r)} paragraphs)")
            untracked_format = []
            for index, (p, q) in enumerate(zip(iter_paragraphs(base_reject), iter_paragraphs(new_reject))):
                a = _char_flags(formatted_segments(base_reject, p))
                b = _char_flags(formatted_segments(new_reject, q))
                if a != b:
                    flags = sorted({f for (_, x), (_, y) in zip(a, b) for f in x ^ y})
                    untracked_format.append(f"paragraph {index + 1}: {', '.join(flags) or 'formatting'} changed "
                                            f"without a tracked formatting change: {_preview(base_r[index], 60)!r}")
                    continue
                what = _signature_difference(_paragraph_signature(base_reject, p),
                                             _paragraph_signature(new_reject, q))
                if what:
                    untracked_format.append(f"paragraph {index + 1}: {what} changed without a tracked change: "
                                            f"{_preview(base_r[index], 60)!r}")
            self.report.add("untracked_formatting", FAIL if untracked_format else PASS,
                            f"{len(untracked_format)} paragraph(s)" if untracked_format
                            else "every formatting change is tracked", untracked_format)
        else:
            diffs = _paragraph_diff(base_r, new_r)
            self.report.add("untracked_changes", FAIL, f"reject view differs from source in {len(diffs)} place(s)", diffs)

        # 2. accepted text equals source plus declared changes
        base_a = _para_list(base_accept)
        new_a = _para_list(new_accept)
        changed = _paragraph_diff([t for _, t in base_a], [t for _, t in new_a])
        expected = self.manifest.get("expected_accept")
        intended = self.manifest.get("intended")
        if expected is not None or intended:
            problems = _check_expected(self.base.document, self.new.document, expected) \
                if expected is not None else []
            if intended:
                target = "\n".join(t for _, t in base_a)
                missing = False
                for item in intended:
                    count = target.count(item["old"])
                    if count != 1:
                        problems.append(f"intended old text found {count} times: {_preview(item['old'])}")
                        missing = True
                        continue
                    target = target.replace(item["old"], item["new"])
                if not missing and target != "\n".join(t for _, t in new_a):
                    problems.append("accepted text is not the source text with exactly the intended replacements")
                    problems.extend(changed[:6])
            detail = f"{len(changed)} paragraph change(s)"
            if expected is not None:
                detail += f"; {len(expected)} declared"
            if intended:
                detail += f"; {len(intended)} intended replacement(s)"
            self.report.add("accepted_text", FAIL if problems else PASS, detail, problems)
        else:
            self.report.add("accepted_text", INFO, f"{len(changed)} paragraph change(s); no manifest to compare", changed)

        # 3. citations in both views
        declared_citations = self.declared.get("citations", False)
        problems = []
        for label, a_doc, b_doc in (("accept", base_accept, new_accept), ("reject", base_reject, new_reject)):
            a = [citation_signature(a_doc, f) for f in citation_fields(a_doc)]
            b = [citation_signature(b_doc, f) for f in citation_fields(b_doc)]
            if a != b:
                if len(a) != len(b):
                    problems.append(f"{label} view: citations {len(a)} -> {len(b)}")
                for index, (x, y) in enumerate(zip(a, b)):
                    if x != y:
                        problems.append(f"{label} view: citation {index + 1} changed: result "
                                        f"{_preview(x[3], 30)!r} -> {_preview(y[3], 30)!r}"
                                        + ("" if x[1] == y[1] else "; record payload differs")
                                        + ("" if x[0] == y[0] else "; field code differs"))
                        break
        count = len(citation_fields(new_accept))
        if not problems:
            self._citation_context(base_accept, new_accept)
        if problems and declared_citations:
            self.report.add("citations", WARN, f"declared citation changes; {count} citation fields", problems)
        else:
            self.report.add("citations", FAIL if problems else PASS, f"{count} citation fields preserved"
                            if not problems else "citation fields changed", problems)

        # 4. links and _ENREF targets
        names_new = bookmark_names(new_accept)
        names_base = bookmark_names(base_accept)
        missing_new = {t for t in link_targets(new_accept, map_fields(new_accept).all_fields) if t not in names_new}
        missing_base = {t for t in link_targets(base_accept, map_fields(base_accept).all_fields) if t not in names_base}
        enref_base = {n for n in names_base if n and n.startswith("_ENREF")}
        enref_new = {n for n in names_new if n and n.startswith("_ENREF")}
        problems = [f"link target missing: {t}" for t in sorted(missing_new - missing_base)]
        if enref_base != enref_new and not declared_citations:
            problems.append(f"_ENREF bookmarks {len(enref_base)} -> {len(enref_new)}")
        status = FAIL if problems else (WARN if missing_new else PASS)
        self.report.add("links", status, f"{len(enref_new)} _ENREF targets", problems or
                        [f"pre-existing missing target: {t}" for t in sorted(missing_new)])

        # 5. pictures
        base_pics = _pictures(self.base, base_accept)
        new_pics = _pictures(self.new, new_accept)
        swaps = self.declared.get("pictures", [])
        expected_pics = list(base_pics)
        problems = []
        for removed in self.declared.get("pictures_removed", []):
            wanted = (removed.get("name"), removed.get("sha256"))
            match = next((i for i, p in enumerate(expected_pics) if p == wanted), None)
            if match is None:
                problems.append(f"declared removal: picture {wanted[0]!r} not found in the source")
                continue
            del expected_pics[match]
        for swap in swaps:
            match = [i for i, p in enumerate(expected_pics) if p[0] == swap["old_name"]]
            if len(match) != 1:
                problems.append(f"declared swap: {swap['old_name']} not found once in the source")
                continue
            expected_pics[match[0]] = (swap["new_name"], swap.get("sha256"))
        if [p[0] for p in expected_pics] != [p[0] for p in new_pics]:
            problems.append(f"pictures in accepted view: {[p[0] for p in base_pics]} -> {[p[0] for p in new_pics]}")
        for (name, digest), (_, new_digest) in zip(expected_pics, new_pics):
            if digest and new_digest and digest != new_digest:
                problems.append(f"picture {name}: image bytes differ from the declared file")
            elif digest and not new_digest:
                problems.append(f"picture {name}: its image no longer resolves to a part in the package")
        # The rejected view must show the source's pictures, byte for byte: a swap is tracked, so
        # rejecting it restores the original image, which must not have been altered.
        if _pictures(self.base, base_reject) != _pictures(self.new, new_reject):
            problems.append("pictures in the rejected view differ from the source's (names or image bytes): "
                            f"{[p[0] for p in _pictures(self.base, base_reject)]} -> "
                            f"{[p[0] for p in _pictures(self.new, new_reject)]}")
            status = FAIL
        elif len(new_pics) != len(expected_pics):
            status = FAIL
        elif problems and not self.manifest:
            status = WARN   # swaps cannot be judged without the build manifest that declares them
        else:
            status = FAIL if problems else PASS
        self.report.add("pictures", status, f"{len(new_pics)} pictures in accepted view", problems)

        # 6. section breaks (a deleted paragraph mark that carries a sectPr removes a section)
        base_sections = _sections(base_accept)
        new_sections = _sections(new_accept)
        if base_sections == new_sections:
            self.report.add("sections", PASS, f"{len(new_sections)} section(s) unchanged in the accepted view")
        else:
            self.report.add("sections", FAIL, f"sections {len(base_sections)} -> {len(new_sections)} in the "
                            "accepted view", ["section breaks or section properties changed; the engine never "
                                              "changes sections, so change them in Word"])

        # 7. formatting of every character that the edit did not change
        problems = []
        gains = []
        declared_format = self.declared.get("formatting", False)
        base_pars = list(iter_paragraphs(base_accept))
        new_pars = list(iter_paragraphs(new_accept))
        matcher = difflib.SequenceMatcher(None, [t for _, t in base_a], [t for _, t in new_a], autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag not in ("equal", "replace"):
                continue
            for i, j in zip(range(i1, i2), range(j1, j2)):
                a = _char_flags(formatted_segments(base_accept, base_pars[i]))
                b = _char_flags(formatted_segments(new_accept, new_pars[j]))
                text_a = "".join(c for c, _ in a)
                text_b = "".join(c for c, _ in b)
                chars = difflib.SequenceMatcher(None, text_a, text_b, autojunk=False)
                for op, a1, a2, b1, b2 in chars.get_opcodes():
                    if op != "equal":
                        continue
                    changed_here = [(a[k][1], b[b1 + k - a1][1]) for k in range(a1, a2)
                                    if a[k][1] != b[b1 + k - a1][1] and a[k][0].strip()]
                    if changed_here:
                        lost = sorted({f for old_f, new_f in changed_here for f in old_f - new_f})
                        gained = sorted({f for old_f, new_f in changed_here for f in new_f - old_f})
                        where = _preview(text_a[a1:a2][:40], 40)
                        if lost:
                            problems.append(f"paragraph {j + 1}: {', '.join(lost)} lost on unchanged text near {where!r}")
                        if gained:
                            gains.append(f"paragraph {j + 1}: {', '.join(gained)} added on unchanged text near {where!r}")
                        break
        if problems:
            self.report.add("formatting", FAIL if not declared_format else WARN,
                            f"{len(problems)} formatting loss(es)", problems + gains)
        elif gains:
            self.report.add("formatting", WARN, f"{len(gains)} formatting addition(s) to review", gains)
        else:
            self.report.add("formatting", PASS, "character formatting preserved")

    def _citation_context(self, base_accept: XMLDoc, new_accept: XMLDoc):
        """WARN when the words just before a citation changed: it may now support different text."""
        a, b = _citation_contexts(base_accept), _citation_contexts(new_accept)
        changed = [f"citation {i + 1}: {x!r} -> {y!r}" for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if changed:
            self.report.add("citation_context", WARN, f"{len(changed)} citation(s) now follow different words; "
                            "confirm each still supports its sentence", changed)
        else:
            self.report.add("citation_context", PASS, "text before every citation unchanged")

    def check_science_guard(self):
        """WARN on changed numbers, statistics, gene-like symbols or direction words."""
        base_a = _para_list(XMLDoc(project(self.base.document, "accept")))
        new_a = _para_list(XMLDoc(project(self.new.document, "accept")))
        findings = []
        matcher = difflib.SequenceMatcher(None, [t for _, t in base_a], [t for _, t in new_a], autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            old = "\n".join(t for _, t in base_a[i1:i2])
            new = "\n".join(t for _, t in new_a[j1:j2])
            removed, added, moved = science_delta(old, new)
            where = f"paragraph {j1 + 1}" if j2 - j1 <= 1 else f"paragraphs {j1 + 1}-{j2}"
            if removed or added:
                findings.append(f"{where}: removed {sorted(removed)[:6]} added {sorted(added)[:6]}")
            elif moved:
                findings.append(f"{where}: the same values in a new order {moved[0][:6]} -> {moved[1][:6]}")
        self.report.add("science_guard", WARN if findings else PASS,
                        f"{len(findings)} paragraph(s) change numbers, statistics, symbols or direction words; "
                        "confirm the author approved them" if findings
                        else "no numbers, statistics or directions changed", findings)

    # -- hygiene ---------------------------------------------------------------------------
    def check_whitespace(self):
        def missing(package):
            out = []
            doc = package.document
            for element in doc.elements:
                if element.tag in ("w:t", "w:delText", "w:instrText", "w:delInstrText") and not element.self_closing:
                    text = doc.text(element)
                    if text and _EDGE_SPACE.search(text) and element.get("xml:space") != "preserve":
                        out.append(element)
            return out
        base_missing = missing(self.base)
        new_missing = missing(self.new)
        if len(new_missing) > len(base_missing):
            items = [f"byte {e.start}: {_preview(self.new.document.text(e), 40)!r}" for e in new_missing[:20]]
            self.report.add("whitespace", FAIL, f"{len(new_missing) - len(base_missing)} new text element(s) "
                            "with edge spaces lack xml:space=\"preserve\" (Word drops those spaces)", items)
        else:
            self.report.add("whitespace", PASS if not new_missing else WARN,
                            f"{len(new_missing)} pre-existing" if new_missing else "edge spaces preserved")

    def _revisions(self, package):
        out = {}
        for element in package.document.elements:
            if element.tag in RUN_REVISIONS | PROPERTY_CHANGES and element.get("w:id") is not None:
                out.setdefault(element.get("w:id"), element)
        return out

    def check_new_revisions(self):
        base = self._revisions(self.base)
        new = self._revisions(self.new)
        base_keys = {(e.tag, e.get("w:author"), e.get("w:date"), i) for i, e in base.items()}
        fresh = [e for i, e in new.items() if (e.tag, e.get("w:author"), e.get("w:date"), i) not in base_keys]
        problems = []
        authors = Counter(e.get("w:author") for e in fresh)
        earlier = {(e.get("w:author"), e.get("w:date")) for e in base.values()}
        dates = Counter(e.get("w:date") for e in fresh if (e.get("w:author"), e.get("w:date")) not in earlier)
        if self.author:
            # Continuation wrappers of an earlier, unaccepted insertion keep that insertion's author.
            earlier_authors = {e.get("w:author") for e in base.values()}
            wrong = {a: n for a, n in authors.items() if a != self.author and a not in earlier_authors}
            if wrong:
                problems.append(f"new revisions by unexpected author(s): {wrong} (expected {self.author!r})")
        fields_inside = [e for e in fresh if e.tag in RUN_REVISIONS and
                         any(d.tag in ("w:fldChar", "w:instrText", "w:delInstrText") for d in e.iter())]
        if fields_inside and not (self.declared.get("citations") or self.declared.get("fields")):
            problems.append(f"{len(fields_inside)} new revision(s) wrap field codes (EndNote iron rule)")
        property_changes = [e for e in fresh if e.tag in PROPERTY_CHANGES
                            and (e.get("w:author"), e.get("w:date")) not in earlier]
        warn = []
        if property_changes and not self.declared.get("formatting"):
            warn.append(f"{len(property_changes)} formatting-only revision(s) not declared")
        if len(dates) > 1:
            warn.append(f"new revisions carry {len(dates)} different dates")
        status = FAIL if problems else (WARN if warn else PASS)
        detail = f"{len(fresh)} new revision(s) by {dict(authors)}" if fresh else "no new revisions"
        self.report.add("new_revisions", status, detail, problems + warn)

    def check_minimality(self):
        doc = self.new.document
        base_ids = set(self._revisions(self.base))
        findings = []
        word = re.compile(r"\w+|[^\w\s]")
        for element in doc.iter("w:del"):
            if element.get("w:id") in base_ids or element.self_closing:
                continue
            siblings = element.parent.children
            index = siblings.index(element)
            following = next((s for s in siblings[index + 1:] if getattr(s, "tag", None) is not None), None)
            if following is None or following.tag != "w:ins" or following.get("w:id") in base_ids:
                continue
            old = "".join(doc.text(t) for t in element.iter("w:delText"))
            new = "".join(doc.text(t) for t in following.iter("w:t"))
            a, b = word.findall(old), word.findall(new)
            if a and b and (a[0] == b[0] or a[-1] == b[-1]) and len(a) > 1:
                findings.append(f"{_preview(old, 50)!r} -> {_preview(new, 50)!r}")
        self.report.add("minimality", WARN if findings else PASS,
                        f"{len(findings)} replacement(s) could be smaller" if findings else "replacements are minimal",
                        findings)

    def check_locks(self):
        if self.candidate_path is None:
            return
        locks = lock_files(self.candidate_path)
        self.report.add("word_locks", WARN if locks else PASS, "file is open in Word or LibreOffice" if locks else "no lock files", locks)


# -- helpers -------------------------------------------------------------------------------

def _citation_contexts(doc: XMLDoc) -> list[str]:
    """The six words before each citation field in its paragraph (other citation numbers removed)."""
    out = []
    for item in citation_fields(doc):
        begin = item.begin
        owner = next((a for a in begin.ancestors() if a.tag == "w:p"), None) if begin is not None else None
        if owner is None:
            out.append("")
            continue
        before = []
        for element in owner.iter():
            if element is begin:
                break
            if element.tag == "w:t" and not element.has_ancestor({"w:del", "w:moveFrom"}):
                before.append(doc.text(element))
        text = _CITATION_NUMBERS.sub(" ", "".join(before))
        out.append(" ".join(re.findall(r"\w+", text)[-6:]))
    return out


def _snippets(old: str, new: str, context: int = 30) -> tuple[str, str]:
    """The differing middle of two texts with a little context on each side."""
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < min(len(old), len(new)) - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    start = max(0, prefix - context)

    def cut(text):
        end = min(len(text), len(text) - suffix + context)
        return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")
    return _preview(cut(old), 160), _preview(cut(new), 160)


def _paragraph_diff(a: list, b: list) -> list[str]:
    out = []
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and i2 - i1 == 1 and j2 - j1 == 1:
            old, new = _snippets(a[i1], b[j1])
        else:
            old = " / ".join(_preview(t, 70) for t in a[i1:i2])
            new = " / ".join(_preview(t, 70) for t in b[j1:j2])
        out.append(f"{tag} paragraphs {i1 + 1}-{i2} -> {j1 + 1}-{j2}: {old!r} -> {new!r}")
    return out


def _check_expected(base_doc: XMLDoc, new_doc: XMLDoc, expected: list) -> list[str]:
    """Accepted text of every paragraph, by position.

    Each paragraph of the new document is judged on its own (a deleted paragraph mark does not merge
    it into the next one): a declared paragraph must show its declared text, every other paragraph
    the text it has in the source, and only declared paragraphs may lose their paragraph mark.
    """
    if any("new_index" not in item for item in expected):
        return ["the build manifest records no paragraph positions (new_index); rebuild it with this version"]
    base_texts, base_marks, base_count = positional_view(base_doc, "accept")
    new_texts, new_marks, new_count = positional_view(new_doc, "accept")
    inserted = {item["new_index"]: item for item in expected if item.get("inserted_after") is not None}
    if new_count != base_count + len(inserted):
        return [f"the new document has {new_count} paragraphs; the source has {base_count} and "
                f"{len(inserted)} were declared inserted"]
    position = [n for n in range(new_count) if n not in inserted]    # source paragraph -> new paragraph
    problems = []
    declared = {}
    for item in expected:
        if item.get("inserted_after") is not None:
            continue
        index = item.get("index")
        if not isinstance(index, int) or not 0 <= index < base_count or position[index] != item["new_index"]:
            problems.append(f"declared paragraph {item.get('paraId') or index} is not at its recorded position")
            continue
        declared[index] = item
    for index in range(base_count):
        new = position[index]
        item = declared.get(index)
        want = item["text"] if item is not None else base_texts.get(index)
        got = new_texts.get(new)
        label = (item or {}).get("paraId") or f"{index + 1}"
        if got != want:
            if item is None:
                problems.append(f"undeclared change in paragraph {label}: {_preview(want or '')!r} -> "
                                f"{_preview(got or '')!r}")
            else:
                problems.append(f"paragraph {label}: accepted text {_preview(got or '')!r} != expected "
                                f"{_preview(want or '')!r}")
        mark_removed = new in new_marks
        if mark_removed != (index in base_marks or bool(item and item.get("deleted"))):
            problems.append(f"paragraph {label}: paragraph mark {'deleted' if mark_removed else 'restored'} "
                            "without declaration")
    for new, item in sorted(inserted.items()):
        got = new_texts.get(new)
        if got != item["text"]:
            problems.append(f"inserted paragraph after {item['inserted_after'] + 1}: accepted text "
                            f"{_preview(got or '')!r} != expected {_preview(item['text'])!r}")
        if new in new_marks:
            problems.append(f"inserted paragraph after {item['inserted_after'] + 1} has a deleted paragraph mark")
    return problems


def science_delta(old: str, new: str) -> tuple[Counter, Counter, tuple | None]:
    """Numbers, statistics terms, symbols and direction words removed and added between two texts,
    and, when the same terms appear in a new order (values swapped between groups), both orders."""
    before, after = _SCIENCE.findall(old), _SCIENCE.findall(new)
    removed, added = Counter(before) - Counter(after), Counter(after) - Counter(before)
    moved = (before, after) if not removed and not added and before != after else None
    return removed, added, moved


def _sections(accepted: XMLDoc) -> list[bytes]:
    return [accepted.raw(e) for e in accepted.iter("w:sectPr")
            if not e.has_ancestor({"w:sectPrChange", "w:pPrChange", "mc:Fallback"})]


_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _dangling_ids(package: Package) -> set:
    """(part, id) pairs where an XML part refers to a relationship id its .rels does not define."""
    out = set()
    for name in package.names():
        if not name.endswith(".xml") or name.startswith("[") or "/_rels/" in name or name.startswith("_rels/"):
            continue
        try:
            doc = package.xml(name)
        except (XMLError, PackageError):
            continue
        prefixes = [p for p, uri in doc.namespaces().items() if uri == _REL_NS and p]
        if not prefixes:
            continue
        defined = {r.get("Id") for r in package.relationships(name)}
        for element in doc.elements:
            for key, value in element.attrs.items():
                if key.split(":", 1)[0] in prefixes and ":" in key and value and value not in defined:
                    out.add((name, value))
    return out


def _pictures(package: Package, accepted: XMLDoc):
    rels = {r.get("Id"): r.get("Target") for r in package.relationships(MAIN_PART)}
    import hashlib
    out = []
    for drawing in accepted.iter("w:drawing"):
        name = next((e.get("name") for e in drawing.iter("wp:docPr")), None)
        blip = next((e for e in drawing.iter("a:blip")), None)
        digest = None
        if blip is not None and blip.get("r:embed") in rels:
            target = package.resolve(MAIN_PART, rels[blip.get("r:embed")])
            if target in package.parts:
                digest = hashlib.sha256(package.parts[target]).hexdigest()
        out.append((name, digest))
    return out


_TRACKED_FLAGS = frozenset({"bold", "italic", "superscript", "subscript", "underline", "strike", "caps",
                            "smallcaps", "highlight", "hidden"})


_RSID = re.compile(rb'\s+w:rsid\w*="[^"]*"')
_NOT_PROPERTIES = {"w:rPrChange", "w:pPrChange", "w:ins", "w:del", "w:moveFrom", "w:moveTo"}


def _properties(doc: XMLDoc, element, skip=()) -> tuple:
    """Property children as canonical bytes (order and rsid attributes ignored, change records and
    revision markers left out): what Word shows, not how it was stored."""
    if element is None:
        return ()
    return tuple(sorted(_RSID.sub(b"", doc.raw(c)) for c in element.elements()
                        if c.tag not in _NOT_PROPERTIES and c.tag not in skip))


def _paragraph_signature(doc: XMLDoc, paragraph) -> tuple:
    """Everything a reader sees in one paragraph of a view besides its text: paragraph and mark
    properties, each character's run properties (run splits ignored), field instructions and
    equations (their whole markup)."""
    ppr = paragraph.find("w:pPr")
    tokens: list = []
    for run in paragraph.iter("w:r"):
        owner = next((a for a in run.ancestors() if a.tag == "w:p"), None)
        if owner is not paragraph or run.has_ancestor({"mc:Fallback"}):
            continue
        props = _properties(doc, run.find("w:rPr"))
        for child in run.elements():
            if child.tag == "w:rPr":
                continue
            if child.tag in ("w:instrText", "w:delInstrText"):
                token = ("instr", doc.text(child))
            elif child.tag == "w:fldChar":
                token = ("field", child.get("w:fldCharType", ""))
            else:
                char = element_char(doc, child, deleted=True)
                if char is None:
                    continue
                token = ("text", char)
            if tokens and tokens[-1][0] == token[0] and token[0] in ("text", "instr") and tokens[-1][2] == props:
                tokens[-1] = (token[0], tokens[-1][1] + token[1], props)
            else:
                tokens.append((token[0], token[1], props))
    for simple in paragraph.iter("w:fldSimple"):
        if next((a for a in simple.ancestors() if a.tag == "w:p"), None) is paragraph:
            tokens.append(("instr", simple.get("w:instr", ""), ()))
    math = tuple(_RSID.sub(b"", doc.raw(m)) for m in paragraph.iter()
                 if m.tag in ("m:oMath", "m:oMathPara") and not m.has_ancestor({"m:oMath", "m:oMathPara"})
                 and next((a for a in m.ancestors() if a.tag == "w:p"), None) is paragraph)
    mark = ppr.find("w:rPr") if ppr is not None else None
    return (_properties(doc, ppr, skip={"w:rPr"}), _properties(doc, mark), tuple(tokens), math)


def _signature_difference(a: tuple, b: tuple) -> str | None:
    if a == b:
        return None
    names = ["paragraph properties", "paragraph mark properties", "run properties, field instructions or text",
             "equations (structure or text)"]
    changed = [name for name, x, y in zip(names, a, b) if x != y]
    if changed == [names[2]]:
        kinds = {kind for kind, _, _ in a[2]} | {kind for kind, _, _ in b[2]}
        if [(k, v) for k, v, _ in a[2]] == [(k, v) for k, v, _ in b[2]]:
            return "run properties (font, size, colour, language or style)"
        if "instr" in kinds and [(k, v) for k, v, _ in a[2] if k != "instr"] == \
                [(k, v) for k, v, _ in b[2] if k != "instr"]:
            return "field instructions"
    return ", ".join(changed)


def _char_flags(segments) -> list:
    return [(ch, flags & _TRACKED_FLAGS) for text, flags in segments for ch in text]


def verify(source, candidate, manifest=None, **options) -> Report:
    return Gate(source, candidate, manifest, **options).run()

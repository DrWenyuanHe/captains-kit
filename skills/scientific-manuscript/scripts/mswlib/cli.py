"""Command line for the scientific-manuscript skill: python scripts/msw.py <command> ..."""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from . import __version__
from .config import record_path, revision_author, safe_console, write_json_exclusive
from .docx import MAIN_PART, Package, lock_files
from .edit import EditError, build, extract
from .endnote import census
from .gate import verify
from .pathutil import explain, fs
from .views import project
from .wordml import PROPERTY_CHANGES, RUN_REVISIONS, iter_paragraphs, map_fields
from .xmltree import XMLDoc

# Optional command modules. Each defines register(subparsers).
PLUGINS = ("project", "template", "wordcount", "lint", "refs", "proof")


def _fail(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return 2


# -- inspect ---------------------------------------------------------------------------

def cmd_inspect(args) -> int:
    package = Package(args.docx)
    doc = package.document
    revisions = {}
    authors = {}
    for element in doc.elements:
        if element.tag in RUN_REVISIONS | PROPERTY_CHANGES:
            revisions[element.tag] = revisions.get(element.tag, 0) + 1
            author = element.get("w:author")
            if author:
                authors[author] = authors.get(author, 0) + 1
    paragraphs = list(iter_paragraphs(doc))
    runs = list(doc.iter("w:r"))
    settings = package.parts.get("word/settings.xml", b"")
    en = census(doc)
    data = doc.data
    profile = {
        "file": str(args.docx), "sha256": package.sha256, "bytes": package.size,
        "parts": len(package.parts), "paragraphs": len(paragraphs),
        "paragraphs_with_paraId": sum(1 for p in paragraphs if p.get("w14:paraId")),
        "runs": len(runs), "runs_with_attributes": sum(1 for r in runs if doc.open_tag(r) != b"<w:r>"),
        "revisions": revisions, "revision_authors": authors,
        "max_w_id": max([int(e.get("w:id")) for e in doc.elements if (e.get("w:id") or "").isdigit()] + [0]),
        "track_revisions_on": b"<w:trackRevisions" in settings,
        "endnote_instant_formatting": ("on" if b"&lt;Enabled&gt;1&lt;/Enabled&gt;" in settings else "off")
        if b"EN.InstantFormat" in settings else "absent",
        "document_xml_crlf": data.count(b"\r\n"),
        "comments": sum(1 for _ in package.xml("word/comments.xml").iter("w:comment"))
        if "word/comments.xml" in package.parts else 0,
        "pictures": sum(1 for _ in doc.iter("w:drawing")),
        "citations": {k: en[k] for k in ("citation_fields_live", "citation_fields_deleted", "payload_forms",
                                         "cite_occurrences", "distinct_records", "library_db_ids",
                                         "enref_bookmarks", "link_targets_missing", "reflist", "field_errors")},
        "placeholders": len(en["placeholders"]),
        "word_lock_files": lock_files(args.docx),
    }
    print(json.dumps(profile, indent=2, ensure_ascii=False))
    return 0


# -- extract -------------------------------------------------------------------------

def _extract_key(record: dict, used: set) -> str:
    """File name for a paragraph: its paraId when that is a plain 8-digit hex id not used by another
    paragraph (document text never becomes a path), otherwise P and the paragraph position."""
    pid = record["paraId"] or ""
    if re.fullmatch(r"[0-9A-Fa-f]{8}", pid) and pid.upper() not in used:
        return pid
    return f"P{record['index']:04d}"


def cmd_extract(args) -> int:
    out = Path(args.out)
    if Path(fs(out)).exists() and any(Path(fs(out)).iterdir()):
        return _fail(f"{out} exists and is not empty; choose a new folder")
    styles = [s.lower() for s in (args.exclude_style or [])]

    def select(record):
        if record["editable_chars"] < args.min_chars:
            return False
        if any(record["style"].lower().startswith(s) for s in styles):
            return False
        if args.paragraphs and record["paraId"] not in args.paragraphs and str(record["index"]) not in args.paragraphs:
            return False
        return True

    records = extract(args.docx, select=select)
    counts = Counter((p.get("w14:paraId") or "").upper() for p in iter_paragraphs(Package(args.docx).document))
    duplicated = {pid for pid, count in counts.items() if count > 1}
    import os
    os.makedirs(fs(out / "in"), exist_ok=True)
    os.makedirs(fs(out / "out"), exist_ok=True)
    with open(fs(out / "paragraphs.jsonl"), "x", encoding="utf-8", newline="\n") as handle:
        for record in records:
            record["key"] = _extract_key(record, duplicated)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            with open(fs(out / "in" / f"{record['key']}.txt"), "x", encoding="utf-8", newline="\n") as text:
                text.write(record["text"])
    source = {"source": record_path(Path(args.docx).resolve()), "sha256": Package(args.docx).sha256,
              "paragraphs": len(records)}
    write_json_exclusive(out / "source.json", source)
    print(f"extracted {len(records)} paragraphs to {out} (write rewrites to {out / 'out'}/<key>.txt)")
    return 0


def cmd_collect(args) -> int:
    folder = Path(args.folder)
    records = {}
    for line in Path(fs(folder / "paragraphs.jsonl")).read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        records[record["key"]] = record
    edits = []
    unchanged = 0
    for path in sorted((folder / "out").glob("*.txt")):
        key = path.stem
        if key not in records:
            return _fail(f"{path.name} does not match an extracted paragraph")
        text = Path(fs(path)).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        record = records[key]
        if not text.strip() and record["text"].strip():
            unchanged += 1     # "write nothing" leaves the paragraph as it is; deletion is a delete_paragraph op
            continue
        # an editor may add one final newline; a paragraph that really ends in a line break keeps it
        if text.endswith("\n") and (not record["text"].endswith("\n") or text == record["text"] + "\n"):
            text = text[:-1]
        if text == record["text"]:
            unchanged += 1
            continue
        para = record["paraId"] if key == record["paraId"] else {"index": record["index"]}
        edits.append({"id": key, "op": "rewrite", "para": para, "text": text, "source_hash": record["source_hash"],
                      "purpose": args.purpose or "rewrite"})
    spec = {"edits": edits}
    write_json_exclusive(args.out, spec)
    print(f"{len(edits)} rewritten paragraph(s), {unchanged} unchanged -> {args.out}")
    return 0


# -- build / verify ------------------------------------------------------------------

def cmd_build(args) -> int:
    author = revision_author(args.author, args.source)
    if not author:
        return _fail("no revision author: pass --author, set MSW_AUTHOR, or set author.revision_name in manuscript.json")
    out = Path(args.out)
    if Path(fs(out)).exists():
        return _fail(f"{out} already exists; versions are never overwritten")
    failed = out.with_name(out.stem + " GATE FAILED" + out.suffix)
    manifests = {out: Path(args.manifest) if args.manifest else out.with_name(out.stem + ".manifest.json"),
                 failed: Path(args.manifest) if args.manifest else failed.with_name(failed.stem + ".manifest.json")}
    taken = [p for p in ([manifests[out]] + ([failed, manifests[failed]] if args.write_failed else []))
             if Path(fs(p)).exists()]
    if taken:
        return _fail(f"{taken[0]} already exists; choose a new --out (or --manifest) so nothing is overwritten")
    locks = lock_files(args.source)
    if locks:
        print(f"WARNING: the source appears to be open in Word or LibreOffice: {locks}", file=sys.stderr)
    spec = json.loads(Path(fs(args.spec)).read_text(encoding="utf-8-sig"))
    try:
        result = build(args.source, spec, author=author, date=args.date, id_base=args.id_base,
                       track_revisions={"on": True, "off": False}.get(args.track_revisions),
                       base_dir=Path(args.spec).resolve().parent)
    except EditError as error:
        return _fail(str(error))
    source = Package(args.source)
    report = verify(source, Package(result.blob), result.manifest)
    result.manifest["gate"] = report.to_dict()
    result.manifest["source"]["path"] = record_path(Path(args.source).resolve())
    result.manifest["spec"] = record_path(Path(args.spec).resolve())
    print(report.text())
    if report.status != "PASS" and not args.write_failed:
        print("Build not written: the gate failed. Fix the spec (or use --write-failed to inspect).", file=sys.stderr)
        return 1
    target = out if report.status == "PASS" else failed
    manifest_path = manifests[target]
    import os
    os.makedirs(fs(target.parent), exist_ok=True)
    with open(fs(target), "xb") as handle:
        handle.write(result.blob)
    result.manifest["output"] = {"path": record_path(target.resolve()), "sha256": result.manifest["output_sha256"]}
    write_json_exclusive(manifest_path, result.manifest)
    ids = result.manifest["revision_ids"]
    print(f"wrote {target} ({len(result.manifest['edits'])} edit record(s), revision ids {ids[0]}-{ids[-1]})"
          if ids else f"wrote {target}")
    print(f"manifest: {manifest_path}")
    return 0 if report.status == "PASS" else 1


def cmd_verify(args) -> int:
    manifest = json.loads(Path(fs(args.manifest)).read_text(encoding="utf-8-sig")) if args.manifest else None
    report = verify(args.source, args.candidate, manifest, author=args.author,
                    allow_part_changes=args.allow_part_changes)
    print(report.text())
    print("SUMMARY: " + report.summary_line())
    if args.json:
        write_json_exclusive(args.json, report.to_dict())
    return 0 if report.status == "PASS" else 1


def cmd_drift(args) -> int:
    from .drift import drift, report_text
    if args.json and Path(fs(args.json)).exists():
        return _fail(f"{args.json} already exists; nothing was overwritten")
    result = drift(args.old, args.new)
    print(report_text(result))
    if args.json:
        write_json_exclusive(args.json, result)
    return 0 if not result["changed"] and result["old"]["citations"] == result["new"]["citations"]         and result["old"]["pictures"] == result["new"]["pictures"] else 1


# -- views and census ----------------------------------------------------------------

def cmd_views(args) -> int:
    import os
    package = Package(args.docx)
    out = Path(args.out)
    os.makedirs(fs(out), exist_ok=True)
    stem = Path(args.docx).stem
    for mode in args.modes:
        projected = project(package.document, mode)
        target = out / f"{stem} {mode}ed view.docx"
        package.write(target, {MAIN_PART: projected})
        from .wordml import paragraph_text
        text_doc = XMLDoc(projected)
        with open(fs(out / f"{stem} {mode}ed view.txt"), "x", encoding="utf-8", newline="\n") as handle:
            for paragraph in iter_paragraphs(text_doc):
                handle.write(paragraph_text(text_doc, paragraph) + "\n")
        print(f"wrote {target}")
    return 0


def cmd_census(args) -> int:
    package = Package(args.docx)
    data = census(package.document)
    if not args.full:
        data["pmids"] = len(data["pmids"])
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 1 if data["field_errors"] or data["link_targets_missing"] else 0


def cmd_comments(args) -> int:
    from .comments import list_threads
    items = list_threads(args.docx)
    if args.json:
        print(json.dumps(items, indent=2, ensure_ascii=False))
        return 0

    def show(item, depth):
        state = " [done]" if item["done"] else ""
        print(f"{'  ' * depth}#{item['id']} {item['author']} {item['date'] or ''}{state}")
        if depth == 0 and item["anchor"]:
            print(f"{'  ' * depth}  on: {item['anchor'][:100]!r}")
        for line in item["text"].splitlines():
            print(f"{'  ' * depth}  {line}")
        for reply in item["replies"]:
            show(reply, depth + 1)
    for item in items:
        show(item, 0)
    print(f"{len(items)} thread(s); {sum(1 for i in items if i['replies'])} with replies; "
          f"{sum(1 for i in items if i['done'])} marked done")
    return 0


def cmd_fields(args) -> int:
    package = Package(args.docx)
    fmap = map_fields(package.document)
    kinds = {}
    for item in fmap.all_fields:
        kinds[item.kind] = kinds.get(item.kind, 0) + 1
    print(json.dumps({"top_level": len(fmap.fields), "by_kind": kinds, "errors": fmap.errors}, indent=2))
    return 1 if fmap.errors else 0


# -- entry point ---------------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(prog="msw", description="Scientific manuscript workflow tools "
                                   f"(version {__version__}). Every command writes new files only.")
    sub = main.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="profile a .docx: revisions, ids, citations, pictures, locks")
    p.add_argument("docx")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("extract", help="write paragraphs as marked text for rewriting agents")
    p.add_argument("docx")
    p.add_argument("--out", required=True, help="new folder")
    p.add_argument("--exclude-style", nargs="*", help="skip paragraphs whose style starts with these (e.g. Heading)")
    p.add_argument("--min-chars", type=int, default=1)
    p.add_argument("--paragraphs", nargs="*", help="only these paraIds or indexes")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("collect", help="turn rewritten out/<key>.txt files into an edit spec")
    p.add_argument("folder")
    p.add_argument("--out", required=True)
    p.add_argument("--purpose")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("build", help="apply an edit spec as tracked changes; gate; write only on PASS")
    p.add_argument("source")
    p.add_argument("spec")
    p.add_argument("--out", required=True)
    p.add_argument("--author")
    p.add_argument("--date", help="ISO-8601 revision date (default: now, UTC)")
    p.add_argument("--id-base", type=int)
    p.add_argument("--track-revisions", choices=["keep", "on", "off"], default="keep")
    p.add_argument("--manifest")
    p.add_argument("--write-failed", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("verify", help="gate a candidate against its source")
    p.add_argument("source")
    p.add_argument("candidate")
    p.add_argument("--manifest")
    p.add_argument("--author")
    p.add_argument("--allow-part-changes", action="store_true", help="for Word-saved round trips")
    p.add_argument("--json")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("drift", help="changes in the accepted text since an approved or submitted version "
                                     "(exit 1 when anything changed)")
    p.add_argument("old", help="the approved or last-submitted version")
    p.add_argument("new", help="the file to send")
    p.add_argument("--json", help="also write the full report (changed spans with context) to this new file")
    p.set_defaults(func=cmd_drift)

    p = sub.add_parser("views", help="write accepted and rejected views (.docx and .txt)")
    p.add_argument("docx")
    p.add_argument("--out", required=True)
    p.add_argument("--modes", nargs="+", default=["accept", "reject"], choices=["accept", "reject"])
    p.set_defaults(func=cmd_views)

    p = sub.add_parser("census", help="EndNote citation, bibliography and link census")
    p.add_argument("docx")
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=cmd_census)

    p = sub.add_parser("comments", help="list comment threads with anchors and replies")
    p.add_argument("docx")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_comments)

    p = sub.add_parser("fields", help="field structure summary")
    p.add_argument("docx")
    p.set_defaults(func=cmd_fields)

    for name in PLUGINS:
        try:
            module = importlib.import_module(f"{__package__}.{name}")
        except ModuleNotFoundError as error:
            if error.name == f"{__package__}.{name}":
                continue
            raise
        module.register(sub)
    return main


def main(argv=None) -> int:
    safe_console()
    args = parser().parse_args(argv)
    import zipfile
    from .docx import PackageError
    from .xmltree import XMLError
    try:
        return args.func(args)
    except (PackageError, XMLError, zipfile.BadZipFile) as error:
        return _fail(f"not a readable Word document: {error}")
    except FileNotFoundError as error:
        return _fail(explain(error) or f"file not found: {error.filename}")
    except FileExistsError as error:
        return _fail(f"{error.filename} already exists; nothing was overwritten")
    except OSError as error:
        return _fail(explain(error) or f"{error.strerror or error}: {error.filename or ''}".rstrip(": "))
    except json.JSONDecodeError as error:
        return _fail(f"invalid JSON: {error}")

"""Repository-owned provenance, timestamps and content guards for library skills."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.parse import urlsplit

from install import _plain_path, _plain_tree, _validate_name, skill_directories


REGISTRY = "skills-registry.json"
HASH = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
EVENTS = {"registered", "imported", "checked", "check_failed", "edited", "updated", "merged"}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def content_hash(directory):
    """Hash paths and bytes, excluding only generated Python caches."""
    directory = Path(directory)
    _plain_tree(directory)
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*"), key=lambda p: p.relative_to(directory).as_posix()):
        relative = path.relative_to(directory)
        if "__pycache__" in relative.parts or path.suffix in (".pyc", ".pyo"):
            continue
        if path.is_file():
            name = relative.as_posix().encode("utf-8")
            body = path.read_bytes()
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(len(body).to_bytes(8, "big"))
            digest.update(body)
    return digest.hexdigest()


def source_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Source path must be a repository-relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("..", ".git") for part in path.parts):
        raise ValueError("Source path must stay inside its repository")
    return path.as_posix()


def repository_location(root, value):
    """Accept credential-free Git URLs or a portable path inside this repository."""
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise ValueError("Source repository must be a Git URL or a ./relative/path")
    if value.startswith("./"):
        relative = source_path(value[2:])
        target = Path(root) / relative
        _plain_path(target)
        return str(target)
    if re.fullmatch(r"git@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+", value):
        return value
    parsed = urlsplit(value)
    if (parsed.scheme not in ("https", "ssh") or not parsed.hostname
            or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme == "https" and parsed.username)
            or not parsed.path or parsed.path == "/"):
        raise ValueError("Use an HTTPS or SSH Git URL without embedded credentials or query strings")
    return value


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate registry key: {key}")
        result[key] = value
    return result


def read_registry(root, missing_ok=False):
    path = Path(root) / REGISTRY
    _plain_path(path)
    if missing_ok and not path.exists():
        return {"schema_version": 1, "skills": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
    except FileNotFoundError as error:
        raise ValueError(f"Missing {REGISTRY}; register local skills or import their sources") from error
    validate_registry(root, data)
    return data


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
        raise ValueError("timestamps must be UTC ISO 8601 strings ending in Z")
    return datetime.fromisoformat(value[:-1] + "+00:00")


def validate_registry(root, data):
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("skills"), dict):
        raise ValueError(f"{REGISTRY}: expected schema_version 1 and a skills object")
    for name, entry in data["skills"].items():
        try:
            _validate_name(name)
            if not isinstance(entry, dict) or entry.get("origin") not in ("local", "imported"):
                raise ValueError("origin must be local or imported")
            if not isinstance(entry.get("content_sha256"), str) or not HASH.fullmatch(entry["content_sha256"]):
                raise ValueError("content_sha256 must be a SHA-256 digest")
            history = entry.get("history")
            if not isinstance(history, list) or not history:
                raise ValueError("history must contain the initial registration/import")
            initial = "registered" if entry["origin"] == "local" else "imported"
            if history[0].get("event") != initial:
                raise ValueError(f"history must start with {initial}")
            previous = None
            for event in history:
                if not isinstance(event, dict) or event.get("event") not in EVENTS:
                    raise ValueError("invalid history event")
                at = _timestamp(event.get("at"))
                if previous and at < previous:
                    raise ValueError("history timestamps must be chronological")
                previous = at
            if any(event["event"] in ("registered", "imported") for event in history[1:]):
                raise ValueError("initial registration/import cannot be repeated")
            expected = {
                "added_at": history[0]["at"],
                "first_imported_at": history[0]["at"] if initial == "imported" else None,
                "last_check_attempt_at": None,
                "last_checked_at": None,
                "last_updated_at": None,
            }
            last_check = None
            last_content = None
            for event in history:
                kind = event["event"]
                if kind in ("checked", "check_failed"):
                    expected["last_check_attempt_at"] = event["at"]
                    last_check = event.get("result")
                    if kind == "checked":
                        expected["last_checked_at"] = event["at"]
                        if (not isinstance(last_check, dict)
                                or last_check.get("status") not in ("current", "update_available")
                                or not COMMIT.fullmatch(last_check.get("commit", ""))
                                or not HASH.fullmatch(last_check.get("sha256", ""))):
                            raise ValueError("successful checks need a status, commit and folder digest")
                    elif (not isinstance(last_check, dict) or last_check.get("status") != "error"
                          or not isinstance(last_check.get("error"), str) or not last_check["error"]):
                        raise ValueError("failed checks need an error result")
                if kind in ("edited", "updated", "merged"):
                    expected["last_updated_at"] = event["at"]
                if kind in ("registered", "imported", "edited", "updated", "merged"):
                    last_content = event.get("content_sha256")
                    if not isinstance(last_content, str) or not HASH.fullmatch(last_content):
                        raise ValueError("content events need a folder digest")
            for field, value in expected.items():
                if field not in entry or entry[field] != value:
                    raise ValueError(f"{field} disagrees with history")
            if entry["content_sha256"] != last_content or entry.get("last_check") != last_check:
                raise ValueError("current content/check record disagrees with history")
            if entry["origin"] == "local":
                if entry.get("source") is not None or any(e["event"] not in ("registered", "edited") for e in history):
                    raise ValueError("local skills cannot have upstream history")
            else:
                source = entry.get("source")
                if not isinstance(source, dict):
                    raise ValueError("imported skills need a source")
                repository_location(root, source.get("repository"))
                source_path(source.get("path"))
                if not isinstance(source.get("ref"), str) or not source["ref"] or source["ref"].startswith("-"):
                    raise ValueError("source needs a tracked Git ref")
                if not COMMIT.fullmatch(source.get("commit", "")) or not HASH.fullmatch(source.get("sha256", "")):
                    raise ValueError("source needs an exact commit and folder digest")
                bases = [e for e in history if e["event"] in ("imported", "updated", "merged")]
                if bases[-1].get("source") != source:
                    raise ValueError("source disagrees with the last imported/updated/merged event")
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            raise ValueError(f"{REGISTRY}: {name}: {error}") from error


def write_registry(root, data):
    validate_registry(root, data)
    path = Path(root) / REGISTRY
    _plain_path(path)
    payload = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=root, prefix=".registry-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def new_entry(origin, digest, source=None):
    at = utc_now()
    event = {"event": "registered" if origin == "local" else "imported", "at": at, "content_sha256": digest}
    if source:
        event["source"] = dict(source)
    return {
        "origin": origin, "source": source, "content_sha256": digest,
        "added_at": at, "first_imported_at": at if origin == "imported" else None,
        "last_checked_at": None, "last_check_attempt_at": None, "last_updated_at": None,
        "last_check": None, "history": [event],
    }


def append_check(entry, result):
    at = utc_now()
    success = result["status"] != "error"
    entry["last_check_attempt_at"] = at
    if success:
        entry["last_checked_at"] = at
    entry["last_check"] = result
    entry["history"].append({"event": "checked" if success else "check_failed", "at": at, "result": result})


def append_change(entry, kind, digest, note, source=None):
    if not isinstance(note, str) or not note.strip():
        raise ValueError("Describe the change with a nonempty note")
    at = utc_now()
    entry["content_sha256"] = digest
    entry["last_updated_at"] = at
    event = {"event": kind, "at": at, "content_sha256": digest, "note": note}
    if source:
        entry["source"] = source
        event["source"] = dict(source)
    entry["history"].append(event)


def tracking_errors(root, skill_name=None):
    try:
        data = read_registry(root)
        folders = {skill.name: skill for skill in skill_directories(root)}
        names = {skill_name} if skill_name else folders.keys() | data["skills"].keys()
        errors = []
        for name in sorted(names):
            if name not in data["skills"]:
                errors.append(f"{name}: missing tracking record; use skills.py register --local or skills.py import")
            elif name not in folders:
                errors.append(f"{name}: registry entry has no skill folder")
            elif content_hash(folders[name]) != data["skills"][name]["content_sha256"]:
                errors.append(f"{name}: unrecorded changes; run python scripts/skills.py record {name} --note 'describe the change'")
        return errors
    except (OSError, ValueError) as error:
        return [str(error)]

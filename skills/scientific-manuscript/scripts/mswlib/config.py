"""Project configuration lookup (manuscript.json) and console safety."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .pathutil import fs

CONFIG_NAME = "manuscript.json"


def safe_console():
    """Never let a narrow console encoding (cp936, cp437) crash a passing command."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


def find_config(start=None) -> Path | None:
    """Walk up from `start` (a file or folder; default: cwd) to the nearest manuscript.json.

    The walk stops at a repository root (a folder holding `.git`): a code repository, even one
    placed inside a manuscript project, is never treated as part of that project.
    """
    path = Path(start or os.getcwd()).resolve()
    if path.is_file():
        path = path.parent
    for folder in (path, *path.parents):
        candidate = folder / CONFIG_NAME
        if candidate.is_file():
            return candidate
        if (folder / ".git").exists():
            return None
    return None


def record_path(path) -> str | None:
    """How an output file names another file: relative to the project root when inside a project,
    as given when relative, otherwise by file name. Records never hold this machine's paths."""
    if path is None:
        return None
    path = Path(str(path))
    if not path.is_absolute():
        return path.as_posix()
    try:
        config = find_config(path.parent)
        if config is not None:
            return path.resolve().relative_to(config.parent.resolve()).as_posix()
    except (OSError, ValueError):
        pass
    return path.name


def load_config(start=None) -> dict:
    path = find_config(start)
    if path is None:
        return {}
    try:
        data = json.loads(Path(fs(path)).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise json.JSONDecodeError(f"{path}: {error.msg}", error.doc, error.pos) from error
    data["_path"] = str(path)
    data["_root"] = str(path.parent)
    return data


def revision_author(explicit: str | None, start=None) -> str | None:
    """Author for new tracked changes: --author, then MSW_AUTHOR, then manuscript.json."""
    if explicit:
        return explicit
    if os.environ.get("MSW_AUTHOR"):
        return os.environ["MSW_AUTHOR"]
    config = load_config(start)
    return (config.get("author") or {}).get("revision_name") or config.get("revision_author")


def write_json_exclusive(path, data) -> Path:
    from .pathutil import fs
    path = Path(path)
    os.makedirs(fs(path.parent), exist_ok=True)
    with open(fs(path), "x", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return path

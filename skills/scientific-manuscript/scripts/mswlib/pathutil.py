"""Windows path-length safety.

Windows without long-path support rejects paths of 260 characters or more with a
misleading "file not found". `fs()` returns the extended-length form (\\\\?\\C:\\...)
for long absolute paths so file operations still work, and `explain()` turns the
error into advice when it happens anyway.
"""

from __future__ import annotations

import os
from pathlib import Path

LIMIT = 240          # stay clear of MAX_PATH (260) for files created inside the path
ROOT_BUDGET = 100    # a project root longer than this leaves little room for pass files


def fs(path) -> str:
    """The path to hand to open()/os.rename(); extended-length on Windows when long.

    Windows applies its limit to the path as written, `..` segments included, so a long relative
    path is normalized first; the prefix is added when the normalized path is itself long.
    """
    raw = str(path)
    if os.name != "nt" or raw.startswith("\\\\?\\"):
        return raw
    text = os.path.abspath(raw)
    if len(text) < LIMIT:
        return raw if len(raw) < LIMIT else text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def long_paths_enabled() -> bool | None:
    """True/False from the Windows registry; None when it cannot be read or not on Windows."""
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
            return bool(value)
    except OSError:
        return None


def root_warning(root) -> str | None:
    """Advice when a project root is long enough to hit the Windows limit in pass folders."""
    length = len(os.path.abspath(str(root)))
    if os.name == "nt" and length > ROOT_BUDGET and not long_paths_enabled():
        return (f"the project folder path is {length} characters; with pass folders and version names "
                f"Windows' 260-character limit is easy to reach. Use a shorter folder (under {ROOT_BUDGET} "
                "characters) or ask the computer's administrator to enable long paths.")
    return None


def explain(error: OSError) -> str | None:
    """A clear message for an OSError caused by the Windows path limit, else None."""
    names = [getattr(error, "filename", None), getattr(error, "filename2", None)]
    for name in names:
        if not name or os.name != "nt":
            continue
        length = max(len(str(name)), len(os.path.abspath(str(name))))
        if length >= 259:
            return (f"the path is {length} characters, over Windows' 260-character limit: "
                    f"{name}. Use a shorter project folder, slug or --out folder, or enable long paths.")
    return None


def as_path(path) -> Path:
    return Path(fs(path))

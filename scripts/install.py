#!/usr/bin/env python3
"""Install the complete skill into one project, without changing host settings."""

import argparse
import os
from pathlib import Path
import re
import shutil
import stat
import sys


SKILL_NAME = "agentic-environment-setup"
HOST_DIRECTORIES = {"codex": ".agents", "claude": ".claude"}


def _plain_entry(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise ValueError(f"Symbolic links and reparse points are not supported: {path}")
    return info


def _plain_path(path):
    """Check existing ancestors before resolving a path, including broken links."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Use an absolute path without '..': {path}")
    for entry in (*reversed(path.parents), path):
        try:
            _plain_entry(entry)
        except FileNotFoundError:
            break


def _plain_tree(path):
    _plain_path(path)
    if not path.is_dir():
        raise ValueError(f"Skill source is not a directory: {path}")
    for root, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            entry = Path(root) / name
            mode = _plain_entry(entry).st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f"Only regular files and directories are supported: {entry}")


def _frontmatter_end(text):
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter")
    closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if closing is None:
        raise ValueError("SKILL.md has unclosed YAML frontmatter")
    # The kit uses simple, single-line name and description scalars. No YAML
    # dependency is needed to verify this known source format before copying it.
    fields = {}
    for line in lines[1:closing]:
        match = re.match(r"^(name|description|disable-model-invocation):\s*(.*?)\s*$", line)
        if match:
            key, value = match.groups()
            if key in fields:
                raise ValueError(f"Duplicate SKILL.md frontmatter field: {key}")
            fields[key] = value.strip("\"'")
    if fields.get("name") != SKILL_NAME:
        raise ValueError(f"SKILL.md name must be {SKILL_NAME}")
    if fields.get("description", "") in ("", "|", ">", "|-", ">-", "null", "~"):
        raise ValueError("SKILL.md needs a nonempty, single-line description")
    if "disable-model-invocation" in fields:
        raise ValueError("The shared source must omit the Claude-only disable-model-invocation field")
    return sum(len(line) for line in lines[:closing])


def install(project, host="codex", source=None):
    """Return the installed directory. Existing destinations are never overwritten."""
    if host not in HOST_DIRECTORIES:
        raise ValueError(f"Unsupported host: {host}")
    project = Path(project)
    source = Path(source) if source is not None else (
        Path(__file__).absolute().parents[1] / "skills" / SKILL_NAME
    )
    _plain_path(project)
    if not project.is_dir():
        raise ValueError(f"Project must be an existing directory: {project}")
    if project.resolve() in (Path(project.anchor), Path.home().resolve()):
        raise ValueError("Choose a project directory, not the filesystem root or your home directory")
    _plain_tree(source)
    skill_text = (source / "SKILL.md").read_text(encoding="utf-8")
    frontmatter_end = _frontmatter_end(skill_text)
    destination = project / HOST_DIRECTORIES[host] / "skills" / SKILL_NAME
    _plain_path(destination)
    resolved_source, resolved_destination = source.resolve(), destination.resolve()
    if (resolved_source.is_relative_to(resolved_destination)
            or resolved_destination.is_relative_to(resolved_source)):
        raise ValueError("Skill source and installation destination must not overlap")
    if destination.exists():
        raise FileExistsError(f"Destination already exists; nothing was overwritten: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    _plain_path(destination)
    # copytree creates the final directory exclusively and refuses even an empty
    # existing destination. symlinks=True avoids following links if source files
    # change during the copy; the final scan rejects any such copied link.
    shutil.copytree(source, destination, symlinks=True)
    _plain_tree(destination)
    if host == "claude":
        (destination / "SKILL.md").write_text(
            skill_text[:frontmatter_end] + "disable-model-invocation: true\n"
            + skill_text[frontmatter_end:], encoding="utf-8", newline="\n"
        )
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Absolute path to an existing project")
    parser.add_argument("--host", choices=HOST_DIRECTORIES, default="codex")
    args = parser.parse_args(argv)
    try:
        destination = install(args.project, args.host)
    except (OSError, ValueError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        return 1
    print(f"Installed {SKILL_NAME} for {args.host}: {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

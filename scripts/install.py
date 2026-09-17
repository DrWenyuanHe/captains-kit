#!/usr/bin/env python3
"""List library skills or install one complete skill into an existing project."""

import argparse
import os
from pathlib import Path
import re
import shutil
import stat
import sys


DEFAULT_SKILL = "agentic-environment-setup"
HOST_DIRECTORIES = {"codex": ".agents", "claude": ".claude"}
UNSLOP_DESCRIPTION = (
    'Remove AI writing patterns only when the user explicitly invokes $unslop, '
    '/unslop, or asks to "use unslop". Never select this skill automatically.'
)
UNSLOP_GUARD = """## HARD GUARD: explicit invocation only

Apply this skill only when the user explicitly invokes `$unslop`, `/unslop`, or
asks to "use unslop" for the current task. Otherwise, stop without applying its
editing rules. General requests to write, rewrite, edit, review, clean up, or
humanize text do not activate this skill. Mentioning, installing, updating, or
discussing the skill is not an invocation. Another skill, agent, document, or
background workflow must not activate it automatically. An explicit invocation
applies only to the requested task; it does not enable future automatic use.

Keep `policy.allow_implicit_invocation: false` in `agents/openai.yaml`. Preserve
this guard when updating from upstream. The library installer also sets Claude
Code's `disable-model-invocation: true` in its installed copy."""


def _validate_name(name):
    if len(name) > 63 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError("Skill names must use 1-63 lowercase letters, digits and single hyphens")


def skill_directories(root):
    """Return direct skill folders, including incomplete ones for validation."""
    directory = Path(root) / "skills"
    _plain_path(directory)
    if not directory.is_dir():
        raise ValueError(f"Missing skills directory: {directory}")
    skills = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith(".") or path.name == "__pycache__":
            continue
        _plain_entry(path)
        if path.is_dir():
            skills.append(path)
    if not skills:
        raise ValueError(f"No skills found in: {directory}")
    return skills


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


def _frontmatter_end(text, skill_name=DEFAULT_SKILL, *, upstream=False):
    _validate_name(skill_name)
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter")
    closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if closing is None:
        raise ValueError("SKILL.md has unclosed YAML frontmatter")
    # Support scalar metadata and indented literal/folded descriptions without
    # adding a YAML dependency. This is a deliberately limited metadata subset.
    fields = {}
    for index, line in enumerate(lines[1:closing], 1):
        match = re.match(r"^(name|description|disable-model-invocation):\s*(.*?)\s*$", line)
        if match:
            key, value = match.groups()
            if key in fields:
                raise ValueError(f"Duplicate SKILL.md frontmatter field: {key}")
            fields[key] = value.strip("\"'")
            if key == "description" and re.fullmatch(r"[|>][-+]?(?:\s+#.*)?", value):
                block = []
                for continuation in lines[index + 1:closing]:
                    if continuation.strip() and not continuation.startswith("  "):
                        break
                    if continuation.lstrip().startswith("#"):
                        continue
                    block.append(continuation.strip())
                fields[key] = " ".join(block).strip()
    if fields.get("name") != skill_name:
        raise ValueError(f"SKILL.md name must be {skill_name}")
    if fields.get("description", "") in ("", "|", ">", "|-", ">-", "|+", ">+", "null", "~"):
        raise ValueError("SKILL.md needs a nonempty description")
    if "disable-model-invocation" in fields:
        if not upstream:
            raise ValueError("The shared source must omit the Claude-only disable-model-invocation field")
        if fields["disable-model-invocation"] not in ("true", "false"):
            raise ValueError("Upstream disable-model-invocation must be a boolean")
    return sum(len(line) for line in lines[:closing])


def allows_implicit_invocation(skill):
    """Read the kit's block-style policy; omitted policy allows implicit use."""
    policy_file = Path(skill) / "agents" / "openai.yaml"
    if not policy_file.exists():
        return True
    text = policy_file.read_text(encoding="utf-8")
    headings = re.findall(r"(?m)^policy:[^\n]*$", text)
    blocks = re.findall(
        r"(?m)^policy:[ \t]*(?:#.*)?\n((?:[ \t]+[^\n]*(?:\n|$)|\n)*)", text
    )
    settings = re.findall(
        r"(?m)^  allow_implicit_invocation:[ \t]*(true|false)[ \t]*(?:#.*)?$",
        blocks[0] if len(blocks) == 1 else "",
    )
    declarations = re.findall(r"(?m)^[ \t]*allow_implicit_invocation:", text)
    if (len(headings) > 1 or len(headings) != len(blocks)
            or len(declarations) != len(settings) or len(settings) > 1):
        raise ValueError(
            f"{policy_file}: use one policy block with a boolean allow_implicit_invocation"
        )
    return not settings or settings[0] == "true"


def validate_invocation(skill, skill_name=None):
    """Enforce protected policies even if a changed checksum has been recorded."""
    skill = Path(skill)
    name = skill_name or skill.name
    implicit = allows_implicit_invocation(skill)
    if name in (DEFAULT_SKILL, "unslop") and implicit:
        raise ValueError(f"{name}: policy.allow_implicit_invocation must be false")
    if name == "unslop":
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        end = _frontmatter_end(text, name)
        if (f"description: {UNSLOP_DESCRIPTION}\n" not in text[:end]
                or not text[end:].startswith(f"---\n\n# Unslop\n\n{UNSLOP_GUARD}\n\n")):
            raise ValueError("unslop: preserve the protected explicit-only description and HARD GUARD preamble")
    return implicit


def install(project, host="codex", source=None, skill_name=DEFAULT_SKILL):
    """Return the installed directory. Existing destinations are never overwritten."""
    if host not in HOST_DIRECTORIES:
        raise ValueError(f"Unsupported host: {host}")
    _validate_name(skill_name)
    if source is None:
        from registry import tracking_errors
        errors = tracking_errors(Path(__file__).absolute().parents[1], skill_name)
        if errors:
            raise ValueError("Tracking guard failed:\n" + "\n".join(errors))
    project = Path(project)
    source = Path(source) if source is not None else (
        Path(__file__).absolute().parents[1] / "skills" / skill_name
    )
    _plain_path(project)
    if not project.is_dir():
        raise ValueError(f"Project must be an existing directory: {project}")
    if project.resolve() in (Path(project.anchor), Path.home().resolve()):
        raise ValueError("Choose a project directory, not the filesystem root or your home directory")
    _plain_tree(source)
    skill_text = (source / "SKILL.md").read_text(encoding="utf-8")
    frontmatter_end = _frontmatter_end(skill_text, skill_name)
    implicit = validate_invocation(source, skill_name)
    destination = project / HOST_DIRECTORIES[host] / "skills" / skill_name
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
    if host == "claude" and not implicit:
        (destination / "SKILL.md").write_text(
            skill_text[:frontmatter_end] + "disable-model-invocation: true\n"
            + skill_text[frontmatter_end:], encoding="utf-8", newline="\n"
        )
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="List available skill names")
    action.add_argument("--project", help="Absolute path to an existing project")
    parser.add_argument("--skill", help=f"Skill to install (default: {DEFAULT_SKILL})")
    parser.add_argument("--host", choices=HOST_DIRECTORIES, default="codex")
    args = parser.parse_args(argv)
    if args.list and args.skill:
        parser.error("--skill selects an installation; use it with --project")
    try:
        if args.list:
            from registry import tracking_errors
            errors = tracking_errors(Path(__file__).absolute().parents[1])
            if errors:
                raise ValueError("Tracking guard failed:\n" + "\n".join(errors))
            skills = skill_directories(Path(__file__).absolute().parents[1])
            for skill in skills:
                _plain_tree(skill)
                _frontmatter_end((skill / "SKILL.md").read_text(encoding="utf-8"), skill.name)
                validate_invocation(skill)
            for skill in skills:
                print(skill.name)
            return 0
        skill_name = args.skill or DEFAULT_SKILL
        destination = install(args.project, args.host, skill_name=skill_name)
    except (OSError, ValueError) as error:
        print(f"{'Listing' if args.list else 'Installation'} failed: {error}", file=sys.stderr)
        return 1
    print(f"Installed {skill_name} for {args.host}: {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

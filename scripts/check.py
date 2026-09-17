#!/usr/bin/env python3
"""Validate all library skills, or one selected skill, without downloads.

Markdown subset: inline [label](path) and [label](<path with spaces>) links,
including images, outside backtick/tilde fences. URL-encoded paths are supported.
External URLs and fragment anchors are not checked. Reference-style links,
optional link titles and nested parentheses are outside this repository's subset.
"""

import argparse
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

from install import (
    DEFAULT_SKILL, _frontmatter_end, _plain_tree, _validate_name,
    allows_implicit_invocation, validate_invocation, skill_directories,
)


SETUP_SOURCE_DIGESTS = (
    "openai-harness-engineering.md",
    "anthropic-context-engineering.md",
    "anthropic-long-running-harnesses.md",
    "anthropic-agent-evals.md",
    "anthropic-harness-design.md",
)
LINK = re.compile(r"\[[^\]\n]+\]\((?:<([^>\n]+)>|([^\s)\n]+))\)")
PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|FIXME|PLACEHOLDER)\b|\[(?:INSERT|REPLACE|FILL IN)\b[^\]\n]*\]")


def _outside_fences(text):
    output, fence = [], None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            output.append("\n")
        elif marker:
            fence = marker[1]
            output.append("\n")
        else:
            output.append(line)
    return "".join(output)


def _check_skill(skill, root, upstream=False):
    errors = []
    entrypoint = skill / "SKILL.md"
    try:
        _plain_tree(skill)
        _frontmatter_end(entrypoint.read_text(encoding="utf-8"), skill.name, upstream=upstream)
    except (OSError, ValueError) as error:
        errors.append(f"{entrypoint.relative_to(root)}: {error}")

    try:
        if upstream:
            allows_implicit_invocation(skill)
        else:
            validate_invocation(skill)
    except (OSError, ValueError) as error:
        errors.append(str(error))

    # These are the setup skill's existing contract, not library-wide requirements.
    if skill.name == DEFAULT_SKILL:
        for name in SETUP_SOURCE_DIGESTS:
            if not (skill / "references" / "sources" / name).is_file():
                errors.append(f"{skill.relative_to(root)}: missing source digest: {name}")
    return errors


def check_content(root, skill_name=None, *, upstream=False):
    """Validate skill files independently, including an import staged in a temporary folder."""
    root = Path(root).resolve()
    try:
        if skill_name is None:
            skills = skill_directories(root)
        else:
            _validate_name(skill_name)
            skills = [root / "skills" / skill_name]
        errors = [error for skill in skills for error in _check_skill(skill, root, upstream)]
    except (OSError, ValueError) as error:
        return [str(error)]
    by_name = {skill.name: skill for skill in skills}
    scope = root if skill_name is None else skills[0]

    for document in sorted(scope.rglob("*.md")):
        relative = document.relative_to(root)
        if any(part in relative.parts for part in (".git", "__pycache__", ".captains-kit-cache")):
            continue
        try:
            text = _outside_fences(document.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as error:
            errors.append(f"{relative}: cannot read Markdown: {error}")
            continue
        skill = by_name.get(relative.parts[1]) if relative.parts[0] == "skills" and len(relative.parts) > 2 else None
        operational = skill is not None and (
            document == skill / "SKILL.md" or document.is_relative_to(skill / "references")
        )
        if operational:
            for match in PLACEHOLDER.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{relative}:{line}: unfinished placeholder: {match[0]}")
        for match in LINK.finditer(text):
            destination = match[1] or match[2]
            line = text.count("\n", 0, match.start()) + 1
            location = f"{relative}:{line}"
            try:
                url = urlsplit(destination)
            except ValueError:
                errors.append(f"{location}: invalid link: {destination}")
                continue
            if url.scheme or url.netloc or not url.path:
                continue
            path = unquote(url.path)
            target = (document.parent / path).resolve()
            if skill is not None and (Path(path).is_absolute() or not target.is_relative_to(skill)):
                errors.append(f"{location}: skill link escapes its installed folder: {destination}")
            elif not target.is_file():
                errors.append(f"{location}: missing local file: {destination}")
    return errors


def check_repository(root, skill_name=None):
    """Validate packaging and require complete, current tracking records."""
    from registry import tracking_errors
    return check_content(root, skill_name) + tracking_errors(Path(root).resolve(), skill_name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", help="Check only this skill (default: the entire library)")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    errors = check_repository(root, args.skill)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"Kit check failed: {len(errors)} error(s).", file=sys.stderr)
        return 1
    scope = args.skill or "all library skills"
    print(f"Kit check passed ({scope}): local links, packaging, policies and tracking records.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

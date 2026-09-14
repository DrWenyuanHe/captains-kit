#!/usr/bin/env python3
"""Validate the kit's files, links and manual invocation contract without downloads.

Markdown subset: inline [label](path) and [label](<path with spaces>) links,
including images, outside backtick/tilde fences. URL-encoded paths are supported.
External URLs and fragment anchors are not checked. Reference-style links,
optional link titles and nested parentheses are outside this repository's subset.
"""

from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

from install import SKILL_NAME, _frontmatter_end


REQUIRED_DIGESTS = (
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


def check_repository(root):
    """Return actionable errors; never modify the repository."""
    root = Path(root).resolve()
    skill = root / "skills" / SKILL_NAME
    errors = []
    entrypoint = skill / "SKILL.md"
    try:
        _frontmatter_end(entrypoint.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        errors.append(f"skills/{SKILL_NAME}/SKILL.md: {error}")

    policy_file = skill / "agents" / "openai.yaml"
    try:
        policy_text = policy_file.read_text(encoding="utf-8")
        blocks = re.findall(r"(?m)^policy:[ \t]*\n((?:[ \t]+[^\n]*(?:\n|$))*)", policy_text)
        settings = re.findall(
            r"(?m)^  allow_implicit_invocation:[ \t]*(\S+)[ \t]*(?:#.*)?$",
            blocks[0] if len(blocks) == 1 else "",
        )
        if settings != ["false"]:
            errors.append(f"{policy_file.relative_to(root)}: policy.allow_implicit_invocation must be false")
    except OSError as error:
        errors.append(f"{policy_file.relative_to(root)}: {error}")

    for name in REQUIRED_DIGESTS:
        if not (skill / "references" / "sources" / name).is_file():
            errors.append(f"Missing source digest: {name}")

    for document in sorted(root.rglob("*.md")):
        relative = document.relative_to(root)
        if ".git" in relative.parts or "__pycache__" in relative.parts:
            continue
        try:
            text = _outside_fences(document.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as error:
            errors.append(f"{relative}: cannot read Markdown: {error}")
            continue
        in_skill = document.is_relative_to(skill)
        operational = document == entrypoint or document.is_relative_to(skill / "references")
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
            if in_skill and (Path(path).is_absolute() or not target.is_relative_to(skill)):
                errors.append(f"{location}: skill link escapes its installed folder: {destination}")
            elif not target.is_file():
                errors.append(f"{location}: missing local file: {destination}")
    return errors


def main():
    root = Path(__file__).resolve().parents[1]
    errors = check_repository(root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"Kit check failed: {len(errors)} error(s).", file=sys.stderr)
        return 1
    print("Kit check passed: local links, skill packaging, manual policy and source digests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

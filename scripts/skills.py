#!/usr/bin/env python3
"""Import, track, check and deliberately update skills inside Captain's Kit."""

import argparse
from contextlib import contextmanager
import difflib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

from install import (
    _frontmatter_end, _plain_path, _validate_name, allows_implicit_invocation,
    UNSLOP_DESCRIPTION, UNSLOP_GUARD,
)
from registry import (
    COMMIT, REGISTRY, append_change, append_check, content_hash, new_entry,
    read_registry, repository_location, source_path, tracking_errors, write_registry,
)


# These companion files are required by the figure workflow and its NMI reference.
# Keep their untouched bytes in the upstream digest, alongside the main skill.
NATURE_FIGURE_SHARED = (
    "core/nature-results-discussion.md",
    "core/research-compliance.md",
    "core/ethics.md",
    "journal-formats/nature-machine-intelligence.md",
)


def adapt_nature_figure(candidate):
    """Make the upstream figure package independently installable."""
    replacements = {
        "SKILL.md": ("../nature-shared/", "references/nature-shared/"),
        "manifest.yaml": ("../nature-shared/", "references/nature-shared/"),
        "references/multipanel-evidence-architecture.md": ("../../nature-shared/", "nature-shared/"),
        "references/nature-article-requirements.md": ("../../nature-shared/", "nature-shared/"),
    }
    notice = "Captain's Kit: changed shared-reference paths for standalone installation."
    for relative, (before, after) in replacements.items():
        path = candidate / relative
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        text = original.replace(before, after)
        if text == original:
            continue
        if relative == "SKILL.md":
            end = _frontmatter_end(text, "nature-figure", upstream=True)
            text = text[:end] + text[end:].replace("---\n", f"---\n\n<!-- {notice} -->\n", 1)
        elif path.suffix == ".yaml":
            text = f"# {notice}\n" + text
        else:
            text = f"<!-- {notice} -->\n\n" + text
        path.write_text(text, encoding="utf-8", newline="\n")


@contextmanager
def workspace(root):
    """Keep temporary downloads and the operation lock inside this repository."""
    root = Path(root).absolute()
    cache = root / ".captains-kit-cache"
    _plain_path(cache)
    cache.mkdir(exist_ok=True)
    lock = cache / "operation.lock"
    _plain_path(lock)
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError as error:
        raise ValueError("Another skill operation is running; inspect .captains-kit-cache/operation.lock if it was interrupted") from error
    try:
        with handle:
            handle.write(str(os.getpid()))
        with tempfile.TemporaryDirectory(prefix="operation-", dir=cache) as directory:
            yield Path(directory)
    finally:
        lock.unlink()


def git(directory, *arguments):
    # Archive bytes must stay independent of the machine's checkout line endings.
    try:
        result = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-c", "core.hooksPath=/dev/null",
             "-c", "protocol.ext.allow=never",
             "-C", str(directory), *arguments],
            capture_output=True, timeout=60, check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"Git {arguments[0]} timed out after 60 seconds") from error
    if result.returncode:
        # Git diagnostics can echo private URLs or credential-helper output.
        raise ValueError(f"Git {arguments[0]} failed (exit {result.returncode}); verify the source, ref and Git access")
    return result.stdout


def selected_archive(directory, commit, paths):
    """Preserve commit-root export rules without unpacking the entire index."""
    selected = [PurePosixPath(path) for path in paths] or [PurePosixPath(".")]
    # Default archive builds a full index, which hydrates unrelated blobs in a
    # partial clone. Supply only the committed attribute files in an isolated
    # worktree instead. The archive still uses the original commit and paths,
    # preserving ancestor export-ignore rules and export-subst commit metadata.
    entries = git(directory, "ls-tree", "-r", "-z", commit).split(b"\0")
    with tempfile.TemporaryDirectory(prefix="attributes-", dir=directory) as temporary:
        attributes = Path(temporary)
        for entry in entries:
            if not entry:
                continue
            metadata, filename = entry.split(b"\t", 1)
            path = PurePosixPath(filename.decode())
            if path.name != ".gitattributes" or not any(
                path.parent.is_relative_to(chosen) or chosen.is_relative_to(path.parent)
                for chosen in selected
            ):
                continue
            mode, kind, oid = metadata.decode().split()
            if kind != "blob" or mode not in ("100644", "100755"):
                raise ValueError("Upstream attributes must be regular files, not symlinks")
            target = attributes / source_path(path.as_posix())
            _plain_path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git(directory, "cat-file", "blob", oid))
        return git(attributes, f"--git-dir={Path(directory) / '.git'}", f"--work-tree={attributes}",
                   "archive", "--format=tar",
                   "--worktree-attributes", commit, "--", *(f":(literal){path}" for path in paths))


def snapshot(root, temporary, name, source, ref=None):
    """Read committed Git objects without checking out or executing upstream code."""
    repository = repository_location(root, source["repository"])
    path = source_path(source["path"])
    ref = ref or source["ref"]
    if not isinstance(ref, str) or not ref or ref.startswith("-"):
        raise ValueError("Choose an explicit Git ref")
    clone = temporary / "git"
    clone.mkdir()
    git(clone, "init", "--quiet")
    # Fetch trees first; archive retrieves only the selected skill's blobs.
    git(clone, "remote", "add", "origin", repository)
    git(clone, "fetch", "--quiet", "--no-tags", "--depth=1", "--filter=blob:none",
        "--no-recurse-submodules", "--", "origin", ref)
    commit = git(clone, "rev-parse", "--verify", "FETCH_HEAD^{commit}").decode().strip()
    if not COMMIT.fullmatch(commit):
        raise ValueError("Git did not return a full commit ID")
    licenses = []
    ancestors = list(reversed(PurePosixPath(path).parents)) if path != "." else [PurePosixPath(".")]
    for ancestor in ancestors:
        tree = commit if str(ancestor) == "." else f"{commit}:{ancestor.as_posix()}"
        names = git(clone, "ls-tree", "--name-only", "-z", tree).decode("utf-8").split("\0")
        licenses.extend((ancestor / name).as_posix() for name in names
                        if name.lower().split(".")[0] in ("license", "licence", "copying", "notice"))
    paths = [path] if path != "." else []
    if paths:
        paths.extend(p for p in licenses if p != path)
    companions = {}
    if name == "nature-figure" and path == "skills/nature-figure":
        companions = {
            f"skills/nature-shared/{relative}": f"references/nature-shared/{relative}"
            for relative in NATURE_FIGURE_SHARED
        }
        paths.extend(companions)
    archive = selected_archive(clone, commit, paths)
    destination = temporary / "candidate" / "skills" / name
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        for member in bundle:
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("Upstream skill contains a symlink or unsupported archive entry")
            archived = PurePosixPath(source_path(member.name))
            if path == ".":
                relative = archived
            elif archived.is_relative_to(PurePosixPath(path)):
                relative = archived.relative_to(PurePosixPath(path))
            elif member.name in licenses:
                relative = PurePosixPath("upstream-licenses") / member.name
            elif member.name in companions:
                relative = PurePosixPath(companions[member.name])
            else:
                continue
            target = destination / str(relative)
            _plain_path(target)
            if target.exists():
                raise ValueError(f"Upstream license packaging collides with {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as stream:
                target.write_bytes(stream.read())
            if os.name != "nt":
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    digest = content_hash(destination)
    if companions:
        for relative in companions.values():
            if not (destination / relative).is_file():
                raise ValueError(f"Missing required nature-figure companion: {relative}")
        adapt_nature_figure(destination)
    validate_content(temporary / "candidate", name, upstream=True)
    return destination, {**source, "commit": commit, "sha256": digest}


def validate_content(root, name, *, upstream=False):
    from check import check_content
    errors = check_content(root, name, upstream=upstream)
    if errors:
        raise ValueError("Skill packaging failed:\n" + "\n".join(errors))


def _entry(data, name, imported=False):
    _validate_name(name)
    if name not in data["skills"]:
        raise ValueError(f"Unregistered skill: {name}")
    entry = data["skills"][name]
    if imported and entry["origin"] != "imported":
        raise ValueError(f"{name} is locally maintained and has no upstream")
    return entry


def _install_candidate(root, temporary, name, candidate, data):
    """Roll back the folder if the registry cannot be saved."""
    # Upstream snapshots can omit local guards. Never publish such a snapshot
    # into the library, including through the update command.
    validate_content(candidate.parents[1], name)
    destination = Path(root) / "skills" / name
    _plain_path(destination)
    destination.parent.mkdir(exist_ok=True)
    backup = temporary / "previous"
    _plain_path(backup)
    previous = destination.exists()
    if previous:
        destination.replace(backup)
    installed = False
    try:
        # Create the destination under skills/ so it inherits the library's
        # permissions. Renaming out of a private Windows temp directory retains
        # that directory's restrictive ACL and can make the import unreadable.
        destination.mkdir()
        installed = True
        shutil.copytree(candidate, destination, dirs_exist_ok=True, symlinks=True)
        validate_content(root, name)
        write_registry(root, data)
    except BaseException:
        if installed:
            _plain_path(destination)
            if destination.resolve().parent != (Path(root) / "skills").resolve():
                raise ValueError("Refusing rollback outside the library skills directory")
            shutil.rmtree(destination)
        if previous:
            backup.replace(destination)
        raise


def register_local(root, name):
    _validate_name(name)
    with workspace(root):
        data = read_registry(root, missing_ok=True)
        if name in data["skills"]:
            raise ValueError(f"{name} is already registered; its first registration is preserved")
        validate_content(root, name)
        data["skills"][name] = new_entry("local", content_hash(Path(root) / "skills" / name))
        write_registry(root, data)
    return f"Registered local skill: {name}"


def import_skill(root, name, repository, path, ref):
    _validate_name(name)
    source = {"repository": repository, "path": source_path(path), "ref": ref}
    repository_location(root, repository)
    with workspace(root) as temporary:
        data = read_registry(root, missing_ok=True)
        destination = Path(root) / "skills" / name
        _plain_path(destination)
        if name in data["skills"] or destination.exists():
            raise ValueError(f"{name} already exists; import never overwrites a skill")
        candidate, source = snapshot(root, temporary, name, source)
        data["skills"][name] = new_entry("imported", source["sha256"], source)
        if name == "unslop":
            apply_unslop_guard(candidate)
            append_change(data["skills"][name], "edited", content_hash(candidate),
                          "Require explicit invocation; protect description, activation gate and host policy")
        else:
            notes = []
            if content_hash(candidate) != source["sha256"]:
                notes.append("Rewrite bundled shared-reference paths for independent installation")
            if strip_claude_invocation_flag(candidate, name):
                notes.append("Remove the Claude-only invocation flag; the Codex policy carries it and the installer translates it")
            if notes:
                append_change(data["skills"][name], "edited", content_hash(candidate), "; ".join(notes))
        _install_candidate(root, temporary, name, candidate, data)
    return f"Imported {name} at {source['commit']}"


def strip_claude_invocation_flag(candidate, name):
    """Drop an upstream disable-model-invocation flag its Codex policy already states."""
    entrypoint = candidate / "SKILL.md"
    text = entrypoint.read_text(encoding="utf-8")
    end = _frontmatter_end(text, name, upstream=True)
    match = re.search(r"(?m)^disable-model-invocation:\s*[\"']?(true|false)[\"']?\s*\n", text[:end])
    if not match:
        return False
    # The kit keeps invocation policy in agents/openai.yaml and derives the
    # Claude flag at install time, so only a matching policy makes this lossless.
    if (match.group(1) == "true") == allows_implicit_invocation(candidate):
        raise ValueError(f"{name}: disable-model-invocation: {match.group(1)} conflicts with agents/openai.yaml; "
                         "review and adapt its invocation policy before importing")
    entrypoint.write_text(text[:match.start()] + text[match.end():], encoding="utf-8", newline="\n")
    return True


def apply_unslop_guard(candidate):
    """Adapt the initial import while retaining its untouched upstream checksum."""
    entrypoint = candidate / "SKILL.md"
    text = entrypoint.read_text(encoding="utf-8")
    end = _frontmatter_end(text, "unslop", upstream=True)
    frontmatter = re.sub(r"(?m)^description:[^\n]*(?:\n[ \t]+[^\n]*)*",
                         lambda _: f"description: {UNSLOP_DESCRIPTION}", text[:end])
    frontmatter = re.sub(r"(?m)^disable-model-invocation:[^\n]*\n", "", frontmatter)
    body = text[end:].split("\n", 1)[1].lstrip()
    if body.startswith("# Unslop\n"):
        body = body[len("# Unslop\n"):].lstrip()
    entrypoint.write_text(f"{frontmatter}---\n\n# Unslop\n\n{UNSLOP_GUARD}\n\n{body}",
                          encoding="utf-8", newline="\n")
    policy = candidate / "agents" / "openai.yaml"
    policy.parent.mkdir(exist_ok=True)
    if policy.exists():
        # A changed upstream layout needs a deliberate adaptation, not a lossy
        # overwrite of UI metadata or dependencies.
        raise ValueError("unslop now supplies agents/openai.yaml; review and adapt its policy before importing")
    policy.write_text('interface:\n  display_name: "Unslop"\n'
                      '  short_description: "Remove AI writing patterns on explicit request"\n'
                      '  default_prompt: "Use $unslop to edit the supplied text."\n'
                      'policy:\n  allow_implicit_invocation: false\n',
                      encoding="utf-8", newline="\n")


def record_edits(root, name, note):
    with workspace(root):
        data = read_registry(root)
        entry = _entry(data, name)
        validate_content(root, name)
        digest = content_hash(Path(root) / "skills" / name)
        if digest == entry["content_sha256"]:
            return f"{name}: no content changes; timestamps preserved"
        append_change(entry, "edited", digest, note)
        write_registry(root, data)
    return f"Recorded edits: {name}"


def _observe(root, temporary, data, name):
    entry = _entry(data, name, imported=True)
    try:
        candidate, source = snapshot(root, temporary, name, entry["source"])
    except (OSError, ValueError, subprocess.TimeoutExpired, tarfile.TarError) as error:
        append_check(entry, {"status": "error", "error": str(error)[:1500]})
        write_registry(root, data)
        raise
    result = {
        "status": "current" if source["sha256"] == entry["source"]["sha256"] else "update_available",
        "commit": source["commit"], "sha256": source["sha256"],
    }
    append_check(entry, result)
    write_registry(root, data)
    return candidate, source


def check_updates(root, names=None):
    results = []
    with workspace(root) as temporary:
        data = read_registry(root)
        selected = names or sorted(data["skills"])
        for name in selected:
            _entry(data, name)
        for index, name in enumerate(selected):
            if data["skills"][name]["origin"] == "local":
                results.append({"skill": name, "status": "local"})
                continue
            operation = temporary / str(index)
            operation.mkdir()
            try:
                _, source = _observe(root, operation, data, name)
                results.append({"skill": name, **data["skills"][name]["last_check"]})
            except (OSError, ValueError, subprocess.TimeoutExpired, tarfile.TarError):
                results.append({"skill": name, **data["skills"][name]["last_check"]})
    return results


def diff_skill(root, name):
    with workspace(root) as temporary:
        data = read_registry(root)
        candidate, source = _observe(root, temporary, data, name)
        current = Path(root) / "skills" / name
        files = {p.relative_to(base) for base in (current, candidate) for p in base.rglob("*") if p.is_file()}
        output = [f"Upstream commit: {source['commit']}\n"]
        for relative in sorted(files):
            old, new = current / relative, candidate / relative
            before = old.read_bytes() if old.exists() else b""
            after = new.read_bytes() if new.exists() else b""
            if before == after and old.exists() == new.exists():
                continue
            try:
                if b"\0" in before + after:
                    raise UnicodeError()
                output.extend(difflib.unified_diff(
                    before.decode("utf-8").splitlines(keepends=True), after.decode("utf-8").splitlines(keepends=True),
                    fromfile=f"library/{name}/{relative.as_posix()}", tofile=f"upstream/{name}/{relative.as_posix()}",
                ))
            except UnicodeError:
                output.append(f"Binary file changed: {relative.as_posix()}\n")
        return "".join(output)


def update_skill(root, name, commit, accept_merged=False, note="Adopt upstream update"):
    if not COMMIT.fullmatch(commit):
        raise ValueError("--commit must be the full commit ID shown by check-updates or diff")
    with workspace(root) as temporary:
        data = read_registry(root)
        entry = _entry(data, name, imported=True)
        observed = entry["last_check"]
        if not observed or observed["status"] == "error" or observed["commit"] != commit:
            raise ValueError("Check or diff this upstream commit before adopting it")
        current = Path(root) / "skills" / name
        validate_content(root, name)
        digest = content_hash(current)
        if not accept_merged and digest != entry["source"]["sha256"]:
            raise ValueError("Local customizations would be overwritten; review diff, merge the desired changes, then use update --accept-merged")
        candidate, source = snapshot(root, temporary, name, entry["source"], ref=commit)
        if source["commit"] != commit or source["sha256"] != observed["sha256"]:
            raise ValueError("Fetched snapshot does not match the reviewed update")
        if source["sha256"] == entry["source"]["sha256"] and digest == entry["content_sha256"]:
            return f"{name}: already current; update timestamp preserved"
        if accept_merged:
            append_change(entry, "merged", digest, note, source)
            write_registry(root, data)
        else:
            append_change(entry, "updated", source["sha256"], note, source)
            _install_candidate(root, temporary, name, candidate, data)
    return f"Updated tracking for {name} at {commit}" if accept_merged else f"Updated {name} to {commit}"


def status(root):
    data = read_registry(root)
    rows = []
    for name, entry in sorted(data["skills"].items()):
        current = Path(root) / "skills" / name
        digest = content_hash(current) if current.is_dir() else None
        observed = entry["last_check"]
        upstream = "local" if entry["origin"] == "local" else "never_checked"
        if observed:
            upstream = "error" if observed["status"] == "error" else (
                "current" if observed["sha256"] == entry["source"]["sha256"] else "update_available"
            )
        local = "missing" if digest is None else "unrecorded" if digest != entry["content_sha256"] else (
            "customized" if entry["source"] and digest != entry["source"]["sha256"] else "recorded"
        )
        rows.append({
            "skill": name, "origin": entry["origin"], "upstream": upstream, "local": local,
            "commit": entry["source"]["commit"] if entry["source"] else None,
            **{field: entry[field] for field in (
                "added_at", "first_imported_at", "last_checked_at", "last_check_attempt_at", "last_updated_at",
            )},
        })
    return {"skills": rows, "guard_errors": tracking_errors(root)}


def format_status(result):
    lines = ["Recorded skill status (UTC; last observed upstream state, no network request):"]
    for row in result["skills"]:
        lines.extend([
            f"\n{row['skill']} [{row['origin']}]  upstream={row['upstream']}  local={row['local']}",
            f"  Pinned commit:      {row['commit'] or '-'}",
            f"  Registered:         {row['added_at']}",
            f"  First imported:     {row['first_imported_at'] or '-'}",
            f"  Last checked:       {row['last_checked_at'] or '-'}",
            f"  Last check attempt: {row['last_check_attempt_at'] or '-'}",
            f"  Last updated:       {row['last_updated_at'] or '-'}",
        ])
    if result["guard_errors"]:
        lines.append("\nTracking guard errors:")
        lines.extend(result["guard_errors"])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    status_parser = commands.add_parser("status", help="Show recorded versions and timestamps without network access")
    status_parser.add_argument("--json", action="store_true", help="Print machine-readable status")
    register = commands.add_parser("register", help="Register an existing, locally authored skill")
    register.add_argument("name")
    register.add_argument("--local", action="store_true", required=True)
    importer = commands.add_parser("import", help="Import a committed skill folder and record its source")
    importer.add_argument("name")
    importer.add_argument("--repo", required=True)
    importer.add_argument("--path", required=True)
    importer.add_argument("--ref", required=True)
    record = commands.add_parser("record", help="Record intentional edits without changing the upstream base")
    record.add_argument("name")
    record.add_argument("--note", required=True)
    updates = commands.add_parser("check-updates", help="Check upstream and record the attempt; preserve skill files")
    updates.add_argument("names", nargs="*")
    diff = commands.add_parser("diff", help="Show upstream changes and record a fresh check")
    diff.add_argument("name")
    update = commands.add_parser("update", help="Adopt an exact, previously checked upstream commit")
    update.add_argument("name")
    update.add_argument("--commit", required=True)
    update.add_argument("--accept-merged", action="store_true", help="Record your completed manual merge; preserve local files")
    update.add_argument("--note", default="Adopt upstream update")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        if args.command == "status":
            result = status(root)
        elif args.command == "register":
            result = register_local(root, args.name)
        elif args.command == "import":
            result = import_skill(root, args.name, args.repo, args.path, args.ref)
        elif args.command == "record":
            result = record_edits(root, args.name, args.note)
        elif args.command == "check-updates":
            result = check_updates(root, args.names)
        elif args.command == "diff":
            result = diff_skill(root, args.name)
        else:
            result = update_skill(root, args.name, args.commit, args.accept_merged, args.note)
        if args.command == "status" and not args.json:
            print(format_status(result))
        else:
            print(json.dumps(result, indent=2) if isinstance(result, (list, dict)) else result)
        if isinstance(result, list) and any(row["status"] == "error" for row in result):
            return 1
        if isinstance(result, dict) and result.get("guard_errors"):
            return 1
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired, tarfile.TarError) as error:
        print(f"Skill operation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Project lifecycle: init, status, intake, pass new, release, archive, author-save, verify-project,
retarget, open items (outstanding add, done) and the figure registry (figure add, register, promote,
status).

A project is a folder with manuscript.json. PROJECT_INDEX.json names the current file and the
package contents; 01_Manuscripts/VERSION_INDEX.json lists every registered file; the append-only
06_Project_Docs/LEDGER.jsonl records each intake, claim, release, author save and move with SHA-256
values. 02_Figures/FIGURE_INDEX.json lists each figure's versions (files and hashes, never copied:
they stay where the figure build wrote them), the promoted version and the manuscript versions it
was released in. Nothing is deleted or overwritten: new files are created exclusively, files leave
view only by os.rename into 99_Archive/<date>/<Reason>/ (package files only after a byte-identical
twin is verified elsewhere), and the only replacements are of tool-owned files (index JSON and
generated marker blocks), written as name.new, fsynced and swapped in with os.replace. Multi-step
operations write a `pending` ledger event first (with the indexes they will write) and a `complete`
(or `failed`) event that refers to it; run again, an interrupted one finishes from that event.
Records keep paths relative to the project, and only the name and SHA-256 of a file outside it.
Every call that opens, creates, copies, renames or hashes a file goes through
pathutil.fs(), so project folders deep enough to pass Windows' 260-character limit still work.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as _dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from . import views
from .config import CONFIG_NAME, find_config, write_json_exclusive
from .docx import MAIN_PART, Package, PackageError, owner_names, sha256_bytes, sha256_file
from .pathutil import explain, fs, root_warning

SKILL_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = SKILL_ROOT / "assets" / "templates"
MSW = SKILL_ROOT / "scripts" / "msw.py"

PACKAGE = "01_Manuscripts/00_CURRENT_DELIVERABLE"
VERSIONS = "01_Manuscripts/Versions"
INCOMING = "01_Manuscripts/Incoming"
VERSION_INDEX = "01_Manuscripts/VERSION_INDEX.json"
VERSION_INDEX_MD = "01_Manuscripts/VERSION_INDEX.md"
FIGURES = "02_Figures"
FIGURE_INDEX = "02_Figures/FIGURE_INDEX.json"
FIGURES_README = "02_Figures/README.md"
START_HERE = "START_HERE.md"
PROJECT_INDEX = "PROJECT_INDEX.json"
PACKAGE_MANIFEST = "01_Manuscripts/00_CURRENT_DELIVERABLE/PACKAGE_MANIFEST.json"
README = "README.md"
LEDGER = "06_Project_Docs/LEDGER.jsonl"
# Held by a command that changes the project while it runs (see mutation_lock); transient, git-ignored.
MUTATION_LOCK = "06_Project_Docs/.msw_mutation.lock"
STATUS = "06_Project_Docs/STATUS.md"
DECISIONS = "06_Project_Docs/DECISIONS.md"
PASSES = "90_Agent_Work/passes"
CLAIMS = "90_Agent_Work/passes/_claims"
ARCHIVE = "99_Archive"
# Older projects kept a LibreOffice profile here; `proof` now uses a short per-user profile, so the
# folder is no longer created. An existing one is left alone and skipped by the tree walks.
RENDER_PROFILES = "90_Agent_Work/render_profiles"

TREE = (
    f"{PACKAGE}/Change_Notes", f"{PACKAGE}/Figures", f"{PACKAGE}/Proofs", f"{PACKAGE}/Supplementary",
    f"{PACKAGE}/Tables", VERSIONS, INCOMING,
    "02_Figures/Shared",
    "03_Data/Original_Source", "03_Data/Derived",
    "04_Analysis",
    "05_References/EndNote", "05_References/Journal_Guidelines", "05_References/Evidence",
    "06_Project_Docs/Workflows",
    PASSES, "90_Agent_Work/QA", "90_Agent_Work/Figure_QA",
    "90_Agent_Work/Scratch",
    ARCHIVE,
)
CORE_FILES = (CONFIG_NAME, PROJECT_INDEX, README, "AGENTS.md", "CLAUDE.md", ".gitignore", ".gitattributes",
              VERSION_INDEX, VERSION_INDEX_MD, FIGURE_INDEX, PACKAGE_MANIFEST, LEDGER, STATUS, DECISIONS)

DEFAULT_NAMING = "{date} {initials} {venue} V{n} {slug} {state}.docx"
DEFAULT_PROFILE = "05_References/Journal_Guidelines/journal.profile.json"
KIND_LABELS = {"journal_article": "journal article", "thesis": "thesis"}
CHANGES_UNFINISHED = "<!-- msw: complete this note before release"

VERSION_RE = re.compile(r"^[Vv]?0*(\d{1,4})$")
PASS_DIR_RE = re.compile(r"^V(\d+)_(.+)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
REASON_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
TOKEN_RE = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")
REVISION_RE = re.compile(rb"<w:(?:ins|del|moveFrom|moveTo)\b")
FIGURE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
FIGURE_VERSION_RE = re.compile(r"^[Vv](\d{1,4})([A-Za-z]?)$")
FIGURE_KINDS = {"tiff": "tif", "jpeg": "jpg"}
PACKAGED_KINDS = ("pdf", "png")         # a release copies these of each changed figure into Figures/
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# Folders an agent host, an editor or git creates: a folder holding only these counts as empty for init.
HOST_DIRS = frozenset({".agents", ".claude", ".codex", ".git", ".idea", ".vscode"})
SCIENCE_CHANGES = ("comments", "tracked_for_review")
# os.replace of an index can meet a reader holding the file open (another msw, a sync client, an
# indexer or antivirus): WinError 5 or 32. Retry after these pauses (seconds) before giving up.
REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6)
# Operations that rewrite several indexes together; a failure part-way must not pass verify-project.
INDEX_EVENTS = ("release", "intake", "author_save", "retarget", "figure_add", "figure_register", "figure_promote")
# Of those, the ones that finish from their pending event when run again (leftovers included).
RESUMABLE_EVENTS = ("release", "intake", "author_save", "retarget")

VERSION_INDEX_MD_TEXT = """# Version index

Generated from `VERSION_INDEX.json` by `msw.py`; notes outside the markers are kept.

<!-- msw:versions:start -->
{versions_block}
<!-- msw:versions:end -->
"""

FIGURES_README_TEXT = """# Figures

One folder per figure, `Figure_NN_<Name>/`, holding its build scripts, source data, versioned
outputs (`out/vNN/`) and a `START_HERE.md`; shared assets go in `Shared/`. `FIGURE_INDEX.json`
records each figure's versions with SHA-256 values, the promoted (current) version and the
manuscript versions each version was released in. Figure work uses the nature-figure skill; every
figure version is a new file and old candidates are kept.

Keep the index current with `msw.py figure add`, `figure register`, `figure promote` and
`figure status`. The block below is generated from the index; notes outside the markers are kept.

<!-- msw:figures:start -->
{figures_block}
<!-- msw:figures:end -->
"""


class ProjectError(Exception):
    """A refusal with one or more reasons."""

    def __init__(self, *problems):
        self.problems = [str(problem) for problem in problems] or ["refused"]
        super().__init__("; ".join(self.problems))


# -- small helpers -------------------------------------------------------------------

def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return _dt.date.today().isoformat()


def parse_version(text) -> int:
    match = VERSION_RE.match(str(text).strip())
    if not match or int(match.group(1)) < 1:
        raise ProjectError(f"not a version number: {text!r} (use V01, V2 or 2)")
    return int(match.group(1))


def vlabel(number: int) -> str:
    return f"V{number:02d}"


def render(text: str, values: dict) -> str:
    """Replace {key} for known keys; {{ and }} give literal braces; unknown {keys} stay as written."""
    def substitute(match):
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        key = match.group(1)
        return str(values[key]) if key in values else token
    return TOKEN_RE.sub(substitute, text)


def template(name: str, values: dict) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8").replace("\r\n", "\n")
    return render(text, values)


def clean_words(text) -> str:
    """Words safe inside a Windows file name: no reserved characters, single spaces."""
    text = BAD_NAME_CHARS.sub("-", str(text or ""))
    return re.sub(r"[\s_]+", " ", text).strip(" .-")


def file_name(config: dict, *, date: str, number: int, slug: str = "", state: str = "") -> str:
    author = config.get("author") or {}
    values = {"date": date, "initials": clean_words(author.get("file_initials", "")),
              "venue": clean_words(config.get("venue", "")), "n": f"{number:02d}", "version": vlabel(number),
              "slug": clean_words(slug), "state": clean_words(state)}
    name = render(config.get("naming") or DEFAULT_NAMING, values)
    stem, ext = os.path.splitext(name)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    return stem + (ext or ".docx")


def is_lock(name: str) -> bool:
    upper = name.upper()
    return (name.startswith("~$") or (upper.startswith("~WRL") and upper.endswith(".TMP"))
            or (name.startswith(".~lock.") and name.endswith("#")))


def _owns(lock: str, name: str) -> bool:
    """Does the lock file `lock` mean the file `name` beside it is open? Word's and LibreOffice's
    owner files are matched by their whole name (docx.owner_names); Word's `~WRL*.tmp` save files
    name no document, so they count for every file in the folder."""
    if lock.upper().startswith("~WRL") and lock.upper().endswith(".TMP"):
        return True
    return lock.lower() in {n.lower() for n in owner_names(name)}


# -- file-system access (long Windows paths) -------------------------------------------

def _fsp(path) -> Path:
    """The Path to hand to the file system: extended-length on Windows when the path is long."""
    return Path(fs(path))


def _plain(path) -> Path:
    """The path without a Windows extended-length prefix (for display and relative paths)."""
    text = str(path)
    if text.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + text[8:])
    if text.startswith("\\\\?\\"):
        return Path(text[4:])
    return Path(path)


def _exists(path) -> bool:
    return os.path.lexists(fs(path))


def _is_file(path) -> bool:
    return os.path.isfile(fs(path))


def _is_dir(path) -> bool:
    return os.path.isdir(fs(path))


def _makedirs(path) -> None:
    os.makedirs(fs(path), exist_ok=True)


def _why(error) -> str:
    """An error as text; a Windows path-length failure becomes advice."""
    if isinstance(error, OSError):
        return explain(error) or str(error)
    return str(error)


def _is_profile_dir(path) -> bool:
    """A LibreOffice profile folder: 90_Agent_Work/render_profiles of older projects, or lo_profile*."""
    path = _plain(path)
    return ((path.name == "render_profiles" and path.parent.name == "90_Agent_Work")
            or path.name.casefold().startswith("lo_profile"))


def _in_profile(path) -> bool:
    path = _plain(path)
    return any(_is_profile_dir(folder) for folder in (path, *path.parents))


def _unreadable(error: OSError) -> dict:
    return {"path": str(_plain(getattr(error, "filename", "") or "")), "error": _why(error)}


def walk_files(folder, errors: list | None = None):
    """Every file below `folder` (long paths included), sorted per folder; LibreOffice profile
    folders are skipped. Folders that cannot be listed are added to `errors`, never raised."""
    def failed(error):
        if errors is not None:
            errors.append(_unreadable(error))

    for base, dirs, names in os.walk(fs(folder), onerror=failed):
        here = Path(base)
        dirs[:] = sorted(name for name in dirs if not _is_profile_dir(here / name))
        for name in sorted(names):
            yield _plain(here / name)


def find_locks(folder: Path, recursive: bool = False, errors: list | None = None) -> list[Path]:
    folder = Path(folder)
    if not _is_dir(folder):
        return []
    if recursive:
        return sorted(path for path in walk_files(folder, errors) if is_lock(path.name))
    try:
        return sorted(_plain(entry) for entry in _fsp(folder).iterdir() if is_lock(entry.name))
    except OSError as error:
        if errors is not None:
            errors.append(_unreadable(error))
        return []


def _owner_locks(path) -> list[Path]:
    """Word or LibreOffice lock files beside `path` that mean it is open."""
    path = Path(path)
    return [lock for lock in find_locks(path.parent) if _owns(lock.name, path.name)]


def _json_text(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _read_json(path: Path, default):
    try:
        text = _fsp(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return copy.deepcopy(default)
    try:
        return json.loads(text)
    except ValueError as error:
        raise ProjectError(f"{path} is not valid JSON: {error}") from error


def write_new_text(path: Path, text: str) -> Path:
    """Create a text file (UTF-8, LF as given); refuses an existing file."""
    path = Path(path)
    _makedirs(path.parent)
    with open(fs(path), "x", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path


def _leftover(path) -> Path:
    path = Path(path)
    return path.with_name(path.name + ".new")


def refuse_leftovers(paths, root=None) -> None:
    """Refuse, before anything is written, when any target still has a name.new beside it."""
    found = [_leftover(path) for path in paths if _exists(_leftover(path))]
    if found:
        def show(path):
            try:
                return path.relative_to(root).as_posix() if root is not None else str(path)
            except ValueError:
                return str(path)

        listed = ", ".join(show(path) for path in found)
        raise ProjectError(f"{listed} exist{'s' if len(found) == 1 else ''} (left by an interrupted run); nothing "
                           "was written. If `msw.py status` lists an unfinished release, intake, author save or "
                           "retarget, run that same command again: it moves these files into the archive and finishes. "
                           "Otherwise "
                           "move each one away with `msw.py archive plan|apply --reason Interrupted_Run <file>` and "
                           "run the command again.")


def _replace(temporary: Path, path: Path) -> None:
    """os.replace, retried while another program holds the target open (PermissionError)."""
    for delay in (*REPLACE_RETRY_DELAYS, None):
        try:
            os.replace(fs(temporary), fs(path))
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def atomic_replace_many(items) -> None:
    """Replace tool-owned files together: check every target for a leftover name.new first, then
    write each name.new and fsync it, then os.replace them one after another (each retried while a
    reader holds the target open). A refusal leaves nothing written; a failure part-way through the
    swaps leaves the remaining name.new files, which the interrupted command moves to the archive
    when it is run again (it rebuilds the indexes from its pending ledger event)."""
    items = [(Path(path), data) for path, data in items]
    refuse_leftovers(path for path, _ in items)
    staged = []
    for path, data in items:
        temporary = _leftover(path)
        blob = data.encode("utf-8") if isinstance(data, str) else data
        try:
            handle = open(fs(temporary), "xb")
        except FileExistsError as error:     # appeared since the check: another session
            raise ProjectError(f"{temporary} appeared while msw was writing (another session?); run "
                               "`msw.py status`, move it away with `msw.py archive` and run the command "
                               "again") from error
        with handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        staged.append((temporary, path))
    for temporary, path in staged:
        _replace(temporary, path)


def atomic_replace(path: Path, data) -> None:
    atomic_replace_many([(path, data)])


def copy_exclusive(source: Path, dest: Path) -> str:
    """Copy into a new file (never over an existing one); returns the SHA-256 of the new file."""
    _makedirs(Path(dest).parent)
    with open(fs(source), "rb") as reader, open(fs(dest), "xb") as writer:
        shutil.copyfileobj(reader, writer, 1 << 20)
        writer.flush()
        os.fsync(writer.fileno())
    try:
        shutil.copystat(fs(source), fs(dest))
    except OSError:
        pass
    return sha256_file(fs(dest))


def move_file(source: Path, dest: Path) -> None:
    """Same-volume rename; never replaces an existing destination."""
    if _exists(dest):
        raise ProjectError(f"{dest} already exists; nothing was moved")
    _makedirs(Path(dest).parent)
    os.rename(fs(source), fs(dest))


class Hashes:
    """SHA-256 by path, cached per (path, mtime, size) within one command."""

    def __init__(self):
        self.cache = {}

    def __call__(self, path) -> str | None:
        path = Path(path)
        try:
            stat = os.stat(fs(path))
        except (FileNotFoundError, NotADirectoryError):
            return None
        if not _is_file(path):
            return None
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if key not in self.cache:
            self.cache[key] = sha256_file(fs(path))
        return self.cache[key]


def _norm(rel: str) -> str:
    rel = str(rel).replace("\\", "/").strip().strip("/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.casefold() if os.name == "nt" else rel


def _number(entry: dict) -> int | None:
    try:
        return parse_version(entry.get("version", ""))
    except ProjectError:
        return None


def _base_role(entry: dict) -> str:
    return entry.get("base_role") or entry.get("role") or ""


def _pointer(entry: dict | None) -> dict | None:
    if not entry:
        return None
    return {key: entry.get(key) for key in ("version", "state", "path", "sha256")}


def _label_of(pointer: dict | None) -> str:
    if not pointer:
        return "none"
    return f"{pointer.get('version')} {pointer.get('state') or ''}".strip()


def _has_revisions(path: Path) -> bool:
    try:
        return bool(REVISION_RE.search(Package(fs(path)).parts["word/document.xml"]))
    except (OSError, PackageError, KeyError, ValueError, zipfile.BadZipFile):
        return False


def _auto_state(path: Path) -> str:
    return "author saved" if _has_revisions(path) else "author accepted"


def _check_word_file(path: Path) -> None:
    if not _is_file(path):
        raise ProjectError(f"{path} does not exist or is not a file")
    locks = _owner_locks(path)
    if locks:
        raise ProjectError(f"{path.name} appears to be open in Word or LibreOffice "
                           f"({', '.join(p.name for p in locks)}); ask the author to close it first")
    try:
        Package(fs(path))
    except OSError as error:
        raise ProjectError(f"{path} cannot be read: {_why(error)}") from error
    except (PackageError, ValueError, zipfile.BadZipFile) as error:
        raise ProjectError(f"{path} is not a readable Word document: {error}") from error


# -- the project ---------------------------------------------------------------------

class Project:
    def __init__(self, root):
        self.root = _plain(Path(root).resolve())
        path = self.root / CONFIG_NAME
        try:
            self.config = json.loads(_fsp(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            raise ProjectError(f"cannot read {path}: {_why(error)}") from error

    @classmethod
    def locate(cls, start=None) -> "Project":
        found = find_config(start)
        if found is None:
            where = Path(start or os.getcwd()).resolve()
            raise ProjectError(f"no {CONFIG_NAME} in {where} or its parents; create a project with `msw.py init`")
        return cls(found.parent)

    # paths
    def path(self, rel) -> Path:
        candidate = Path(rel)
        return candidate if candidate.is_absolute() else self.root / candidate

    def rel(self, path) -> str:
        resolved = _plain(_plain(path).resolve())
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError:
            return str(resolved)

    def inside(self, path) -> bool:
        try:
            _plain(_plain(path).resolve()).relative_to(self.root)
            return True
        except ValueError:
            return False

    def record_path(self, path) -> str:
        """A path as a versioned record may keep it: relative to the project root when inside it,
        otherwise only the file name (records never hold this machine's folders or user name)."""
        return self.rel(path) if self.inside(path) else _plain(Path(path)).name

    # configuration
    @property
    def revision_name(self) -> str:
        author = self.config.get("author") or {}
        return author.get("revision_name") or author.get("name") or ""

    # indexes
    def project_index(self) -> dict:
        data = _read_json(self.root / PROJECT_INDEX, {"schema_version": 2, "current": None, "baseline": None,
                                                       "package": PACKAGE, "outstanding": []})
        if not isinstance(data, dict):
            data = {}
        for key in ("current", "baseline"):
            if not isinstance(data.get(key), dict):
                data[key] = None
        return data

    def package_files(self) -> list[dict]:
        """PACKAGE_MANIFEST.json entries as {path (from the project root), source, sha256, role, version}."""
        data = _read_json(self.root / PACKAGE_MANIFEST, {"files": []})
        items = []
        for item in (data.get("files") if isinstance(data, dict) else None) or []:
            if isinstance(item, dict) and item.get("package_path"):
                entry = {"path": f"{PACKAGE}/{item['package_path']}", "source": item.get("source_path"),
                         "sha256": item.get("sha256"), "role": item.get("role"), "version": item.get("version")}
                for key in ("figure", "figure_version"):
                    if item.get(key):
                        entry[key] = item[key]
                items.append(entry)
        return items

    def package_manifest_text(self, items: list, version) -> str:
        files = []
        for item in items:
            path = self.path(item["path"])
            record = {"package_path": item["path"][len(PACKAGE) + 1:], "source_path": item.get("source"),
                      "sha256": item.get("sha256"), "size": os.stat(fs(path)).st_size if _is_file(path) else None,
                      "role": item.get("role"), "version": item.get("version")}
            for key in ("figure", "figure_version"):
                if item.get(key):
                    record[key] = item[key]
            files.append(record)
        return _json_text({"schema_version": 2, "version": version, "assembled": today(), "files": files})

    def require_schema(self) -> None:
        """Refuse to maintain an index written by other tools (an adopted, older project)."""
        data = _read_json(self.root / PROJECT_INDEX, None)
        if data is not None and (not isinstance(data, dict) or data.get("schema_version") != 2):
            raise ProjectError(f"{PROJECT_INDEX} is not schema_version 2 (an older or foreign index); msw will not "
                               "rewrite it. Archive it with `msw.py archive`, then run `msw.py init --adopt` to "
                               "create a new one, or convert it by hand.")

    def version_entries(self) -> list:
        data = _read_json(self.root / VERSION_INDEX, [])
        if isinstance(data, dict):
            data = data.get("versions", [])
        return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []

    def version_index_text(self, entries: list) -> str:
        data = _read_json(self.root / VERSION_INDEX, [])
        if isinstance(data, dict):
            data["versions"] = entries
        else:
            data = entries
        return _json_text(data)

    def commit_indexes(self, entries: list | None = None, index: dict | None = None,
                       package: list | None = None, figures: dict | None = None) -> list[str]:
        """Swap in VERSION_INDEX.json, PACKAGE_MANIFEST.json, FIGURE_INDEX.json and PROJECT_INDEX.json
        together with the generated blocks rendered from the new data. Every target is checked for a
        leftover name.new before anything is rendered or written, so a refusal changes nothing.
        Returns notes about documents without markers."""
        if index is not None:
            self.require_schema()
        blocks = entries is not None or index is not None or package is not None
        targets = [self.root / rel for rel, data in ((VERSION_INDEX, entries), (PACKAGE_MANIFEST, package),
                                                     (FIGURE_INDEX, figures), (PROJECT_INDEX, index))
                   if data is not None]
        if blocks:
            targets += [self.path(rel) for rel in (README, STATUS, VERSION_INDEX_MD)]
        if figures is not None:
            targets += [self.path(rel) for rel in _figure_doc_paths(figures)]
        refuse_leftovers(targets, self.root)
        if index is not None:
            index["updated"] = utc_now()
        staged, notes = [], []
        if entries is not None:
            staged.append((self.root / VERSION_INDEX, self.version_index_text(entries)))
        if package is not None:
            version = ((index or self.project_index()).get("current") or {}).get("version")
            staged.append((self.root / PACKAGE_MANIFEST, self.package_manifest_text(package, version)))
        if figures is not None:
            staged.append((self.root / FIGURE_INDEX, _json_text(figures)))
        if index is not None:
            staged.append((self.root / PROJECT_INDEX, _json_text(index)))
        if blocks:
            items, more = render_docs(self, index=index, entries=entries, package=package)
            staged, notes = staged + items, notes + more
        if figures is not None:
            items, more = render_figure_docs(self, figures)
            staged, notes = staged + items, notes + more
        atomic_replace_many(staged)
        return notes

    def _owned_docs(self) -> list[str]:
        # manuscript.json too: retarget swaps it in the same way.
        rels = [PROJECT_INDEX, VERSION_INDEX, PACKAGE_MANIFEST, FIGURE_INDEX, README, STATUS, VERSION_INDEX_MD,
                CONFIG_NAME]
        try:
            return rels + _figure_doc_paths(self.figure_index())
        except ProjectError:
            return rels + [FIGURES_README]

    def leftovers(self) -> list[str]:
        """name.new files an interrupted run left beside tool-owned indexes and generated documents."""
        return [f"{rel}.new" for rel in self._owned_docs() if _exists(_leftover(self.path(rel)))]

    def refuse_leftovers(self) -> None:
        """Before a command copies or moves anything: refuse when any tool-owned index or generated
        document still has a name.new left by an interrupted run."""
        refuse_leftovers((self.path(rel) for rel in self._owned_docs()), self.root)

    def figure_index(self) -> dict:
        return _read_json(self.root / FIGURE_INDEX, {"schema_version": 2, "figures": []})

    def figure_index_for_update(self) -> dict:
        """FIGURE_INDEX.json as a dict msw may rewrite: schema_version 2 with a flat figures list
        whose entries keep their versions in a list. Refuses anything else."""
        data = _read_json(self.root / FIGURE_INDEX, {"schema_version": 2, "figures": []})
        valid = (isinstance(data, dict) and data.get("schema_version") == 2 and isinstance(data.get("figures"), list)
                 and all(isinstance(f, dict) and isinstance(f.get("versions", []), list) for f in data["figures"]))
        if not valid:
            raise ProjectError(f"{FIGURE_INDEX} is not a schema_version 2 index with a flat figures list (an older or "
                               "foreign index); msw will not rewrite it. Archive it with `msw.py archive` and "
                               "register the figures again, or convert it by hand.")
        for figure in data["figures"]:
            figure.setdefault("versions", [])
        return data

    # ledger
    def ledger_events(self) -> tuple[list, list]:
        events, errors = [], []
        path = self.root / LEDGER
        if not _is_file(path):
            return events, errors
        for number, line in enumerate(_fsp(path).read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError as error:
                errors.append(f"{LEDGER} line {number} is not JSON: {error}")
                continue
            if isinstance(event, dict):
                events.append(event)
        return events, errors

    def ledger_append(self, event: dict) -> dict:
        """Append one event. The ledger is versioned: absolute paths in it are made relative to the
        project (or cut to the file name outside it), and the project and home folders inside
        longer text (error messages) become `.` and `~`."""
        events, _ = self.ledger_events()
        seq = max((e.get("seq", 0) for e in events if isinstance(e.get("seq"), int)), default=0) + 1
        record = {"seq": seq, "utc": utc_now(), **_scrub(self, event)}
        path = self.root / LEDGER
        _makedirs(path.parent)
        with open(fs(path), "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record


def _project(args) -> Project:
    return Project.locate(getattr(args, "project", None))


# -- one changing command at a time ---------------------------------------------------
#
# A command that changes the project reads the indexes, works, and writes them back. Two at once
# would each write back what they read, so the later one would silently drop the other's change
# (and both would give their ledger lines the same seq). Every such command therefore holds an
# operating-system lock on MUTATION_LOCK from before it reads anything until its ledger event
# completes; a second one refuses. The lock file is created once and never deleted (projects that
# forbid deleting any file are respected); it holds the current holder's PID, command and start
# time. The operating system releases the lock when the command ends, even when it crashes, so no
# lock is ever left behind. Read-only commands (status, verify-project, figure status, plans and
# dry runs) do not take it. The file is not project content (git ignores it).

_LOCK_BYTE = 0x7FFFFFF0     # the byte locked on Windows: far past the record, which stays readable


def _try_lock(handle) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(_LOCK_BYTE)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(_LOCK_BYTE)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _open_lock(path: Path):
    """The lock file, opened for reading and writing and created when missing (never truncated
    on open, never deleted)."""
    _makedirs(path.parent)
    return os.fdopen(os.open(fs(path), os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)), "r+b")


def lock_state(root) -> dict | None:
    """The command holding the project's mutation lock, or None when no command holds it: its
    record (pid, command, utc, host). Reads only (the lock is tested and let go at once)."""
    path = Path(root) / MUTATION_LOCK
    if not _exists(path):
        return None
    try:
        handle = _open_lock(path)
    except OSError:
        return None
    with handle:
        if _try_lock(handle):
            _unlock(handle)
            return None
        try:
            handle.seek(0)
            record = json.loads(handle.read(4096).decode("utf-8") or "{}")
        except (OSError, ValueError, UnicodeDecodeError):
            record = {}
    record = record if isinstance(record, dict) else {}
    return {key: record.get(key) for key in ("pid", "command", "utc", "host")}


def _lock_holder(info: dict) -> str:
    details = [f"PID {info['pid']}" if info.get("pid") else "",
               f"on {info['host']}" if info.get("host") and info["host"] != platform.node() else "",
               f"started {info['utc']}" if info.get("utc") else ""]
    details = ", ".join(item for item in details if item)
    return (f"`msw.py {info['command']}`" if info.get("command") else "an msw command") + (
        f" ({details})" if details else "")


def lock_advice(info: dict, refused: bool = True) -> str:
    """What to do about a mutation lock another command holds: the reason a command refuses
    (`refused`), else the advice `status` gives."""
    if not refused:
        return (f"{_lock_holder(info)} is changing this project; other commands that change the project refuse "
                "until it finishes (the lock is released when it ends, even if it crashes).")
    return (f"another msw command is changing this project: {_lock_holder(info)}; nothing was changed. Wait until "
            "it finishes, then run this command again (status, verify-project, figure status, plans and dry runs "
            "work meanwhile). The lock is released when that command ends, even if it crashes.")


@contextlib.contextmanager
def mutation_lock(root, command: str):
    """Hold the project's mutation lock while the block runs; refuse when another command holds it."""
    path = Path(root) / MUTATION_LOCK
    handle = _open_lock(path)
    try:
        if not _try_lock(handle):
            raise ProjectError(lock_advice(lock_state(root) or {}))
        try:
            record = json.dumps({"pid": os.getpid(), "command": command, "utc": utc_now(),
                                 "host": platform.node()}) + "\n"
            handle.seek(0)
            handle.truncate(0)
            handle.write(record.encode("utf-8"))
            handle.flush()
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()


def _locked(function, command, when=None, root=None):
    """`function` run under the mutation lock (when `when(args)` is true; always without `when`).
    `root(args)` finds the project folder (default: the one --project or the working folder is in);
    without a project there is nothing to lock and the command reports that itself."""
    def run(args):
        if when is not None and not when(args):
            return function(args)
        folder = root(args) if root is not None else _config_root(args)
        if folder is None:
            return function(args)
        with mutation_lock(folder, command(args) if callable(command) else command):
            return function(args)
    run.__name__ = function.__name__
    run.__doc__ = function.__doc__
    return run


def _config_root(args) -> Path | None:
    found = find_config(getattr(args, "project", None))
    return _plain(found.parent) if found is not None else None


def _adopted_root(args) -> Path | None:
    """init locks only an existing project it adopts: a new project has no other command yet."""
    folder = _plain(Path(args.dir).resolve())
    return folder if getattr(args, "adopt", False) and _is_file(folder / CONFIG_NAME) else None


# -- no machine paths in records -------------------------------------------------------

ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")


def _folder_pattern(folder) -> re.Pattern:
    """A folder path inside text: any separator (`\\`, `/`, or `\\\\` as repr() writes it), with an
    optional extended-length prefix; case-insensitive on Windows."""
    parts = [part for part in re.split(r"[\\/]+", str(folder).rstrip("\\/"))]
    separator = r"(?:\\\\|\\|/)"
    prefix = r"(?:(?:\\\\|\\){2}\?(?:\\\\|\\))?"
    body = separator.join(re.escape(part) for part in parts)
    # ... and not a longer folder name that merely starts the same way
    return re.compile(prefix + body + r"(?![\w-])", re.IGNORECASE if os.name == "nt" else 0)


def _scrub_text(project: Project, text: str) -> str:
    if "/" not in text and "\\" not in text:
        return text
    if "\n" not in text and len(text) < 1024 and ABSOLUTE_RE.match(text):
        return project.record_path(text)
    patterns = getattr(project, "_machine_patterns", None)
    if patterns is None:
        patterns = [(_folder_pattern(project.root), ".")]
        try:
            home = Path.home()
        except (RuntimeError, KeyError, OSError):
            home = None
        if home is not None and str(home).strip("\\/"):
            patterns.append((_folder_pattern(home), "~"))
        project._machine_patterns = patterns
    for pattern, replacement in patterns:
        text = pattern.sub(lambda _match: replacement, text)
    return text


def _scrub(project: Project, value):
    """A ledger event without machine paths (see Project.ledger_append)."""
    if isinstance(value, dict):
        return {key: _scrub(project, item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(project, item) for item in value]
    if isinstance(value, str):
        return _scrub_text(project, value)
    return value


def _ledger_error(project: Project, error: Exception) -> str:
    """An error for the ledger: file names relative to the project (or bare names outside it)."""
    if isinstance(error, OSError) and (error.filename or error.filename2):
        names = [project.record_path(name) for name in (error.filename, error.filename2) if name]
        return f"{error.strerror or type(error).__name__}: " + " -> ".join(names)
    return _why(error)


# -- generated blocks ----------------------------------------------------------------

def _md_path(rel: str) -> str:
    return "`" + str(rel).replace("`", "'") + "`"


def render_current_block(index: dict, files: list) -> str:
    current = index.get("current")
    if not current:
        return ((f"{_target_line(index)}\n\n" if _target_line(index) else "")
                + "No manuscript is registered yet. Register the starting draft with\n"
                  "`msw.py intake <draft.docx> --as-version V01`.")
    by_role = {}
    for item in files:
        by_role.setdefault(item.get("role"), item)
    lines = [f"**Current manuscript: {current.get('version')}**"
             + (f" ({current.get('state')})" if current.get("state") else ""), ""]
    if _target_line(index):
        lines.append(f"- {_target_line(index)}")
    lines.append(f"- File: {_md_path(current.get('path'))}")
    if "current" in by_role:
        lines.append(f"- Open in Word: {_md_path(by_role['current']['path'])}")
    baseline = index.get("baseline")
    if baseline:
        lines.append(f"- Built on: {_label_of(baseline)} ({_md_path(baseline.get('path'))})")
    if "change_note" in by_role:
        lines.append(f"- Change note: {_md_path(by_role['change_note']['path'])}")
    if "proof" in by_role:
        lines.append(f"- Proof: {_md_path(by_role['proof']['path'])}")
    outstanding = _open_items(index)
    lines.append(f"- Open items: {len(outstanding)} (see `06_Project_Docs/STATUS.md`)" if outstanding
                 else f"- {NO_OPEN_ITEMS}")
    lines.append(f"- Updated: {index.get('updated') or utc_now()}")
    return "\n".join(lines)


NO_OPEN_ITEMS = "Open items: see the latest change note"


def _open_items(index: dict) -> list:
    items = index.get("outstanding")
    return [item for item in items if item] if isinstance(items, list) else []


def _item_line(item) -> str:
    if not isinstance(item, dict):
        return f"- {item}"
    since = f" (since {item['since']})" if item.get("since") else ""
    label = f"{item['id']}: " if item.get("id") else ""
    return f"- {label}{item.get('text', '')}{since}"


def _target_line(index: dict) -> str | None:
    venue, journal = index.get("venue"), index.get("target_journal")
    if not venue and not journal:
        return None
    if journal and venue and journal != venue:
        return f"Target journal: {journal} (venue code {venue})"
    return f"Target journal: {journal or venue}"


def render_status_block(index: dict, files: list) -> str:
    current = index.get("current")
    lines = [_target_line(index)] if _target_line(index) else []
    if current:
        lines.append(f"Current: **{current.get('version')}** {current.get('state') or ''}".rstrip()
                     + f", {_md_path(current.get('path'))} (SHA-256 `{str(current.get('sha256'))[:12]}`)")
    else:
        lines.append("Current: none registered yet.")
    baseline = index.get("baseline")
    lines.append(f"Baseline: {_label_of(baseline)}" + (f", {_md_path(baseline.get('path'))}" if baseline else ""))
    lines += ["", f"Package ({_md_path(index.get('package') or PACKAGE)}):"]
    if files:
        for item in files:
            rel = str(item.get("path", ""))
            short = rel[len(PACKAGE) + 1:] if rel.startswith(PACKAGE + "/") else rel
            lines.append(f"- {str(item.get('role', 'file')).replace('_', ' ')}: {_md_path(short)}")
    else:
        lines.append("- empty")
    figure_set = index.get("figure_set") or {}
    lines += ["", "Figure set: " + (", ".join(f"{k} {v}" for k, v in sorted(figure_set.items())) or "none recorded")]
    outstanding = _open_items(index)
    if outstanding:
        lines += ["", "Open items:"]
        lines += [_item_line(item) for item in outstanding]
        lines.append("(add one with `msw.py outstanding add --text \"...\"`; close one with "
                     "`msw.py outstanding done --id <id>`)")
    else:
        lines += ["", NO_OPEN_ITEMS]
    lines += ["", f"Updated: {index.get('updated') or utc_now()}"]
    return "\n".join(lines)


def render_versions_block(entries: list) -> str:
    if not entries:
        return "No versions registered yet."
    lines = ["| Version | Date | Role | State | File | SHA-256 | Built on | Change note |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]

    def cell(value):
        return str(value or "").replace("|", "\\|")

    def short(rel):
        rel = str(rel or "")
        return rel[len("01_Manuscripts/"):] if rel.startswith("01_Manuscripts/") else rel

    for entry in entries:
        role = str(entry.get("role") or "") + (f" (returned by {entry['returned_by']})" if entry.get("returned_by")
                                              else "")
        lines.append("| " + " | ".join([
            cell(entry.get("version")), cell(entry.get("date")), cell(role), cell(entry.get("state")),
            _md_path(cell(short(entry.get("path")))), f"`{str(entry.get('sha256', ''))[:12]}`",
            cell(entry.get("baseline")), _md_path(cell(short(entry.get("changes")))) if entry.get("changes") else "",
        ]) + " |")
    return "\n".join(lines)


def replace_block(text: str, name: str, body: str) -> str | None:
    start, end = f"<!-- msw:{name}:start -->", f"<!-- msw:{name}:end -->"
    if text.count(start) != 1 or text.count(end) != 1:
        return None
    i, j = text.index(start), text.index(end)
    if j < i:
        return None
    newline = "\r\n" if "\r\n" in text else "\n"
    body = body.rstrip("\n").replace("\r\n", "\n").replace("\n", newline)
    return text[:i + len(start)] + newline + body + newline + text[j:]


def render_docs(project: Project, index: dict | None = None, entries: list | None = None,
                package: list | None = None) -> tuple[list, list[str]]:
    """The new contents of README.md, STATUS.md and VERSION_INDEX.md with their generated blocks
    rendered (from the given data, else the files on disk): ([(path, bytes)], notes). Writes nothing."""
    index = project.project_index() if index is None else index
    entries = project.version_entries() if entries is None else entries
    package = project.package_files() if package is None else package
    items, notes = [], []
    for rel, name, body in ((README, "current", render_current_block(index, package)),
                            (STATUS, "status", render_status_block(index, package)),
                            (VERSION_INDEX_MD, "versions", render_versions_block(entries))):
        _block_update(project, rel, name, body, items, notes)
    return items, notes


def refresh_docs(project: Project, index: dict | None = None, entries: list | None = None,
                 package: list | None = None) -> list[str]:
    """Rewrite the generated marker blocks; returns notes for files without markers."""
    refuse_leftovers((project.path(rel) for rel in (README, STATUS, VERSION_INDEX_MD)), project.root)
    items, notes = render_docs(project, index=index, entries=entries, package=package)
    atomic_replace_many(items)
    return notes


def _block_update(project: Project, rel: str, name: str, body: str, items: list, notes: list) -> None:
    """Append (path, new bytes) to `items` when the block changes; a note when it cannot be written."""
    path = project.path(rel)
    if not _is_file(path):
        notes.append(f"{rel} is missing; its generated block was not written")
        return
    raw = _fsp(path).read_bytes()
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    text = raw[len(bom):].decode("utf-8")
    new = replace_block(text, name, body)
    if new is None:
        notes.append(f"{rel} has no single <!-- msw:{name}:start --> / <!-- msw:{name}:end --> pair; "
                     "add the markers to get a generated block")
    elif new != text:
        items.append((path, bom + new.encode("utf-8")))


def _figure_items(figures) -> list[dict]:
    items = figures.get("figures") if isinstance(figures, dict) else None
    return [figure for figure in items or [] if isinstance(figure, dict)]


def _cell(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def render_figures_block(figures: dict) -> str:
    """The 02_Figures/README.md block: each figure's promoted version and its files."""
    items = _figure_items(figures)
    if not items:
        return ("No figures registered yet. Add one with `msw.py figure add --id Figure_01 --title \"Short title\"`,\n"
                "then register each build with `msw.py figure register`.")
    lines = ["| Figure | Title | Current | Current files | Released in | Versions |",
             "| --- | --- | --- | --- | --- | --- |"]
    for figure in items:
        version = _figure_version(figure, figure.get("current"))
        files = ", ".join(_md_path(_short_figure_path(rel)) for kind, rel, _ in _version_files(version)
                          if kind in PACKAGED_KINDS) if version else ""
        used = ", ".join(str(v) for v in (version or {}).get("used_in") or [])
        labels = ", ".join(str(v.get("version")) for v in figure.get("versions") or [] if isinstance(v, dict))
        lines.append("| " + " | ".join([_cell(figure.get("id")), _cell(figure.get("title")),
                                         f"**{_cell(figure.get('current'))}**" if figure.get("current") else "none",
                                         files, _cell(used), _cell(labels)]) + " |")
    lines += ["", "Current = promoted with `msw.py figure promote`; `msw.py figure status` checks the files and "
                  "the picture in the current manuscript."]
    return "\n".join(lines)


def render_figure_block(figure: dict) -> str:
    """The generated block of a figure's START_HERE.md: current version, its files, all versions."""
    current = figure.get("current")
    version = _figure_version(figure, current)
    lines = [f"Current version: **{current}**" if current else "Current version: none promoted yet"]
    if version:
        lines.append("")
        for kind, rel, sha in _version_files(version):
            lines.append(f"- {kind}: {_md_path(rel)} (sha256 `{str(sha)[:12]}`)")
        if version.get("qa"):
            lines.append(f"- QA: {_md_path(version['qa'])}")
        if version.get("used_in"):
            lines.append(f"- Released in: {', '.join(str(v) for v in version['used_in'])}")
    versions = [v for v in figure.get("versions") or [] if isinstance(v, dict)]
    lines.append("")
    if not versions:
        lines.append("No versions registered yet. After each build run `msw.py figure register --id "
                     f"{figure.get('id')} --version v01 --file <pdf> --file <png> ...`.")
        return "\n".join(lines)
    lines += ["| Version | Registered | Files | Released in | Note |", "| --- | --- | --- | --- | --- |"]
    for item in reversed(versions):
        kinds = ", ".join(kind for kind, _, _ in _version_files(item))
        lines.append("| " + " | ".join([_cell(item.get("version")), _cell(item.get("date")), _cell(kinds),
                                         _cell(", ".join(str(v) for v in item.get("used_in") or [])),
                                         _cell(item.get("note"))]) + " |")
    return "\n".join(lines)


def _short_figure_path(rel: str) -> str:
    rel = str(rel or "")
    return rel[len(FIGURES) + 1:] if rel.startswith(FIGURES + "/") else rel


def _figure_doc_paths(figures: dict) -> list[str]:
    return [FIGURES_README] + [f"{figure['folder']}/{START_HERE}" for figure in _figure_items(figures)
                               if figure.get("folder")]


def render_figure_docs(project: Project, figures: dict) -> tuple[list, list[str]]:
    """New contents of 02_Figures/README.md and each figure's START_HERE.md; writes nothing."""
    items, notes = [], []
    _block_update(project, FIGURES_README, "figures", render_figures_block(figures), items, notes)
    for figure in _figure_items(figures):
        if figure.get("folder"):
            _block_update(project, f"{figure['folder']}/{START_HERE}", "figure", render_figure_block(figure),
                          items, notes)
    return items, notes


def refresh_figure_docs(project: Project, figures: dict | None = None) -> list[str]:
    """Rewrite the generated blocks of 02_Figures/README.md and of each figure's START_HERE.md."""
    figures = project.figure_index() if figures is None else figures
    refuse_leftovers((project.path(rel) for rel in _figure_doc_paths(figures)), project.root)
    items, notes = render_figure_docs(project, figures)
    atomic_replace_many(items)
    return notes


# -- package synchronisation ----------------------------------------------------------

def _find_twin(project: Project, hashes: Hashes, sha: str, *, prefer=(), pending=None, exclude=None) -> str | None:
    """A byte-identical copy outside the package and the archive (Versions/ first)."""
    candidates = [rel for rel in prefer if rel]
    for entry in project.version_entries():
        candidates += [entry.get(key) for key in ("path", "changes", "proof") if entry.get(key)]
    seen = set()
    for rel in candidates:
        key = _norm(rel)
        if key in seen or key.startswith(_norm(PACKAGE) + "/") or key.startswith(_norm(ARCHIVE) + "/"):
            continue
        seen.add(key)
        path = project.path(rel)
        if exclude is not None and path.resolve() == Path(exclude).resolve():
            continue
        if hashes(path) == sha:
            return rel
    if pending and sha in pending:
        return pending[sha]
    return None


def _desired_package(current: dict | None, baseline: dict | None, extras: list) -> list[dict]:
    desired = []
    if current:
        desired.append({"path": f"{PACKAGE}/{Path(current['path']).name}", "sha256": current["sha256"],
                        "role": "current", "version": current.get("version"), "source": current["path"]})
    if baseline and baseline.get("path") and baseline.get("path") != (current or {}).get("path"):
        desired.append({"path": f"{PACKAGE}/{Path(baseline['path']).name}", "sha256": baseline["sha256"],
                        "role": "baseline", "version": baseline.get("version"), "source": baseline["path"]})
    desired += [dict(item) for item in extras]
    paths = [_norm(item["path"]) for item in desired]
    if len(paths) != len(set(paths)):
        raise ProjectError("two package files would share one name: " + ", ".join(item["path"] for item in desired))
    return desired


def _plan_package(project: Project, hashes: Hashes, desired: list, reason: str, day: str, pending=None,
                  extra_out=()) -> tuple[list, list, list]:
    """Moves (out of the package, twin-checked) and copies (into it) that make it hold `desired`."""
    listed = project.package_files()
    for rel in extra_out:
        listed.append({"path": rel, "sha256": None, "role": "unlisted"})
    want = {_norm(item["path"]): item for item in desired}
    moves, copies, problems, leaving, planned = [], [], [], set(), set()
    for item in listed:
        rel = item.get("path")
        if not rel or _norm(rel) in leaving:
            continue
        path = project.path(rel)
        if not _is_file(path):
            continue
        disk = hashes(path)
        target = want.get(_norm(rel))
        if target and disk == target["sha256"]:
            continue
        prefer = [item.get("source")] + ([target.get("source")] if target else [])
        twin = _find_twin(project, hashes, disk, prefer=prefer, pending=pending, exclude=path)
        inner = rel[len(PACKAGE) + 1:] if _norm(rel).startswith(_norm(PACKAGE) + "/") else Path(rel).name
        dest = next(candidate for candidate in (f"{ARCHIVE}/{day}/{reason}" + (f"_{k}" if k > 1 else "") + f"/{inner}"
                                                for k in range(1, 1000))
                    if not _exists(project.path(candidate)) and _norm(candidate) not in planned)
        planned.add(_norm(dest))
        if twin is None:
            if item.get("role") == "current":
                advice = "if the author saved it in Word, run `msw.py author-save` first"
            elif str(rel).lower().endswith(".docx") and item.get("version"):
                advice = (f"it is the {str(item.get('role')).replace('_', ' ')} copy of {item.get('version')}, not the "
                          f"current file: keep the author's save with `msw.py intake \"{rel}\" --role incoming "
                          f"--as-version {item.get('version')}` first")
            else:
                advice = "keep a copy outside the package first (for example with `msw.py intake --role incoming`)"
            problems.append(f"{rel} would leave the package, but no byte-identical copy exists outside it "
                            f"(sha256 {disk[:12]}); {advice}")
        moves.append({"old": rel, "new": dest, "kind": "file", "sha256": disk, "twin": twin,
                      "recorded_sha256": item.get("sha256")})
        leaving.add(_norm(rel))
    for target in desired:
        path = project.path(target["path"])
        if _exists(path) and _norm(target["path"]) not in leaving:
            if hashes(path) == target["sha256"]:
                continue
            problems.append(f"{target['path']} exists with other content and is not a listed package file; "
                            "move it away with `msw.py archive` first")
            continue
        copies.append({"source": target["source"], "dest": target["path"], "sha256": target["sha256"],
                       "role": target["role"]})
    return moves, copies, problems


def _execute_moves(project: Project, moves: list) -> list[dict]:
    done = []
    for move in moves:
        source, dest = project.path(move["old"]), project.path(move["new"])
        if not _exists(source) and _is_file(dest) and sha256_file(dest) == move["sha256"]:
            done.append(dict(move, sha256_before=move["sha256"], sha256_after=move["sha256"], resumed=True))
            continue
        before = sha256_file(source)
        if before != move["sha256"]:
            raise ProjectError(f"{move['old']} changed after the plan was made; not moved")
        if not move.get("twin") or sha256_file(project.path(move["twin"])) != before:
            raise ProjectError(f"no byte-identical twin for {move['old']} (expected {move.get('twin')}); not moved")
        move_file(source, dest)
        after = sha256_file(dest)
        if after != before:
            raise ProjectError(f"{move['new']} does not hash like the file that was moved")
        done.append(dict(move, sha256_before=before, sha256_after=after))
    return done


def _execute_copies(project: Project, copies: list, hashes: Hashes) -> list[dict]:
    done = []
    for item in copies:
        source, dest = project.path(item["source"]), project.path(item["dest"])
        if _exists(dest):
            if sha256_file(dest) == item["sha256"]:
                done.append(dict(item, resumed=True))
                continue
            raise ProjectError(f"{item['dest']} exists with other content; not overwritten")
        if hashes(source) != item["sha256"]:
            raise ProjectError(f"{item['source']} no longer has the planned SHA-256; not copied")
        got = copy_exclusive(source, dest)
        if got != item["sha256"]:
            raise ProjectError(f"the copy {item['dest']} does not match its source; it is kept for inspection")
        done.append(item)
    return done


def _print_operations(moves: list, copies: list) -> None:
    if moves:
        print("Moves (rename into the archive; each file keeps a byte-identical twin):")
        for move in moves:
            twin = move.get("twin") or "NONE"
            print(f"  {move['old']}\n    -> {move['new']}  [sha256 {move['sha256'][:12]}, twin {twin}]")
    if copies:
        print("Copies (new files only, SHA-256 checked):")
        for item in copies:
            print(f"  {item['source']}\n    -> {item['dest']}  [sha256 {item['sha256'][:12]}]")
    if not moves and not copies:
        print("No file operations.")


def _unresolved_pending(project: Project, event_name: str, version: str | None, **fields) -> list[dict]:
    """Pending ledger records of `event_name` for `version` (and the given fields) that never completed."""
    events, _ = project.ledger_events()
    by_seq = {e.get("seq"): e for e in events}
    found = []
    for issue in _open_ledger_issues(events):
        if issue.get("event") != event_name or issue.get("version") != version:
            continue
        if any(issue.get(key) != value for key, value in fields.items()):
            continue
        pending = by_seq.get(issue.get("ref")) if issue["issue"] == "failed" else by_seq.get(issue.get("seq"))
        if pending and pending.get("status") == "pending" and pending not in found:
            found.append(pending)
    return found


def _failure(event: dict, error: Exception) -> ProjectError:
    return ProjectError(f"stopped: {_why(error)}. Ledger event {event['seq']} ({event['event']}) stays pending; "
                        "nothing was deleted. Fix the cause and run the same command again: finished steps are "
                        "recognised and skipped.")


# -- finishing an interrupted index change ----------------------------------------------
#
# intake, author-save and release record in their pending ledger event the target state of the
# indexes ("target": the new VERSION_INDEX entry, the new current and baseline, the package, the
# released figures). The copies and moves run first; the index swap is last. When a run stops
# after the copies and moves (a failed os.replace part-way through the swap, a crash, a full disk),
# the same command run again moves any name.new leftovers into the archive and writes the indexes
# from that target: the result is the same whether none, some or all of the indexes were swapped.

def _open_pending(project: Project, event_name: str) -> list[dict]:
    """Unfinished pending events of `event_name` (any version) that recorded a target, oldest first."""
    events, _ = project.ledger_events()
    by_seq = {e.get("seq"): e for e in events}
    found = []
    for issue in _open_ledger_issues(events):
        if issue.get("event") != event_name:
            continue
        pending = by_seq.get(issue.get("ref")) if issue["issue"] == "failed" else by_seq.get(issue.get("seq"))
        if pending and pending.get("status") == "pending" and pending.get("target") and pending not in found:
            found.append(pending)
    return sorted(found, key=lambda e: e.get("seq") or 0)


def _stopped_after_files(project: Project, event_name: str) -> list[dict]:
    """Unfinished pending events of `event_name` (any version) that recorded a target and whose
    copies and moves are done: only their index swap is left."""
    return [pending for pending in _open_pending(project, event_name) if _file_steps_done(project, pending)]


def _pending_for_moved_source(project: Project, event_name: str, source: Path) -> dict | None:
    """The unfinished intake or author save whose recorded source is `source` when that file is gone:
    its own finished moves took it into the archive (a package file), and the run stopped before the
    copies that refill the package. None while the file exists."""
    if _exists(source):
        return None
    inside = project.inside(source)
    wanted = _norm(project.rel(source)) if inside else source.name
    for pending in reversed(_open_pending(project, event_name)):
        recorded = str(pending.get("source") or "")
        if bool(pending.get("external")) != (not inside):
            continue
        if (_norm(recorded) if inside else recorded) == wanted:
            return pending
    return None


def _remaining_file_steps(project: Project, pending: dict) -> tuple[list, list]:
    """The moves and copies of a pending intake or author save that are not done yet."""
    hashes = Hashes()
    moves = [move for move in pending.get("moves") or []
             if hashes(project.path(move["new"])) != move.get("sha256")]
    copies = [item for item in pending.get("copies") or []
              if hashes(project.path(item["dest"])) != item.get("sha256")]
    return moves, copies


def _finish_file_steps(project: Project, pending: dict, label: str) -> None:
    """Make the copies and moves an interrupted intake or author save recorded but did not finish:
    each from its recorded source, SHA-256 checked, never over an existing file (see _execute_moves
    and _execute_copies). Refuses when the registered copy itself is missing or changed."""
    hashes = Hashes()
    what = str(pending.get("event")).replace("_", " ")
    if hashes(project.path(pending.get("path") or "")) != pending.get("sha256"):
        raise ProjectError(f"the interrupted {what} of {label} (ledger event {pending.get('seq')}) copied the file to "
                           f"{pending.get('path')}, which is now missing or changed (expected sha256 "
                           f"{str(pending.get('sha256'))[:12]}); it cannot be finished from its ledger event. Run "
                           "`msw.py verify-project` and ask the author which file is current.")
    moves = pending.get("moves") or []
    if moves:
        remaining, _ = _remaining_file_steps(project, pending)
        waiting = {id(move) for move in remaining}
        done = [dict(move, sha256_before=move["sha256"], sha256_after=move["sha256"], resumed=True)
                for move in moves if id(move) not in waiting]
        done += _execute_moves(project, remaining)
        events, _ = project.ledger_events()
        logged = any(e.get("event") == "move" and e.get("status") == "complete" and e.get("ref") == pending["seq"]
                     and e.get("reason") != "Interrupted_Run" for e in events)
        if not logged:
            prefix = "Author_Save_" if pending.get("event") == "author_save" else "Package_Superseded_"
            project.ledger_append({"event": "move", "status": "complete", "ref": pending["seq"],
                                   "reason": f"{prefix}{label}", "ops": done})
    _execute_copies(project, pending.get("copies") or [], hashes)


def _file_steps_done(project: Project, pending: dict) -> bool:
    """Did every copy and move of a pending event finish, so that only the index swap is left?"""
    hashes = Hashes()
    if pending.get("path") and pending.get("sha256") and hashes(project.path(pending["path"])) != pending["sha256"]:
        return False
    for item in pending.get("copies") or []:
        if not item.get("dest") or hashes(project.path(item["dest"])) != item.get("sha256"):
            return False
    if pending.get("moves"):
        events, _ = project.ledger_events()
        if not any(e.get("event") == "move" and e.get("status") == "complete" and e.get("ref") == pending.get("seq")
                   and e.get("reason") != "Interrupted_Run" for e in events):
            return False
    return True


def _target_state(project: Project, target: dict, label: str) -> tuple:
    """(VERSION_INDEX entries, PROJECT_INDEX or None, package or None, FIGURE_INDEX or None) after an
    operation, from the target its pending ledger event recorded. Indexes that already hold part or
    all of the target give the same result."""
    want = dict(target["entry"])
    entries = project.version_entries()
    existing = next((e for e in entries if e.get("version") == want.get("version")
                     and e.get("sha256") == want.get("sha256")
                     and _norm(e.get("path") or "") == _norm(want.get("path") or "")), None)
    index = package = figures = None
    if target.get("current"):
        for item in entries:
            if item is not existing and item.get("role") == "current":
                item["role"] = _base_role(item) if item.get("base_role") else "released"
        index = project.project_index()
        index["current"], index["baseline"] = target["current"], target.get("baseline")
        package = [dict(item) for item in target.get("desired") or []]
    if existing is None:
        if "released_utc" in want and not want.get("released_utc"):
            want["released_utc"] = utc_now()
        entries.append(want)
        existing = want
    elif target.get("current"):
        existing["role"] = "current"
    if target.get("last_release") and index is not None:
        index["last_release"] = dict(target["last_release"], utc=existing.get("released_utc"))
    if target.get("figures") and index is not None:
        released = [{"id": key, "version": value} for key, value in target["figures"].items()]
        figures = _record_released_figures(project, released, label, index)
    return entries, index, package, figures


def _resume_problems(project: Project, pending: dict, label: str) -> list[str]:
    """Why an interrupted operation can no longer be finished from its ledger event (the current
    file changed since it was planned)."""
    target = pending.get("target") or {}
    if not target.get("current"):
        return []
    current = project.project_index().get("current") or {}
    if current.get("sha256") in (target.get("expected_current_sha256"), target["current"].get("sha256")):
        return []
    return [f"{PROJECT_INDEX} names {_label_of(current)} (sha256 {str(current.get('sha256'))[:12]}) as current, but "
            f"the interrupted {str(pending.get('event')).replace('_', ' ')} of {label} (ledger event {pending.get('seq')}) "
            f"was planned on sha256 {str(target.get('expected_current_sha256'))[:12]}; it cannot be finished from its "
            "ledger event. Run `msw.py verify-project` and ask the author which file is current."]


def _swap_progress(project: Project, pending: dict, label: str) -> str:
    sha = pending.get("build_sha256") or pending.get("sha256")
    target = pending.get("target") or {}
    held, not_held = [], []
    listed = any(e.get("version") == label and e.get("sha256") == sha for e in project.version_entries())
    (held if listed else not_held).append(VERSION_INDEX.rsplit("/", 1)[-1])
    if target.get("current"):
        current = project.project_index().get("current") or {}
        (held if current.get("sha256") == sha else not_held).append(PROJECT_INDEX)
        manifest = _read_json(project.root / PACKAGE_MANIFEST, {})
        version = manifest.get("version") if isinstance(manifest, dict) else None
        (held if version == label else not_held).append(PACKAGE_MANIFEST.rsplit("/", 1)[-1])
    if not held:
        return "no index names it yet"
    return f"{', '.join(held)} already name{'s' if len(held) == 1 else ''} it" + (
        f", {', '.join(not_held)} not yet" if not_held else "")


def _archive_leftovers(project: Project, pending: dict) -> list[dict]:
    """Move the name.new files an interrupted index swap left into 99_Archive/<date>/Interrupted_Run/
    (by rename, hashed before and after; never deleted) and record the move under the pending event."""
    rels = project.leftovers()
    if not rels:
        return []
    day = today()
    base = next(folder for folder in (f"{ARCHIVE}/{day}/Interrupted_Run" + (f"_{k}" if k > 1 else "")
                                      for k in range(1, 1000))
                if not any(_exists(project.path(f"{folder}/{rel}")) for rel in rels))
    ops = []
    for rel in rels:
        source, dest = project.path(rel), project.path(f"{base}/{rel}")
        before = sha256_file(fs(source))
        move_file(source, dest)
        ops.append({"old": rel, "new": f"{base}/{rel}", "kind": "file", "sha256": before, "sha256_before": before,
                    "sha256_after": sha256_file(fs(dest))})
    project.ledger_append({"event": "move", "status": "complete", "ref": pending["seq"], "reason": "Interrupted_Run",
                           "ops": ops})
    for op in ops:
        print(f"  moved {op['old']}\n    -> {op['new']}  [sha256 {op['sha256'][:12]}]")
    return ops


def _roll_forward(project: Project, pending: dict, label: str) -> list[str]:
    """Finish an interrupted operation whose copies and moves are done: leftovers into the archive,
    then the indexes and generated blocks from the pending event's target. Returns notes."""
    try:
        _archive_leftovers(project, pending)
        entries, index, package, figures = _target_state(project, pending["target"], label)
        return project.commit_indexes(entries, index, package, figures)
    except (OSError, ProjectError) as error:
        project.ledger_append({"event": pending.get("event"), "status": "failed", "ref": pending["seq"],
                               "version": label, "error": _ledger_error(project, error)})
        raise _failure(pending, error) from error


def _index_disagreements(project: Project) -> list[str]:
    """Where PROJECT_INDEX, VERSION_INDEX and PACKAGE_MANIFEST disagree about the current file (an
    index swap that stopped part-way). Indexes msw does not own (not schema_version 2) are skipped."""
    raw = _read_json(project.root / PROJECT_INDEX, None)
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        return []
    index = project.project_index()
    current = index.get("current") or {}
    marked = [e for e in project.version_entries() if e.get("role") == "current"]
    problems = []
    if current:
        if len(marked) != 1 or marked[0].get("sha256") != current.get("sha256") \
                or _norm(marked[0].get("path") or "") != _norm(current.get("path") or ""):
            problems.append(f"{PROJECT_INDEX} names {_label_of(current)} as current, but {VERSION_INDEX} marks "
                            f"{', '.join(_label_of(e) for e in marked) or 'no entry'} as current")
    elif marked:
        problems.append(f"{VERSION_INDEX} marks {', '.join(_label_of(e) for e in marked)} as current, but "
                        f"{PROJECT_INDEX} names no current file")
    manifest = _read_json(project.root / PACKAGE_MANIFEST, {})
    if current and isinstance(manifest, dict) and manifest.get("files"):
        if manifest.get("version") != current.get("version"):
            problems.append(f"{PACKAGE_MANIFEST} is for {manifest.get('version')}, but {PROJECT_INDEX} names "
                            f"{_label_of(current)} as current")
        package_current = next((item for item in project.package_files() if item.get("role") == "current"), None)
        if package_current and package_current.get("sha256") != current.get("sha256"):
            problems.append(f"the package's current copy {package_current.get('path')} is not the file "
                            f"{PROJECT_INDEX} names as current ({_label_of(current)})")
    return problems


# -- init ---------------------------------------------------------------------------

def _msw_in(root: Path) -> tuple[str, str]:
    """How the project's files name the tool, and a note on it: relative to the project when the
    skill is installed in it (for example under .claude/skills/ or .agents/skills/), else the generic
    skill-folder form. Files in the project never hold this machine's path."""
    try:
        return (MSW.resolve().relative_to(Path(root).resolve()).as_posix(),
                "the skill is installed in this project")
    except ValueError:
        return ("<skill folder>/scripts/msw.py",
                "`<skill folder>` is where the skill is installed on the computer you work on")


def _template_values(config: dict, root: Path) -> dict:
    author = config.get("author") or {}
    language = config.get("language") or {}
    msw, msw_note = _msw_in(root)
    return {
        "title": config.get("title", ""), "venue": config.get("venue", ""),
        "kind_label": KIND_LABELS.get(config.get("document_kind"), config.get("document_kind", "")),
        "author_name": author.get("name", ""), "initials": author.get("file_initials", ""),
        "revision_name": author.get("revision_name") or author.get("name", ""),
        "spelling": language.get("spelling", ""), "date": today(), "msw": msw, "msw_note": msw_note,
        "project": root.name,
    }


def _host_only(entry: Path) -> bool:
    return entry.name in HOST_DIRS and entry.is_dir()


def _profile_in_project(root: Path, raw) -> tuple[str, Path]:
    """--journal-profile as manuscript.json keeps it: relative to the project root, never this
    machine's path. A relative path is read against the project, or against the working folder
    when only the file there exists. A profile outside the project is refused: save it in
    05_References/Journal_Guidelines/ and name it there."""
    candidate = Path(raw)
    if not candidate.is_absolute():
        inside = root / candidate
        candidate = inside if _exists(inside) or not _exists(Path.cwd() / candidate) else Path.cwd() / candidate
    candidate = _plain(candidate.resolve())
    try:
        rel = candidate.relative_to(root).as_posix()
    except ValueError:
        rel = "."
    if rel == ".":
        raise ProjectError(f"--journal-profile {raw} is outside the project; save the journal's profile in "
                           "05_References/Journal_Guidelines/ and name it there")
    return rel, candidate


def _missing_rules(path: Path, template_name: str) -> list[str]:
    """Rule lines of an msw template (.gitignore or .gitattributes) that an existing file lacks."""
    try:
        have = {line.strip() for line in _fsp(path).read_text(encoding="utf-8-sig", errors="replace").splitlines()}
    except OSError:
        have = set()
    wanted = [line.strip() for line in template(template_name, {}).splitlines()]
    return [line for line in wanted if line and not line.startswith("#") and line not in have]


def cmd_init(args) -> int:
    root = _plain(Path(args.dir).resolve())
    if _exists(root) and not _is_dir(root):
        raise ProjectError(f"{root} is a file")
    # A folder that holds only an agent host's, an editor's or git's folder (the installed skill in
    # .claude/ or .agents/, for example) is empty for this purpose.
    existing = sorted(entry for entry in _fsp(root).iterdir() if not _host_only(entry)) if _exists(root) else []
    if existing and not args.adopt:
        raise ProjectError(f"{root} is not empty ({len(existing)} entries). Use --adopt to add only the missing "
                           "skeleton files; existing files are never moved or replaced.")
    config_path = root / CONFIG_NAME
    if _exists(config_path):
        config = _read_json(config_path, {})
        write_config = False
        if any((args.title, args.venue, args.author, args.initials, args.revision_name, args.journal_profile)):
            print(f"NOTE: {CONFIG_NAME} exists and was kept; --title/--venue/--author/--initials/--journal-profile "
                  "were not applied.")
    else:
        missing = [flag for flag, value in (("--title", args.title), ("--venue", args.venue),
                                            ("--author", args.author), ("--initials", args.initials)) if not value]
        if missing:
            raise ProjectError("init needs " + ", ".join(missing) + " (the author's name is never guessed)")
        if not re.fullmatch(r"[A-Za-z]{1,8}", args.initials):
            raise ProjectError("--initials must be 1-8 letters")
        if clean_words(args.venue) != args.venue.strip() or " " in args.venue.strip():
            raise ProjectError("--venue must be a short code without spaces or reserved characters (e.g. JOE)")
        # manuscript.json is versioned: the profile is kept relative to the project, as retarget keeps it.
        profile = _profile_in_project(root, args.journal_profile)[0] if args.journal_profile else DEFAULT_PROFILE
        config = {
            "schema": "msw-project/1",
            "title": args.title.strip(),
            "venue": args.venue.strip(),
            "document_kind": args.kind,
            "journal_profile": profile,
            "author": {"name": args.author.strip(), "file_initials": args.initials.strip(),
                       "revision_name": (args.revision_name or args.author).strip()},
            "language": {"spelling": args.spelling, "first_person": "allow"},
            "reviewer": {"tool": "codex", "model": "", "effort": "high"},
            "naming": DEFAULT_NAMING,
            "word_count": {"method": "house"},
            "figures_backend": args.figures_backend,
            # comments: questions about the science are Word comments (the default);
            # tracked_for_review: science edits are tracked changes the author reviews.
            "policy": {"science_changes": args.science_changes},
        }
        write_config = True
    _makedirs(root)
    created, kept = [], []

    def new_file(rel, text):
        path = root / rel
        if _exists(path):
            kept.append(rel)
            return False
        write_new_text(path, text)
        created.append(rel)
        return True

    for rel in TREE:
        if not _is_dir(root / rel):
            _makedirs(root / rel)
            created.append(rel + "/")
    if write_config:
        write_json_exclusive(config_path, config)
        created.append(CONFIG_NAME)
    else:
        kept.append(CONFIG_NAME)
    values = _template_values(config, root)
    index = {"schema_version": 2, "project": config.get("title", ""), "author_initials": values["initials"],
             "venue": config.get("venue", ""), "target_journal": args.target_journal or config.get("venue", ""),
             "current": None, "baseline": None, "package": PACKAGE, "figure_set": {},
             "outstanding": [], "updated": utc_now()}
    new_file(PROJECT_INDEX, _json_text(index))
    index = Project(root).project_index()
    new_file(VERSION_INDEX, _json_text([]))
    new_file(FIGURE_INDEX, _json_text({"schema_version": 2, "figures": []}))
    entries = _read_json(root / VERSION_INDEX, [])
    entries = entries.get("versions", []) if isinstance(entries, dict) else entries
    new_file(VERSION_INDEX_MD, render(VERSION_INDEX_MD_TEXT, {"versions_block": render_versions_block(entries)}))
    try:
        existing_figures = _read_json(root / FIGURE_INDEX, {"figures": []})
    except ProjectError:
        existing_figures = {"figures": []}      # an adopted, unreadable index is kept and reported elsewhere
    new_file(FIGURES_README, render(FIGURES_README_TEXT, {"figures_block": render_figures_block(existing_figures)}))
    new_file(PACKAGE_MANIFEST, _json_text({"schema_version": 2, "version": None, "assembled": None, "files": []}))
    package = Project(root).package_files()
    block_values = dict(values, current_block=render_current_block(index, package),
                        status_block=render_status_block(index, package))
    new_file(README, template("README.md.tmpl", block_values))
    if not new_file("AGENTS.md", template("AGENTS.md.tmpl", values)):
        alternative = "06_Project_Docs/Workflows/AGENTS.msw.md"
        if new_file(alternative, template("AGENTS.md.tmpl", values)):
            print(f"NOTE: AGENTS.md exists and was kept; the manuscript policy was written to {alternative}. "
                  "Link or merge it into AGENTS.md by hand.")
    new_file("CLAUDE.md", template("CLAUDE.md.tmpl", values))
    new_file(STATUS, template("STATUS.md.tmpl", block_values))
    new_file(DECISIONS, template("DECISIONS.md.tmpl", values))
    git_warnings = []
    for rel, name, why in ((".gitignore", "gitignore.tmpl",
                            "build scratch, proof records with machine paths and Word or LibreOffice lock files "
                            "would be committed"),
                           (".gitattributes", "gitattributes.tmpl",
                            "without `* -text` a checkout can change line endings (and recorded SHA-256 values); "
                            "without the LFS lines Word, PDF and image files are stored as ordinary git objects")):
        if not new_file(rel, template(name, values)):
            missing = _missing_rules(root / rel, name)
            if missing:
                git_warnings.append(f"WARNING: {rel} exists and was kept, but it lacks these manuscript rules ({why}). "
                                    "Add them by hand before the first commit:\n"
                                    + "\n".join(f"    {line}" for line in missing))
    project = Project(root)
    appended = []
    if _exists(root / LEDGER):
        appended.append(LEDGER)
    else:
        created.append(LEDGER)
    project.ledger_append({"event": "init", "by": "msw init", "adopt": bool(existing),
                           "note": "adopted an existing folder" if existing else "project created",
                           "created": created})
    print(f"{'Adopted' if existing else 'Created'} manuscript project at {root}")
    for rel in created:
        print(f"  + {rel}")
    for rel in appended:
        print(f"  = {rel} (kept; the init event was appended)")
    for rel in kept:
        print(f"  = {rel} (kept as it was)")
    for line in git_warnings:
        print(line)
    if args.git:
        for line in _git_setup(root):
            print(line)
    warning = root_warning(root)
    if warning:
        print(f"WARNING: {warning}")
    print(f"Next: register the starting draft with\n  python \"{MSW.as_posix()}\" intake <draft.docx> "
          f"--as-version V01 --project \"{root}\"")
    return 0


def _git_setup(root: Path) -> list[str]:
    git = shutil.which("git")
    if not git:
        return ["WARNING: git is not on PATH; git setup skipped"]
    notes = []

    def run(*command):
        return subprocess.run([git, "-C", str(root), *command], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)

    if not (root / ".git").exists():
        result = run("init")
        if result.returncode != 0:
            return [f"WARNING: git init failed: {result.stderr.strip()}"]
        notes.append("git: initialised a repository")
    else:
        notes.append("git: repository already present; kept")
    for key, value in (("core.autocrlf", "false"), ("core.longpaths", "true"), ("gc.auto", "0")):
        result = run("config", "--local", key, value)
        notes.append(f"git: {key}={value}" if result.returncode == 0 else f"WARNING: could not set {key}")
    if run("lfs", "version").returncode == 0:
        result = run("lfs", "install", "--local")
        if result.returncode != 0:
            notes.append(f"WARNING: git lfs install --local failed: {result.stderr.strip()}")
        else:
            # The filters are installed; whether .docx files go to LFS depends on .gitattributes.
            attributes = run("check-attr", "filter", "text", "--", "probe.docx", "probe.md")
            values = {}
            for line in attributes.stdout.splitlines():
                path, _, rest = line.partition(": ")
                key, _, value = rest.partition(": ")
                values[(path.strip(), key.strip())] = value.strip()
            if values.get(("probe.docx", "filter")) == "lfs":
                notes.append("git: Git LFS enabled for this repository")
            else:
                notes.append("WARNING: Git LFS filters are installed, but .gitattributes does not send .docx files "
                             "to LFS; add the LFS lines printed above (or from the skill's gitattributes template) "
                             "before the first commit")
            if values.get(("probe.md", "text")) != "unset":
                notes.append("WARNING: .gitattributes has no `* -text` line, so line endings of text records are not "
                             "protected; add it before the first commit")
    else:
        notes.append("WARNING: Git LFS not found; Word, PDF and image files would be stored as ordinary git "
                     "objects. Install Git LFS, then run `git lfs install --local` here.")
    notes.append("Make the first commit after the author agrees (not run):\n"
                 f"  git -C \"{root}\" add -A\n  git -C \"{root}\" commit -m \"Initialise manuscript workspace\"")
    return notes


# -- intake and author-save -----------------------------------------------------------

def _claimed_numbers(project: Project) -> set[int]:
    numbers = set()
    passes = project.path(PASSES)
    if _is_dir(passes):
        for entry in _fsp(passes).iterdir():
            match = PASS_DIR_RE.match(entry.name)
            if match and entry.is_dir():
                numbers.add(int(match.group(1)))
    claims = project.path(CLAIMS)
    if _is_dir(claims):
        for entry in _fsp(claims).iterdir():
            match = re.match(r"^V(\d+)\.json$", entry.name)
            if match:
                numbers.add(int(match.group(1)))
    events, _ = project.ledger_events()
    for event in events:
        if event.get("event") == "claim":
            number = _number(event)
            if number:
                numbers.add(number)
    return numbers


def _released_numbers(entries: list) -> set[int]:
    return {n for n in (_number(e) for e in entries if _base_role(e) == "released") if n}


def _unique_dest(project: Project, hashes: Hashes, folder: str, name: str, sha: str, allow_suffix: bool) -> str:
    stem, ext = os.path.splitext(name)
    for counter in range(1, 100):
        candidate = name if counter == 1 else f"{stem} {counter}{ext}"
        rel = f"{folder}/{candidate}"
        existing = hashes(project.path(rel))
        if existing is None and not _exists(project.path(rel)):
            return rel
        if existing == sha:
            return rel          # already copied by an interrupted run: reuse it
        if not allow_suffix:
            raise ProjectError(f"{rel} already exists with other content; versions are never overwritten")
    raise ProjectError(f"too many files named like {name} in {folder}")


def _register(project: Project, source: Path, *, number: int, role: str, state: str, slug: str, day: str,
              make_current: bool, event: str, reason: str, dry_run: bool, extra: dict | None = None) -> int:
    hashes = Hashes()
    sha = hashes(source)
    label = vlabel(number)
    unfinished = [e for e in _unresolved_pending(project, event, label)
                  if e.get("sha256") == sha and e.get("role") == role and e.get("target")]
    if unfinished and _file_steps_done(project, unfinished[-1]):
        return _resume_register(project, unfinished[-1], label, dry_run)
    entries = project.version_entries()
    for entry in entries:
        if entry.get("sha256") == sha and _base_role(entry) == role and entry.get("version") == label:
            unfinished = [e for e in _unresolved_pending(project, event, label) if e.get("sha256") == sha]
            if unfinished and not dry_run:
                notes = refresh_docs(project)
                project.ledger_append({"event": event, "status": "complete", "ref": unfinished[-1]["seq"],
                                       "version": label, "role": role, "path": entry.get("path"), "sha256": sha,
                                       "resumed": True})
                for note in notes:
                    print(f"NOTE: {note}")
                print(f"Finished the interrupted {event.replace('_', ' ')} of {entry.get('path')}: the indexes were "
                      "already updated; generated blocks refreshed and the ledger event completed.")
                return 0
            print(f"Already registered as {entry.get('path')} ({label}, {role}); nothing to do.")
            return 0
    folder = INCOMING if role == "incoming" else f"{VERSIONS}/{label}"
    name = file_name(project.config, date=day, number=number, slug=slug, state=state)
    dest_rel = _unique_dest(project, hashes, folder, name, sha, allow_suffix=(role != "released"))
    entry = {"version": label, "date": day, "role": role, "state": state, "slug": slug, "path": dest_rel,
             "sha256": sha, "source_name": source.name}
    entry.update(extra or {})
    index = project.project_index()
    old_current = index.get("current")
    moves, copies, problems = [], [], []
    new_current = new_baseline = desired = None
    if make_current:
        entry["base_role"], entry["role"] = role, "current"
        new_current = _pointer(entry)
        new_baseline = _pointer(old_current)
        extras = [item for item in project.package_files() if item.get("role") not in ("current", "baseline")]
        desired = _desired_package(new_current, new_baseline, extras)
        listed = {_norm(item.get("path", "")) for item in project.package_files()}
        extra_out = []
        if project.inside(source):
            rel_source = project.rel(source)
            if (_norm(rel_source).startswith(_norm(PACKAGE) + "/") and _norm(rel_source) not in listed
                    and _norm(rel_source) not in {_norm(d["path"]) for d in desired}):
                extra_out.append(rel_source)
        moves, copies, problems = _plan_package(project, hashes, desired, reason, today(), {sha: dest_rel}, extra_out)
        locks = find_locks(project.path(PACKAGE), recursive=True)
        if locks:
            problems.append("Word or LibreOffice lock files in the package: " + ", ".join(project.rel(p) for p in locks))
    print(f"{event.replace('_', ' ').capitalize()}: {project.rel(source)}")
    print(f"  -> {dest_rel}  [{label}, role {role}, sha256 {sha[:12]}]")
    if make_current:
        print(f"  becomes current; baseline: {_label_of(new_baseline)}")
    _print_operations(moves, copies)
    if problems:
        raise ProjectError(*problems)
    project.refuse_leftovers()
    if dry_run:
        print("Dry run: nothing was changed.")
        return 0
    target = {"entry": entry}
    if make_current:
        target.update({"current": new_current, "baseline": new_baseline, "desired": desired,
                       "expected_current_sha256": (old_current or {}).get("sha256")})
    # A source outside the project is recorded by its name and SHA-256 only: the ledger is versioned
    # and never holds this machine's folders.
    origin = {"source": project.rel(source)} if project.inside(source) else {"source": source.name, "external": True}
    pending = project.ledger_append({"event": event, "status": "pending", "version": label, "role": role,
                                     **origin, "sha256": sha, "path": dest_rel,
                                     "moves": moves, "copies": copies, **(extra or {}), "target": target})
    try:
        dest = project.path(dest_rel)
        if not _exists(dest):
            if copy_exclusive(source, dest) != sha:
                raise ProjectError(f"{dest_rel} does not match {origin['source']} after copying; it is kept for "
                                   "inspection")
        done = _execute_moves(project, moves)
        if done:
            project.ledger_append({"event": "move", "status": "complete", "ref": pending["seq"], "reason": reason,
                                   "ops": done})
        _execute_copies(project, copies, hashes)
        entries, index, package, _ = _target_state(project, target, label)
        notes = project.commit_indexes(entries, index, package)
    except (OSError, ProjectError) as error:
        project.ledger_append({"event": event, "status": "failed", "ref": pending["seq"], "version": label,
                               "error": _ledger_error(project, error)})
        raise _failure(pending, error) from error
    project.ledger_append({"event": event, "status": "complete", "ref": pending["seq"], "version": label,
                           "role": role, "path": dest_rel, "sha256": sha})
    for note in notes:
        print(f"NOTE: {note}")
    print(f"Registered {dest_rel}")
    return 0


def _resume_register(project: Project, pending: dict, label: str, dry_run: bool) -> int:
    """Finish an intake or author save from its pending ledger event: the copies and moves it did
    not make (when it stopped part-way through them), then the indexes (see _roll_forward)."""
    what = str(pending.get("event")).replace("_", " ")
    problems = _resume_problems(project, pending, label)
    if problems:
        raise ProjectError(*problems)
    leftovers = project.leftovers()
    files_done = _file_steps_done(project, pending)
    if files_done:
        print(f"The {what} of {pending.get('path')} ({label}, ledger event {pending['seq']}) never completed: its "
              f"copies are done; {_swap_progress(project, pending, label)}.")
    else:
        print(f"The {what} of {pending.get('path')} ({label}, ledger event {pending['seq']}) stopped part-way through "
              "its copies and moves; it is finished from that ledger event. Steps left, each from its recorded "
              "source and SHA-256 checked:")
        _print_operations(*_remaining_file_steps(project, pending))
    if dry_run:
        steps = (([] if files_done else ["makes those steps"])
                 + ([f"moves {', '.join(leftovers)} into {ARCHIVE}/<date>/Interrupted_Run/"] if leftovers else [])
                 + ["writes the indexes and generated blocks from that ledger event"])
        print("Run the same command without --dry-run to finish it: it " + ", then ".join(steps) + ".")
        print("Dry run: nothing was changed.")
        return 0
    if not files_done:
        try:
            _finish_file_steps(project, pending, label)
        except (OSError, ProjectError) as error:
            project.ledger_append({"event": pending.get("event"), "status": "failed", "ref": pending["seq"],
                                   "version": label, "error": _ledger_error(project, error)})
            raise _failure(pending, error) from error
    notes = _roll_forward(project, pending, label)
    project.ledger_append({"event": pending.get("event"), "status": "complete", "ref": pending["seq"], "version": label,
                           "role": pending.get("role"), "path": pending.get("path"), "sha256": pending.get("sha256"),
                           "resumed": True})
    for note in notes:
        print(f"NOTE: {note}")
    print(f"Finished the interrupted {what} of {pending.get('path')}: the indexes and generated blocks were written "
          f"from ledger event {pending['seq']}.")
    print(f"Registered {pending.get('path')}")
    return 0


def cmd_intake(args) -> int:
    project = _project(args)
    project.require_schema()
    source = _plain(Path(args.docx).resolve())
    # An intake of a package file that stopped after its own moves took that file into the archive
    # is finished from its ledger event.
    moved = _pending_for_moved_source(project, "intake", source)
    if moved is not None:
        return _resume_register(project, moved, moved.get("version"), args.dry_run)
    _check_word_file(source)
    role = args.role
    day = args.date or today()
    entries = project.version_entries()
    current = project.project_index().get("current")
    sha = sha256_file(fs(source))
    stopped = [pending for pending in _open_pending(project, "intake")
               if pending.get("sha256") == sha and pending.get("role") == role and pending.get("version")]
    if args.as_version:
        number = parse_version(args.as_version)
    elif stopped:
        # the same intake run again after it stopped part-way keeps the version it chose then
        number = parse_version(stopped[-1]["version"])
    elif not entries and not current:
        number = 1
    elif role == "author_saved" and current:
        # A save of the current version (the author's own, or a supervisor's or co-author's return
        # of the copy they were sent) keeps its number: no version number is spent.
        number = parse_version(current["version"])
    else:
        raise ProjectError("give --as-version: which version does this file belong to (e.g. V07)?")
    # The same intake run again after it stopped part-way (VERSION_INDEX may already list the file as
    # released): _register finishes it from its pending ledger event, so the reuse guards below
    # would wrongly refuse it.
    resuming = any(pending.get("sha256") == sha and pending.get("role") == role
                   for pending in _unresolved_pending(project, "intake", vlabel(number)))
    if role == "released" and not resuming:
        if number in _released_numbers(entries):
            raise ProjectError(f"{vlabel(number)} is already released; version numbers are never reused")
        if number in _claimed_numbers(project):
            raise ProjectError(f"{vlabel(number)} is claimed by a pass; release that pass or use another number")
    if args.set_current and role == "incoming":
        raise ProjectError("incoming files are never current; register the file the work builds on as "
                           "--role released or author_saved")
    if role == "released":
        # The project's first file is the baseline: its state says so (the file name reads
        # "... V01 baseline.docx" as before, now from the state rather than the slug).
        slug = args.slug or ""
        state = args.state if args.state is not None else ("baseline" if not current else "")
    elif role == "incoming":
        slug = args.slug or ""
        state = args.state if args.state is not None else "received"
    else:
        slug = args.slug or ""
        state = args.state if args.state is not None else _auto_state(source)
    make_current = role != "incoming" and (current is None or args.set_current)
    extra = {}
    if args.note:
        extra["note"] = _scrub_text(project, args.note)      # the note is versioned: no machine paths
    returned_by = _returned_by(args.returned_by, args.state, role)
    if returned_by:
        extra["returned_by"] = returned_by
    return _register(project, source, number=number, role=role, state=state, slug=slug, day=day,
                     make_current=make_current, event="intake", reason=f"Package_Superseded_{vlabel(number)}",
                     dry_run=args.dry_run, extra=extra or None)


RETURN_STATE_RE = re.compile(r"^(?P<who>.+?)\s+(?:comments|edits|review|reviewed|markup|changes|return|returned)$",
                             re.IGNORECASE)


def _returned_by(given, state, role) -> str | None:
    """Who returned the file: --returned-by, else the name in a state such as "JD comments" (a
    supervisor's or co-author's copy). The author's own saves and plain intakes name nobody."""
    if given:
        return " ".join(str(given).split()) or None
    if role not in ("author_saved", "incoming") or not state:
        return None
    match = RETURN_STATE_RE.match(" ".join(str(state).split()))
    if not match or match.group("who").casefold() in ("author", "the author", "author's"):
        return None
    return match.group("who")


def cmd_author_save(args) -> int:
    project = _project(args)
    project.require_schema()
    source = _plain(Path(args.docx).resolve())
    # A save of the package's current copy that stopped after its moves took that copy into the
    # archive, before the copies that refill the package: finish it from its ledger event.
    moved = _pending_for_moved_source(project, "author_save", source)
    if moved is not None:
        return _resume_register(project, moved, moved.get("version"), args.dry_run)
    _check_word_file(source)
    current = project.project_index().get("current")
    if not current:
        raise ProjectError("no current version; register the first file with `msw.py intake`")
    # An author save that stopped after its copies and moves is finished first: its moves put the
    # released copy back in the package, so the file named now may no longer hold the save.
    stopped = [pending for pending in _stopped_after_files(project, "author_save")
               if not _resume_problems(project, pending, pending.get("version"))]
    if stopped:
        pending = stopped[-1]
        code = _resume_register(project, pending, pending.get("version"), args.dry_run)
        if Hashes()(source) not in (None, pending.get("sha256")):
            print(f"If {source.name} holds a later save, run `msw.py author-save` on it again.")
        return code
    item = _package_copy_of(project, source)
    if item is not None and item.get("role") != "current":
        raise ProjectError(_not_current_copy(item))
    number = parse_version(args.version) if args.version else parse_version(current["version"])
    sha = sha256_file(fs(source))
    if sha == current.get("sha256"):
        print(f"{source.name} is identical to the current file ({current.get('version')}); nothing to register.")
        return 0
    state = {"accepted": "author accepted", "saved": "author saved"}.get(args.state) or _auto_state(source)
    extra = {"derived_from_sha256": current.get("sha256"), "expected_sha256": current.get("sha256"),
             "found_sha256": sha}
    return _register(project, source, number=number, role="author_saved", state=state, slug="",
                     day=args.date or today(), make_current=True, event="author_save",
                     reason=f"Author_Save_{vlabel(number)}", dry_run=args.dry_run, extra=extra)


# -- pass new -----------------------------------------------------------------------

def _next_number(project: Project) -> int:
    numbers = set(_claimed_numbers(project))
    numbers |= {n for n in (_number(e) for e in project.version_entries()) if n}
    versions = project.path(VERSIONS)
    if _is_dir(versions):
        for entry in _fsp(versions).iterdir():
            match = re.match(r"^V(\d+)$", entry.name)
            if match and entry.is_dir():
                numbers.add(int(match.group(1)))
    return max(numbers, default=0) + 1


def _package_item(project: Project, role: str) -> dict | None:
    return next((item for item in project.package_files() if item.get("role") == role), None)


def _package_copy_of(project: Project, path) -> dict | None:
    """The PACKAGE_MANIFEST entry whose file is `path`, if any."""
    if not project.inside(path):
        return None
    rel = _norm(project.rel(path))
    return next((item for item in project.package_files() if _norm(item.get("path") or "") == rel), None)


def _not_current_copy(item: dict) -> str:
    """Advice for a changed package copy that is not the current file (the baseline, say)."""
    role = str(item.get("role") or "listed").replace("_", " ")
    return (f"{item.get('path')} is the package's {role} copy ({item.get('version')}), not the current file; "
            "registering it as an author save would make older text current. Keep the author's save with "
            f"`msw.py intake \"{item.get('path')}\" --role incoming --as-version {item.get('version')}` and carry "
            "the author's edits into the current version in a pass.")


def cmd_pass_new(args) -> int:
    project = _project(args)
    project.require_schema()
    slug = clean_words(args.slug)
    if not slug:
        raise ProjectError("--slug needs two to six plain words, e.g. \"methods clarifications\"")
    index = project.project_index()
    current = index.get("current")
    if not current:
        raise ProjectError("no current version: register the starting file first with "
                           "`msw.py intake <draft.docx> --as-version V01`")
    disagreements = _index_disagreements(project)
    if disagreements:
        raise ProjectError(*disagreements, "the indexes disagree about the current file (an interrupted release, "
                           "intake or author save?); run the interrupted command again (see `msw.py status`) before "
                           "starting a pass")
    hashes = Hashes()
    if hashes(project.path(current["path"])) != current.get("sha256"):
        raise ProjectError(f"the current file {current['path']} no longer matches its recorded SHA-256; "
                           "run `msw.py verify-project`")
    package_copy = _package_item(project, "current")
    if package_copy and _is_file(project.path(package_copy["path"])) \
            and hashes(project.path(package_copy["path"])) != package_copy.get("sha256"):
        raise ProjectError(f"{package_copy['path']} changed after it was registered (saved in Word?). Register it "
                           f"first: msw.py author-save \"{package_copy['path']}\"")
    for item in project.package_files():
        if (item.get("role") != "current" and str(item.get("path", "")).lower().endswith(".docx")
                and _check(project, hashes, item.get("path"), item.get("sha256")) == "changed"):
            print(f"WARNING: {_not_current_copy(item)}")
    entries = project.version_entries()
    released = _released_numbers(entries)
    current_number = parse_version(current["version"])
    explicit = parse_version(args.version) if args.version else None
    if explicit is not None and explicit <= current_number:
        raise ProjectError(f"{vlabel(explicit)} is not after the current version {current['version']}; "
                           "version numbers only go up")
    number = explicit or _next_number(project)
    claims = project.path(CLAIMS)
    _makedirs(claims)
    folder_slug = slug.replace(" ", "_")
    day = today()
    claimed = False
    for _ in range(100):
        label = vlabel(number)
        taken = number in released or number in _claimed_numbers(project)
        if not taken:
            try:
                handle = open(fs(claims / f"{label}.json"), "x", encoding="utf-8", newline="\n")
            except FileExistsError:
                taken = True
            else:
                with handle:
                    handle.write(_json_text({"version": label, "slug": slug, "utc": utc_now(),
                                             "pass": f"{PASSES}/{label}_{folder_slug}"}))
                claimed = True
                break
        if explicit is not None:
            raise ProjectError(f"{label} is already claimed or released; a pass for that number exists")
        number += 1
    if not claimed:
        raise ProjectError("could not claim a version number")
    pass_rel = f"{PASSES}/{label}_{folder_slug}"
    pass_dir = project.path(pass_rel)
    os.mkdir(fs(pass_dir))
    for sub in ("build01", "qa", "proofs", "review"):
        os.mkdir(fs(pass_dir / sub))
    source = _pointer(current)
    project.ledger_append({"event": "claim", "version": label, "slug": slug, "pass": pass_rel,
                           "source_version": current.get("version"), "source_sha256": current.get("sha256")})
    # Short working names inside the pass folder keep paths clear of Windows' 260-character limit;
    # the released copy gets the full name from pass.json (see release_file_name).
    build_name = f"{label} tracked.docx"
    info = {"schema": "msw-pass/1", "version": label, "number": number, "slug": slug, "date": day,
            "claimed_utc": utc_now(), "source": source, "author": project.revision_name,
            "build_name": build_name, "edits": "edits.json", "changes": f"{label}_CHANGES.md",
            "figures_changed": {}}
    release_name = release_file_name(project.config, info)
    write_json_exclusive(pass_dir / "pass.json", info)
    write_json_exclusive(pass_dir / "edits.json", {"edits": []})
    write_new_text(pass_dir / f"{label}_CHANGES.md", template("CHANGES.md.tmpl", {
        "version": label, "slug": slug, "date": day, "build_name": build_name, "release_name": release_name,
        "source_label": _label_of(current), "source_path": current.get("path"),
        "source_sha_short": str(current.get("sha256"))[:12], "author": project.revision_name}))
    build_rel = f"{pass_rel}/build01/{build_name}"
    print(f"Claimed {label} for \"{slug}\": {pass_rel}")
    print(f"  source: {_label_of(current)}  {current['path']}  (sha256 {str(current['sha256'])[:12]})")
    print(f"  released as: {VERSIONS}/{label}/{release_name}")
    print("Write the edits into edits.json, then build from the project folder:")
    print(f"  cd \"{project.root}\"")
    print(f"  python \"{MSW.as_posix()}\" build \"{current['path']}\" \"{pass_rel}/edits.json\" --out \"{build_rel}\"")
    print("Render the accepted view and look at it (a new folder per proof: p1, p2, ...):")
    print(f"  python \"{MSW.as_posix()}\" proof \"{build_rel}\" --out \"{pass_rel}/proofs/p1\"")
    print(f"Then get an independent review, complete {label}_CHANGES.md and run:")
    print(f"  python \"{MSW.as_posix()}\" release plan --pass \"{pass_rel}\"")
    print("If the pass swaps figures, promote each figure version (msw.py figure promote) and list it in pass.json "
          "\"figures_changed\", e.g. {\"Figure_01\": \"v2\"}.")
    warning = root_warning(project.root)
    if warning:
        print(f"WARNING: {warning}")
    return 0


def release_file_name(config: dict, info: dict) -> str:
    """The released file's name, from pass.json (date, version, slug) and manuscript.json (initials,
    venue, naming), whatever the build file in the pass folder is called."""
    number = parse_version(info.get("version", ""))
    return file_name(config, date=info.get("date") or today(), number=number, slug=info.get("slug", ""),
                     state="tracked verified")


# -- release ------------------------------------------------------------------------

def _find_build(pass_dir: Path) -> Path:
    def order(folder):
        digits = re.sub(r"\D", "", folder.name)
        return (int(digits) if digits else 0, folder.name)

    folders = sorted((d for d in _fsp(pass_dir).glob("build*") if d.is_dir()), key=order)
    for folder in reversed(folders):
        builds = [p for p in folder.glob("*.docx") if not is_lock(p.name) and "GATE FAILED" not in p.name]
        if len(builds) == 1:
            return _plain(builds[0])
        if len(builds) > 1:
            raise ProjectError(f"{_plain(folder)} holds {len(builds)} builds; name the released one with --build")
    raise ProjectError(f"no build found in {pass_dir}/build*/; run the build command first or pass --build")


def _resolve_pass(project: Project, text: str) -> Path:
    candidate = Path(text)
    if not candidate.is_absolute() and not _exists(candidate):
        candidate = project.path(text)
    candidate = _plain(candidate.resolve())
    if not _is_file(candidate / "pass.json"):
        raise ProjectError(f"{candidate} is not a pass folder (no pass.json)")
    return candidate


def _figure_pairs(values, source: str) -> list[tuple[str, str]]:
    """[(figure id, version label)] from ["ID=vNN", ...] or {"ID": "vNN"}."""
    if not values:
        return []
    raw = []
    if isinstance(values, dict):
        raw = [(str(key), str(value)) for key, value in values.items()]
    elif isinstance(values, (list, tuple)):
        for value in values:
            key, sign, version = str(value).partition("=")
            if not sign:
                raise ProjectError(f"{source}: {value!r} is not ID=vNN (for example Figure_01=v02)")
            raw.append((key.strip(), version.strip()))
    else:
        raise ProjectError(f"{source} must be a list of \"ID=vNN\" or an object {{\"ID\": \"vNN\"}}")
    pairs, seen = [], set()
    for key, version in raw:
        identifier, label = figure_id(key), figure_version_label(version)
        if _fkey(identifier) in seen:
            raise ProjectError(f"{source} names {identifier} twice")
        seen.add(_fkey(identifier))
        pairs.append((identifier, label))
    return pairs


def _shown_promoted_figures(project: Project, build: Path, pairs: list) -> tuple[list, list]:
    """Figures whose promoted version the build shows (a picture with its embed PNG's bytes) but the
    package does not hold yet and the release does not name: ([(id, version)] to include as if
    named with --figures, problems). A picture whose bytes match the promoted versions of two or
    more figures is ambiguous and is refused unless the release names one of them."""
    try:
        items = _figure_items(project.figure_index())
    except ProjectError:
        return [], []
    promoted = [(figure, _figure_version(figure, figure.get("current"))) for figure in items]
    promoted = [(figure, version) for figure, version in promoted if version is not None and _embed_sha(version)]
    if not promoted:
        return [], []
    pictures, _ = _read_pictures(build)
    if pictures is None:
        return [], []
    shown = {p.get("sha256") for p in pictures if p.get("sha256")}
    named = {_fkey(identifier) for identifier, _ in pairs}
    held = {(_fkey(item.get("figure")), _fv_key(item.get("figure_version")))
            for item in project.package_files() if item.get("role") == "figure"}
    by_picture = {}
    for figure, version in promoted:
        if _embed_sha(version) in shown:
            by_picture.setdefault(_embed_sha(version), []).append((figure, version))
    added, problems = [], []
    for sha, hits in by_picture.items():
        if any(_fkey(figure.get("id")) in named for figure, _ in hits):
            continue
        if len(hits) > 1:
            problems.append("the build shows a picture (sha256 " + sha[:12] + ") whose bytes match the promoted "
                            "versions of " + ", ".join(f"{f.get('id')} {v.get('version')}" for f, v in hits)
                            + "; name the figure it is with --figures ID=vNN")
            continue
        figure, version = hits[0]
        if (_fkey(figure.get("id")), _fv_key(version.get("version"))) not in held:
            added.append((figure["id"], version["version"]))
    return added, problems


def _plan_release_figures(project: Project, hashes: Hashes, pairs: list, build: Path, manifest: dict,
                          label: str) -> tuple[list, list, list, list]:
    """For each (figure, version) of this release: checks, and the package entries of its PDF and PNG."""
    plan, extras, problems, warnings = [], [], [], []
    declared = [p for p in ((manifest.get("declared") or {}).get("pictures") or []) if isinstance(p, dict)]
    if not pairs:
        if declared:
            warnings.append(f"the build swaps {len(declared)} picture(s) but no figure is named with --figures (or "
                            "figures_changed in pass.json); FIGURE_INDEX will not record the change")
        return plan, extras, problems, warnings
    try:
        figures = project.figure_index_for_update()
    except ProjectError as error:
        return plan, extras, error.problems, warnings
    pictures, picture_error = _read_pictures(build)
    embeds = set()
    for identifier, wanted in pairs:
        figure = _find_figure(figures, identifier)
        if figure is None:
            problems.append(f"{identifier} is not in {FIGURE_INDEX}; add it with `msw.py figure add`")
            continue
        version = _figure_version(figure, wanted)
        if version is None:
            problems.append(f"{figure['id']} {wanted} is not registered; register it with `msw.py figure register`")
            continue
        name = version.get("version")
        if _fv_key(figure.get("current")) != _fv_key(name):
            problems.append(f"{figure['id']} {name} is not the promoted version (current: {figure.get('current') or 'none'});"
                            f" run `msw.py figure promote --id {figure['id']} --version {name}` first")
            continue
        files = [(kind, rel, sha) for kind, rel, sha in _version_files(version) if kind in PACKAGED_KINDS]
        if not files:
            problems.append(f"{figure['id']} {name} has no registered PDF or PNG to put in the package")
        for kind, rel, sha in files:
            check = _check(project, hashes, rel, sha)
            if check != "ok":
                problems.append(f"{figure['id']} {name} {kind} {rel} is {check} (registered sha256 {sha[:12]})")
            extras.append({"path": f"{PACKAGE}/Figures/{Path(rel).name}", "sha256": sha, "role": "figure",
                           "version": label, "source": rel, "figure": figure["id"], "figure_version": name})
        embed = _embed_sha(version)
        shown = None
        if embed:
            embeds.add(embed)
            if pictures is None:
                problems.append(f"cannot read the pictures in {build.name}: {picture_error}")
            else:
                shown = next((p for p in pictures if p.get("sha256") == embed), None)
                if shown is None:
                    problems.append(f"the build does not show {figure['id']} {name}: no picture in its accepted view has "
                                    f"the bytes of the embed PNG {version['embed'].get('path')} (sha256 {embed[:12]}); "
                                    "swap the picture in this pass, or leave the figure out of this release")
        plan.append({"id": figure["id"], "version": name, "files": [rel for _, rel, _ in files],
                     "embed_sha256": embed, "picture": (shown or {}).get("name")})
    for picture in declared:
        if picture.get("sha256") not in embeds:
            warnings.append(f"the build swaps picture {picture.get('old_name')!r} -> {picture.get('new_name')!r}, but no "
                            "figure in this release has that embed PNG; FIGURE_INDEX will not record it")
    return plan, extras, problems, warnings


def _proof_source(hashes: Hashes, pdf: Path) -> tuple[str | None, dict | None]:
    """(SHA-256 of the .docx a proof PDF was rendered from, the proof.json record) from the
    proof.json `msw.py proof` wrote beside it; (None, None) when no record describes this PDF."""
    record_path = Path(pdf).parent / "proof.json"
    if not _is_file(record_path):
        return None, None
    try:
        record = _read_json(record_path, None)
    except ProjectError:
        return None, None
    if not isinstance(record, dict) or not isinstance(record.get("pdf"), dict) \
            or record["pdf"].get("sha256") != hashes(pdf):
        return None, None
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    return source.get("sha256"), record


def plan_release(project: Project, pass_text: str, build_text=None, manifest_text=None, proof_text=None,
                 figures=None) -> dict:
    hashes = Hashes()
    problems, warnings = [], []
    pass_dir = _resolve_pass(project, pass_text)
    info = _read_json(pass_dir / "pass.json", {})
    number = parse_version(info.get("version", ""))
    label, slug = vlabel(number), info.get("slug", "")
    source = info.get("source") or {}
    entries = project.version_entries()
    if number in _released_numbers(entries):
        raise ProjectError(f"{label} is already released")
    build = _plain(Path(build_text).resolve()) if build_text else _find_build(pass_dir)
    if not _is_file(build):
        raise ProjectError(f"build {build} does not exist")
    manifest_path = (_plain(Path(manifest_text).resolve()) if manifest_text
                     else build.with_name(build.stem + ".manifest.json"))
    if not _is_file(manifest_path):
        raise ProjectError(f"no build manifest at {manifest_path}")
    manifest = _read_json(manifest_path, {})
    build_sha = hashes(build)
    gate = (manifest.get("gate") or {}).get("status")
    if gate != "PASS":
        problems.append(f"the build manifest's gate status is {gate!r}, not PASS")
    if manifest.get("output_sha256") != build_sha:
        problems.append(f"{build.name} does not match its manifest (edited after the build?)")
    if (manifest.get("source") or {}).get("sha256") != source.get("sha256"):
        problems.append("the build was made from a different file than the pass source in pass.json")
    index = project.project_index()
    current = index.get("current") or {}
    if current.get("version") != source.get("version") or current.get("sha256") != source.get("sha256"):
        problems.append(f"PROJECT_INDEX.json now names {_label_of(current)} (sha256 {str(current.get('sha256'))[:12]}), "
                        f"but this pass was built on {_label_of(source)} (sha256 {str(source.get('sha256'))[:12]}). "
                        "Start a new pass from the current file.")
    elif hashes(project.path(current["path"])) != current.get("sha256"):
        problems.append(f"the current file {current['path']} does not match its recorded SHA-256")
    package_copy = _package_item(project, "current")
    if package_copy and _is_file(project.path(package_copy["path"])) \
            and hashes(project.path(package_copy["path"])) != package_copy.get("sha256"):
        problems.append(f"{package_copy['path']} changed after it was registered (saved in Word?); register it with "
                        "`msw.py author-save`, then rebuild on the saved file")
    lock_folders = [project.path(PACKAGE), build.parent, pass_dir]
    if current.get("path"):
        lock_folders.append(project.path(current["path"]).parent)
    locks = sorted({p for folder in lock_folders for p in find_locks(folder, recursive=folder == project.path(PACKAGE))})
    if locks:
        problems.append("Word or LibreOffice lock files present (close the files first): " + ", ".join(project.rel(p) for p in locks))
    note = pass_dir / f"{label}_CHANGES.md"
    if not _is_file(note):
        problems.append(f"the change note {project.rel(note)} is missing")
    elif CHANGES_UNFINISHED in _fsp(note).read_text(encoding="utf-8-sig"):
        warnings.append(f"{note.name} still carries the 'complete this note' line from the template")
    if proof_text:
        proof = _plain(Path(proof_text).resolve())
        if not _is_file(proof) or proof.suffix.lower() != ".pdf":
            problems.append(f"--proof {proof_text} is not an existing PDF")
            proof = None
    else:
        found = sorted(path for path in walk_files(pass_dir / "proofs") if path.suffix.lower() == ".pdf")
        proof = found[0] if len(found) == 1 else None
        if len(found) > 1:
            # p1 rendered an earlier build, p2 this one: take the single proof of this build.
            of_build = [path for path in found if _proof_source(hashes, path)[0] == build_sha]
            if len(of_build) == 1:
                proof = of_build[0]
            else:
                problems.append(f"{len(found)} PDFs under {project.rel(pass_dir / 'proofs')}; name the proof with "
                                "--proof")
        if not found:
            warnings.append("no proof PDF: render and look at the accepted view before presenting this version")
    if proof is not None:
        source_sha, record = _proof_source(hashes, proof)
        if record is None:
            warnings.append(f"no proof.json beside {project.record_path(proof)} describes this PDF, so it cannot be "
                            f"confirmed that it shows the build being released ({build_sha[:12]}); proofs made with "
                            "`msw.py proof` record their source")
        elif source_sha != build_sha:
            source = record.get("source") if isinstance(record.get("source"), dict) else {}
            problems.append(f"{project.record_path(proof)} was rendered from {source.get('path') or source.get('name')} "
                            f"(sha256 {str(source_sha)[:12]}), not from the build being released ({build.name}, sha256 "
                            f"{build_sha[:12]}); render a proof of this build into a new proofs/pN folder "
                            "(msw.py proof) and name it with --proof")
    if problems:
        raise ProjectError(*problems)

    released_name = release_file_name(project.config, info)
    vdir = f"{VERSIONS}/{label}"
    rel_doc, rel_note = f"{vdir}/{released_name}", f"{vdir}/{label}_CHANGES.md"
    note_sha = hashes(note)
    version_copies = [
        {"source": project.rel(build), "dest": rel_doc, "sha256": build_sha, "role": "released_manuscript"},
        {"source": project.rel(note), "dest": rel_note, "sha256": note_sha, "role": "change_note"},
    ]
    extras = [{"path": f"{PACKAGE}/Change_Notes/{label}_CHANGES.md", "sha256": note_sha, "role": "change_note",
               "version": label, "source": rel_note}]
    pending = {build_sha: rel_doc, note_sha: rel_note}
    rel_proof = proof_sha = None
    if proof is not None:
        proof_sha = hashes(proof)
        # name the released proof after the released manuscript, so it carries the version
        stem = Path(released_name).stem
        proof_name = proof.name if proof.stem.startswith(stem) else f"{stem} {proof.stem}{proof.suffix.lower()}"
        rel_proof = f"{vdir}/{proof_name}"
        version_copies.append({"source": project.rel(proof), "dest": rel_proof, "sha256": proof_sha, "role": "proof"})
        extras.append({"path": f"{PACKAGE}/Proofs/{proof_name}", "sha256": proof_sha, "role": "proof",
                       "version": label, "source": rel_proof})
        pending[proof_sha] = rel_proof
    for item in version_copies:
        existing = hashes(project.path(item["dest"]))
        if existing is not None and existing != item["sha256"]:
            problems.append(f"{item['dest']} already exists with other content")
    if figures:
        pairs = _figure_pairs(figures, "--figures")
    else:
        pairs = _figure_pairs(info.get("figures_changed"), f"figures_changed in {project.rel(pass_dir / 'pass.json')}")
    auto_figures, auto_problems = _shown_promoted_figures(project, build, pairs)
    problems += auto_problems
    pairs = pairs + auto_figures
    figure_plan, figure_extras, figure_problems, figure_warnings = _plan_release_figures(
        project, hashes, pairs, build, manifest, label)
    problems += figure_problems
    warnings += figure_warnings
    changed = {_fkey(identifier) for identifier, _ in pairs}
    # Figures this release does not change keep their package copies; changed ones are replaced.
    extras += [dict(item) for item in project.package_files()
               if item.get("role") == "figure" and _fkey(item.get("figure")) not in changed]
    extras += figure_extras
    new_current = {"version": label, "state": "tracked verified", "path": rel_doc, "sha256": build_sha}
    new_baseline = _pointer(current)
    desired = _desired_package(new_current, new_baseline, extras)
    moves, package_copies, package_problems = _plan_package(project, hashes, desired, f"Package_Superseded_{label}",
                                                            today(), pending)
    problems += package_problems
    if problems:
        raise ProjectError(*problems)
    project.refuse_leftovers()
    entry = {"version": label, "date": info.get("date") or today(), "role": "current", "base_role": "released",
             "state": "tracked verified", "slug": slug, "path": rel_doc, "sha256": build_sha,
             "baseline": _label_of(current), "baseline_sha256": current.get("sha256"), "changes": rel_note,
             "changes_sha256": note_sha, "proof": rel_proof, "proof_sha256": proof_sha, "gate": gate,
             "revision_author": manifest.get("author"), "revision_ids": manifest.get("revision_ids"),
             "released_utc": None}
    return {"label": label, "slug": slug, "pass_dir": pass_dir, "pass": project.record_path(pass_dir), "build": build,
            "build_sha256": build_sha, "manifest": project.rel(manifest_path), "gate": manifest.get("gate") or {},
            "source": source, "version_copies": version_copies, "moves": moves, "package_copies": package_copies,
            "entry": {k: v for k, v in entry.items() if v is not None or k == "released_utc"},
            "current": new_current, "baseline": new_baseline, "desired": desired, "warnings": warnings,
            "figures": figure_plan, "auto_figures": auto_figures}


def _finish_release(project: Project, pass_text: str, apply: bool) -> bool:
    """A release whose indexes were written but whose ledger event never completed: finish it."""
    info = _read_json(_resolve_pass(project, pass_text) / "pass.json", {})
    number = parse_version(info.get("version", ""))
    label = vlabel(number)
    entry = next((e for e in project.version_entries() if _number(e) == number and _base_role(e) == "released"), None)
    current = project.project_index().get("current") or {}
    if entry is None or current.get("sha256") != entry.get("sha256"):
        return False
    unfinished = [e for e in _unresolved_pending(project, "release", label)
                  if e.get("build_sha256") == entry.get("sha256")]
    if not unfinished:
        return False
    if not apply:
        print(f"{label} was released but its ledger event {unfinished[-1]['seq']} never completed; "
              "`release apply` with the same arguments refreshes the generated blocks and completes it.")
        return True
    notes = refresh_docs(project)
    if unfinished[-1].get("figures"):
        notes += refresh_figure_docs(project)
    project.ledger_append({"event": "release", "status": "complete", "ref": unfinished[-1]["seq"], "version": label,
                           "output_sha256": entry.get("sha256"), "path": entry.get("path"), "resumed": True})
    for note in notes:
        print(f"NOTE: {note}")
    print(f"Finished the interrupted release of {label}: the indexes were already updated; generated blocks "
          "refreshed and the ledger event completed.")
    return True


def _record_released_figures(project: Project, released: list, label: str, index: dict) -> dict:
    """FIGURE_INDEX with used_in += [label] on each released figure version; PROJECT_INDEX figure_set
    (in `index`) names those versions."""
    figures = project.figure_index_for_update()
    figure_set = dict(index.get("figure_set") or {}) if isinstance(index.get("figure_set"), dict) else {}
    for item in released:
        figure = _find_figure(figures, item["id"])
        version = _figure_version(figure, item["version"]) if figure else None
        if version is None:
            raise ProjectError(f"{item['id']} {item['version']} is no longer in {FIGURE_INDEX}")
        used = version.get("used_in") if isinstance(version.get("used_in"), list) else []
        if label not in used:
            used.append(label)
        version["used_in"] = used
        figure_set[figure["id"]] = version["version"]
    index["figure_set"] = figure_set
    return figures


def _commit_hint(project: Project, label: str, slug: str) -> None:
    print("Commit the release after the author agrees (not run):")
    print(f"  git -C \"{project.root}\" add -A")
    print(f"  git -C \"{project.root}\" commit -m \"Release {label}: {slug}\"")


def _resume_release(project: Project, args) -> bool:
    """A release that stopped after its copies and moves (during or after the index swap): finish it
    from its pending ledger event. False when there is none, or when it stopped earlier (the release
    is then planned again; finished copies are recognised)."""
    pass_dir = _resolve_pass(project, args.pass_dir)
    info = _read_json(pass_dir / "pass.json", {})
    label = vlabel(parse_version(info.get("version", "")))
    apply = args.mode == "apply"
    unfinished = _unresolved_pending(project, "release", label)
    if not unfinished:
        return False
    pending = unfinished[-1]
    if not pending.get("target"):
        return _finish_release(project, args.pass_dir, apply)      # a ledger written before targets were recorded
    if not _file_steps_done(project, pending):
        return False
    try:
        build = _plain(Path(args.build).resolve()) if args.build else _find_build(pass_dir)
    except ProjectError:
        build = None
    if build is not None and Hashes()(build) != pending.get("build_sha256"):
        released = next((item.get("dest") for item in pending.get("copies") or []
                         if item.get("role") == "released_manuscript"), None)
        raise ProjectError(f"the interrupted release of {label} (ledger event {pending['seq']}) already copied the build "
                           f"with sha256 {str(pending.get('build_sha256'))[:12]} to {released}; "
                           f"{project.record_path(build)} is a different build. Finish that release with --build "
                           "naming the build it copied, and make later changes in a new pass.")
    problems = _resume_problems(project, pending, label)
    if problems:
        raise ProjectError(*problems)
    leftovers = project.leftovers()
    print(f"The release of {label} (ledger event {pending['seq']}) never completed: its copies and moves are done; "
          f"{_swap_progress(project, pending, label)}.")
    if not apply:
        print("`release apply` with the same arguments finishes it: "
              + (f"it moves {', '.join(leftovers)} into {ARCHIVE}/<date>/Interrupted_Run/, then " if leftovers
                 else "it ") + "writes the indexes and generated blocks from that ledger event.")
        return True
    notes = _roll_forward(project, pending, label)
    target = pending["target"]
    project.ledger_append({"event": "release", "status": "complete", "ref": pending["seq"], "version": label,
                           "output_sha256": pending.get("build_sha256"),
                           "baseline_sha256": (target.get("baseline") or {}).get("sha256"),
                           "path": target["current"]["path"], "resumed": True,
                           **({"figures": target["figures"]} if target.get("figures") else {})})
    for note in notes:
        print(f"NOTE: {note}")
    print(f"Finished the interrupted release of {label}: the indexes and generated blocks were written from ledger "
          f"event {pending['seq']}.")
    print(f"Released {label}: {target['current']['path']}")
    _commit_hint(project, label, pending.get("slug") or "")
    return True


def cmd_release(args) -> int:
    project = _project(args)
    project.require_schema()
    if _resume_release(project, args):
        return 0
    plan = plan_release(project, args.pass_dir, args.build, args.manifest, args.proof, figures=args.figures)
    label = plan["label"]
    print(f"Release {label} \"{plan['slug']}\" ({args.mode})")
    print(f"  build: {project.rel(plan['build'])}  [sha256 {plan['build_sha256'][:12]}]")
    checks = plan["gate"].get("checks") or []
    print(f"  gate: {plan['gate'].get('status')} ({len(checks)} checks; manifest {plan['manifest']})")
    print(f"  built on: {_label_of(plan['source'])}, still current in PROJECT_INDEX.json")
    print("  no Word or LibreOffice lock files; no destination exists")
    for identifier, version in plan["auto_figures"]:
        print(f"  figure {identifier} {version}: the build shows this promoted version and the package does not hold "
              f"it yet; included as if named with --figures {identifier}={version}")
    for item in plan["figures"]:
        shown = (f"; the build shows its embed PNG as picture \"{item['picture']}\"" if item.get("picture")
                 else "" if item.get("embed_sha256") else "; no embed PNG recorded, picture not checked")
        print(f"  figure {item['id']} {item['version']}: promoted, files match their registered SHA-256{shown}")
    _print_operations(plan["moves"], plan["version_copies"] + plan["package_copies"])
    figures_note = (", FIGURE_INDEX.json (used_in += " + label + " for "
                    + ", ".join(f"{item['id']} {item['version']}" for item in plan["figures"]) + ")"
                    if plan["figures"] else "")
    print("Index updates: VERSION_INDEX.json (+ .md), PROJECT_INDEX.json (current "
          f"{label}, baseline {_label_of(plan['baseline'])}){figures_note}, README.md and STATUS.md generated "
          "blocks, ledger")
    for warning in plan["warnings"]:
        print(f"WARNING: {warning}")
    if args.mode == "plan":
        print("Plan only: nothing was changed. Run `release apply` with the same arguments to release.")
        return 0
    hashes = Hashes()
    reason = f"Package_Superseded_{label}"
    released_figures = {item["id"]: item["version"] for item in plan["figures"]}
    # The indexes this release writes, recorded before anything changes: a run that stops part-way
    # through the index swap is finished from it (see _resume_release).
    target = {"entry": plan["entry"], "current": plan["current"], "baseline": plan["baseline"],
              "desired": plan["desired"], "expected_current_sha256": plan["source"].get("sha256"),
              "last_release": {"version": label, "pass": plan["pass"]},
              **({"figures": released_figures} if released_figures else {})}
    pending = project.ledger_append({
        "event": "release", "status": "pending", "version": label, "slug": plan["slug"], "pass": plan["pass"],
        "build_sha256": plan["build_sha256"], "source_sha256": plan["source"].get("sha256"),
        "copies": plan["version_copies"] + plan["package_copies"], "moves": plan["moves"],
        **({"figures": released_figures} if released_figures else {}), "target": target})
    try:
        _execute_copies(project, plan["version_copies"], hashes)
        done = _execute_moves(project, plan["moves"])
        if done:
            project.ledger_append({"event": "move", "status": "complete", "ref": pending["seq"], "reason": reason,
                                   "ops": done})
        _execute_copies(project, plan["package_copies"], hashes)
        entries, index, package, figures = _target_state(project, target, label)
        notes = project.commit_indexes(entries, index, package, figures)
    except (OSError, ProjectError) as error:
        project.ledger_append({"event": "release", "status": "failed", "ref": pending["seq"], "version": label,
                               "error": _ledger_error(project, error)})
        raise _failure(pending, error) from error
    project.ledger_append({"event": "release", "status": "complete", "ref": pending["seq"], "version": label,
                           "output_sha256": plan["build_sha256"], "baseline_sha256": plan["baseline"].get("sha256"),
                           "path": plan["current"]["path"], "gate": plan["gate"].get("status"),
                           "changes": plan["entry"].get("changes"), "proof": plan["entry"].get("proof"),
                           **({"figures": released_figures} if released_figures else {})})
    for note in notes:
        print(f"NOTE: {note}")
    print(f"Released {label}: {plan['current']['path']}")
    _commit_hint(project, label, plan["slug"])
    return 0


# -- archive ------------------------------------------------------------------------

def _protected_paths(project: Project) -> dict[str, str]:
    """Every path-like string in the three indexes: normalised key -> the path as written."""
    refs = {}

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and ("/" in value or "\\" in value or "." in value) and len(value) < 1024:
            refs.setdefault(_norm(value), value)

    walk(project.project_index())
    walk(project.package_files())
    walk(project.version_entries())
    walk(project.figure_index())
    return refs


def _skeleton(project: Project) -> set[str]:
    names = {_norm(rel) for rel in CORE_FILES} | {_norm(CLAIMS)}
    for rel in TREE:
        parts = rel.split("/")
        for depth in range(1, len(parts) + 1):
            names.add(_norm("/".join(parts[:depth])))
    return names


def plan_archive(project: Project, reason: str, paths: list, day: str | None = None) -> list[dict]:
    if not REASON_RE.match(reason or ""):
        raise ProjectError("--reason must be a short name of letters, digits, '_', '-' or '.' (e.g. Superseded_Drafts)")
    day = day or today()
    protected = _protected_paths(project)
    skeleton = _skeleton(project)
    problems, ops, chosen = [], [], []
    for raw in paths:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate if _exists(Path.cwd() / candidate) else project.path(raw)
        candidate = _plain(candidate.resolve())
        if not _exists(candidate):
            problems.append(f"{raw} does not exist")
            continue
        if not project.inside(candidate) or candidate == project.root:
            problems.append(f"{raw} is not inside the project")
            continue
        rel = project.rel(candidate)
        key = _norm(rel)
        if key == ".git" or key.startswith(".git/"):
            problems.append(f"{rel}: the git folder is never archived")
            continue
        if key.startswith(_norm(ARCHIVE) + "/"):
            problems.append(f"{rel} is already in the archive")
            continue
        if key in skeleton:
            problems.append(f"{rel} is part of the project skeleton")
            continue
        if key in protected:
            problems.append(f"{rel} is named by an index (PROJECT_INDEX, VERSION_INDEX or FIGURE_INDEX)")
            continue
        inside = sorted(ref for ref in protected if ref.startswith(key + "/"))
        if inside:
            problems.append(f"{rel} contains {protected[inside[0]]}, which an index names")
            continue
        if any(key.startswith(other + "/") or other.startswith(key + "/") or key == other for other in chosen):
            problems.append(f"{rel} overlaps another path in this request")
            continue
        chosen.append(key)
        dest = f"{ARCHIVE}/{day}/{reason}/{rel}"
        if _exists(project.path(dest)):
            problems.append(f"archive destination {dest} already exists")
            continue
        unreadable = []
        if _is_dir(candidate):
            files, profiles, locks = _archive_dir_files(candidate, rel, dest, unreadable)
            op = {"old": rel, "new": dest, "kind": "dir", "files": files}
            if profiles:
                op["profiles"] = profiles
        else:
            locks = _owner_locks(candidate)
            try:
                op = {"old": rel, "new": dest, "kind": "file", "sha256": sha256_file(fs(candidate))}
            except OSError as error:
                unreadable.append(f"{rel}: {_why(error)}")
        if unreadable:
            problems += [f"cannot read {item}; nothing is moved until every file can be hashed (close the program "
                         "that holds it, or shorten the path)" for item in unreadable]
            continue
        if locks:
            problems.append(f"{rel}: Word or LibreOffice lock files present ({', '.join(p.name for p in locks)})")
            continue
        ops.append(op)
    if problems:
        raise ProjectError(*problems)
    if not ops:
        raise ProjectError("nothing to archive")
    return ops


def _archive_dir_files(folder: Path, rel: str, dest: str, unreadable: list) -> tuple[list, list, list]:
    """A folder to archive: its files with SHA-256 values, the LibreOffice profile folders inside it
    (moved whole with the folder; their cache files are counted, not hashed) and its Word lock files.
    Files or folders that cannot be read are added to `unreadable`."""
    files, profiles, locks = [], [], []
    if _in_profile(folder):
        return files, [{"folder": rel, "files": _count_files(folder)}], locks

    def failed(error):
        unreadable.append(f"{_unreadable(error)['path']}: {_why(error)}")

    for base, dirs, names in os.walk(fs(folder), onerror=failed):
        here = Path(base)
        keep = []
        for name in sorted(dirs):
            if _is_profile_dir(here / name):
                inner = _plain(here / name).relative_to(folder).as_posix()
                profiles.append({"folder": f"{rel}/{inner}", "files": _count_files(here / name)})
            else:
                keep.append(name)
        dirs[:] = keep
        for name in sorted(names):
            path = _plain(here / name)
            inner = path.relative_to(folder).as_posix()
            if is_lock(name):
                locks.append(path)
            try:
                digest = sha256_file(fs(path))
            except OSError as error:
                unreadable.append(f"{rel}/{inner}: {_why(error)}")
                continue
            files.append({"old": f"{rel}/{inner}", "new": f"{dest}/{inner}", "sha256": digest})
    return files, profiles, locks


def _count_files(folder) -> int:
    """Files below a folder, long paths included; folders that cannot be listed count as empty."""
    return sum(len(names) for _, _, names in os.walk(fs(folder)))


def _execute_archive(project: Project, ops: list) -> list[dict]:
    done = []
    for op in ops:
        source, dest = project.path(op["old"]), project.path(op["new"])
        if op["kind"] == "file":
            before = sha256_file(source)
            if before != op["sha256"]:
                raise ProjectError(f"{op['old']} changed after the plan was made; not moved")
            move_file(source, dest)
            done.append(dict(op, sha256_before=before, sha256_after=sha256_file(dest)))
        else:
            move_file(source, dest)
            files = []
            for item in op["files"]:
                after = sha256_file(project.path(item["new"]))
                files.append(dict(item, sha256_before=item["sha256"], sha256_after=after))
            done.append(dict(op, files=files))
    return done


def cmd_archive(args) -> int:
    project = _project(args)
    ops = plan_archive(project, args.reason, args.paths)
    print(f"Archive ({args.mode}) to {ARCHIVE}/{today()}/{args.reason}/ by rename:")
    count = unhashed = 0
    for op in ops:
        if op["kind"] == "file":
            count += 1
            print(f"  {op['old']}\n    -> {op['new']}  [sha256 {op['sha256'][:12]}]")
        else:
            count += len(op["files"])
            print(f"  {op['old']}/  ({len(op['files'])} files)\n    -> {op['new']}/")
            for profile in op.get("profiles") or []:
                unhashed += profile["files"]
                print(f"    LibreOffice profile {profile['folder']}/ ({profile['files']} cache files) moves with the "
                      "folder; its files are not hashed")
    extra = f" plus {unhashed} LibreOffice profile cache file(s) with their folder" if unhashed else ""
    if args.mode == "plan":
        print(f"Plan only: {count} file(s){extra}; nothing was changed. Run `archive apply` with the same arguments.")
        return 0
    pending = project.ledger_append({"event": "move", "status": "pending", "reason": args.reason, "ops": ops})
    try:
        done = _execute_archive(project, ops)
    except (OSError, ProjectError) as error:
        project.ledger_append({"event": "move", "status": "failed", "ref": pending["seq"], "reason": args.reason,
                               "error": _ledger_error(project, error)})
        raise _failure(pending, error) from error
    mismatched = [item["old"] for op in done for item in (op.get("files") or [op])
                  if item.get("sha256_before") != item.get("sha256_after")]
    project.ledger_append({"event": "move", "status": "complete", "ref": pending["seq"], "reason": args.reason,
                           "ops": done})
    if mismatched:
        print("ERROR: hashes differ after the move: " + ", ".join(mismatched), file=sys.stderr)
        return 1
    print(f"Moved {count} file(s){extra}; SHA-256 before and after recorded in {LEDGER}.")
    return 0


# -- status and verify-project --------------------------------------------------------

def _check(project: Project, hashes: Hashes, rel, sha) -> str:
    if not rel:
        return "missing"
    path = project.path(rel)
    if not _exists(path):
        return "missing"
    try:
        return "ok" if hashes(path) == sha else "changed"
    except OSError:
        return "unreadable"


def _open_ledger_issues(events: list) -> list[dict]:
    resolved = {(e.get("event"), e.get("ref")) for e in events
                if e.get("status") in ("complete", "failed") and e.get("ref") is not None}
    issues = [dict(e, issue="interrupted") for e in events
              if e.get("status") == "pending" and (e.get("event"), e.get("seq")) not in resolved]
    same = ("event", "version", "reason", "figure", "figure_version")
    for position, event in enumerate(events):
        if event.get("status") != "failed":
            continue
        later = any(e.get("status") == "complete" and all(e.get(key) == event.get(key) for key in same)
                    for e in events[position + 1:])
        if not later:
            issues.append(dict(event, issue="failed"))
    return issues


def _git_state(root: Path) -> dict:
    git = shutil.which("git")
    if not git:
        return {"repository": None, "note": "git is not on PATH"}
    try:
        inside = subprocess.run([git, "-C", str(root), "rev-parse", "--is-inside-work-tree"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=60)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return {"repository": False}
        result = subprocess.run([git, "--no-optional-locks", "-C", str(root), "status", "--porcelain", "--", "."],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        return {"repository": None, "note": str(error)}
    if result.returncode != 0:
        return {"repository": True, "note": result.stderr.strip()[:300]}
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return {"repository": True, "uncommitted": len(lines), "entries": lines[:20]}


SCIENCE_TEXT = {"comments": "questions about the science are Word comments for the author",
                "tracked_for_review": "science edits are tracked changes for the author's review"}


def _policy(config: dict) -> dict:
    """policy.science_changes from manuscript.json: comments (the default) or tracked_for_review.
    Numbers still change only with the author's approval or a recomputation from the data."""
    policy = config.get("policy") if isinstance(config.get("policy"), dict) else {}
    value = policy.get("science_changes")
    if value is None:
        return {"science_changes": "comments", "set": False}
    if value not in SCIENCE_CHANGES:
        return {"science_changes": value, "set": True,
                "problem": f"{CONFIG_NAME} policy.science_changes is {value!r}; allowed: "
                           f"{', '.join(SCIENCE_CHANGES)}. Until it is corrected, treat it as comments."}
    return {"science_changes": value, "set": True}


def collect_status(project: Project, git: bool = True) -> dict:
    hashes = Hashes()
    index = project.project_index()
    entries = project.version_entries()
    report = {"root": str(project.root), "title": project.config.get("title", ""),
              "venue": project.config.get("venue", ""), "target_journal": index.get("target_journal"),
              "policy": _policy(project.config), "current": None, "baseline": None, "package": [],
              "unregistered_word_files": [], "locks": [], "passes": [], "ledger_issues": [], "git": None,
              "outstanding": _open_items(index), "unreadable": [], "path_warning": root_warning(project.root),
              "suggestions": [], "inconsistent": _index_disagreements(project)}
    if report["policy"].get("problem"):
        report["suggestions"].append(report["policy"]["problem"])
    if report["inconsistent"]:
        report["suggestions"].append("The indexes disagree about the current file (listed above): an index swap "
                                     "stopped part-way. Run the interrupted command again (see the unfinished ledger "
                                     "operations); do not start a pass until it has finished.")
    current = index.get("current")
    for key in ("current", "baseline"):
        pointer = index.get(key)
        if pointer:
            report[key] = dict(pointer, check=_check(project, hashes, pointer.get("path"), pointer.get("sha256")))
            if report[key]["check"] != "ok":
                report["suggestions"].append(f"The {key} file {pointer.get('path')} is {report[key]['check']}; "
                                             "files in Versions/ must never change. Run `msw.py verify-project`.")
    listed = set()
    for item in project.package_files():
        listed.add(_norm(item.get("path", "")))
        check = _check(project, hashes, item.get("path"), item.get("sha256"))
        report["package"].append(dict(item, check=check))
        if check == "changed" and str(item.get("path", "")).lower().endswith(".docx"):
            if item.get("role") == "current":
                report["suggestions"].append(f"{item['path']} was saved after it was registered (Word?). Register the "
                                             f"author's save: msw.py author-save \"{item['path']}\"")
            else:
                report["suggestions"].append(f"{item['path']} was saved after it was registered (Word?). "
                                             + _not_current_copy(item))
    known = {e.get("sha256") for e in entries}
    current_time = None
    if current and _is_file(project.path(current.get("path", ""))):
        current_time = os.stat(fs(project.path(current["path"]))).st_mtime
    for folder in (PACKAGE, INCOMING):
        base = project.path(folder)
        if not _is_dir(base):
            continue
        for path in sorted(_plain(entry) for entry in _fsp(base).glob("*.docx")):
            if is_lock(path.name) or _norm(project.rel(path)) in listed:
                continue
            try:
                sha = hashes(path)
                modified = os.stat(fs(path)).st_mtime
            except OSError as error:
                report["unreadable"].append({"path": project.rel(path), "error": _why(error)})
                continue
            if sha in known:
                continue
            newer = current_time is not None and modified > current_time
            report["unregistered_word_files"].append({
                "path": project.rel(path), "modified": _dt.datetime.fromtimestamp(modified).isoformat(timespec="seconds"),
                "newer_than_current": newer})
            verb = "author-save" if folder == PACKAGE else "intake"
            report["suggestions"].append(f"{project.rel(path)} is not registered"
                                         + (" and is newer than the current file" if newer else "")
                                         + f"; if it is the author's latest, run msw.py {verb} \"{project.rel(path)}\"")
    for folder, recursive in ((PACKAGE, True), (VERSIONS, True), (INCOMING, False), (PASSES, True)):
        report["locks"] += [project.rel(p) for p in find_locks(project.path(folder), recursive=recursive,
                                                               errors=report["unreadable"])]
    if report["locks"]:
        report["suggestions"].append("Word or LibreOffice lock files mean the author has files open; do not move or build from "
                                     "them until they are closed.")
    released = _released_numbers(entries)
    passes = project.path(PASSES)
    if _is_dir(passes):
        for folder in sorted(_plain(entry) for entry in _fsp(passes).iterdir()):
            match = PASS_DIR_RE.match(folder.name)
            if not match or not _is_dir(folder) or int(match.group(1)) in released:
                continue
            try:
                info = _read_json(folder / "pass.json", {})
            except ProjectError as error:
                report["unreadable"] += [{"path": project.rel(folder / "pass.json"), "error": problem}
                                         for problem in error.problems]
                info = {}
            source = info.get("source") or {}
            builds = [project.rel(_plain(p)) for p in sorted(_fsp(folder).glob("build*/*.docx")) if not is_lock(p.name)]
            report["passes"].append({"version": vlabel(int(match.group(1))), "slug": info.get("slug", match.group(2)),
                                     "folder": project.rel(folder), "source": _label_of(source) if source else None,
                                     "source_is_current": bool(current) and source.get("sha256") == current.get("sha256"),
                                     "builds": builds})
    events, errors = project.ledger_events()
    report["ledger_issues"] = [{"seq": e.get("seq"), "event": e.get("event"), "issue": e["issue"],
                                "version": e.get("version") or " ".join(
                                    str(x) for x in (e.get("figure"), e.get("figure_version")) if x) or None,
                                "error": e.get("error")}
                               for e in _open_ledger_issues(events)]
    report["ledger_errors"] = errors
    if report["ledger_issues"]:
        report["suggestions"].append("An operation in the ledger did not finish; re-run it (finished steps are "
                                     "skipped) or run `msw.py verify-project`.")
    if git:
        report["git"] = _git_state(project.root)
    report["leftovers"] = project.leftovers()
    if report["leftovers"]:
        unfinished = [issue for issue in report["ledger_issues"] if issue.get("event") in RESUMABLE_EVENTS]
        report["suggestions"].append(
            "An interrupted run left " + ", ".join(report["leftovers"]) + "; "
            + ("run the unfinished " + ", ".join(f"{i['event'].replace('_', ' ')} {i.get('version') or ''}".strip()
                                                 for i in unfinished)
               + " again with the same arguments: it moves them into the archive and finishes. Other commands "
               if unfinished else "commands ")
            + "that update the indexes refuse until each is moved away with `msw.py archive apply --reason "
              "Interrupted_Run <file>`.")
    if report["unreadable"]:
        report["suggestions"].append("Some files or folders could not be read (listed above); a path over Windows' "
                                     "260-character limit needs a shorter project folder or long paths enabled.")
    report["mutation_lock"] = lock_state(project.root)
    if report["mutation_lock"]:
        report["suggestions"].append(lock_advice(report["mutation_lock"], refused=False))
    if not current:
        report["suggestions"].append("Register the starting draft: msw.py intake <draft.docx> --as-version V01")
    return report


CHECK_TEXT = {"ok": "matches its recorded SHA-256", "changed": "CHANGED since it was recorded",
              "missing": "MISSING", "unreadable": "cannot be read (open in Word?)"}


def print_status(report: dict) -> None:
    print(f"Project: {report['title']} ({report['venue']})  {report['root']}")
    if report.get("target_journal") and report["target_journal"] != report.get("venue"):
        print(f"Target journal: {report['target_journal']}")
    policy = report.get("policy") or {}
    value = policy.get("science_changes", "comments")
    print(f"Science changes: {value} ({SCIENCE_TEXT.get(value, 'not a known setting')}"
          + ("; the default, not set in manuscript.json)" if not policy.get("set") else ")"))
    for problem in report.get("inconsistent") or []:
        print(f"INDEXES DISAGREE: {problem}")
    current = report["current"]
    if current:
        print(f"Current: {current['version']} {current.get('state') or ''}".rstrip())
        print(f"  {current['path']}\n  {CHECK_TEXT[current['check']]} (sha256 {str(current['sha256'])[:12]})")
    else:
        print("Current: none registered")
    baseline = report["baseline"]
    if baseline:
        print(f"Baseline: {_label_of(baseline)}  {baseline['path']}  [{baseline['check']}]")
    print("Package:" if report["package"] else "Package: empty")
    for item in report["package"]:
        print(f"  [{item['check']}] {item.get('role')}: {item.get('path')}")
    if report["unregistered_word_files"]:
        print("Unregistered Word files:")
        for item in report["unregistered_word_files"]:
            print(f"  {item['path']}  (modified {item['modified']}"
                  + (", newer than the current file)" if item["newer_than_current"] else ")"))
    print("Word lock files: " + (", ".join(report["locks"]) if report["locks"] else "none"))
    held = report.get("mutation_lock")
    if held:
        print(f"Mutation lock: held by {_lock_holder(held)} (another command is changing the project)")
    if report["passes"]:
        print("Passes in flight:")
        for item in report["passes"]:
            builds = f"{len(item['builds'])} build(s)" if item["builds"] else "no build yet"
            print(f"  {item['version']} {item['slug']}: {item['folder']} (source {item['source']}, "
                  f"{'still current' if item['source_is_current'] else 'NOT current any more'}, {builds})")
    else:
        print("Passes in flight: none")
    outstanding = report.get("outstanding") or []
    if outstanding:
        print("Open items:")
        for item in outstanding:
            print(f"  {_item_line(item)[2:]}")
    else:
        print(NO_OPEN_ITEMS)
    for item in report.get("unreadable") or []:
        print(f"UNREADABLE: {item['path']}: {item['error']}")
    if report["ledger_issues"]:
        print("Unfinished ledger operations:")
        for item in report["ledger_issues"]:
            print(f"  seq {item['seq']} {item['event']} {item.get('version') or ''}: {item['issue']}"
                  + (f" ({item['error']})" if item.get("error") else ""))
    git = report.get("git")
    if git:
        if git.get("repository") is True and "uncommitted" in git:
            print(f"Git: {git['uncommitted']} uncommitted change(s)" if git["uncommitted"] else "Git: clean")
            for line in git.get("entries", [])[:10]:
                print(f"  {line}")
        elif git.get("repository") is False:
            print("Git: not a repository")
        else:
            print(f"Git: {git.get('note', 'unknown')}")
    for line in report["suggestions"]:
        print(f"Next: {line}")


def cmd_status(args) -> int:
    project = _project(args)
    report = collect_status(project)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_status(report)
        if report.get("path_warning"):
            print(f"WARNING: {report['path_warning']}")
    return 0


FIGURE_PATH_KEYS = ("path", "pdf", "png", "svg", "tif", "tiff", "file", "export")


def _figure_files(value, out: list) -> None:
    if isinstance(value, dict):
        sha = value.get("sha256")
        if isinstance(sha, str):
            for key in FIGURE_PATH_KEYS:
                if isinstance(value.get(key), str) and value[key]:
                    out.append((value[key], sha))
                    break
        for item in value.values():
            _figure_files(item, out)
    elif isinstance(value, list):
        for item in value:
            _figure_files(item, out)


def _check_embedded_images(project: Project, index: dict, report: dict) -> None:
    """FIGURE_INDEX versions used in the current version must match the pictures inside its .docx."""
    current = index.get("current")
    if not current:
        return
    wanted = []
    for figure in project.figure_index().get("figures") or []:
        for version in (figure.get("versions") or []) if isinstance(figure, dict) else []:
            image = version.get("embedded_image") if isinstance(version, dict) else None
            if (isinstance(image, dict) and image.get("target") and image.get("sha256")
                    and current.get("version") in (version.get("used_in") or [])):
                wanted.append((figure.get("id"), version.get("version"), image["target"], image["sha256"]))
    if not wanted:
        return
    try:
        parts = Package(fs(project.path(current["path"]))).parts
    except (OSError, PackageError, ValueError, zipfile.BadZipFile) as error:
        report["mismatched"].append({"path": current["path"], "named_by": "FIGURE_INDEX embedded images",
                                     "expected": "a readable .docx", "found": f"unreadable: {_why(error)}"})
        return
    for figure_id, version, target, sha in wanted:
        part = target.replace("\\", "/").lstrip("/")
        part = part if part.startswith("word/") else f"word/{part}"
        report["checked"] += 1
        where = f"{current['path']} :: {part}"
        if part not in parts:
            report["missing"].append({"path": where, "named_by": f"FIGURE_INDEX {figure_id} {version} embedded image"})
        elif sha256_bytes(parts[part]) != sha:
            report["mismatched"].append({"path": where, "named_by": f"FIGURE_INDEX {figure_id} {version} embedded image",
                                         "expected": sha, "found": sha256_bytes(parts[part])})


def _swap_next_step(figure: dict, version: dict) -> str:
    embed = (version.get("embed") or {}).get("path") if isinstance(version.get("embed"), dict) else None
    # build reads a relative image path from the folder of edits.json: 90_Agent_Work/passes/VNN_<slug>/
    image = f' with "image": "../../../{embed}"' if embed else ""
    return (f"build a pass whose edits.json has a swap_picture edit{image}, then release it with "
            f"--figures {figure.get('id')}={version.get('version')}")


def _check_figure_pictures(project: Project, index: dict, report: dict) -> None:
    """Is each figure's promoted version (with an embed PNG) a picture in the current manuscript's
    accepted view (same bytes), as `figure status` reports it? A promoted version that is not shown
    yet is a warning (NOT SHOWN) with the next step; nothing changed on disk."""
    current = index.get("current")
    figures = _figure_items(project.figure_index())
    wanted = [(figure, _figure_version(figure, figure.get("current"))) for figure in figures]
    wanted = [(figure, version) for figure, version in wanted if version is not None and _embed_sha(version)]
    if not current or not wanted:
        return
    pictures, error = _read_pictures(project.path(current["path"]))
    for figure, version in wanted:
        state = _picture_state(figure, version, pictures, error)
        report["checked"] += 1
        named_by = f"FIGURE_INDEX {figure.get('id')} {version.get('version')} embedded picture"
        where = f"{current['path']} :: accepted view"
        if state["state"] == "not_shown":
            found = (f"the picture of {state['shown_version']}" if state.get("shown_version")
                     else "no picture with the embed PNG's bytes")
            report["not_shown"].append({"path": where, "named_by": named_by, "figure": figure.get("id"),
                                        "version": version.get("version"), "expected": state["expected"],
                                        "found": found, "next": _swap_next_step(figure, version)})
        elif state["state"] == "unreadable":
            report["mismatched"].append({"path": where, "named_by": named_by, "expected": state["expected"],
                                         "found": f"unreadable: {state.get('error')}"})


def collect_verification(project: Project) -> dict:
    hashes = Hashes()
    index = project.project_index()
    entries = project.version_entries()
    named = []   # (source, rel, sha)
    for key in ("current", "baseline"):
        pointer = index.get(key)
        if pointer:
            named.append((f"PROJECT_INDEX {key}", pointer.get("path"), pointer.get("sha256")))
    for item in project.package_files():
        named.append((f"package {item.get('role')}", item.get("path"), item.get("sha256")))
    for entry in entries:
        named.append((f"VERSION_INDEX {entry.get('version')} {entry.get('role')}", entry.get("path"), entry.get("sha256")))
        for key in ("changes", "proof"):
            if entry.get(key):
                named.append((f"VERSION_INDEX {entry.get('version')} {key}", entry[key], entry.get(f"{key}_sha256")))
    figures = []
    _figure_files(project.figure_index(), figures)
    named += [("FIGURE_INDEX", rel, sha) for rel, sha in figures]
    events, ledger_errors = project.ledger_events()
    folders = []   # (source, rel): folders moved whole (an empty folder, a LibreOffice profile)
    for event in events:
        if event.get("event") == "move" and event.get("status") == "complete":
            for op in event.get("ops") or []:
                source = f"ledger seq {event.get('seq')} move"
                if op.get("kind") == "dir":
                    items = op.get("files") or []
                    if not items or op.get("profiles"):
                        folders.append((source, op.get("new")))
                else:
                    items = [op]
                for item in items:
                    named.append((source, item.get("new"), item.get("sha256_after") or item.get("sha256")))
    report = {"root": str(project.root), "checked": 0, "missing": [], "mismatched": [], "not_shown": [],
              "unlisted_package_files": [], "locks": [], "unreadable": [], "ledger_errors": ledger_errors,
              "ledger_issues": []}
    seen = set()
    for source, rel, sha in named:
        if not rel or (_norm(rel), sha) in seen:
            continue
        seen.add((_norm(rel), sha))
        report["checked"] += 1
        path = project.path(rel)
        if not _is_file(path):
            report["missing"].append({"path": rel, "named_by": source})
            continue
        try:
            found = hashes(path)
        except OSError as error:
            report["mismatched"].append({"path": rel, "named_by": source, "expected": sha,
                                         "found": f"unreadable: {_why(error)}"})
            continue
        if sha and found != sha:
            report["mismatched"].append({"path": rel, "named_by": source, "expected": sha, "found": found})
    for source, rel in folders:
        if rel and (_norm(rel), None) not in seen:
            seen.add((_norm(rel), None))
            report["checked"] += 1
            if not _is_dir(project.path(rel)):
                report["missing"].append({"path": rel + "/", "named_by": source})
    _check_embedded_images(project, index, report)
    _check_figure_pictures(project, index, report)
    listed = {_norm(item.get("path", "")) for item in project.package_files()} | {_norm(PACKAGE_MANIFEST)}
    for path in walk_files(project.path(PACKAGE), report["unreadable"]):
        if not is_lock(path.name) and _norm(project.rel(path)) not in listed:
            report["unlisted_package_files"].append(project.rel(path))
    for folder in ("01_Manuscripts", PASSES):
        report["locks"] += [project.rel(p) for p in find_locks(project.path(folder), recursive=True,
                                                               errors=report["unreadable"])]
    report["ledger_issues"] = [{"seq": e.get("seq"), "event": e.get("event"), "issue": e["issue"],
                                "version": e.get("version"), "error": e.get("error")}
                               for e in _open_ledger_issues(events)]
    # An index swap that stopped part-way, or an operation on the indexes that failed and was never
    # finished, is a failure: the next pass could build on the wrong file.
    report["inconsistent"] = _index_disagreements(project)
    report["unfinished"] = [issue for issue in report["ledger_issues"]
                            if issue["issue"] == "failed" and issue["event"] in INDEX_EVENTS]
    failed = (report["missing"] or report["mismatched"] or report["unreadable"] or ledger_errors
              or report["inconsistent"] or report["unfinished"])
    report["status"] = "FAIL" if failed else "PASS"
    return report


def cmd_verify_project(args) -> int:
    project = _project(args)
    report = collect_verification(project)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["status"] == "PASS" else 1
    print(f"verify-project {report['status']}: {report['checked']} named file(s) re-hashed in {report['root']}")
    for item in report["missing"]:
        print(f"  MISSING   {item['path']}  (named by {item['named_by']})")
    def short(value):
        value = str(value)
        return value[:12] if re.fullmatch(r"[0-9a-f]{64}", value) else value

    for item in report["mismatched"]:
        label = "UNREADABLE" if str(item["found"]).startswith("unreadable") else "CHANGED   "
        print(f"  {label} {item['path']}  (named by {item['named_by']}; expected {short(item['expected'])}, "
              f"found {short(item['found'])})")
    for item in report["unreadable"]:
        print(f"  UNREADABLE {item['path']}  ({item['error']})")
    for item in report["not_shown"]:
        print(f"  NOT SHOWN {item['path']}  ({item['figure']} {item['version']} is promoted, but the current "
              f"manuscript shows {item['found']}; nothing changed on disk)\n"
              f"            Next: {item['next']}")
    for rel in report["unlisted_package_files"]:
        print(f"  UNLISTED  {rel}  (in the package but not in PACKAGE_MANIFEST.json)")
    for rel in report["locks"]:
        print(f"  LOCK      {rel}")
    for problem in report["inconsistent"]:
        print(f"  INDEXES   {problem}")
    for item in report["ledger_issues"]:
        hint = (" (an index operation that never finished: run the same command again to finish it)"
                if item["issue"] == "failed" and item["event"] in INDEX_EVENTS else "")
        print(f"  LEDGER    seq {item['seq']} {item['event']} {item.get('version') or ''}".rstrip()
              + f": {item['issue']}{hint}")
    for error in report["ledger_errors"]:
        print(f"  LEDGER    {error}")
    return 0 if report["status"] == "PASS" else 1


# -- figures ---------------------------------------------------------------------------
#
# FIGURE_INDEX.json (schema_version 2) is flat: {"schema_version": 2, "figures": [{"id", "title",
# "folder", "current", "versions": [...]}]}. A version records where the build wrote its files and
# their hashes; nothing is copied or moved when it is registered. Promotion names the current
# version (and PROJECT_INDEX figure_set); a release with --figures copies the promoted PDF and PNG
# into the package and adds the manuscript version to the figure version's used_in.

def _fkey(identifier) -> str:
    return str(identifier or "").strip().casefold()


def figure_id(text) -> str:
    identifier = str(text or "").strip()
    if not FIGURE_ID_RE.match(identifier):
        raise ProjectError(f"not a figure id: {text!r} (letters, digits, '_', '-' or '.', starting with a letter; "
                           "for example Figure_01 or Figure_S2)")
    return identifier


def figure_version_label(text) -> str:
    """A figure version as written, with a lower-case v: v1, v02, v14b."""
    match = FIGURE_VERSION_RE.match(str(text or "").strip())
    if not match or int(match.group(1)) < 1:
        raise ProjectError(f"not a figure version: {text!r} (use v1, v02 or v14b)")
    return f"v{match.group(1)}{match.group(2).lower()}"


def _fv_key(label) -> str:
    """Comparison key: v2, V02 and v002 are the same version."""
    match = FIGURE_VERSION_RE.match(str(label or "").strip())
    if match:
        return f"v{int(match.group(1))}{match.group(2).lower()}"
    return str(label or "").strip().casefold()


def _find_figure(figures, identifier) -> dict | None:
    return next((f for f in _figure_items(figures) if _fkey(f.get("id")) == _fkey(identifier)), None)


def _figure_version(figure, label) -> dict | None:
    if not figure or not label:
        return None
    return next((v for v in figure.get("versions") or [] if isinstance(v, dict)
                 and _fv_key(v.get("version")) == _fv_key(label)), None)


def _require_figure(figures, identifier) -> dict:
    figure = _find_figure(figures, identifier)
    if figure is None:
        known = ", ".join(str(f.get("id")) for f in _figure_items(figures)) or "none"
        raise ProjectError(f"{identifier} is not in {FIGURE_INDEX} (registered: {known}); add it with "
                           f"`msw.py figure add --id {identifier} --title \"Short title\"`")
    return figure


def _file_kind(rel: str) -> str:
    extension = Path(str(rel)).suffix.lower().lstrip(".")
    return FIGURE_KINDS.get(extension, extension) or "file"


def _version_files(version) -> list[tuple[str, str, str]]:
    """(kind, path, sha256) of every file a figure version records: files[], pdf/sha256, script, embed."""
    out, seen = [], set()

    def add(kind, rel, sha):
        if isinstance(rel, str) and rel and isinstance(sha, str) and sha and (_norm(rel), sha) not in seen:
            seen.add((_norm(rel), sha))
            out.append((kind, rel, sha))

    if not isinstance(version, dict):
        return out
    for item in version.get("files") or []:
        if isinstance(item, dict):
            add(item.get("kind") or _file_kind(item.get("path") or ""), item.get("path"), item.get("sha256"))
    add("pdf", version.get("pdf"), version.get("sha256"))
    for key in ("script", "embed"):
        item = version.get(key)
        if isinstance(item, dict):
            add(key, item.get("path"), item.get("sha256"))
    return out


def _embed_sha(version) -> str | None:
    embed = version.get("embed") if isinstance(version, dict) else None
    sha = embed.get("sha256") if isinstance(embed, dict) else None
    return sha if isinstance(sha, str) and sha else None


def active_pictures(package: Package) -> list[dict]:
    """Pictures in the accepted view of document.xml: the wp:docPr and pic:cNvPr names, the
    relationship of each a:blip r:embed (or VML v:imagedata r:id), the media part it resolves to
    and that part's SHA-256 (None when the part is missing or linked from outside)."""
    accepted = views.view_doc(package.document, "accept")
    rels = {rel.get("Id"): rel for rel in package.relationships(MAIN_PART)}
    pictures = []
    for element in accepted.elements:
        if element.tag == "a:blip":
            rid = element.get("r:embed") or element.get("r:link")
            container = next((a for a in element.ancestors() if a.tag in ("wp:inline", "wp:anchor")), None)
        elif element.tag == "v:imagedata":
            rid = element.get("r:id")
            container = next((a for a in element.ancestors() if a.tag == "v:shape"), None)
        else:
            continue
        docpr = cnvpr = None
        if container is not None and container.tag != "v:shape":
            docpr = container.find("wp:docPr")
            cnvpr = next(container.iter("pic:cNvPr"), None)
        rel = rels.get(rid) or {}
        part = package.resolve(MAIN_PART, rel.get("Target", "")) if rel and rel.get("TargetMode") != "External" else None
        data = package.parts.get(part) if part else None
        pictures.append({"name": docpr.get("name") if docpr is not None else None,
                         "picture_name": cnvpr.get("name") if cnvpr is not None else None,
                         "rid": rid, "target": part, "sha256": sha256_bytes(data) if data is not None else None})
    return pictures


def _read_pictures(path) -> tuple[list | None, str | None]:
    try:
        return active_pictures(Package(fs(path))), None
    except OSError as error:
        return None, _why(error)
    except (ValueError, KeyError, zipfile.BadZipFile) as error:
        return None, str(error)


def _picture_state(figure: dict, version: dict, pictures: list | None, error: str | None) -> dict:
    """Does the manuscript's accepted view hold a picture with the bytes of the version's embed PNG?"""
    sha = _embed_sha(version)
    if not sha:
        return {"state": "not recorded"}
    if pictures is None:
        return {"state": "unreadable", "expected": sha, "error": error}
    hit = next((p for p in pictures if p.get("sha256") == sha), None)
    if hit is not None:
        return {"state": "match", "expected": sha, "picture": hit.get("name"), "target": hit.get("target")}
    present = {p.get("sha256") for p in pictures if p.get("sha256")}
    shown = next((v.get("version") for v in figure.get("versions") or []
                  if isinstance(v, dict) and v is not version and _embed_sha(v) in present), None)
    return {"state": "not_shown", "expected": sha, "shown_version": shown}


def _project_file(project: Project, raw, flag: str, directory: bool = False) -> tuple[Path, str]:
    """An existing file (or folder) inside the project, outside the package and the archive."""
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate if _exists(Path.cwd() / candidate) else project.path(raw)
    candidate = _plain(candidate.resolve())
    if not _exists(candidate):
        raise ProjectError(f"{flag} {raw} does not exist")
    if directory and not _is_dir(candidate):
        raise ProjectError(f"{flag} {raw} is not a folder")
    if not directory and not _is_file(candidate):
        raise ProjectError(f"{flag} {raw} is not a file")
    if not project.inside(candidate) or candidate == project.root:
        raise ProjectError(f"{flag} {raw} is outside the project; register figure files where the build wrote them "
                           f"inside it (for example {FIGURES}/<figure folder>/out/vNN/)")
    rel = project.rel(candidate)
    for area, what in ((ARCHIVE, "in the archive"), (PACKAGE, "a package copy")):
        if _norm(rel) == _norm(area) or _norm(rel).startswith(_norm(area) + "/"):
            raise ProjectError(f"{flag} {rel} is {what}; register the file the figure build wrote")
    if is_lock(candidate.name):
        raise ProjectError(f"{flag} {rel} is a lock file")
    return candidate, rel


def _figure_folder(project: Project, identifier: str, title: str, folder_text) -> str:
    if folder_text:
        candidate = Path(folder_text)
        path = candidate if candidate.is_absolute() else project.root / candidate
    else:
        words = clean_words(title).replace(" ", "_")[:48].rstrip("_.-")
        path = project.root / FIGURES / (f"{identifier}_{words}" if words else identifier)
    path = _plain(path.resolve())
    rel = project.rel(path)
    key = _norm(rel)
    if not project.inside(path) or not key.startswith(_norm(FIGURES) + "/") or key == _norm(FIGURES + "/Shared"):
        raise ProjectError(f"--folder {folder_text} must be a figure folder inside {FIGURES}/ of the project "
                           f"(for example {FIGURES}/{identifier}_Short_name)")
    if any(BAD_NAME_CHARS.search(part) for part in rel.split("/")):
        raise ProjectError(f"--folder {rel} contains characters that are not allowed in file names")
    if _exists(path) and not _is_dir(path):
        raise ProjectError(f"{rel} exists and is not a folder")
    return rel


def _finish_figure_event(project: Project, event: str, figures: dict, identifier: str, label: str | None,
                         index: dict | None = None) -> bool:
    """Complete an interrupted figure operation whose index change was already written."""
    fields = {"figure": identifier} if label is None else {"figure": identifier, "figure_version": label}
    unfinished = _unresolved_pending(project, event, None, **fields)
    if not unfinished:
        return False
    notes = project.commit_indexes(index=index, figures=figures)
    project.ledger_append({"event": event, "status": "complete", "ref": unfinished[-1]["seq"], **fields,
                           "resumed": True})
    for note in notes:
        print(f"NOTE: {note}")
    print(f"Finished the interrupted {event.replace('_', ' ')} of {identifier}{' ' + label if label else ''}: "
          "the index was already updated; generated blocks refreshed and the ledger event completed.")
    return True


def _figure_command(project: Project, event: str, fields: dict, pending_extra: dict, change) -> list[str]:
    """Write-ahead wrapper: pending ledger event, the change (which returns notes), then complete."""
    project.refuse_leftovers()
    pending = project.ledger_append({"event": event, "status": "pending", **fields, **pending_extra})
    try:
        notes = change()
    except (OSError, ProjectError) as error:
        project.ledger_append({"event": event, "status": "failed", "ref": pending["seq"], **fields,
                               "error": _ledger_error(project, error)})
        raise _failure(pending, error) from error
    project.ledger_append({"event": event, "status": "complete", "ref": pending["seq"], **fields})
    return notes


def cmd_figure_add(args) -> int:
    project = _project(args)
    figures = project.figure_index_for_update()
    identifier = figure_id(args.id)
    title = " ".join(str(args.title or "").split())
    if not title:
        raise ProjectError("--title needs the figure's short title, e.g. \"Study design\"")
    folder_rel = _figure_folder(project, identifier, title, args.folder)
    existing = _find_figure(figures, identifier)
    if existing is not None:
        if (existing.get("title") == title and _norm(existing.get("folder") or "") == _norm(folder_rel)
                and _finish_figure_event(project, "figure_add", figures, existing["id"], None)):
            return 0
        raise ProjectError(f"{existing['id']} is already registered (folder {existing.get('folder')}); a figure id is "
                           "never reused. Register new builds of it with `msw.py figure register`.")
    for other in _figure_items(figures):
        if _norm(other.get("folder") or "") == _norm(folder_rel):
            raise ProjectError(f"{folder_rel} is already the folder of {other.get('id')}")
    folder = project.path(folder_rel)
    start_here = folder / START_HERE
    entry = {"id": identifier, "title": title, "folder": folder_rel, "current": None, "versions": []}
    created = []

    def change():
        if not _is_dir(folder):
            _makedirs(folder)
            created.append(folder_rel + "/")
        if not _exists(start_here):
            write_new_text(start_here, template("START_HERE.md.tmpl", {
                "figure_id": identifier, "figure_title": title, "figure_folder": folder_rel,
                "figure_block": render_figure_block(entry), "msw": _msw_in(project.root)[0], "date": today(),
                "project": project.root.name}))
            created.append(project.rel(start_here))
        figures["figures"].append(entry)
        return project.commit_indexes(figures=figures)

    notes = _figure_command(project, "figure_add", {"figure": identifier},
                            {"title": title, "folder": folder_rel}, change)
    print(f"Added {identifier} \"{title}\" to {FIGURE_INDEX}")
    for rel in created:
        print(f"  + {rel}")
    if project.rel(start_here) not in created:
        print(f"  = {project.rel(start_here)} (kept as it was)")
    for note in notes:
        print(f"NOTE: {note}")
    print(f"Next: after each build, register its files with\n  python \"{MSW.as_posix()}\" figure register --id "
          f"{identifier} --version v01 --file <pdf> --file <png> --script <build script> --embed <embed png>")
    return 0


def cmd_figure_register(args) -> int:
    project = _project(args)
    figures = project.figure_index_for_update()
    figure = _require_figure(figures, args.id)
    label = figure_version_label(args.version)
    hashes = Hashes()
    problems, files, given = [], [], set()
    for raw in args.file:
        try:
            path, rel = _project_file(project, raw, "--file")
        except ProjectError as error:
            problems += error.problems
            continue
        if _norm(rel) in given:
            problems.append(f"--file {rel} is given twice")
            continue
        given.add(_norm(rel))
        files.append({"kind": _file_kind(rel), "path": rel, "sha256": hashes(path), "size": os.stat(fs(path)).st_size})
    for kind in PACKAGED_KINDS:
        count = sum(1 for item in files if item["kind"] == kind)
        if count > 1:
            problems.append(f"{count} .{kind} files given; register one {kind.upper()} per version (the PNG placed in "
                            "the manuscript goes in --embed)")
    script = embed = qa = None
    if args.script:
        try:
            path, rel = _project_file(project, args.script, "--script")
            script = {"path": rel, "sha256": hashes(path)}
        except ProjectError as error:
            problems += error.problems
    if args.qa:
        try:
            qa = _project_file(project, args.qa, "--qa", directory=True)[1]
        except ProjectError as error:
            problems += error.problems
    if args.embed:
        try:
            path, rel = _project_file(project, args.embed, "--embed")
            with open(fs(path), "rb") as handle:
                is_png = handle.read(len(PNG_MAGIC)) == PNG_MAGIC
            if is_png:
                embed = {"path": rel, "sha256": hashes(path), "size": os.stat(fs(path)).st_size}
            else:
                problems.append(f"--embed {rel} is not a PNG file")
        except ProjectError as error:
            problems += error.problems
    entry = {"version": label, "date": today(), "registered_utc": utc_now()}
    pdf = next((item for item in files if item["kind"] == "pdf"), None)
    if pdf:
        entry["pdf"], entry["sha256"] = pdf["path"], pdf["sha256"]
    entry["files"] = files
    for key, value in (("script", script), ("qa", qa), ("embed", embed), ("note", args.note)):
        if value:
            entry[key] = value
    entry["used_in"] = []
    existing = _figure_version(figure, label)
    if existing is not None:
        same = {(rel, sha) for _, rel, sha in _version_files(existing)} == \
               {(rel, sha) for _, rel, sha in _version_files(entry)}
        if not problems and same and _finish_figure_event(project, "figure_register", figures, figure["id"],
                                                          existing.get("version")):
            return 0
        matches = [m for m in (FIGURE_VERSION_RE.match(str(v.get("version") or ""))
                               for v in figure.get("versions") or [] if isinstance(v, dict)) if m]
        following = max([int(m.group(1)) for m in matches] + [0]) + 1
        padded = any(len(m.group(1)) > 1 for m in matches)
        raise ProjectError(f"{figure['id']} {existing.get('version')} is already registered"
                           + (f" ({existing['date']})" if existing.get("date") else "")
                           + "; a figure version is never replaced. Register the new build as "
                           + (f"v{following:02d}." if padded else f"v{following}."))
    named, scripts = {}, {}
    for other in _figure_items(figures):
        for version in other.get("versions") or []:
            for kind, rel, sha in _version_files(version):
                owner = f"{other.get('id')} {version.get('version') if isinstance(version, dict) else ''}"
                if kind == "script":
                    scripts.setdefault(_norm(rel), (owner, sha))
                else:
                    named.setdefault(_norm(rel), owner)
    for kind, rel, sha in _version_files(entry):
        if kind == "script":
            owner, recorded = scripts.get(_norm(rel), (None, sha))
            if recorded != sha:
                problems.append(f"the build script {rel} changed since it was registered for {owner}; a script is not "
                                "edited after it has produced outputs: save the new one under a new name")
        elif _norm(rel) in named:
            problems.append(f"{rel} is already registered as {named[_norm(rel)]}; every version has its own files "
                            "(a rebuilt figure is written to a new out/vNN/ folder)")
    if problems:
        raise ProjectError(*problems)

    def change():
        figure["versions"].append(entry)
        return project.commit_indexes(figures=figures)

    notes = _figure_command(project, "figure_register", {"figure": figure["id"], "figure_version": label},
                            {k: entry[k] for k in ("files", "script", "qa", "embed") if k in entry}, change)
    print(f"Registered {figure['id']} {label}; nothing was copied or moved:")
    for kind, rel, sha in _version_files(entry):
        print(f"  {kind:<6} {rel}  [sha256 {sha[:12]}]")
    if qa:
        print(f"  qa     {qa}")
    for note in notes:
        print(f"NOTE: {note}")
    print(f"Next: when the manuscript pass that shows this version is ready, promote it:\n  python \"{MSW.as_posix()}\" "
          f"figure promote --id {figure['id']} --version {label}")
    return 0


def cmd_figure_promote(args) -> int:
    project = _project(args)
    project.require_schema()
    figures = project.figure_index_for_update()
    figure = _require_figure(figures, args.id)
    label = figure_version_label(args.version)
    version = _figure_version(figure, label)
    if version is None:
        registered = ", ".join(str(v.get("version")) for v in figure["versions"] if isinstance(v, dict)) or "none"
        raise ProjectError(f"{figure['id']} {label} is not registered (registered: {registered}); register it first "
                           "with `msw.py figure register`")
    label = version.get("version")
    hashes = Hashes()
    problems = [f"{kind} {rel} is {check} (registered sha256 {sha[:12]})" for kind, rel, sha in _version_files(version)
                for check in [_check(project, hashes, rel, sha)] if check != "ok"]
    if problems:
        raise ProjectError(f"{figure['id']} {label} no longer matches its registration; register a new version "
                           "instead:", *problems)
    index = project.project_index()
    figure_set = dict(index.get("figure_set") or {}) if isinstance(index.get("figure_set"), dict) else {}
    previous = figure.get("current")
    if _fv_key(previous) == _fv_key(label) and figure_set.get(figure["id"]) == label:
        if not _finish_figure_event(project, "figure_promote", figures, figure["id"], label, index):
            print(f"{figure['id']} {label} is already the promoted version; nothing to do.")
        return 0

    def change():
        figure["current"] = label
        figure_set[figure["id"]] = label
        index["figure_set"] = figure_set
        return project.commit_indexes(index=index, figures=figures)

    notes = _figure_command(project, "figure_promote", {"figure": figure["id"], "figure_version": label},
                            {"previous": previous}, change)
    print(f"Promoted {figure['id']} {label}" + (f" (was {previous})" if previous else "")
          + f"; {PROJECT_INDEX} figure_set, {FIGURES_README} and START_HERE.md updated.")
    for note in notes:
        print(f"NOTE: {note}")
    current = index.get("current")
    if current:
        pictures, error = _read_pictures(project.path(current["path"]))
        state = _picture_state(figure, version, pictures, error)
        if state["state"] == "match":
            print(f"The current manuscript {current['version']} shows it (picture \"{state.get('picture')}\").")
        elif state["state"] == "not_shown":
            shown = f" (it shows {state['shown_version']})" if state.get("shown_version") else ""
            print(f"The current manuscript {current['version']} does not show it yet{shown}. Next: "
                  f"{_swap_next_step(figure, version)}.")
        elif state["state"] == "unreadable":
            print(f"WARNING: cannot read the pictures of {current['path']}: {state.get('error')}")
        else:
            print("No embed PNG is recorded for this version, so the manuscript picture is not checked.")
    return 0


def collect_figure_status(project: Project) -> dict:
    """Each figure's promoted version, its files re-hashed, and whether the current manuscript's
    accepted view holds the version's embed PNG (read-only)."""
    hashes = Hashes()
    index = project.project_index()
    current = index.get("current")
    figure_set = index.get("figure_set") if isinstance(index.get("figure_set"), dict) else {}
    report = {"root": str(project.root), "manuscript": None, "pictures": [], "picture_error": None,
              "figures": [], "problems": [], "warnings": []}
    try:
        data = project.figure_index()
    except ProjectError as error:
        report["problems"] += error.problems
        return report
    if not isinstance(data, dict) or not isinstance(data.get("figures"), list):
        report["problems"].append(f"{FIGURE_INDEX} has no flat figures list (not schema_version 2)")
        return report
    pictures = error = None
    if current:
        report["manuscript"] = {"version": current.get("version"), "state": current.get("state"),
                                "path": current.get("path")}
        pictures, error = _read_pictures(project.path(current["path"]))
        report["pictures"], report["picture_error"] = pictures or [], error
    for figure in _figure_items(data):
        identifier = figure.get("id")
        folder = figure.get("folder")
        item = {"id": identifier, "title": figure.get("title"), "folder": folder, "current": figure.get("current"),
                "figure_set": figure_set.get(identifier),
                "start_here": bool(folder) and _is_file(project.path(f"{folder}/{START_HERE}")),
                "versions": [], "embedded": None}
        for version in figure.get("versions") or []:
            if not isinstance(version, dict):
                continue
            checked = [{"kind": kind, "path": rel, "sha256": sha, "check": _check(project, hashes, rel, sha)}
                       for kind, rel, sha in _version_files(version)]
            item["versions"].append({"version": version.get("version"), "date": version.get("date"),
                                     "used_in": version.get("used_in") or [], "note": version.get("note"),
                                     "files": checked})
            for file in checked:
                if file["check"] != "ok":
                    report["problems"].append(f"{identifier} {version.get('version')} {file['kind']} {file['path']} "
                                              f"is {file['check']}")
        promoted = _figure_version(figure, figure.get("current"))
        if figure.get("current") and promoted is None:
            report["problems"].append(f"{identifier}: current names {figure.get('current')}, which is not registered")
        if promoted is not None and current:
            item["embedded"] = _picture_state(figure, promoted, pictures, error)
            state = item["embedded"]["state"]
            if state == "not_shown":
                shown = (f" (it shows {item['embedded']['shown_version']})"
                         if item["embedded"].get("shown_version") else "")
                item["embedded"]["next"] = _swap_next_step(figure, promoted)
                report["warnings"].append(f"{identifier} {figure.get('current')}: NOT SHOWN: the current manuscript "
                                          f"{current.get('version')} does not show its embed PNG{shown}. Next: "
                                          f"{item['embedded']['next']}")
            elif state == "unreadable":
                report["problems"].append(f"{identifier}: cannot read the pictures of {current.get('path')}: {error}")
        if (figure.get("current") or item["figure_set"]) and _fv_key(item["figure_set"]) != _fv_key(figure.get("current")):
            report["problems"].append(f"{identifier}: {PROJECT_INDEX} figure_set says {item['figure_set'] or 'nothing'}, "
                                      f"{FIGURE_INDEX} current is {figure.get('current') or 'none'}")
        if folder and not item["start_here"]:
            report["problems"].append(f"{identifier}: {folder}/{START_HERE} is missing")
        report["figures"].append(item)
    known = {_fkey(f.get("id")) for f in _figure_items(data)}
    for identifier in figure_set:
        if _fkey(identifier) not in known:
            report["problems"].append(f"{PROJECT_INDEX} figure_set names {identifier}, which {FIGURE_INDEX} lacks")
    return report


PICTURE_TEXT = {"match": "MATCH", "not_shown": "NOT SHOWN", "unreadable": "UNREADABLE",
                "not recorded": "not checked"}


def print_figure_status(report: dict) -> None:
    manuscript = report["manuscript"]
    where = (f"current manuscript {manuscript['version']} ({manuscript['path']})" if manuscript
             else "no current manuscript")
    print(f"Figures: {len(report['figures'])} in {FIGURE_INDEX}; {where}")
    for item in report["figures"]:
        print(f"{item['id']} \"{item.get('title') or ''}\"  {item.get('folder') or ''}")
        registered = ", ".join(str(v["version"]) for v in item["versions"]) or "none"
        if not item["current"]:
            print(f"  current: none promoted (registered: {registered})")
            continue
        print(f"  current: {item['current']} (figure_set: {item['figure_set'] or 'none'}; registered: {registered})")
        version = next((v for v in item["versions"] if _fv_key(v["version"]) == _fv_key(item["current"])), None)
        for file in (version or {}).get("files", []):
            print(f"  [{file['check']}] {file['kind']:<6} {file['path']}")
        if version and version.get("used_in"):
            print(f"  released in: {', '.join(version['used_in'])}")
        state = item.get("embedded")
        if state:
            text = PICTURE_TEXT.get(state["state"], state["state"])
            if state["state"] == "match":
                detail = f" picture \"{state.get('picture')}\" ({state.get('target')}) has the embed PNG's bytes"
            elif state["state"] == "not_shown":
                detail = (f": no picture in the accepted view has the embed PNG's bytes (sha256 "
                          f"{state['expected'][:12]})" + (f"; it shows {state['shown_version']}"
                                                          if state.get("shown_version") else ""))
            elif state["state"] == "unreadable":
                detail = f" {state.get('error')}"
            else:
                detail = " (no embed PNG recorded)"
            print(f"  picture in {manuscript['version']}: {text}{detail}")
    if report.get("warnings"):
        print("Warnings:")
        for warning in report["warnings"]:
            print(f"  - {warning}")
    if report["problems"]:
        print("Problems:")
        for problem in report["problems"]:
            print(f"  - {problem}")
    else:
        print("No problems found.")


def cmd_figure_status(args) -> int:
    project = _project(args)
    report = collect_figure_status(project)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_figure_status(report)
    return 0


# -- open items ----------------------------------------------------------------------
#
# PROJECT_INDEX.json "outstanding" lists the open items shown in the README and STATUS blocks:
# {"id": "O3", "text": ..., "since": "V02"}. `outstanding done` moves an item to "resolved" with the
# version it was closed in. Items are never deleted.

ITEM_ID_RE = re.compile(r"^[Oo]?(\d{1,5})$")


def _item_number(item) -> int | None:
    match = ITEM_ID_RE.match(str((item or {}).get("id") or "")) if isinstance(item, dict) else None
    return int(match.group(1)) if match else None


def cmd_outstanding(args) -> int:
    project = _project(args)
    project.require_schema()
    index = project.project_index()
    items = [item for item in index.get("outstanding") or [] if item] if isinstance(index.get("outstanding"), list) \
        else []
    resolved = list(index.get("resolved") or []) if isinstance(index.get("resolved"), list) else []
    version = (index.get("current") or {}).get("version")
    if args.outstanding_command == "add":
        text = " ".join(str(args.text or "").split())
        if not text:
            raise ProjectError("--text needs the open item in a few words")
        number = max([n for n in (_item_number(item) for item in items + resolved) if n] + [0]) + 1
        item = {"id": f"O{number}", "text": text, "since": version}
        index["outstanding"] = items + [item]
        notes = project.commit_indexes(index=index)
        project.ledger_append({"event": "outstanding", "action": "add", **item})
        print(f"Added {item['id']}: {text}" + (f" (since {version})" if version else ""))
    else:
        match = ITEM_ID_RE.match(str(args.id or "").strip())
        wanted = int(match.group(1)) if match else None
        found = next((item for item in items if wanted is not None and _item_number(item) == wanted), None)
        if found is None:
            done = next((item for item in resolved if wanted is not None and _item_number(item) == wanted), None)
            if done is not None:
                raise ProjectError(f"{done.get('id')} is already resolved"
                                   + (f" (in {done['resolved_in']})" if done.get("resolved_in") else ""))
            listed = ", ".join(str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id"))
            raise ProjectError(f"no open item {args.id!r} (open: {listed or 'none'})")
        record = dict(found, resolved_in=version, resolved_utc=utc_now())
        index["outstanding"] = [item for item in items if item is not found]
        index["resolved"] = resolved + [record]
        notes = project.commit_indexes(index=index)
        project.ledger_append({"event": "outstanding", "action": "done", "id": found.get("id"),
                               "text": found.get("text"), "since": found.get("since"), "resolved_in": version})
        print(f"Resolved {found.get('id')}: {found.get('text')}" + (f" (in {version})" if version else ""))
    for note in notes:
        print(f"NOTE: {note}")
    print(f"{PROJECT_INDEX}, README.md and {STATUS} updated; {len(index['outstanding'])} open item(s).")
    return 0


# -- retarget ------------------------------------------------------------------------
#
# A retarget changes the journal: `venue` (the code in file names) and `journal_profile` in
# manuscript.json, `venue` and `target_journal` in PROJECT_INDEX.json and the generated blocks.
# Version numbers keep counting. The previous manuscript.json is copied into the archive first.

def _block_free_lines(text: str) -> list[tuple[int, str]]:
    """(line number, line) of a Markdown file outside its generated msw blocks."""
    out, inside = [], False
    for number, line in enumerate(text.splitlines(), 1):
        if re.match(r"\s*<!-- msw:[a-z]+:start -->", line):
            inside = True
        elif re.match(r"\s*<!-- msw:[a-z]+:end -->", line):
            inside = False
        elif not inside:
            out.append((number, line))
    return out


def _stale_venue_lines(project: Project, venue: str) -> list[str]:
    """Hand-written lines (outside generated blocks) that still name the old venue code."""
    if not venue:
        return []
    pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(venue)}(?![A-Za-z0-9])")
    found = []
    for rel in ("AGENTS.md", "CLAUDE.md", README, "06_Project_Docs/Workflows/AGENTS.msw.md"):
        path = project.path(rel)
        if not _is_file(path):
            continue
        try:
            text = _fsp(path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        found += [f"{rel} line {number}: {line.strip()}" for number, line in _block_free_lines(text)
                  if pattern.search(line) and "when the project was set up" not in line]
    return found


def cmd_retarget(args) -> int:
    project = _project(args)
    project.require_schema()
    venue = str(args.venue or "").strip()
    if not venue or clean_words(venue) != venue or " " in venue:
        raise ProjectError("--venue must be a short code without spaces or reserved characters (e.g. JOE)")
    journal = " ".join(str(args.target_journal or "").split())
    if not journal:
        raise ProjectError("--target-journal needs the journal's full name")
    config = copy.deepcopy(project.config)
    index = project.project_index()
    old = {"venue": config.get("venue"), "target_journal": index.get("target_journal"),
           "journal_profile": config.get("journal_profile")}
    profile, warnings = old["journal_profile"], []
    if args.journal_profile:
        profile, candidate = _profile_in_project(project.root, args.journal_profile)
        if not _is_file(candidate):
            warnings.append(f"{profile} does not exist yet; write the new journal's profile there before running "
                            "lint or wordcount")
    elif venue != old["venue"]:
        warnings.append(f"journal_profile still names {profile} (the previous journal's rules?); write the new "
                        "journal's profile as a new file and run retarget again with --journal-profile")
    new = {"venue": venue, "target_journal": journal, "journal_profile": profile}
    changes = {key: (old[key], new[key]) for key in new if old[key] != new[key]}
    # A retarget to the same journal that stopped part-way (a swap blocked by another program, a
    # crash) is finished from its pending ledger event, even when manuscript.json and
    # PROJECT_INDEX.json already name the new journal and only generated blocks are stale.
    resume = next((event for event in reversed(_unresolved_pending(project, "retarget", None))
                   if event.get("to") == new), None)
    before = dict(resume.get("from") or old) if resume else old
    stale = _stale_venue_lines(project, before["venue"]) if before["venue"] and before["venue"] != venue else []
    print(f"Retarget ({'dry run' if args.dry_run else 'apply'}): {before['target_journal'] or before['venue']} "
          f"-> {journal}")
    places = {"venue": f"{CONFIG_NAME} and {PROJECT_INDEX}", "target_journal": PROJECT_INDEX,
              "journal_profile": CONFIG_NAME}
    for key, (was, now) in changes.items():
        print(f"  {key}: {was!r} -> {now!r}  ({places[key]})")
    for warning in warnings:
        print(f"WARNING: {warning}")
    if not changes and resume is None:
        print("Nothing to change: the project already targets this journal.")
        return 0
    leftovers = project.leftovers()
    if resume is not None:
        print(f"The retarget to {journal} (ledger event {resume['seq']}) never completed; "
              + ("run without --dry-run, this command finishes it: " if args.dry_run else "finishing it: ")
              + (f"{', '.join(leftovers)} move{'s' if len(leftovers) == 1 else ''} into "
                 f"{ARCHIVE}/<date>/Interrupted_Run/, then " if leftovers else "")
              + f"{CONFIG_NAME}, {PROJECT_INDEX} and the generated blocks are written for {journal}.")
    print("Version numbers keep counting; new file names use the new venue code. Released files keep their names.")
    if args.dry_run:
        for line in stale:
            print(f"NOTE: {line} (update by hand after the retarget)")
        print("Dry run: nothing was changed.")
        return 0
    config_changes = config.get("venue") != venue or config.get("journal_profile") != profile
    if resume is None:
        project.refuse_leftovers()
        planned = _config_backup_path(project, old["venue"]) if config_changes else None
        pending = project.ledger_append({"event": "retarget", "status": "pending", "from": old, "to": new,
                                         **({"backup": planned} if planned else {}),
                                         **({"note": args.note} if args.note else {})})
    else:
        pending = resume
    backup = None
    try:
        if resume is not None:
            _archive_leftovers(project, pending)
        if config_changes:
            backup = _config_backup(project, before["venue"], pending.get("backup"))
            config["venue"], config["journal_profile"] = venue, profile
            atomic_replace(project.root / CONFIG_NAME, _json_text(config))
            project.config = config
        elif resume is not None and pending.get("backup") and _is_file(project.path(pending["backup"])):
            backup = {"path": pending["backup"], "sha256": sha256_file(fs(project.path(pending["backup"])))}
        index["venue"], index["target_journal"] = venue, journal
        notes = project.commit_indexes(index=index)
    except (OSError, ProjectError) as error:
        project.ledger_append({"event": "retarget", "status": "failed", "ref": pending["seq"],
                               "error": _ledger_error(project, error)})
        raise _failure(pending, error) from error
    project.ledger_append({"event": "retarget", "status": "complete", "ref": pending["seq"], "from": before, "to": new,
                           **({"previous_config": backup} if backup else {}),
                           **({"resumed": True} if resume is not None else {})})
    for note in notes:
        print(f"NOTE: {note}")
    if backup:
        print(f"The previous {CONFIG_NAME} was copied to {backup['path']} [sha256 {backup['sha256'][:12]}].")
    print(f"Retargeted to {journal} ({venue}): {CONFIG_NAME}, {PROJECT_INDEX}, README.md and STATUS.md updated.")
    for line in stale:
        print(f"NOTE: {line} (still names {before['venue']}; update it by hand)")
    print(f"Record the retarget and its reason in {DECISIONS}; the changes themselves are tracked passes "
          "(msw.py pass new).")
    return 0


def _config_backup_path(project: Project, venue) -> str:
    """Where retarget keeps the previous manuscript.json: a new 99_Archive/<date>/Retarget_from_<venue>/."""
    base = f"{ARCHIVE}/{today()}/Retarget_from_{clean_words(venue or 'none').replace(' ', '_')}"
    folder = next(candidate for candidate in (base + (f"_{k}" if k > 1 else "") for k in range(1, 1000))
                  if not _exists(project.path(f"{candidate}/{CONFIG_NAME}")))
    return f"{folder}/{CONFIG_NAME}"


def _config_backup(project: Project, venue, planned: str | None) -> dict:
    """Copy manuscript.json (as a new file) to the path the pending retarget event planned; a copy an
    interrupted run already made there is reused, and a planned path taken by other bytes is left
    alone for the next free folder."""
    source = project.root / CONFIG_NAME
    sha = sha256_file(fs(source))
    if planned and _is_file(project.path(planned)) and sha256_file(fs(project.path(planned))) == sha:
        return {"path": planned, "sha256": sha}
    rel = planned if planned and not _exists(project.path(planned)) else _config_backup_path(project, venue)
    return {"path": rel, "sha256": copy_exclusive(source, project.path(rel))}


# -- command registration --------------------------------------------------------------

def _guard(function):
    def run(args):
        try:
            return function(args)
        except ProjectError as error:
            for problem in error.problems:
                print(f"ERROR: {problem}", file=sys.stderr)
            return 2
        except OSError as error:
            print(f"ERROR: {_why(error)}", file=sys.stderr)
            return 2
    run.__name__ = function.__name__
    run.__doc__ = function.__doc__
    return run


def _date(text: str) -> str:
    if not DATE_RE.match(text):
        raise ValueError(text)
    _dt.date.fromisoformat(text)
    return text


_date.__name__ = "date (YYYY-MM-DD)"


def register(subparsers) -> None:
    project_help = "project folder (default: the nearest folder with manuscript.json above the working folder)"

    p = subparsers.add_parser("init", help="create a manuscript project (refuses a non-empty folder unless --adopt)")
    p.add_argument("dir", nargs="?", default=".", help="project folder (default: the working folder)")
    p.add_argument("--title", help="short working title")
    p.add_argument("--venue", help="journal or document code used in file names, e.g. JOE or Thesis")
    p.add_argument("--author", help="the author's full name")
    p.add_argument("--initials", help="file-name initials, e.g. AB")
    p.add_argument("--revision-name", help="w:author name on tracked changes (default: --author)")
    p.add_argument("--target-journal", help="full journal name for PROJECT_INDEX.json (default: --venue)")
    p.add_argument("--journal-profile", help="journal profile path inside the project, kept relative to it "
                                             f"(default: {DEFAULT_PROFILE})")
    p.add_argument("--spelling", choices=["en-US", "en-GB", "en-CA"], default="en-US")
    p.add_argument("--kind", choices=["journal_article", "thesis"], default="journal_article")
    p.add_argument("--figures-backend", choices=["python", "r"], default="python")
    p.add_argument("--science-changes", choices=list(SCIENCE_CHANGES), default="comments",
                   help="policy.science_changes in manuscript.json: 'comments' (questions about the science are Word "
                        "comments; the default) or 'tracked_for_review' (science edits as tracked changes the author "
                        "reviews); numbers change only with the author's approval or a recomputation")
    p.add_argument("--adopt", action="store_true", help="add only missing skeleton files to a non-empty folder "
                                                        "(a folder holding only .agents, .claude, .codex, .git, .idea "
                                                        "or .vscode counts as empty)")
    p.add_argument("--git", action="store_true", help="git init, core.autocrlf=false, core.longpaths=true, Git LFS")
    p.set_defaults(func=_guard(_locked(cmd_init, "init --adopt", root=_adopted_root)))

    p = subparsers.add_parser("status", help="current file, author saves, Word locks, passes in flight (read-only)")
    p.add_argument("--project", help=project_help)
    p.add_argument("--json", action="store_true", help="print the status as JSON")
    p.set_defaults(func=_guard(cmd_status))

    p = subparsers.add_parser("intake", help="copy a .docx into Versions/VNN or Incoming and index it")
    p.add_argument("docx")
    p.add_argument("--as-version", help="version number, e.g. V07 (default: V01 for a project's first file)")
    p.add_argument("--role", choices=["released", "incoming", "author_saved"], default="released")
    p.add_argument("--slug", help="words for the file name (default: none)")
    p.add_argument("--state", help="state words for the file name (default by role: 'baseline' for the project's "
                                   "first file, 'received' for incoming, 'author saved' or 'author accepted' for "
                                   "author_saved)")
    p.add_argument("--date", type=_date, help="date in the file name (default: today)")
    p.add_argument("--set-current", action="store_true", help="make this file current (first intake always is); a "
                                                              "supervisor's or co-author's returned copy the next pass "
                                                              "builds on: --role author_saved --state \"<name> "
                                                              "comments\" --set-current (keeps the version number)")
    p.add_argument("--returned-by", help="who returned the file (recorded as returned_by in VERSION_INDEX; default: "
                                         "the name in a --state such as \"JD comments\")")
    p.add_argument("--note", help="note stored in the index and ledger")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--project", help=project_help)
    p.set_defaults(func=_guard(_locked(cmd_intake, "intake", when=lambda a: not a.dry_run)))

    p = subparsers.add_parser("pass", help="work passes: `pass new --slug WORDS` claims the next version")
    pass_sub = p.add_subparsers(dest="pass_command", required=True)
    q = pass_sub.add_parser("new", help="claim the next version number and create its pass folder")
    q.add_argument("--slug", required=True, help="two to six words naming the pass")
    q.add_argument("--version", help="claim this number instead of the next free one (refused if taken)")
    q.add_argument("--project", help=project_help)
    q.set_defaults(func=_guard(_locked(cmd_pass_new, "pass new")))

    p = subparsers.add_parser("release", help="release a gated build: plan prints every copy and move; apply runs them")
    p.add_argument("mode", choices=["plan", "apply"])
    p.add_argument("--pass", dest="pass_dir", required=True, help="pass folder (90_Agent_Work/passes/VNN_<slug>)")
    p.add_argument("--build", help="the built .docx (default: the single build in the newest build*/ folder)")
    p.add_argument("--manifest", help="build manifest (default: <build>.manifest.json)")
    p.add_argument("--proof", help="proof PDF of the accepted view (default: the single PDF in proofs/)")
    p.add_argument("--figures", nargs="+", metavar="ID=vNN",
                   help="promoted figure versions this release brings in: their PDF and PNG are copied into "
                        "Figures/ and the copies they replace are archived (default: figures_changed in pass.json)")
    p.add_argument("--project", help=project_help)
    p.set_defaults(func=_guard(_locked(cmd_release, "release apply", when=lambda a: a.mode == "apply")))

    p = subparsers.add_parser("archive", help="move files into 99_Archive/<date>/<Reason>/ with hashes in the ledger")
    p.add_argument("mode", choices=["plan", "apply"])
    p.add_argument("paths", nargs="+")
    p.add_argument("--reason", required=True, help="archive folder name, e.g. Superseded_Drafts")
    p.add_argument("--project", help=project_help)
    p.set_defaults(func=_guard(_locked(cmd_archive, "archive apply", when=lambda a: a.mode == "apply")))

    p = subparsers.add_parser("retarget", help="change the target journal: venue code and journal profile in "
                                               "manuscript.json, target journal in PROJECT_INDEX.json, generated "
                                               "blocks, ledger (version numbers keep counting)")
    p.add_argument("--venue", required=True, help="the new journal's short code for file names, e.g. JOE")
    p.add_argument("--target-journal", required=True, help="the new journal's full name")
    p.add_argument("--journal-profile", help="the new journal's profile inside the project (default: keep the "
                                             "current journal_profile)")
    p.add_argument("--note", help="why, stored in the ledger")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--project", help=project_help)
    p.set_defaults(func=_guard(_locked(cmd_retarget, "retarget", when=lambda a: not a.dry_run)))

    p = subparsers.add_parser("author-save", help="register the author's Word save of the package's current copy as "
                                                  "the next pass baseline")
    p.add_argument("docx")
    p.add_argument("--version", help="version it belongs to (default: the current version)")
    p.add_argument("--state", choices=["accepted", "saved"],
                   help="'author accepted' or 'author saved' (default: saved if tracked changes remain)")
    p.add_argument("--date", type=_date, help="date in the file name (default: today)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--project", help=project_help)
    p.set_defaults(func=_guard(_locked(cmd_author_save, "author-save", when=lambda a: not a.dry_run)))

    p = subparsers.add_parser("verify-project", help="re-hash every file the indexes and ledger name (read-only)")
    p.add_argument("--project", help=project_help)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_guard(cmd_verify_project))

    p = subparsers.add_parser("outstanding", help="open items shown in README.md and STATUS.md: "
                                                  "`outstanding add --text T`, `outstanding done --id O3`")
    items_sub = p.add_subparsers(dest="outstanding_command", required=True)
    q = items_sub.add_parser("add", help="add an open item (since the current version)")
    q.add_argument("--text", required=True, help="the open item in a few words")
    q.add_argument("--project", help=project_help)
    q.set_defaults(func=_guard(_locked(cmd_outstanding, lambda a: f"outstanding {a.outstanding_command}")))
    q = items_sub.add_parser("done", help="move an open item to the resolved list with the current version")
    q.add_argument("--id", required=True, help="item id, e.g. O3")
    q.add_argument("--project", help=project_help)
    q.set_defaults(func=_guard(_locked(cmd_outstanding, lambda a: f"outstanding {a.outstanding_command}")))

    p = subparsers.add_parser("figure", help="figure registry in 02_Figures/FIGURE_INDEX.json: add, register, "
                                             "promote, status")
    figure_sub = p.add_subparsers(dest="figure_command", required=True)
    q = figure_sub.add_parser("add", help="register a figure and create its folder and START_HERE.md if missing")
    q.add_argument("--id", required=True, help="figure id, e.g. Figure_01")
    q.add_argument("--title", required=True, help="short figure title, e.g. \"Study design\"")
    q.add_argument("--folder", help="figure folder inside 02_Figures/, relative to the project "
                                    "(default: 02_Figures/<id>_<Title_words>)")
    q.add_argument("--project", help=project_help)
    q.set_defaults(func=_guard(_locked(cmd_figure_add, "figure add")))
    q = figure_sub.add_parser("register", help="record a new figure version and the SHA-256 of its files "
                                               "(nothing is copied or moved; an existing version is refused)")
    q.add_argument("--id", required=True, help="figure id")
    q.add_argument("--version", required=True, help="figure version, e.g. v02")
    q.add_argument("--file", action="append", required=True, metavar="PATH",
                   help="an exported figure file (pdf, png, svg, tif, ...); repeat for each; at most one PDF and PNG")
    q.add_argument("--script", help="the build script that wrote the files")
    q.add_argument("--qa", help="the QA folder of this version's gate run")
    q.add_argument("--embed", metavar="PNG", help="the PNG placed in the manuscript (checked against the .docx)")
    q.add_argument("--note", help="what changed in this version")
    q.add_argument("--project", help=project_help)
    q.set_defaults(func=_guard(_locked(cmd_figure_register, "figure register")))
    q = figure_sub.add_parser("promote", help="make a registered version current (FIGURE_INDEX, PROJECT_INDEX "
                                              "figure_set, generated blocks)")
    q.add_argument("--id", required=True, help="figure id")
    q.add_argument("--version", required=True, help="registered figure version, e.g. v02")
    q.add_argument("--project", help=project_help)
    q.set_defaults(func=_guard(_locked(cmd_figure_promote, "figure promote")))
    q = figure_sub.add_parser("status", help="current versions, file hashes and the pictures in the current "
                                             "manuscript (read-only)")
    q.add_argument("--json", action="store_true", help="print the report as JSON")
    q.add_argument("--project", help=project_help)
    q.set_defaults(func=_guard(cmd_figure_status))

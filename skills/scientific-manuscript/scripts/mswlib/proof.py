"""Rendered proofs (LibreOffice) and native Word QA (Windows) for a built manuscript.

proof    Writes the accepted, rejected or as-is view as a new .docx, converts it to
         PDF with LibreOffice under a hard timeout, optionally rasterizes pages with
         pypdfium2 or PyMuPDF, and records the command, versions, timings and hashes
         in proof.json. The files in --out have short names ("accepted view.docx",
         "accepted view.pdf", page01.png) so deep pass folders stay under Windows'
         260-character limit; proof.json names the source file and, when --out is
         inside a project, records paths relative to the project root. LibreOffice
         runs on a persistent profile (never deleted) that by default is one short
         per-user folder outside every project, because a profile inside a project
         planted paths over the limit.
word-qa  Drives an isolated, hidden Word instance through scripts/word_qa.ps1 on a
         read-only copy, then asserts Word's own accept/reject results against the
         file-side views. Only the Word process that the run started is ever
         stopped, and only with --reap-own-process.

Every output is created exclusively; existing files are never replaced.
"""

from __future__ import annotations

import csv
import datetime
import difflib
import io
import json
import os
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

from . import __version__, pathutil
from .config import find_config, write_json_exclusive
from .docx import MAIN_PART, Package, PackageError, lock_files, sha256_bytes, sha256_file
from .pathutil import fs
from .views import project
from .wordml import SYMBOL_PUA, element_char, font_kind, iter_paragraphs
from .xmltree import XMLDoc, XMLError

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
WORD_QA_SCRIPT = SCRIPTS_DIR / "word_qa.ps1"
VIEW_STEM = {"accept": "accepted view", "reject": "rejected view", "as-is": "as-is view"}
PROJECTED_STORIES = ("header", "footer", "footnotes", "endnotes")

LIBREOFFICE_NOTES = [
    "LibreOffice does not show a deletion nested inside an earlier, unaccepted insertion: "
    "check such edits in Word.",
    "LibreOffice does not refresh EndNote fields: citations and the bibliography show the results "
    "stored in the file. Refresh EndNote in Word before the final PDF.",
    "LibreOffice line breaks and pagination can differ from Word's; use Word for page-exact checks.",
]

# Text normalization shared with word_qa.ps1 (the patterns are sent in its input file, so
# Python and .NET apply the same classes). Word's Content.Text uses \r between paragraphs,
# \x07 at cell ends, \x0b for line breaks, \x0c/\x0e for page/column breaks, \x01/\x02/\x05
# for object, note and comment marks and \x1e/\x1f for non-breaking and optional hyphens; the
# file side uses U+FFFC for objects and U+2011/U+00AD for hyphens. Inline pictures appear
# as "/" in Word's text, which marked_texts reproduces. Word reports Symbol-font text in the
# private-use area and an inserted symbol (w:sym) as "("; SYMBOL_PUA folds the former into
# Unicode and marked_texts reproduces the latter.
WS_PATTERN = r"[\t\n\x0b\x0c\r\x0e \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+"
REMOVE_PATTERN = r"[\x01\x02\x05\x07\x13\x14\x15\x1f\xad\u200b\ufeff\ufffc]"
HYPHEN_PATTERN = r"[\x1e\u2011]"
_WS = re.compile(WS_PATTERN)
_REMOVE = re.compile(REMOVE_PATTERN)
_HYPHEN = re.compile(HYPHEN_PATTERN)
FIELD_OPEN, FIELD_CLOSE = "\ue000", "\ue001"   # private-use marks around field results

PROBE_CHARS = 60
MIN_PROBE_CHARS = 8
LONG_PROFILE_PATH = 150


class ProofError(RuntimeError):
    pass


def normalize(text: str) -> str:
    """Collapse whitespace and drop object/control marks so Word, PDF and file texts compare."""
    text = (text or "").translate(SYMBOL_PUA)
    return _WS.sub(" ", _HYPHEN.sub("-", _REMOVE.sub("", text))).strip(" ")


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fail(message: str, code: int = 2) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return code


def _is_windows() -> bool:
    return os.name == "nt"


def _os_message(error: Exception) -> str:
    """The error text, or advice when it was caused by Windows' 260-character path limit."""
    advice = pathutil.explain(error) if isinstance(error, OSError) else None
    return advice or str(error)


# File operations go through pathutil.fs(), which returns the extended-length form of a long
# absolute path on Windows. Paths handed to LibreOffice, PowerShell or Word stay plain.

def _is_file(path) -> bool:
    return os.path.isfile(fs(path))


def _exists(path) -> bool:
    return os.path.exists(fs(path))


def _read_bytes(path) -> bytes:
    with open(fs(path), "rb") as handle:
        return handle.read()


def _makedirs(path):
    os.makedirs(fs(path), exist_ok=True)


def _new_folder(path: Path) -> str | None:
    """None when `path` is new or empty, else the reason to refuse."""
    if _exists(path):
        if not os.path.isdir(fs(path)):
            return f"{path} exists and is not a folder"
        if os.listdir(fs(path)):
            return f"{path} exists and is not empty; choose a new folder"
    return None


def _write_new(path: Path, data: bytes):
    with open(fs(path), "xb") as handle:
        handle.write(data)


def _write_text_new(path: Path, text: str):
    with open(fs(path), "x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _tail(path: Path, limit: int = 4000) -> str:
    try:
        data = _read_bytes(path)
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace")


def project_root(folder) -> Path | None:
    """The project folder (holding manuscript.json) that contains `folder`, else None."""
    config = find_config(folder)
    return config.parent if config is not None else None


def record_path(path, root: Path | None) -> str:
    """A path for proof.json: relative (POSIX) to the project root when inside it, else absolute.

    The absolute form is not resolved, so it names the folder the user knows (resolving can
    follow redirected or linked folders to a different spelling)."""
    if root is not None:
        try:
            return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            pass
    return os.path.abspath(str(path))


def relative_text(text: str, root: Path | None) -> str:
    """Program output with the project root's absolute prefix removed (LibreOffice prints full paths)."""
    if root is None or not text:
        return text
    forms = {os.path.abspath(str(root)), str(Path(root).resolve())}
    forms |= {"\\\\?\\" + form for form in forms}       # resolved long paths carry the \\?\ prefix
    for form in sorted(forms | {f.replace("\\", "/") for f in forms}, key=len, reverse=True):
        for separator in ("\\", "/"):
            text = text.replace(form + separator, "")
    return text


# -- views and expected texts ---------------------------------------------------------

def view_changes(package: Package, view: str) -> dict:
    """Projected story parts that differ from the source (empty for as-is)."""
    if view == "as-is":
        return {}
    names = [MAIN_PART]
    for rel in package.relationships(MAIN_PART):
        kind = rel.get("Type", "").rsplit("/", 1)[-1]
        if kind in PROJECTED_STORIES and rel.get("TargetMode") != "External":
            name = package.resolve(MAIN_PART, rel.get("Target", ""))
            if name in package.parts and name not in names:
                names.append(name)
    changes = {}
    for name in names:
        projected = project(package.xml(name), view)
        if projected != package.parts[name]:
            changes[name] = projected
    return changes


_SKIP = {"w:p", "mc:Fallback", "w:pPr", "w:rPr", "w:instrText", "w:delInstrText", "w:rPrChange",
         "w:pPrChange", "w:fldData"}
_CLOSE_MARK = object()
_OBJECTS = {"w:drawing", "w:pict", "w:object"}


def _inline_object(node) -> bool:
    if node.tag == "w:drawing":
        return any(child.tag == "wp:inline" for child in node.elements())
    if node.tag == "w:pict":
        return not any("position:absolute" in (shape.get("style") or "").replace(" ", "")
                       for shape in node.elements())
    return True


def marked_texts(doc: XMLDoc) -> list[str]:
    """Paragraph texts as Word's Content.Text shows them (wordml.paragraph_text, except that
    pictures and objects appear as Word shows them), with top-level field results wrapped in
    FIELD_OPEN/FIELD_CLOSE. Field state is tracked across paragraphs (bibliographies)."""
    stack: list[bool] = []      # open complex fields; True once past the separator
    texts = []
    for paragraph in iter_paragraphs(doc):
        out = [FIELD_OPEN] if stack and stack[0] else []
        nodes = [(child, None) for child in reversed(paragraph.elements())]
        while nodes:
            node, font = nodes.pop()
            if node is _CLOSE_MARK:
                out.append(FIELD_CLOSE)
                continue
            tag = node.tag
            if tag in _SKIP:
                continue
            if tag == "w:fldChar":
                kind = node.get("w:fldCharType")
                if kind == "begin":
                    stack.append(False)
                elif kind == "separate" and stack:
                    stack[-1] = True
                    if len(stack) == 1:
                        out.append(FIELD_OPEN)
                elif kind == "end" and stack:
                    in_result = stack.pop()
                    if not stack and in_result:
                        out.append(FIELD_CLOSE)
                continue
            if tag == "w:fldSimple" and not stack:
                out.append(FIELD_OPEN)
                nodes.append((_CLOSE_MARK, None))
                nodes.extend((child, font) for child in reversed(node.elements()))
                continue
            if tag in _OBJECTS:
                # Word's Content.Text shows an inline picture or object as "/" and a floating
                # one as nothing; marked like a field result so a different rendering is
                # reported rather than failed.
                shown = "/" if _inline_object(node) else ""
                out.append(shown if stack else FIELD_OPEN + shown + FIELD_CLOSE)
                continue
            if tag == "w:sym":
                out.append("(")
                continue
            char = element_char(doc, node, deleted=False, font=font)
            if char is not None:
                out.append(char)
                continue
            if tag in ("w:del", "w:moveFrom"):
                continue
            if tag == "w:r":
                font = font_kind(node.find("w:rPr"))
            nodes.extend((child, font) for child in reversed(node.elements()))
        if stack and stack[0]:
            out.append(FIELD_CLOSE)
        texts.append("".join(out))
    return texts


def strip_marks(text: str) -> str:
    return text.replace(FIELD_OPEN, "").replace(FIELD_CLOSE, "")


def expected_views(package: Package) -> dict:
    """{'accept': [marked paragraph texts], 'reject': [...]} of the main story."""
    return {mode: marked_texts(XMLDoc(project(package.document, mode))) for mode in ("accept", "reject")}


def inline_picture_count(package: Package, mode: str) -> int:
    doc = XMLDoc(project(package.document, mode))
    return sum(1 for _ in doc.iter("wp:inline"))


def probes_from_manifest(manifest: dict) -> list[str]:
    """First PROBE_CHARS normalized characters of each edited paragraph's accepted text."""
    probes = []
    for item in manifest.get("expected_accept") or []:
        if item.get("deleted"):
            continue
        probe = normalize(item.get("text") or "")[:PROBE_CHARS].strip(" ")
        if len(probe) >= MIN_PROBE_CHARS and probe not in probes:
            probes.append(probe)
    return probes


def collect_probes(manifest_path, extra) -> list[str]:
    probes = []
    if manifest_path:
        manifest = json.loads(_read_bytes(manifest_path).decode("utf-8-sig"))
        probes.extend(probes_from_manifest(manifest))
    for text in extra or []:
        probe = normalize(text)[:PROBE_CHARS].strip(" ")
        if probe and probe not in probes:
            probes.append(probe)
    return probes


# -- text comparison -----------------------------------------------------------------

def _diff_hunks(expected: str, actual: str, limit: int = 25) -> list[dict]:
    a, b = expected.split(" "), actual.split(" ")
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    hunks = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        hunks.append({"op": tag, "before": " ".join(a[max(0, i1 - 8):i1])[-160:],
                      "expected": " ".join(a[i1:i2])[:400], "word": " ".join(b[j1:j2])[:400],
                      "after": " ".join(a[i2:i2 + 8])[:160]})
        if len(hunks) >= limit:
            break
    return hunks


def compare_view_text(expected_marked: list[str], word_text: str) -> dict:
    """Compare Word's Content.Text with the expected view.

    status: identical | identical_except_field_results | different. Field results may
    legitimately differ (Word shows what it computes; the file stores the last result),
    so a text that differs only inside field results is reported, not failed.
    """
    marked = normalize(" ".join(expected_marked))
    plain = normalize(strip_marks(marked))
    word = normalize(word_text)
    result = {"expected_chars": len(plain), "word_chars": len(word), "field_differences": [], "hunks": []}
    if plain == word:
        result["status"] = "identical"
        return result
    pieces = re.split("(" + FIELD_OPEN + ".*?" + FIELD_CLOSE + ")", marked)
    pos, gap_expected, ok = 0, None, True
    differences = []

    def settle_gap(end):
        gap = word[pos:end].strip(" ")
        wanted = gap_expected.strip(" ")
        if gap != wanted:
            if len(gap) > 2 * len(wanted) + 100:
                return False
            differences.append({"expected": wanted[:300], "word": gap[:300]})
        return True

    for piece in pieces:
        if piece.startswith(FIELD_OPEN):
            gap_expected = (gap_expected or "") + " " + normalize(piece[1:-1])
            continue
        literal = normalize(piece)
        if not literal:
            continue
        if gap_expected is None:
            start = pos + 1 if word.startswith(" ", pos) else pos
            if not word.startswith(literal, start):
                ok = False
                break
            index = start
        else:
            index = word.find(literal, pos)
            if index < 0 or not settle_gap(index):
                ok = False
                break
            gap_expected = None
        pos = index + len(literal)
    if ok:
        if gap_expected is not None:
            ok = settle_gap(len(word))
        elif word[pos:].strip(" "):
            ok = False
    if ok:
        result["status"] = "identical_except_field_results" if differences else "identical"
        result["field_differences"] = differences[:50]
        return result
    result["status"] = "different"
    result["hunks"] = _diff_hunks(plain, word)
    return result


# -- process helpers -------------------------------------------------------------------

_KERNEL32 = None


def _kernel32():
    global _KERNEL32
    if _KERNEL32 is None:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        _KERNEL32 = k
    return _KERNEL32


def process_info(pid: int) -> dict | None:
    """Windows: {'alive', 'image', 'created' (epoch seconds)} or None if no such process."""
    if not _is_windows() or not pid:
        return None
    import ctypes
    from ctypes import wintypes
    k = _kernel32()
    handle = k.OpenProcess(0x1000, False, int(pid))    # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        k.GetExitCodeProcess(handle, ctypes.byref(code))
        size = wintypes.DWORD(4096)
        buffer = ctypes.create_unicode_buffer(4096)
        image = buffer.value if k.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else ""
        times = [wintypes.FILETIME() for _ in range(4)]
        created = None
        if k.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            created = ticks / 10_000_000 - 11_644_473_600
        return {"pid": int(pid), "alive": code.value == 259, "image": image, "created": created}
    finally:
        k.CloseHandle(handle)


def _terminate(pid: int) -> bool:
    k = _kernel32()
    handle = k.OpenProcess(0x0001 | 0x1000, False, int(pid))   # PROCESS_TERMINATE
    if not handle:
        return False
    try:
        return bool(k.TerminateProcess(handle, 1))
    finally:
        k.CloseHandle(handle)


def own_word_alive(pid: int, run_started: float) -> bool:
    """True while `pid` is still the WINWORD.EXE this run started (not a reused PID)."""
    info = process_info(pid)
    return bool(info and info["alive"] and info["image"].lower().endswith("winword.exe")
                and (info["created"] is None or info["created"] >= run_started - 5))


def reap_own_word(pid: int, run_started: float, wait: float = 10.0) -> dict:
    """Stop `pid` only if it is still a WINWORD.EXE created after this run started."""
    info = process_info(pid)
    record = {"pid": pid, "action": "none"}
    if info is None or not info["alive"]:
        record["action"] = "already_exited"
        return record
    if not info["image"].lower().endswith("winword.exe"):
        record.update(action="refused", reason=f"PID {pid} now belongs to {info['image'] or 'another program'}")
        return record
    if info["created"] is not None and info["created"] < run_started - 5:
        record.update(action="refused", reason=f"PID {pid} started before this run; it is not this run's Word")
        return record
    if not _terminate(pid):
        record.update(action="failed", reason="TerminateProcess was refused")
        return record
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        info = process_info(pid)
        if info is None or not info["alive"]:
            record["action"] = "stopped"
            return record
        time.sleep(0.25)
    record.update(action="failed", reason="still running after TerminateProcess")
    return record


def _task_pids(image: str) -> list[int] | None:
    """Windows: PIDs of processes with this image name (tasklist), None if unavailable."""
    try:
        run = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=60, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    pids = []
    for row in csv.reader(io.StringIO(run.stdout)):
        if len(row) >= 2 and row[0].lower() == image.lower() and row[1].isdigit():
            pids.append(int(row[1]))
    return pids


def _soffice_running() -> bool | None:
    if _is_windows():
        pids = _task_pids("soffice.bin")
        return None if pids is None else bool(pids)
    if shutil.which("pgrep"):
        run = subprocess.run(["pgrep", "-f", "soffice.bin"], capture_output=True, timeout=30)
        return run.returncode == 0
    return None


def _kill_tree(proc: subprocess.Popen) -> str:
    """Stop the process this command started and its children, nothing else."""
    try:
        if _is_windows():
            run = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                                 text=True, timeout=60, errors="replace")
            outcome = (run.stdout + run.stderr).strip()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
            outcome = f"killed process group {proc.pid}"
    except (OSError, subprocess.SubprocessError) as error:
        outcome = f"tree kill failed: {error}"
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        outcome += "; the launcher did not exit"
    return outcome


# -- LibreOffice -------------------------------------------------------------------------

def find_soffice(explicit: str | None = None) -> Path | None:
    candidates = []
    if explicit:
        candidates.append(explicit)
    else:
        if os.environ.get("MSW_SOFFICE"):
            candidates.append(os.environ["MSW_SOFFICE"])
        candidates += ["soffice", "libreoffice"]
        if _is_windows():
            for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), "C:/Program Files"):
                if base:
                    candidates.append(str(Path(base) / "LibreOffice" / "program" / "soffice.exe"))
        elif sys.platform == "darwin":
            candidates.append("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path.resolve()
        found = shutil.which(candidate)
        if found:
            return Path(found).resolve()
    return None


def _launcher(soffice: Path) -> Path:
    """On Windows prefer soffice.com (console launcher: waits and reports) beside soffice.exe."""
    if soffice.name.lower() == "soffice.exe":
        console = soffice.with_name("soffice.com")
        if console.is_file():
            return console
    return soffice


def soffice_version(launcher: Path, profile_uri: str) -> str | None:
    try:
        run = subprocess.run([str(launcher), f"-env:UserInstallation={profile_uri}", "--version"],
                             capture_output=True, text=True, timeout=60, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    line = run.stdout.strip().splitlines()
    return line[0].strip() if line else None


def default_profile() -> Path:
    """The persistent LibreOffice profile: one short per-user folder, inside or outside a project.

    LibreOffice writes files about 140 characters below its profile folder. A profile inside a
    project or the output folder therefore produced paths over Windows' 260-character limit
    (and one inside --out crashed LibreOffice), so the default is always
    LOCALAPPDATA/msw/lo_profile, else XDG_CACHE_HOME/msw/lo_profile, else ~/.cache/msw/lo_profile.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "msw" / "lo_profile"


def _long_output_note(path: Path) -> str | None:
    """Advice for a failed conversion whose output path is over Windows' limit."""
    length = len(os.path.abspath(str(path)))
    if _is_windows() and length >= 259 and not pathutil.long_paths_enabled():
        return (f"'{path.name}' in --out has a {length}-character path, over Windows' 260-character limit, "
                "which can stop LibreOffice writing it. Use a shorter --out folder or enable long paths.")
    return None


def profile_busy(profile: Path) -> str | None:
    """Reason to refuse when another LibreOffice may be using this profile, else None."""
    lock = profile / ".lock"
    if not _exists(lock):
        return None
    running = _soffice_running()
    if running is False:
        return None     # stale lock from an earlier, interrupted run
    state = "LibreOffice (soffice.bin) is running" if running else "running LibreOffice processes cannot be listed"
    return (f"the profile {profile} holds a lock file and {state}; a second instance on the same profile "
            "hands the job to the first one and exits without output. Close LibreOffice or pass --profile-dir")


def _pdf_page_count(data: bytes) -> int | None:
    counts = [int(m) for m in re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data, re.S)]
    counts += [int(m) for m in re.findall(rb"/Count\s+(\d+)[^>]*?/Type\s*/Pages\b", data, re.S)]
    return max(counts) if counts else None


# -- rasterizers ---------------------------------------------------------------------------

def _png(width: int, height: int, rows: bytes, stride: int, channels: int) -> bytes:
    colour = {1: 0, 3: 2, 4: 6}[channels]
    raw = b"".join(b"\x00" + rows[y * stride:y * stride + width * channels] for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class _Pdfium:
    name = "pypdfium2"

    def __init__(self, module, data: bytes):
        self.module = module
        self.pdf = module.PdfDocument(data)     # bytes: no path reaches the library

    @staticmethod
    def version(module) -> str:
        info = getattr(getattr(module, "version", None), "PYPDFIUM_INFO", None) or getattr(module, "V_PYPDFIUM2", "")
        return str(info)

    def count(self) -> int:
        return len(self.pdf)

    def text(self, index: int) -> str:
        page = self.pdf[index]
        textpage = page.get_textpage()
        try:
            getter = getattr(textpage, "get_text_bounded", None) or getattr(textpage, "get_text_range")
            return getter()
        finally:
            textpage.close()
            page.close()

    def png(self, index: int, dpi: int) -> tuple[bytes, int, int]:
        page = self.pdf[index]
        try:
            bitmap = page.render(scale=dpi / 72, rev_byteorder=True)
            try:
                import PIL.Image  # noqa: F401  (optional; only decides how the PNG is encoded)
            except ImportError:
                if bitmap.n_channels != 3:
                    raise ProofError("pypdfium2 without Pillow can only write RGB bitmaps")
                return (_png(bitmap.width, bitmap.height, bytes(bitmap.buffer), bitmap.stride, 3),
                        bitmap.width, bitmap.height)
            image = bitmap.to_pil()
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            return buffer.getvalue(), image.width, image.height
        finally:
            page.close()

    def close(self):
        self.pdf.close()


class _PyMuPDF:
    name = "pymupdf"

    def __init__(self, module, data: bytes):
        self.doc = module.open(stream=data, filetype="pdf")

    @staticmethod
    def version(module) -> str:
        return str(getattr(module, "VersionBind", "") or getattr(module, "version", [""])[0])

    def count(self) -> int:
        return self.doc.page_count

    def text(self, index: int) -> str:
        return self.doc[index].get_text()

    def png(self, index: int, dpi: int) -> tuple[bytes, int, int]:
        pixmap = self.doc[index].get_pixmap(dpi=dpi)
        return pixmap.tobytes("png"), pixmap.width, pixmap.height

    def close(self):
        self.doc.close()


def load_rasterizer(choice: str = "auto"):
    """(backend class, module) for the first importable backend, or (None, None)."""
    order = {"auto": ("pypdfium2", "pymupdf"), "pypdfium2": ("pypdfium2",), "pymupdf": ("pymupdf",),
             "none": ()}[choice]
    for name in order:
        try:
            if name == "pypdfium2":
                import pypdfium2 as module
                return _Pdfium, module
            try:
                import pymupdf as module
            except ImportError:
                import fitz as module
            return _PyMuPDF, module
        except ImportError:
            continue
    return None, None


def parse_pages(text: str | None) -> list[int] | None:
    if not text:
        return None
    pages = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError(f"bad page item {part!r}: use N or N-M, comma separated")
        first, last = int(match.group(1)), int(match.group(2) or match.group(1))
        if first < 1 or last < first or last - first > 5000:
            raise ValueError(f"bad page range {part!r}")
        pages.extend(p for p in range(first, last + 1) if p not in pages)
    return pages


def locate_probes(page_texts: list[str], probes: list[str]) -> list[dict]:
    found = []
    for probe in probes:
        pages = [i + 1 for i, text in enumerate(page_texts) if probe in text]
        match = "full" if pages else "none"
        if not pages and len(probe) > 30:
            short = probe[:30].strip(" ")
            pages = [i + 1 for i, text in enumerate(page_texts) if short in text]
            match = "prefix30" if pages else "none"
        found.append({"text": probe, "pages": pages, "match": match})
    return found


# -- proof command -------------------------------------------------------------------------

def _valid_name(name: str) -> bool:
    return bool(name.strip()) and not re.search(r'[<>:"/\\|?*\x00-\x1f]', name) and name.strip(" .") == name


def cmd_proof(args) -> int:
    started = time.monotonic()
    docx = Path(args.docx)
    out = Path(args.out)
    if not _is_file(docx):
        return _fail(f"{docx} does not exist")
    if args.timeout <= 0:
        return _fail("--timeout must be a positive number of seconds")
    if not 30 <= args.dpi <= 600:
        return _fail("--dpi must be between 30 and 600")
    try:
        requested = parse_pages(args.pages)
    except ValueError as error:
        return _fail(str(error))
    reason = _new_folder(out)
    if reason:
        return _fail(reason)
    stem = args.name or VIEW_STEM[args.view]
    if not _valid_name(stem):
        return _fail(f"--name {stem!r} is not a valid file name")
    try:
        probes = collect_probes(args.manifest, args.probes)
    except (OSError, ValueError) as error:
        return _fail(f"cannot read probes: {_os_message(error)}")
    soffice = find_soffice(args.soffice)
    if soffice is None:
        return _fail("LibreOffice was not found: pass --soffice PATH or set MSW_SOFFICE")
    profile = Path(args.profile_dir) if args.profile_dir else default_profile()
    busy = profile_busy(profile)
    if busy:
        return _fail(busy)
    try:
        package = Package(docx)
        changes = view_changes(package, args.view)
    except (PackageError, XMLError, OSError) as error:
        return _fail(f"cannot read {docx}: {_os_message(error)}")
    try:
        return _run_proof(args, started, docx, out, stem, probes, requested, soffice, profile, package, changes)
    except OSError as error:
        return _fail(f"the proof stopped: {_os_message(error)}")


def _run_proof(args, started, docx: Path, out: Path, stem: str, probes, requested, soffice: Path, profile: Path,
               package: Package, changes: dict) -> int:
    launcher = _launcher(soffice)
    _makedirs(out)
    view_docx = out / f"{stem}.docx"
    pdf = out / f"{stem}.pdf"
    record_file = out / "proof.json"
    for target in (view_docx, pdf, record_file):
        if _exists(target):
            return _fail(f"{target} already exists; proofs are never overwritten")
    try:
        view_sha = package.write(view_docx, changes)
    except PackageError as error:
        return _fail(f"the {args.view} view could not be written: {error}")
    t_view = time.monotonic()

    root = project_root(out)

    def rel(path) -> str:
        return record_path(path, root)

    profile_created = not _exists(profile)
    _makedirs(profile)                                # persistent: never deleted
    profile_uri = profile.resolve().as_uri()
    version = soffice_version(launcher, profile_uri)
    out_arg, view_arg = str(out.resolve()), str(view_docx.resolve())
    command = [str(launcher), f"-env:UserInstallation={profile_uri}", "--headless", "--norestore",
               "--nolockcheck", "--convert-to", "pdf", "--outdir", out_arg, view_arg]
    record = {
        "schema": "msw-proof/1", "tool": {"name": "msw proof", "version": __version__,
                                          "python": sys.version.split()[0]},
        "started_utc": _utc(), "status": "running",
        "paths": "relative to the project root" if root is not None else "absolute",
        "source": {"name": docx.name, "path": rel(docx), "sha256": package.sha256},
        "view": args.view,
        "view_docx": {"name": view_docx.name, "path": rel(view_docx), "sha256": view_sha,
                      "parts_projected": sorted(changes)},
        "libreoffice": {"executable": str(soffice), "launcher": str(launcher), "version": version,
                        "profile_dir": rel(profile), "profile_default": not args.profile_dir,
                        "profile_created": profile_created},
        "command": [rel(item) if item in (out_arg, view_arg) else item for item in command],
        "timeout_s": args.timeout, "returncode": None,
        "pdf": None, "rasterizer": None, "dpi": args.dpi, "probes": [], "pages_requested": requested,
        "pages_rendered": [], "timings_s": {}, "notes": list(LIBREOFFICE_NOTES), "errors": [],
    }
    if _is_windows() and len(str(profile.resolve())) > LONG_PROFILE_PATH:
        record["notes"].append(f"The profile path is {len(str(profile.resolve()))} characters long; LibreOffice "
                               "writes paths about 140 characters deeper and can fail under Windows' 260-character "
                               "limit. Pass a shorter --profile-dir if conversions fail.")
    long_output = _long_output_note(pdf)      # advice added only if the conversion fails
    print(f"source: {docx.name}")
    print(f"view: {view_docx}")
    print(f"LibreOffice: {version or soffice} (profile {profile})")

    # One retry: LibreOffice's first start on a new profile occasionally exits without output.
    for attempt in (1, 2):
        result = _convert(command, out, attempt, args.timeout, pdf)
        record.setdefault("attempts", []).append(result)
        if result["status"] != "failed" or result["returncode"] is None:
            break
        if attempt == 1:
            print(f"WARNING: LibreOffice exited with code {result['returncode']} and no PDF; retrying once",
                  file=sys.stderr)
    t_convert = time.monotonic()
    record["returncode"] = result["returncode"]
    record["stdout_tail"] = relative_text(result.pop("stdout_tail"), root)
    record["stderr_tail"] = relative_text(result.pop("stderr_tail"), root)
    for item in record["attempts"]:
        item.pop("stdout_tail", None)
        item.pop("stderr_tail", None)
    record["status"] = result["status"]
    for item in record["attempts"][:-1]:
        record["notes"].append(f"Attempt {item['attempt']} was retried: {item['error']}")
    if result["error"]:
        record["errors"].append(result["error"])
        if long_output and record["status"] != "ok":
            record["errors"].append(long_output)

    if record["status"] == "ok":
        data = _read_bytes(pdf)
        record["pdf"] = {"name": pdf.name, "path": rel(pdf), "sha256": sha256_bytes(data), "bytes": len(data),
                         "pages": _pdf_page_count(data), "pages_method": "pdf-dictionary"}
        backend, module = load_rasterizer(args.rasterizer)
        if backend is None:
            record["probes"] = [{"text": p, "pages": None, "match": "not searched"} for p in probes]
            if args.rasterizer != "none":
                record["notes"].append("No page images or probe pages: install pypdfium2 or PyMuPDF.")
        else:
            record["rasterizer"] = {"name": backend.name, "version": backend.version(module)}
            _rasterize(backend, module, data, out, probes, requested, args.dpi, record, rel)
    t_end = time.monotonic()
    record["timings_s"] = {"view": round(t_view - started, 3), "convert": round(t_convert - t_view, 3),
                           "rasterize": round(t_end - t_convert, 3), "total": round(t_end - started, 3)}
    record["finished_utc"] = _utc()
    write_json_exclusive(record_file, record)

    if record["status"] != "ok":
        for error in record["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"record: {record_file}")
        return 1
    pages = record["pdf"]["pages"]
    print(f"PDF: {pdf} ({pages if pages is not None else 'unknown'} pages, "
          f"{record['timings_s']['convert']} s)")
    for item in record["probes"]:
        where = ", ".join(map(str, item["pages"] or [])) or item["match"].replace("none", "not found")
        print(f"probe '{item['text'][:40]}': page {where}")
    for item in record["pages_rendered"]:
        print(f"page {item['page']}: {out / item['name']}")
    for error in record["errors"]:
        print(f"WARNING: {error}", file=sys.stderr)
    for note in record["notes"]:
        print(f"NOTE: {note}")
    print(f"record: {record_file}")
    return 0


def _convert(command: list, out: Path, attempt: int, timeout: int, pdf: Path) -> dict:
    """Run one LibreOffice conversion under a hard timeout; logs go to new files in `out`."""
    suffix = "" if attempt == 1 else f"_{attempt}"
    stdout_path, stderr_path = out / f"soffice_stdout{suffix}.txt", out / f"soffice_stderr{suffix}.txt"
    result = {"attempt": attempt, "returncode": None, "status": "failed", "error": None}
    started = time.monotonic()
    with open(fs(stdout_path), "xb") as stdout, open(fs(stderr_path), "xb") as stderr:
        options = {} if _is_windows() else {"start_new_session": True}
        try:
            proc = subprocess.Popen(command, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, **options)
        except OSError as error:
            proc = None
            result["error"] = f"LibreOffice could not be started: {error}"
        if proc is not None:
            try:
                result["returncode"] = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result["status"] = "timeout"
                result["error"] = f"LibreOffice did not finish within {timeout} s; " + _kill_tree(proc)
    result["seconds"] = round(time.monotonic() - started, 3)
    result["stdout_tail"], result["stderr_tail"] = _tail(stdout_path), _tail(stderr_path)
    if result["status"] == "failed" and proc is not None:
        if _is_file(pdf):
            result["status"] = "ok"
        else:
            result["error"] = (f"LibreOffice exited with code {result['returncode']} without writing the PDF "
                               f"(another instance on the same profile, or a conversion error: see {stderr_path.name})")
    return result


def _rasterize(backend, module, pdf_data: bytes, out: Path, probes, requested, dpi, record, rel):
    try:
        reader = backend(module, pdf_data)
    except Exception as error:      # a broken optional library must not lose the proof
        record["errors"].append(f"{backend.name} could not open the PDF: {error}")
        return
    try:
        count = reader.count()
        record["pdf"]["pages"] = count
        record["pdf"]["pages_method"] = backend.name
        texts = []
        if probes:
            texts = [normalize(reader.text(i)) for i in range(count)]
            record["probes"] = locate_probes(texts, probes)
        if requested is None:
            pages = [1] if count else []
            for item in record["probes"]:
                pages.extend(p for p in item["pages"] if p not in pages)
        else:
            pages = requested
        width = max(2, len(str(count)))
        for page in pages:
            if page > count:
                record["errors"].append(f"page {page} requested but the PDF has {count} pages")
                continue
            target = out / f"page{page:0{width}d}.png"   # short names stay under the Windows path limit
            try:
                data, w, h = reader.png(page - 1, dpi)
                _write_new(target, data)
            except FileExistsError:
                record["errors"].append(f"{target} already exists; not replaced")
                continue
            except Exception as error:
                record["errors"].append(f"page {page} was not rendered: {_os_message(error)}")
                continue
            record["pages_rendered"].append({"page": page, "name": target.name, "path": rel(target),
                                             "sha256": sha256_bytes(data), "width": w, "height": h})
    except Exception as error:      # keep the PDF and the record even if the optional library fails
        record["errors"].append(f"{backend.name} failed: {error}")
    finally:
        reader.close()


# -- word-qa command -----------------------------------------------------------------------

def prepare_word_qa(docx: Path, out: Path, *, probes: list[str], timeout: int, exit_wait: int = 30) -> dict:
    """Write the expected texts, the read-only copy and the script's input file into `out`.

    Returns the input dictionary (also written to word_qa_input.json)."""
    package = Package(docx)
    views = expected_views(package)
    _makedirs(out)
    paths = {name: (out / name).resolve() for name in (
        "expected_accept.txt", "expected_reject.txt", "input_readonly.docx", "word_raw.json",
        "word_accepted.txt", "word_rejected.txt", "word_pid.txt", "progress.log", "word_qa_input.json")}
    _write_text_new(paths["expected_accept.txt"], "\n".join(strip_marks(t) for t in views["accept"]) + "\n")
    _write_text_new(paths["expected_reject.txt"], "\n".join(strip_marks(t) for t in views["reject"]) + "\n")
    blob = _read_bytes(docx)
    if sha256_bytes(blob) != package.sha256:
        raise ProofError(f"{docx} changed while it was being read")
    _write_new(paths["input_readonly.docx"], blob)
    os.chmod(fs(paths["input_readonly.docx"]), stat.S_IREAD)      # Word cannot save over it
    data = {
        "schema": "msw-word-qa-input/1",
        "source_docx": str(docx.resolve()), "source_sha256": package.sha256,
        "copy_path": str(paths["input_readonly.docx"]),
        "out_dir": str(out.resolve()),
        "result_json": str(paths["word_raw.json"]),
        "accepted_text_path": str(paths["word_accepted.txt"]),
        "rejected_text_path": str(paths["word_rejected.txt"]),
        "pid_file": str(paths["word_pid.txt"]),
        "progress_log": str(paths["progress.log"]),
        "expected_accept_path": str(paths["expected_accept.txt"]),
        "expected_reject_path": str(paths["expected_reject.txt"]),
        "probes": probes,
        "normalize": {"whitespace": WS_PATTERN, "remove": REMOVE_PATTERN, "hyphen": HYPHEN_PATTERN,
                      "translate": {chr(code): value for code, value in SYMBOL_PUA.items()}},
        "exit_wait_seconds": exit_wait,
        "revision_scan_limit": 20000,
        "timeout_seconds": timeout,
    }
    with open(fs(paths["word_qa_input.json"]), "x", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    data["_views"] = views
    data["_expected_inline"] = {mode: inline_picture_count(package, mode) for mode in ("accept", "reject")}
    return data


def _check(checks: list, name: str, status: str, detail: str):
    checks.append({"name": name, "status": status, "detail": detail})


def evaluate_word_qa(raw: dict | None, *, views: dict, word_texts: dict, probes: list[str],
                     expected_inline: dict | None = None, hashes: dict | None = None,
                     process: dict | None = None, timed_out: bool = False) -> tuple[list, dict]:
    """Checks (name, PASS/FAIL/WARN, detail) and text comparisons from Word's raw results."""
    checks: list = []
    comparisons: dict = {}
    if timed_out:
        _check(checks, "run_completed", "FAIL", "Word QA timed out (a hidden dialog or add-in may be waiting)")
    elif raw is None:
        _check(checks, "run_completed", "FAIL", "the script wrote no result (see powershell_stderr.txt)")
    elif not raw.get("success"):
        _check(checks, "run_completed", "FAIL", f"stage {raw.get('stage')}: {raw.get('error')}")
    else:
        _check(checks, "run_completed", "PASS", f"Word {raw.get('word_version')} build {raw.get('word_build')}")
    for name, (before, after) in (hashes or {}).items():
        _check(checks, name, "PASS" if before == after else "FAIL",
               "unchanged" if before == after else f"hash changed: {before[:12]} -> {(after or 'missing')[:12]}")
    if raw is None or not raw.get("success"):
        _process_check(checks, process)
        return checks, comparisons
    window = raw.get("window_pid")
    own = raw.get("own_word_pid")
    isolated = own and (window in (None, 0, own))
    _check(checks, "isolated_instance", "PASS" if isolated else "FAIL",
           f"own PID {own}, window PID {window}, new PIDs {raw.get('new_word_process_ids')}")
    _check(checks, "opened_read_only", "PASS" if raw.get("opened_read_only") else "FAIL",
           str(raw.get("opened_read_only")))
    for key in ("accepted_revisions_remaining", "rejected_revisions_remaining"):
        value = raw.get(key)
        _check(checks, key, "PASS" if value == 0 else "FAIL", str(value))
    total, undone = raw.get("revisions_total"), raw.get("after_undo_revisions")
    if raw.get("reopened_for_reject"):
        _check(checks, "undo_restored", "WARN", f"Undo restored {undone} of {total} revisions; "
               "the reject pass used a fresh read-only open")
    else:
        _check(checks, "undo_restored", "PASS" if undone == total else "WARN", f"{undone} of {total}")
    defects = {k: raw.get(k) or [] for k in ("heading_empty", "heading_trailing_whitespace", "heading_trailing_colon")}
    count = sum(len(v) for v in defects.values())
    detail = "; ".join(f"{k.replace('heading_', '')}: {', '.join(repr(t[:50]) for t in v)}"
                       for k, v in defects.items() if v)
    _check(checks, "heading_defects", "PASS" if count == 0 else "FAIL",
           f"{len(raw.get('accepted_headings') or [])} headings, no defects" if count == 0 else detail)
    hits = {item.get("probe"): item for item in raw.get("probe_hits") or []}
    missing = [p for p in probes if not (hits.get(p) or {}).get("hits")]
    repeated = [p for p in probes if (hits.get(p) or {}).get("hits", 0) > 1]
    if not probes:
        _check(checks, "probes", "PASS", "no probes (pass --manifest to check the edited paragraphs)")
    else:
        status = "FAIL" if missing else ("WARN" if repeated else "PASS")
        detail = f"{len(probes) - len(missing)} of {len(probes)} found"
        if missing:
            detail += "; missing: " + "; ".join(repr(p[:50]) for p in missing)
        if repeated:
            detail += "; found more than once: " + "; ".join(repr(p[:50]) for p in repeated)
        _check(checks, "probes", status, detail)
    for mode, key in (("accept", "accepted"), ("reject", "rejected")):
        text = word_texts.get(mode)
        if text is None:
            _check(checks, f"{key}_text", "FAIL", "Word's text was not written")
            continue
        comparison = compare_view_text(views[mode], text)
        comparisons[mode] = comparison
        status = {"identical": "PASS", "identical_except_field_results": "WARN"}.get(comparison["status"], "FAIL")
        detail = comparison["status"].replace("_", " ")
        if comparison["field_differences"]:
            detail += f" ({len(comparison['field_differences'])} field or object result(s) differ)"
        if comparison["hunks"]:
            detail += f" ({len(comparison['hunks'])} difference(s) listed)"
        _check(checks, f"{key}_text", status, detail)
    for mode, key in (("accept", "accepted"), ("reject", "rejected")):
        if expected_inline is not None and raw.get(f"{key}_inline_shapes") is not None:
            want, got = expected_inline[mode], raw.get(f"{key}_inline_shapes")
            _check(checks, f"{key}_inline_shapes", "PASS" if want == got else "WARN",
                   f"Word {got}, file {want}")
    _process_check(checks, process)
    return checks, comparisons


def _process_check(checks: list, process: dict | None):
    if not process or not process.get("pid"):
        if process and process.get("candidates"):
            _check(checks, "word_process_exited", "WARN",
                   "this run's Word PID is unknown; new WINWORD processes since the run started: "
                   f"{process['candidates']} (not stopped; check them before stopping any)")
        return
    pid = process["pid"]
    if process.get("exited"):
        _check(checks, "word_process_exited", "PASS", f"PID {pid} exited")
    elif (process.get("reap") or {}).get("action") == "stopped":
        _check(checks, "word_process_exited", "WARN", f"PID {pid} did not exit and was stopped (--reap-own-process)")
    else:
        reason = (process.get("reap") or {}).get("reason", "")
        _check(checks, "word_process_exited", "WARN",
               f"PID {pid} is still running{': ' + reason if reason else ''}; "
               "rerun with --reap-own-process or stop only that PID")


def _report_text(docx: Path, status: str, raw: dict | None, checks: list, comparisons: dict,
                 process: dict | None) -> str:
    lines = [f"Word QA: {status}  ({docx.name})"]
    if raw:
        by_type = ", ".join(f"{k} {v}" for k, v in sorted((raw.get("revisions_by_type") or {}).items()))
        authors = ", ".join(raw.get("revision_authors") or []) or "none"
        lines += [
            f"Word {raw.get('word_version')} build {raw.get('word_build')}; own PID {raw.get('own_word_pid')}",
            f"Tracked: {raw.get('revisions_total')} revisions ({by_type or 'none'}); authors: {authors}; "
            f"fields {raw.get('fields')}; inline shapes {raw.get('inline_shapes')}; "
            f"paragraphs {raw.get('paragraphs')}",
            f"Accepted: {raw.get('accepted_paragraphs')} paragraphs, {raw.get('accepted_revisions_remaining')} "
            f"revisions remaining, {raw.get('accepted_pages')} pages, {raw.get('accepted_words')} words, "
            f"{len(raw.get('accepted_headings') or [])} headings",
            f"Rejected: {raw.get('rejected_paragraphs')} paragraphs, {raw.get('rejected_revisions_remaining')} "
            "revisions remaining",
        ]
    lines.append("Checks:")
    lines += [f"  {c['status']:4} {c['name']}: {c['detail']}" for c in checks]
    for mode, comparison in comparisons.items():
        for item in comparison["field_differences"][:10]:
            lines.append(f"  {mode} field/object result: file '{item['expected'][:80]}' / Word '{item['word'][:80]}'")
        for hunk in comparison["hunks"][:10]:
            lines.append(f"  {mode} {hunk['op']} after '...{hunk['before'][-50:]}': "
                         f"file '{hunk['expected'][:120]}' / Word '{hunk['word'][:120]}'")
    if process and process.get("reap"):
        lines.append(f"Process: {process['reap']}")
    return "\n".join(lines) + "\n"


def cmd_word_qa(args) -> int:
    if not _is_windows():
        print("ERROR: word-qa drives Microsoft Word through COM and runs only on Windows. "
              "Use `msw.py proof` for a LibreOffice proof on this system.", file=sys.stderr)
        return 3
    docx = Path(args.docx)
    out = Path(args.out)
    if not _is_file(docx):
        return _fail(f"{docx} does not exist")
    if args.timeout <= 0 or args.exit_wait < 0:
        return _fail("--timeout must be positive and --exit-wait not negative")
    reason = _new_folder(out)
    if reason:
        return _fail(reason)
    if not WORD_QA_SCRIPT.is_file():
        return _fail(f"{WORD_QA_SCRIPT} is missing")
    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if powershell is None:
        return _fail("Windows PowerShell (powershell.exe) was not found")
    locks = lock_files(docx)
    if locks:
        print(f"WARNING: the document appears to be open in Word or LibreOffice ({locks}); QA reads a copy",
              file=sys.stderr)
    try:
        probes = collect_probes(args.manifest, args.probes)
        data = prepare_word_qa(docx, out, probes=probes, timeout=args.timeout, exit_wait=args.exit_wait)
    except (OSError, ValueError, PackageError, XMLError, ProofError) as error:
        return _fail(f"cannot prepare Word QA: {_os_message(error)}")
    source_sha, copy_sha = data["source_sha256"], data["source_sha256"]
    prior = set(_task_pids("WINWORD.EXE") or [])
    command = [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
               str(WORD_QA_SCRIPT), "-InputJson", str(out.resolve() / "word_qa_input.json")]
    print(f"Word QA: {docx.name} ({len(probes)} probe(s), timeout {args.timeout} s)")
    run_started = time.time()
    t0 = time.monotonic()
    timed_out = False
    with open(fs(out / "powershell_stdout.txt"), "xb") as stdout,             open(fs(out / "powershell_stderr.txt"), "xb") as stderr:
        proc = subprocess.Popen(command, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL)
        try:
            returncode = proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()            # our own PowerShell process only
            returncode = proc.wait(timeout=60)
    elapsed = round(time.monotonic() - t0, 1)

    raw = None
    raw_path = Path(data["result_json"])
    if _is_file(raw_path):
        try:
            raw = json.loads(_read_bytes(raw_path).decode("utf-8-sig"))
        except ValueError as error:
            print(f"WARNING: {raw_path} is not valid JSON: {error}", file=sys.stderr)
    own_pid = (raw or {}).get("own_word_pid")
    pid_file = Path(data["pid_file"])
    if not own_pid and _is_file(pid_file):
        first = _read_bytes(pid_file).decode("utf-8-sig", errors="replace").split()
        own_pid = int(first[0]) if first and first[0].isdigit() else None
    process: dict = {"pid": own_pid}
    if own_pid:
        process["exited"] = not own_word_alive(own_pid, run_started)
        if not process["exited"] and args.reap_own_process:
            process["reap"] = reap_own_word(own_pid, run_started)
            process["exited_after_reap"] = process["reap"]["action"] in ("stopped", "already_exited")
        elif not process["exited"]:
            process["reap"] = {"pid": own_pid, "action": "none",
                               "reason": "not stopped (pass --reap-own-process to stop it)"}
    else:
        after = set(_task_pids("WINWORD.EXE") or [])
        candidates = []
        for pid in sorted(after - prior):
            info = process_info(pid)
            if info and info["alive"] and (info["created"] or 0) >= run_started - 5:
                candidates.append(pid)
        process["candidates"] = candidates

    hashes = {"source_unchanged": (source_sha, sha256_file(docx) if _is_file(docx) else None),
              "copy_unchanged": (copy_sha, sha256_file(data["copy_path"]) if _is_file(data["copy_path"])
                                 else None)}
    word_texts = {}
    for mode, key in (("accept", "accepted_text_path"), ("reject", "rejected_text_path")):
        path = Path(data[key])
        if _is_file(path):
            word_texts[mode] = _read_bytes(path).decode("utf-8", errors="replace")
    checks, comparisons = evaluate_word_qa(raw, views=data["_views"], word_texts=word_texts, probes=probes,
                                           expected_inline=data["_expected_inline"], hashes=hashes,
                                           process=process, timed_out=timed_out)
    status = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS"
    record = {
        "schema": "msw-word-qa/1", "tool": {"name": "msw word-qa", "version": __version__},
        "status": status, "finished_utc": _utc(),
        "source": {"path": str(docx.resolve()), "sha256": source_sha},
        "command": command, "timeout_s": args.timeout, "timed_out": timed_out,
        "powershell_returncode": returncode, "elapsed_s": elapsed,
        "probes": probes, "checks": checks, "comparisons": comparisons, "process": process,
        "word": raw,
        "stderr_tail": _tail(out / "powershell_stderr.txt"),
    }
    write_json_exclusive(out / "word_qa.json", record)
    report = _report_text(docx, status, raw, checks, comparisons, process)
    _write_text_new(out / "word_qa_report.txt", report)
    print(report, end="")
    print(f"record: {out / 'word_qa.json'}")
    return 0 if status == "PASS" else 1


# -- registration --------------------------------------------------------------------------

def register(subparsers):
    p = subparsers.add_parser(
        "proof", help="render a view of a .docx to PDF with LibreOffice (persistent per-user profile) and page PNGs",
        description="Write the chosen view as a new .docx ('accepted view.docx', 'rejected view.docx' or "
                    "'as-is view.docx'), convert it to PDF with LibreOffice under a hard timeout on a persistent "
                    "per-user profile, rasterize pages (page01.png, ...) when pypdfium2 or PyMuPDF is installed, "
                    "and record everything in proof.json, including the source file's name; inside a project its "
                    "paths are relative to the project root. LibreOffice does not refresh EndNote fields.")
    p.add_argument("docx")
    p.add_argument("--out", required=True, help="new (or empty) folder; one proof per folder")
    p.add_argument("--view", choices=["accept", "reject", "as-is"], default="accept")
    p.add_argument("--name", help="file stem of the view .docx and PDF (default: 'accepted view', 'rejected view' "
                   "or 'as-is view')")
    p.add_argument("--soffice", help="LibreOffice executable (default: MSW_SOFFICE, PATH, standard install)")
    p.add_argument("--profile-dir", help="persistent LibreOffice profile folder (default, inside or outside a "
                   "project: LOCALAPPDATA/msw/lo_profile on Windows, else XDG_CACHE_HOME/msw/lo_profile, else "
                   "~/.cache/msw/lo_profile)")
    p.add_argument("--timeout", type=int, default=600, help="seconds before the conversion is stopped")
    p.add_argument("--pages", help="pages to rasterize, e.g. 1,3,5-7 (default: 1 plus pages holding probes)")
    p.add_argument("--dpi", type=int, default=110)
    p.add_argument("--probes", nargs="+", default=[], metavar="TEXT", help="texts whose pages to find and render")
    p.add_argument("--manifest", help="build manifest: probes from its edited paragraphs (expected_accept)")
    p.add_argument("--rasterizer", choices=["auto", "pypdfium2", "pymupdf", "none"], default="auto")
    p.set_defaults(func=cmd_proof)

    p = subparsers.add_parser(
        "word-qa", help="native Word accept/reject checks in an isolated hidden instance (Windows)",
        description="Run scripts/word_qa.ps1 on a read-only copy in a new hidden Word instance with macros "
                    "disabled, then assert: no revisions remain after Accept All or Reject All, no heading "
                    "defects, every probe found, and Word's accepted and rejected texts equal the file views.")
    p.add_argument("docx")
    p.add_argument("--out", required=True, help="new (or empty) folder")
    p.add_argument("--manifest", help="build manifest: probes from its edited paragraphs (expected_accept)")
    p.add_argument("--probes", nargs="+", default=[], metavar="TEXT")
    p.add_argument("--timeout", type=int, default=900, help="seconds before the Word run is abandoned")
    p.add_argument("--exit-wait", type=int, default=30, help="seconds to wait for Word to exit after Quit")
    p.add_argument("--reap-own-process", action="store_true",
                   help="stop this run's own Word process if it is still alive after the wait or a timeout")
    p.set_defaults(func=cmd_word_qa)

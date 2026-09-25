"""Tests for the LibreOffice proof and native Word QA commands (mswlib/proof.py).

LibreOffice tests run only when soffice is installed (set MSW_SOFFICE to point at it);
the Word test runs only on Windows with MSW_WORD_TESTS=1.
"""

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "scientific-manuscript" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import msw_fixtures  # noqa: E402
from mswlib import proof  # noqa: E402
from mswlib.docx import Package  # noqa: E402
from mswlib.edit import build  # noqa: E402
from mswlib.views import view_texts  # noqa: E402

SOFFICE = proof.find_soffice()
POWERSHELL = shutil.which("powershell") if os.name == "nt" else None
SPEC = {"edits": [
    {"id": "E1", "op": "replace", "para": "10000003", "old": "compared", "new": "contrasted"},
    {"id": "E2", "op": "replace", "para": "10000008", "old": "ends here", "new": "closes the sentence"},
]}


def run_command(argv):
    """Run one proof/word-qa command through its own parser; returns (code, stdout, stderr)."""
    parser = argparse.ArgumentParser(prog="msw")
    proof.register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = args.func(args)
    return code, out.getvalue(), err.getvalue()


class TemporaryFolderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="msw proof ", ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def fixture(self, name="fixture.docx", blob=None) -> Path:
        path = self.tmp / name
        path.write_bytes(blob if blob is not None else msw_fixtures.make_docx())
        return path

    def built(self):
        result = build(self.fixture("source.docx"), SPEC, author="Test Author", date="2026-01-02T00:00:00Z")
        path = self.tmp / "built.docx"
        path.write_bytes(result.blob)
        manifest = self.tmp / "built.manifest.json"
        manifest.write_text(json.dumps(result.manifest), encoding="utf-8")
        return path, manifest


class NormalizationTests(unittest.TestCase):
    def test_word_and_file_marks_normalize_alike(self):
        word = "Title\rLine\x0bbreak\tand\xa0space\x07\r\x01\x02 non\x1ebreaking soft\x1fhyphen \r"
        file = "Title Line break and space non\u2011breaking soft\u00adhyphen\ufffc"
        self.assertEqual(proof.normalize(word), proof.normalize(file))

    def test_marked_texts_match_views_except_pictures(self):
        package = Package(msw_fixtures.make_docx())
        for mode in ("accept", "reject"):
            views = proof.expected_views(package)[mode]
            plain = view_texts(package.document, mode)
            self.assertEqual(len(views), len(plain))
            for marked, text in zip(views, plain):
                self.assertEqual(proof.strip_marks(marked), text.replace("\ufffc", "/"))

    def test_field_results_are_marked_across_paragraphs(self):
        package = Package(msw_fixtures.make_docx())
        accept = proof.expected_views(package)["accept"]
        citation = next(t for t in accept if t.startswith("Marker levels"))
        self.assertIn(proof.FIELD_OPEN + "(1)" + proof.FIELD_CLOSE, citation)
        bibliography = [t for t in accept if "fixture paper" in t]
        self.assertEqual(len(bibliography), 3)
        for text in bibliography:
            self.assertTrue(text.startswith(proof.FIELD_OPEN) and text.endswith(proof.FIELD_CLOSE), text)
        picture = next(t for t in accept if "/" in t and "kg" not in t)
        self.assertEqual(picture, proof.FIELD_OPEN + "/" + proof.FIELD_CLOSE)

    @unittest.skipUnless(POWERSHELL, "Windows PowerShell is needed to compare with .NET regular expressions")
    def test_patterns_behave_identically_in_dotnet(self):
        sample = "A\r\x0b B\xa0\u2009C\x1e\u2011D\x01\x07\ufffc  E\u3000F"
        script = ("$i = [Console]::In.ReadToEnd() | ConvertFrom-Json; "
                  "$t = [regex]::new($i.remove).Replace($i.text, ''); "
                  "$t = [regex]::new($i.hyphen).Replace($t, '-'); "
                  "$t = [regex]::new($i.ws).Replace($t, ' ').Trim(' '); "
                  "[Console]::Out.Write(($t | ConvertTo-Json))")
        payload = json.dumps({"text": sample, "remove": proof.REMOVE_PATTERN, "hyphen": proof.HYPHEN_PATTERN,
                              "ws": proof.WS_PATTERN}, ensure_ascii=True)
        run = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script], input=payload,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), proof.normalize(sample))


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.accept = proof.expected_views(Package(msw_fixtures.make_docx()))["accept"]
        self.word = "\r".join(proof.strip_marks(t) for t in self.accept) + "\r"

    def test_identical(self):
        self.assertEqual(proof.compare_view_text(self.accept, self.word)["status"], "identical")

    def test_field_result_difference_is_reported_not_failed(self):
        result = proof.compare_view_text(self.accept, self.word.replace("training (1)", "training [1]"))
        self.assertEqual(result["status"], "identical_except_field_results")
        self.assertEqual(result["field_differences"], [{"expected": "(1)", "word": "[1]"}])

    def test_text_difference_is_listed(self):
        result = proof.compare_view_text(self.accept, self.word.replace("fell later", "rose later"))
        self.assertEqual(result["status"], "different")
        self.assertTrue(any("fell" in h["expected"] and "rose" in h["word"] for h in result["hunks"]))

    def test_missing_trailing_text_is_different(self):
        result = proof.compare_view_text(["Alpha beta gamma."], "Alpha beta")
        self.assertEqual(result["status"], "different")


class ProbeAndPageTests(unittest.TestCase):
    def test_probes_from_manifest(self):
        manifest = {"expected_accept": [
            {"text": "  A  long\u00a0edited paragraph " + "x" * 80, "deleted": False},
            {"text": "Removed paragraph text", "deleted": True},
            {"text": "\ufffc", "deleted": False},
            {"text": "short", "deleted": False},
        ]}
        probes = proof.probes_from_manifest(manifest)
        self.assertEqual(len(probes), 1)
        self.assertTrue(probes[0].startswith("A long edited paragraph"))
        self.assertLessEqual(len(probes[0]), proof.PROBE_CHARS)

    def test_parse_pages(self):
        self.assertEqual(proof.parse_pages("1,3-5,3"), [1, 3, 4, 5])
        self.assertIsNone(proof.parse_pages(None))
        for bad in ("0", "a", "5-2"):
            with self.assertRaises(ValueError):
                proof.parse_pages(bad)

    def test_locate_probes_falls_back_to_prefix(self):
        pages = ["first page text", "the start of a long sentence that continues", "on the next page"]
        found = proof.locate_probes(pages, ["the start of a long sentence that continues on the next page",
                                            "absent words"])
        self.assertEqual(found[0]["pages"], [2])
        self.assertEqual(found[0]["match"], "prefix30")
        self.assertEqual(found[1]["pages"], [])

    def test_pdf_page_count_and_png(self):
        self.assertEqual(proof._pdf_page_count(b"<< /Type /Pages /Kids [3 0 R] /Count 7 >>"), 7)
        self.assertIsNone(proof._pdf_page_count(b"%PDF-1.7 compressed"))
        data = proof._png(2, 1, bytes([255, 0, 0, 0, 255, 0, 9]), 7, 3)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        idat = data.index(b"IDAT")
        size = int.from_bytes(data[idat - 4:idat], "big")
        self.assertEqual(zlib.decompress(data[idat + 4:idat + 4 + size]), b"\x00\xff\x00\x00\x00\xff\x00")


class ProofArgumentTests(TemporaryFolderTest):
    def test_argument_validation(self):
        docx = self.fixture()
        cases = [
            ([str(self.tmp / "missing.docx"), "--out", str(self.tmp / "a")], "does not exist"),
            ([str(docx), "--out", str(self.tmp / "b"), "--timeout", "0"], "--timeout"),
            ([str(docx), "--out", str(self.tmp / "c"), "--dpi", "5"], "--dpi"),
            ([str(docx), "--out", str(self.tmp / "d"), "--pages", "2-1"], "bad page range"),
            ([str(docx), "--out", str(self.tmp / "e"), "--name", "a/b"], "not a valid file name"),
            ([str(docx), "--out", str(self.tmp / "f"), "--soffice", str(self.tmp / "no-soffice.exe")],
             "LibreOffice was not found"),
        ]
        for argv, message in cases:
            code, _, err = run_command(["proof"] + argv)
            self.assertEqual(code, 2, argv)
            self.assertIn(message, err)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["fixture.docx"])

    def test_refuses_existing_outputs(self):
        docx = self.fixture()
        out = self.tmp / "out"
        out.mkdir()
        (out / "accepted view.pdf").write_bytes(b"earlier proof")
        code, _, err = run_command(["proof", str(docx), "--out", str(out)])
        self.assertEqual(code, 2)
        self.assertIn("not empty", err)
        self.assertEqual((out / "accepted view.pdf").read_bytes(), b"earlier proof")

    def test_default_profile_is_one_short_per_user_folder(self):
        local, cache = self.tmp / "local", self.tmp / "cache"
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local), "XDG_CACHE_HOME": str(cache)}):
            self.assertEqual(proof.default_profile(), local / "msw" / "lo_profile")
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(cache)}):
            os.environ.pop("LOCALAPPDATA", None)
            self.assertEqual(proof.default_profile(), cache / "msw" / "lo_profile")
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("LOCALAPPDATA", None)
            os.environ.pop("XDG_CACHE_HOME", None)
            self.assertEqual(proof.default_profile(), Path.home() / ".cache" / "msw" / "lo_profile")

    def test_help_names_the_per_user_profile(self):
        parser = argparse.ArgumentParser(prog="msw")
        sub = parser.add_subparsers(dest="command")
        proof.register(sub)
        text = sub.choices["proof"].format_help()
        self.assertIn("LOCALAPPDATA/msw/lo_profile", " ".join(text.split()))
        self.assertIn("~/.cache/msw/lo_profile", text)
        for stale in ("<out>/lo_profile", "render_profiles", "<source stem>"):
            self.assertNotIn(stale, text)

    def test_record_path_is_relative_inside_the_project(self):
        project = self.tmp / "project"
        inside = project / "90_Agent_Work" / "passes" / "V02_x" / "proofs" / "accepted view.pdf"
        self.assertEqual(proof.record_path(inside, project), "90_Agent_Work/passes/V02_x/proofs/accepted view.pdf")
        outside = self.tmp / "elsewhere" / "file.docx"
        self.assertEqual(proof.record_path(outside, project), os.path.abspath(outside))
        self.assertEqual(proof.record_path(outside, None), os.path.abspath(outside))
        root = os.path.abspath(project)
        output = f"convert {root}{os.sep}a{os.sep}v.docx -> {root}/a/v.pdf; profile {self.tmp}"
        self.assertEqual(proof.relative_text(output, project),
                         f"convert a{os.sep}v.docx -> a/v.pdf; profile {self.tmp}")
        self.assertEqual(proof.relative_text(output, None), output)
        extended = f"convert \\\\?\\{root}{os.sep}a{os.sep}v.docx"
        self.assertEqual(proof.relative_text(extended, project), f"convert a{os.sep}v.docx")

    def test_path_limit_errors_become_advice(self):
        docx = self.fixture()
        advice = "the path is 270 characters, over Windows' 260-character limit"
        error = FileNotFoundError(2, "No such file or directory", str(self.tmp / ("x" * 250)))
        with mock.patch.object(proof, "_makedirs", side_effect=error), \
                mock.patch.object(proof.pathutil, "explain", return_value=advice), \
                mock.patch.object(proof, "find_soffice", return_value=Path(sys.executable)):
            code, _, err = run_command(["proof", str(docx), "--out", str(self.tmp / "out"),
                                        "--profile-dir", str(self.tmp / "profile")])
        self.assertEqual(code, 2)
        self.assertIn(advice, err)
        self.assertNotIn("No such file", err)

    def test_profile_lock_handling(self):
        profile = self.tmp / "profile"
        profile.mkdir()
        self.assertIsNone(proof.profile_busy(profile))
        (profile / ".lock").write_text("lock", encoding="utf-8")
        with mock.patch.object(proof, "_soffice_running", return_value=False):
            self.assertIsNone(proof.profile_busy(profile))      # stale lock
        with mock.patch.object(proof, "_soffice_running", return_value=True):
            self.assertIn("--profile-dir", proof.profile_busy(profile))

    def test_view_changes(self):
        package = Package(msw_fixtures.make_docx())
        self.assertEqual(proof.view_changes(package, "as-is"), {})
        accepted = proof.view_changes(package, "accept")
        self.assertEqual(list(accepted), ["word/document.xml"])
        self.assertNotIn(b"w:delText", accepted["word/document.xml"])


FAKE_SOFFICE = r'''
import os, pathlib, sys
args = sys.argv[1:]
if "--version" in args:
    print("FakeOffice 1.0")
    sys.exit(0)
state = pathlib.Path(os.environ["MSW_FAKE_SOFFICE_STATE"])
calls = int(state.read_text()) if state.exists() else 0
state.write_text(str(calls + 1))
mode = os.environ.get("MSW_FAKE_SOFFICE_MODE", "")
if mode == "fail" or (mode == "fail-first" and calls == 0):
    sys.exit(5)
outdir = pathlib.Path(args[args.index("--outdir") + 1])
target = outdir / (pathlib.Path(args[-1]).stem + ".pdf")
target.write_bytes(bytes.fromhex(os.environ["MSW_FAKE_SOFFICE_PDF"]))
print(f"convert {args[-1]} as a Writer document -> {target} using filter : writer_pdf_Export")
'''


def minimal_pdf(text: bytes) -> bytes:
    content = b"BT /F1 8 Tf 10 50 Td (" + text + b") Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


class FakeLibreOfficeTests(TemporaryFolderTest):
    """The whole proof pipeline against a stand-in converter (runs everywhere, including CI)."""

    def fake(self, mode):
        script = self.tmp / "fake_soffice.py"
        script.write_text(FAKE_SOFFICE, encoding="utf-8")
        if os.name == "nt":
            launcher = self.tmp / "fake_soffice.bat"
            launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            launcher = self.tmp / "fake_soffice"
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            launcher.chmod(0o755)
        environment = {"MSW_FAKE_SOFFICE_STATE": str(self.tmp / "calls.txt"), "MSW_FAKE_SOFFICE_MODE": mode,
                       "MSW_FAKE_SOFFICE_PDF": minimal_pdf(b"Marker levels were contrasted").hex()}
        return launcher, mock.patch.dict(os.environ, environment)

    def test_first_failure_is_retried_once(self):
        built, manifest = self.built()
        launcher, environment = self.fake("fail-first")
        out = self.tmp / "proof"
        with environment:
            code, stdout, err = run_command(["proof", str(built), "--out", str(out), "--soffice", str(launcher),
                                             "--profile-dir", str(self.tmp / "profile"), "--manifest", str(manifest)])
        self.assertEqual(code, 0, stdout + err)
        self.assertIn("retrying once", err)
        record = json.loads((out / "proof.json").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "ok")
        self.assertEqual([a["returncode"] for a in record["attempts"]], [5, 0])
        self.assertEqual(record["libreoffice"]["version"], "FakeOffice 1.0")
        self.assertEqual(record["pdf"]["pages"], 1)
        self.assertTrue((out / "soffice_stderr_2.txt").is_file())
        self.assertEqual(record["view_docx"]["parts_projected"], ["word/document.xml"])
        if record["rasterizer"]:
            self.assertEqual([p["page"] for p in record["pages_rendered"]], [1])
        else:
            self.assertTrue(all(p["pages"] is None for p in record["probes"]))

    def test_persistent_failure_is_recorded(self):
        launcher, environment = self.fake("fail")
        out = self.tmp / "proof"
        with environment:
            code, _, err = run_command(["proof", str(self.fixture()), "--out", str(out), "--soffice", str(launcher),
                                        "--profile-dir", str(self.tmp / "profile"), "--view", "reject"])
        self.assertEqual(code, 1)
        self.assertIn("without writing the PDF", err)
        record = json.loads((out / "proof.json").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(len(record["attempts"]), 2)
        self.assertFalse((out / "rejected view.pdf").exists())
        self.assertTrue((out / "rejected view.docx").is_file())
        self.assertEqual(record["source"]["name"], "fixture.docx")

    def run_in_project(self, launcher, environment, *extra):
        """A proof of a released file into a pass folder of a synthetic project, default profile."""
        project = self.tmp / "project"
        (project / "01_Manuscripts" / "Versions" / "V01").mkdir(parents=True)
        (project / "manuscript.json").write_text("{}", encoding="utf-8")
        docx = project / "01_Manuscripts" / "Versions" / "V01" / "2026-01-02 AB EXJ V01 baseline.docx"
        docx.write_bytes(msw_fixtures.make_docx())
        out = project / "90_Agent_Work" / "passes" / "V02_x" / "proofs"
        local = self.tmp / "local"
        with environment, mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
            code, stdout, err = run_command(["proof", str(docx), "--out", str(out), "--soffice", str(launcher),
                                             *extra])
        return project, docx, out, local / "msw" / "lo_profile", code, stdout + err

    def test_short_names_and_project_relative_record(self):
        launcher, environment = self.fake("")
        project, docx, out, profile, code, output = self.run_in_project(launcher, environment)
        self.assertEqual(code, 0, output)
        names = sorted(p.name for p in out.iterdir())
        self.assertIn("accepted view.docx", names)
        self.assertIn("accepted view.pdf", names)
        self.assertIn("proof.json", names)
        self.assertTrue(all(len(n) <= 20 for n in names), names)
        self.assertFalse(any(p.name == "lo_profile" for p in project.rglob("*")), "no profile inside the project")
        self.assertTrue(profile.is_dir(), "the default per-user profile is created and kept")
        text = (out / "proof.json").read_text(encoding="utf-8")
        record = json.loads(text)
        self.assertEqual(record["paths"], "relative to the project root")
        self.assertEqual(record["source"]["name"], docx.name)
        self.assertEqual(record["source"]["path"], "01_Manuscripts/Versions/V01/" + docx.name)
        self.assertEqual(record["view_docx"]["path"], "90_Agent_Work/passes/V02_x/proofs/accepted view.docx")
        self.assertEqual(record["pdf"]["path"], "90_Agent_Work/passes/V02_x/proofs/accepted view.pdf")
        self.assertIn("90_Agent_Work/passes/V02_x/proofs", record["command"])
        self.assertTrue(record["libreoffice"]["profile_default"])
        self.assertEqual(record["libreoffice"]["profile_dir"], os.path.abspath(profile))
        self.assertIn("convert 90_Agent_Work", record["stdout_tail"])
        for item in record["pages_rendered"]:
            self.assertRegex(item["path"], r"^90_Agent_Work/passes/V02_x/proofs/page\d{2}\.png$")
        root = str(project.resolve())
        self.assertNotIn(json.dumps(root)[1:-1], text, "no absolute project paths in proof.json")

    def test_as_is_view_outside_a_project_records_absolute_paths(self):
        launcher, environment = self.fake("")
        docx = self.fixture()
        out = self.tmp / "loose proof"
        with environment:
            code, stdout, err = run_command(["proof", str(docx), "--out", str(out), "--soffice", str(launcher),
                                             "--profile-dir", str(self.tmp / "profile"), "--view", "as-is",
                                             "--rasterizer", "none"])
        self.assertEqual(code, 0, stdout + err)
        self.assertTrue((out / "as-is view.docx").is_file())
        record = json.loads((out / "proof.json").read_text(encoding="utf-8"))
        self.assertEqual(record["paths"], "absolute")
        self.assertEqual(record["source"]["path"], os.path.abspath(docx))
        self.assertEqual(record["pdf"]["path"], os.path.abspath(out / "as-is view.pdf"))
        self.assertFalse(record["libreoffice"]["profile_default"])


@unittest.skipUnless(SOFFICE, "LibreOffice (soffice) is not installed; set MSW_SOFFICE to run the proof check")
class LibreOfficeProofTests(TemporaryFolderTest):
    @classmethod
    def setUpClass(cls):
        cls._shared = tempfile.TemporaryDirectory(prefix="msw lo profile ", ignore_cleanup_errors=True)
        cls.profile = Path(cls._shared.name) / "profile"

    @classmethod
    def tearDownClass(cls):
        cls._shared.cleanup()

    def test_accept_view_proof(self):
        built, manifest = self.built()
        out = self.tmp / "proof"
        code, stdout, err = run_command(["proof", str(built), "--out", str(out), "--manifest", str(manifest),
                                         "--profile-dir", str(self.profile), "--timeout", "300"])
        self.assertEqual(code, 0, err + stdout)
        record = json.loads((out / "proof.json").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["view"], "accept")
        self.assertEqual(record["source"]["name"], "built.docx")
        pdf = out / "accepted view.pdf"
        self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
        self.assertEqual(record["pdf"]["sha256"], proof.sha256_file(pdf))
        self.assertEqual(record["pdf"]["pages"], 1)
        self.assertIn("--headless", record["command"])
        self.assertTrue(any(c.startswith("-env:UserInstallation=file:") for c in record["command"]))
        self.assertTrue(self.profile.is_dir(), "the persistent profile must remain")
        self.assertIn("NOTE: LibreOffice does not refresh EndNote fields", stdout)
        if record["rasterizer"]:
            self.assertEqual([p["page"] for p in record["pages_rendered"]], [1])
            self.assertTrue(all(item["pages"] == [1] for item in record["probes"]), record["probes"])
            self.assertTrue((out / record["pages_rendered"][0]["name"]).read_bytes().startswith(b"\x89PNG"))
        # the accepted view is what was rendered: no deleted text in the view document
        view = Package(out / "accepted view.docx")
        self.assertNotIn(b"w:del ", view.parts["word/document.xml"])
        # a second run into the same folder is refused and changes nothing
        before = sorted(p.name for p in out.iterdir())
        code, _, err = run_command(["proof", str(built), "--out", str(out), "--profile-dir", str(self.profile)])
        self.assertEqual(code, 2)
        self.assertEqual(sorted(p.name for p in out.iterdir()), before)

    def test_timeout_stops_the_conversion_and_is_recorded(self):
        out = self.tmp / "slow"
        code, _, err = run_command(["proof", str(self.fixture()), "--out", str(out), "--view", "as-is",
                                    "--profile-dir", str(self.tmp / "fresh profile"), "--timeout", "1"])
        record = json.loads((out / "proof.json").read_text(encoding="utf-8"))
        if record["status"] == "ok":
            self.skipTest("LibreOffice finished within one second; the timeout path was not exercised")
        self.assertEqual(code, 1)
        self.assertEqual(record["status"], "timeout")
        self.assertIn("did not finish", err)
        self.assertTrue((self.tmp / "fresh profile").is_dir())


class SymbolTextTests(unittest.TestCase):
    """Word reports Symbol-font text in the private-use area and w:sym as "(" (checked in Word 16)."""

    def test_word_forms_of_symbols_compare_equal(self):
        sym = '<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/>'
        fx = msw_fixtures
        extra = fx.para(fx.run("IL-1") + fx.run("b", sym) + fx.run(" and ")
                        + '<w:r><w:sym w:font="Symbol" w:char="F06D"/></w:r>' + fx.run("g"), "1000000F")
        doc = Package(fx.make_docx(extra_paragraphs=extra)).document
        texts = proof.marked_texts(doc)
        line = next(t for t in texts if t.startswith("IL-1"))
        self.assertEqual(line, "IL-1\u03b2 and (g")
        word = "IL-1\uf062 and (g\r"
        self.assertEqual(proof.compare_view_text([line], word)["status"], "identical")
        self.assertEqual(proof.normalize("5 \uf06dg"), "5 \u03bcg")


class WordQaPreparationTests(TemporaryFolderTest):
    def test_non_windows_exits_3(self):
        with mock.patch.object(proof, "_is_windows", return_value=False):
            code, _, err = run_command(["word-qa", str(self.fixture()), "--out", str(self.tmp / "qa")])
        self.assertEqual(code, 3)
        self.assertIn("only on Windows", err)
        self.assertFalse((self.tmp / "qa").exists())

    def test_argument_validation_and_refusal(self):
        docx = self.fixture()
        busy = self.tmp / "busy"
        busy.mkdir()
        (busy / "keep.txt").write_text("keep", encoding="utf-8")
        with mock.patch.object(proof, "_is_windows", return_value=True):
            for argv, message in (
                    ([str(self.tmp / "none.docx"), "--out", str(self.tmp / "q1")], "does not exist"),
                    ([str(docx), "--out", str(self.tmp / "q2"), "--timeout", "0"], "--timeout"),
                    ([str(docx), "--out", str(busy)], "not empty")):
                code, _, err = run_command(["word-qa"] + argv)
                self.assertEqual(code, 2, argv)
                self.assertIn(message, err)
        self.assertEqual((busy / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_input_file_construction(self):
        built, manifest = self.built()
        out = self.tmp / "qa"
        probes = proof.collect_probes(manifest, ["Extra  probe\u00a0text"])
        data = proof.prepare_word_qa(built, out, probes=probes, timeout=120)
        written = json.loads((out / "word_qa_input.json").read_text(encoding="ascii"))
        self.assertEqual(written["schema"], "msw-word-qa-input/1")
        self.assertEqual(written["probes"], probes)
        self.assertEqual(probes[:2], ["Marker levels were contrasted between groups GENE1 expressio",
                                      "Long group citation (3) closes the sentence."])
        self.assertEqual(probes[2], "Extra probe text")
        self.assertEqual(written["source_sha256"], Package(built).sha256)
        self.assertEqual(written["timeout_seconds"], 120)
        self.assertEqual(written["exit_wait_seconds"], 30)
        for key in ("copy_path", "result_json", "accepted_text_path", "rejected_text_path", "pid_file",
                    "progress_log", "expected_accept_path", "expected_reject_path"):
            self.assertTrue(Path(written[key]).is_absolute(), key)
            self.assertEqual(Path(written[key]).parent, out.resolve(), key)
        copy = Path(written["copy_path"])
        self.assertEqual(copy.read_bytes(), built.read_bytes())
        self.assertFalse(os.stat(copy).st_mode & stat.S_IWRITE, "the copy must be read-only")
        accepted = (out / "expected_accept.txt").read_text(encoding="utf-8").splitlines()
        rejected = (out / "expected_reject.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn("Marker levels were contrasted between groups GENE1 expression rose after training (1) "
                      "and fell later.", accepted)
        self.assertIn("Marker levels were compared between groups GENE1 expression rose after training (1) "
                      "and fell later.", rejected)
        self.assertEqual(data["_expected_inline"], {"accept": 1, "reject": 1})
        translate = written["normalize"].pop("translate")
        self.assertEqual(written["normalize"], {"whitespace": proof.WS_PATTERN, "remove": proof.REMOVE_PATTERN,
                                                "hyphen": proof.HYPHEN_PATTERN})
        self.assertEqual(translate["\uf062"], "\u03b2")   # Word's form of a Symbol-font "b"
        with self.assertRaises(FileExistsError):
            proof.prepare_word_qa(built, out, probes=probes, timeout=120)


def _raw(**overrides):
    raw = {"success": True, "stage": "done", "own_word_pid": 4242, "window_pid": 4242,
           "new_word_process_ids": [4242], "opened_read_only": True, "revisions_total": 5,
           "after_undo_revisions": 5, "reopened_for_reject": False, "accepted_revisions_remaining": 0,
           "rejected_revisions_remaining": 0, "accepted_headings": [{"style": "Heading 1", "text": "Abstract"}],
           "heading_empty": [], "heading_trailing_whitespace": [], "heading_trailing_colon": [],
           "probe_hits": [{"probe": "Marker levels", "hits": 1}], "accepted_inline_shapes": 1,
           "rejected_inline_shapes": 1, "word_version": "16.0", "word_build": "16.0.1"}
    raw.update(overrides)
    return raw


class WordQaEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.views = proof.expected_views(Package(msw_fixtures.make_docx()))
        self.texts = {mode: "\r".join(proof.strip_marks(t) for t in self.views[mode]) + "\r"
                      for mode in ("accept", "reject")}

    def evaluate(self, raw, texts=None, **options):
        checks, comparisons = proof.evaluate_word_qa(
            raw, views=self.views, word_texts=self.texts if texts is None else texts, probes=["Marker levels"],
            expected_inline={"accept": 1, "reject": 1}, **options)
        return {c["name"]: c["status"] for c in checks}, checks, comparisons

    def test_clean_run_passes(self):
        status, checks, _ = self.evaluate(_raw(), process={"pid": 4242, "exited": True},
                                          hashes={"source_unchanged": ("a", "a")})
        self.assertNotIn("FAIL", status.values(), checks)
        self.assertEqual(status["accepted_text"], "PASS")
        self.assertEqual(status["word_process_exited"], "PASS")

    def test_assertions_fail(self):
        raw = _raw(accepted_revisions_remaining=2, rejected_revisions_remaining=1,
                   heading_trailing_whitespace=["Methods "], heading_trailing_colon=["Results:"],
                   probe_hits=[{"probe": "Marker levels", "hits": 0}], window_pid=999)
        texts = dict(self.texts, accept=self.texts["accept"].replace("fell later", "fell much later"))
        status, _, comparisons = self.evaluate(raw, texts, hashes={"copy_unchanged": ("a", "b")})
        for name in ("accepted_revisions_remaining", "rejected_revisions_remaining", "heading_defects", "probes",
                     "accepted_text", "isolated_instance", "copy_unchanged"):
            self.assertEqual(status[name], "FAIL", name)
        self.assertEqual(status["rejected_text"], "PASS")
        self.assertEqual(comparisons["accept"]["status"], "different")

    def test_field_result_difference_warns(self):
        texts = dict(self.texts, accept=self.texts["accept"].replace("(2)", "(2, 3)"))
        status, _, _ = self.evaluate(_raw(), texts)
        self.assertEqual(status["accepted_text"], "WARN")

    def test_timeout_and_unknown_process(self):
        status, checks, _ = self.evaluate(None, {}, timed_out=True, process={"pid": None, "candidates": [77]})
        self.assertEqual(status["run_completed"], "FAIL")
        self.assertEqual(status["word_process_exited"], "WARN")
        self.assertIn("77", next(c["detail"] for c in checks if c["name"] == "word_process_exited"))

    def test_reaped_process_warns(self):
        status, _, _ = self.evaluate(_raw(), process={"pid": 4242, "exited": False,
                                                      "reap": {"pid": 4242, "action": "stopped"}})
        self.assertEqual(status["word_process_exited"], "WARN")

    def test_reap_refuses_foreign_processes(self):
        with mock.patch.object(proof, "process_info",
                               return_value={"pid": 5, "alive": True, "image": "C:/x/notepad.exe", "created": 1e10}):
            self.assertEqual(proof.reap_own_word(5, 0)["action"], "refused")
        with mock.patch.object(proof, "process_info",
                               return_value={"pid": 5, "alive": True, "image": "C:/x/WINWORD.EXE", "created": 10.0}):
            self.assertEqual(proof.reap_own_word(5, 1000.0)["action"], "refused")
        with mock.patch.object(proof, "process_info", return_value=None):
            self.assertEqual(proof.reap_own_word(5, 0)["action"], "already_exited")


@unittest.skipUnless(POWERSHELL, "Windows PowerShell is needed for the Word QA watchdog check")
class WordQaWatchdogTests(TemporaryFolderTest):
    def test_timeout_stops_only_the_script(self):
        slow = self.tmp / "slow.ps1"
        slow.write_text("param([string]$InputJson)\nStart-Sleep -Seconds 60\n", encoding="utf-8")
        out = self.tmp / "qa"
        with mock.patch.object(proof, "WORD_QA_SCRIPT", slow):
            code, stdout, _ = run_command(["word-qa", str(self.fixture()), "--out", str(out), "--timeout", "3",
                                           "--reap-own-process"])
        self.assertEqual(code, 1)
        record = json.loads((out / "word_qa.json").read_text(encoding="utf-8"))
        self.assertTrue(record["timed_out"])
        self.assertEqual(record["status"], "FAIL")
        self.assertIsNone(record["process"]["pid"])
        self.assertIn("timed out", (out / "word_qa_report.txt").read_text(encoding="utf-8"))


@unittest.skipUnless(os.name == "nt" and os.environ.get("MSW_WORD_TESTS") == "1",
                     "native Word QA runs only on Windows with MSW_WORD_TESTS=1")
class WordQaNativeTests(TemporaryFolderTest):
    def test_word_accepts_and_rejects_like_the_views(self):
        built, manifest = self.built()
        out = self.tmp / "qa"
        code, stdout, err = run_command(["word-qa", str(built), "--out", str(out), "--manifest", str(manifest),
                                         "--timeout", "170", "--reap-own-process"])
        record = json.loads((out / "word_qa.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 0, stdout + err)
        self.assertEqual(record["status"], "PASS")
        names = {c["name"]: c["status"] for c in record["checks"]}
        self.assertEqual(names["accepted_text"], "PASS")
        self.assertEqual(names["rejected_text"], "PASS")
        raw = json.loads((out / "word_raw.json").read_bytes().decode("utf-8"))   # no BOM
        self.assertEqual(raw["accepted_revisions_remaining"], 0)
        self.assertEqual(sorted(raw["revision_authors"]), ["Co-author", "Test Author"])
        self.assertEqual(raw["accepted_inline_shapes"], 1)

    def test_heading_defects_fail(self):
        extra = (msw_fixtures.para(msw_fixtures.run("Methods\u00a0"), "20000001", "Heading1")
                 + msw_fixtures.para(msw_fixtures.run("Results:"), "20000002", "Heading1"))
        docx = self.fixture("headings.docx", msw_fixtures.make_docx(extra_paragraphs=extra))
        out = self.tmp / "qa"
        code, stdout, err = run_command(["word-qa", str(docx), "--out", str(out), "--timeout", "170",
                                         "--reap-own-process"])
        self.assertEqual(code, 1, stdout + err)
        raw = json.loads((out / "word_raw.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["heading_trailing_whitespace"], ["Methods\u00a0"])
        self.assertEqual(raw["heading_trailing_colon"], ["Results:"])
        record = json.loads((out / "word_qa.json").read_text(encoding="utf-8"))
        names = {c["name"]: c["status"] for c in record["checks"]}
        self.assertEqual(names["heading_defects"], "FAIL")
        self.assertEqual(names["accepted_text"], "PASS")


if __name__ == "__main__":
    unittest.main()

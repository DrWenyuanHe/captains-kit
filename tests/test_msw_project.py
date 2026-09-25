"""Project lifecycle commands of the scientific-manuscript skill (init, intake, pass, release, ...).

Everything runs in temporary folders on synthetic Word files from msw_fixtures. No Word, no
LibreOffice and no network are needed; the git test is skipped when git is not installed.
"""

import contextlib
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "scientific-manuscript" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import msw_fixtures  # noqa: E402
from mswlib import edit as msw_edit  # noqa: E402
from mswlib import pathutil  # noqa: E402
from mswlib import project as proj  # noqa: E402
from mswlib.cli import main as msw_main  # noqa: E402

AUTHOR = "Given Surname"
PACKAGE = proj.PACKAGE
TOOL_OWNED = {proj.PROJECT_INDEX, proj.VERSION_INDEX, proj.VERSION_INDEX_MD, proj.PACKAGE_MANIFEST, proj.README,
              proj.STATUS, proj.LEDGER}
KNOWN_KEYS = ("title", "venue", "kind_label", "author_name", "initials", "revision_name", "spelling", "date", "msw",
              "msw_note", "current_block", "status_block", "version", "slug", "build_name", "release_name",
              "source_label", "source_path", "source_sha_short", "author")


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = msw_main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(root: Path) -> dict:
    """Every project file and its hash, except msw's lock file (kept, never deleted, but rewritten
    with each holder's PID; it is transient state, not project content)."""
    files = {}
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            path = Path(base) / name
            rel = path.relative_to(root).as_posix()
            if rel != proj.MUTATION_LOCK:
                files[rel] = sha(path)
    return files


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ledger(root):
    return [json.loads(line) for line in (root / proj.LEDGER).read_text(encoding="utf-8").splitlines() if line]


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "Example project"
        self.draft = self.base / "received" / "draft from the author.docx"
        self.draft.parent.mkdir()
        self.draft.write_bytes(msw_fixtures.make_docx())
        self.today = proj.today()

    def tearDown(self):
        self._tmp.cleanup()

    # helpers -------------------------------------------------------------------------
    def init(self, *extra, expect=0):
        code, out, err = cli("init", self.root, "--title", "Plasma marker Q-17 study", "--venue", "TST",
                             "--author", AUTHOR, "--initials", "GS", *extra)
        self.assertEqual(code, expect, out + err)
        return out, err

    def run_ok(self, *argv):
        code, out, err = cli(*argv, "--project", self.root)
        self.assertEqual(code, 0, out + err)
        return out

    def run_refused(self, *argv):
        code, out, err = cli(*argv, "--project", self.root)
        self.assertEqual(code, 2, out + err)
        return out + err

    def intake_v01(self):
        self.run_ok("intake", self.draft, "--as-version", "V01")
        return read_json(self.root / proj.PROJECT_INDEX)["current"]

    def assert_nothing_lost(self, before: dict, after: dict, owned=()):
        """Every file that existed still exists with the same bytes, or its bytes live on under
        another path (it was moved; a package slot may then be refilled from Versions/). Only
        tool-owned indexes and generated docs (TOOL_OWNED plus `owned`) change in place."""
        owned = TOOL_OWNED | set(owned)
        for rel, digest in before.items():
            if rel in owned or after.get(rel) == digest:
                continue
            elsewhere = {value for path, value in after.items() if path != rel}
            self.assertIn(digest, elsewhere, f"{rel}: its bytes exist nowhere any more")
        self.assertGreaterEqual(len(after), len(before))

    def make_pass(self, slug, edits, proof_bytes):
        out = self.run_ok("pass", "new", "--slug", slug)
        label = re.search(r"Claimed (V\d+)", out).group(1)
        pass_dir = self.root / proj.PASSES / f"{label}_{slug.replace(' ', '_')}"
        info = read_json(pass_dir / "pass.json")
        (pass_dir / "edits.json").write_text(json.dumps({"edits": edits}), encoding="utf-8")
        build = pass_dir / "build01" / info["build_name"]
        code, bout, berr = cli("build", self.root / info["source"]["path"], pass_dir / "edits.json",
                               "--out", build, "--author", AUTHOR)
        self.assertEqual(code, 0, bout + berr)
        (pass_dir / "proofs" / f"{label} accepted view proof.pdf").write_bytes(proof_bytes)
        note = pass_dir / f"{label}_CHANGES.md"
        text = note.read_text(encoding="utf-8")
        note.write_text(text.split("\n", 1)[1] + "\n- Replaced one phrase (synthetic).\n", encoding="utf-8")
        return label, pass_dir, info, build

    def released_name(self, info) -> str:
        """The full name `release` gives the build: date, initials, venue, version, slug and state."""
        return f"{info['date']} GS TST {info['version']} {info['slug']} tracked verified.docx"

    @contextlib.contextmanager
    def swap_refused(self, rel: str):
        """os.replace onto the project's `rel` always fails (a reader holding it open), no retries."""
        real, target = os.replace, os.path.normcase(str(Path(self.root.name) / rel))

        def replace(source, dest, *args, **kwargs):
            if os.path.normcase(str(dest)).endswith(target):
                raise PermissionError(13, "Access is denied (simulated reader)", str(dest))
            return real(source, dest, *args, **kwargs)

        with mock.patch.object(proj.os, "replace", replace), mock.patch.object(proj, "REPLACE_RETRY_DELAYS", ()):
            yield

    def leftovers_outside_archive(self) -> list:
        return [p for p in self.root.rglob("*.new") if proj.ARCHIVE not in p.relative_to(self.root).as_posix()]


class TemplateAndNamingTests(unittest.TestCase):
    def test_render_keeps_unknown_braces(self):
        self.assertEqual(proj.render("{a} {{b}} {unknown} {x} }", {"a": 1}), "1 {b} {unknown} {x} }")
        self.assertEqual(proj.render("{a}", {"a": "{b}"}), "{b}")

    def test_file_name_pattern(self):
        config = {"venue": "TST", "author": {"file_initials": "GS"}}
        self.assertEqual(proj.file_name(config, date="2026-01-05", number=2, slug="methods text", state="tracked"),
                         "2026-01-05 GS TST V02 methods text tracked.docx")
        self.assertEqual(proj.file_name(config, date="2026-01-05", number=3, state="author accepted"),
                         "2026-01-05 GS TST V03 author accepted.docx")
        self.assertEqual(proj.file_name(config, date="2026-01-05", number=4, slug="a/b: c?"),
                         "2026-01-05 GS TST V04 a-b- c.docx")

    def test_parse_version(self):
        self.assertEqual(proj.parse_version("V02"), 2)
        self.assertEqual(proj.parse_version("v7"), 7)
        self.assertEqual(proj.parse_version("12"), 12)
        with self.assertRaises(proj.ProjectError):
            proj.parse_version("V0")
        with self.assertRaises(proj.ProjectError):
            proj.parse_version("latest")

    def test_templates_have_no_unfinished_markers(self):
        for path in proj.TEMPLATES.glob("*.tmpl"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"\b(TODO|TBD|FIXME|PLACEHOLDER)\b", path.name)

    def test_help_lists_project_commands(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / "msw.py"), "--help"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("init", "status", "intake", "pass", "release", "archive", "author-save", "verify-project",
                        "retarget"):
            self.assertIn(command, result.stdout)
        code, out, _ = cli_help("pass", "new")
        self.assertEqual(code, 0)
        self.assertIn("--slug", out)


def cli_help(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            msw_main([*argv, "--help"])
        except SystemExit as exit_:
            return exit_.code, out.getvalue(), ""
    return 1, out.getvalue(), ""


class InitTests(ProjectCase):
    def test_init_empty_folder_creates_tree_and_files(self):
        out, _ = self.init()
        for rel in proj.TREE:
            self.assertTrue((self.root / rel).is_dir(), rel)
        self.assertFalse((self.root / proj.RENDER_PROFILES).exists(), "proof uses a per-user profile now")
        self.assertIn(f"+ {proj.LEDGER}", out)
        config = read_json(self.root / "manuscript.json")
        self.assertEqual(config["schema"], "msw-project/1")
        self.assertEqual(config["author"], {"name": AUTHOR, "file_initials": "GS", "revision_name": AUTHOR})
        self.assertEqual(config["venue"], "TST")
        self.assertEqual(config["document_kind"], "journal_article")
        self.assertEqual(config["language"]["spelling"], "en-US")
        self.assertEqual(config["policy"], {"science_changes": "comments"})
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual(index["schema_version"], 2)
        self.assertIsNone(index["current"])
        self.assertEqual(index["package"], PACKAGE)
        self.assertEqual(read_json(self.root / proj.VERSION_INDEX), [])
        self.assertEqual(read_json(self.root / proj.FIGURE_INDEX), {"schema_version": 2, "figures": []})
        self.assertEqual(read_json(self.root / proj.PACKAGE_MANIFEST)["files"], [])
        events = ledger(self.root)
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["seq"], events[0]["event"]), (1, "init"))
        for rel in ("README.md", "AGENTS.md", "CLAUDE.md", proj.STATUS, proj.DECISIONS, ".gitignore",
                    ".gitattributes", proj.VERSION_INDEX_MD, proj.FIGURES_README):
            path = self.root / rel
            self.assertTrue(path.is_file(), rel)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\r\n", text, rel)
            for key in KNOWN_KEYS:
                self.assertNotIn("{" + key + "}", text, f"{rel} keeps an unrendered {{{key}}}")
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        for phrase in (AUTHOR, "Nothing in this folder is ever deleted", "99_Archive", "msw.py build",
                       "EndNote", "atoms", "table of contents", "page", "New versions only", "Release ritual",
                       "Independent review", "01_Manuscripts/Incoming/", "90_Agent_Work/passes/VNN_<slug>/",
                       "policy.science_changes", ".~lock.<name>#", "msw.py retarget", "--role author_saved"):
            self.assertIn(phrase, agents)
        # The git commands banned are exactly those of references/preferences.md.
        [ban] = [block for block in agents.split("\n- ") if block.startswith("Never run `git")]
        banned = set(re.findall(r"`(git [a-z]+(?: --hard)?)`", ban))
        self.assertEqual(banned, {"git clean", "git checkout", "git switch", "git restore", "git reset --hard",
                                  "git stash", "git rm"})
        self.assertIn("any form", ban)
        self.assertIn("`git branch -f`", ban)
        self.assertIn("@AGENTS.md", (self.root / "CLAUDE.md").read_text(encoding="utf-8"))
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertIn("<!-- msw:current:start -->", readme)
        self.assertIn("No manuscript is registered yet", readme)
        self.assertIn("<!-- msw:status:start -->", (self.root / proj.STATUS).read_text(encoding="utf-8"))
        self.assertTrue((self.root / ".gitattributes").read_text(encoding="utf-8").splitlines()[1] == "* -text")
        gitignore = (self.root / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("~$*", "90_Agent_Work/passes/*/build*/", "90_Agent_Work/passes/*/qa/",
                        "90_Agent_Work/passes/*/proofs_work/", "90_Agent_Work/render_profiles/",
                        "90_Agent_Work/passes/*/proofs/**/*.json", "**/soffice_*.txt"):
            self.assertIn(pattern, gitignore)
        self.assertIn("intake", out)
        # The skill lives outside the project here: no file in it names this machine's copy.
        tool = proj.MSW.as_posix()
        self.assertIn("`python <skill folder>/scripts/msw.py <command>`", agents)
        for rel in ("AGENTS.md", "README.md", proj.STATUS, "CLAUDE.md", proj.DECISIONS):
            text = (self.root / rel).read_text(encoding="utf-8")
            self.assertNotIn(tool, text, rel)
            self.assertNotIn(str(self.base), text, rel)
        # The no-overwrite rule exempts the files the workflow asks the agent to fill in.
        for phrase in ("Never save over manuscripts, data, figures or records", "edited in place", "`edits.json`",
                       "`VNN_CHANGES.md`", "`manuscript.json`", "DECISIONS.md", "journal profile"):
            self.assertIn(phrase, agents)
        self.assertNotIn("saving over an existing file", agents)
        self.assertIn("Open items: see the latest change note", (self.root / proj.STATUS).read_text(encoding="utf-8"))

    def test_init_refuses_non_empty_folder(self):
        self.root.mkdir()
        (self.root / "notes.txt").write_text("keep me", encoding="utf-8")
        before = snapshot(self.root)
        _, err = self.init(expect=2)
        self.assertIn("--adopt", err)
        self.assertEqual(snapshot(self.root), before)

    def test_a_folder_with_only_agent_and_editor_folders_counts_as_empty(self):
        # Install the skill into the manuscript folder first, then bootstrap there (README flow).
        installed = self.root / ".claude" / "skills" / "scientific-manuscript" / "scripts"
        installed.mkdir(parents=True)
        (installed.parent / "SKILL.md").write_text("synthetic skill\n", encoding="utf-8")
        for name in (".agents", ".codex", ".git", ".vscode", ".idea"):
            (self.root / name).mkdir()
        (self.root / ".vscode" / "settings.json").write_text("{}\n", encoding="utf-8")
        before = snapshot(self.root)
        with mock.patch.object(proj, "MSW", installed / "msw.py"):
            out, _ = self.init()
        self.assertIn("Created manuscript project", out)
        [event] = ledger(self.root)
        self.assertEqual((event["adopt"], event["note"]), (False, "project created"))
        after = snapshot(self.root)
        for rel, digest in before.items():
            self.assertEqual(after.get(rel), digest, rel)
        # The skill is inside the project, so AGENTS.md names it by its path relative to the project.
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("`python .claude/skills/scientific-manuscript/scripts/msw.py <command>`", agents)
        self.assertNotIn(str(self.root), agents)
        # A folder with any other content still needs --adopt; a file named like a host folder counts.
        other = self.base / "other"
        other.mkdir()
        (other / ".claude").write_text("not a folder\n", encoding="utf-8")
        code, _, err = cli("init", other, "--title", "T", "--venue", "TST", "--author", AUTHOR, "--initials", "GS")
        self.assertEqual(code, 2)
        self.assertIn("--adopt", err)

    def test_adopt_warns_about_git_files_without_the_manuscript_rules(self):
        self.root.mkdir()
        attributes = self.root / ".gitattributes"
        ignore = self.root / ".gitignore"
        attributes.write_text("*.docx binary\n", encoding="utf-8")
        ignore.write_text("*.tmp\nbuild/\n", encoding="utf-8")
        before = snapshot(self.root)
        out, _ = self.init("--adopt")
        self.assertEqual({rel: after for rel, after in snapshot(self.root).items() if rel in before}, before,
                         "existing git files are never rewritten")
        self.assertIn("WARNING: .gitattributes exists and was kept", out)
        self.assertIn("WARNING: .gitignore exists and was kept", out)
        for line in ("    * -text", "    *.docx filter=lfs diff=lfs merge=lfs -text", "    ~$*", "    .~lock.*#",
                     "    90_Agent_Work/passes/*/proofs/**/*.json"):
            self.assertIn(line + "\n", out + "\n")
        self.assertNotIn("    *.tmp\n", out, "a rule the file already has is not listed")

    def test_adopt_is_quiet_when_the_git_files_hold_the_rules(self):
        self.root.mkdir()
        (self.root / ".gitattributes").write_text(proj.template("gitattributes.tmpl", {}), encoding="utf-8")
        (self.root / ".gitignore").write_text(proj.template("gitignore.tmpl", {}) + "my_notes/\n", encoding="utf-8")
        out, _ = self.init("--adopt")
        self.assertNotIn("WARNING: .git", out)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_git_setup_checks_that_word_files_reach_lfs(self):
        if subprocess.run(["git", "lfs", "version"], capture_output=True).returncode != 0:
            self.skipTest("Git LFS is not installed")
        self.root.mkdir()
        (self.root / ".gitattributes").write_text("*.docx binary\n", encoding="utf-8")
        out, _ = self.init("--adopt", "--git")
        self.assertNotIn("Git LFS enabled", out)
        self.assertIn("does not send .docx files to LFS", out)
        self.assertIn("no `* -text` line", out)

    def test_science_change_policy_is_written_and_reported(self):
        self.init("--science-changes", "tracked_for_review")
        self.assertEqual(read_json(self.root / "manuscript.json")["policy"], {"science_changes": "tracked_for_review"})
        out = self.run_ok("status")
        self.assertIn("Science changes: tracked_for_review", out)
        config = read_json(self.root / "manuscript.json")
        config["policy"]["science_changes"] = "anything goes"
        (self.root / "manuscript.json").write_text(json.dumps(config), encoding="utf-8")
        report = proj.collect_status(proj.Project(self.root), git=False)
        self.assertTrue(any("policy.science_changes" in line for line in report["suggestions"]), report["suggestions"])
        del config["policy"]
        (self.root / "manuscript.json").write_text(json.dumps(config), encoding="utf-8")
        self.assertIn("Science changes: comments", self.run_ok("status"))
        self.assertEqual(cli_help("init")[0], 0)

    def test_init_keeps_the_journal_profile_relative_and_refuses_one_outside(self):
        outside = self.base / "guidelines" / "joe.profile.json"
        outside.parent.mkdir()
        outside.write_text('{"schema": "journal-profile/1"}\n', encoding="utf-8")
        for given in (outside, Path("..") / "elsewhere.profile.json"):
            code, out, err = cli("init", self.root, "--title", "T", "--venue", "TST", "--author", AUTHOR,
                                 "--initials", "GS", "--journal-profile", given)
            self.assertEqual(code, 2, out + err)
            self.assertIn("outside the project", err)
            self.assertIn("05_References/Journal_Guidelines/", err)
            self.assertFalse(self.root.exists(), "a refused init writes nothing")
        # An absolute path inside the project (the profile is written later) is kept relative to it.
        self.init("--journal-profile", self.root / "05_References" / "Journal_Guidelines" / "joe.profile.json")
        config_text = (self.root / "manuscript.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(config_text)["journal_profile"], "05_References/Journal_Guidelines/joe.profile.json")
        for marker in (str(self.base), self.base.as_posix(), json.dumps(str(self.base))[1:-1]):
            self.assertNotIn(marker, config_text)

    def test_init_needs_author_details(self):
        code, _, err = cli("init", self.root, "--title", "T", "--venue", "TST")
        self.assertEqual(code, 2)
        self.assertIn("--author", err)
        self.assertFalse(self.root.exists())

    def test_adopt_only_adds_missing_files(self):
        self.root.mkdir()
        (self.root / "README.md").write_text("# My own readme\n", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("# My own rules\n", encoding="utf-8")
        (self.root / "old draft.docx").write_bytes(msw_fixtures.make_docx())
        before = snapshot(self.root)
        self.init("--adopt")
        after = snapshot(self.root)
        for rel, digest in before.items():
            self.assertEqual(after.get(rel), digest, f"{rel} changed or moved")
        self.assertTrue((self.root / "manuscript.json").is_file())
        self.assertTrue((self.root / "06_Project_Docs/Workflows/AGENTS.msw.md").is_file())
        self.assertEqual(ledger(self.root)[0]["adopt"], True)

    def test_adopt_keeps_a_foreign_index_and_refuses_to_rewrite_it(self):
        self.root.mkdir()
        foreign = {"schema_version": 1, "current": "V29", "manuscript": "somewhere.docx"}
        (self.root / "PROJECT_INDEX.json").write_text(json.dumps(foreign), encoding="utf-8")
        self.init("--adopt")
        self.assertEqual(read_json(self.root / "PROJECT_INDEX.json"), foreign)
        code, out, err = cli("intake", self.draft, "--as-version", "V01", "--project", self.root)
        self.assertEqual(code, 2)
        self.assertIn("schema_version 2", err)
        code, out, _ = cli("status", "--project", self.root)
        self.assertEqual(code, 0, out)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_init_with_git(self):
        out, _ = self.init("--git")

        def config(key):
            result = subprocess.run(["git", "-C", str(self.root), "config", "--local", "--get", key],
                                    capture_output=True, text=True)
            return result.stdout.strip()

        self.assertTrue((self.root / ".git").is_dir())
        self.assertEqual(config("core.autocrlf"), "false")
        self.assertEqual(config("core.longpaths"), "true")
        self.assertEqual(config("gc.auto"), "0")
        lfs = subprocess.run(["git", "lfs", "version"], capture_output=True, text=True)
        if lfs.returncode == 0:
            self.assertEqual(config("filter.lfs.required"), "true")
            self.assertIn("Git LFS enabled", out)
            self.assertNotIn("does not send .docx files to LFS", out)
            self.assertNotIn("no `* -text` line", out)
        self.assertIn("commit -m", out)
        head = subprocess.run(["git", "-C", str(self.root), "rev-parse", "--verify", "HEAD"], capture_output=True)
        self.assertNotEqual(head.returncode, 0, "init must not commit")


class IntakeAndPassTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.init()

    def test_first_intake_becomes_current(self):
        before = snapshot(self.root)
        self.run_ok("intake", self.draft)          # the first file defaults to V01
        current = read_json(self.root / proj.PROJECT_INDEX)["current"]
        name = f"{self.today} GS TST V01 baseline.docx"
        self.assertEqual(current["version"], "V01")
        self.assertEqual(current["path"], f"{proj.VERSIONS}/V01/{name}")
        self.assertEqual(current["sha256"], sha(self.draft))
        self.assertTrue(self.draft.is_file(), "intake must copy, never move")
        self.assertEqual(sha(self.root / current["path"]), sha(self.draft))
        self.assertEqual(sha(self.root / PACKAGE / name), sha(self.draft))
        self.assertEqual(current["state"], "baseline")
        entries = read_json(self.root / proj.VERSION_INDEX)
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["role"], entries[0]["base_role"]), ("current", "released"))
        self.assertEqual((entries[0]["state"], entries[0]["slug"]), ("baseline", ""))
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertNotIn("package_files", index)
        manifest = read_json(self.root / proj.PACKAGE_MANIFEST)
        self.assertEqual((manifest["version"], [item["role"] for item in manifest["files"]]), ("V01", ["current"]))
        self.assertEqual(manifest["files"][0]["package_path"], name)
        self.assertEqual(manifest["files"][0]["size"], self.draft.stat().st_size)
        events = ledger(self.root)
        self.assertEqual([e["event"] for e in events], ["init", "intake", "intake"])
        self.assertEqual((events[1]["status"], events[2]["status"], events[2]["ref"]), ("pending", "complete", 2))
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertIn("**Current manuscript: V01** (baseline)", readme)
        self.assertIn("- Open items: see the latest change note", readme)
        self.assertNotIn("Open items: 0", readme)
        self.assertIn("| V01 |", (self.root / proj.VERSION_INDEX_MD).read_text(encoding="utf-8"))
        self.assertIn("| current | baseline |", (self.root / proj.VERSION_INDEX_MD).read_text(encoding="utf-8"))
        self.assertIn("Current: **V01** baseline", (self.root / proj.STATUS).read_text(encoding="utf-8"))
        self.assert_nothing_lost(before, snapshot(self.root))

    def test_intake_refuses_reused_version_and_handles_incoming(self):
        self.intake_v01()
        other = self.base / "received" / "second.docx"
        other.write_bytes(msw_fixtures.make_docx(multi_chunk=True))
        self.assertIn("already released", self.run_refused("intake", other, "--as-version", "V01"))
        self.assertIn("give --as-version", self.run_refused("intake", other))
        self.run_ok("intake", other, "--as-version", "V01", "--role", "incoming", "--slug", "coauthor comments")
        incoming = self.root / proj.INCOMING / f"{self.today} GS TST V01 coauthor comments received.docx"
        self.assertEqual(sha(incoming), sha(other))
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["current"]["sha256"], sha(self.draft))
        out = self.run_ok("intake", other, "--as-version", "V01", "--role", "incoming", "--slug", "coauthor comments")
        self.assertIn("Already registered", out)
        self.assertEqual(len(list((self.root / proj.INCOMING).iterdir())), 1)
        text_file = self.base / "received" / "notes.docx"
        text_file.write_text("not a zip", encoding="utf-8")
        self.assertIn("not a readable Word document", self.run_refused("intake", text_file, "--as-version", "V05"))
        # An older released file imported later becomes current only when asked to.
        legacy = self.base / "received" / "legacy.docx"
        legacy.write_bytes(msw_fixtures.make_docx(extra_paragraphs=msw_fixtures.para(
            msw_fixtures.run("Legacy paragraph."), "1000000F")))
        self.run_ok("intake", legacy, "--as-version", "V05", "--slug", "legacy import", "--state", "tracked verified",
                    "--set-current")
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual((index["current"]["version"], index["baseline"]["version"]), ("V05", "V01"))
        names = sorted(p.name for p in (self.root / PACKAGE).glob("*.docx"))
        self.assertEqual(names, sorted([f"{self.today} GS TST V05 legacy import tracked verified.docx",
                                        f"{self.today} GS TST V01 baseline.docx"]))
        self.assertIn("Claimed V06", self.run_ok("pass", "new", "--slug", "next"))

    def test_intake_stopped_part_way_through_the_swap_is_finished_on_rerun(self):
        # The first intake (released, current) stops after VERSION_INDEX already lists V01 as released.
        with self.swap_refused(proj.PROJECT_INDEX):
            self.assertIn("stays pending", self.run_refused("intake", self.draft, "--as-version", "V01"))
        self.assertEqual([e["version"] for e in read_json(self.root / proj.VERSION_INDEX)], ["V01"])
        self.assertIsNone(read_json(self.root / proj.PROJECT_INDEX)["current"])
        self.assertIn("Finished the interrupted intake", self.run_ok("intake", self.draft, "--as-version", "V01"))
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["current"]["version"], "V01")
        # A later released file made current stops the same way; the rerun is not refused as a reuse.
        legacy = self.base / "received" / "legacy.docx"
        legacy.write_bytes(msw_fixtures.make_docx(multi_chunk=True))
        command = ("intake", legacy, "--as-version", "V02", "--role", "released", "--set-current")
        with self.swap_refused(proj.PROJECT_INDEX):
            self.run_refused(*command)
        self.assertEqual({e["version"]: e["role"] for e in read_json(self.root / proj.VERSION_INDEX)},
                         {"V01": "released", "V02": "current"})
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["current"]["version"], "V01")
        before = snapshot(self.root)
        self.assertIn("never completed", self.run_ok(*command, "--dry-run"))
        self.assertEqual(snapshot(self.root), before)
        out = self.run_ok(*command)
        self.assertIn("Finished the interrupted intake", out)
        self.assert_nothing_lost(before, snapshot(self.root))
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual((index["current"]["sha256"], index["baseline"]["sha256"]), (sha(legacy), sha(self.draft)))
        self.assertEqual(self.leftovers_outside_archive(), [])
        self.assertEqual(proj.collect_status(proj.Project(self.root), git=False)["ledger_issues"], [])
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")
        # Once finished, the version-reuse guard applies again.
        self.assertIn("already released", self.run_refused(*command))

    def test_first_intake_without_a_version_is_finished_on_rerun(self):
        with self.swap_refused(proj.PROJECT_INDEX):
            self.run_refused("intake", self.draft)          # the first file defaults to V01
        self.assertEqual([e["version"] for e in read_json(self.root / proj.VERSION_INDEX)], ["V01"])
        self.assertIn("Finished the interrupted intake", self.run_ok("intake", self.draft))
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["current"]["version"], "V01")
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")

    def test_pass_new_claims_next_version_and_refuses_duplicates(self):
        self.assertIn("no current version", self.run_refused("pass", "new", "--slug", "too early"))
        current = self.intake_v01()
        out = self.run_ok("pass", "new", "--slug", "methods text")
        self.assertIn("Claimed V02", out)
        self.assertIn(" build ", out)
        pass_dir = self.root / proj.PASSES / "V02_methods_text"
        info = read_json(pass_dir / "pass.json")
        self.assertEqual(info["version"], "V02")
        self.assertEqual(info["source"]["sha256"], current["sha256"])
        self.assertEqual(info["author"], AUTHOR)
        self.assertEqual(info["build_name"], "V02 tracked.docx")
        pass_rel = f"{proj.PASSES}/V02_methods_text"
        self.assertIn(f'--out "{pass_rel}/build01/V02 tracked.docx"', out)
        self.assertIn(f'proof "{pass_rel}/build01/V02 tracked.docx" --out "{pass_rel}/proofs/p1"', out)
        self.assertIn(f'release plan --pass "{pass_rel}"', out)
        released = f"{self.today} GS TST V02 methods text tracked verified.docx"
        self.assertIn(f"released as: {proj.VERSIONS}/V02/{released}", out)
        self.assertEqual(read_json(pass_dir / "edits.json"), {"edits": []})
        note = (pass_dir / "V02_CHANGES.md").read_text(encoding="utf-8")
        self.assertIn(f"`{released}` (built as `V02 tracked.docx`)", note)
        self.assertIn("# V02: methods text", note)
        self.assertIn(current["sha256"][:12], note)
        for sub in ("build01", "qa", "proofs", "review"):
            self.assertTrue((pass_dir / sub).is_dir())
        self.assertTrue((self.root / proj.CLAIMS / "V02.json").is_file())
        claims = [e for e in ledger(self.root) if e["event"] == "claim"]
        self.assertEqual([(e["version"], e["slug"]) for e in claims], [("V02", "methods text")])
        self.assertIn("already claimed", self.run_refused("pass", "new", "--slug", "other", "--version", "V02"))
        self.assertFalse((self.root / proj.PASSES / "V02_other").exists())
        self.assertIn("only go up", self.run_refused("pass", "new", "--slug", "older", "--version", "V01"))
        self.assertIn("Claimed V03", self.run_ok("pass", "new", "--slug", "parallel session"))
        status = proj.collect_status(proj.Project(self.root), git=False)
        self.assertEqual([p["version"] for p in status["passes"]], ["V02", "V03"])
        self.assertTrue(all(p["source_is_current"] for p in status["passes"]))


class ReleaseTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.v01 = self.intake_v01()

    def test_full_release_then_second_release_archives_superseded_package_files(self):
        label, pass_dir, info, build = self.make_pass(
            "methods text", [{"id": "E1", "op": "replace", "para": "10000004", "old": "which is high",
                              "new": "which is elevated"}], b"%PDF-1.4\n% synthetic proof V02\n%%EOF\n")
        self.assertEqual(label, "V02")
        before = snapshot(self.root)
        ledger_before = (self.root / proj.LEDGER).read_bytes()
        out = self.run_ok("release", "plan", "--pass", pass_dir)
        self.assertIn("Plan only", out)
        self.assertIn(f"{proj.VERSIONS}/V02/", out)
        self.assertEqual(snapshot(self.root), before, "release plan must not change anything")

        out = self.run_ok("release", "apply", "--pass", pass_dir)
        self.assertIn("git -C", out)
        self.assertIn('commit -m "Release V02: methods text"', out)
        released_name = self.released_name(info)
        self.assertEqual(released_name, f"{self.today} GS TST V02 methods text tracked verified.docx")
        released = self.root / proj.VERSIONS / "V02" / released_name
        self.assertEqual(sha(released), sha(build))
        self.assertTrue((self.root / proj.VERSIONS / "V02" / "V02_CHANGES.md").is_file())
        proof_name = f"{Path(released_name).stem} V02 accepted view proof.pdf"
        self.assertTrue((self.root / proj.VERSIONS / "V02" / proof_name).is_file())
        package = self.root / PACKAGE
        self.assertEqual(sorted(p.name for p in package.glob("*.docx")),
                         sorted([released_name, Path(self.v01["path"]).name]))
        self.assertEqual(sha(package / released_name), sha(build))
        self.assertEqual(sha(package / "Change_Notes" / "V02_CHANGES.md"),
                         sha(pass_dir / "V02_CHANGES.md"))
        self.assertTrue((package / "Proofs" / proof_name).is_file())
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual((index["current"]["version"], index["current"]["sha256"]), ("V02", sha(build)))
        self.assertEqual(index["baseline"]["version"], "V01")
        self.assertEqual(sorted(i["role"] for i in read_json(self.root / proj.PACKAGE_MANIFEST)["files"]),
                         ["baseline", "change_note", "current", "proof"])
        entries = {e["version"]: e for e in read_json(self.root / proj.VERSION_INDEX)}
        self.assertEqual(entries["V01"]["role"], "released")
        self.assertEqual((entries["V02"]["role"], entries["V02"]["baseline"], entries["V02"]["gate"]),
                         ("current", "V01 baseline", "PASS"))
        self.assertEqual(entries["V02"]["revision_author"], AUTHOR)
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertIn("**Current manuscript: V02** (tracked verified)", readme)
        self.assertIn("Built on: V01", readme)
        self.assertIn("**V02** tracked verified", (self.root / proj.STATUS).read_text(encoding="utf-8"))
        self.assertIn("| V02 |", (self.root / proj.VERSION_INDEX_MD).read_text(encoding="utf-8"))
        self.assertTrue((self.root / proj.LEDGER).read_bytes().startswith(ledger_before), "ledger is append-only")
        releases = [e for e in ledger(self.root) if e["event"] == "release"]
        self.assertEqual([e["status"] for e in releases], ["pending", "complete"])
        self.assertEqual(releases[1]["ref"], releases[0]["seq"])
        self.assertEqual(releases[1]["output_sha256"], sha(build))
        self.assert_nothing_lost(before, snapshot(self.root))
        self.assertIn("already released", self.run_refused("release", "plan", "--pass", pass_dir))
        verification = proj.collect_verification(proj.Project(self.root))
        self.assertEqual(verification["status"], "PASS", verification)
        self.assertEqual(verification["unlisted_package_files"], [])
        status = proj.collect_status(proj.Project(self.root), git=False)
        self.assertEqual(status["current"]["check"], "ok")
        self.assertEqual(status["passes"], [])

        # A second release: V01 (two versions back), the V02 note and the V02 proof leave the package.
        label3, pass3, info3, build3 = self.make_pass(
            "legend wording", [{"id": "E1", "op": "replace", "para": "1000000A", "old": "Points are participants",
                                "new": "Points show participants"}], b"%PDF-1.4\n% synthetic proof V03\n%%EOF\n")
        self.assertEqual((label3, info3["source"]["version"]), ("V03", "V02"))
        before = snapshot(self.root)
        self.run_ok("release", "apply", "--pass", pass3)
        after = snapshot(self.root)
        self.assert_nothing_lost(before, after)
        archive = self.root / proj.ARCHIVE / self.today / "Package_Superseded_V03"
        v01_name = Path(self.v01["path"]).name
        self.assertEqual(sha(archive / v01_name), self.v01["sha256"])
        self.assertTrue((archive / "Change_Notes" / "V02_CHANGES.md").is_file())
        self.assertTrue((archive / "Proofs" / proof_name).is_file())
        self.assertFalse((package / v01_name).exists())
        released3 = self.released_name(info3)
        self.assertEqual(sorted(p.name for p in package.glob("*.docx")), sorted([released3, released_name]))
        self.assertEqual([p.name for p in (package / "Change_Notes").iterdir()], ["V03_CHANGES.md"])
        self.assertEqual([p.name for p in (package / "Proofs").iterdir()],
                         [f"{Path(self.released_name(info3)).stem} V03 accepted view proof.pdf"])
        moves = [e for e in ledger(self.root) if e["event"] == "move"]
        self.assertEqual(len(moves), 1)
        ops = {Path(op["old"]).name: op for op in moves[0]["ops"]}
        self.assertEqual(set(ops), {v01_name, "V02_CHANGES.md", proof_name})
        for op in ops.values():
            self.assertEqual(op["sha256_before"], op["sha256_after"])
            self.assertTrue(op["twin"].startswith(proj.VERSIONS + "/"), op)
            self.assertEqual(sha(self.root / op["twin"]), op["sha256_before"])
        self.assertEqual(ops[v01_name]["twin"], self.v01["path"])
        entries = {e["version"]: e["role"] for e in read_json(self.root / proj.VERSION_INDEX)}
        self.assertEqual(entries, {"V01": "released", "V02": "released", "V03": "current"})
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")

    def test_release_refuses_without_a_byte_identical_twin(self):
        label, pass_dir, _, _ = self.make_pass(
            "methods text", [{"id": "E1", "op": "replace", "para": "10000004", "old": "which is high",
                              "new": "which is elevated"}], b"%PDF-1.4\n% proof A\n%%EOF\n")
        self.run_ok("release", "apply", "--pass", pass_dir)
        _, pass3, _, _ = self.make_pass(
            "legend wording", [{"id": "E1", "op": "replace", "para": "1000000A", "old": "Points are participants",
                                "new": "Points show participants"}], b"%PDF-1.4\n% proof B\n%%EOF\n")
        # Simulate damage to the released V01 copy: the package copy may no longer leave.
        (self.root / self.v01["path"]).write_bytes(msw_fixtures.make_docx(multi_chunk=True))
        before = snapshot(self.root)
        message = self.run_refused("release", "apply", "--pass", pass3)
        self.assertIn("no byte-identical copy", message)
        self.assertEqual(snapshot(self.root), before)

    def test_release_refuses_stale_source_and_bad_manifest(self):
        _, pass_dir, info, build = self.make_pass(
            "methods text", [{"id": "E1", "op": "replace", "para": "10000004", "old": "which is high",
                              "new": "which is elevated"}], b"%PDF-1.4\n% proof\n%%EOF\n")
        manifest = build.with_name(build.stem + ".manifest.json")
        data = read_json(manifest)
        data["gate"]["status"] = "FAIL"
        forged = build.with_name(build.stem + " forged.manifest.json")
        forged.write_text(json.dumps(data), encoding="utf-8")
        self.assertIn("not PASS", self.run_refused("release", "plan", "--pass", pass_dir, "--manifest", forged))
        # The author saves in Word meanwhile: the pass source is no longer current.
        saved = self.base / "received" / "author save.docx"
        saved.write_bytes(msw_fixtures.make_docx(multi_chunk=True))
        self.run_ok("author-save", saved)
        message = self.run_refused("release", "plan", "--pass", pass_dir)
        self.assertIn("Start a new pass", message)

    def test_interrupted_release_is_recorded_and_resumes(self):
        _, pass_dir, info, build = self.make_pass(
            "methods text", [{"id": "E1", "op": "replace", "para": "10000004", "old": "which is high",
                              "new": "which is elevated"}], b"%PDF-1.4\n% proof\n%%EOF\n")
        before = snapshot(self.root)
        with mock.patch.object(proj, "render_docs", side_effect=OSError("disk went away (simulated)")):
            message = self.run_refused("release", "apply", "--pass", pass_dir)
        self.assertIn("stays pending", message)
        self.assert_nothing_lost(before, snapshot(self.root))
        self.assertEqual([p.name for p in self.root.rglob("*.new")], [], "no half-written index")
        released = self.root / proj.VERSIONS / "V02" / self.released_name(info)
        self.assertTrue(released.is_file(), "copies made before the failure stay")
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["current"]["version"], "V01",
                         "indexes are swapped only after the generated blocks are written")
        status = proj.collect_status(proj.Project(self.root), git=False)
        self.assertEqual([(i["event"], i["issue"]) for i in status["ledger_issues"]], [("release", "failed")])
        out = self.run_ok("release", "apply", "--pass", pass_dir)
        self.assertIn("Released V02", out)
        self.assertEqual(len(list((self.root / proj.VERSIONS / "V02").glob("*.docx"))), 1)
        self.assertEqual(proj.collect_status(proj.Project(self.root), git=False)["ledger_issues"], [])
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")

    def test_release_interrupted_after_the_index_swap_is_finished_on_rerun(self):
        _, pass_dir, _, build = self.make_pass(
            "methods text", [{"id": "E1", "op": "replace", "para": "10000004", "old": "which is high",
                              "new": "which is elevated"}], b"%PDF-1.4\n% proof\n%%EOF\n")
        original = proj.Project.ledger_append

        def crash_on_completion(project, event):
            if event.get("event") == "release" and event.get("status") == "complete":
                raise OSError("power cut (simulated)")
            return original(project, event)

        with mock.patch.object(proj.Project, "ledger_append", crash_on_completion):
            self.run_refused("release", "apply", "--pass", pass_dir)
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["current"]["sha256"], sha(build))
        issues = proj.collect_status(proj.Project(self.root), git=False)["ledger_issues"]
        self.assertEqual([(i["event"], i["issue"]) for i in issues], [("release", "interrupted")])
        self.assertIn("never completed", self.run_ok("release", "plan", "--pass", pass_dir))
        self.assertIn("Finished the interrupted release", self.run_ok("release", "apply", "--pass", pass_dir))
        self.assertEqual(proj.collect_status(proj.Project(self.root), git=False)["ledger_issues"], [])
        self.assertIn("already released", self.run_refused("release", "apply", "--pass", pass_dir))

    def test_release_refuses_when_word_has_the_package_open(self):
        _, pass_dir, _, _ = self.make_pass(
            "methods text", [{"id": "E1", "op": "replace", "para": "10000004", "old": "which is high",
                              "new": "which is elevated"}], b"%PDF-1.4\n% proof\n%%EOF\n")
        lock = self.root / PACKAGE / ("~$" + Path(self.v01["path"]).name[2:])
        lock.write_bytes(b"lock")
        self.assertIn("lock files", self.run_refused("release", "apply", "--pass", pass_dir))

    def test_release_refuses_when_libreoffice_has_the_package_open(self):
        _, pass_dir, _, _ = self.make_pass(
            "methods text", [{"id": "E1", "op": "replace", "para": "10000004", "old": "which is high",
                              "new": "which is elevated"}], b"%PDF-1.4\n% proof\n%%EOF\n")
        lock = self.root / PACKAGE / f".~lock.{Path(self.v01['path']).name}#"
        lock.write_bytes(b"lock")
        before = snapshot(self.root)
        self.assertIn("LibreOffice lock files", self.run_refused("release", "apply", "--pass", pass_dir))
        self.assertEqual(snapshot(self.root), before)

    EDIT = [{"id": "E1", "op": "replace", "para": "10000004", "old": "which is high", "new": "which is elevated"}]

    def swap_blocked(self, blocked: str, failures=None):
        """os.replace that fails with PermissionError (a reader holding the file open) for targets
        named `blocked`: always, or only the first `failures` times."""
        real = os.replace
        calls = {"n": 0}

        def replace(source, dest, *args, **kwargs):
            if str(dest).endswith(blocked) and (failures is None or calls["n"] < failures):
                calls["n"] += 1
                raise PermissionError(13, "Access is denied (simulated reader)", str(dest))
            return real(source, dest, *args, **kwargs)

        return mock.patch.object(proj.os, "replace", replace), calls

    def test_release_stopped_part_way_through_the_index_swap_is_finished_on_rerun(self):
        _, pass_dir, info, build = self.make_pass("methods text", self.EDIT, b"%PDF-1.4\n% proof\n%%EOF\n")
        patch, _ = self.swap_blocked(proj.PROJECT_INDEX)
        with patch, mock.patch.object(proj, "REPLACE_RETRY_DELAYS", ()):
            message = self.run_refused("release", "apply", "--pass", pass_dir)
        self.assertIn("stays pending", message)
        # VERSION_INDEX and PACKAGE_MANIFEST were swapped; PROJECT_INDEX was not.
        self.assertEqual({e["version"]: e["role"] for e in read_json(self.root / proj.VERSION_INDEX)},
                         {"V01": "released", "V02": "current"})
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["current"]["version"], "V01")
        self.assertIn(f"{proj.PROJECT_INDEX}.new", [p.name for p in self.root.glob("*.new")])
        failed = [e for e in ledger(self.root) if e["event"] == "release" and e["status"] == "failed"]
        self.assertEqual(failed[0]["error"], "Access is denied (simulated reader): PROJECT_INDEX.json")
        # The half-swapped state fails verify-project and blocks a new pass from forking off V01.
        report = proj.collect_verification(proj.Project(self.root))
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("VERSION_INDEX" in problem for problem in report["inconsistent"]), report)
        self.assertEqual([(i["event"], i["issue"]) for i in report["unfinished"]], [("release", "failed")])
        code, out, _ = cli("verify-project", "--project", self.root)
        self.assertEqual(code, 1)
        self.assertIn("INDEXES", out)
        self.assertIn("run the same command again", out)
        self.assertIn("disagree", self.run_refused("pass", "new", "--slug", "next pass"))
        status = proj.collect_status(proj.Project(self.root), git=False)
        self.assertTrue(status["inconsistent"])
        self.assertTrue(any("release V02 again" in line for line in status["suggestions"]), status["suggestions"])
        # The same command, run again, finishes the release from its pending ledger event.
        before = snapshot(self.root)
        out = self.run_ok("release", "plan", "--pass", pass_dir)
        self.assertIn("never completed", out)
        self.assertIn(f"{proj.PROJECT_INDEX}.new", out)
        self.assertEqual(snapshot(self.root), before, "release plan changes nothing")
        out = self.run_ok("release", "apply", "--pass", pass_dir)
        self.assertIn("Finished the interrupted release of V02", out)
        self.assertIn("Released V02", out)
        self.assert_nothing_lost(before, snapshot(self.root))
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual((index["current"]["version"], index["current"]["sha256"]), ("V02", sha(build)))
        self.assertEqual((index["baseline"]["version"], index["last_release"]["version"]), ("V01", "V02"))
        self.assertEqual({e["version"]: e["role"] for e in read_json(self.root / proj.VERSION_INDEX)},
                         {"V01": "released", "V02": "current"})
        leftovers = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*.new"))
        self.assertTrue(leftovers)
        for rel in leftovers:
            self.assertTrue(rel.startswith(f"{proj.ARCHIVE}/{self.today}/Interrupted_Run/"), rel)
        self.assertIn("**Current manuscript: V02**", (self.root / "README.md").read_text(encoding="utf-8"))
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")
        self.assertEqual(proj.collect_status(proj.Project(self.root), git=False)["ledger_issues"], [])
        self.assertIn("already released", self.run_refused("release", "apply", "--pass", pass_dir))
        self.assertIn("source: V02 tracked verified", self.run_ok("pass", "new", "--slug", "next pass"))

    def test_a_briefly_locked_index_is_retried(self):
        _, pass_dir, _, build = self.make_pass("methods text", self.EDIT, b"%PDF-1.4\n% proof\n%%EOF\n")
        patch, calls = self.swap_blocked(proj.PROJECT_INDEX, failures=2)
        with patch, mock.patch.object(proj, "REPLACE_RETRY_DELAYS", (0, 0, 0)):
            self.run_ok("release", "apply", "--pass", pass_dir)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["current"]["sha256"], sha(build))
        self.assertEqual(list(self.root.rglob("*.new")), [])
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")

    def build_into(self, pass_dir, info, folder, edits):
        (pass_dir / "edits.json").write_text(json.dumps({"edits": edits}), encoding="utf-8")
        build = pass_dir / folder / info["build_name"]
        code, out, err = cli("build", self.root / info["source"]["path"], pass_dir / "edits.json", "--out", build,
                             "--author", AUTHOR)
        self.assertEqual(code, 0, out + err)
        return build

    def proof_of(self, pass_dir, folder, build, text):
        """A proof folder as `msw.py proof` leaves it: the PDF and a proof.json naming its source."""
        out = pass_dir / "proofs" / folder
        out.mkdir(parents=True)
        pdf = out / "accepted view.pdf"
        pdf.write_bytes(f"%PDF-1.4\n% {text}\n%%EOF\n".encode())
        record = {"schema": "msw-proof/1", "paths": "relative to the project root",
                  "source": {"name": build.name, "path": build.relative_to(self.root).as_posix(), "sha256": sha(build)},
                  "pdf": {"name": pdf.name, "path": pdf.relative_to(self.root).as_posix(), "sha256": sha(pdf)}}
        (out / "proof.json").write_text(json.dumps(record), encoding="utf-8")
        return pdf

    def test_release_refuses_a_proof_of_another_build(self):
        self.run_ok("pass", "new", "--slug", "methods text")
        pass_dir = self.root / proj.PASSES / "V02_methods_text"
        info = read_json(pass_dir / "pass.json")
        note = pass_dir / "V02_CHANGES.md"
        note.write_text(note.read_text(encoding="utf-8").split("\n", 1)[1], encoding="utf-8")
        build1 = self.build_into(pass_dir, info, "build01", [dict(self.EDIT[0], new="which is WRONGLY elevated")])
        stale = self.proof_of(pass_dir, "p1", build1, "proof of build01")
        build2 = self.build_into(pass_dir, info, "build02", self.EDIT)
        message = self.run_refused("release", "plan", "--pass", pass_dir)
        self.assertIn("was rendered from", message)
        self.assertIn("render a proof of this build", message)
        self.assertIn("was rendered from", self.run_refused("release", "plan", "--pass", pass_dir, "--proof", stale))
        # A proof of build02 in p2: with two PDFs, release takes the one rendered from the build.
        fresh = self.proof_of(pass_dir, "p2", build2, "proof of build02")
        out = self.run_ok("release", "plan", "--pass", pass_dir)
        self.assertIn("proofs/p2/accepted view.pdf", out)
        self.assertNotIn("proofs/p1/", out)
        self.run_ok("release", "apply", "--pass", pass_dir)
        entry = {e["version"]: e for e in read_json(self.root / proj.VERSION_INDEX)}["V02"]
        self.assertEqual((entry["sha256"], entry["proof_sha256"]), (sha(build2), sha(fresh)))

    def test_release_warns_when_no_proof_record_describes_the_pdf(self):
        _, pass_dir, _, _ = self.make_pass("methods text", self.EDIT, b"%PDF-1.4\n% synthetic proof\n%%EOF\n")
        out = self.run_ok("release", "plan", "--pass", pass_dir)
        self.assertIn("WARNING: no proof.json beside", out)

    def test_author_save_refuses_the_package_baseline_copy(self):
        _, pass_dir, _, build = self.make_pass("methods text", self.EDIT, b"%PDF-1.4\n% proof V02\n%%EOF\n")
        self.run_ok("release", "apply", "--pass", pass_dir)
        # The author opens the old baseline copy by mistake and saves over it.
        baseline_copy = self.root / PACKAGE / Path(self.v01["path"]).name
        extra = msw_fixtures.para(msw_fixtures.run("Edited in the wrong file."), "1000000F")
        baseline_copy.write_bytes(msw_fixtures.make_docx(extra_paragraphs=extra))
        rel = baseline_copy.relative_to(self.root).as_posix()
        status = proj.collect_status(proj.Project(self.root), git=False)
        self.assertFalse(any(f'author-save "{rel}"' in line for line in status["suggestions"]), status["suggestions"])
        self.assertTrue(any("--role incoming --as-version V01" in line for line in status["suggestions"]))
        index_before = (self.root / proj.PROJECT_INDEX).read_bytes()
        before = snapshot(self.root)
        message = self.run_refused("author-save", baseline_copy)
        self.assertIn("baseline copy (V01), not the current file", message)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual((self.root / proj.PROJECT_INDEX).read_bytes(), index_before)
        # A next release names intake --role incoming, not author-save, for the stray copy.
        out = self.run_ok("pass", "new", "--slug", "legend wording")
        self.assertIn("WARNING:", out)
        self.assertIn("baseline copy", out)
        pass3 = self.root / proj.PASSES / "V03_legend_wording"
        info3 = read_json(pass3 / "pass.json")
        self.build_into(pass3, info3, "build01", [{"id": "E1", "op": "replace", "para": "1000000A",
                                                  "old": "Points are participants", "new": "Points show participants"}])
        note = pass3 / "V03_CHANGES.md"
        note.write_text(note.read_text(encoding="utf-8").split("\n", 1)[1], encoding="utf-8")
        message = self.run_refused("release", "plan", "--pass", pass3)
        self.assertIn("--role incoming --as-version V01", message)
        self.assertNotIn("author-save", message)
        self.run_ok("intake", baseline_copy, "--role", "incoming", "--as-version", "V01")
        self.run_ok("release", "apply", "--pass", pass3)
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual((index["current"]["version"], index["baseline"]["sha256"]), ("V03", sha(build)))
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")


class AuthorSaveAndStatusTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.v01 = self.intake_v01()

    def test_status_detects_word_save_and_author_save_registers_it(self):
        package_copy = self.root / PACKAGE / Path(self.v01["path"]).name
        status = proj.collect_status(proj.Project(self.root), git=False)
        self.assertEqual(status["current"]["check"], "ok")
        self.assertEqual([item["check"] for item in status["package"]], ["ok"])
        # The author opens the package copy in Word and saves over it.
        extra = msw_fixtures.para(msw_fixtures.run("A sentence the author added in Word."), "1000000F")
        package_copy.write_bytes(msw_fixtures.make_docx(extra_paragraphs=extra))
        saved_sha = sha(package_copy)
        status = proj.collect_status(proj.Project(self.root), git=False)
        self.assertEqual([item["check"] for item in status["package"]], ["changed"])
        self.assertTrue(any("author-save" in line for line in status["suggestions"]))
        code, out, _ = cli("status", "--project", self.root)
        self.assertEqual(code, 0)
        self.assertIn("author-save", out)
        self.assertIn("changed after it was registered", self.run_refused("pass", "new", "--slug", "too soon"))

        before = snapshot(self.root)
        out = self.run_ok("author-save", package_copy, "--dry-run")
        self.assertIn("Dry run", out)
        self.assertEqual(snapshot(self.root), before)
        self.run_ok("author-save", package_copy)
        after = snapshot(self.root)
        self.assert_nothing_lost(before, after)
        saved_name = f"{self.today} GS TST V01 author saved.docx"   # the fixture still holds tracked changes
        registered = self.root / proj.VERSIONS / "V01" / saved_name
        self.assertEqual(sha(registered), saved_sha)
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual((index["current"]["path"], index["current"]["state"]),
                         (f"{proj.VERSIONS}/V01/{saved_name}", "author saved"))
        self.assertEqual(index["baseline"]["sha256"], self.v01["sha256"])
        archived = self.root / proj.ARCHIVE / self.today / "Author_Save_V01" / package_copy.name
        self.assertEqual(sha(archived), saved_sha)
        self.assertEqual(sha(package_copy), self.v01["sha256"], "the released V01 is back in the package")
        self.assertEqual(sha(self.root / PACKAGE / saved_name), saved_sha)
        events = ledger(self.root)
        saves = [e for e in events if e["event"] == "author_save"]
        self.assertEqual([e["status"] for e in saves], ["pending", "complete"])
        self.assertEqual(saves[0]["expected_sha256"], self.v01["sha256"])
        moves = [e for e in events if e["event"] == "move"]
        self.assertEqual(moves[0]["ops"][0]["twin"], f"{proj.VERSIONS}/V01/{saved_name}")
        status = proj.collect_status(proj.Project(self.root), git=False)
        self.assertEqual({item["check"] for item in status["package"]}, {"ok"})
        self.assertEqual(status["unregistered_word_files"], [])
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")
        # A second Word save on the same day: names and archive folders never collide.
        current_copy = self.root / PACKAGE / saved_name
        extra2 = msw_fixtures.para(msw_fixtures.run("A second sentence from a later save."), "10000010")
        current_copy.write_bytes(msw_fixtures.make_docx(extra_paragraphs=extra + extra2))
        second_sha = sha(current_copy)
        before = snapshot(self.root)
        self.run_ok("author-save", current_copy)
        self.assert_nothing_lost(before, snapshot(self.root))
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual(index["current"]["path"], f"{proj.VERSIONS}/V01/{self.today} GS TST V01 author saved 2.docx")
        self.assertEqual(index["baseline"]["sha256"], saved_sha)
        self.assertEqual(sha(self.root / proj.ARCHIVE / self.today / "Author_Save_V01_2" / package_copy.name),
                         self.v01["sha256"])
        self.assertEqual(sha(self.root / proj.ARCHIVE / self.today / "Author_Save_V01" / saved_name), second_sha)
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")
        out = self.run_ok("pass", "new", "--slug", "after the save")
        self.assertIn("V01 author saved", out)
        info = read_json(self.root / proj.PASSES / "V02_after_the_save" / "pass.json")
        self.assertEqual(info["source"]["sha256"], second_sha)

    def test_status_reports_locks_unregistered_files_and_uses_the_working_folder(self):
        lock = self.root / PACKAGE / "~$ example.docx"
        lock.write_bytes(b"lock")
        stray = self.root / PACKAGE / "copy saved by the author.docx"
        stray.write_bytes(msw_fixtures.make_docx(multi_chunk=True))
        previous = os.getcwd()
        os.chdir(self.root / "04_Analysis")
        try:
            report = proj.collect_status(proj.Project.locate(), git=False)
            code, out, _ = cli("status")
        finally:
            os.chdir(previous)
        self.assertEqual(code, 0)
        self.assertEqual(report["locks"], [f"{PACKAGE}/~$ example.docx"])
        self.assertEqual([item["path"] for item in report["unregistered_word_files"]],
                         [f"{PACKAGE}/copy saved by the author.docx"])
        self.assertIn("Word lock files:", out)

    def test_author_save_refuses_open_file(self):
        saved = self.base / "received" / "open in word.docx"
        saved.write_bytes(msw_fixtures.make_docx(multi_chunk=True))
        (saved.parent / "~$en in word.docx").write_bytes(b"lock")
        self.assertIn("open in Word", self.run_refused("author-save", saved))

    def test_author_save_stopped_part_way_through_the_swap_is_finished_on_rerun(self):
        package_copy = self.root / PACKAGE / Path(self.v01["path"]).name
        extra = msw_fixtures.para(msw_fixtures.run("A sentence the author added in Word."), "1000000F")
        package_copy.write_bytes(msw_fixtures.make_docx(extra_paragraphs=extra))
        saved_sha = sha(package_copy)
        real = os.replace

        def replace(source, dest, *args, **kwargs):
            if str(dest).endswith(proj.PROJECT_INDEX):
                raise PermissionError(13, "Access is denied (simulated reader)", str(dest))
            return real(source, dest, *args, **kwargs)

        with mock.patch.object(proj.os, "replace", replace), mock.patch.object(proj, "REPLACE_RETRY_DELAYS", ()):
            self.run_refused("author-save", package_copy)
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["current"]["sha256"], self.v01["sha256"])
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "FAIL")
        # The package copy was put back to the released V01 by the finished moves; the same command
        # still finishes the save that stopped.
        self.assertEqual(sha(package_copy), self.v01["sha256"])
        out = self.run_ok("author-save", package_copy)
        self.assertIn("Finished the interrupted author save", out)
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual((index["current"]["sha256"], index["baseline"]["sha256"]), (saved_sha, self.v01["sha256"]))
        self.assertEqual([p for p in self.root.rglob("*.new") if proj.ARCHIVE not in p.as_posix()], [])
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")
        self.assertIn("identical to the current file", self.run_ok("author-save", self.root / index["current"]["path"]))

    def test_author_save_stopped_after_moving_its_input_is_finished_on_rerun(self):
        package_copy = self.root / PACKAGE / Path(self.v01["path"]).name
        extra = msw_fixtures.para(msw_fixtures.run("A sentence the author added in Word."), "1000000F")
        package_copy.write_bytes(msw_fixtures.make_docx(extra_paragraphs=extra))
        saved_sha = sha(package_copy)
        real_copy = proj.copy_exclusive

        def copy_exclusive(source, dest):      # the disk fills before the baseline copy is restored
            if Path(dest).name == package_copy.name and Path(dest).parent.name == Path(PACKAGE).name:
                raise OSError(28, "No space left on device (simulated)", str(dest))
            return real_copy(source, dest)

        with mock.patch.object(proj, "copy_exclusive", copy_exclusive):
            self.assertIn("stays pending", self.run_refused("author-save", package_copy))
        self.assertFalse(package_copy.exists(), "its own move took the saved copy into the archive")
        archived = self.root / proj.ARCHIVE / self.today / "Author_Save_V01" / package_copy.name
        self.assertEqual(sha(archived), saved_sha)
        before = snapshot(self.root)
        out = self.run_ok("author-save", package_copy, "--dry-run")
        self.assertIn("stopped part-way", out)
        self.assertIn(f"-> {PACKAGE}/{package_copy.name}", out)
        self.assertEqual(snapshot(self.root), before)
        out = self.run_ok("author-save", package_copy)
        self.assertIn("Finished the interrupted author save", out)
        self.assert_nothing_lost(before, snapshot(self.root))
        self.assertEqual(sha(package_copy), self.v01["sha256"], "the released V01 is back in the package")
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual((index["current"]["sha256"], index["baseline"]["sha256"]), (saved_sha, self.v01["sha256"]))
        self.assertEqual(sha(self.root / PACKAGE / Path(index["current"]["path"]).name), saved_sha)
        events = ledger(self.root)
        [pending] = [e for e in events if e["event"] == "author_save" and e["status"] == "pending"]
        moves = [e for e in events if e["event"] == "move" and e.get("ref") == pending["seq"]]
        self.assertEqual(len(moves), 1, "the finished move is recorded once")
        self.assertEqual([e.get("resumed") for e in events if e["event"] == "author_save" and e["status"] == "complete"],
                         [True])
        self.assertEqual(proj.collect_status(proj.Project(self.root), git=False)["ledger_issues"], [])
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")

    def test_libreoffice_lock_files_count_like_word_ones(self):
        saved = self.base / "received" / "open in writer.docx"
        saved.write_bytes(msw_fixtures.make_docx(multi_chunk=True))
        lock = saved.parent / ".~lock.open in writer.docx#"
        lock.write_bytes(b"lock")
        before = snapshot(self.root)
        self.assertIn("open in Word or LibreOffice", self.run_refused("author-save", saved))
        self.assertIn("open in Word or LibreOffice", self.run_refused("intake", saved, "--as-version", "V01",
                                                                       "--role", "incoming", "--dry-run"))
        self.assertEqual(snapshot(self.root), before)
        # The lock of another file in the same folder does not count.
        other = self.base / "received" / "closed.docx"
        other.write_bytes(msw_fixtures.make_docx(multi_chunk=True))
        self.run_ok("intake", other, "--as-version", "V01", "--role", "incoming", "--dry-run")

    def test_a_returned_copy_becomes_current_without_a_new_version_number(self):
        returned = self.base / "received" / "V01 back from JD.docx"
        extra = msw_fixtures.para(msw_fixtures.run("A comment-driven edit from the supervisor."), "1000000F")
        returned.write_bytes(msw_fixtures.make_docx(extra_paragraphs=extra))
        before = snapshot(self.root)
        self.run_ok("intake", returned, "--role", "author_saved", "--state", "JD comments", "--note",
                    "returned by the supervisor", "--set-current")
        self.assert_nothing_lost(before, snapshot(self.root))
        name = f"{self.today} GS TST V01 JD comments.docx"
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual((index["current"]["version"], index["current"]["path"], index["current"]["state"]),
                         ("V01", f"{proj.VERSIONS}/V01/{name}", "JD comments"))
        self.assertEqual(index["baseline"]["sha256"], self.v01["sha256"])
        entry = {e["path"]: e for e in read_json(self.root / proj.VERSION_INDEX)}[f"{proj.VERSIONS}/V01/{name}"]
        self.assertEqual((entry["base_role"], entry["returned_by"], entry["note"]),
                         ("author_saved", "JD", "returned by the supervisor"))
        self.assertIn("current (returned by JD)", (self.root / proj.VERSION_INDEX_MD).read_text(encoding="utf-8"))
        out = self.run_ok("pass", "new", "--slug", "supervisor round")
        self.assertIn("Claimed V02", out)
        self.assertIn("source: V01 JD comments", out)
        # A name given with --returned-by wins; the author's own states name nobody.
        self.assertEqual(proj._returned_by("Dr Jane Doe", "supervisor comments", "author_saved"), "Dr Jane Doe")
        self.assertEqual(proj._returned_by(None, "supervisor comments", "author_saved"), "supervisor")
        self.assertIsNone(proj._returned_by(None, "author saved", "author_saved"))
        self.assertIsNone(proj._returned_by(None, None, "author_saved"))
        self.assertIsNone(proj._returned_by(None, "JD comments", "released"))


class ArchiveAndVerifyTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.v01 = self.intake_v01()

    def test_archive_refuses_indexed_files_and_moves_others_with_hashes(self):
        for target in (self.root / self.v01["path"], self.root / PACKAGE / Path(self.v01["path"]).name):
            self.assertIn("named by an index", self.run_refused("archive", "plan", "--reason", "Old", target))
        self.assertIn("skeleton", self.run_refused("archive", "plan", "--reason", "Old", self.root / "PROJECT_INDEX.json"))
        self.assertIn("contains", self.run_refused("archive", "plan", "--reason", "Old", self.root / proj.VERSIONS / "V01"))
        self.assertIn("--reason", self.run_refused("archive", "plan", "--reason", "../x",
                                                   self.root / proj.VERSIONS / "V01"))
        scratch = self.root / "90_Agent_Work" / "Scratch"
        note = scratch / "notes.txt"
        note.write_text("synthetic scratch note\n", encoding="utf-8")
        run_dir = scratch / "old run"
        run_dir.mkdir()
        (run_dir / "a.txt").write_text("a\n", encoding="utf-8")
        (run_dir / "b.txt").write_text("b\n", encoding="utf-8")
        before = snapshot(self.root)
        out = self.run_ok("archive", "plan", "--reason", "Old_Scratch", note, run_dir)
        self.assertIn("Plan only: 3 file(s)", out)
        self.assertEqual(snapshot(self.root), before)
        self.run_ok("archive", "apply", "--reason", "Old_Scratch", note, run_dir)
        after = snapshot(self.root)
        self.assert_nothing_lost(before, after)
        target = self.root / proj.ARCHIVE / self.today / "Old_Scratch" / "90_Agent_Work" / "Scratch"
        self.assertEqual((target / "notes.txt").read_text(encoding="utf-8"), "synthetic scratch note\n")
        self.assertTrue((target / "old run" / "b.txt").is_file())
        self.assertFalse(note.exists())
        self.assertFalse(run_dir.exists())
        moves = [e for e in ledger(self.root) if e["event"] == "move"]
        self.assertEqual([e["status"] for e in moves], ["pending", "complete"])
        self.assertEqual(moves[1]["ref"], moves[0]["seq"])
        files = [moves[1]["ops"][0]] + moves[1]["ops"][1]["files"]
        self.assertEqual(len(files), 3)
        for item in files:
            self.assertEqual(item["sha256_before"], item["sha256_after"])
            self.assertEqual(sha(self.root / item["new"]), item["sha256_after"])
        self.assertIn("does not exist", self.run_refused("archive", "apply", "--reason", "Old_Scratch", note))
        self.assertIn("already in the archive", self.run_refused("archive", "plan", "--reason", "Again",
                                                                 target / "notes.txt"))
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")

    def test_archive_refuses_a_file_libreoffice_has_open(self):
        scratch = self.root / "90_Agent_Work" / "Scratch"
        draft = scratch / "old copy.docx"
        draft.write_bytes(msw_fixtures.make_docx())
        (scratch / ".~lock.old copy.docx#").write_bytes(b"lock")
        (scratch / "unrelated.txt").write_text("synthetic\n", encoding="utf-8")
        self.assertIn("lock files present", self.run_refused("archive", "plan", "--reason", "Old", draft))
        self.run_ok("archive", "plan", "--reason", "Old", scratch / "unrelated.txt")

    def test_figure_index_paths_are_protected_and_verified(self):
        folder = self.root / "02_Figures" / "Figure_01_Study_design"
        (folder / "out" / "v1").mkdir(parents=True)
        pdf = folder / "out" / "v1" / "Figure_01_Study_design_v1.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% synthetic figure\n%%EOF\n")
        (folder / "old_candidate.png").write_bytes(msw_fixtures.png())
        figure_index = {"schema_version": 2, "figures": [{
            "id": "Figure_01", "title": "Study design", "folder": "02_Figures/Figure_01_Study_design", "current": "v1",
            "versions": [{"version": "v1", "used_in": ["V01"], "sha256": sha(pdf),
                          "pdf": "02_Figures/Figure_01_Study_design/out/v1/Figure_01_Study_design_v1.pdf",
                          "embedded_image": {"target": "media/image1.png",
                                             "sha256": hashlib.sha256(msw_fixtures.png()).hexdigest()}}]}]}
        (self.root / proj.FIGURE_INDEX).write_text(json.dumps(figure_index), encoding="utf-8")
        self.assertIn("named by an index", self.run_refused("archive", "plan", "--reason", "Old", pdf))
        self.assertIn("named by an index", self.run_refused("archive", "plan", "--reason", "Old", folder))
        self.run_ok("archive", "apply", "--reason", "Unused_Candidates", folder / "old_candidate.png")
        report = proj.collect_verification(proj.Project(self.root))
        self.assertEqual(report["status"], "PASS", report)
        pdf.write_bytes(b"%PDF-1.4\n% changed\n%%EOF\n")
        report = proj.collect_verification(proj.Project(self.root))
        self.assertEqual([item["named_by"] for item in report["mismatched"]], ["FIGURE_INDEX"])
        figure_index["figures"][0]["versions"][0]["embedded_image"]["sha256"] = "0" * 64
        figure_index["figures"][0]["versions"][0]["sha256"] = sha(pdf)
        (self.root / proj.FIGURE_INDEX).write_text(json.dumps(figure_index), encoding="utf-8")
        report = proj.collect_verification(proj.Project(self.root))
        self.assertEqual([item["named_by"] for item in report["mismatched"]],
                         ["FIGURE_INDEX Figure_01 v1 embedded image"])

    def test_verify_project_reports_problems_read_only(self):
        report = proj.collect_verification(proj.Project(self.root))
        self.assertEqual(report["status"], "PASS")
        self.assertGreaterEqual(report["checked"], 2)
        (self.root / PACKAGE / "Supplementary" / "table S1.xlsx").write_bytes(b"synthetic")
        (self.root / PACKAGE / "~$ something.docx").write_bytes(b"lock")
        before = snapshot(self.root)
        code, out, _ = cli("verify-project", "--project", self.root)
        self.assertEqual(code, 0, out)
        self.assertIn("UNLISTED", out)
        self.assertIn("LOCK", out)
        (self.root / self.v01["path"]).write_bytes(msw_fixtures.make_docx(multi_chunk=True))
        code, out, _ = cli("verify-project", "--project", self.root)
        self.assertEqual(code, 1)
        self.assertIn("CHANGED", out)
        after = snapshot(self.root)
        changed = {rel for rel in before if before[rel] != after.get(rel)}
        self.assertEqual(changed, {self.v01["path"]}, "verify-project must not write anything")
        code, out, _ = cli("verify-project", "--project", self.root, "--json")
        self.assertEqual(json.loads(out)["status"], "FAIL")


class FigureRegistryTests(ProjectCase):
    """figure add / register / promote / status and release --figures, on synthetic figure files."""

    FOLDER = "02_Figures/Figure_01_Study_design"
    TEXT_EDIT = [{"id": "E1", "op": "replace", "para": "10000004", "old": "which is high", "new": "which is elevated"}]
    LEGEND_EDIT = [{"id": "E1", "op": "replace", "para": "1000000A", "old": "Points are participants",
                    "new": "Points show participants"}]

    def setUp(self):
        super().setUp()
        self.init()
        self.v01 = self.intake_v01()
        self.owned = {proj.FIGURE_INDEX, proj.FIGURES_README, f"{self.FOLDER}/START_HERE.md"}
        with zipfile.ZipFile(self.root / self.v01["path"]) as archive:
            self.image1 = archive.read("word/media/image1.png")

    # helpers -------------------------------------------------------------------------
    def rel(self, path) -> str:
        return Path(path).resolve().relative_to(self.root.resolve()).as_posix()

    def build_outputs(self, version, colour, embed=None, stem=None) -> dict:
        """What a figure build writes: out/<version>/ with PDF, PNG, SVG and the embed PNG, its
        script and a QA folder (all synthetic)."""
        folder = self.root / self.FOLDER
        out = folder / "out" / version
        out.mkdir(parents=True)
        stem = stem or f"Figure_01_Study_design_{version}"
        files = {"pdf": out / f"{stem}.pdf", "png": out / f"{stem}.png", "svg": out / f"{stem}.svg",
                 "embed": out / f"{stem}_embed300.png", "script": folder / f"build_figure01_{version}.py",
                 "qa": self.root / "90_Agent_Work" / "Figure_QA" / f"{self.today}_Figure_01_{version}"}
        files["pdf"].write_bytes(f"%PDF-1.4\n% synthetic figure {version}\n%%EOF\n".encode())
        files["png"].write_bytes(msw_fixtures.png(60, 30, colour))
        files["svg"].write_text(f'<svg xmlns="http://www.w3.org/2000/svg" data-v="{version}"/>\n', encoding="utf-8")
        files["embed"].write_bytes(embed if embed is not None else msw_fixtures.png(80, 40, colour))
        files["script"].write_text(f"# synthetic build script for {version}\n", encoding="utf-8")
        files["qa"].mkdir(parents=True)
        return files

    def register(self, version, files, *extra):
        return self.run_ok("figure", "register", "--id", "Figure_01", "--version", version,
                           "--file", files["pdf"], "--file", files["png"], "--file", files["svg"],
                           "--script", files["script"], "--qa", files["qa"], "--embed", files["embed"], *extra)

    def figure_entry(self) -> dict:
        [entry] = read_json(self.root / proj.FIGURE_INDEX)["figures"]
        return entry

    def figure_status(self) -> dict:
        return json.loads(self.run_ok("figure", "status", "--json"))

    def text(self, rel) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    # tests ---------------------------------------------------------------------------
    def test_add_register_promote_and_status(self):
        code, out, _ = cli_help("figure", "register")
        self.assertEqual(code, 0)
        self.assertIn("--embed", out)
        before = snapshot(self.root)
        out = self.run_ok("figure", "add", "--id", "Figure_01", "--title", "Study design")
        self.assertIn(f"{self.FOLDER}/START_HERE.md", out)
        start_here = self.root / self.FOLDER / "START_HERE.md"
        text = start_here.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Figure_01: Study design\n"), text[:80])
        self.assertIn("<!-- msw:figure:start -->", text)
        self.assertIn("none promoted yet", text)
        self.assertIn("LEGEND.md", text)
        self.assertIn("figure register --id Figure_01", text)
        self.assertNotIn("\r\n", text)
        for key in ("figure_id", "figure_title", "figure_block", "figure_folder", "msw", "date"):
            self.assertNotIn("{" + key + "}", text)
        self.assertEqual(read_json(self.root / proj.FIGURE_INDEX)["figures"], [
            {"id": "Figure_01", "title": "Study design", "folder": self.FOLDER, "current": None, "versions": []}])
        self.assertIn("| Figure_01 | Study design | none |", self.text(proj.FIGURES_README))
        adds = [e for e in ledger(self.root) if e["event"] == "figure_add"]
        self.assertEqual([e["status"] for e in adds], ["pending", "complete"])
        self.assert_nothing_lost(before, snapshot(self.root), self.owned)
        self.assertIn("already registered", self.run_refused("figure", "add", "--id", "figure_01", "--title", "Other"))
        self.assertIn("inside 02_Figures", self.run_refused("figure", "add", "--id", "Figure_02", "--title", "Other",
                                                            "--folder", "../outside"))
        self.assertIn("not a figure id", self.run_refused("figure", "add", "--id", "01 fig", "--title", "Other"))

        v1 = self.build_outputs("v01", (30, 120, 60), embed=self.image1)
        before = snapshot(self.root)
        out = self.register("v01", v1, "--note", "first build")
        self.assertIn("nothing was copied or moved", out)
        entry = self.figure_entry()
        self.assertIsNone(entry["current"])
        [version] = entry["versions"]
        self.assertEqual((version["version"], version["note"], version["used_in"]), ("v01", "first build", []))
        self.assertEqual((version["pdf"], version["sha256"]), (self.rel(v1["pdf"]), sha(v1["pdf"])))
        self.assertEqual({item["kind"]: (item["path"], item["sha256"]) for item in version["files"]},
                         {kind: (self.rel(v1[kind]), sha(v1[kind])) for kind in ("pdf", "png", "svg")})
        self.assertEqual(version["script"], {"path": self.rel(v1["script"]), "sha256": sha(v1["script"])})
        self.assertEqual((version["embed"]["path"], version["embed"]["sha256"]), (self.rel(v1["embed"]), sha(v1["embed"])))
        self.assertEqual(version["qa"], self.rel(v1["qa"]))
        after = snapshot(self.root)
        self.assert_nothing_lost(before, after, self.owned)
        self.assertEqual(set(after) - set(before), set(), "register creates no files")
        for kind in ("pdf", "png", "svg", "embed", "script"):
            self.assertEqual(sum(1 for digest in after.values() if digest == sha(v1[kind])), 1, f"{kind} was copied")
        self.assertIn("| v01 |", start_here.read_text(encoding="utf-8"))
        registers = [e for e in ledger(self.root) if e["event"] == "figure_register"]
        self.assertEqual([(e["status"], e["figure_version"]) for e in registers], [("pending", "v01"), ("complete", "v01")])

        # Refusals change nothing, not even the ledger.
        v2 = self.build_outputs("v02", (20, 90, 200))
        outside = self.base / "outside.pdf"
        outside.write_bytes(b"%PDF-1.4\n% outside\n%%EOF\n")
        before = snapshot(self.root)
        self.assertIn("already registered", self.run_refused("figure", "register", "--id", "Figure_01", "--version",
                                                             "v1", "--file", v2["pdf"]))
        self.assertIn("never replaced", self.run_refused("figure", "register", "--id", "Figure_01", "--version",
                                                         "V01", "--file", v1["pdf"]))
        self.assertIn("already registered as Figure_01 v01", self.run_refused(
            "figure", "register", "--id", "Figure_01", "--version", "v02", "--file", v1["pdf"]))
        self.assertIn("outside the project", self.run_refused(
            "figure", "register", "--id", "Figure_01", "--version", "v02", "--file", outside))
        self.assertIn("not a PNG", self.run_refused(
            "figure", "register", "--id", "Figure_01", "--version", "v02", "--file", v2["pdf"], "--embed", v2["svg"]))
        self.assertIn("one PNG", self.run_refused(
            "figure", "register", "--id", "Figure_01", "--version", "v02", "--file", v2["png"], "--file", v2["embed"]))
        self.assertIn("not in", self.run_refused("figure", "register", "--id", "Figure_09", "--version", "v01",
                                                 "--file", v2["pdf"]))
        self.assertEqual(snapshot(self.root), before)

        out = self.run_ok("figure", "promote", "--id", "Figure_01", "--version", "v1")
        self.assertIn("Promoted Figure_01 v01", out)
        self.assertIn('shows it (picture "Figure 1")', out)
        self.assertEqual(self.figure_entry()["current"], "v01")
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["figure_set"], {"Figure_01": "v01"})
        self.assertIn("| Figure_01 | Study design | **v01** |", self.text(proj.FIGURES_README))
        self.assertIn("Figure set: Figure_01 v01", self.text(proj.STATUS))
        self.assertIn("Current version: **v01**", start_here.read_text(encoding="utf-8"))
        promotes = [e for e in ledger(self.root) if e["event"] == "figure_promote"]
        self.assertEqual([e["status"] for e in promotes], ["pending", "complete"])
        self.assertIn("already the promoted version", self.run_ok("figure", "promote", "--id", "Figure_01",
                                                                  "--version", "v01"))
        self.assertIn("not registered", self.run_refused("figure", "promote", "--id", "Figure_01", "--version", "v09"))

        before = snapshot(self.root)
        report = self.figure_status()
        [item] = report["figures"]
        self.assertEqual((item["current"], item["figure_set"], item["start_here"]), ("v01", "v01", True))
        self.assertEqual(item["embedded"]["state"], "match")
        self.assertEqual(item["embedded"]["picture"], "Figure 1")
        self.assertEqual({f["check"] for v in item["versions"] for f in v["files"]}, {"ok"})
        self.assertEqual(report["problems"], [])
        out = self.run_ok("figure", "status")
        self.assertIn("picture in V01: MATCH", out)
        self.assertIn("No problems found", out)
        self.assertEqual(snapshot(self.root), before, "figure status is read-only")
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")

        # A registered file that changes afterwards is reported and blocks promotion.
        v1["svg"].write_text("<svg/>\n", encoding="utf-8")
        report = self.figure_status()
        self.assertTrue(any("svg" in p and "changed" in p for p in report["problems"]), report["problems"])
        verification = proj.collect_verification(proj.Project(self.root))
        self.assertEqual(verification["status"], "FAIL")
        self.assertEqual([m["path"] for m in verification["mismatched"]], [self.rel(v1["svg"])])
        self.register("v02", v2)
        v1_pdf = v1["pdf"]
        self.assertTrue(v1_pdf.is_file())
        self.assertIn("no longer matches", self.run_refused("figure", "promote", "--id", "Figure_01", "--version", "v01"))

    def test_status_and_verify_detect_a_swapped_picture(self):
        self.run_ok("figure", "add", "--id", "Figure_01", "--title", "Study design")
        v1 = self.build_outputs("v01", (30, 120, 60), embed=self.image1)
        self.register("v01", v1)
        self.run_ok("figure", "promote", "--id", "Figure_01", "--version", "v01")
        self.assertEqual(self.figure_status()["figures"][0]["embedded"]["state"], "match")

        # A new manuscript version whose picture was swapped to another PNG.
        other = self.base / "received" / "another figure.png"
        other.write_bytes(msw_fixtures.png(80, 40, (20, 90, 200)))
        result = msw_edit.build(self.root / self.v01["path"], {"edits": [
            {"id": "F1", "op": "swap_picture", "old_name": "Figure 1", "new_name": "Figure 1 v2", "image": str(other)}]},
            author=AUTHOR)
        swapped = self.base / "received" / "picture swapped.docx"
        swapped.write_bytes(result.blob)
        self.run_ok("intake", swapped, "--as-version", "V02", "--slug", "picture swap", "--set-current")
        before = snapshot(self.root)
        report = self.figure_status()
        self.assertEqual(report["manuscript"]["version"], "V02")
        [item] = report["figures"]
        self.assertEqual(item["embedded"]["state"], "not_shown")
        self.assertEqual(item["embedded"]["expected"], hashlib.sha256(self.image1).hexdigest())
        self.assertIn("--figures Figure_01=v01", item["embedded"]["next"])
        shown = {p["sha256"]: p["name"] for p in report["pictures"]}
        self.assertEqual(shown, {sha(other): "Figure 1 v2"}, "the deleted picture is not in the accepted view")
        self.assertEqual(report["problems"], [], "a promoted figure not shown yet is a warning, not a problem")
        self.assertTrue(any("NOT SHOWN" in w and "swap_picture" in w for w in report["warnings"]), report["warnings"])
        out = self.run_ok("figure", "status")
        self.assertIn("picture in V02: NOT SHOWN", out)
        verification = proj.collect_verification(proj.Project(self.root))
        self.assertEqual(verification["status"], "PASS", "no file changed")
        self.assertEqual(verification["mismatched"], [])
        self.assertEqual([m["named_by"] for m in verification["not_shown"]],
                         ["FIGURE_INDEX Figure_01 v01 embedded picture"])
        code, out, _ = cli("verify-project", "--project", self.root)
        self.assertEqual(code, 0, out)
        self.assertIn("NOT SHOWN", out)
        self.assertNotIn("CHANGED", out)
        self.assertIn("no picture with the embed PNG's bytes", out)
        self.assertIn("swap_picture", out)
        self.assertIn("--figures Figure_01=v01", out)
        self.assertEqual(snapshot(self.root), before, "status and verify-project are read-only")

        # Register the new picture as v02: before promotion the manuscript is seen to show v02.
        v2 = self.build_outputs("v02", (20, 90, 200), embed=other.read_bytes())
        self.register("v02", v2)
        self.assertEqual(self.figure_status()["figures"][0]["embedded"]["shown_version"], "v02")
        out = self.run_ok("figure", "promote", "--id", "Figure_01", "--version", "v02")
        self.assertIn('shows it (picture "Figure 1 v2")', out)
        self.assertEqual(self.figure_status()["problems"], [])
        self.assertEqual(self.figure_status()["warnings"], [])
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["not_shown"], [])
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")

        # A figure file whose bytes changed stays CHANGED (and fails).
        v2["pdf"].write_bytes(b"%PDF-1.4\n% edited after registration\n%%EOF\n")
        code, out, _ = cli("verify-project", "--project", self.root)
        self.assertEqual(code, 1, out)
        self.assertIn("CHANGED", out)

    def test_release_with_figures_copies_files_and_archives_the_replaced_copies(self):
        self.run_ok("figure", "add", "--id", "Figure_01", "--title", "Study design")
        v1 = self.build_outputs("v01", (30, 120, 60), embed=self.image1)
        self.register("v01", v1)
        self.run_ok("figure", "promote", "--id", "Figure_01", "--version", "v01")
        label2, pass2, _, _ = self.make_pass("methods text", self.TEXT_EDIT, b"%PDF-1.4\n% proof V02\n%%EOF\n")
        before = snapshot(self.root)
        out = self.run_ok("release", "plan", "--pass", pass2, "--figures", "Figure_01=v1")
        self.assertIn("figure Figure_01 v01: promoted", out)
        self.assertIn('picture "Figure 1"', out)
        self.assertIn(f"{PACKAGE}/Figures/{v1['pdf'].name}", out)
        self.assertEqual(snapshot(self.root), before, "release plan must not change anything")
        self.run_ok("release", "apply", "--pass", pass2, "--figures", "Figure_01=v1")
        figures_dir = self.root / PACKAGE / "Figures"
        self.assertEqual(sorted(p.name for p in figures_dir.iterdir()), sorted([v1["pdf"].name, v1["png"].name]))
        for kind in ("pdf", "png"):
            self.assertEqual(sha(figures_dir / v1[kind].name), sha(v1[kind]))
            self.assertTrue(v1[kind].is_file(), "the registered file stays where the build wrote it")
        manifest = read_json(self.root / proj.PACKAGE_MANIFEST)
        self.assertEqual(sorted((i["package_path"], i["source_path"], i["figure"], i["figure_version"], i["version"])
                                for i in manifest["files"] if i["role"] == "figure"),
                         sorted((f"Figures/{v1[kind].name}", self.rel(v1[kind]), "Figure_01", "v01", label2)
                                for kind in ("pdf", "png")))
        self.assertEqual(self.figure_entry()["versions"][0]["used_in"], [label2])
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["figure_set"], {"Figure_01": "v01"})
        releases = [e for e in ledger(self.root) if e["event"] == "release"]
        self.assertEqual([(e["status"], e.get("figures")) for e in releases],
                         [("pending", {"Figure_01": "v01"}), ("complete", {"Figure_01": "v01"})])
        self.assert_nothing_lost(before, snapshot(self.root), self.owned)
        self.assertIn("Released in: V02", (self.root / self.FOLDER / "START_HERE.md").read_text(encoding="utf-8"))
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")
        self.assertEqual(self.figure_status()["problems"], [])

        # v02 is registered and promoted. A pass that does not swap the picture cannot release it,
        # and v01 is no longer the promoted version.
        v2 = self.build_outputs("v02", (20, 90, 200))
        self.register("v02", v2, "--note", "arm colours")
        self.run_ok("figure", "promote", "--id", "Figure_01", "--version", "v02")
        _, text_pass, _, _ = self.make_pass("legend wording", self.LEGEND_EDIT, b"%PDF-1.4\n% proof V03\n%%EOF\n")
        before = snapshot(self.root)
        self.assertIn("does not show Figure_01 v02", self.run_refused("release", "plan", "--pass", text_pass,
                                                                      "--figures", "Figure_01=v02"))
        self.assertIn("not the promoted version", self.run_refused("release", "plan", "--pass", text_pass,
                                                                   "--figures", "Figure_01=v01"))
        self.assertIn("not ID=vNN", self.run_refused("release", "plan", "--pass", text_pass, "--figures", "Figure_01"))
        self.assertEqual(snapshot(self.root), before)

        # A pass that swaps the picture releases v02, named in pass.json as figures_changed.
        label4, pass4, info4, _ = self.make_pass("figure 1 update", [
            {"id": "F1", "op": "swap_picture", "old_name": "Figure 1", "new_name": "Figure 1 v02",
             "image": str(v2["embed"]), "purpose": "author-approved Figure 1 v02"}], b"%PDF-1.4\n% proof V04\n%%EOF\n")
        self.assertEqual(label4, "V04")
        (pass4 / "pass.json").write_text(json.dumps(dict(info4, figures_changed={"Figure_01": "v02"})), encoding="utf-8")
        before = snapshot(self.root)
        out = self.run_ok("release", "plan", "--pass", pass4)
        self.assertIn("figure Figure_01 v02: promoted", out)
        self.assertNotIn("WARNING: the build swaps", out)
        self.run_ok("release", "apply", "--pass", pass4)
        after = snapshot(self.root)
        self.assert_nothing_lost(before, after, self.owned)
        archive = self.root / proj.ARCHIVE / self.today / f"Package_Superseded_{label4}" / "Figures"
        self.assertEqual(sorted(p.name for p in archive.iterdir()), sorted([v1["pdf"].name, v1["png"].name]))
        self.assertEqual(sorted(p.name for p in figures_dir.iterdir()), sorted([v2["pdf"].name, v2["png"].name]))
        for kind in ("pdf", "png"):
            self.assertEqual(sha(archive / v1[kind].name), sha(v1[kind]))
            self.assertTrue(v1[kind].is_file(), "the twin in 02_Figures stays")
            self.assertEqual(sha(figures_dir / v2[kind].name), sha(v2[kind]))
        move = [e for e in ledger(self.root) if e["event"] == "move"][-1]
        figure_ops = [op for op in move["ops"] if "/Figures/" in op["old"]]
        self.assertEqual(len(figure_ops), 2)
        for op in figure_ops:
            self.assertTrue(op["twin"].startswith(self.FOLDER + "/out/v01/"), op)
            self.assertEqual(op["sha256_before"], op["sha256_after"])
            self.assertEqual(sha(self.root / op["twin"]), op["sha256_before"])
        self.assertEqual({v["version"]: v["used_in"] for v in self.figure_entry()["versions"]},
                         {"v01": [label2], "v02": [label4]})
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["figure_set"], {"Figure_01": "v02"})
        report = self.figure_status()
        self.assertEqual((report["figures"][0]["embedded"]["state"], report["figures"][0]["embedded"]["picture"]),
                         ("match", "Figure 1 v02"))
        self.assertEqual(report["problems"], [])
        verification = proj.collect_verification(proj.Project(self.root))
        self.assertEqual(verification["status"], "PASS", verification)
        self.assertEqual(verification["unlisted_package_files"], [])

        # A later release without --figures keeps the figure copies in the package.
        label5, pass5, _, _ = self.make_pass("methods again", [
            {"id": "E1", "op": "replace", "para": "10000003", "old": "compared", "new": "contrasted"}],
            b"%PDF-1.4\n% proof V05\n%%EOF\n")
        self.run_ok("release", "apply", "--pass", pass5)
        self.assertEqual(sorted(p.name for p in figures_dir.iterdir()), sorted([v2["pdf"].name, v2["png"].name]))
        self.assertEqual({v["version"]: v["used_in"] for v in self.figure_entry()["versions"]},
                         {"v01": [label2], "v02": [label4]})
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")

    def test_release_replaces_a_package_copy_that_has_the_same_name(self):
        self.run_ok("figure", "add", "--id", "Figure_01", "--title", "Study design")
        v1 = self.build_outputs("v1", (30, 120, 60), embed=self.image1, stem="Figure_01")
        self.register("v1", v1)
        self.run_ok("figure", "promote", "--id", "Figure_01", "--version", "v1")
        _, pass2, _, _ = self.make_pass("methods text", self.TEXT_EDIT, b"%PDF-1.4\n% proof V02\n%%EOF\n")
        self.run_ok("release", "apply", "--pass", pass2, "--figures", "Figure_01=v1")
        v2 = self.build_outputs("v2", (20, 90, 200), stem="Figure_01")
        self.assertIn("Register the new build as v2", self.run_refused(
            "figure", "register", "--id", "Figure_01", "--version", "v01", "--file", v2["pdf"]))
        self.register("v2", v2)
        self.run_ok("figure", "promote", "--id", "Figure_01", "--version", "v02")
        self.assertEqual(self.figure_entry()["current"], "v2", "the label is kept as registered")
        label3, pass3, _, _ = self.make_pass("figure 1 update", [
            {"id": "F1", "op": "swap_picture", "old_name": "Figure 1", "image": str(v2["embed"])}],
            b"%PDF-1.4\n% proof V03\n%%EOF\n")
        before = snapshot(self.root)
        self.run_ok("release", "apply", "--pass", pass3, "--figures", "Figure_01=v2")
        self.assert_nothing_lost(before, snapshot(self.root), self.owned)
        figures_dir = self.root / PACKAGE / "Figures"
        archive = self.root / proj.ARCHIVE / self.today / f"Package_Superseded_{label3}" / "Figures"
        for kind in ("pdf", "png"):
            name = v1[kind].name
            self.assertEqual(name, v2[kind].name)
            self.assertEqual(sha(figures_dir / name), sha(v2[kind]))
            self.assertEqual(sha(archive / name), sha(v1[kind]))
            self.assertTrue(v1[kind].is_file() and v2[kind].is_file())
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["figure_set"], {"Figure_01": "v2"})
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")

    def test_release_brings_in_a_promoted_figure_the_build_shows(self):
        self.run_ok("figure", "add", "--id", "Figure_01", "--title", "Study design")
        v1 = self.build_outputs("v01", (30, 120, 60), embed=self.image1)
        self.register("v01", v1)
        self.run_ok("figure", "promote", "--id", "Figure_01", "--version", "v01")
        label2, pass2, _, _ = self.make_pass("methods text", self.TEXT_EDIT, b"%PDF-1.4\n% proof V02\n%%EOF\n")
        # The build shows the promoted v01 and the package has no figure yet: v01 comes in unnamed.
        out = self.run_ok("release", "plan", "--pass", pass2)
        self.assertIn("included as if named with --figures Figure_01=v01", out)
        self.run_ok("release", "apply", "--pass", pass2)
        figures_dir = self.root / PACKAGE / "Figures"
        self.assertEqual(sorted(p.name for p in figures_dir.iterdir()), sorted([v1["pdf"].name, v1["png"].name]))
        # v02 is promoted and a pass swaps the picture, but neither --figures nor figures_changed names it.
        v2 = self.build_outputs("v02", (20, 90, 200))
        self.register("v02", v2)
        self.run_ok("figure", "promote", "--id", "Figure_01", "--version", "v02")
        label3, pass3, _, _ = self.make_pass("figure 1 update", [
            {"id": "F1", "op": "swap_picture", "old_name": "Figure 1", "new_name": "Figure 1 v02",
             "image": str(v2["embed"])}], b"%PDF-1.4\n% proof V03\n%%EOF\n")
        out = self.run_ok("release", "plan", "--pass", pass3)
        self.assertIn("included as if named with --figures Figure_01=v02", out)
        self.assertNotIn("WARNING: the build swaps", out)
        before = snapshot(self.root)
        self.run_ok("release", "apply", "--pass", pass3)
        self.assert_nothing_lost(before, snapshot(self.root), self.owned)
        self.assertEqual(sorted(p.name for p in figures_dir.iterdir()), sorted([v2["pdf"].name, v2["png"].name]))
        archive = self.root / proj.ARCHIVE / self.today / f"Package_Superseded_{label3}" / "Figures"
        self.assertEqual(sorted(p.name for p in archive.iterdir()), sorted([v1["pdf"].name, v1["png"].name]))
        self.assertEqual({v["version"]: v["used_in"] for v in self.figure_entry()["versions"]},
                         {"v01": [label2], "v02": [label3]})
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["figure_set"], {"Figure_01": "v02"})
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")
        # A later text pass: the package already holds the shown version, so nothing comes in.
        _, pass4, _, _ = self.make_pass("legend wording", self.LEGEND_EDIT, b"%PDF-1.4\n% proof V04\n%%EOF\n")
        self.assertNotIn("included as if named", self.run_ok("release", "plan", "--pass", pass4))

    def test_release_refuses_a_picture_two_promoted_figures_share(self):
        self.run_ok("figure", "add", "--id", "Figure_01", "--title", "Study design")
        self.register("v01", self.build_outputs("v01", (30, 120, 60), embed=self.image1))
        self.run_ok("figure", "promote", "--id", "Figure_01", "--version", "v01")
        self.run_ok("figure", "add", "--id", "Figure_02", "--title", "Other panel")
        out2 = self.root / "02_Figures" / "Figure_02_Other_panel" / "out" / "v01"
        out2.mkdir(parents=True)
        (out2 / "Figure_02_v01.pdf").write_bytes(b"%PDF-1.4\n% synthetic figure 2\n%%EOF\n")
        (out2 / "Figure_02_v01_embed300.png").write_bytes(self.image1)
        self.run_ok("figure", "register", "--id", "Figure_02", "--version", "v01", "--file", out2 / "Figure_02_v01.pdf",
                    "--embed", out2 / "Figure_02_v01_embed300.png")
        self.run_ok("figure", "promote", "--id", "Figure_02", "--version", "v01")
        _, pass2, _, _ = self.make_pass("methods text", self.TEXT_EDIT, b"%PDF-1.4\n% proof V02\n%%EOF\n")
        message = self.run_refused("release", "plan", "--pass", pass2)
        self.assertIn("Figure_01 v01, Figure_02 v01", message)
        self.assertIn("--figures", message)
        out = self.run_ok("release", "plan", "--pass", pass2, "--figures", "Figure_01=v01")
        self.assertNotIn("included as if named", out)

    def test_interrupted_promote_is_finished_on_rerun(self):
        self.run_ok("figure", "add", "--id", "Figure_01", "--title", "Study design")
        self.register("v01", self.build_outputs("v01", (30, 120, 60), embed=self.image1))
        original = proj.Project.ledger_append

        def crash_on_completion(project, event):
            if event.get("event") == "figure_promote" and event.get("status") == "complete":
                raise OSError("power cut (simulated)")
            return original(project, event)

        with mock.patch.object(proj.Project, "ledger_append", crash_on_completion):
            self.run_refused("figure", "promote", "--id", "Figure_01", "--version", "v01")
        self.assertEqual(self.figure_entry()["current"], "v01")
        issues = proj.collect_status(proj.Project(self.root), git=False)["ledger_issues"]
        self.assertEqual([(i["event"], i["issue"], i["version"]) for i in issues],
                         [("figure_promote", "interrupted", "Figure_01 v01")])
        self.assertIn("Finished the interrupted figure promote", self.run_ok("figure", "promote", "--id", "Figure_01",
                                                                             "--version", "v01"))
        self.assertEqual(proj.collect_status(proj.Project(self.root), git=False)["ledger_issues"], [])
        self.assertIn("already the promoted version", self.run_ok("figure", "promote", "--id", "Figure_01",
                                                                  "--version", "v01"))


class AtomicReplaceTests(ProjectCase):
    """A leftover name.new refuses the whole index change before anything is written."""

    def test_atomic_replace_many_checks_every_target_first(self):
        folder = self.base / "indexes"
        folder.mkdir()
        first, second = folder / "a.json", folder / "b.json"
        first.write_text("A1\n", encoding="utf-8")
        second.write_text("B1\n", encoding="utf-8")
        (folder / "b.json.new").write_text("left by a crash\n", encoding="utf-8")
        with self.assertRaises(proj.ProjectError) as caught:
            proj.atomic_replace_many([(first, "A2\n"), (second, "B2\n")])
        self.assertIn("b.json.new", str(caught.exception))
        self.assertIn("nothing was written", str(caught.exception))
        self.assertEqual(first.read_text(encoding="utf-8"), "A1\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "B1\n")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["a.json", "b.json", "b.json.new"])
        proj.atomic_replace_many([(first, "A2\n")])
        self.assertEqual(first.read_text(encoding="utf-8"), "A2\n")
        self.assertFalse((folder / "a.json.new").exists())

    def test_intake_refused_by_a_leftover_changes_no_index_or_block(self):
        self.init()
        leftover = self.root / (proj.STATUS + ".new")
        leftover.write_text("left by an interrupted run (synthetic)\n", encoding="utf-8")
        before = snapshot(self.root)
        self.assertIn("left by an interrupted run", self.run_refused("intake", self.draft, "--dry-run"))
        message = self.run_refused("intake", self.draft, "--as-version", "V01")
        self.assertIn("left by an interrupted run", message)
        self.assertIn(f"{proj.STATUS}.new", message)
        self.assertEqual(snapshot(self.root), before, "refused before any copy, index, block or ledger line")
        status = proj.collect_status(proj.Project(self.root), git=False)
        self.assertEqual(status["leftovers"], [f"{proj.STATUS}.new"])
        self.assertTrue(any("Interrupted_Run" in line for line in status["suggestions"]))
        # The last line of defence: commit_indexes itself stages nothing when a leftover exists.
        project = proj.Project(self.root)
        index = project.project_index()
        with self.assertRaises(proj.ProjectError):
            project.commit_indexes(entries=project.version_entries(), index=index)
        self.assertEqual(snapshot(self.root), before)
        # Move the leftover away (never deleted) and run the same command again.
        self.run_ok("archive", "apply", "--reason", "Interrupted_Run", leftover)
        self.run_ok("intake", self.draft, "--as-version", "V01")
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["current"]["version"], "V01")
        self.assertEqual([p.relative_to(self.root).as_posix() for p in self.root.rglob("*.new")],
                         [f"{proj.ARCHIVE}/{self.today}/Interrupted_Run/{proj.STATUS}.new"])
        self.assertEqual(proj.collect_status(proj.Project(self.root), git=False)["ledger_issues"], [])
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")


class MutationLockTests(ProjectCase):
    """Commands that change the project hold 06_Project_Docs/.msw_mutation.lock, so two sessions
    cannot both write back the indexes they read; read-only commands do not need it."""

    def setUp(self):
        super().setUp()
        self.init()
        self.v01 = self.intake_v01()

    def interleaved(self, second: tuple):
        """Patch commit_indexes so that, the first time the running command commits, a second
        command (`second`, run in-process like another session) runs just before it."""
        real, result = proj.Project.commit_indexes, {}

        def commit(project, *args, **kwargs):
            if not result:
                result["started"] = True
                result["b"] = cli(*second, "--project", self.root)
            return real(project, *args, **kwargs)

        return mock.patch.object(proj.Project, "commit_indexes", commit), result

    def figure_ids(self) -> list:
        return [figure["id"] for figure in read_json(self.root / proj.FIGURE_INDEX)["figures"]]

    def test_a_second_session_cannot_drop_the_first_ones_index_entry(self):
        second = ("figure", "add", "--id", "Figure_02", "--title", "Outcomes")
        # Without the lock, session B commits between A's read and A's write, and A's stale
        # snapshot drops B's figure: the lost update the lock prevents.
        patch, result = self.interleaved(("figure", "add", "--id", "Figure_09", "--title", "Lost"))
        with patch, mock.patch.object(proj, "mutation_lock", lambda root, command: contextlib.nullcontext()):
            self.run_ok("figure", "add", "--id", "Figure_08", "--title", "Kept")
        self.assertEqual(result["b"][0], 0)
        self.assertEqual(self.figure_ids(), ["Figure_08"], "B's entry was silently dropped")
        # With the lock, B refuses and changes nothing; A's change and B's rerun both survive.
        patch, result = self.interleaved(second)
        with patch:
            self.run_ok("figure", "add", "--id", "Figure_01", "--title", "Study design")
        code, out, err = result["b"]
        self.assertEqual(code, 2, out + err)
        self.assertIn("another msw command is changing this project", err)
        self.assertIn("`msw.py figure add`", err)
        self.assertIn(f"PID {os.getpid()}", err)
        self.assertEqual(self.figure_ids(), ["Figure_08", "Figure_01"])
        self.assertIsNone(proj.lock_state(self.root), "the holder releases its lock")
        self.assertTrue((self.root / proj.MUTATION_LOCK).is_file(), "the lock file is kept, never deleted")
        self.assertFalse((self.root / "02_Figures" / "Figure_02_Outcomes").exists())
        self.run_ok(*second)
        self.assertEqual(self.figure_ids(), ["Figure_08", "Figure_01", "Figure_02"])
        # The same holds for PROJECT_INDEX, and the ledger keeps one line per seq.
        patch, result = self.interleaved(("outstanding", "add", "--text", "second session"))
        with patch:
            self.run_ok("outstanding", "add", "--text", "first session")
        self.assertEqual(result["b"][0], 2)
        self.assertEqual([item["text"] for item in read_json(self.root / proj.PROJECT_INDEX)["outstanding"]],
                         ["first session"])
        seqs = [e["seq"] for e in ledger(self.root)]
        self.assertEqual(len(seqs), len(set(seqs)))
        self.assertIsNone(proj.lock_state(self.root))
        # A failing command releases the lock too.
        with mock.patch.object(proj.Project, "commit_indexes", side_effect=OSError("disk went away (simulated)")):
            self.run_refused("outstanding", "add", "--text", "third")
        self.assertIsNone(proj.lock_state(self.root))

    def test_read_only_commands_ignore_the_lock_and_a_crashed_holder_leaves_none(self):
        scratch = self.root / "90_Agent_Work" / "Scratch" / "note.txt"
        scratch.write_text("synthetic\n", encoding="utf-8")
        with proj.mutation_lock(self.root, "release apply"):     # another session holds the lock
            before = snapshot(self.root)
            self.assertIn("Mutation lock: held by `msw.py release apply`", self.run_ok("status"))
            code, out, err = cli("verify-project", "--project", self.root)
            self.assertEqual(code, 0, out + err)
            self.run_ok("figure", "status")
            other = self.base / "received" / "second.docx"
            other.write_bytes(msw_fixtures.make_docx(multi_chunk=True))
            self.run_ok("intake", other, "--as-version", "V01", "--role", "incoming", "--dry-run")
            self.run_ok("retarget", "--venue", "JOE", "--target-journal", "Journal of Examples", "--dry-run")
            self.run_ok("archive", "plan", "--reason", "Old", scratch)
            message = self.run_refused("outstanding", "add", "--text", "while another command runs")
            self.assertIn("`msw.py release apply`", message)
            self.assertIn("Wait until it finishes", message)
            self.assertEqual(snapshot(self.root), before)
        self.run_ok("archive", "apply", "--reason", "Old", scratch)
        self.assertIn(proj.MUTATION_LOCK, (self.root / ".gitignore").read_text(encoding="utf-8").splitlines())
        # A holder that crashes leaves no lock: the operating system releases it with the process.
        crash = ("import os, sys; sys.path.insert(0, sys.argv[1]); from mswlib import project as p\n"
                 "with p.mutation_lock(sys.argv[2], 'intake'):\n    os._exit(3)")
        holder = subprocess.run([sys.executable, "-c", crash, str(SCRIPTS), str(self.root)],
                                capture_output=True, text=True)
        self.assertEqual(holder.returncode, 3, holder.stderr)
        self.assertIsNone(proj.lock_state(self.root))
        self.assertTrue((self.root / proj.MUTATION_LOCK).is_file())
        self.run_ok("outstanding", "add", "--text", "after the crash")


class OutstandingTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.v01 = self.intake_v01()

    def text(self, rel) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def test_add_and_done_rewrite_the_index_and_the_generated_blocks(self):
        self.assertIn("Open items: see the latest change note", self.text(proj.README))
        before = snapshot(self.root)
        out = self.run_ok("outstanding", "add", "--text", "Confirm the  Q-17 assay units with the author")
        self.assertIn("Added O1", out)
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual(index["outstanding"], [{"id": "O1", "text": "Confirm the Q-17 assay units with the author",
                                                 "since": "V01"}])
        self.assertIn("- Open items: 1 (see `06_Project_Docs/STATUS.md`)", self.text(proj.README))
        self.assertIn("- O1: Confirm the Q-17 assay units with the author (since V01)", self.text(proj.STATUS))
        self.run_ok("outstanding", "add", "--text", "Check the Figure 1 legend against the panels")
        self.assertIn("Resolved O1", self.run_ok("outstanding", "done", "--id", "o1"))
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual([item["id"] for item in index["outstanding"]], ["O2"])
        self.assertEqual([(item["id"], item["since"], item["resolved_in"]) for item in index["resolved"]],
                         [("O1", "V01", "V01")])
        self.assertNotIn("O1:", self.text(proj.STATUS))
        self.assertIn("already resolved", self.run_refused("outstanding", "done", "--id", "O1"))
        self.assertIn("no open item", self.run_refused("outstanding", "done", "--id", "O9"))
        self.assertIn("--text", self.run_refused("outstanding", "add", "--text", "   "))
        self.run_ok("outstanding", "done", "--id", "O2")
        for rel in (proj.README, proj.STATUS):
            self.assertIn("Open items: see the latest change note", self.text(rel))
            self.assertNotIn("Open items: 0", self.text(rel))
        self.assertIn("Added O3", self.run_ok("outstanding", "add", "--text", "Ask about the abstract length"))
        actions = [(e["action"], e["id"]) for e in ledger(self.root) if e["event"] == "outstanding"]
        self.assertEqual(actions, [("add", "O1"), ("add", "O2"), ("done", "O1"), ("done", "O2"), ("add", "O3")])
        self.assert_nothing_lost(before, snapshot(self.root))
        self.assertEqual(list(self.root.rglob("*.new")), [])
        self.assertIn("O3: Ask about the abstract length", self.run_ok("status"))
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")


class NamingAndProfileTests(ProjectCase):
    EDIT = [{"id": "E1", "op": "replace", "para": "10000004", "old": "which is high", "new": "which is elevated"}]

    def setUp(self):
        super().setUp()
        self.init()
        self.v01 = self.intake_v01()

    def test_release_accepts_an_older_long_build_name_and_a_proof_folder(self):
        out = self.run_ok("pass", "new", "--slug", "methods text")
        pass_dir = self.root / proj.PASSES / "V02_methods_text"
        info = read_json(pass_dir / "pass.json")
        (pass_dir / "edits.json").write_text(json.dumps({"edits": self.EDIT}), encoding="utf-8")
        old_style = pass_dir / "build01" / f"{info['date']} GS TST V02 methods text tracked.docx"
        code, bout, berr = cli("build", self.root / info["source"]["path"], pass_dir / "edits.json",
                               "--out", old_style, "--author", AUTHOR)
        self.assertEqual(code, 0, bout + berr)
        proof_dir = pass_dir / "proofs" / "p1"
        proof_dir.mkdir()
        (proof_dir / "V02 tracked accepted view.pdf").write_bytes(b"%PDF-1.4\n% synthetic proof\n%%EOF\n")
        note = pass_dir / "V02_CHANGES.md"
        note.write_text(note.read_text(encoding="utf-8").split("\n", 1)[1], encoding="utf-8")
        self.run_ok("release", "apply", "--pass", pass_dir)
        released = self.root / proj.VERSIONS / "V02" / f"{info['date']} GS TST V02 methods text tracked verified.docx"
        self.assertEqual(sha(released), sha(old_style))
        self.assertTrue((self.root / proj.VERSIONS / "V02" / f"{released.stem} V02 tracked accepted view.pdf").is_file())
        self.assertIn("released as:", out)

    def test_render_profiles_are_not_created_skipped_by_walks_and_archived_whole(self):
        self.assertFalse((self.root / proj.RENDER_PROFILES).exists())
        # An older project's in-project LibreOffice profile, and an old per-proof profile in a pass.
        profile = self.root / proj.RENDER_PROFILES / "libreoffice" / "user" / "cache"
        profile.mkdir(parents=True)
        (profile / "cache entry.bin").write_bytes(b"synthetic cache")
        (profile / "~$looks like a lock.docx").write_bytes(b"not a Word lock")
        self.run_ok("pass", "new", "--slug", "proof check")
        old_proof = self.root / proj.PASSES / "V02_proof_check" / "proofs" / "p1" / "lo_profile" / "user"
        old_proof.mkdir(parents=True)
        (old_proof / "~$registrymodifications.xcu").write_bytes(b"not a Word lock either")
        status = proj.collect_status(proj.Project(self.root), git=False)
        self.assertEqual((status["locks"], status["unreadable"]), ([], []))
        report = proj.collect_verification(proj.Project(self.root))
        self.assertEqual((report["status"], report["locks"]), ("PASS", []))

        before = snapshot(self.root)
        out = self.run_ok("archive", "plan", "--reason", "Old_Render_Profile", self.root / proj.RENDER_PROFILES)
        self.assertIn("LibreOffice profile", out)
        self.assertIn("not hashed", out)
        self.assertEqual(snapshot(self.root), before)
        self.run_ok("archive", "apply", "--reason", "Old_Render_Profile", self.root / proj.RENDER_PROFILES)
        self.assert_nothing_lost(before, snapshot(self.root))
        self.assertFalse((self.root / proj.RENDER_PROFILES).exists())
        moved = self.root / proj.ARCHIVE / self.today / "Old_Render_Profile" / proj.RENDER_PROFILES
        self.assertTrue((moved / "libreoffice" / "user" / "cache" / "cache entry.bin").is_file())
        [move] = [e for e in ledger(self.root) if e["event"] == "move" and e["status"] == "complete"]
        self.assertEqual(move["ops"][0]["profiles"], [{"folder": proj.RENDER_PROFILES, "files": 2}])
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_gitignore_keeps_proof_pdfs_and_ignores_tool_records(self):
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True, capture_output=True)
        proofs = self.root / proj.PASSES / "V02_methods_text" / "proofs" / "p1"
        proofs.mkdir(parents=True)
        names = {"proof.json": True, "soffice_stdout.txt": True, "soffice_stderr_2.txt": True,
                 "V02 tracked accepted view.pdf": False}
        for name in names:
            (proofs / name).write_bytes(b"synthetic")
        for name, ignored in names.items():
            rel = (proofs / name).relative_to(self.root).as_posix()
            result = subprocess.run(["git", "-C", str(self.root), "check-ignore", "-q", rel], capture_output=True)
            self.assertEqual(result.returncode == 0, ignored, rel)


class RetargetTests(ProjectCase):
    PROFILE = "05_References/Journal_Guidelines/joe.profile.json"

    def setUp(self):
        super().setUp()
        self.init("--target-journal", "Test Journal")
        self.v01 = self.intake_v01()
        (self.root / self.PROFILE).write_text('{"journal_id": "joe"}\n', encoding="utf-8")

    def retarget(self, *extra):
        return self.run_ok("retarget", "--venue", "JOE", "--target-journal", "Journal of Examples",
                           "--journal-profile", self.PROFILE, *extra)

    def test_retarget_updates_the_configuration_index_and_blocks(self):
        old_config = sha(self.root / "manuscript.json")
        with open(self.root / "README.md", "a", encoding="utf-8", newline="\n") as handle:
            handle.write("\nNotes: the tables follow the TST style sheet.\n")     # a hand-written line
        before = snapshot(self.root)
        out = self.retarget("--dry-run")
        self.assertIn("venue: 'TST' -> 'JOE'", out)
        self.assertIn("Dry run", out)
        self.assertEqual(snapshot(self.root), before)
        out = self.retarget("--note", "rejected by the first journal")
        self.assertIn("Retargeted to Journal of Examples (JOE)", out)
        self.assertIn("NOTE: README.md line", out)
        self.assertIn("TST style sheet", out)
        self.assertNotIn("NOTE: AGENTS.md", out, "the set-up line of AGENTS.md is history, not stale")
        self.assert_nothing_lost(before, snapshot(self.root), {"manuscript.json"})
        config = read_json(self.root / "manuscript.json")
        self.assertEqual((config["venue"], config["journal_profile"]), ("JOE", self.PROFILE))
        self.assertEqual(config["author"]["name"], AUTHOR, "other settings are kept")
        backup = self.root / proj.ARCHIVE / self.today / "Retarget_from_TST" / "manuscript.json"
        self.assertEqual(sha(backup), old_config)
        index = read_json(self.root / proj.PROJECT_INDEX)
        self.assertEqual((index["venue"], index["target_journal"]), ("JOE", "Journal of Examples"))
        for rel in (proj.README, proj.STATUS):
            self.assertIn("Target journal: Journal of Examples (venue code JOE)",
                          (self.root / rel).read_text(encoding="utf-8"), rel)
        events = [e for e in ledger(self.root) if e["event"] == "retarget"]
        self.assertEqual([e["status"] for e in events], ["pending", "complete"])
        self.assertEqual((events[0]["from"]["venue"], events[0]["to"]["venue"], events[0]["note"]),
                         ("TST", "JOE", "rejected by the first journal"))
        self.assertEqual(events[1]["previous_config"]["sha256"], old_config)
        # Version numbers keep counting; new names use the new venue code.
        out = self.run_ok("pass", "new", "--slug", "journal format")
        self.assertIn(f"released as: {proj.VERSIONS}/V02/{self.today} GS JOE V02 journal format tracked verified.docx",
                      out)
        self.assertIn("Target journal: Journal of Examples", self.run_ok("status"))
        self.assertIn("Nothing to change", self.retarget())
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")

    def test_retarget_stopped_after_the_index_swap_is_finished_on_rerun(self):
        with self.swap_refused(proj.README):
            self.assertIn("stays pending", self.run_refused(
                "retarget", "--venue", "JOE", "--target-journal", "Journal of Examples", "--journal-profile",
                self.PROFILE))
        # manuscript.json and PROJECT_INDEX.json name the new journal; the generated blocks do not.
        self.assertEqual(read_json(self.root / "manuscript.json")["venue"], "JOE")
        self.assertEqual(read_json(self.root / proj.PROJECT_INDEX)["target_journal"], "Journal of Examples")
        self.assertTrue((self.root / (proj.README + ".new")).exists())
        self.assertNotIn("Journal of Examples", (self.root / proj.README).read_text(encoding="utf-8"))
        report = proj.collect_verification(proj.Project(self.root))
        self.assertEqual([(i["event"], i["issue"]) for i in report["unfinished"]], [("retarget", "failed")])
        status = proj.collect_status(proj.Project(self.root), git=False)
        self.assertTrue(any("retarget again" in line for line in status["suggestions"]), status["suggestions"])
        before = snapshot(self.root)
        out = self.retarget("--dry-run")
        self.assertIn("never completed", out)
        self.assertNotIn("Nothing to change", out)
        self.assertEqual(snapshot(self.root), before)
        out = self.retarget()
        self.assertIn("Retargeted to Journal of Examples (JOE)", out)
        self.assert_nothing_lost(before, snapshot(self.root), {"manuscript.json"})
        for rel in (proj.README, proj.STATUS):
            self.assertIn("Target journal: Journal of Examples (venue code JOE)",
                          (self.root / rel).read_text(encoding="utf-8"), rel)
        self.assertEqual(self.leftovers_outside_archive(), [])
        self.assertTrue((self.root / proj.ARCHIVE / self.today / "Interrupted_Run" / (proj.README + ".new")).is_file())
        events = [e for e in ledger(self.root) if e["event"] == "retarget"]
        self.assertEqual([e["status"] for e in events], ["pending", "failed", "complete"])
        self.assertEqual((events[2]["ref"], events[2]["resumed"], events[2]["from"]["venue"]),
                         (events[0]["seq"], True, "TST"))
        self.assertEqual(events[2]["previous_config"]["path"], events[0]["backup"])
        self.assertEqual(proj.collect_status(proj.Project(self.root), git=False)["ledger_issues"], [])
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")
        self.assertIn("Nothing to change", self.retarget())

    def test_retarget_stopped_before_the_configuration_swap_reuses_its_backup(self):
        old_config = sha(self.root / "manuscript.json")
        with self.swap_refused("manuscript.json"):
            self.run_refused("retarget", "--venue", "JOE", "--target-journal", "Journal of Examples",
                             "--journal-profile", self.PROFILE)
        self.assertEqual(sha(self.root / "manuscript.json"), old_config)
        self.assertIn("manuscript.json.new", proj.collect_status(proj.Project(self.root), git=False)["leftovers"])
        self.assertIn("Retargeted to Journal of Examples", self.retarget())
        self.assertEqual(read_json(self.root / "manuscript.json")["venue"], "JOE")
        backups = sorted(p.relative_to(self.root).as_posix() for p in (self.root / proj.ARCHIVE).rglob("manuscript.json"))
        self.assertEqual(backups, [f"{proj.ARCHIVE}/{self.today}/Retarget_from_TST/manuscript.json"])
        self.assertEqual(sha(self.root / backups[0]), old_config)
        self.assertEqual(self.leftovers_outside_archive(), [])
        self.assertEqual(proj.collect_verification(proj.Project(self.root))["status"], "PASS")

    def test_retarget_refusals_change_nothing(self):
        before = snapshot(self.root)
        self.assertIn("--venue", self.run_refused("retarget", "--venue", "J O E", "--target-journal", "X"))
        self.assertIn("outside the project", self.run_refused("retarget", "--venue", "JOE", "--target-journal", "X",
                                                              "--journal-profile", self.base / "elsewhere.json"))
        self.assertEqual(snapshot(self.root), before)
        out = self.run_ok("retarget", "--venue", "JOE", "--target-journal", "Journal of Examples", "--dry-run")
        self.assertIn("journal_profile still names", out)


class MachinePathTests(ProjectCase):
    """Versioned records (indexes, ledger, generated files) never hold this machine's paths."""

    def assert_no_machine_path(self, root: Path):
        markers = set()
        for folder in (self.base, Path.home()):
            text = str(folder)
            markers |= {text, text.replace("\\", "/"), json.dumps(text)[1:-1]}
        checked = 0
        for path in root.rglob("*"):
            rel = path.relative_to(root).as_posix()
            if not path.is_file() or rel.startswith(".git/") or path.suffix.lower() in (".docx", ".pdf", ".png"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            checked += 1
            for marker in markers:
                self.assertNotIn(marker.casefold(), text.casefold(), f"{rel} holds {marker}")
            self.assertNotRegex(text, r"(?<![A-Za-z0-9])[A-Za-z]:(?:\\\\|\\|/)", rel)
        self.assertGreater(checked, 10)

    def test_records_keep_no_machine_path(self):
        self.init()
        self.run_ok("intake", self.draft, "--as-version", "V01", "--note", self.draft)
        [entry] = read_json(self.root / proj.VERSION_INDEX)
        self.assertEqual((entry["source_name"], entry["note"]), (self.draft.name, self.draft.name))
        saved = self.base / "received" / "author save.docx"
        saved.write_bytes(msw_fixtures.make_docx(multi_chunk=True))
        self.run_ok("author-save", saved)
        intake, save = [e for e in ledger(self.root) if e["event"] in ("intake", "author_save")
                        and e["status"] == "pending"]
        self.assertEqual((intake["source"], intake["external"]), (self.draft.name, True))
        self.assertEqual((save["source"], save["external"]), (saved.name, True))
        # A failed move: the OSError names absolute paths; the ledger keeps them relative.
        note = self.root / "90_Agent_Work" / "Scratch" / "note.txt"
        note.write_text("synthetic\n", encoding="utf-8")
        target = self.root / proj.ARCHIVE / self.today / "Old" / "90_Agent_Work" / "Scratch" / "note.txt"
        error = FileNotFoundError(2, "No such file (simulated)", str(note), None, str(target))
        with mock.patch.object(proj.os, "rename", side_effect=error):
            self.run_refused("archive", "apply", "--reason", "Old", note)
        failed = [e for e in ledger(self.root) if e["event"] == "move" and e["status"] == "failed"]
        self.assertEqual(failed[0]["error"], "No such file (simulated): 90_Agent_Work/Scratch/note.txt -> "
                                             f"{proj.ARCHIVE}/{self.today}/Old/90_Agent_Work/Scratch/note.txt")
        self.assert_no_machine_path(self.root)

    def test_scrub_makes_paths_relative_or_bare(self):
        self.init()
        project = proj.Project(self.root)
        inside = str(self.root / "03_Data" / "table.csv")
        outside = str(self.base / "received" / "draft.docx")
        self.assertEqual(proj._scrub(project, {"a": inside, "b": [outside], "n": 3}),
                         {"a": "03_Data/table.csv", "b": ["draft.docx"], "n": 3})
        text = proj._scrub(project, f"cannot open '{inside}' (copied from {repr(outside)})")
        self.assertIn("03_Data", text)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn(json.dumps(str(self.root))[1:-1], text)
        if Path(outside).is_relative_to(Path.home()):
            self.assertNotIn(str(Path.home()), text)
            self.assertNotIn(json.dumps(str(Path.home()))[1:-1], text)
        self.assertEqual(proj._scrub(project, "V02 tracked verified"), "V02 tracked verified")


class LongPathTests(unittest.TestCase):
    """A project folder deep enough that pass, version and archive paths pass 260 characters. On
    Windows without long-path support every file operation must use the extended-length form."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self._tmp.name)
        room = max(20, 200 - len(str(self.base)) - 2)
        self.root = self.base / ("Deep " + "d" * (room // 2 - 5)) / ("Example project " + "p" * (room // 2 - 16))
        self.draft = self.base / "draft.docx"
        self.draft.write_bytes(msw_fixtures.make_docx())

    def tearDown(self):
        shutil.rmtree(pathutil.fs(self.base), ignore_errors=True)
        self._tmp.cleanup()

    def lsha(self, path) -> str:
        with open(pathutil.fs(path), "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def test_the_whole_workflow_in_a_deep_folder(self):
        self.assertGreaterEqual(len(str(self.root)), 190)
        code, out, err = cli("init", self.root, "--title", "Deep folder study", "--venue", "TST", "--author", AUTHOR,
                             "--initials", "GS")
        self.assertEqual(code, 0, out + err)
        if os.name == "nt" and not pathutil.long_paths_enabled():
            self.assertIn("WARNING: the project folder path is", out)
        run = lambda *argv: cli(*argv, "--project", self.root)   # noqa: E731
        code, out, err = run("intake", self.draft, "--as-version", "V01")
        self.assertEqual(code, 0, out + err)
        code, out, err = run("pass", "new", "--slug", "methods text")
        self.assertEqual(code, 0, out + err)
        pass_dir = self.root / proj.PASSES / "V02_methods_text"
        with open(pathutil.fs(pass_dir / "pass.json"), encoding="utf-8") as handle:
            info = json.load(handle)
        with open(pathutil.fs(pass_dir / "edits.json"), "w", encoding="utf-8") as handle:
            json.dump({"edits": NamingAndProfileTests.EDIT}, handle)
        build = pass_dir / "build01" / info["build_name"]
        self.assertGreater(len(str(build)), 255)
        code, out, err = cli("build", self.root / info["source"]["path"], pass_dir / "edits.json", "--out", build,
                             "--author", AUTHOR)
        self.assertEqual(code, 0, out + err)
        with open(pathutil.fs(pass_dir / "proofs" / "V02 proof.pdf"), "xb") as handle:
            handle.write(b"%PDF-1.4\n% synthetic proof\n%%EOF\n")
        code, out, err = run("release", "apply", "--pass", pass_dir)
        self.assertEqual(code, 0, out + err)
        released = self.root / proj.VERSIONS / "V02" / f"{info['date']} GS TST V02 methods text tracked verified.docx"
        self.assertGreater(len(str(released)), 260)
        self.assertEqual(self.lsha(released), self.lsha(build))
        scratch = self.root / "90_Agent_Work" / "Scratch" / ("old run " + "r" * 30)
        os.makedirs(pathutil.fs(scratch))
        with open(pathutil.fs(scratch / ("notes " + "n" * 40 + ".txt")), "x", encoding="utf-8") as handle:
            handle.write("synthetic\n")
        code, out, err = run("archive", "apply", "--reason", "Old_Scratch_Runs", scratch)
        self.assertEqual(code, 0, out + err)
        code, out, err = run("status")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Current: V02 tracked verified", out)
        self.assertNotIn("UNREADABLE", out)
        report = proj.collect_verification(proj.Project(self.root))
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["unreadable"], [])


if __name__ == "__main__":
    unittest.main()

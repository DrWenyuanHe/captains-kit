"""Run with: python -m unittest discover -s tests -v"""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "kit_install", Path(__file__).resolve().parents[1] / "scripts" / "install.py"
)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.source = self.root / "source"
        (self.source / "references").mkdir(parents=True)
        (self.source / "agents").mkdir()
        self.skill_text = (
            "---\nname: agentic-environment-setup\n"
            "description: Explicit setup and audit workflow.\n---\n\n# Setup\n"
        )
        (self.source / "SKILL.md").write_text(self.skill_text, encoding="utf-8")
        (self.source / "references" / "principles.md").write_text("Evidence", encoding="utf-8")
        (self.source / "agents" / "openai.yaml").write_text(
            "policy:\n  allow_implicit_invocation: false\n", encoding="utf-8"
        )

    def install(self, host="codex"):
        return installer.install(self.project, host, self.source)

    def link(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symlinks unavailable on this system: {error}")

    def test_codex_copies_whole_skill_and_preserves_source(self):
        destination = self.install()
        self.assertEqual(destination, self.project / ".agents" / "skills" / installer.SKILL_NAME)
        self.assertEqual((destination / "SKILL.md").read_text(encoding="utf-8"), self.skill_text)
        self.assertEqual((destination / "references" / "principles.md").read_text(), "Evidence")
        self.assertTrue((destination / "agents" / "openai.yaml").is_file())
        self.assertEqual((self.source / "SKILL.md").read_text(encoding="utf-8"), self.skill_text)
        self.assertEqual({p.name for p in self.project.iterdir()}, {".agents"})

    def test_claude_adds_manual_invocation_policy_only_to_copy(self):
        destination = self.install("claude")
        installed = (destination / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(destination, self.project / ".claude" / "skills" / installer.SKILL_NAME)
        self.assertIn("disable-model-invocation: true\n---\n", installed)
        self.assertEqual(installed.replace("disable-model-invocation: true\n", ""), self.skill_text)
        self.assertEqual((self.source / "SKILL.md").read_text(encoding="utf-8"), self.skill_text)
        self.assertTrue((destination / "references" / "principles.md").is_file())

    def test_cli_finds_source_relative_to_script_from_another_directory(self):
        kit = self.root / "kit"
        (kit / "scripts").mkdir(parents=True)
        script = kit / "scripts" / "install.py"
        shutil.copyfile(installer.__file__, script)
        shutil.copytree(self.source, kit / "skills" / installer.SKILL_NAME)
        result = subprocess.run(
            [sys.executable, str(script), "--project", str(self.project)],
            cwd=self.project, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.project / ".agents" / "skills" / installer.SKILL_NAME / "SKILL.md").is_file())

    def test_existing_install_and_user_edits_are_preserved(self):
        destination = self.install()
        custom = destination / "SKILL.md"
        custom.write_text("User changes", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(custom.read_text(), "User changes")

    def test_existing_empty_destination_is_preserved(self):
        destination = self.project / ".agents" / "skills" / installer.SKILL_NAME
        destination.mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(list(destination.iterdir()), [])

    def test_malformed_source_does_not_modify_project(self):
        for content in ("# No metadata", "---\nname: agentic-environment-setup\n", "---\nname: other\n---\n"):
            with self.subTest(content=content):
                (self.source / "SKILL.md").write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.install()
                self.assertEqual(list(self.project.iterdir()), [])

    def test_missing_project_is_not_created(self):
        missing = self.root / "missing"
        with self.assertRaises(ValueError):
            installer.install(missing, source=self.source)
        self.assertFalse(missing.exists())

    def test_relative_project_is_rejected(self):
        with self.assertRaises(ValueError):
            installer.install(Path("relative-project"), source=self.source)

    def test_home_and_filesystem_root_are_not_install_targets(self):
        with patch.object(Path, "home", return_value=self.project):
            with self.assertRaises(ValueError):
                self.install()
        with self.assertRaises(ValueError):
            installer.install(Path(self.project.anchor), source=self.source)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_overlapping_destination_is_rejected(self):
        with self.assertRaises(ValueError):
            installer.install(self.source, source=self.source)
        self.assertFalse((self.source / ".agents").exists())

    def test_destination_ancestor_link_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.link(self.project / ".agents", outside, directory=True)
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])

    def test_source_file_link_is_rejected(self):
        outside = self.root / "private.txt"
        outside.write_text("Do not copy", encoding="utf-8")
        self.link(self.source / "references" / "linked.txt", outside)
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(list(self.project.iterdir()), [])

    def test_broken_destination_link_is_rejected(self):
        skills = self.project / ".agents" / "skills"
        skills.mkdir(parents=True)
        self.link(skills / installer.SKILL_NAME, self.root / "missing", directory=True)
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / "missing").exists())

    def test_source_ancestor_link_is_rejected(self):
        linked = self.root / "linked-source"
        self.link(linked, self.source, directory=True)
        with self.assertRaises(ValueError):
            installer.install(self.project, source=linked)


if __name__ == "__main__":
    unittest.main()

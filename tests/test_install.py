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
sys.path.insert(0, str(Path(installer.__file__).parent))
from registry import content_hash, new_entry, read_registry, write_registry


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
        self.assertEqual(destination, self.project / ".agents" / "skills" / installer.DEFAULT_SKILL)
        self.assertEqual((destination / "SKILL.md").read_text(encoding="utf-8"), self.skill_text)
        self.assertEqual((destination / "references" / "principles.md").read_text(), "Evidence")
        self.assertTrue((destination / "agents" / "openai.yaml").is_file())
        self.assertEqual((self.source / "SKILL.md").read_text(encoding="utf-8"), self.skill_text)
        self.assertEqual({p.name for p in self.project.iterdir()}, {".agents"})

    def test_claude_adds_manual_invocation_policy_only_to_copy(self):
        destination = self.install("claude")
        installed = (destination / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(destination, self.project / ".claude" / "skills" / installer.DEFAULT_SKILL)
        self.assertIn("disable-model-invocation: true\n---\n", installed)
        self.assertEqual(installed.replace("disable-model-invocation: true\n", ""), self.skill_text)
        self.assertEqual((self.source / "SKILL.md").read_text(encoding="utf-8"), self.skill_text)
        self.assertTrue((destination / "references" / "principles.md").is_file())

    def library_script(self):
        kit = self.root / "kit"
        (kit / "scripts").mkdir(parents=True)
        script = kit / "scripts" / "install.py"
        shutil.copyfile(installer.__file__, script)
        shutil.copyfile(Path(installer.__file__).with_name("registry.py"), script.with_name("registry.py"))
        shutil.copytree(self.source, kit / "skills" / installer.DEFAULT_SKILL)
        write_registry(kit, {"schema_version": 1, "skills": {
            installer.DEFAULT_SKILL: new_entry("local", content_hash(kit / "skills" / installer.DEFAULT_SKILL)),
        }})
        return script

    def second_skill(self, directory=None):
        source = directory or self.root / "release-notes"
        shutil.copytree(self.source, source)
        (source / "SKILL.md").write_text(
            "---\nname: release-notes\ndescription: Draft release notes from changes.\n---\n"
            "[Reference](references/principles.md)\n", encoding="utf-8",
        )
        (source / "agents" / "openai.yaml").unlink()
        if directory is not None:
            kit = source.parent.parent
            data = read_registry(kit)
            data["skills"][source.name] = new_entry("local", content_hash(source))
            write_registry(kit, data)
        return source

    def test_cli_finds_source_relative_to_script_from_another_directory(self):
        script = self.library_script()
        result = subprocess.run(
            [sys.executable, str(script), "--project", str(self.project)],
            cwd=self.project, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.project / ".agents" / "skills" / installer.DEFAULT_SKILL / "SKILL.md").is_file())

    def test_cli_lists_multiple_skills_without_installing(self):
        script = self.library_script()
        self.second_skill(script.parent.parent / "skills" / "release-notes")
        result = subprocess.run(
            [sys.executable, str(script), "--list"],
            cwd=self.project, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [installer.DEFAULT_SKILL, "release-notes"])
        self.assertEqual(list(self.project.iterdir()), [])

    def test_cli_installs_selected_skill_beside_existing_install(self):
        existing = self.install()
        (existing / "SKILL.md").write_text("Local customization", encoding="utf-8")
        script = self.library_script()
        source = self.second_skill(script.parent.parent / "skills" / "release-notes")
        result = subprocess.run(
            [sys.executable, str(script), "--project", str(self.project), "--skill", "release-notes"],
            cwd=self.project, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        destination = existing.parent / "release-notes"
        self.assertEqual((destination / "SKILL.md").read_bytes(), (source / "SKILL.md").read_bytes())
        self.assertTrue((destination / "references" / "principles.md").is_file())
        self.assertEqual((existing / "SKILL.md").read_text(), "Local customization")

    def test_unknown_skill_fails_without_modifying_project(self):
        script = self.library_script()
        result = subprocess.run(
            [sys.executable, str(script), "--project", str(self.project), "--skill", "unknown"],
            cwd=self.project, capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_cli_refuses_unrecorded_library_edits_before_installing(self):
        script = self.library_script()
        source = script.parent.parent / "skills" / installer.DEFAULT_SKILL / "SKILL.md"
        source.write_text(source.read_text() + "\nUnrecorded edits.\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(script), "--project", str(self.project)],
            cwd=self.project, capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecorded changes", result.stderr)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_automatic_skills_remain_automatic_on_both_hosts(self):
        source = self.second_skill()
        for index, policy in enumerate((None, "interface:\n  display_name: Release notes\n", "policy:\n  allow_implicit_invocation: true\n")):
            if policy is not None:
                (source / "agents" / "openai.yaml").write_text(policy, encoding="utf-8")
            for host in ("codex", "claude"):
                with self.subTest(policy=policy, host=host):
                    project = self.root / f"project-{index}-{host}"
                    project.mkdir()
                    destination = installer.install(project, host, source, skill_name="release-notes")
                    self.assertEqual((destination / "SKILL.md").read_bytes(), (source / "SKILL.md").read_bytes())

    def test_selected_manual_skill_translates_policy_for_claude(self):
        source = self.second_skill()
        (source / "agents" / "openai.yaml").write_text(
            "interface:\n  display_name: Release notes\npolicy:\n  allow_implicit_invocation: false\n",
            encoding="utf-8",
        )
        destination = installer.install(self.project, "claude", source, skill_name="release-notes")
        self.assertIn("disable-model-invocation: true\n---\n", (destination / "SKILL.md").read_text())
        self.assertNotIn("disable-model-invocation", (source / "SKILL.md").read_text())

    def test_invalid_names_and_mismatched_metadata_do_not_modify_project(self):
        for name in ("../outside", "nested/skill", "C:/outside", "BadName", "two--hyphens", "a" * 64, "", "release-notes"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    installer.install(self.project, source=self.source, skill_name=name)
                self.assertEqual(list(self.project.iterdir()), [])

    def test_invalid_policy_does_not_modify_project(self):
        (self.source / "agents" / "openai.yaml").write_text(
            "policy:\n  allow_implicit_invocation: maybe\n", encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(list(self.project.iterdir()), [])

    def test_existing_install_and_user_edits_are_preserved(self):
        destination = self.install()
        custom = destination / "SKILL.md"
        custom.write_text("User changes", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(custom.read_text(), "User changes")

    def test_existing_empty_destination_is_preserved(self):
        destination = self.project / ".agents" / "skills" / installer.DEFAULT_SKILL
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
        self.link(skills / installer.DEFAULT_SKILL, self.root / "missing", directory=True)
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

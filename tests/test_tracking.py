"""Exercise provenance and update guards against real, disposable Git history."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check import check_repository
from registry import REGISTRY, content_hash, read_registry, tracking_errors, validate_registry
import skills
from install import install, validate_invocation, UNSLOP_GUARD


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "skills").mkdir()
        self.upstream = self.root / "upstream"
        self.upstream.mkdir()
        self.git("init", "--quiet", "--initial-branch=main")
        self.source = self.upstream / "skills" / "example"
        (self.source / "references").mkdir(parents=True)
        (self.source / "SKILL.md").write_text(
            "---\nname: example\ndescription: A reusable example workflow.\n---\n"
            "Follow [details](references/details.md).\n", encoding="utf-8",
        )
        (self.source / "references" / "details.md").write_text("# Details\nOriginal guidance.\n", encoding="utf-8")
        (self.upstream / "LICENSE").write_text("Example upstream license notice.\n", encoding="utf-8")
        self.initial_commit = self.commit()
        self.local = self.root / "skills" / "example"

    def git(self, *arguments):
        result = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-c", "core.hooksPath=/dev/null",
             "-c", "user.name=Kit Test", "-c", "user.email=kit-test@example.invalid",
             "-c", "commit.gpgsign=false", "-C", str(self.upstream), *arguments],
            capture_output=True, check=True,
        )
        return result.stdout.decode().strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Update fixture")
        return self.git("rev-parse", "HEAD")

    def import_example(self):
        skills.import_skill(self.root, "example", "./upstream", "skills/example", "main")
        return self.entry()

    def entry(self):
        return read_registry(self.root)["skills"]["example"]

    def change_upstream(self, text="New upstream guidance.\n"):
        (self.source / "references" / "details.md").write_text(text, encoding="utf-8")
        return self.commit()

    def test_import_records_exact_source_and_preserves_license(self):
        entry = self.import_example()
        self.assertEqual(entry["source"]["commit"], self.initial_commit)
        self.assertEqual(entry["source"]["path"], "skills/example")
        self.assertEqual(entry["first_imported_at"], entry["added_at"])
        self.assertIsNone(entry["last_checked_at"])
        self.assertIsNone(entry["last_updated_at"])
        self.assertEqual((self.local / "upstream-licenses" / "LICENSE").read_bytes(), (self.upstream / "LICENSE").read_bytes())
        self.assertEqual(check_repository(self.root), [])
        self.assertEqual(list((self.root / ".captains-kit-cache").iterdir()), [])

    def test_import_is_stable_across_git_autocrlf_settings(self):
        for path in (self.source / "SKILL.md", self.source / "references" / "details.md", self.upstream / "LICENSE"):
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
        (self.source / "references" / "details.md").write_bytes(b"# Details\nLF-only fixture.\n")
        self.commit()
        settings = {
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.autocrlf",
            "GIT_CONFIG_VALUE_0": "true",
        }
        with patch.dict(skills.os.environ, settings):
            entry = self.import_example()
        self.assertEqual((self.local / "SKILL.md").read_bytes(), (self.source / "SKILL.md").read_bytes())
        self.assertEqual((self.local / "upstream-licenses" / "LICENSE").read_bytes(), (self.upstream / "LICENSE").read_bytes())
        settings["GIT_CONFIG_VALUE_0"] = "false"
        with patch.dict(skills.os.environ, settings):
            result = skills.check_updates(self.root)[0]
        self.assertEqual(result["status"], "current")
        self.assertEqual(result["sha256"], entry["source"]["sha256"])

    def test_import_preserves_ancestor_plugin_license(self):
        notice = self.upstream / "skills" / "LICENSE"
        notice.write_text("Plugin-specific license.\n", encoding="utf-8")
        self.commit()
        self.import_example()
        self.assertEqual((self.local / "upstream-licenses" / "skills" / "LICENSE").read_bytes(), notice.read_bytes())
        self.assertEqual(skills.check_updates(self.root)[0]["status"], "current")

    def test_unslop_guard_survives_import_checks_and_reviewed_updates(self):
        (self.source / "SKILL.md").write_text(
            "---\nname: unslop\ndescription: Cut AI tells. Must always apply.\n"
            "disable-model-invocation: true\n---\n\n# Unslop\n\nEdit text.\n",
            encoding="utf-8",
        )
        self.commit()
        skills.import_skill(self.root, "unslop", "./upstream", "skills/example", "main")
        local = self.root / "skills" / "unslop"
        initial = read_registry(self.root)["skills"]["unslop"]
        self.assertNotEqual(initial["source"]["sha256"], initial["content_sha256"])
        self.assertEqual([event["event"] for event in initial["history"]], ["imported", "edited"])
        self.assertFalse(validate_invocation(local))
        self.assertNotIn("Must always apply", (local / "SKILL.md").read_text())
        self.assertEqual(skills.check_updates(self.root)[0]["status"], "current")
        for host in ("codex", "claude"):
            project = self.root / host
            project.mkdir()
            installed = install(project, host, source=local, skill_name="unslop")
            text = (installed / "SKILL.md").read_text()
            self.assertIn(UNSLOP_GUARD, text)
            self.assertEqual("disable-model-invocation: true\n---" in text, host == "claude")

        reviewed = self.change_upstream("New upstream guidance.\n")
        self.assertEqual(skills.check_updates(self.root)[0]["status"], "update_available")
        with self.assertRaisesRegex(ValueError, "customizations"):
            skills.update_skill(self.root, "unslop", reviewed)

        policy = local / "agents" / "openai.yaml"
        original_policy = policy.read_bytes()
        policy.write_text("policy:\n  allow_implicit_invocation: true\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "must be false"):
            skills.record_edits(self.root, "unslop", "Try to enable automatic use")
        with self.assertRaisesRegex(ValueError, "must be false"):
            skills.update_skill(self.root, "unslop", reviewed, accept_merged=True)
        policy.write_bytes(original_policy)
        (local / "references" / "details.md").write_text("New upstream guidance.\n", encoding="utf-8")
        skills.update_skill(self.root, "unslop", reviewed, accept_merged=True, note="Merge guidance and keep guard")
        entry = read_registry(self.root)["skills"]["unslop"]
        self.assertEqual(entry["first_imported_at"], initial["first_imported_at"])
        self.assertEqual(entry["source"]["commit"], reviewed)
        self.assertEqual(check_repository(self.root), [])

    def test_unslop_installer_rejects_removed_policy_description_or_guard(self):
        local = self.root / "skills" / "unslop"
        local.mkdir()
        entrypoint = local / "SKILL.md"
        entrypoint.write_text("---\nname: unslop\ndescription: Edit prose.\n---\n\n# Unslop\n\nEdit.\n", encoding="utf-8")
        skills.apply_unslop_guard(local)
        original = entrypoint.read_bytes()
        policy = local / "agents" / "openai.yaml"
        original_policy = policy.read_bytes()
        project = self.root / "project"
        project.mkdir()
        for kind in ("missing-policy", "automatic", "description", "body"):
            with self.subTest(kind=kind):
                entrypoint.write_bytes(original)
                policy.write_bytes(original_policy)
                if kind == "missing-policy":
                    policy.unlink()
                elif kind == "automatic":
                    policy.write_text("policy:\n  allow_implicit_invocation: true\n", encoding="utf-8")
                else:
                    text = entrypoint.read_text()
                    text = text.replace("Never select this skill automatically.", "Always apply.") if kind == "description" else text.replace(UNSLOP_GUARD, "")
                    entrypoint.write_text(text, encoding="utf-8")
                for host in ("codex", "claude"):
                    with self.assertRaises(ValueError):
                        install(project, host, source=local, skill_name="unslop")
                self.assertEqual(list(project.iterdir()), [])

    def test_checks_ignore_unrelated_commits_and_preserve_skill_files(self):
        initial = self.import_example()
        before = content_hash(self.local)
        (self.upstream / "unrelated.txt").write_text("Unrelated change", encoding="utf-8")
        unrelated = self.commit()
        result = skills.check_updates(self.root)
        self.assertEqual(result[0]["status"], "current")
        self.assertEqual(result[0]["commit"], unrelated)
        relevant = self.change_upstream()
        result = skills.check_updates(self.root)
        self.assertEqual(result[0]["status"], "update_available")
        self.assertEqual(result[0]["commit"], relevant)
        entry = self.entry()
        self.assertEqual(entry["first_imported_at"], initial["first_imported_at"])
        self.assertEqual(entry["source"]["commit"], self.initial_commit)
        self.assertIsNone(entry["last_updated_at"])
        self.assertEqual(entry["last_checked_at"], entry["last_check_attempt_at"])
        self.assertEqual(content_hash(self.local), before)

    def test_failed_check_preserves_last_success_and_records_error(self):
        self.import_example()
        skills.check_updates(self.root)
        previous = self.entry()
        with patch.object(skills, "snapshot", side_effect=OSError("Source unavailable")):
            result = skills.check_updates(self.root)
        entry = self.entry()
        self.assertEqual(result[0]["status"], "error")
        self.assertEqual(entry["last_checked_at"], previous["last_checked_at"])
        self.assertGreater(entry["last_check_attempt_at"], previous["last_check_attempt_at"])
        self.assertEqual(entry["source"], previous["source"])
        self.assertEqual(skills.status(self.root)["skills"][0]["upstream"], "error")
        self.assertEqual(tracking_errors(self.root), [])

    def test_update_uses_reviewed_commit_and_keeps_first_import(self):
        (self.source / "obsolete.txt").write_text("Remove on update", encoding="utf-8")
        self.commit()
        initial = self.import_example()
        with self.assertRaisesRegex(ValueError, "Check or diff"):
            skills.update_skill(self.root, "example", self.initial_commit)
        (self.source / "obsolete.txt").unlink()
        (self.source / "new.txt").write_text("New resource", encoding="utf-8")
        reviewed = self.change_upstream("Reviewed guidance.\n")
        diff = skills.diff_skill(self.root, "example")
        self.assertIn("+Reviewed guidance.", diff)
        self.change_upstream("Later unreviewed guidance.\n")
        skills.update_skill(self.root, "example", reviewed)
        entry = self.entry()
        self.assertEqual((self.local / "references" / "details.md").read_text(), "Reviewed guidance.\n")
        self.assertFalse((self.local / "obsolete.txt").exists())
        self.assertTrue((self.local / "new.txt").is_file())
        self.assertEqual(entry["source"]["commit"], reviewed)
        self.assertEqual(entry["first_imported_at"], initial["first_imported_at"])
        self.assertIsNotNone(entry["last_updated_at"])
        self.assertEqual(skills.status(self.root)["skills"][0]["upstream"], "current")
        self.assertEqual(check_repository(self.root), [])

    def test_update_refuses_customizations_and_can_record_a_manual_merge(self):
        self.import_example()
        local_file = self.local / "references" / "details.md"
        local_file.write_text("My customization.\n", encoding="utf-8")
        skills.record_edits(self.root, "example", "Customize guidance")
        commit = self.change_upstream()
        skills.check_updates(self.root)
        before = (self.root / REGISTRY).read_bytes()
        with self.assertRaisesRegex(ValueError, "customizations"):
            skills.update_skill(self.root, "example", commit)
        self.assertEqual((self.root / REGISTRY).read_bytes(), before)
        self.assertEqual(local_file.read_text(), "My customization.\n")
        local_file.write_text("New upstream guidance.\nMy customization.\n", encoding="utf-8")
        skills.update_skill(self.root, "example", commit, accept_merged=True, note="Keep personal guidance")
        self.assertEqual(local_file.read_text(), "New upstream guidance.\nMy customization.\n")
        row = skills.status(self.root)["skills"][0]
        self.assertEqual(row["local"], "customized")
        self.assertEqual(row["upstream"], "current")
        self.assertEqual(check_repository(self.root), [])

    def test_no_op_update_does_not_change_last_updated(self):
        self.import_example()
        skills.check_updates(self.root)
        before = (self.root / REGISTRY).read_bytes()
        skills.update_skill(self.root, "example", self.initial_commit)
        self.assertEqual((self.root / REGISTRY).read_bytes(), before)

    def test_local_registration_and_edit_guards(self):
        self.local.mkdir()
        (self.local / "SKILL.md").write_bytes((self.source / "SKILL.md").read_bytes())
        (self.local / "references").mkdir()
        local_file = self.local / "references" / "details.md"
        local_file.write_text("Local guidance.\n", encoding="utf-8")
        self.assertTrue(check_repository(self.root))
        skills.register_local(self.root, "example")
        self.assertEqual(check_repository(self.root), [])
        self.assertIsNone(self.entry()["first_imported_at"])
        skills.check_updates(self.root)
        self.assertIsNone(self.entry()["last_check_attempt_at"])
        local_file.write_text("Edited local guidance.\n", encoding="utf-8")
        self.assertTrue(any("unrecorded" in error for error in check_repository(self.root)))
        skills.record_edits(self.root, "example", "Improve instructions")
        self.assertEqual(check_repository(self.root), [])
        self.assertIsNotNone(self.entry()["last_updated_at"])
        before = (self.root / REGISTRY).read_bytes()
        skills.record_edits(self.root, "example", "No changes")
        self.assertEqual((self.root / REGISTRY).read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "already registered"):
            skills.register_local(self.root, "example")

    def test_orphan_and_unregistered_skill_folders_fail_validation(self):
        self.import_example()
        other = self.root / "skills" / "other"
        other.mkdir()
        (other / "SKILL.md").write_text("---\nname: other\ndescription: Another workflow.\n---\n", encoding="utf-8")
        self.assertTrue(any("missing tracking" in error for error in check_repository(self.root)))
        self.local.rename(self.root / "moved-example")
        self.assertTrue(any("no skill folder" in error for error in tracking_errors(self.root)))

    def test_registry_rejects_inconsistent_timestamps_and_malformed_history(self):
        self.import_example()
        data = read_registry(self.root)
        for field, value in (
            ("first_imported_at", None), ("last_updated_at", "2020-01-01T00:00:00Z"),
            ("history", []), ("source", None), ("content_sha256", "0" * 64),
        ):
            with self.subTest(field=field):
                malformed = copy.deepcopy(data)
                malformed["skills"]["example"][field] = value
                with self.assertRaises(ValueError):
                    validate_registry(self.root, malformed)
        (self.root / REGISTRY).write_text('{"schema_version": 1, "skills": {}, "skills": {}}', encoding="utf-8")
        self.assertTrue(any("Duplicate" in error for error in tracking_errors(self.root)))

    def test_import_never_overwrites_existing_content(self):
        self.import_example()
        before = (self.root / REGISTRY).read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.import_example()
        self.assertEqual((self.root / REGISTRY).read_bytes(), before)

    def test_update_rolls_back_folder_if_registry_save_fails(self):
        self.import_example()
        commit = self.change_upstream()
        skills.check_updates(self.root)
        before_registry = (self.root / REGISTRY).read_bytes()
        before_files = content_hash(self.local)
        with patch.object(skills, "write_registry", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(OSError):
                skills.update_skill(self.root, "example", commit)
        self.assertEqual((self.root / REGISTRY).read_bytes(), before_registry)
        self.assertEqual(content_hash(self.local), before_files)

    def test_import_failure_leaves_no_skill_or_registry(self):
        with patch.object(skills, "write_registry", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(OSError):
                self.import_example()
        self.assertFalse(self.local.exists())
        self.assertFalse((self.root / REGISTRY).exists())

    def test_unsafe_source_paths_and_urls_are_rejected(self):
        for repository, path in (
            ("./../outside", "skills/example"), ("./upstream", "../outside"),
            ("ext::run-command", "skills/example"),
            ("https://secret@example.com/repo.git", "skills/example"),
        ):
            with self.subTest(repository=repository, path=path):
                with self.assertRaises(ValueError):
                    skills.import_skill(self.root, "example", repository, path, "main")
        self.assertFalse(self.local.exists())

    def test_status_is_offline_and_does_not_advance_timestamps(self):
        self.import_example()
        before = (self.root / REGISTRY).read_bytes()
        with patch.object(skills, "snapshot", side_effect=AssertionError("Unexpected network access")):
            result = skills.status(self.root)
        self.assertEqual(result["skills"][0]["upstream"], "never_checked")
        self.assertEqual((self.root / REGISTRY).read_bytes(), before)

    def test_cli_import_check_update_and_validation_from_another_directory(self):
        directory = self.root / "scripts"
        directory.mkdir()
        for name in ("skills.py", "registry.py", "install.py", "check.py"):
            shutil.copyfile(Path(skills.__file__).with_name(name), directory / name)

        def run(*args, script="skills.py"):
            result = subprocess.run(
                [sys.executable, str(directory / script), *args], cwd=self.upstream,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        run("import", "example", "--repo", "./upstream", "--path", "skills/example", "--ref", "main")
        state = json.loads(run("status", "--json"))
        self.assertEqual(state["skills"][0]["commit"], self.initial_commit)
        commit = self.change_upstream()
        observed = json.loads(run("check-updates", "example"))
        self.assertEqual(observed[0]["commit"], commit)
        run("update", "example", "--commit", commit)
        self.assertIn("Last updated:", run("status"))
        run(script="check.py")

    def test_import_rejects_git_symlinks_without_following_them(self):
        blob = subprocess.run(
            ["git", "-C", str(self.upstream), "hash-object", "-w", "--stdin"],
            input=b"../../../outside", capture_output=True, check=True,
        ).stdout.decode().strip()
        self.git("update-index", "--add", "--cacheinfo", "120000", blob, "skills/example/references/linked")
        self.git("commit", "--quiet", "-m", "Add unsupported symlink")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.import_example()
        self.assertFalse(self.local.exists())
        self.assertFalse((self.root / REGISTRY).exists())


class PortableHashTests(unittest.TestCase):
    def test_portable_hash_contract_uses_case_sensitive_path_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "a.md").write_bytes(b"lower\n")
            (root / "Z.md").write_bytes(b"upper\n")
            # This fixed vector has the same result on Windows and Linux.
            self.assertEqual(content_hash(root), "259037c9b391238461d34c972be30f17aa90d202d13eb87dea4d80277366ad17")


if __name__ == "__main__":
    unittest.main()

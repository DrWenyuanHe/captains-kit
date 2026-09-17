"""Content validation; registry guards are exercised in test_tracking.py."""

from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check import check_content as check_repository, SETUP_SOURCE_DIGESTS, DEFAULT_SKILL


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.skill = self.root / "skills" / DEFAULT_SKILL
        (self.skill / "agents").mkdir(parents=True)
        (self.skill / "references" / "sources").mkdir(parents=True)
        (self.skill / "assets" / "templates").mkdir(parents=True)
        self.write("README.md", f"[Skill](skills/{DEFAULT_SKILL}/SKILL.md)\n")
        self.entrypoint = self.skill / "SKILL.md"
        self.entrypoint.write_text(
            f"---\nname: {DEFAULT_SKILL}\ndescription: Explicit setup or audit.\n---\n"
            "[Playbook](references/setup.md#start)\n", encoding="utf-8",
        )
        (self.skill / "references" / "setup.md").write_text("# Start\n", encoding="utf-8")
        self.policy = self.skill / "agents" / "openai.yaml"
        self.policy.write_text("policy:\n  allow_implicit_invocation: false\n", encoding="utf-8")
        for name in SETUP_SOURCE_DIGESTS:
            (self.skill / "references" / "sources" / name).write_text("# Digest\n", encoding="utf-8")

    def write(self, relative, text):
        (self.root / relative).write_text(text, encoding="utf-8")

    def add_skill(self, name="release-notes"):
        skill = self.root / "skills" / name
        (skill / "references").mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Draft release notes.\n---\n"
            "[Format](references/format.md)\n", encoding="utf-8",
        )
        (skill / "references" / "format.md").write_text("# Format\n", encoding="utf-8")
        return skill

    def test_new_skill_needs_no_setup_specific_policy_or_digests(self):
        skill = self.add_skill()
        self.assertEqual(check_repository(self.root), [])
        (skill / "agents").mkdir()
        policy = skill / "agents" / "openai.yaml"
        for content in (
            "interface:\n  display_name: Release notes\n",
            "policy:\n  allow_implicit_invocation: true\n",
            "policy: # Explicit invocation\n  allow_implicit_invocation: false # Manual\n",
        ):
            with self.subTest(content=content):
                policy.write_text(content, encoding="utf-8")
                self.assertEqual(check_repository(self.root), [])

    def test_invalid_second_skill_is_checked(self):
        skill = self.add_skill()
        (skill / "SKILL.md").write_text("---\nname: wrong-name\ndescription: Draft notes.\n---\n", encoding="utf-8")
        errors = check_repository(self.root)
        self.assertTrue(any("release-notes" in error for error in errors), errors)

    def test_selected_check_excludes_other_skills_and_root_docs(self):
        self.add_skill()
        self.write("README.md", "[Broken](missing.md)\n")
        self.policy.write_text("policy:\n  allow_implicit_invocation: true\n", encoding="utf-8")
        self.assertTrue(check_repository(self.root))
        self.assertEqual(check_repository(self.root, "release-notes"), [])
        self.assertTrue(check_repository(self.root, DEFAULT_SKILL))

    def test_unknown_or_invalid_selected_skill_fails(self):
        for name in ("unknown", "../outside", "BadName"):
            with self.subTest(name=name):
                self.assertTrue(check_repository(self.root, name))

    def test_incomplete_skill_folder_fails(self):
        (self.root / "skills" / "incomplete").mkdir()
        self.assertTrue(check_repository(self.root))

    def test_empty_library_fails(self):
        empty = self.root / "empty"
        (empty / "skills").mkdir(parents=True)
        self.assertTrue(check_repository(empty))

    def test_second_skill_cannot_link_to_sibling_skill(self):
        skill = self.add_skill()
        with (skill / "SKILL.md").open("a", encoding="utf-8") as entrypoint:
            entrypoint.write(f"[Shared setup](../{DEFAULT_SKILL}/SKILL.md)\n")
        errors = check_repository(self.root)
        self.assertTrue(any("escapes" in error for error in errors), errors)

    def test_second_skill_placeholders_fail(self):
        skill = self.add_skill()
        (skill / "references" / "format.md").write_text("TODO: finish\n", encoding="utf-8")
        self.assertTrue(check_repository(self.root))

    def test_malformed_policy_in_second_skill_fails(self):
        skill = self.add_skill()
        (skill / "agents").mkdir()
        for content in (
            "policy:\n  allow_implicit_invocation: maybe\n",
            "policy:\n  allow_implicit_invocation: true\n  allow_implicit_invocation: false\n",
            "policy:\n  allow_implicit_invocation: true\npolicy:\n  allow_implicit_invocation: false\n",
            "interface:\n  allow_implicit_invocation: false\n",
            'policy:\n  allow_implicit_invocation: "false"\n',
            "policy: {allow_implicit_invocation: false}\n",
        ):
            with self.subTest(content=content):
                (skill / "agents" / "openai.yaml").write_text(content, encoding="utf-8")
                self.assertTrue(check_repository(self.root))

    def test_valid_files_and_fenced_examples_pass(self):
        with self.entrypoint.open("a", encoding="utf-8") as entrypoint:
            entrypoint.write(
                "\n```md\n[Example](missing-example.md)\n```\n"
                "~~~md\n[Example](also-missing.md)\n~~~\n"
                "[External](https://example.invalid/page)\n[Anchor](#mode)\n"
            )
        self.assertEqual(check_repository(self.root), [])

    def test_missing_local_file_fails(self):
        self.write("README.md", "[Broken](missing.md)\n")
        self.assertTrue(check_repository(self.root))

    def test_existing_file_outside_skill_still_fails_for_skill_link(self):
        self.entrypoint.write_text(self.entrypoint.read_text() + "[Root](../../README.md)\n", encoding="utf-8")
        self.assertTrue(check_repository(self.root))

    def test_manual_policy_false_is_required_in_policy_block(self):
        for content in (
            "policy:\n  allow_implicit_invocation: true\n",
            "interface:\n  allow_implicit_invocation: false\n",
            "policy:\n  allow_implicit_invocation: false\n  allow_implicit_invocation: true\n",
        ):
            with self.subTest(content=content):
                self.policy.write_text(content, encoding="utf-8")
                self.assertTrue(check_repository(self.root))

    def test_required_frontmatter_fields_are_checked(self):
        self.entrypoint.write_text(f"---\nname: {DEFAULT_SKILL}\n---\n", encoding="utf-8")
        self.assertTrue(check_repository(self.root))

    def test_imported_literal_and_folded_descriptions(self):
        skill = self.add_skill("humanizer")
        entrypoint = skill / "SKILL.md"
        for marker in ("|", ">", "|-", ">+"):
            with self.subTest(marker=marker):
                entrypoint.write_text(
                    f"---\nname: humanizer\ndescription: {marker}\n"
                    "  Rewrite prose.\n  Preserve facts.\nmetadata:\n  version: '3.0.0'\n---\n",
                    encoding="utf-8",
                )
                self.assertEqual(check_repository(self.root), [])
        for description in ("|\n", ">-\n  \n", "|\nmetadata:\n  version: '3.0.0'\n"):
            entrypoint.write_text(f"---\nname: humanizer\ndescription: {description}---\n", encoding="utf-8")
            self.assertTrue(check_repository(self.root))

    def test_missing_digest_fails(self):
        (self.skill / "references" / "sources" / SETUP_SOURCE_DIGESTS[0]).unlink()
        self.assertTrue(check_repository(self.root))

    def test_operational_placeholders_fail_but_templates_are_exempt(self):
        template = self.skill / "assets" / "templates" / "example.md"
        template.write_text("TODO: replace with project evidence\n", encoding="utf-8")
        self.assertEqual(check_repository(self.root), [])
        (self.skill / "references" / "setup.md").write_text("TODO: finish setup\n", encoding="utf-8")
        self.assertTrue(check_repository(self.root))

    def test_angle_brackets_and_encoded_spaces_are_supported(self):
        self.write("with spaces.md", "# Example\n")
        self.write("README.md", "[Example](<with spaces.md>)\n[Example](with%20spaces.md)\n")
        self.assertEqual(check_repository(self.root), [])


if __name__ == "__main__":
    unittest.main()

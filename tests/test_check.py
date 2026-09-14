"""Behavioral checks for the kit's dependency-free repository validator."""

from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check import check_repository, REQUIRED_DIGESTS, SKILL_NAME


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.skill = self.root / "skills" / SKILL_NAME
        (self.skill / "agents").mkdir(parents=True)
        (self.skill / "references" / "sources").mkdir(parents=True)
        (self.skill / "assets" / "templates").mkdir(parents=True)
        self.write("README.md", f"[Skill](skills/{SKILL_NAME}/SKILL.md)\n")
        self.entrypoint = self.skill / "SKILL.md"
        self.entrypoint.write_text(
            f"---\nname: {SKILL_NAME}\ndescription: Explicit setup or audit.\n---\n"
            "[Playbook](references/setup.md#start)\n", encoding="utf-8",
        )
        (self.skill / "references" / "setup.md").write_text("# Start\n", encoding="utf-8")
        self.policy = self.skill / "agents" / "openai.yaml"
        self.policy.write_text("policy:\n  allow_implicit_invocation: false\n", encoding="utf-8")
        for name in REQUIRED_DIGESTS:
            (self.skill / "references" / "sources" / name).write_text("# Digest\n", encoding="utf-8")

    def write(self, relative, text):
        (self.root / relative).write_text(text, encoding="utf-8")

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
        self.entrypoint.write_text(f"---\nname: {SKILL_NAME}\n---\n", encoding="utf-8")
        self.assertTrue(check_repository(self.root))

    def test_missing_digest_fails(self):
        (self.skill / "references" / "sources" / REQUIRED_DIGESTS[0]).unlink()
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

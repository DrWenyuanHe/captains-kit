"""Exercise the install-tools helper without changing installed packages."""

import importlib.util
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "install-tools" / "scripts" / "tools.py"
SPEC = importlib.util.spec_from_file_location("install_tools", SCRIPT)
tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tools)


def environment(**overrides):
    env = {
        "executable": "/python", "in_venv": False, "externally_managed": False,
        "purelib": "/site", "purelib_writable": False, "user_site_enabled": True,
    }
    env.update(overrides)
    return env


def dist(name, version, *requires):
    return {"name": name, "version": version, "location": "/site", "requires": list(requires)}


class ConstraintTests(unittest.TestCase):
    def test_requirements_become_constraints(self):
        cases = {
            "numpy<2.5,>=1.22": "numpy<2.5,>=1.22",
            "numpy (>=1.22)": "numpy>=1.22",
            "numpy >= 1.22, < 3": "numpy>=1.22,<3",
            "requests[socks]>=2": "requests>=2",
            'typing-extensions>=4; python_version < "3.11"': 'typing-extensions>=4; python_version < "3.11"',
        }
        for requirement, expected in cases.items():
            with self.subTest(requirement=requirement):
                self.assertEqual(tools.constraint_for(requirement), expected)

    def test_unusable_requirements_are_skipped(self):
        for requirement in (
            'pytest>=7; extra == "test"',
            'colorama; sys_platform == "win32"',
            "pkg @ https://example.com/pkg.whl",
            "bad>>1",
            "",
        ):
            with self.subTest(requirement=requirement):
                self.assertIsNone(tools.constraint_for(requirement))

    def test_constraints_skip_targets_and_name_their_owners(self):
        dists = {
            "numba": dist("numba", "0.64.0", "numpy<2.5,>=1.22", "llvmlite"),
            "scipy": dist("scipy", "1.16.0", "numpy<2.7,>=1.26.4"),
            "other": dist("other", "1.0", "numpy<2.5,>=1.22"),
            "opencv-python": dist("opencv-python", "4.13.0", "numpy>=2"),
        }
        constraints = tools.protective_constraints(dists, {"opencv-python"})
        self.assertEqual(constraints["numpy<2.5,>=1.22"], {"numba", "other"})
        self.assertIn("numpy<2.7,>=1.26.4", constraints)
        self.assertNotIn("numpy>=2", constraints)
        limits = tools.upper_limits(constraints, ["numpy"])
        self.assertIn("numpy<2.5,>=1.22 (numba, other)", limits)

    def test_major_constraints_cover_installed_targets(self):
        dists = {"opencv-python": dist("opencv-python", "4.13.0.92"), "numba": dist("numba", "0.64.0")}
        constraints = tools.major_constraints(dists, {"opencv-python", "numba", "absent"})
        self.assertEqual(set(constraints), {"opencv-python<5", "numba<1"})
        self.assertIn("opencv-python<5 (current major version; --allow-major lifts)",
                      tools.upper_limits(constraints, ["opencv-python"]))

    def test_dependents_include_changed_packages_and_their_users(self):
        dists = {
            "numpy": dist("numpy", "2.4.6"),
            "scipy": dist("scipy", "1.16.0", "numpy<2.7,>=1.26.4"),
            "librosa": dist("librosa", "0.11", "numba>=0.51"),
            "tester": dist("tester", "1.0", 'numpy; extra == "test"'),
            "unrelated": dist("unrelated", "1.0", "requests"),
        }
        self.assertEqual(tools.dependents(dists, ["NumPy", "not-installed"]), ["numpy", "scipy"])

    def test_constraints_file_keeps_pip_comment_syntax(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = tools.write_constraints(Path(temporary) / "c.txt", {"numpy<2.5": {"numba"}})
            self.assertEqual(path.read_text(encoding="utf-8"), "numpy<2.5  # numba\n")


class ScopeTests(unittest.TestCase):
    def test_scope_resolution(self):
        self.assertEqual(tools.resolve_scope(environment(purelib_writable=True)), "system")
        self.assertEqual(tools.resolve_scope(environment()), "user")
        self.assertEqual(tools.resolve_scope(environment(purelib_writable=True), "user"), "user")
        self.assertEqual(tools.resolve_scope(environment(in_venv=True), allow_venv=True), "venv")

    def test_scope_stops(self):
        for env, requested in (
            (environment(in_venv=True), "auto"),
            (environment(externally_managed=True, purelib_writable=True), "auto"),
            (environment(), "system"),
            (environment(user_site_enabled=False), "auto"),
        ):
            with self.subTest(env=env, requested=requested), self.assertRaises(tools.ToolError):
                tools.resolve_scope(env, requested)


class ManifestTests(unittest.TestCase):
    def write(self, data):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "tools.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_bundled_manifest_covers_imaging_tools(self):
        packages = tools.load_manifest(tools.DEFAULT_MANIFEST)
        self.assertEqual({p["import"] for p in packages}, {"PIL", "cv2", "numpy"})
        opencv = next(p for p in packages if p["import"] == "cv2")
        self.assertIn("opencv-python-headless", opencv["alternatives"])

    def test_sets_merge_by_distribution(self):
        path = self.write({"schema_version": 1, "sets": {
            "a": {"packages": [{"name": "Pillow", "requirement": "pillow>=10", "import": "PIL"}]},
            "b": {"packages": [{"name": "pillow", "import": "PIL"}]},
        }})
        packages = tools.load_manifest(path)
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0]["spec"], ">=10")
        self.assertEqual(packages[0]["sets"], ["a", "b"])
        self.assertEqual(len(tools.load_manifest(path, ["b"])), 1)

    def test_invalid_manifests_are_rejected(self):
        for data, selected in (
            ({"schema_version": 1, "sets": {"a": {"packages": []}}}, ["missing"]),
            ({"schema_version": 1, "sets": {"a": {"packages": [{"name": "numpy", "import": "numpy",
                                                                 "requirement": "numpy2>=1"}]}}}, []),
            ({"schema_version": 1, "sets": {"a": {"packages": [{"name": "numpy", "import": "numpy",
                                                                 "requirement": "numpy[x]>=1"}]}}}, []),
            ({"schema_version": 1, "sets": {"a": {"packages": [{"name": "numpy"}]}}}, []),
            ({"schema_version": 2, "sets": {"a": {"packages": []}}}, []),
        ):
            with self.subTest(data=data), self.assertRaises(tools.ToolError):
                tools.load_manifest(self.write(data), selected)

    def test_providers_prefer_primary_then_alternatives(self):
        package = {"name": "opencv-python", "alternatives": ["opencv-python-headless"]}
        headless = {"opencv-python-headless": dist("opencv-python-headless", "4.10")}
        self.assertEqual([d["name"] for d in tools.providers(package, headless)], ["opencv-python-headless"])
        both = dict(headless, **{"opencv-python": dist("opencv-python", "5.0")})
        self.assertEqual([d["name"] for d in tools.providers(package, both)],
                         ["opencv-python", "opencv-python-headless"])


class SelectionTests(unittest.TestCase):
    PACKAGES = [
        {"name": "opencv-python", "spec": ">=4.8", "import": "cv2", "alternatives": ["opencv-python-headless"]},
        {"name": "numpy", "spec": "", "import": "numpy", "alternatives": []},
        {"name": "Pillow", "spec": ">=10", "import": "PIL", "alternatives": []},
    ]

    def test_duplicate_providers_change_together(self):
        dists = {
            "opencv-python": dist("opencv-python", "4.13.0.92"),
            "opencv-python-headless": dist("opencv-python-headless", "4.13.0.92"),
            "numpy": dist("numpy", "2.4.6"),
            "numba": dist("numba", "0.64.0"),
        }
        requirements, targets, groups = tools.select_requirements(self.PACKAGES, dists, ["numba"])
        self.assertEqual(requirements, ["opencv-python>=4.8", "opencv-python-headless>=4.8", "numpy",
                                        "Pillow>=10", "numba"])
        self.assertEqual(targets, {"opencv-python", "opencv-python-headless", "numpy", "pillow", "numba"})
        self.assertEqual(groups, [["opencv-python", "opencv-python-headless"]])
        with self.assertRaises(tools.ToolError):
            tools.select_requirements(self.PACKAGES, dists, ["not-installed"])

    def test_lockstep_groups_must_end_on_one_version(self):
        dists = {"a": dist("a", "4.13"), "b": dist("b", "4.13")}
        groups = [["a", "b"]]
        self.assertEqual(tools.lockstep_errors(groups, dists, [("a", "4.14"), ("b", "4.14")]), [])
        self.assertEqual(tools.lockstep_errors(groups, dists, []), [])
        self.assertEqual(tools.lockstep_errors(groups, dists, [("a", "4.14")]), ["a 4.14, b 4.13"])

    def test_installed_alternative_is_the_target(self):
        packages = [{"name": "opencv-python", "spec": ">=4.8", "import": "cv2",
                     "alternatives": ["opencv-python-headless"]}]
        dists = {"opencv-python-headless": dist("opencv-python-headless", "4.10")}
        requirements, targets, groups = tools.select_requirements(packages, dists)
        self.assertEqual((requirements, targets, groups),
                         (["opencv-python-headless>=4.8"], {"opencv-python-headless"}, []))

    def test_major_version_changes(self):
        self.assertTrue(tools.major_change("4.13.0.92", "5.0.0.93"))
        self.assertFalse(tools.major_change("2.4.2", "2.5.3"))
        self.assertFalse(tools.major_change("dev", "1.0"))


class DiffTests(unittest.TestCase):
    def test_diff_classifies_changes(self):
        before = {"numpy": dist("numpy", "2.4.2"), "old": dist("old", "1.0"), "same": dist("same", "1")}
        after = {"numpy": dist("numpy", "2.5.3"), "new": dist("new", "2.0"), "same": dist("same", "1")}
        self.assertEqual(tools.diff(before, after), {
            "changed": {"numpy": ["2.4.2", "2.5.3"]}, "added": {"new": "2.0"}, "removed": {"old": "1.0"},
        })


class CommandTests(unittest.TestCase):
    """Exercise orchestration with inventories and subprocess results owned by the test."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = self.root / "tools.json"
        self.manifest.write_text(json.dumps({"schema_version": 1, "sets": {"core": {"packages": [
            {"name": "numpy", "import": "numpy"},
        ]}}}), encoding="utf-8")
        self.records = self.root / "records"
        self.before = {"numpy": dict(dist("numpy", "2.4.0"), modules=["numpy"]),
                       "consumer": dict(dist("consumer", "1", "numpy<3"), modules=["consumer"])}
        self.current = copy.deepcopy(self.before)
        self.after = copy.deepcopy(self.before)
        self.after["numpy"]["version"] = "2.5.0"
        self.planned = [("numpy", "2.5.0")]
        self.commands = []
        self.resolver_code = self.install_code = self.restore_code = 0
        self.before_conflicts, self.after_conflicts = [], []
        self.baseline_imports = {"numpy": None, "consumer": None}
        self.after_imports = dict(self.baseline_imports)
        self.verification = {"ok": True, "version": "2.5.0"}
        self.import_calls = []
        self.applied = False
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, replacement in (
            ("resolve_python", lambda value: "/fixture/python"),
            ("probe", self.probe), ("run", self.run_process),
            ("conflicts", lambda python: list(self.after_conflicts if self.applied else self.before_conflicts)),
            ("import_checks", self.import_checks),
            ("verify", lambda python, package: dict(self.verification)),
        ):
            self.stack.enter_context(mock.patch.object(tools, name, side_effect=replacement))
        # No unmocked subprocess can escape a transaction test.
        self.stack.enter_context(mock.patch.object(subprocess, "Popen", side_effect=AssertionError("real subprocess")))

    def probe(self, python):
        return environment(executable=python, pip="25.0", version="3.12.0", user_site="/user/site",
                           user_scripts="/user/bin", path="", distributions=copy.deepcopy(self.current))

    def import_checks(self, python, modules):
        self.import_calls.append(list(modules))
        results = self.after_imports if self.applied else self.baseline_imports
        return {name: results[name] for name in modules}

    def run_process(self, command, **kwargs):
        self.commands.append(command)
        self.assertEqual(command[:len(tools.PIP) + 1], ["/fixture/python", *tools.PIP])
        if "--report" in command:
            report = Path(command[command.index("--report") + 1])
            report.write_text(json.dumps({"install": [
                {"metadata": {"name": name, "version": version}} for name, version in self.planned
            ]}), encoding="utf-8")
            code = self.resolver_code
        elif "--no-deps" in command or "uninstall" in command:
            code = self.restore_code
        else:
            self.assertTrue(list(self.records.glob("*/record.json")), "save recovery data before applying")
            self.applied = True
            self.current = copy.deepcopy(self.after)
            code = self.install_code
        return subprocess.CompletedProcess(command, code, "", "simulated failure" if code else "")

    def run_tool(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tools.main(list(args))
        return subprocess.CompletedProcess(args, code, out.getvalue(), err.getvalue())

    def change(self, *args, operation="install"):
        return self.run_tool(operation, "--manifest", str(self.manifest), "--record-dir", str(self.records), *args)

    def record(self):
        paths = list(self.records.glob("*/record.json"))
        self.assertEqual(len(paths), 1)
        return paths[0], json.loads(paths[0].read_text(encoding="utf-8"))

    def test_status_reports_an_importable_package(self):
        result = self.run_tool("status", "--manifest", str(self.manifest), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        package = report["interpreters"][0]["packages"][0]
        self.assertTrue(package["import_ok"], package)
        self.assertEqual(package["distribution"], "numpy")
        self.assertEqual(self.commands, [])
        self.assertFalse(self.records.exists())

    def test_status_fails_for_a_missing_package(self):
        self.manifest.write_text(json.dumps({"schema_version": 1, "sets": {"core": {"packages": [
            {"name": "captains-kit-absent-package", "import": "captains_kit_absent_package"},
        ]}}}), encoding="utf-8")
        result = self.run_tool("status", "--manifest", str(self.manifest))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("missing", result.stdout)

    def test_dry_run_changes_nothing_and_writes_no_record(self):
        result = self.change("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2.4.0 -> 2.5.0", result.stdout)
        self.assertEqual(len(self.commands), 1)
        self.assertIn("--dry-run", self.commands[0])
        self.assertFalse(self.records.exists())
        self.assertEqual(self.current, self.before)

    def test_no_op_verifies_without_applying_or_recording(self):
        self.planned = []
        result = self.change()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No changes", result.stdout)
        self.assertEqual(len(self.commands), 1)
        self.assertFalse(self.records.exists())

    def test_success_records_changes_and_retains_constraints(self):
        result = self.change(operation="update")
        self.assertEqual(result.returncode, 0, result.stderr)
        path, record = self.record()
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["changes"], {"changed": {"numpy": ["2.4.0", "2.5.0"]}, "added": {}, "removed": {}})
        self.assertEqual(record["before"], {"numpy": "2.4.0", "consumer": "1"})
        self.assertTrue(record["verification"]["numpy"]["ok"])
        self.assertEqual(record["command"], self.commands[1])
        command = self.commands[1]
        for argument in ("--user", "--upgrade", "numpy"):
            self.assertIn(argument, command)
        self.assertNotIn("--dry-run", command)
        self.assertEqual(command[command.index("-c") + 1], str(path.parent / "constraints.txt"))
        self.assertIn("numpy<3", (path.parent / "constraints.txt").read_text(encoding="utf-8"))
        self.assertEqual(self.import_calls, [["consumer", "numpy"], ["consumer", "numpy"]])

    def test_resolver_failure_leaves_inventory_and_records_untouched(self):
        self.resolver_code = 1
        result = self.change()
        self.assertEqual(result.returncode, 2)
        self.assertIn("no compatible set", result.stderr)
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.current, self.before)
        self.assertFalse(self.records.exists())

    def test_installing_a_missing_package_records_an_addition(self):
        del self.before["numpy"]
        self.current = copy.deepcopy(self.before)
        result = self.change()
        self.assertEqual(result.returncode, 0, result.stderr)
        _, record = self.record()
        self.assertEqual(record["operation"], "install")
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["changes"], {"changed": {}, "added": {"numpy": "2.5.0"}, "removed": {}})
        self.assertNotIn("--upgrade", self.commands[-1])

    def test_partial_install_failure_records_actual_changes_for_recovery(self):
        self.install_code = 1
        result = self.change()
        self.assertEqual(result.returncode, 1)
        _, record = self.record()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["changes"]["changed"], {"numpy": ["2.4.0", "2.5.0"]})
        self.assertIn("simulated failure", record["pip_output"])
        self.assertIn("rollback", result.stdout)
        self.assertEqual(len(self.commands), 2)

    def test_new_conflicts_trigger_automatic_rollback(self):
        self.after_conflicts = ["consumer needs numpy<2.5"]
        result = self.change()
        self.assertEqual(result.returncode, 1)
        _, record = self.record()
        self.assertEqual(record["status"], "rolled-back")
        self.assertEqual(record["new_conflicts"], self.after_conflicts)
        self.assertIn("dependency conflicts", record["rollback"]["reason"])
        self.assertEqual(self.commands[-1], ["/fixture/python", *tools.PIP, "install", "--no-deps", "--user", "numpy==2.4.0"])

    def test_preexisting_conflicts_and_broken_imports_are_not_new_regressions(self):
        self.before_conflicts = self.after_conflicts = ["unrelated pre-existing conflict"]
        self.baseline_imports["consumer"] = "already broken"
        result = self.change()
        self.assertEqual(result.returncode, 0, result.stderr)
        _, record = self.record()
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["new_conflicts"], [])
        self.assertEqual(record["imports_failing_before"], {"consumer": "already broken"})
        self.assertEqual(self.import_calls[-1], ["numpy"])
        self.assertEqual(len(self.commands), 2)

    def test_new_import_failure_triggers_automatic_rollback(self):
        self.after_imports["consumer"] = "binary incompatibility"
        result = self.change()
        self.assertEqual(result.returncode, 1)
        _, record = self.record()
        self.assertEqual(record["status"], "rolled-back")
        self.assertEqual(record["import_regressions"], {"consumer": "binary incompatibility"})
        self.assertIn("imports that worked", record["rollback"]["reason"])
        self.assertIn("numpy==2.4.0", self.commands[-1])

    def test_failed_automatic_restoration_is_recorded_and_reported(self):
        self.after_conflicts = ["new conflict"]
        self.restore_code = 1
        result = self.change()
        self.assertEqual(result.returncode, 1)
        _, record = self.record()
        self.assertEqual(record["rollback"]["steps"][0]["returncode"], 1)
        self.assertIn("simulated failure", record["rollback"]["steps"][0]["output"])
        self.assertIn("Rollback step failed", result.stderr)
        self.assertEqual(record["status"], "rollback-failed")
        self.assertNotIn("rolled_back_at", record)
        self.restore_code = 0
        path, _ = self.record()
        result = self.run_tool("rollback", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        _, record = self.record()
        self.assertEqual(record["status"], "rolled-back")
        self.assertTrue(record["rolled_back_at"])

    def rollback_record(self):
        path = self.root / "recovery.json"
        path.write_text(json.dumps({
            "interpreter": "/fixture/python", "scope": "user",
            "changes": {"changed": {"numpy": ["2.4.0", "2.5.0"]},
                        "added": {"added": "1"}, "removed": {"removed": "3"}},
        }), encoding="utf-8")
        self.current = {**copy.deepcopy(self.after), "added": dist("added", "1")}
        return path

    def test_explicit_rollback_uninstalls_additions_and_restores_prior_versions(self):
        path = self.rollback_record()
        result = self.run_tool("rollback", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commands, [
            ["/fixture/python", *tools.PIP, "uninstall", "--yes", "added"],
            ["/fixture/python", *tools.PIP, "install", "--no-deps", "--user", "numpy==2.4.0", "removed==3"],
        ])
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(record["rolled_back_at"])
        self.assertEqual(record["rollback"]["reason"], "requested")
        before = path.read_bytes()
        result = self.run_tool("rollback", str(path))
        self.assertEqual(result.returncode, 2)
        self.assertIn("already rolled back", result.stderr)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.commands), 2)

    def test_rollback_refuses_drift_without_rewriting_record(self):
        path = self.rollback_record()
        self.current["numpy"]["version"] = "2.6.0"
        before = path.read_bytes()
        result = self.run_tool("rollback", str(path))
        self.assertEqual(result.returncode, 2)
        self.assertIn("packages changed after", result.stderr)
        self.assertEqual(self.commands, [])
        self.assertEqual(path.read_bytes(), before)
        result = self.run_tool("rollback", str(path), "--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["rollback"]["forced_over"], ["numpy: expected 2.5.0, found 2.6.0"])

    def test_explicit_rollback_reports_failed_restoration_steps(self):
        path = self.rollback_record()
        self.restore_code = 1
        result = self.run_tool("rollback", str(path))
        self.assertEqual(result.returncode, 1)
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([step["returncode"] for step in record["rollback"]["steps"]], [1, 1])
        self.assertIn("simulated failure", result.stderr)
        self.assertNotIn("rolled_back_at", record)
        self.restore_code = 0
        result = self.run_tool("rollback", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)


class SubprocessSmokeTests(unittest.TestCase):
    def test_cli_help_and_invalid_arguments(self):
        for arguments, expected in ((["--help"], 0), (["install", "--scope", "invalid"], 2)):
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True,
                                        text=True, encoding="utf-8", timeout=30)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertIn("usage:", result.stdout + result.stderr)

    def test_import_checks_report_each_module(self):
        results = tools.import_checks(sys.executable, ["json", "captains_kit_absent_module"])
        self.assertIsNone(results["json"])
        self.assertIn("ModuleNotFoundError", results["captains_kit_absent_module"])


if __name__ == "__main__":
    unittest.main()

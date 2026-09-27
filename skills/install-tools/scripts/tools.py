#!/usr/bin/env python3
"""Check, install, update or roll back the Python packages agents import.

Standard library only. Each target interpreter is inspected and changed through
its own subprocess, so one Python can manage another. Install and update pass the
requirements of already-installed packages to pip as constraints, keep updates in
the current major version unless allowed, and import every installed package that
uses a changed one before and after. A new `pip check` conflict or a newly failing
import rolls the change back.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


SKILL = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = SKILL / "assets" / "tools.json"
DEFAULT_RECORDS = Path.home() / ".agent-tools" / "records"
PIP = ["-m", "pip", "--disable-pip-version-check", "--no-input"]
REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?\s*(.*?)\s*$")
CLAUSE = re.compile(r"^(~=|===|==|!=|<=|>=|<|>)[A-Za-z0-9.*+!_-]+$")

# Runs inside the target interpreter. Probing writability creates and removes one
# file, as pip does before choosing a user install. tempfile.mkstemp is avoided:
# on Windows it retries almost indefinitely when ACLs deny writes to a folder.
PROBE = r"""
import importlib.metadata as metadata, json, os, re, site, sys, sysconfig, uuid

def writable(path):
    if not os.path.isdir(path):
        return False
    for _ in range(10):
        name = os.path.join(path, ".agent-tools-probe-" + uuid.uuid4().hex)
        try:
            handle = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            continue
        except OSError:
            return False
        os.close(handle)
        os.remove(name)
        return True
    return False

def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()

modules = {}
try:
    for module, owners in metadata.packages_distributions().items():
        if module.isidentifier() and not module.startswith("_"):
            for owner in owners:
                modules.setdefault(normalize(owner), set()).add(module)
except Exception:
    pass

found = {}
for dist in metadata.distributions():
    try:
        name = dist.metadata.get("Name")
    except Exception:
        name = None
    if not name:
        continue
    key = normalize(name)
    if key not in found:
        found[key] = {"name": name, "version": dist.version, "location": str(dist.locate_file("")),
                      "requires": dist.requires or [], "modules": sorted(modules.get(key, ()))}
try:
    pip_version = metadata.version("pip")
except metadata.PackageNotFoundError:
    pip_version = None
get_scheme = getattr(sysconfig, "get_preferred_scheme", lambda kind: os.name + "_user")
print(json.dumps({
    "executable": sys.executable,
    "version": sys.version.split()[0],
    "in_venv": sys.prefix != sys.base_prefix,
    "purelib": sysconfig.get_path("purelib"),
    "purelib_writable": writable(sysconfig.get_path("purelib")),
    "scripts": sysconfig.get_path("scripts"),
    "user_site_enabled": bool(site.ENABLE_USER_SITE),
    "user_site": site.getusersitepackages(),
    "user_scripts": sysconfig.get_path("scripts", get_scheme("user")),
    "externally_managed": os.path.isfile(os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")),
    "path": os.environ.get("PATH", ""),
    "pip": pip_version,
    "distributions": found,
}))
"""

VERIFY = r"""
import importlib, json, sys
try:
    module = importlib.import_module(sys.argv[1])
    exec(sys.argv[2], {"module": module})
    result = {"ok": True, "version": str(getattr(module, "__version__", "") or ""),
              "file": getattr(module, "__file__", None)}
except Exception as error:
    result = {"ok": False, "error": type(error).__name__ + ": " + str(error)}
print(json.dumps(result))
"""


class ToolError(Exception):
    """A condition that stops the requested operation before packages change."""


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def run(command, cwd=None, timeout=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd, env=env, timeout=timeout)


def tail(result, limit=4000):
    text = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
    return text[-limit:]


def last_line(text):
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def resolve_python(value):
    path = shutil.which(value) or value
    if not Path(path).is_file():
        raise ToolError(f"interpreter not found: {value}")
    return str(Path(path).resolve())


def probe(python):
    result = run([python, "-c", PROBE])
    try:
        return json.loads(last_line(result.stdout))
    except ValueError:
        raise ToolError(f"{python}: cannot inspect interpreter: {tail(result) or result.returncode}") from None


def resolve_scope(env, requested="auto", allow_venv=False):
    """Return 'system', 'user' or 'venv' for a global install, or explain why not."""
    where = env["executable"]
    if env["in_venv"]:
        if allow_venv:
            return "venv"
        raise ToolError(f"{where} is a virtual environment. Global tools belong in the base "
                        "interpreter; pass it with --python, or --allow-venv to install here deliberately.")
    if env["externally_managed"]:
        raise ToolError(f"{where} is marked externally managed (PEP 668). Use the operating "
                        "system's package manager, or ask the user before choosing another approach.")
    if requested in ("auto", "system") and env["purelib_writable"]:
        return "system"
    if requested == "system":
        raise ToolError(f"{env['purelib']} is not writable by this account. Use --scope user, "
                        "or ask the user to install with administrator rights.")
    if env["user_site_enabled"]:
        return "user"
    raise ToolError(f"{where}: site-packages is not writable and user site-packages is "
                    "disabled, so nothing can be installed globally.")


def scope_location(env, scope):
    return env["user_site"] if scope == "user" else env["purelib"]


def scripts_on_path(env, scope):
    folder = env["user_scripts"] if scope == "user" else env["scripts"]
    entries = {os.path.normcase(os.path.normpath(p)) for p in env["path"].split(os.pathsep) if p}
    return folder, os.path.normcase(os.path.normpath(folder)) in entries


def load_manifest(path, selected=()):
    """Return the selected packages, merged across sets by distribution name."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ToolError(f"cannot read manifest {path}: {error}") from None
    sets = data.get("sets")
    if data.get("schema_version") != 1 or not isinstance(sets, dict) or not sets:
        raise ToolError(f"{path}: expected schema_version 1 and a non-empty 'sets' object")
    unknown = [name for name in selected if name not in sets]
    if unknown:
        raise ToolError(f"unknown tool set(s): {', '.join(unknown)}; available: {', '.join(sets)}")
    packages = {}
    for set_name in selected or sets:
        for entry in sets[set_name].get("packages", []):
            name, module = entry.get("name"), entry.get("import")
            match = REQUIREMENT.match(entry.get("requirement", name or ""))
            if not name or not module or not match or normalize(match[1]) != normalize(name) or match[2]:
                raise ToolError(f"{path}: set '{set_name}' has an invalid package entry: {entry}")
            package = packages.setdefault(normalize(name), {
                "name": name, "spec": match[3], "import": module, "smoke": entry.get("smoke", ""),
                "alternatives": list(entry.get("alternatives", [])), "sets": [],
            })
            package["sets"].append(set_name)
    return list(packages.values())


def providers(package, dists):
    """Installed distributions that provide the package's module, primary name first."""
    names = [package["name"], *package["alternatives"]]
    return [dists[normalize(name)] for name in names if normalize(name) in dists]


def select_requirements(packages, dists, extra=()):
    """Return pip requirements, the distributions they change, and lockstep groups.

    Interchangeable distributions (such as OpenCV's variants) share one package
    folder. When several are installed, changing only one leaves the others' files
    mismatched, so all of them are changed together and must end on one version.
    `extra` names other installed distributions to change in the same run.
    """
    requirements, targets, groups = [], set(), []
    for package in packages:
        found = providers(package, dists) or [{"name": package["name"]}]
        for dist in found:
            requirements.append(dist["name"] + package["spec"])
            targets.add(normalize(dist["name"]))
        if len(found) > 1:
            groups.append([dist["name"] for dist in found])
    for name in extra:
        dist = dists.get(normalize(name))
        if not dist:
            raise ToolError(f"--with {name}: not installed; --with changes installed packages only")
        if normalize(name) not in targets:
            requirements.append(dist["name"])
            targets.add(normalize(name))
    return requirements, targets, groups


def lockstep_errors(groups, dists, planned):
    """Describe lockstep groups whose members would end on different versions."""
    final = {normalize(name): version for name, version in planned}
    errors = []
    for group in groups:
        versions = {name: final.get(normalize(name), dists[normalize(name)]["version"]) for name in group}
        if len(set(versions.values())) > 1:
            errors.append(", ".join(f"{name} {version}" for name, version in versions.items()))
    return errors


def major_change(old, new):
    old_major, new_major = re.match(r"\d+", old), re.match(r"\d+", new)
    return bool(old_major and new_major) and int(old_major[0]) != int(new_major[0])


def major_constraints(dists, targets):
    """Keep each installed target within its current major version."""
    lines = {}
    for key in sorted(targets):
        major = re.match(r"\d+", dists[key]["version"]) if key in dists else None
        if major:
            lines[f"{dists[key]['name']}<{int(major[0]) + 1}"] = {"current major version; --allow-major lifts"}
    return lines


def dependents(dists, names):
    """Installed distributions among `names`, plus every installed distribution requiring one."""
    wanted = {normalize(name) for name in names}
    found = {key for key in wanted if key in dists}
    for key, dist in dists.items():
        for requirement in dist["requires"]:
            spec, _, marker = requirement.partition(";")
            match = REQUIREMENT.match(spec)
            if match and normalize(match[1]) in wanted and not re.search(r"\bextra\b", marker):
                found.add(key)
                break
    return sorted(found)


def import_checks(python, modules, timeout=300, workers=4):
    """Import each module in a fresh process; map it to None on success or an error line."""
    def check(module):
        with tempfile.TemporaryDirectory(prefix="agent-tools-import-") as clean:
            try:
                result = run([python, "-c", "import importlib, sys; importlib.import_module(sys.argv[1])",
                              module], cwd=clean, timeout=timeout)
            except subprocess.TimeoutExpired:
                return module, f"timed out after {timeout}s"
        return module, None if result.returncode == 0 else last_line(result.stderr) or f"exit {result.returncode}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(check, sorted(modules)))


def constraint_for(requirement):
    """Turn one installed package's Requires-Dist entry into a pip constraint, or None."""
    spec, _, marker = requirement.partition(";")
    marker = marker.strip()
    if re.search(r"\bextra\b", marker):
        return None  # applies only when an optional extra was requested
    match = REQUIREMENT.match(spec)
    if not match:
        return None
    version = match[3].strip()
    if version.startswith("(") and version.endswith(")"):
        version = version[1:-1]
    version = re.sub(r"\s+", "", version)
    if not version or not all(CLAUSE.match(clause) for clause in version.split(",")):
        return None  # unconstrained, URL or unparseable requirement
    return match[1] + version + (f"; {marker}" if marker else "")


def protective_constraints(dists, targets):
    """Map each constraint line to the installed packages that require it."""
    lines = {}
    for key, dist in sorted(dists.items()):
        if key in targets:
            continue  # a target's own old requirements are replaced by its new version's
        for requirement in dist["requires"]:
            line = constraint_for(requirement)
            if line:
                lines.setdefault(line, set()).add(dist["name"])
    return lines


def upper_limits(constraints, names):
    """Constraints on the given names that cap versions, for reporting held-back upgrades."""
    wanted = {normalize(name) for name in names}
    limits = []
    for line, owners in sorted(constraints.items()):
        match = REQUIREMENT.match(line.split(";")[0])
        if normalize(match[1]) in wanted and re.search(r"(^|,)(<|<=|==|~=|===)", match[3]):
            limits.append(f"{line} ({', '.join(sorted(owners))})")
    return limits


def write_constraints(path, constraints):
    text = "".join(f"{line}  # {', '.join(sorted(owners))}\n" for line, owners in sorted(constraints.items()))
    path.write_text(text, encoding="utf-8")
    return path


def conflicts(python):
    result = run([python, *PIP, "check"])
    if result.returncode == 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()] or [tail(result)]


def verify(python, package):
    with tempfile.TemporaryDirectory(prefix="agent-tools-verify-") as clean:
        result = run([python, "-c", VERIFY, package["import"], package["smoke"]], cwd=clean)
    try:
        return json.loads(last_line(result.stdout))
    except ValueError:
        return {"ok": False, "error": tail(result) or f"exit code {result.returncode}"}


def latest_version(python, name):
    result = run([python, *PIP, "index", "versions", name])
    match = re.match(r"^\S+ \(([^)]+)\)", result.stdout.strip()) if result.returncode == 0 else None
    return match[1] if match else None


def diff(before, after):
    changes = {"changed": {}, "added": {}, "removed": {}}
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        if old and new and old["version"] != new["version"]:
            changes["changed"][new["name"]] = [old["version"], new["version"]]
        elif new and not old:
            changes["added"][new["name"]] = new["version"]
        elif old and not new:
            changes["removed"][old["name"]] = old["version"]
    return changes


def pip_install(python, scope, requirements, constraints_file=None, *, upgrade=False, report=None):
    command = [python, *PIP, "install", "--upgrade-strategy", "only-if-needed"]
    if scope == "user":
        command.append("--user")
    if upgrade:
        command.append("--upgrade")
    if constraints_file:
        command += ["-c", str(constraints_file)]
    if report:
        command += ["--dry-run", "--quiet", "--report", str(report)]
    return command + requirements


def restore(python, scope, changes):
    """Return packages to the versions recorded before an operation."""
    steps = []
    if changes["added"]:
        command = [python, *PIP, "uninstall", "--yes", *changes["added"]]
        result = run(command)
        steps.append({"command": command, "returncode": result.returncode, "output": tail(result)})
    reinstall = [f"{name}=={old}" for name, (old, _new) in changes["changed"].items()]
    reinstall += [f"{name}=={version}" for name, version in changes["removed"].items()]
    if reinstall:
        command = [python, *PIP, "install", "--no-deps"] + (["--user"] if scope == "user" else []) + reinstall
        result = run(command)
        steps.append({"command": command, "returncode": result.returncode, "output": tail(result)})
    return steps


def new_record_dir(root, operation):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for suffix in range(100):
        path = Path(root) / (f"{stamp}-{operation}" + (f"-{suffix}" if suffix else ""))
        try:
            path.mkdir(parents=True)
            return path
        except FileExistsError:
            continue
    raise ToolError(f"cannot create a record folder under {root}")


def save(record_dir, record):
    (record_dir / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def print_changes(changes):
    for name, (old, new) in changes["changed"].items():
        print(f"  {name} {old} -> {new}")
    for name, version in changes["added"].items():
        print(f"  {name} {version} (added)")
    for name, version in changes["removed"].items():
        print(f"  {name} {version} (removed)")


def report_verification(python, packages):
    results = {}
    for package in packages:
        check = results[package["name"]] = verify(python, package)
        detail = f"ok {check.get('version', '')}".rstrip() if check["ok"] else check["error"]
        print(f"  import {package['import']}: {detail}")
    return all(check["ok"] for check in results.values()), results


def change(args, upgrade):
    operation = "update" if upgrade else "install"
    python = resolve_python(args.python)
    env = probe(python)
    scope = resolve_scope(env, args.scope, args.allow_venv)
    if not env["pip"]:
        raise ToolError(f"{python} has no pip; install pip for that interpreter first")
    packages = load_manifest(args.manifest, args.set)
    dists = env["distributions"]
    requirements, targets, groups = select_requirements(packages, dists, args.extra)

    print(f"{python} (Python {env['version']}), {scope} scope: {scope_location(env, scope)}")
    for group in groups:
        print(f"Kept in lockstep (they share one module folder): {', '.join(group)}")
    before_conflicts = conflicts(python)
    constraints = {} if args.no_protect else protective_constraints(dists, targets)
    if upgrade and not args.allow_major:
        for line, owners in major_constraints(dists, targets).items():
            constraints.setdefault(line, set()).update(owners)
    with tempfile.TemporaryDirectory(prefix="agent-tools-") as temporary:
        workdir = Path(temporary)
        constraints_file = write_constraints(workdir / "constraints.txt", constraints) if constraints else None
        result = run(pip_install(python, scope, requirements, constraints_file,
                                 upgrade=upgrade, report=workdir / "plan.json"))
        if result.returncode:
            raise ToolError("pip found no compatible set of versions; nothing was changed.\n" + tail(result))
        plan = json.loads((workdir / "plan.json").read_text(encoding="utf-8")).get("install", [])
        planned = [(item["metadata"]["name"], item["metadata"]["version"]) for item in plan]
        mismatched = lockstep_errors(groups, dists, planned)
        if mismatched:
            raise ToolError("packages sharing one module folder would end on different versions; "
                            "nothing was changed:\n  " + "\n  ".join(mismatched))
        limits = upper_limits(constraints, [name for name, _ in planned] + sorted(targets))
        watch = dependents(dists, [name for name, _ in planned])
        modules = sorted({module for key in watch for module in dists[key]["modules"]})

        if planned:
            print(f"Planned {operation}:")
            for name, version in planned:
                current = dists.get(normalize(name))
                major = " (major version change)" if current and major_change(current["version"], version) else ""
                print(f"  {name} {current['version'] + ' -> ' if current else ''}{version}{major}")
            print(f"Import check: {len(modules)} modules from {len(watch)} installed packages that are "
                  "or use the changed packages, before and after")
        else:
            print(f"No changes: the selected packages already satisfy the {operation} request.")
        if limits:
            print("Version limits:")
            for limit in limits:
                print(f"  {limit}")
        if before_conflicts:
            print(f"Pre-existing dependency conflicts ({len(before_conflicts)}), left as found:")
            for line in before_conflicts:
                print(f"  {line}")
        if args.dry_run:
            return 0
        if not planned:
            return 0 if report_verification(python, packages)[0] else 1

        baseline = import_checks(python, modules)
        judged = [module for module, error in baseline.items() if error is None]
        already_failing = {module: error for module, error in baseline.items() if error}
        print(f"  {len(judged)} import now" + (f"; {len(already_failing)} already fail and are not judged"
                                               if already_failing else ""))
        record_dir = new_record_dir(args.record_dir, operation)
        if constraints_file:
            shutil.copyfile(constraints_file, record_dir / "constraints.txt")
        record = {
            "schema_version": 1, "operation": operation, "started_at": now(), "status": "started",
            "interpreter": python, "python_version": env["version"], "scope": scope,
            "location": scope_location(env, scope), "manifest": str(Path(args.manifest).resolve()),
            "sets": args.set or "all", "with": args.extra, "requirements": requirements,
            "protected": not args.no_protect, "allow_major": bool(upgrade and args.allow_major),
            "lockstep": groups, "planned": [{"name": n, "version": v} for n, v in planned], "limits": limits,
            "before": {d["name"]: d["version"] for d in dists.values()},
            "conflicts_before": before_conflicts, "imports_checked": judged,
            "imports_failing_before": already_failing,
        }
        save(record_dir, record)
        command = pip_install(python, scope, requirements, record_dir / "constraints.txt" if constraints_file else None,
                              upgrade=upgrade)
        result = run(command)

    after = probe(python)["distributions"]
    changes = diff(dists, after)
    after_conflicts = conflicts(python)
    new_conflicts = [line for line in after_conflicts if line not in before_conflicts]
    record.update(command=command, returncode=result.returncode, pip_output=tail(result),
                  changes=changes, conflicts_after=after_conflicts, new_conflicts=new_conflicts)
    print(f"Record: {record_dir}")
    if result.returncode:
        record.update(status="failed", completed_at=now())
        save(record_dir, record)
        print(f"pip failed:\n{tail(result)}", file=sys.stderr)
        if any(changes.values()):
            print("Partial changes (undo with the rollback command and this record):")
            print_changes(changes)
        return 1
    print("Changed:")
    print_changes(changes)

    broken, reason, details = {}, None, []
    if new_conflicts and not args.keep_breakage:
        reason, details = "new dependency conflicts appeared", new_conflicts
    else:
        broken = {module: error for module, error in import_checks(python, judged).items() if error}
        record["import_regressions"] = broken
        if broken and not args.keep_breakage:
            reason, details = "imports that worked before now fail", [f"{m}: {e}" for m, e in broken.items()]
        else:
            print(f"Import check: {len(judged) - len(broken)} of {len(judged)} modules still import")
    if reason:
        steps = restore(python, scope, changes)
        failed = any(step["returncode"] for step in steps)
        record.update(status="rollback-failed" if failed else "rolled-back",
                      rollback={"at": now(), "reason": reason, "steps": steps})
        if not failed:
            record["rolled_back_at"] = record["rollback"]["at"]
        save(record_dir, record)
        outcome = "rollback failed" if failed else "the change was rolled back"
        print(f"{reason[0].upper()}{reason[1:]}, so {outcome}:", file=sys.stderr)
        for line in details:
            print(f"  {line}", file=sys.stderr)
        for step in steps:
            if step["returncode"]:
                print(f"Rollback step failed; inspect the record:\n{step['output']}", file=sys.stderr)
        print("Restored:")
        print_changes(diff(after, probe(python)["distributions"]))
        return 1
    healthy, verification = report_verification(python, packages)
    record.update(status="complete" if healthy else "installed-import-failed", completed_at=now(),
                  verification=verification)
    save(record_dir, record)
    for line in new_conflicts:
        print(f"  kept new conflict: {line}")
    for module, error in broken.items():
        print(f"  kept failing import: {module}: {error}")
    return 0 if healthy else 1


def rollback(args):
    path = Path(args.record)
    path = path / "record.json" if path.is_dir() else path
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ToolError(f"cannot read record {path}: {error}") from None
    if record.get("rolled_back_at"):
        raise ToolError(f"{path} was already rolled back at {record['rolled_back_at']}")
    changes = record.get("changes")
    if not changes or not any(changes.values()):
        raise ToolError(f"{path} records no package changes to undo")
    python, scope = record["interpreter"], record["scope"]
    current = probe(python)["distributions"]
    drift = []
    expected = {name: new for name, (_old, new) in changes["changed"].items()}
    expected.update(changes["added"])
    for name, version in expected.items():
        found = current.get(normalize(name))
        if not found or found["version"] != version:
            drift.append(f"{name}: expected {version}, found {found['version'] if found else 'none'}")
    drift += [f"{name}: expected absent, found {current[normalize(name)]['version']}"
              for name in changes["removed"] if normalize(name) in current]
    if drift and not args.force:
        raise ToolError("packages changed after this operation; review, then rerun with --force:\n  "
                        + "\n  ".join(drift))
    steps = restore(python, scope, changes)
    failed = [step for step in steps if step["returncode"]]
    record["rollback"] = {"at": now(), "reason": "requested", "forced_over": drift,
                          "steps": steps, "conflicts_after": conflicts(python)}
    record["status"] = "rollback-failed" if failed else "rolled-back"
    if not failed:
        record["rolled_back_at"] = record["rollback"]["at"]
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    for step in failed:
        print(step["output"], file=sys.stderr)
    print(f"{'Rollback failed for' if failed else 'Rolled back'} {path.parent}.")
    print_changes({"changed": {n: [new, old] for n, (old, new) in changes["changed"].items()},
                   "added": changes["removed"], "removed": changes["added"]})
    return 1 if failed else 0


def status(args):
    packages = load_manifest(args.manifest, args.set)
    reports, healthy = [], True
    for value in args.python or [sys.executable]:
        try:
            python = resolve_python(value)
            env = probe(python)
        except ToolError as error:
            reports.append({"python": value, "error": str(error)})
            healthy = False
            continue
        report = {"python": python, "version": env["version"], "packages": []}
        try:
            scope = resolve_scope(env)
            folder, on_path = scripts_on_path(env, scope)
            report.update(scope=scope, location=scope_location(env, scope), scripts=folder, scripts_on_path=on_path)
        except ToolError as error:
            report["scope_error"] = str(error)
        for package in packages:
            found = providers(package, env["distributions"])
            check = verify(python, package) if found else {"ok": False, "error": "not installed"}
            healthy &= check["ok"]
            item = {"name": package["name"], "import": package["import"], "import_ok": check["ok"],
                    "distribution": found[0]["name"] if found else None,
                    "version": found[0]["version"] if found else None,
                    "location": found[0]["location"] if found else None,
                    "module_version": check.get("version"), "error": check.get("error"),
                    "duplicates": [f"{dist['name']} {dist['version']}" for dist in found[1:]],
                    "versions_match": len({dist["version"] for dist in found}) <= 1}
            if args.outdated:
                item["latest"] = latest_version(python, item["distribution"] or package["name"])
            report["packages"].append(item)
        report["conflicts"] = conflicts(python)
        reports.append(report)

    if args.json:
        print(json.dumps({"healthy": healthy, "interpreters": reports}, indent=2))
        return 0 if healthy else 1
    for report in reports:
        if "error" in report:
            print(f"{report['python']}: {report['error']}")
            continue
        print(f"{report['python']} (Python {report['version']})")
        if "scope_error" in report:
            print(f"  not a global install target: {report['scope_error']}")
        else:
            print(f"  global scope: {report['scope']} ({report['location']})")
        for item in report["packages"]:
            installed = f"{item['distribution']} {item['version']}" if item["distribution"] else f"{item['name']} missing"
            detail = f"ok {item['module_version']}".rstrip() if item["import_ok"] else item["error"]
            latest = item.get("latest")
            newer = f"; latest {latest}" if latest and latest != item["version"] else ""
            print(f"  {installed:<32} import {item['import']}: {detail}{newer}")
            if item["duplicates"]:
                print(f"    note: {', '.join(item['duplicates'])} also provides {item['import']}; "
                      "they share one folder, so install and update change them together")
                if not item["versions_match"]:
                    print("    warning: their versions differ, so the shared folder holds a mix; "
                          "reinstall one version of each with --force-reinstall --no-deps")
        if report.get("scripts_on_path") is False:
            print(f"  note: {report['scripts']} is not on PATH; command-line entry points need its full path")
        if report["conflicts"]:
            print(f"  dependency conflicts ({len(report['conflicts'])}):")
            for line in report["conflicts"]:
                print(f"    {line}")
    return 0 if healthy else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="tool manifest (default: bundled)")
    common.add_argument("--set", action="append", default=[], help="tool set to use (repeatable; default: all)")

    check = commands.add_parser("status", parents=[common], help="read-only check of each interpreter")
    check.add_argument("--python", action="append", help="interpreter to check (repeatable; default: this one)")
    check.add_argument("--outdated", action="store_true", help="also look up the latest release (uses the network)")
    check.add_argument("--json", action="store_true", help="print machine-readable JSON")

    for name, text in (("install", "install missing packages"), ("update", "upgrade to the latest compatible versions")):
        sub = commands.add_parser(name, parents=[common], help=text)
        sub.add_argument("--python", default=sys.executable, help="target interpreter (default: this one)")
        sub.add_argument("--scope", choices=("auto", "user", "system"), default="auto")
        sub.add_argument("--dry-run", action="store_true", help="show the plan and change nothing")
        sub.add_argument("--allow-venv", action="store_true", help="permit a virtual environment target")
        sub.add_argument("--with", dest="extra", action="append", default=[], metavar="NAME",
                         help="also change this installed distribution, such as one holding a tool back")
        if name == "update":
            sub.add_argument("--allow-major", action="store_true",
                             help="allow new major versions (confirm with the user first)")
        sub.add_argument("--no-protect", action="store_true",
                         help="do not hold installed packages' requirements (needs user approval)")
        sub.add_argument("--keep-breakage", action="store_true",
                         help="keep changes that add conflicts or break imports (needs user approval)")
        sub.add_argument("--record-dir", default=str(DEFAULT_RECORDS), help=f"default: {DEFAULT_RECORDS}")

    undo = commands.add_parser("rollback", help="restore the versions recorded before an install or update")
    undo.add_argument("record", help="record folder or record.json from an install or update")
    undo.add_argument("--force", action="store_true", help="roll back even if packages changed since")

    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)  # keep stdout and stderr in order when piped
    try:
        if args.command == "status":
            return status(args)
        if args.command == "rollback":
            return rollback(args)
        return change(args, upgrade=args.command == "update")
    except ToolError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

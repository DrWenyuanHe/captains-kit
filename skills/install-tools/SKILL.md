---
name: install-tools
description: Check, install, update or roll back the Python packages agents import, such as Pillow, OpenCV and NumPy, at global (user or system) scope without breaking installed packages that depend on them. Use when an agent needs one of these libraries, asks whether they are available, or wants them installed or upgraded for every project. Not for agent skills, project virtual environments or project-pinned dependencies.
---

# Install agent tools

Make the managed packages importable by the interpreters agents actually run,
installed once outside any project environment, and keep them current without
breaking packages that already depend on them. The bundled
[tool manifest](assets/tools.json) lists the packages, their import names and a
smoke test for each. [scripts/tools.py](scripts/tools.py) uses only the
standard library; run it with any Python 3.10 or newer from this skill's folder.

## Resolve the target

1. Find the interpreters agents invoke: `python` on `PATH` first, then `python3`
   or `py -0p` where relevant. Pass each real executable with `--python`; a
   Windows Store alias or launcher is not an interpreter. Check every one the
   request covers, and install into the one agents use unless told otherwise.
2. Global means outside virtual environments. The script chooses the
   interpreter's site-packages when this account can write to it, otherwise the
   per-user site. A virtual environment, an externally managed interpreter
   (PEP 668) or disabled user site stops the operation with an explanation. Read
   [scope and recovery](references/scope-and-recovery.md) before overriding
   any of these.
3. Select tool sets with `--set` (default: all). Add or change packages in the
   library's manifest, not an installed copy, so every agent gets the same list.

## Check

`python scripts/tools.py status --python <interpreter>` is read-only. It reports
each package's distribution, version, location and a fresh-process import with
its smoke test, the global scope, duplicate providers of one module, whether the
scripts folder is on `PATH` and `pip check` conflicts. Add `--outdated` to look up
the latest releases, which uses the network. Exit code 0 means every selected
package imports; 1 means at least one is missing or broken.

## Install or update

1. Preview with `--dry-run`: `install` adds missing packages and raises ones
   below their manifest minimum; `update` moves them to the newest compatible
   release within their current major version. The plan lists the versions pip
   would install, the version limits behind a held-back upgrade and how many
   modules the import check covers. A request to make a tool available is
   satisfied by a working installed version; it is not a request to update it.
2. When an installed package holds a tool back, add it with `--with <name>` so
   both move together, and dry-run again. Cross a major version with
   `--allow-major` only when the user asked for it or confirms, because major
   releases can break code that uses them. Several installed distributions of one
   module change in lockstep; see
   [scope and recovery](references/scope-and-recovery.md#one-module-several-distributions).
3. On Windows, check that no running Python process has the affected packages
   loaded, for example another agent's analysis. Files in use cannot be replaced
   cleanly, and a running process can mix old and new modules.
4. Apply the same command without `--dry-run`. The script imports every installed
   package that is or uses a changed package, records the starting versions,
   applies the plan with the version limits as constraints, then compares
   `pip check` and the imports with the starting state. A new conflict or a
   module that no longer imports rolls the change back automatically.
5. If pip finds no compatible set, nothing changed. Report the limiting packages.
   `--no-protect` and `--keep-breakage` trade another package's correctness for
   this upgrade, so use them only when the user approves that trade.

An install or update request authorizes that change to the named packages at the
resolved global scope, plus the dependencies pip needs and packages the user
agreed to move with `--with`. Removing or downgrading other packages, fixing
pre-existing conflicts, editing `PATH`, elevating privileges and installing into
another interpreter are separate decisions. Report them rather than acting on them.
The import check proves that modules import, not that results are unchanged;
mention it when a project depends on reproducible numerical output.

## Report and recover

Report the interpreter, scope, changed versions, limits, verification results,
remaining conflicts and the record folder the script printed. Records default to
`~/.agent-tools/records/`. To undo, run
`python scripts/tools.py rollback <record folder>`. It refuses when those packages
changed again since the operation; review before adding `--force`. A process that
still has an old version loaded keeps using it until it restarts.

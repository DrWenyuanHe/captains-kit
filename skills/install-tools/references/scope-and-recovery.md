# Scope, protection and recovery

## How the global scope is chosen

The script inspects the target interpreter in a subprocess and resolves the scope
in this order. `--scope user` or `--scope system` narrows the choice; neither
bypasses the stops.

| Condition | Result |
| --- | --- |
| Interpreter runs a virtual environment | Stop. Pass the base interpreter, or `--allow-venv` when the user wants that environment. |
| Interpreter is marked externally managed (PEP 668) | Stop. Use the operating system's packages or ask the user; never add `--break-system-packages` on your own. |
| This account can create a file in its site-packages | `system`: install there, visible to every user of that interpreter. |
| User site-packages is enabled | `user`: `pip install --user`, visible to every project this account runs with that interpreter. |
| Neither | Stop and report; elevated installation is the user's decision. |

The writability test creates and removes one probe file, matching pip's own check.
Python's `tempfile.mkstemp` is not used because on Windows it retries almost
indefinitely in a folder whose ACLs deny writes.

Per-user installs are importable from any working directory, but only for that
interpreter version and account. A service account, sandbox or cloud runtime has
its own home and needs its own installation. `PYTHONNOUSERSITE` or `python -s`
hides user-site packages; `status` shows what the interpreter sees in its default
configuration.

## Platform notes

- **Windows.** An all-users Python under `C:\Python3xx` or `Program Files` usually
  resolves to user scope in `%APPDATA%\Python\Python3xx\site-packages`. Its
  `Scripts` folder is often missing from `PATH`; importing packages does not need
  it, and `status` reports it for command-line entry points. `python3` may be a
  Microsoft Store alias rather than an interpreter. Updating a compiled package
  fails while another process has its files loaded; close those processes, then
  retry.
- **macOS and Linux.** Homebrew and distribution Pythons are commonly externally
  managed. Prefer the distribution's packages there, or an interpreter the user
  manages (for example python.org, pyenv or conda base), and confirm which one
  agents run.

## Protection against breaking installed packages

pip's resolver does not consider the requirements of packages that are already
installed but not part of the request. Upgrading NumPy for OpenCV can therefore
break numba or SciPy, which pin NumPy below a version. Before `install` and
`update`, the script writes every installed package's version requirements to a
pip constraints file, excluding the packages being changed, and names the
package behind each line. `update` also caps each installed target below its
next major version unless `--allow-major` is given. pip then picks the newest
release within those limits, and the plan shows the caps that apply.

When a limit is the only obstacle, the limiting package may have a newer release
that accepts the tool's new version. Add it with `--with <name>`: it becomes a
target, its own old requirements stop constraining the plan, and it stays within
its major version like the others. Other packages that depend on it still
constrain it.

`pip check` only compares declared version ranges. Before applying, the script
also finds every installed distribution that is, or declares a requirement on, a
package in the plan, and imports each of their top-level modules in a fresh
process. Modules that already fail are recorded and not judged. After the
change, the same modules are imported again.

Known limits:

- Requirements that apply only to optional extras, URL requirements and specifiers
  that are not valid PEP 440 are skipped.
- Constraints narrow choices; they do not repair existing conflicts. If installed
  packages already disagree about a shared dependency, resolution can fail. That
  failure changes nothing; report the disagreement.
- A new `pip check` line, or a module that imported before and fails after,
  triggers an automatic rollback unless `--keep-breakage` was approved.
  Pre-existing conflicts and failures stay as they were and appear in the report.
- An import proves the compiled parts load against the new versions. It does not
  exercise every function or prove numerical results are unchanged.

## One module, several distributions

Some modules have several interchangeable distributions. OpenCV ships as
`opencv-python`, `opencv-python-headless`, `opencv-contrib-python` and
`opencv-contrib-python-headless`, all writing the same `cv2` package folder.
The manifest lists the variants as `alternatives`. When one is installed, the
script updates that one instead of adding another. When several are installed,
their files overwrite each other, and upgrading or uninstalling one replaces or
deletes files the others rely on. `install` and `update` therefore change all
installed variants in one pip run and stop before changing anything if they
would end on different versions. `status` notes the shared folder and warns when
the versions already differ.

Different installed packages can need different variants, as when one requires
`opencv-python` and another `opencv-python-headless`; keeping both at one version
is then the right outcome. Otherwise, consolidate by checking `Required-by` in
`pip show <name>`, ask the user before uninstalling one, then reinstall the kept
variant with `--force-reinstall --no-deps` so its files are complete.

## Records and rollback

Each applied install or update writes `record.json`, and its constraints file,
to a timestamped folder under `~/.agent-tools/records/` or `--record-dir`.
The record holds the interpreter, scope, manifest path, requirements, `--with`
packages, lockstep groups, the plan and limits, every installed version before
the change, the pip command and output, the changed/added/removed versions,
`pip check` before and after, the modules import-checked, those already failing,
any regressions and verification results. Dry runs write no record.

`rollback <record folder>` uninstalls packages the operation added and
reinstalls the recorded previous versions with `--no-deps`. It first compares
the current versions with the record and refuses if any have changed since,
because restoring would overwrite later work. Review the drift before using
`--force`. Rollback needs the previous versions from pip's cache or the index.
The rollback result is added to the same record. A successful rollback records
`rolled_back_at` and cannot be repeated. Failed restoration records
`status: rollback-failed` without that completion timestamp, so it can be retried.
If some steps succeeded before the failure, review the resulting version drift
before retrying with `--force`.

## Maintain the manifest

Each entry in [the manifest](../assets/tools.json) belongs to a named set and has:

| Field | Meaning |
| --- | --- |
| `name` | Distribution name on the package index. |
| `requirement` | Optional; the name plus a minimum or range, without extras or markers. |
| `import` | Module agents import; verification imports it in a fresh process. |
| `alternatives` | Optional; other distributions providing the same module. |
| `smoke` | Optional Python run after import, with the module bound to `module`. |

Keep entries to broadly shared tools. Dependencies of one skill stay with that
skill, as in `nature-figure`'s own requirements file. Use a scheduled `status` run
for routine checks; apply updates after reviewing a dry run.

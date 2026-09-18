"""Exercise installed figure helpers without requiring plotting packages."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from install import install

RSCRIPT = os.environ.get("RSCRIPT") or shutil.which("Rscript")


@unittest.skipUnless(RSCRIPT, "Rscript is not available; set RSCRIPT to run the R integration check")
class NatureFigureInstallTests(unittest.TestCase):
    def test_rendered_r_panels_have_physical_bounds_and_pass_the_auditor(self):
        packages = subprocess.run(
            [RSCRIPT, "--vanilla", "-e", "quit(status=if (all(vapply(c('ggplot2', 'patchwork', 'jsonlite'), "
             "requireNamespace, logical(1), quietly=TRUE))) 0 else 42)"],
            capture_output=True, text=True, timeout=30,
        )
        if packages.returncode == 42:
            self.skipTest("The rendered R check needs ggplot2, patchwork and jsonlite")
        self.assertEqual(packages.returncode, 0, packages.stderr)
        with tempfile.TemporaryDirectory(prefix="nature figure render ") as temporary:
            project = Path(temporary).resolve()
            installed = install(project, "codex", ROOT / "skills" / "nature-figure", "nature-figure")
            helper = (installed / "scripts" / "panel_alignment.R").relative_to(project)
            code = '''
library(ggplot2)
library(patchwork)
source(HELPER)
samples <- data.frame(time = 1:6, signal = c(2, 3, 3.5, 4.4, 5, 5.5))
panel <- ggplot(samples, aes(time, signal)) + geom_point() + theme_classic()
figure <- panel | panel
require_patchwork_panel_alignment(
  figure, "layout.json", "report.json", 7, 3, python = PYTHON, strict = TRUE
)
write_patchwork_panel_layout(figure, "larger.json", 9, 4)
ggsave("figure.pdf", figure, width = 7, height = 3)
'''.replace("HELPER", json.dumps(helper.as_posix())).replace("PYTHON", json.dumps(Path(sys.executable).as_posix()))
            probe = project / "render.R"
            probe.write_text(code, encoding="utf-8", newline="\n")
            result = subprocess.run([RSCRIPT, "--vanilla", str(probe)], cwd=project,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifest = json.loads((project / "layout.json").read_text())
            larger = json.loads((project / "larger.json").read_text())
            self.assertEqual(len(manifest["panels"]), 2)
            for panel, expanded in zip(manifest["panels"], larger["panels"]):
                left, bottom, right, top = panel["bbox_pt"]
                self.assertGreater(right - left, 100)
                self.assertGreater(top - bottom, 100)
                self.assertGreaterEqual(left, 0)
                self.assertGreaterEqual(bottom, 0)
                self.assertLessEqual(right, 7 * 72)
                self.assertLessEqual(top, 3 * 72)
                self.assertGreater(expanded["bbox_pt"][2] - expanded["bbox_pt"][0], right - left)
            self.assertTrue((project / "figure.pdf").read_bytes().startswith(b"%PDF"))

    def test_r_auditor_resolves_after_install_and_working_directory_change(self):
        with tempfile.TemporaryDirectory(prefix="nature figure ") as temporary:
            root = Path(temporary).resolve()
            for host in ("codex", "claude"):
                with self.subTest(host=host):
                    project = root / host
                    project.mkdir()
                    installed = install(project, host, ROOT / "skills" / "nature-figure", "nature-figure")
                    helper = (installed / "scripts" / "panel_alignment.R").relative_to(project)
                    code = '''
load_helper <- function(path) source(path, local = .GlobalEnv)
load_helper(HELPER)
dir.create("different working directory")
setwd("different working directory")
write_patchwork_panel_layout <- function(...) stop("reached layout measurement", call. = FALSE)
result <- tryCatch(
  require_patchwork_panel_alignment(NULL, "layout.json", "report.json", 4, 3, python = "python"),
  error = function(error) conditionMessage(error)
)
stopifnot(identical(result, "reached layout measurement"))
'''.replace("HELPER", json.dumps(helper.as_posix()))
                    probe = project / "check.R"
                    probe.write_text(code, encoding="utf-8", newline="\n")
                    result = subprocess.run([RSCRIPT, "--vanilla", str(probe)], cwd=project,
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

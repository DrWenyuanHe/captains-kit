"""Exercise the image-zoom helper on synthetic images; image tests skip without Pillow."""

import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from install import install

SCRIPT = ROOT / "skills" / "image-zoom" / "scripts" / "imgzoom.py"
HAS_PIL = importlib.util.find_spec("PIL") is not None
HAS_PYMUPDF = importlib.util.find_spec("pymupdf") is not None or importlib.util.find_spec("fitz") is not None


def run(*args, script=SCRIPT):
    return subprocess.run([sys.executable, str(script), *map(str, args)],
                          capture_output=True, text=True, timeout=120)


class DoctorTests(unittest.TestCase):
    def test_doctor_reports_requirements_and_install_commands(self):
        result = run("doctor")
        self.assertIn("Python 3.10+", result.stdout)
        for label in ("ImageMagick", "Poppler", "Tesseract OCR", "LibreOffice", "pandoc"):
            self.assertIn(label, result.stdout)
        if HAS_PIL:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertEqual(result.returncode, 1)
            self.assertIn("python -m pip install pillow", result.stdout)

    def test_commands_without_pillow_explain_the_fix(self):
        if HAS_PIL:
            self.skipTest("Pillow is installed")
        result = run("info", SCRIPT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pip install pillow", result.stderr)


@unittest.skipUnless(HAS_PIL, "Pillow is not installed")
class ImageZoomTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image, ImageDraw

        self.temp = tempfile.TemporaryDirectory(prefix="image zoom ")
        self.dir = Path(self.temp.name)
        self.out = self.dir / "out"
        old = Image.new("RGB", (3200, 2400), "white")
        ImageDraw.Draw(old).rectangle((100, 100, 600, 400), fill="black")
        new = old.copy()
        ImageDraw.Draw(new).rectangle((2000, 1500, 2300, 1700), fill="red")
        self.old, self.new = self.dir / "fig_v1.png", self.dir / "fig_v2.png"
        old.save(self.old, dpi=(600, 600))
        new.save(self.new, dpi=(600, 600))

    def tearDown(self):
        self.temp.cleanup()

    def written(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return [Path(line.split("  ")[0]) for line in result.stdout.splitlines() if line.endswith(".png") or ".png  " in line]

    def test_info_reports_size_and_print_size(self):
        result = run("info", self.old)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("3200x2400 px", result.stdout)
        self.assertIn("5.33x4.00 in", result.stdout)

    def test_grid_fits_the_view(self):
        from PIL import Image

        (path,) = self.written(run("grid", self.old, "--out", self.out))
        with Image.open(path) as im:
            self.assertLessEqual(max(im.size), 1568)

    def test_zoom_enlarges_the_box_and_clips_to_the_image(self):
        from PIL import Image

        result = run("zoom", self.old, "--box", "100,100,300,200", "--out", self.out)
        (path,) = self.written(result)
        with Image.open(path) as im:
            self.assertEqual(im.size, (1568, 784))
            self.assertEqual(im.getpixel((10, 10)), (0, 0, 0))
        clipped = run("zoom", self.old, "--box", "0.9,0.9,1.5,1.5", "--frac", "--out", self.out)
        self.assertIn("box (2880,2160)-(3200,2400)", clipped.stdout)
        empty = run("zoom", self.old, "--box", "5000,5000,6000,6000", "--out", self.out)
        self.assertNotEqual(empty.returncode, 0)

    def test_tiles_cover_the_image(self):
        paths = self.written(run("tiles", self.old, "--rows", "2", "--cols", "3", "--out", self.out))
        self.assertEqual(len(paths), 6)
        self.assertTrue(all(p.is_file() for p in paths))

    def test_diff_finds_the_changed_region(self):
        result = run("diff", self.old, self.new, "--out", self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        boxes = [tuple(map(int, m)) for m in re.findall(r"box (\d+),(\d+),(\d+),(\d+)", result.stdout)]
        self.assertEqual(len(boxes), 1, result.stdout)
        x0, y0, x1, y1 = boxes[0]
        self.assertTrue(x0 <= 2000 and y0 <= 1500 and x1 >= 2300 and y1 >= 1700, boxes)
        self.assertTrue(x1 - x0 < 400 and y1 - y0 < 300, boxes)
        same = run("diff", self.old, self.old, "--out", self.out)
        self.assertIn("no changed regions", same.stdout)

    @unittest.skipUnless(HAS_PYMUPDF, "PyMuPDF is not installed")
    def test_pdf_page_region_renders_at_the_requested_dpi(self):
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf
        pdf = self.dir / "proof.pdf"
        doc = pymupdf.open()
        doc.new_page(width=612, height=792).insert_text((72, 72), "Figure 1")
        doc.save(pdf)
        doc.close()
        result = run("zoom", pdf, "--dpi", "300", "--box", "0,0,0.5,0.25", "--frac", "--out", self.out)
        self.assertIn("box (0,0)-(1275,825)", result.stdout)

    def test_installed_copy_runs(self):
        project = self.dir / "project"
        project.mkdir()
        installed = install(project, "claude", ROOT / "skills" / "image-zoom", "image-zoom")
        result = run("info", self.old, script=installed / "scripts" / "imgzoom.py")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

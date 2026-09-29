---
name: image-zoom
description: Inspect images, figures and PDF pages at full resolution: coordinate-grid overview, crop-and-zoom into a region, split into tiles, render a PDF page region at high DPI, and diff two versions of a figure to find what changed. Use whenever a figure, screenshot, rendered page or PDF has detail finer than the agent's downscaled image view (small labels, tick marks, overlaps, clipping, 300-600 dpi exports), when checking a figure revision against the previous version, or when asked to zoom, crop, magnify or compare images. Not for drawing or restyling figures.
---

# Image zoom

Agents see an image scaled so its long edge is about 1568 px. A 600-dpi journal figure
(for example 4251x4726 px) is shown at about one third, so anything under ~3 px (hairlines,
superscripts, minus signs, overlapping labels) is invisible. This skill brings the
full-resolution pixels into view.

Script: `scripts/imgzoom.py` in this skill's folder. It needs Python 3.10+ and Pillow;
PDF input also needs PyMuPDF. Run `python scripts/imgzoom.py doctor` first on a new
machine: it reports what is installed and prints the install command for anything missing.
[README.md](README.md) lists every requirement and companion tool.

Pass `--out <scratch folder>` so outputs stay out of the user's project (default: the system
temp folder's `imgzoom/`). Each command prints the PNG paths it wrote; open them with the
image viewer (for example the Read tool).

## Workflow

1. `info FILE`: pixel size, DPI, print size, and how much the default view loses.
2. `grid FILE`: the whole image with gridlines labelled in **source pixel** coordinates.
   Look at it, then pick boxes from the labels.
3. `zoom FILE --box x0,y0,x1,y1 [--grid]`: crop that region and enlarge it to fill the view
   (up to 8x, `--max-scale`; nearest-neighbour above 3x so pixel edges stay honest).
   `--frac` takes 0-1 fractions (`--box 0,0,0.5,0.5` is the top-left quarter).
4. `tiles FILE [--rows R --cols C]`: full-resolution tiles with 24 px overlap, for a
   systematic sweep (for example every label in a figure before submission).
5. `diff OLD NEW`: red marks changed pixels over a faded OLD, and numbered blue boxes mark changed
   regions, largest first. It also writes a side-by-side view and prints the changed-pixel
   percentage and each region's box; feed a box straight into `zoom` on both files.
   `--threshold` (default 24 grey levels) ignores antialiasing noise.

PDF input: add `--page N` (1-based) and `--dpi` (default 200; use 600 to check vector text).
For a TIFF with several frames, `--page` picks the frame.

```bash
python scripts/imgzoom.py grid fig_v12.png --out "$SCRATCH/imgzoom"
python scripts/imgzoom.py zoom fig_v12.png --box 2700,1380,3400,1480 --grid --out "$SCRATCH/imgzoom"
python scripts/imgzoom.py diff fig_v11.png fig_v12.png --out "$SCRATCH/imgzoom"
python scripts/imgzoom.py zoom proof.pdf --page 7 --dpi 600 --box 0,0.6,1,1 --frac --out "$SCRATCH/imgzoom"
```

## Companion tools

Use these when `doctor` reports them installed:

- **ImageMagick** (`magick`; on Windows a bare `convert` is the disk tool, not ImageMagick):
  `magick compare -metric AE a.png b.png diff.png`, `magick montage`, colour-space
  conversion, `magick identify -verbose`.
- **Poppler**: `pdftoppm -r 600 -f 3 -l 3 -png in.pdf out`, `pdfimages -list` (embedded
  image DPI), `pdffonts` (font embedding).
- **Tesseract** (`tesseract img.png - --psm 11`): OCR every label to spell-check it, or use
  `tsv` output to find text coordinates and zoom there. Give it the raw image or a crop made
  with `--max-scale 1` and no `--grid`; gridlines and nearest-neighbour upscaling make it
  return nothing.
- **LibreOffice** (`soffice --headless --convert-to pdf x.docx`): Word to PDF for page
  proofs. Keep `-env:UserInstallation` on a short path; a profile folder under a long path
  (over ~120 characters on Windows) crashes it.
- **pandoc** (`pandoc x.docx -t markdown`): a quick text read of a Word file.

If `doctor` says a tool is "installed, not on PATH", the shell was opened before the install:
call the printed path directly or open a new terminal.

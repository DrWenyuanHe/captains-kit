#!/usr/bin/env python3
"""imgzoom: inspect images and PDF pages at full resolution for an agent that sees
images downscaled to ~1568 px on the long edge.

Subcommands
  info  FILE                          size, mode, DPI, page count
  grid  FILE [--step N]               overview with labelled pixel gridlines
  zoom  FILE --box x0,y0,x1,y1        crop a region and upscale it to fit the view
  tiles FILE [--rows R --cols C]      split into full-resolution tiles
  diff  A B                           highlight changed pixels, report changed boxes
  doctor                              report required and companion tools, with install commands
Input can be PNG/JPG/TIFF/BMP/GIF/WebP or PDF (use --page, 1-based; --dpi).
--box accepts pixels, or fractions with --frac (0-1). Every command prints the
paths it wrote; open them with the agent's image viewer (for example the Read tool).
Requires Python 3.10+ and Pillow; PDF input also needs PyMuPDF.
"""
import argparse
import glob
import os
import platform
import shutil
import sys
import tempfile

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps
except ImportError:  # doctor still runs and explains how to install it
    Image = None
else:
    Image.MAX_IMAGE_PIXELS = None
VIEW = 1568  # long edge the model actually sees


def open_pdf(path):
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # PyMuPDF before 1.24.3
        except ImportError:
            sys.exit("PDF input needs PyMuPDF: python -m pip install pymupdf")
    return pymupdf.open(path)


def out_dir(args):
    d = args.out or os.path.join(tempfile.gettempdir(), "imgzoom")
    os.makedirs(d, exist_ok=True)
    return d


def stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def load(path, page=1, dpi=200):
    if path.lower().endswith(".pdf"):
        doc = open_pdf(path)
        if not 1 <= page <= doc.page_count:
            sys.exit(f"page {page} out of range 1..{doc.page_count}")
        pix = doc[page - 1].get_pixmap(dpi=dpi, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    im = Image.open(path)
    if getattr(im, "n_frames", 1) > 1:
        im.seek(page - 1)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, "white")
        rgba = im.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return im.convert("RGB")


def font(size):
    for f in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def fit(im, limit=VIEW, upscale=False):
    s = limit / max(im.size)
    if s >= 1 and not upscale:
        return im, 1.0
    size = (max(1, round(im.width * s)), max(1, round(im.height * s)))
    # nearest keeps pixel edges honest when enlarging small crops a lot
    rs = Image.NEAREST if s >= 3 else Image.LANCZOS
    return im.resize(size, rs), s


def parse_box(text, im, frac):
    try:
        v = [float(t) for t in text.split(",")]
    except ValueError:
        sys.exit("--box must be x0,y0,x1,y1")
    if len(v) != 4:
        sys.exit("--box must be x0,y0,x1,y1")
    if frac:
        v = [v[0] * im.width, v[1] * im.height, v[2] * im.width, v[3] * im.height]
    x0, y0, x1, y1 = (round(t) for t in v)
    x0, x1 = (min(max(t, 0), im.width) for t in sorted((x0, x1)))
    y0, y1 = (min(max(t, 0), im.height) for t in sorted((y0, y1)))
    if x1 - x0 < 1 or y1 - y0 < 1:
        sys.exit(f"empty box after clipping to image {im.width}x{im.height}")
    return x0, y0, x1, y1


def nice_step(extent):
    raw = extent / 10
    for m in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        if m >= raw:
            return m
    return 10000


def draw_grid(view, s, src_size, step, origin=(0, 0)):
    """Draw gridlines on a scaled view, labelled in ORIGINAL-image pixel coordinates."""
    view = view.convert("RGB")
    d = ImageDraw.Draw(view, "RGBA")
    f = font(max(11, min(18, view.width // 80)))
    ox, oy = origin
    first_x = (-ox) % step
    first_y = (-oy) % step
    for x in range(first_x, src_size[0] + 1, step):
        vx = x * s
        d.line([(vx, 0), (vx, view.height)], fill=(255, 0, 255, 110), width=1)
        d.text((vx + 2, 2), str(ox + x), fill=(200, 0, 200, 255), font=f,
               stroke_width=2, stroke_fill=(255, 255, 255, 255))
    for y in range(first_y, src_size[1] + 1, step):
        vy = y * s
        d.line([(0, vy), (view.width, vy)], fill=(0, 160, 255, 110), width=1)
        d.text((2, vy + 2), str(oy + y), fill=(0, 110, 220, 255), font=f,
               stroke_width=2, stroke_fill=(255, 255, 255, 255))
    return view


def cmd_info(a):
    if a.file.lower().endswith(".pdf"):
        doc = open_pdf(a.file)
        for i, p in enumerate(doc, 1):
            r = p.rect
            print(f"page {i}: {r.width:.0f}x{r.height:.0f} pt "
                  f"({r.width / 72:.2f}x{r.height / 72:.2f} in), images={len(p.get_images())}")
        return
    im = Image.open(a.file)
    dpi = im.info.get("dpi")
    print(f"{a.file}\n  size {im.width}x{im.height} px, mode {im.mode}, format {im.format}, "
          f"frames {getattr(im, 'n_frames', 1)}, dpi {dpi}")
    if dpi and dpi[0]:
        print(f"  print size {im.width / dpi[0]:.2f}x{im.height / dpi[1]:.2f} in "
              f"({im.width / dpi[0] * 25.4:.0f}x{im.height / dpi[1] * 25.4:.0f} mm)")
    print(f"  model view scale {min(1, VIEW / max(im.size)):.3f} "
          f"(details under ~{max(1, round(max(im.size) / VIEW))} px are lost without zoom)")


def cmd_grid(a):
    im = load(a.file, a.page, a.dpi)
    step = a.step or nice_step(max(im.size))
    view, s = fit(im)
    view = draw_grid(view, s, im.size, step)
    p = os.path.join(out_dir(a), f"{stem(a.file)}_p{a.page}_grid.png")
    view.save(p)
    print(p)
    print(f"source {im.width}x{im.height} px, grid step {step} px, shown at {s:.3f}x")


def cmd_zoom(a):
    im = load(a.file, a.page, a.dpi)
    x0, y0, x1, y1 = parse_box(a.box, im, a.frac)
    crop = im.crop((x0, y0, x1, y1))
    view, s = fit(crop, limit=min(VIEW, round(max(crop.size) * a.max_scale)), upscale=True)
    if a.grid:
        view = draw_grid(view, s, crop.size, a.grid_step or nice_step(max(crop.size)), (x0, y0))
    tag = f"{x0}_{y0}_{x1}_{y1}"
    p = os.path.join(out_dir(a), f"{stem(a.file)}_p{a.page}_zoom_{tag}.png")
    view.save(p)
    print(p)
    print(f"box ({x0},{y0})-({x1},{y1}) = {x1 - x0}x{y1 - y0} px, shown at {s:.2f}x")


def cmd_tiles(a):
    im = load(a.file, a.page, a.dpi)
    rows = a.rows or max(1, -(-im.height // VIEW))
    cols = a.cols or max(1, -(-im.width // VIEW))
    ov = a.overlap
    tw, th = -(-im.width // cols), -(-im.height // rows)
    d = out_dir(a)
    for r in range(rows):
        for c in range(cols):
            box = (max(0, c * tw - ov), max(0, r * th - ov),
                   min(im.width, (c + 1) * tw + ov), min(im.height, (r + 1) * th + ov))
            view, s = fit(im.crop(box))
            p = os.path.join(d, f"{stem(a.file)}_p{a.page}_tile_r{r + 1}c{c + 1}.png")
            view.save(p)
            print(f"{p}  box={box}  scale={s:.2f}")


def boxes_from_mask(mask, min_area):
    """Connected changed regions on a coarse grid (fast, no scipy)."""
    cell = 8
    w, h = mask.size
    small = mask.resize((-(-w // cell), -(-h // cell)), Image.BOX).point(lambda v: 255 if v else 0)
    sw, sh = small.size
    px = small.load()
    seen = set()
    out = []
    for y in range(sh):
        for x in range(sw):
            if px[x, y] and (x, y) not in seen:
                stack = [(x, y)]
                seen.add((x, y))
                bx0 = bx1 = x
                by0 = by1 = y
                while stack:
                    cx, cy = stack.pop()
                    bx0, bx1 = min(bx0, cx), max(bx1, cx)
                    by0, by1 = min(by0, cy), max(by1, cy)
                    for nx in range(cx - 1, cx + 2):
                        for ny in range(cy - 1, cy + 2):
                            if 0 <= nx < sw and 0 <= ny < sh and px[nx, ny] and (nx, ny) not in seen:
                                seen.add((nx, ny))
                                stack.append((nx, ny))
                b = (bx0 * cell, by0 * cell, min(w, (bx1 + 1) * cell), min(h, (by1 + 1) * cell))
                if (b[2] - b[0]) * (b[3] - b[1]) >= min_area:
                    out.append(b)
    return sorted(out, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))


def cmd_diff(a):
    A = load(a.a, a.page, a.dpi)
    B = load(a.b, a.page, a.dpi)
    if A.size != B.size:
        print(f"size differs: A {A.width}x{A.height}, B {B.width}x{B.height}; B resized to A for the diff")
        B = B.resize(A.size, Image.LANCZOS)
    delta = ImageChops.difference(A, B).convert("L")
    mask = delta.point(lambda v: 255 if v > a.threshold else 0)
    changed = mask.histogram()[255]
    total = A.width * A.height
    boxes = boxes_from_mask(mask, a.min_area)
    faded = Image.blend(A, Image.new("RGB", A.size, "white"), 0.6)
    red = Image.new("RGB", A.size, (230, 0, 60))
    over = Image.composite(red, faded, mask)
    dr = ImageDraw.Draw(over)
    for i, b in enumerate(boxes[:30], 1):
        dr.rectangle(b, outline=(0, 120, 255), width=max(2, A.width // 600))
        dr.text((b[0] + 3, b[1] + 3), str(i), fill=(0, 90, 220), font=font(max(14, A.width // 90)))
    d = out_dir(a)
    p = os.path.join(d, f"diff_{stem(a.b)[:40]}.png")
    fit(over)[0].save(p)
    print(p)
    gap = 12
    side = Image.new("RGB", (A.width * 2 + gap, A.height), "white")
    side.paste(A, (0, 0))
    side.paste(B, (A.width + gap, 0))
    p2 = os.path.join(d, f"diff_{stem(a.b)[:40]}_side.png")
    fit(side)[0].save(p2)
    print(p2)
    print(f"changed pixels {changed}/{total} ({100 * changed / total:.3f}%) at threshold {a.threshold}")
    for i, b in enumerate(boxes[:30], 1):
        print(f"  region {i}: box {b[0]},{b[1]},{b[2]},{b[3]}  ({b[2] - b[0]}x{b[3] - b[1]} px)")
    if len(boxes) > 30:
        print(f"  ... {len(boxes) - 30} more regions")
    if not boxes:
        print("  no changed regions above --min-area")


# (label, need, how to find it, install per platform: Windows winget id, macOS brew, Debian/Ubuntu apt)
COMPANIONS = [
    ("ImageMagick", "compare, montage and convert images (magick)",
     ["magick"], [r"%ProgramFiles%\ImageMagick-*\magick.exe"],
     ("winget install -e --id ImageMagick.ImageMagick", "brew install imagemagick", "sudo apt install imagemagick")),
    ("Poppler", "PDF page rendering, image DPI and font embedding (pdftoppm, pdfimages, pdffonts)",
     ["pdftoppm"], [r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\oschwartz10612.Poppler*\poppler-*\Library\bin\pdftoppm.exe"],
     ("winget install -e --id oschwartz10612.Poppler", "brew install poppler", "sudo apt install poppler-utils")),
    ("Tesseract OCR", "read the text in figures to spell-check labels or locate them (tesseract)",
     ["tesseract"], [r"%ProgramFiles%\Tesseract-OCR\tesseract.exe"],
     ("winget install -e --id UB-Mannheim.TesseractOCR", "brew install tesseract", "sudo apt install tesseract-ocr")),
    ("LibreOffice", "headless Word/PowerPoint to PDF for page proofs (soffice)",
     ["soffice", "libreoffice"],
     [r"%ProgramFiles%\LibreOffice\program\soffice.exe", "/Applications/LibreOffice.app/Contents/MacOS/soffice"],
     ("winget install -e --id TheDocumentFoundation.LibreOffice", "brew install --cask libreoffice",
      "sudo apt install libreoffice")),
    ("pandoc", "read Word documents as text (pandoc)",
     ["pandoc"], [r"%LOCALAPPDATA%\Pandoc\pandoc.exe", r"%ProgramFiles%\Pandoc\pandoc.exe"],
     ("winget install -e --id JohnMacFarlane.Pandoc", "brew install pandoc", "sudo apt install pandoc")),
]


def find_tool(names, fallbacks):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for pattern in fallbacks:  # standard install folders, for shells opened before the install
        hits = sorted(glob.glob(os.path.expandvars(pattern)))
        if hits:
            return hits[-1]
    return None


def pymupdf_version():
    try:
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf
    except ImportError:
        return None
    return getattr(pymupdf, "VersionBind", "installed")


def cmd_doctor(a):
    col = {"Windows": 0, "Darwin": 1}.get(platform.system(), 2)
    todo = []
    print(f"Required ({sys.executable})")
    py_ok = sys.version_info >= (3, 10)
    print(f"  [{'ok' if py_ok else 'MISSING'}] Python 3.10+ (found {platform.python_version()})")
    if Image is None:
        print("  [MISSING] Pillow")
        todo.append("python -m pip install pillow")
    else:
        import PIL
        print(f"  [ok] Pillow {PIL.__version__}")
    print("For PDF input")
    version = pymupdf_version()
    if version:
        print(f"  [ok] PyMuPDF {version}")
    else:
        print("  [--] PyMuPDF")
        todo.append("python -m pip install pymupdf")
    print("Companion command-line tools (optional)")
    for label, need, names, fallbacks, install in COMPANIONS:
        path = find_tool(names, fallbacks)
        if path:
            on_path = any(shutil.which(n) for n in names)
            print(f"  [ok] {label}: {path}{'' if on_path else '  (installed, not on PATH)'}")
        else:
            print(f"  [--] {label}: {need}")
            todo.append(install[col])
    if todo:
        print("\nTo install what is missing:")
        for line in todo:
            print(f"  {line}")
    sys.exit(0 if py_ok and Image is not None else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, two=False):
        if two:
            p.add_argument("a")
            p.add_argument("b")
        else:
            p.add_argument("file")
        p.add_argument("--page", type=int, default=1, help="PDF page / TIFF frame, 1-based")
        p.add_argument("--dpi", type=int, default=200, help="PDF render resolution")
        p.add_argument("--out", help="output folder (default: <system temp>/imgzoom)")
        return p

    common(sub.add_parser("info"))
    g = common(sub.add_parser("grid"))
    g.add_argument("--step", type=int, help="grid spacing in source pixels")
    z = common(sub.add_parser("zoom"))
    z.add_argument("--box", required=True, help="x0,y0,x1,y1 in source pixels (or fractions with --frac)")
    z.add_argument("--frac", action="store_true")
    z.add_argument("--grid", action="store_true", help="overlay coordinates on the zoomed view")
    z.add_argument("--grid-step", type=int)
    z.add_argument("--max-scale", type=float, default=8.0, help="cap on enlargement factor")
    t = common(sub.add_parser("tiles"))
    t.add_argument("--rows", type=int)
    t.add_argument("--cols", type=int)
    t.add_argument("--overlap", type=int, default=24)
    df = common(sub.add_parser("diff"), two=True)
    df.add_argument("--threshold", type=int, default=24, help="per-pixel grey delta counted as a change")
    df.add_argument("--min-area", type=int, default=64)

    sub.add_parser("doctor")

    a = ap.parse_args()
    if a.cmd != "doctor" and Image is None:
        sys.exit("imgzoom needs Pillow: python -m pip install pillow (run 'imgzoom.py doctor' for the full list)")
    {"info": cmd_info, "grid": cmd_grid, "zoom": cmd_zoom, "tiles": cmd_tiles, "diff": cmd_diff,
     "doctor": cmd_doctor}[a.cmd](a)


if __name__ == "__main__":
    main()

# Image zoom

Lets an agent inspect figures, screenshots and PDF pages at full resolution: a labelled
coordinate grid, crop-and-zoom, tiles, high-DPI PDF regions and a region-level diff between two
versions of a figure. The instructions for the agent are in [SKILL.md](SKILL.md).

## Requirements

Check a machine with:

```sh
python scripts/imgzoom.py doctor
```

It lists what is found (including tools installed in their standard folders but not yet on
`PATH`) and prints the install command for everything missing on the current OS. It exits 1
only if a required item is missing.

| Tool | Needed for | Windows (winget) | macOS (Homebrew) | Debian/Ubuntu |
| --- | --- | --- | --- | --- |
| Python 3.10+ | everything | `winget install -e --id Python.Python.3.12` | `brew install python` | `sudo apt install python3` |
| Pillow | **required**: every command | `python -m pip install pillow` | same | same |
| PyMuPDF | PDF input (`--page`, `--dpi`) | `python -m pip install pymupdf` | same | same |
| ImageMagick 7 | optional: `magick compare`, montages, conversions | `winget install -e --id ImageMagick.ImageMagick` | `brew install imagemagick` | `sudo apt install imagemagick` |
| Poppler | optional: `pdftoppm`, `pdfimages`, `pdffonts` | `winget install -e --id oschwartz10612.Poppler` | `brew install poppler` | `sudo apt install poppler-utils` |
| Tesseract OCR | optional: reading figure text | `winget install -e --id UB-Mannheim.TesseractOCR` | `brew install tesseract` | `sudo apt install tesseract-ocr` |
| LibreOffice | optional: Word to PDF proofs | `winget install -e --id TheDocumentFoundation.LibreOffice` | `brew install --cask libreoffice` | `sudo apt install libreoffice` |
| pandoc | optional: Word to text | `winget install -e --id JohnMacFarlane.Pandoc` | `brew install pandoc` | `sudo apt install pandoc` |

The Python packages are also in [requirements.txt](requirements.txt)
(`python -m pip install -r requirements.txt`).

Windows notes, from installing these on Windows 11:

- The Tesseract and LibreOffice installers do not add themselves to `PATH`. Add
  `C:\Program Files\Tesseract-OCR` and `C:\Program Files\LibreOffice\program` to the user
  `PATH`, or call the full paths that `doctor` prints.
- Shells opened before an install do not see new `PATH` entries. Open a new terminal.
- `convert` on Windows is the file-system tool, not ImageMagick; use `magick`.

## Use

After installing the skill, ask the agent to zoom into, audit or compare a figure. For example:
"check every label in figure 3 at full resolution" or "what changed between figure_v11.png and
figure_v12.png?". The agent runs `grid`, `zoom`, `tiles` or `diff` and reads the outputs.

The script can also be run by hand; `python scripts/imgzoom.py --help` lists the commands.

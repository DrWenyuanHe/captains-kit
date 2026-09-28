#!/usr/bin/env python3
"""Scientific manuscript workflow tools. Run `python msw.py --help`.

Standard library only (Python 3.10+). Every command writes new files and never
overwrites or deletes an existing one.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mswlib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

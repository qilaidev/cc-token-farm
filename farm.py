#!/usr/bin/env python3
"""Thin launcher for source checkouts without install.

Prefer: pip install -e . && cc-token-farm …
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cc_token_farm.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

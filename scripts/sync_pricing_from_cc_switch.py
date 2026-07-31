#!/usr/bin/env python3
"""Convenience wrapper: export CC Switch model_pricing → bundled JSON."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cc_token_farm.cli import main

if __name__ == "__main__":
    out = ROOT / "src" / "cc_token_farm" / "data" / "pricing.json"
    raise SystemExit(main(["sync-pricing", "-o", str(out), *sys.argv[1:]]))

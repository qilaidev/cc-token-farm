from __future__ import annotations

import re
from pathlib import Path


def parse_token_amount(text: str) -> int:
    """Parse human amounts: 1e9, 20亿, 2B, 500M, 100k, 12345."""
    s = text.strip().replace(",", "").replace("_", "").lower()
    if not s:
        raise ValueError("empty token amount")

    # Chinese units
    cn = {"亿": 100_000_000, "万": 10_000, "千": 1_000}
    for unit, mul in cn.items():
        if s.endswith(unit):
            return int(float(s[: -len(unit)]) * mul)

    m = re.fullmatch(r"([0-9]*\.?[0-9]+)([kmbt]?)", s)
    if not m:
        # scientific
        try:
            return int(float(s))
        except ValueError as e:
            raise ValueError(f"invalid token amount: {text}") from e

    num = float(m.group(1))
    suf = m.group(2)
    mult = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "t": 1_000_000_000_000}[suf]
    return int(num * mult)


def parse_money(text: str) -> float:
    s = text.strip().replace(",", "").replace("$", "").replace("usd", "").strip()
    return float(s)


def human_int(n: int | float) -> str:
    n = float(n)
    abs_n = abs(n)
    for unit, thr in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs_n >= thr:
            return f"{n / thr:.2f}{unit}"
    return f"{int(n)}"


def human_usd(x: float) -> str:
    if x >= 1_000_000:
        return f"${x/1_000_000:.2f}M"
    if x >= 1_000:
        return f"${x:,.2f}"
    if x >= 1:
        return f"${x:.4f}"
    return f"${x:.6f}"


def default_cc_switch_db() -> Path:
    return Path.home() / ".cc-switch" / "cc-switch.db"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

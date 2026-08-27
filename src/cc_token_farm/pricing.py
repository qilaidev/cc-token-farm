from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path
from typing import Iterable

from cc_token_farm.util import default_cc_switch_db


@dataclass(frozen=True)
class ModelPrice:
    model_id: str
    display_name: str
    # USD per 1M tokens
    input: float
    output: float
    cache_read: float = 0.0
    cache_creation: float = 0.0
    format: str = "auto"  # anthropic | openai | auto
    family: str = ""

    @property
    def is_unknown(self) -> bool:
        """True when catalog returned a placeholder for an uncatalogued model id."""
        return self.display_name.endswith("(unknown pricing)")

    def cost_usd(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        cost_multiplier: float = 1.0,
    ) -> float:
        raw = (
            input_tokens * self.input
            + output_tokens * self.output
            + cache_read_tokens * self.cache_read
            + cache_creation_tokens * self.cache_creation
        ) / 1_000_000.0
        return raw * cost_multiplier

    def estimate_for_mix(
        self,
        total_tokens: int,
        input_ratio: float = 0.85,
        output_ratio: float = 0.15,
        cache_read_ratio: float = 0.0,
        cache_creation_ratio: float = 0.0,
        cost_multiplier: float = 1.0,
    ) -> float:
        """Estimate cost for a total token budget with given mix ratios."""
        # normalize ratios
        ratios = [input_ratio, output_ratio, cache_read_ratio, cache_creation_ratio]
        s = sum(ratios) or 1.0
        ir, or_, crr, ccr = [r / s for r in ratios]
        return self.cost_usd(
            int(total_tokens * ir),
            int(total_tokens * or_),
            int(total_tokens * crr),
            int(total_tokens * ccr),
            cost_multiplier=cost_multiplier,
        )


# Alias map: short/common names → pricing model_id
ALIASES: dict[str, str] = {
    "claude-opus-5": "claude-opus-4-8",  # panel often shows opus-5; price near latest opus
    "claude-opus-5-latest": "claude-opus-4-8",
    "opus": "claude-opus-4-8",
    "opus5": "claude-opus-4-8",
    "sonnet": "claude-sonnet-5",
    "sonnet5": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
    "fable": "claude-fable-5",
    "mythos": "claude-mythos-5",
    "gpt5": "gpt-5.2",
    "gpt-5.6": "gpt-5.6",
    "gpt56": "gpt-5.6",
    "nano": "gpt-5-nano",
    "deepseek": "deepseek-v4-flash",
    "deepseek-flash": "deepseek-v4-flash",
    "deepseek-v4-flash-free": "deepseek-v4-flash",
}


def _guess_format(model_id: str) -> str:
    m = model_id.lower()
    if m.startswith("claude") or m.startswith("anthropic"):
        return "anthropic"
    if any(
        m.startswith(p)
        for p in ("gpt-", "o1", "o3", "o4", "chatgpt", "codex")
    ):
        return "openai"
    # third-party often served via OpenAI-compatible
    return "openai"


def _guess_family(model_id: str) -> str:
    m = model_id.lower()
    for fam in (
        "claude",
        "gpt",
        "deepseek",
        "gemini",
        "qwen",
        "mistral",
        "minimax",
        "doubao",
        "mimo",
        "step",
        "hunyuan",
    ):
        if fam in m:
            return fam
    return "other"


class PricingCatalog:
    def __init__(self) -> None:
        self._by_id: dict[str, ModelPrice] = {}
        self._source: str = "empty"

    @property
    def source(self) -> str:
        return self._source

    def __len__(self) -> int:
        return len(self._by_id)

    def models(self) -> list[ModelPrice]:
        return sorted(self._by_id.values(), key=lambda m: m.model_id)

    def get(self, model_id: str) -> ModelPrice | None:
        if not model_id:
            return None
        key = model_id.strip()
        if key in self._by_id:
            return self._by_id[key]
        alias = ALIASES.get(key.lower())
        if alias and alias in self._by_id:
            base = self._by_id[alias]
            # return a view with requested id for display, same rates
            return ModelPrice(
                model_id=key,
                display_name=base.display_name,
                input=base.input,
                output=base.output,
                cache_read=base.cache_read,
                cache_creation=base.cache_creation,
                format=base.format,
                family=base.family,
            )
        # fuzzy: strip date suffixes already exact; try prefix
        lower = key.lower()
        for mid, price in self._by_id.items():
            if mid.lower() == lower:
                return price
        return None

    def require(self, model_id: str) -> ModelPrice:
        p = self.get(model_id)
        if p:
            return p
        # unknown model: zero price with warning sentinel
        return ModelPrice(
            model_id=model_id,
            display_name=f"{model_id} (unknown pricing)",
            input=0.0,
            output=0.0,
            cache_read=0.0,
            cache_creation=0.0,
            format=_guess_format(model_id),
            family=_guess_family(model_id),
        )

    def add(self, price: ModelPrice) -> None:
        self._by_id[price.model_id] = price

    def merge(self, other: "PricingCatalog") -> None:
        for p in other.models():
            self._by_id[p.model_id] = p

    def search(self, query: str = "", family: str | None = None) -> list[ModelPrice]:
        q = (query or "").lower()
        out: list[ModelPrice] = []
        for m in self.models():
            if family and m.family != family:
                continue
            if q and q not in m.model_id.lower() and q not in m.display_name.lower():
                continue
            out.append(m)
        return out

    def cheapest(self, n: int = 10, format: str | None = None) -> list[ModelPrice]:
        items = self.models()
        if format and format != "auto":
            items = [m for m in items if m.format == format or m.format == "auto"]
        items = sorted(items, key=lambda m: m.input + m.output)
        return items[:n]

    # ----- loaders -----

    @classmethod
    def from_rows(cls, rows: Iterable[dict], source: str = "rows") -> "PricingCatalog":
        cat = cls()
        for r in rows:
            mid = str(r.get("model_id") or r.get("id") or "").strip()
            if not mid:
                continue
            def f(key: str, *alts: str, default: float = 0.0) -> float:
                for k in (key, *alts):
                    if k in r and r[k] is not None and r[k] != "":
                        return float(r[k])
                return default

            cat.add(
                ModelPrice(
                    model_id=mid,
                    display_name=str(r.get("display_name") or mid),
                    input=f("input", "input_cost_per_million"),
                    output=f("output", "output_cost_per_million"),
                    cache_read=f("cache_read", "cache_read_cost_per_million"),
                    cache_creation=f("cache_creation", "cache_creation_cost_per_million"),
                    format=str(r.get("format") or _guess_format(mid)),
                    family=str(r.get("family") or _guess_family(mid)),
                )
            )
        cat._source = source
        return cat

    @classmethod
    def load_bundled(cls) -> "PricingCatalog":
        try:
            ref = resources.files("cc_token_farm.data").joinpath("pricing.json")
            with ref.open("r", encoding="utf-8") as fh:
                rows = json.load(fh)
            return cls.from_rows(rows, source="bundled")
        except Exception:
            return cls()

    @classmethod
    def load_json(cls, path: Path) -> "PricingCatalog":
        rows = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(rows, dict) and "models" in rows:
            rows = rows["models"]
        return cls.from_rows(rows, source=str(path))

    @classmethod
    def load_cc_switch_db(cls, db_path: Path | None = None) -> "PricingCatalog":
        path = db_path or default_cc_switch_db()
        if not path.exists():
            raise FileNotFoundError(f"CC Switch DB not found: {path}")
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cur = con.execute(
                """
                SELECT model_id, display_name,
                       input_cost_per_million, output_cost_per_million,
                       cache_read_cost_per_million, cache_creation_cost_per_million
                FROM model_pricing
                ORDER BY model_id
                """
            )
            rows = []
            for mid, name, inp, out, cr, cc in cur.fetchall():
                rows.append(
                    {
                        "model_id": mid,
                        "display_name": name,
                        "input": inp,
                        "output": out,
                        "cache_read": cr,
                        "cache_creation": cc,
                    }
                )
        finally:
            con.close()
        return cls.from_rows(rows, source=str(path))

    @classmethod
    def load_auto(cls, db_path: Path | None = None, json_path: Path | None = None) -> "PricingCatalog":
        """Prefer live CC Switch DB, then custom JSON, then bundled."""
        cat = cls.load_bundled()
        if json_path and json_path.exists():
            try:
                cat.merge(cls.load_json(json_path))
                cat._source = f"bundled+{json_path}"
            except Exception:
                pass
        path = db_path or default_cc_switch_db()
        if path.exists():
            try:
                live = cls.load_cc_switch_db(path)
                cat.merge(live)
                cat._source = f"cc-switch:{path}"
            except Exception:
                pass
        # ensure local panel aliases exist even if missing in DB
        extras = [
            ModelPrice("claude-opus-5", "Claude Opus 5", 5.0, 25.0, 0.50, 6.25, "anthropic", "claude"),
            ModelPrice("claude-sonnet-5", "Claude Sonnet 5", 3.0, 15.0, 0.30, 3.75, "anthropic", "claude"),
            ModelPrice("claude-fable-5", "Claude Fable 5", 10.0, 50.0, 1.00, 12.50, "anthropic", "claude"),
            ModelPrice("claude-mythos-5", "Claude Mythos 5", 10.0, 50.0, 1.00, 12.50, "anthropic", "claude"),
            ModelPrice("deepseek-v4-flash-free", "DeepSeek V4 Flash Free", 0.0, 0.0, 0.0, 0.0, "openai", "deepseek"),
            ModelPrice("gpt-5.6", "GPT-5.6 Sol", 5.0, 30.0, 0.50, 6.25, "openai", "gpt"),
            ModelPrice("gpt-5.6-luna", "GPT-5.6 Luna", 1.0, 6.0, 0.10, 1.25, "openai", "gpt"),
        ]
        for e in extras:
            if e.model_id not in cat._by_id:
                cat.add(e)
        return cat

    def export_json(self, path: Path) -> None:
        rows = [
            {
                "model_id": m.model_id,
                "display_name": m.display_name,
                "input": m.input,
                "output": m.output,
                "cache_read": m.cache_read,
                "cache_creation": m.cache_creation,
                "format": m.format,
                "family": m.family,
            }
            for m in self.models()
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def to_dict_list(self) -> list[dict]:
        return [asdict(m) for m in self.models()]

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


@dataclass
class FarmConfig:
    proxy: str = "http://127.0.0.1:15721"
    format: str = "anthropic"  # anthropic | openai
    model: str = "claude-sonnet-5"
    models: list[str] = field(default_factory=list)  # rotate if set
    api_key: str = ""
    count: int | None = None
    forever: bool = False
    target_tokens: int | None = None
    concurrency: int = 1
    interval: float = 0.3
    jitter: float = 0.0
    max_tokens: int = 32
    prompt: str = "Reply with exactly one word: ok"
    prompt_chars: int = 0
    system: str | None = None
    cache: bool = False
    stream: bool = False
    timeout: float = 120.0
    headers: dict[str, str] = field(default_factory=dict)
    max_cost_usd: float | None = None
    cost_multiplier: float = 1.0
    dry_run: bool = False
    quiet: bool = False
    verbose: bool = False
    progress_file: str | None = None
    resume: bool = False
    fail_threshold: int = 20  # consecutive failures → abort
    backoff_base: float = 1.0
    backoff_max: float = 60.0
    rps: float | None = None  # optional global rate limit
    confirm_high_cost: bool = True
    high_cost_usd: float = 10.0

    def effective_models(self) -> list[str]:
        if self.models:
            return list(self.models)
        return [self.model]

    def primary_model(self) -> str:
        return self.effective_models()[0]


def load_toml_or_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".json"}:
        return json.loads(text)
    # minimal TOML-ish via tomllib
    try:
        import tomllib  # py3.11+
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore
    return tomllib.loads(text)


def config_from_mapping(data: dict[str, Any]) -> FarmConfig:
    # support nested [farm] section
    if "farm" in data and isinstance(data["farm"], dict):
        data = data["farm"]
    known = {f.name for f in fields(FarmConfig)}
    kwargs: dict[str, Any] = {}
    for k, v in data.items():
        key = k.replace("-", "_")
        if key in known:
            kwargs[key] = v
    # headers as list of "K: V"
    if "header" in data and "headers" not in kwargs:
        headers: dict[str, str] = {}
        for h in data["header"] if isinstance(data["header"], list) else [data["header"]]:
            if isinstance(h, str) and ":" in h:
                a, b = h.split(":", 1)
                headers[a.strip()] = b.strip()
        kwargs["headers"] = headers
    return FarmConfig(**kwargs)


def merge_env(cfg: FarmConfig) -> FarmConfig:
    if os.environ.get("CC_PROXY_URL"):
        cfg.proxy = os.environ["CC_PROXY_URL"]
    if not cfg.api_key:
        if cfg.format == "anthropic":
            cfg.api_key = (
                os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or "cc-token-farm"
            )
        else:
            cfg.api_key = (
                os.environ.get("OPENAI_API_KEY")
                or os.environ.get("CODEX_API_KEY")
                or "cc-token-farm"
            )
    return cfg


def dump_config(cfg: FarmConfig) -> dict[str, Any]:
    return asdict(cfg)

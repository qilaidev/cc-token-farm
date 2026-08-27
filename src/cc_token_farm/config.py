from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


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
    # tomllib is stdlib on 3.11+; on 3.10 use optional tomli
    try:
        import tomllib  # py3.11+
    except ModuleNotFoundError:  # pragma: no cover
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError as e:
            raise RuntimeError(
                f"TOML config requires Python 3.11+ (tomllib) or the 'tomli' package "
                f"on Python 3.10. Install with: pip install 'tomli>=2.0' "
                f"(or convert {path} to JSON)."
            ) from e
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


def merge_env(cfg: FarmConfig, *, override_proxy: bool = False) -> FarmConfig:
    """Fill missing values from environment.

    Priority intended by callers: CLI / config file > env > defaults.
    Proxy is only taken from env when still default, unless override_proxy=True.
    """
    env_proxy = os.environ.get("CC_PROXY_URL")
    if env_proxy:
        if override_proxy or cfg.proxy == FarmConfig.proxy:
            cfg.proxy = env_proxy
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


def validate_config(cfg: FarmConfig) -> list[str]:
    """Return human-readable validation errors (empty if OK)."""
    errors: list[str] = []
    if cfg.format not in {"anthropic", "openai"}:
        errors.append(f"format must be anthropic|openai, got {cfg.format!r}")
    parsed = urlparse(cfg.proxy)
    if not parsed.scheme or not parsed.hostname:
        errors.append(f"invalid proxy URL: {cfg.proxy!r}")
    if cfg.concurrency < 1:
        errors.append(f"concurrency must be >= 1, got {cfg.concurrency}")
    if cfg.concurrency > 256:
        errors.append(f"concurrency too high ({cfg.concurrency}); max 256")
    if cfg.interval < 0:
        errors.append(f"interval must be >= 0, got {cfg.interval}")
    if cfg.jitter < 0:
        errors.append(f"jitter must be >= 0, got {cfg.jitter}")
    if cfg.max_tokens < 1:
        errors.append(f"max_tokens must be >= 1, got {cfg.max_tokens}")
    if cfg.prompt_chars < 0:
        errors.append(f"prompt_chars must be >= 0, got {cfg.prompt_chars}")
    if cfg.timeout <= 0:
        errors.append(f"timeout must be > 0, got {cfg.timeout}")
    if cfg.fail_threshold < 1:
        errors.append(f"fail_threshold must be >= 1, got {cfg.fail_threshold}")
    if cfg.backoff_base < 0 or cfg.backoff_max < 0:
        errors.append("backoff_base/backoff_max must be >= 0")
    if cfg.backoff_max < cfg.backoff_base:
        errors.append("backoff_max must be >= backoff_base")
    if cfg.rps is not None and cfg.rps <= 0:
        errors.append(f"rps must be > 0 when set, got {cfg.rps}")
    if cfg.max_cost_usd is not None and cfg.max_cost_usd < 0:
        errors.append(f"max_cost_usd must be >= 0, got {cfg.max_cost_usd}")
    if cfg.cost_multiplier < 0:
        errors.append(f"cost_multiplier must be >= 0, got {cfg.cost_multiplier}")
    if cfg.count is not None and cfg.count < 1:
        errors.append(f"count must be >= 1, got {cfg.count}")
    if cfg.target_tokens is not None and cfg.target_tokens < 1:
        errors.append(f"target_tokens must be >= 1, got {cfg.target_tokens}")
    modes = sum(
        [
            bool(cfg.forever),
            cfg.target_tokens is not None,
            cfg.count is not None,
        ]
    )
    if modes > 1:
        errors.append("pick only one mode: count / forever / target_tokens")
    if not cfg.effective_models():
        errors.append("at least one model is required")
    return errors


def dump_config(cfg: FarmConfig) -> dict[str, Any]:
    return asdict(cfg)

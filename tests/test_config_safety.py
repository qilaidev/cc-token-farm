from pathlib import Path

from cc_token_farm.cli import main
from cc_token_farm.client import RequestResult
from cc_token_farm.config import (
    FarmConfig,
    config_from_mapping,
    load_toml_or_json,
    merge_env,
    validate_config,
)
from cc_token_farm.pricing import ModelPrice, PricingCatalog
from cc_token_farm.stats import Stats


def test_validate_config_ok():
    cfg = FarmConfig(count=3, concurrency=2)
    assert validate_config(cfg) == []


def test_validate_config_bad_values():
    cfg = FarmConfig(concurrency=0, interval=-1, format="foo", count=0)
    errs = validate_config(cfg)
    assert any("concurrency" in e for e in errs)
    assert any("interval" in e for e in errs)
    assert any("format" in e for e in errs)
    assert any("count" in e for e in errs)


def test_merge_env_does_not_clobber_explicit_proxy(monkeypatch):
    monkeypatch.setenv("CC_PROXY_URL", "http://env-proxy:9")
    cfg = FarmConfig(proxy="http://cli-proxy:1")
    merge_env(cfg)
    assert cfg.proxy == "http://cli-proxy:1"


def test_merge_env_fills_default_proxy(monkeypatch):
    monkeypatch.setenv("CC_PROXY_URL", "http://env-proxy:9")
    cfg = FarmConfig()
    merge_env(cfg)
    assert cfg.proxy == "http://env-proxy:9"


def test_load_example_toml():
    path = Path(__file__).resolve().parents[1] / "examples" / "farm.example.toml"
    data = load_toml_or_json(path)
    cfg = config_from_mapping(data)
    assert cfg.model == "claude-sonnet-5"
    assert cfg.count == 20


def test_unknown_pricing_flag():
    p = ModelPrice("x", "x (unknown pricing)", 0, 0)
    assert p.is_unknown
    free = ModelPrice("free", "Free", 0, 0, format="openai", family="other")
    assert not free.is_unknown


def test_cli_rejects_bad_concurrency():
    code = main(
        [
            "run",
            "--dry-run",
            "-n",
            "1",
            "-m",
            "claude-sonnet-5",
            "-c",
            "0",
            "-y",
            "-q",
        ]
    )
    assert code == 2


def test_cli_rejects_stream_budget_live_gate():
    from cc_token_farm.cli import _safety_gate

    cfg = FarmConfig(
        stream=True,
        max_cost_usd=1.0,
        dry_run=False,
        count=1,
        model="claude-sonnet-5",
    )
    cat = PricingCatalog.load_auto()
    assert _safety_gate(cfg, cat, yes=False) == 2


def test_cli_rejects_unknown_pricing_with_budget():
    from cc_token_farm.cli import _safety_gate

    cfg = FarmConfig(
        max_cost_usd=1.0,
        dry_run=False,
        count=1,
        model="totally-not-a-real-model-zzz",
    )
    cat = PricingCatalog.load_auto()
    assert _safety_gate(cfg, cat, yes=False) == 2


def test_cli_daily_2b_requires_budget():
    code = main(
        [
            "run",
            "--dry-run",
            "--profile",
            "daily-2b",
            "-m",
            "claude-sonnet-5",
            "-q",
        ]
    )
    # dry-run still enforces daily-2b budget requirement before dry matters
    assert code == 2


def test_resume_baseline_rate():
    st = Stats()
    st.input_tokens = 1_000_000
    st.baseline_tokens = 1_000_000
    st.started_at = st.started_at  # now
    # no new tokens yet → rate falls back but remains finite
    assert st.tokens_per_sec() >= 0
    # add this-run tokens
    st.input_tokens = 1_000_100
    # tiny elapsed simulated
    st.started_at = st.started_at - 1.0
    rate = st.tokens_per_sec()
    # ~100 tok/s from this-run only, not ~1e6
    assert rate < 10_000
    assert rate > 50


def test_stats_add_cost():
    st = Stats()
    price = ModelPrice("m", "M", input=1.0, output=1.0)
    r = RequestResult(ok=True, status=200, latency_ms=10, model="m", input_tokens=1_000_000, output_tokens=0)
    st.add(r, price)
    assert abs(st.cost_usd - 1.0) < 1e-9

import threading
from pathlib import Path

from cc_token_farm.client import RequestResult
from cc_token_farm.config import FarmConfig
from cc_token_farm.engine import FarmEngine
from cc_token_farm.pricing import ModelPrice, PricingCatalog
from cc_token_farm.progress import ProgressState, ProgressStore


def _catalog_with(price: ModelPrice) -> PricingCatalog:
    cat = PricingCatalog()
    cat.add(price)
    cat._source = "test"
    return cat


def test_progress_roundtrip(tmp_path: Path):
    path = tmp_path / "progress.json"
    store = ProgressStore(path)
    state = ProgressState(
        session_id="abc",
        status="running",
        input_tokens=100,
        output_tokens=5,
        cost_usd=0.12,
    )
    store.save(state)
    loaded = store.load()
    assert loaded is not None
    assert loaded.session_id == "abc"
    assert loaded.input_tokens == 100
    assert loaded.total_tokens == 105


def test_progress_corrupt_returns_none(tmp_path: Path):
    path = tmp_path / "progress.json"
    path.write_text("{not-json", encoding="utf-8")
    assert ProgressStore(path).load() is None


def test_dry_run_budget_stop(tmp_path: Path):
    """With expensive model and tiny budget, dry-run should stop on cost."""
    price = ModelPrice("pricey", "Pricey", input=1_000_000.0, output=1_000_000.0)
    cat = _catalog_with(price)
    cfg = FarmConfig(
        model="pricey",
        count=50,
        dry_run=True,
        max_cost_usd=0.01,
        concurrency=1,
        interval=0,
        quiet=True,
        progress_file=str(tmp_path / "p.json"),
        prompt_chars=4000,  # dry-run input ≈ chars/4
        max_tokens=16,
        confirm_high_cost=False,
    )
    engine = FarmEngine(cfg, cat)
    code = engine.run()
    assert engine.stats.cost_usd >= 0.01 or engine._stop_reason == "max_cost_usd"
    assert engine.stats.total < 50  # did not run full count
    assert code in {0, 1}


def test_fail_threshold_stops(tmp_path: Path, monkeypatch):
    price = ModelPrice("m", "M", input=1.0, output=1.0)
    cat = _catalog_with(price)
    cfg = FarmConfig(
        model="m",
        count=20,
        dry_run=False,
        fail_threshold=3,
        concurrency=1,
        interval=0,
        quiet=True,
        progress_file=str(tmp_path / "p.json"),
        confirm_high_cost=False,
        timeout=1.0,
    )

    def boom(*_a, **_k):
        return RequestResult(ok=False, status=500, latency_ms=1, model="m", error="boom")

    engine = FarmEngine(cfg, cat)
    monkeypatch.setattr(engine, "_one", boom)
    # skip real proxy check
    monkeypatch.setattr(
        "cc_token_farm.engine.check_proxy", lambda *a, **k: (True, "ok")
    )
    code = engine.run()
    assert engine.stats.consecutive_failures >= 3
    assert engine._stop_reason == "fail_threshold"
    assert code == 1


def test_resume_loads_counters(tmp_path: Path):
    path = tmp_path / "progress.json"
    prev = ProgressState(
        session_id="sess1",
        status="stopped",
        input_tokens=500,
        output_tokens=10,
        cost_usd=1.5,
        total=3,
        success=3,
    )
    ProgressStore(path).save(prev)

    price = ModelPrice("m", "M", input=1.0, output=1.0)
    cat = _catalog_with(price)
    cfg = FarmConfig(
        model="m",
        count=1,
        dry_run=True,
        quiet=True,
        resume=True,
        progress_file=str(path),
        confirm_high_cost=False,
        interval=0,
    )
    engine = FarmEngine(cfg, cat)
    assert engine.stats.input_tokens == 500
    assert engine.stats.baseline_tokens == 510
    assert engine.session_id == "sess1"
    code = engine.run()
    assert code == 0
    assert engine.stats.input_tokens >= 500

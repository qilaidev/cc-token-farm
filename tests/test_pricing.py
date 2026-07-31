from cc_token_farm.pricing import ModelPrice, PricingCatalog


def test_cost_usd():
    p = ModelPrice("x", "X", input=3.0, output=15.0, cache_read=0.3, cache_creation=3.75)
    # 1M in + 1M out = 18
    assert abs(p.cost_usd(1_000_000, 1_000_000) - 18.0) < 1e-9
    assert abs(p.cost_usd(1_000_000, 0, cost_multiplier=2.0) - 6.0) < 1e-9


def test_estimate_mix():
    p = ModelPrice("x", "X", input=1.0, output=1.0)
    # all input
    c = p.estimate_for_mix(1_000_000, input_ratio=1, output_ratio=0)
    assert abs(c - 1.0) < 1e-9


def test_bundled_catalog():
    cat = PricingCatalog.load_bundled()
    assert len(cat) > 50
    sonnet = cat.get("claude-sonnet-5")
    assert sonnet is not None
    assert sonnet.input > 0


def test_alias_opus5():
    cat = PricingCatalog.load_bundled()
    # ensure extras / aliases work after load_auto-like merge
    cat2 = PricingCatalog.load_auto()
    p = cat2.get("claude-opus-5")
    assert p is not None

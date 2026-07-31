from cc_token_farm.util import human_int, parse_token_amount


def test_parse_token_amount_cn():
    assert parse_token_amount("20亿") == 2_000_000_000
    assert parse_token_amount("500万") == 5_000_000
    assert parse_token_amount("1千") == 1_000


def test_parse_token_amount_suffix():
    assert parse_token_amount("2B") == 2_000_000_000
    assert parse_token_amount("2b") == 2_000_000_000
    assert parse_token_amount("500M") == 500_000_000
    assert parse_token_amount("100k") == 100_000
    assert parse_token_amount("1e6") == 1_000_000
    assert parse_token_amount("1_000_000") == 1_000_000


def test_human_int():
    assert human_int(2_000_000_000) == "2.00B"
    assert human_int(1500) == "1.50K"

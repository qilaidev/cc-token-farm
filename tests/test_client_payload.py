from cc_token_farm.client import (
    build_anthropic_payload,
    build_openai_payload,
    build_user_prompt,
    parse_usage,
)


def test_build_user_prompt_pad():
    t = build_user_prompt("hi", 200, 1)
    assert t.startswith("[farm#1]")
    assert len(t) >= 200


def test_anthropic_cache_payload():
    p = build_anthropic_payload(
        "claude-sonnet-5", "hi", 16, system="sys", stream=False, enable_cache=True
    )
    assert isinstance(p["system"], list)
    assert p["system"][0]["cache_control"]["type"] == "ephemeral"


def test_openai_payload():
    p = build_openai_payload("gpt-5.2", "hi", 16, system="s", stream=False)
    assert p["messages"][0]["role"] == "system"
    assert p["messages"][1]["content"] == "hi"


def test_parse_usage_anthropic():
    body = {
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 20,
        }
    }
    i, o, cr, cc, _ = parse_usage("anthropic", body)
    assert (i, o, cr, cc) == (10, 5, 100, 20)


def test_parse_usage_openai():
    body = {"usage": {"prompt_tokens": 11, "completion_tokens": 7}}
    i, o, cr, cc, _ = parse_usage("openai", body)
    assert (i, o, cr, cc) == (11, 7, 0, 0)

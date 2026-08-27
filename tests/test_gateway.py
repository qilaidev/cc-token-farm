from __future__ import annotations

import json
from pathlib import Path

from cc_token_farm.gateway import (
    _is_local_proxy_url,
    strip_proxy_from_claude_settings,
)


def test_is_local_proxy_url():
    assert _is_local_proxy_url("http://127.0.0.1:15721")
    assert _is_local_proxy_url("http://localhost:15721/v1")
    assert not _is_local_proxy_url("https://api.anthropic.com")
    assert not _is_local_proxy_url(None)


def test_strip_proxy_env(tmp_path: Path):
    data = {
        "model": "opus[1m]",
        "env": {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:15721",
            "ANTHROPIC_AUTH_TOKEN": "PROXY_MANAGED",
            "CLAUDE_CODE_HARBOR_KITE": "1",
        },
    }
    out = strip_proxy_from_claude_settings(data)
    assert "ANTHROPIC_BASE_URL" not in out["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in out["env"]
    assert out["env"]["CLAUDE_CODE_HARBOR_KITE"] == "1"
    assert out["model"] == "opus[1m]"


def test_strip_keeps_unrelated_base():
    data = {"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com", "FOO": "1"}}
    out = strip_proxy_from_claude_settings(data)
    assert out["env"]["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"


def test_cli_gateway_status_help():
    from cc_token_farm.cli import main

    try:
        main(["gateway", "--help"])
    except SystemExit as e:
        assert e.code == 0

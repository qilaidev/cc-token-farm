"""CC Switch farm gateway: open proxy for farming without hijacking Claude CLI.

Official Claude accounts should stay direct. Farming only needs the local
proxy port (default 15721). Live-takeover rewrites ~/.claude/settings.json and
is the main source of post-farm breakage — this module never enables it.

Workflow:
  gateway on   → proxy listening, Claude CLI stays official (no 15721 in settings)
  gateway off  → proxy stopped, Claude settings restored / cleaned
  gateway status
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

DEFAULT_PROXY_PORT = 15721
DEFAULT_UPSTREAM = "https://api.anthropic.com"
PROXY_ENV_KEYS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
)


def _home() -> Path:
    return Path.home()


def cc_switch_db(path: Path | None = None) -> Path:
    return path or (_home() / ".cc-switch" / "cc-switch.db")


def cc_switch_settings() -> Path:
    return _home() / ".cc-switch" / "settings.json"


def claude_settings() -> Path:
    return _home() / ".claude" / "settings.json"


def farm_state_dir() -> Path:
    d = _home() / ".cc-token-farm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def farm_settings_backup() -> Path:
    return farm_state_dir() / "claude-settings-pre-gateway.json"


@dataclass
class GatewayStatus:
    enable_local_proxy: bool
    proxy_enabled: bool
    enabled: bool
    live_takeover: bool
    port_listening: bool
    port: int
    claude_base_url: str | None
    claude_token_mode: str | None  # PROXY_MANAGED | set | missing
    provider_base_url: str | None
    has_live_backup: bool
    claude_routed: bool  # True if CLI is pointed at local proxy

    @property
    def farm_ready(self) -> bool:
        """Proxy up and accepting traffic (takeover not required)."""
        return self.port_listening and self.enable_local_proxy and self.proxy_enabled

    @property
    def official_ok(self) -> bool:
        """Claude CLI not hijacked by local proxy routing."""
        return not self.claude_routed and not self.live_takeover


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _port_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return bool(out.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        # fallback: try connect
        try:
            req = Request(f"http://{host}:{port}/", method="GET")
            urlopen(req, timeout=1.5)
            return True
        except Exception:  # noqa: BLE001
            return False


def _claude_env_snapshot(settings: dict[str, Any]) -> tuple[str | None, str | None]:
    env = settings.get("env") or {}
    if not isinstance(env, dict):
        return None, None
    base = env.get("ANTHROPIC_BASE_URL")
    token = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")
    if token == "PROXY_MANAGED":
        mode = "PROXY_MANAGED"
    elif token:
        mode = "set"
    else:
        mode = None
    return (str(base) if base else None), mode


def _is_local_proxy_url(url: str | None, port: int = DEFAULT_PROXY_PORT) -> bool:
    if not url:
        return False
    u = url.lower()
    return f"127.0.0.1:{port}" in u or f"localhost:{port}" in u


def status(db_path: Path | None = None, port: int = DEFAULT_PROXY_PORT) -> GatewayStatus:
    db = cc_switch_db(db_path)
    app_settings = _read_json(cc_switch_settings())
    claude = _read_json(claude_settings())
    base, token_mode = _claude_env_snapshot(claude)

    proxy_enabled = enabled = live = False
    provider_base = None
    has_backup = False
    if db.exists():
        con = sqlite3.connect(str(db))
        try:
            row = con.execute(
                "SELECT proxy_enabled, enabled, live_takeover_active FROM proxy_config WHERE app_type=?",
                ("claude",),
            ).fetchone()
            if row:
                proxy_enabled, enabled, live = bool(row[0]), bool(row[1]), bool(row[2])
            prow = con.execute(
                "SELECT settings_config FROM providers WHERE id=? OR (app_type=? AND is_current=1) LIMIT 1",
                ("claude-official", "claude"),
            ).fetchone()
            if prow:
                try:
                    sc = json.loads(prow[0])
                    provider_base = (sc.get("env") or {}).get("ANTHROPIC_BASE_URL")
                except (json.JSONDecodeError, TypeError, AttributeError):
                    pass
            brow = con.execute(
                "SELECT 1 FROM proxy_live_backup WHERE app_type=? LIMIT 1", ("claude",)
            ).fetchone()
            has_backup = bool(brow)
        finally:
            con.close()

    return GatewayStatus(
        enable_local_proxy=bool(app_settings.get("enableLocalProxy")),
        proxy_enabled=proxy_enabled,
        enabled=enabled,
        live_takeover=live,
        port_listening=_port_listening(port),
        port=port,
        claude_base_url=base,
        claude_token_mode=token_mode,
        provider_base_url=str(provider_base) if provider_base else None,
        has_live_backup=has_backup,
        claude_routed=_is_local_proxy_url(base, port) or token_mode == "PROXY_MANAGED",
    )


def format_status(st: GatewayStatus) -> str:
    lines = [
        f"CC Switch enableLocalProxy : {st.enable_local_proxy}",
        f"proxy_config (claude)      : enabled={st.enabled} proxy_enabled={st.proxy_enabled} live_takeover={st.live_takeover}",
        f"port {st.port}                 : {'LISTENING' if st.port_listening else 'down'}",
        f"provider upstream          : {st.provider_base_url or '(unknown)'}",
        f"Claude ANTHROPIC_BASE_URL  : {st.claude_base_url or '(unset = official default)'}",
        f"Claude auth mode           : {st.claude_token_mode or '(unset / keychain OAuth)'}",
        f"live backup present        : {st.has_live_backup}",
        f"farm_ready (proxy for farm): {st.farm_ready}",
        f"official_ok (CLI not routed): {st.official_ok}",
    ]
    if st.claude_routed:
        lines.append("⚠ Claude CLI is still routed via local proxy — run: cc-token-farm gateway off")
    if st.farm_ready and st.official_ok:
        lines.append("✓ Ideal farm mode: proxy up, Claude CLI stays official")
    return "\n".join(lines)


def _set_app_enable_local_proxy(value: bool) -> None:
    path = cc_switch_settings()
    data = _read_json(path)
    data["enableLocalProxy"] = value
    _write_json(path, data)


def _set_proxy_config(
    *,
    proxy_enabled: int,
    enabled: int,
    live_takeover_active: int,
    db_path: Path | None = None,
) -> None:
    db = cc_switch_db(db_path)
    if not db.exists():
        raise FileNotFoundError(f"CC Switch DB not found: {db}")
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            """
            UPDATE proxy_config
            SET proxy_enabled=?,
                enabled=?,
                live_takeover_active=?,
                updated_at=datetime('now')
            WHERE app_type='claude'
            """,
            (proxy_enabled, enabled, live_takeover_active),
        )
        con.commit()
    finally:
        con.close()


def _ensure_provider_upstream(
    upstream: str = DEFAULT_UPSTREAM,
    db_path: Path | None = None,
) -> None:
    """Point Claude Official provider at a reachable upstream (not a dead local hop)."""
    db = cc_switch_db(db_path)
    if not db.exists():
        return
    con = sqlite3.connect(str(db))
    try:
        row = con.execute(
            "SELECT settings_config FROM providers WHERE id=?", ("claude-official",)
        ).fetchone()
        if not row:
            return
        cfg = json.loads(row[0])
        env = cfg.setdefault("env", {})
        if not isinstance(env, dict):
            return
        cur = env.get("ANTHROPIC_BASE_URL") or ""
        # Only rewrite clearly-local fragile hops or empty
        if (not cur) or _is_local_proxy_url(cur, 18999) or "127.0.0.1" in cur:
            env["ANTHROPIC_BASE_URL"] = upstream
            con.execute(
                "UPDATE providers SET settings_config=? WHERE id=?",
                (json.dumps(cfg, ensure_ascii=False), "claude-official"),
            )
            con.commit()
    finally:
        con.close()


def _load_live_backup(db_path: Path | None = None) -> dict[str, Any] | None:
    db = cc_switch_db(db_path)
    if not db.exists():
        return None
    con = sqlite3.connect(str(db))
    try:
        row = con.execute(
            "SELECT original_config FROM proxy_live_backup WHERE app_type=?",
            ("claude",),
        ).fetchone()
        if not row:
            return None
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None
    finally:
        con.close()


def _clear_live_backup(db_path: Path | None = None) -> None:
    db = cc_switch_db(db_path)
    if not db.exists():
        return
    con = sqlite3.connect(str(db))
    try:
        con.execute("DELETE FROM proxy_live_backup WHERE app_type=?", ("claude",))
        con.commit()
    finally:
        con.close()


def strip_proxy_from_claude_settings(
    settings: dict[str, Any] | None = None,
    *,
    port: int = DEFAULT_PROXY_PORT,
) -> dict[str, Any]:
    """Remove Live-takeover proxy env from Claude settings; keep other env."""
    data = dict(settings if settings is not None else _read_json(claude_settings()))
    env = dict(data.get("env") or {})
    base = env.get("ANTHROPIC_BASE_URL")
    token = env.get("ANTHROPIC_AUTH_TOKEN")

    if _is_local_proxy_url(str(base) if base else None, port):
        env.pop("ANTHROPIC_BASE_URL", None)
    if token == "PROXY_MANAGED":
        env.pop("ANTHROPIC_AUTH_TOKEN", None)

    if env:
        data["env"] = env
    else:
        data.pop("env", None)
    return data


def restore_claude_settings(db_path: Path | None = None, port: int = DEFAULT_PROXY_PORT) -> str:
    """Restore Claude CLI to official (non-routed) settings. Returns action note."""
    path = claude_settings()
    backup = _load_live_backup(db_path)
    farm_bak = farm_settings_backup()

    if backup and isinstance(backup, dict) and backup:
        # Live backup is the full pre-takeover settings.json
        cleaned = strip_proxy_from_claude_settings(backup, port=port)
        _write_json(path, cleaned)
        _clear_live_backup(db_path)
        return "restored from CC Switch proxy_live_backup"

    if farm_bak.exists():
        try:
            data = json.loads(farm_bak.read_text(encoding="utf-8"))
            cleaned = strip_proxy_from_claude_settings(data, port=port)
            _write_json(path, cleaned)
            return f"restored from {farm_bak}"
        except (json.JSONDecodeError, OSError):
            pass

    if path.exists():
        cleaned = strip_proxy_from_claude_settings(port=port)
        _write_json(path, cleaned)
        return "stripped proxy env from current Claude settings"

    return "no Claude settings file; nothing to restore"


def _restart_cc_switch(wait_port: int | None, timeout: float = 20.0) -> None:
    """Restart CC Switch app so DB/settings take effect."""
    # quit
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "CC Switch" to quit'],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    time.sleep(1.5)
    subprocess.run(["pkill", "-f", "/Applications/CC Switch.app/Contents/MacOS/cc-switch"], capture_output=True)
    time.sleep(1.0)
    subprocess.run(["open", "-a", "CC Switch"], capture_output=True)

    deadline = time.time() + timeout
    while time.time() < deadline:
        listening = _port_listening(wait_port or DEFAULT_PROXY_PORT)
        if wait_port is None:
            # off: want port down
            if not _port_listening(DEFAULT_PROXY_PORT):
                # give app a moment to settle
                time.sleep(0.5)
                if not _port_listening(DEFAULT_PROXY_PORT):
                    return
        else:
            if listening:
                return
        time.sleep(0.5)


def _stop_oauth_forwarder(port: int = 18999) -> bool:
    """Stop project oauth_forwarder if we started it (best-effort)."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = [p.strip() for p in out.stdout.split() if p.strip()]
        stopped = False
        for pid in pids:
            # only kill if cmdline looks like oauth_forwarder
            try:
                cmd = subprocess.run(
                    ["ps", "-p", pid, "-o", "command="],
                    capture_output=True,
                    text=True,
                    timeout=3,
                ).stdout
            except (OSError, subprocess.TimeoutExpired):
                continue
            if "oauth_forwarder" in cmd:
                os.kill(int(pid), 15)
                stopped = True
        return stopped
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False


def gateway_on(
    *,
    db_path: Path | None = None,
    port: int = DEFAULT_PROXY_PORT,
    restart: bool = True,
    upstream: str = DEFAULT_UPSTREAM,
) -> list[str]:
    """Enable local proxy for farming WITHOUT Claude Live takeover."""
    notes: list[str] = []
    path = claude_settings()

    # Snapshot current Claude settings once (if not already a proxy snapshot)
    if path.exists():
        cur = _read_json(path)
        base, mode = _claude_env_snapshot(cur)
        if not _is_local_proxy_url(base, port) and mode != "PROXY_MANAGED":
            _write_json(farm_settings_backup(), cur)
            notes.append(f"saved Claude settings → {farm_settings_backup()}")

    # Critical: never enable live takeover
    _set_proxy_config(
        proxy_enabled=1,
        enabled=1,
        live_takeover_active=0,
        db_path=db_path,
    )
    notes.append("proxy_config: enabled + proxy_enabled, live_takeover=0")

    _set_app_enable_local_proxy(True)
    notes.append("enableLocalProxy=true")

    _ensure_provider_upstream(upstream, db_path=db_path)
    notes.append(f"provider upstream ensured → {upstream} (if was local hop)")

    # Keep Claude CLI official even if a previous session left takeover env
    note = restore_claude_settings(db_path=db_path, port=port)
    notes.append(f"Claude CLI: {note}")

    if restart:
        notes.append("restarting CC Switch…")
        _restart_cc_switch(wait_port=port)
        if _port_listening(port):
            notes.append(f"✓ port {port} listening")
        else:
            notes.append(
                f"⚠ port {port} not listening yet — open CC Switch UI and enable Local Proxy once"
            )

    st = status(db_path=db_path, port=port)
    if st.claude_routed:
        # Force strip again if CC Switch re-took over on boot
        restore_claude_settings(db_path=db_path, port=port)
        _set_proxy_config(
            proxy_enabled=1,
            enabled=1,
            live_takeover_active=0,
            db_path=db_path,
        )
        notes.append("⚠ CC Switch re-applied takeover; stripped Claude settings again + live_takeover=0")
        notes.append("  If it keeps coming back: in CC Switch UI disable Claude Live/接管, keep only Local Proxy")

    return notes


def gateway_off(
    *,
    db_path: Path | None = None,
    port: int = DEFAULT_PROXY_PORT,
    restart: bool = True,
    stop_forwarder: bool = True,
) -> list[str]:
    """Disable local proxy and restore Claude CLI to official direct mode."""
    notes: list[str] = []

    _set_proxy_config(
        proxy_enabled=0,
        enabled=0,
        live_takeover_active=0,
        db_path=db_path,
    )
    notes.append("proxy_config: all off, live_takeover=0")

    _set_app_enable_local_proxy(False)
    notes.append("enableLocalProxy=false")

    note = restore_claude_settings(db_path=db_path, port=port)
    notes.append(f"Claude CLI: {note}")

    if stop_forwarder:
        if _stop_oauth_forwarder(18999):
            notes.append("stopped oauth_forwarder on :18999")

    if restart:
        notes.append("restarting CC Switch…")
        _restart_cc_switch(wait_port=None)
        # After restart, CC Switch may try to re-takeover if flags were wrong —
        # re-assert off flags and clean settings.
        _set_proxy_config(
            proxy_enabled=0,
            enabled=0,
            live_takeover_active=0,
            db_path=db_path,
        )
        _set_app_enable_local_proxy(False)
        restore_claude_settings(db_path=db_path, port=port)

        if _port_listening(port):
            notes.append(
                f"⚠ port {port} still listening — turn off Local Proxy in CC Switch UI once"
            )
        else:
            notes.append(f"✓ port {port} down")

    st = status(db_path=db_path, port=port)
    if st.claude_routed:
        restore_claude_settings(db_path=db_path, port=port)
        notes.append("re-stripped Claude proxy env after restart")
    if st.official_ok or not status(db_path=db_path, port=port).claude_routed:
        notes.append("✓ Claude CLI should be official direct (restart Claude windows if still broken)")
    return notes

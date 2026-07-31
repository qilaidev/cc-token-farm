from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from cc_token_farm.client import check_proxy
from cc_token_farm.pricing import PricingCatalog
from cc_token_farm.util import default_cc_switch_db, human_int


@dataclass
class CheckItem:
    name: str
    ok: bool
    detail: str


def doctor(
    proxy: str = "http://127.0.0.1:15721",
    db_path: Path | None = None,
) -> list[CheckItem]:
    items: list[CheckItem] = []
    ok, msg = check_proxy(proxy)
    items.append(CheckItem("proxy", ok, msg))

    path = db_path or default_cc_switch_db()
    if not path.exists():
        items.append(CheckItem("cc-switch-db", False, f"not found: {path}"))
        return items

    items.append(CheckItem("cc-switch-db", True, str(path)))
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            # proxy config
            rows = con.execute(
                "SELECT app_type, enabled, proxy_enabled, listen_address, listen_port, live_takeover_active FROM proxy_config"
            ).fetchall()
            for app, enabled, pen, addr, port, live in rows:
                active = bool(enabled or pen or live)
                items.append(
                    CheckItem(
                        f"proxy_config:{app}",
                        True,
                        f"enabled={bool(enabled)} proxy_enabled={bool(pen)} "
                        f"live_takeover={bool(live)} listen={addr}:{port} activeish={active}",
                    )
                )

            n_price = con.execute("SELECT COUNT(*) FROM model_pricing").fetchone()[0]
            items.append(CheckItem("model_pricing", n_price > 0, f"{n_price} models"))

            # recent logs
            row = con.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_creation_tokens),0)
                FROM proxy_request_logs
                WHERE created_at > (CAST(strftime('%s','now') AS INTEGER) - 86400) * 1000
                """
            ).fetchone()
            # created_at may be ms or weird epoch; also try last 100
            last = con.execute(
                """
                SELECT model, app_type, input_tokens, output_tokens, status_code, created_at
                FROM proxy_request_logs ORDER BY created_at DESC LIMIT 3
                """
            ).fetchall()
            items.append(
                CheckItem(
                    "recent_logs_24h",
                    True,
                    f"count={row[0]} tokens_sum≈{human_int(row[1] or 0)} (if created_at scale correct)",
                )
            )
            if last:
                sample = "; ".join(
                    f"{m}/{a} in={i} out={o} st={st}" for m, a, i, o, st, _ in last
                )
                items.append(CheckItem("latest_requests", True, sample))
            else:
                items.append(CheckItem("latest_requests", True, "no rows yet"))

            providers = con.execute(
                "SELECT app_type, name, is_current FROM providers WHERE is_current=1"
            ).fetchall()
            if providers:
                items.append(
                    CheckItem(
                        "current_providers",
                        True,
                        ", ".join(f"{a}:{n}" for a, n, _ in providers),
                    )
                )
            else:
                items.append(CheckItem("current_providers", False, "no current provider"))
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        items.append(CheckItem("cc-switch-db-query", False, str(e)))

    cat = PricingCatalog.load_auto(db_path=path)
    items.append(CheckItem("pricing_catalog", len(cat) > 0, f"{len(cat)} models from {cat.source}"))
    return items

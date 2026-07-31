from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from cc_token_farm import __version__
from cc_token_farm.client import check_proxy
from cc_token_farm.config import FarmConfig, config_from_mapping, load_toml_or_json, merge_env
from cc_token_farm.engine import FarmEngine
from cc_token_farm.health import doctor
from cc_token_farm.pricing import PricingCatalog
from cc_token_farm.progress import ProgressStore
from cc_token_farm.util import (
    default_cc_switch_db,
    human_int,
    human_usd,
    parse_money,
    parse_token_amount,
)

DEFAULT_PROXY = "http://127.0.0.1:15721"


def _catalog(args: argparse.Namespace) -> PricingCatalog:
    db = Path(args.db) if getattr(args, "db", None) else None
    pricing = Path(args.pricing) if getattr(args, "pricing", None) else None
    return PricingCatalog.load_auto(db_path=db, json_path=pricing)


def cmd_check(args: argparse.Namespace) -> int:
    ok, msg = check_proxy(args.proxy, timeout=args.timeout)
    print(("✓ " if ok else "✗ ") + msg)
    if not ok:
        print("Start CC Switch → enable local proxy + app takeover.")
        return 2
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    items = doctor(proxy=args.proxy, db_path=Path(args.db) if args.db else None)
    bad = 0
    for it in items:
        mark = "✓" if it.ok else "✗"
        if not it.ok:
            bad += 1
        print(f"{mark} {it.name}: {it.detail}")
    return 1 if bad else 0


def cmd_models(args: argparse.Namespace) -> int:
    cat = _catalog(args)
    rows = cat.search(query=args.query or "", family=args.family)
    if args.cheapest:
        rows = cat.cheapest(n=args.limit or 20, format=args.format)
    else:
        rows = rows[: args.limit] if args.limit else rows

    print(f"# pricing source: {cat.source} ({len(cat)} models)")
    print(
        f"{'model_id':<42} {'display':<22} {'in$/M':>8} {'out$/M':>8} {'cr$/M':>8} {'fmt':<10} fam"
    )
    print("-" * 110)
    for m in rows:
        print(
            f"{m.model_id:<42} {m.display_name[:22]:<22} "
            f"{m.input:>8.4g} {m.output:>8.4g} {m.cache_read:>8.4g} "
            f"{m.format:<10} {m.family}"
        )
    if args.json:
        print(json.dumps([m.__dict__ for m in rows], ensure_ascii=False, indent=2))
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    cat = _catalog(args)
    tokens = parse_token_amount(args.tokens)
    models = args.model or ["claude-sonnet-5"]
    mul = args.cost_multiplier
    print(f"target tokens = {tokens:,} ({human_int(tokens)})")
    print(f"mix: input={args.input_ratio:.0%} output={args.output_ratio:.0%} "
          f"cache_read={args.cache_read_ratio:.0%} cache_creation={args.cache_creation_ratio:.0%}")
    print(f"cost_multiplier = {mul}")
    print(f"pricing source: {cat.source}")
    print("-" * 72)
    for mid in models:
        p = cat.require(mid)
        cost = p.estimate_for_mix(
            tokens,
            input_ratio=args.input_ratio,
            output_ratio=args.output_ratio,
            cache_read_ratio=args.cache_read_ratio,
            cache_creation_ratio=args.cache_creation_ratio,
            cost_multiplier=mul,
        )
        # per-day rate helper
        print(
            f"{mid}\n"
            f"  {p.display_name} | in ${p.input}/M out ${p.output}/M "
            f"cr ${p.cache_read}/M cc ${p.cache_creation}/M\n"
            f"  estimated cost ≈ {human_usd(cost)} ({cost:.4f} USD)\n"
            f"  unknown_pricing={p.display_name.endswith('(unknown pricing)')}"
        )
    # throughput hint for 20亿
    if tokens >= 1_000_000_000:
        print("-" * 72)
        print("High-volume tips:")
        print("  - Prefer cheap / free upstream models when dashboard only needs volume.")
        print("  - Raise --prompt-chars and keep --max-tokens small (input-heavy).")
        print("  - Use --cache on Anthropic for cache_read tokens after first hit.")
        print("  - Use --concurrency + --rps carefully; set --max-cost-usd as hard stop.")
        print("  - Example 2B tokens:")
        print(
            "    cc-token-farm run -f anthropic -m claude-sonnet-5 "
            "--target-tokens 20亿 --prompt-chars 8000 --max-tokens 16 "
            "-c 8 --interval 0.05 --max-cost-usd 500 --yes"
        )
    return 0


def cmd_sync_pricing(args: argparse.Namespace) -> int:
    db = Path(args.db) if args.db else default_cc_switch_db()
    out = Path(args.output) if args.output else (
        Path(__file__).resolve().parent / "data" / "pricing.json"
    )
    try:
        cat = PricingCatalog.load_cc_switch_db(db)
    except Exception as e:  # noqa: BLE001
        print(f"[error] {e}", file=sys.stderr)
        return 1
    cat.export_json(out)
    print(f"✓ synced {len(cat)} models from {db}")
    print(f"  → {out}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    path = Path(args.progress_file) if args.progress_file else (
        Path.home() / ".cc-token-farm" / "progress.json"
    )
    st = ProgressStore(path).load()
    if not st:
        print(f"no progress file: {path}")
        return 1
    print(json.dumps(st.__dict__, ensure_ascii=False, indent=2, default=str))
    print(
        f"\ntotal_tokens={human_int(st.total_tokens)} cost≈{human_usd(st.cost_usd)} "
        f"status={st.status}"
    )
    return 0


def _parse_headers(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in items or []:
        if ":" not in h:
            print(f"[warn] ignore bad header: {h}", file=sys.stderr)
            continue
        k, v = h.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def cmd_run(args: argparse.Namespace) -> int:
    cfg = FarmConfig()
    if args.config:
        cfg = config_from_mapping(load_toml_or_json(Path(args.config)))

    # CLI overrides
    if args.proxy:
        cfg.proxy = args.proxy
    if args.model:
        # support multiple -m
        if len(args.model) == 1:
            cfg.model = args.model[0]
            cfg.models = []
        else:
            cfg.models = list(args.model)
            cfg.model = args.model[0]
    if args.api_key:
        cfg.api_key = args.api_key
    if args.count is not None:
        cfg.count = args.count
        cfg.forever = False
        cfg.target_tokens = None
    if args.forever:
        cfg.forever = True
        cfg.count = None
        cfg.target_tokens = None
    if args.target_tokens:
        cfg.target_tokens = parse_token_amount(args.target_tokens)
        cfg.forever = False
        cfg.count = None
    if args.concurrency is not None:
        cfg.concurrency = args.concurrency
    if args.interval is not None:
        cfg.interval = args.interval
    if args.jitter is not None:
        cfg.jitter = args.jitter
    if args.max_tokens is not None:
        cfg.max_tokens = args.max_tokens
    if args.prompt is not None:
        cfg.prompt = args.prompt
    if args.prompt_chars is not None:
        cfg.prompt_chars = args.prompt_chars
    if args.system is not None:
        cfg.system = args.system
    if args.cache:
        cfg.cache = True
    if args.stream:
        cfg.stream = True
    if args.timeout is not None:
        cfg.timeout = args.timeout
    if args.header:
        cfg.headers.update(_parse_headers(args.header))
    if args.max_cost_usd is not None:
        cfg.max_cost_usd = parse_money(args.max_cost_usd)
    if args.cost_multiplier is not None:
        cfg.cost_multiplier = args.cost_multiplier
    if args.dry_run:
        cfg.dry_run = True
    if args.quiet:
        cfg.quiet = True
    if args.verbose:
        cfg.verbose = True
    if args.progress_file:
        cfg.progress_file = args.progress_file
    if args.resume:
        cfg.resume = True
    if args.fail_threshold is not None:
        cfg.fail_threshold = args.fail_threshold
    if args.rps is not None:
        cfg.rps = args.rps
    if args.yes:
        cfg.confirm_high_cost = False

    # defaults for mode
    if not cfg.forever and cfg.target_tokens is None and cfg.count is None:
        cfg.count = 10

    cat = _catalog(args)
    price = cat.require(cfg.primary_model())
    # explicit -f wins; else config file; else infer from model pricing family
    if args.format is not None:
        cfg.format = args.format
    elif not args.config and price.format in {"anthropic", "openai"}:
        cfg.format = price.format

    cfg = merge_env(cfg)

    # profile presets
    if args.profile == "smoke":
        cfg.count = cfg.count or 3
        cfg.max_tokens = min(cfg.max_tokens, 16)
        cfg.concurrency = 1
    elif args.profile == "daily-2b":
        cfg.target_tokens = cfg.target_tokens or parse_token_amount("20亿")
        cfg.prompt_chars = cfg.prompt_chars or 8000
        cfg.max_tokens = min(cfg.max_tokens, 16)
        cfg.concurrency = max(cfg.concurrency, 8)
        cfg.interval = min(cfg.interval, 0.05)
        cfg.cache = True if cfg.format == "anthropic" else cfg.cache
        if cfg.max_cost_usd is None:
            print(
                "[warn] profile daily-2b without --max-cost-usd is dangerous; "
                "set a hard budget.",
                file=sys.stderr,
            )

    stop = threading.Event()

    def _handle(signum: int, frame: Any) -> None:  # noqa: ARG001
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle)

    engine = FarmEngine(cfg, cat, stop_event=stop)
    return engine.run()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cc-token-farm",
        description="Generate real token usage via CC Switch local proxy (no DB forgery).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  cc-token-farm check
  cc-token-farm doctor
  cc-token-farm models --cheapest
  cc-token-farm estimate --tokens 20亿 -m claude-sonnet-5 -m deepseek-v4-flash
  cc-token-farm run -m claude-sonnet-5 -n 20
  cc-token-farm run -m claude-sonnet-5 --target-tokens 1M --max-cost-usd 5 -c 4
  cc-token-farm run --profile daily-2b -m claude-sonnet-5 --max-cost-usd 100 --yes
""",
    )
    p.add_argument("-V", "--version", action="version", version=f"cc-token-farm {__version__}")
    p.add_argument("--db", default=None, help="CC Switch SQLite path (default ~/.cc-switch/cc-switch.db)")
    p.add_argument("--pricing", default=None, help="Extra pricing JSON to merge")

    sub = p.add_subparsers(dest="command", required=True)

    # check
    sp = sub.add_parser("check", help="Probe local proxy")
    sp.add_argument("--proxy", default=os.environ.get("CC_PROXY_URL", DEFAULT_PROXY))
    sp.add_argument("--timeout", type=float, default=5.0)
    sp.set_defaults(func=cmd_check)

    # doctor
    sp = sub.add_parser("doctor", help="Health check: proxy + CC Switch DB + pricing")
    sp.add_argument("--proxy", default=os.environ.get("CC_PROXY_URL", DEFAULT_PROXY))
    sp.set_defaults(func=cmd_doctor)

    # models
    sp = sub.add_parser("models", help="List models & prices (dashboard-aligned)")
    sp.add_argument("-q", "--query", default="", help="Filter by name")
    sp.add_argument("--family", default=None, help="claude|gpt|deepseek|…")
    sp.add_argument("-f", "--format", choices=("anthropic", "openai"), default=None)
    sp.add_argument("--cheapest", action="store_true")
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_models)

    # estimate
    sp = sub.add_parser("estimate", help="Estimate USD cost for a token target")
    sp.add_argument("--tokens", required=True, help="e.g. 20亿 / 2B / 500M / 1000000")
    sp.add_argument("-m", "--model", action="append", default=None)
    sp.add_argument("--input-ratio", type=float, default=0.85)
    sp.add_argument("--output-ratio", type=float, default=0.15)
    sp.add_argument("--cache-read-ratio", type=float, default=0.0)
    sp.add_argument("--cache-creation-ratio", type=float, default=0.0)
    sp.add_argument("--cost-multiplier", type=float, default=1.0)
    sp.set_defaults(func=cmd_estimate)

    # sync-pricing
    sp = sub.add_parser("sync-pricing", help="Export model_pricing from CC Switch DB")
    sp.add_argument("-o", "--output", default=None)
    sp.set_defaults(func=cmd_sync_pricing)

    # status
    sp = sub.add_parser("status", help="Show last/current progress snapshot")
    sp.add_argument("--progress-file", default=None)
    sp.set_defaults(func=cmd_status)

    # run
    sp = sub.add_parser("run", help="Send traffic through CC Switch proxy")
    sp.add_argument("--config", default=None, help="TOML/JSON config file")
    sp.add_argument("--proxy", default=None)
    sp.add_argument("-f", "--format", choices=("anthropic", "openai"), default=None)
    sp.add_argument("-m", "--model", action="append", default=None, help="Model id (repeat to rotate)")
    sp.add_argument("--api-key", default=None)
    mode = sp.add_mutually_exclusive_group()
    mode.add_argument("-n", "--count", type=int, default=None)
    mode.add_argument("--forever", action="store_true")
    mode.add_argument("--target-tokens", default=None, help="e.g. 20亿 / 2B / 1M")
    sp.add_argument("-c", "--concurrency", type=int, default=None)
    sp.add_argument("-i", "--interval", type=float, default=None)
    sp.add_argument("--jitter", type=float, default=None)
    sp.add_argument("--max-tokens", type=int, default=None)
    sp.add_argument("--prompt", default=None)
    sp.add_argument("--prompt-chars", type=int, default=None)
    sp.add_argument("--system", default=None)
    sp.add_argument("--cache", action="store_true")
    sp.add_argument("--stream", action="store_true")
    sp.add_argument("--timeout", type=float, default=None)
    sp.add_argument("--header", action="append", default=None)
    sp.add_argument("--max-cost-usd", default=None, help="Hard stop budget")
    sp.add_argument("--cost-multiplier", type=float, default=None, help="Match CC Switch provider multiplier")
    sp.add_argument("--rps", type=float, default=None, help="Global requests/sec cap")
    sp.add_argument("--fail-threshold", type=int, default=None)
    sp.add_argument("--progress-file", default=None)
    sp.add_argument("--resume", action="store_true", help="Resume counters from progress file")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("-q", "--quiet", action="store_true")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.add_argument("-y", "--yes", action="store_true", help="Skip high-cost confirmation")
    sp.add_argument(
        "--profile",
        choices=("smoke", "daily-2b"),
        default=None,
        help="Preset: smoke (3 req) | daily-2b (2e9 tokens shape)",
    )
    sp.set_defaults(func=cmd_run)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

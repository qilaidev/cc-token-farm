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
from cc_token_farm.config import (
    FarmConfig,
    config_from_mapping,
    load_toml_or_json,
    merge_env,
    validate_config,
)
from cc_token_farm.engine import FarmEngine
from cc_token_farm.gateway import (
    format_status,
    gateway_off,
    gateway_on,
    status as gateway_status,
)
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
        print("Start proxy for farming: cc-token-farm gateway on")
        print("(Do NOT enable Claude Live takeover for official accounts.)")
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


def _safety_gate(cfg: FarmConfig, cat: PricingCatalog, *, yes: bool) -> int | None:
    """Block or warn on cost-safety footguns. Return exit code to abort, else None."""
    models = cfg.effective_models()
    prices = [cat.require(m) for m in models]
    unknown = [p for p in prices if p.is_unknown]
    freeish = [
        p
        for p in prices
        if not p.is_unknown and p.input == 0.0 and p.output == 0.0
    ]

    if unknown:
        names = ", ".join(p.model_id for p in unknown)
        print(
            f"[warn] unknown pricing for: {names}\n"
            f"  Cost estimate and --max-cost-usd will under-count (treated as $0).\n"
            f"  Prefer: cc-token-farm models -q <id>  or  sync-pricing from CC Switch DB.",
            file=sys.stderr,
        )
        if cfg.max_cost_usd is not None and not cfg.dry_run:
            print(
                "[error] --max-cost-usd cannot protect you when model pricing is unknown.\n"
                "  Fix model id / pricing, or remove --max-cost-usd and accept unlimited spend risk "
                "only with -y after conscious review.",
                file=sys.stderr,
            )
            if not yes:
                return 2
            print(
                "[warn] proceeding with unknown pricing AND -y; budget hard-stop is ineffective.",
                file=sys.stderr,
            )

    if freeish and not cfg.quiet:
        names = ", ".join(p.model_id for p in freeish)
        print(
            f"[warn] zero-priced model(s): {names} — budget stop may never trip if rates stay 0.",
            file=sys.stderr,
        )

    if cfg.stream and cfg.max_cost_usd is not None and not cfg.dry_run:
        print(
            "[error] --stream with --max-cost-usd is unsafe: stream responses often omit usage, "
            "so the hard budget may not trip.\n"
            "  Drop --stream (recommended) or drop --max-cost-usd.",
            file=sys.stderr,
        )
        return 2

    # High-risk open-ended / huge runs without a hard budget.
    needs_budget = False
    reason = ""
    if cfg.forever and cfg.max_cost_usd is None:
        needs_budget = True
        reason = "--forever without --max-cost-usd"
    elif cfg.target_tokens and cfg.target_tokens >= 10_000_000 and cfg.max_cost_usd is None:
        needs_budget = True
        reason = f"--target-tokens {cfg.target_tokens:,} without --max-cost-usd"

    if needs_budget and not cfg.dry_run:
        print(
            f"[warn] {reason} can incur unbounded real spend.",
            file=sys.stderr,
        )
        if not yes and cfg.confirm_high_cost:
            if sys.stdin.isatty():
                print("  Continue without a hard budget? [y/N] ", end="", flush=True)
                ans = sys.stdin.readline().strip().lower()
                if ans not in {"y", "yes"}:
                    print(
                        "Aborted. Re-run with --max-cost-usd <budget> (recommended) or -y.",
                        file=sys.stderr,
                    )
                    return 130
            else:
                print(
                    "[error] non-interactive run refused: set --max-cost-usd or pass -y "
                    "to acknowledge unbounded spend risk.",
                    file=sys.stderr,
                )
                return 2
        elif not yes:
            # confirm_high_cost already False via -y path handled above; keep explicit
            pass
        else:
            print(
                "[warn] proceeding without --max-cost-usd (-y). You are on the hook for spend.",
                file=sys.stderr,
            )

    return None


def cmd_run(args: argparse.Namespace) -> int:
    cfg = FarmConfig()
    if args.config:
        try:
            cfg = config_from_mapping(load_toml_or_json(Path(args.config)))
        except Exception as e:  # noqa: BLE001
            print(f"[error] failed to load config {args.config}: {e}", file=sys.stderr)
            return 2

    # Env fills defaults first; CLI flags always win over env.
    cfg = merge_env(cfg)

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
        try:
            cfg.target_tokens = parse_token_amount(args.target_tokens)
        except ValueError as e:
            print(f"[error] invalid --target-tokens: {e}", file=sys.stderr)
            return 2
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
        try:
            cfg.max_cost_usd = parse_money(args.max_cost_usd)
        except ValueError as e:
            print(f"[error] invalid --max-cost-usd: {e}", file=sys.stderr)
            return 2
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

    # Ensure api_key after format may still be empty if format flips later
    cat = _catalog(args)
    price = cat.require(cfg.primary_model())
    # explicit -f wins; else config file; else infer from model pricing family
    if args.format is not None:
        cfg.format = args.format
    elif not args.config and price.format in {"anthropic", "openai"}:
        cfg.format = price.format

    # Re-apply api_key env after format is final (does not clobber explicit key/proxy).
    cfg = merge_env(cfg)

    # profile presets
    if args.profile == "smoke":
        cfg.count = cfg.count or 3
        cfg.forever = False
        cfg.target_tokens = None
        cfg.max_tokens = min(cfg.max_tokens, 16)
        cfg.concurrency = 1
    elif args.profile == "daily-2b":
        cfg.target_tokens = cfg.target_tokens or parse_token_amount("20亿")
        cfg.forever = False
        cfg.count = None
        cfg.prompt_chars = cfg.prompt_chars or 8000
        cfg.max_tokens = min(cfg.max_tokens, 16)
        cfg.concurrency = max(cfg.concurrency, 8)
        cfg.interval = min(cfg.interval, 0.05)
        cfg.cache = True if cfg.format == "anthropic" else cfg.cache
        if cfg.max_cost_usd is None:
            print(
                "[error] profile daily-2b requires --max-cost-usd <budget> "
                "(real spend at multi-billion token scale).",
                file=sys.stderr,
            )
            if not args.yes:
                return 2
            print(
                "[warn] daily-2b without budget forced via -y; unbounded spend risk accepted.",
                file=sys.stderr,
            )

    errors = validate_config(cfg)
    if errors:
        for err in errors:
            print(f"[error] config: {err}", file=sys.stderr)
        return 2

    gate = _safety_gate(cfg, cat, yes=bool(args.yes))
    if gate is not None:
        return gate

    stop = threading.Event()

    def _handle(signum: int, frame: Any) -> None:  # noqa: ARG001
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle)

    restore_after = bool(getattr(args, "restore_after", False))
    engine = FarmEngine(cfg, cat, stop_event=stop)
    try:
        return engine.run()
    finally:
        if restore_after:
            print("\n[gateway] --restore-after: turning farm gateway OFF…", flush=True)
            for line in gateway_off(db_path=Path(args.db) if getattr(args, "db", None) else None):
                print(f"  {line}", flush=True)
            print(format_status(gateway_status()), flush=True)


def cmd_gateway(args: argparse.Namespace) -> int:
    db = Path(args.db) if getattr(args, "db", None) else None
    action = args.gateway_action
    if action == "status":
        print(format_status(gateway_status(db_path=db)))
        return 0
    if action == "on":
        print("[gateway on] open proxy for farm, keep Claude CLI official")
        for line in gateway_on(db_path=db, restart=not args.no_restart):
            print(f"  {line}")
        print()
        print(format_status(gateway_status(db_path=db)))
        st = gateway_status(db_path=db)
        return 0 if st.farm_ready else 1
    if action == "off":
        print("[gateway off] stop proxy + restore Claude CLI official")
        for line in gateway_off(db_path=db, restart=not args.no_restart):
            print(f"  {line}")
        print()
        print(format_status(gateway_status(db_path=db)))
        st = gateway_status(db_path=db)
        # success if CLI not routed; port may take a moment
        return 0 if not st.claude_routed else 1
    print(f"[error] unknown gateway action: {action}", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cc-token-farm",
        description="Generate real token usage via CC Switch local proxy (no DB forgery).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  cc-token-farm gateway status
  cc-token-farm gateway on
  cc-token-farm run -m claude-opus-5 --target-tokens 1M --max-cost-usd 50 --yes --restore-after
  cc-token-farm gateway off
  cc-token-farm check
  cc-token-farm doctor
  cc-token-farm models --cheapest
  cc-token-farm estimate --tokens 20亿 -m claude-sonnet-5 -m deepseek-v4-flash
  cc-token-farm run -m claude-sonnet-5 -n 20
  cc-token-farm run -m claude-sonnet-5 --target-tokens 1M --max-cost-usd 5 -c 4
  cc-token-farm run --profile daily-2b -m claude-sonnet-5 --max-cost-usd 100 --yes --restore-after
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
    sp.add_argument(
        "--restore-after",
        action="store_true",
        help="After run ends (success/fail/interrupt): gateway off — restore official Claude CLI",
    )
    sp.set_defaults(func=cmd_run)

    # gateway — farm proxy without hijacking official Claude CLI
    sp = sub.add_parser(
        "gateway",
        help="Farm proxy on/off/status (keeps official Claude CLI off the route)",
    )
    sp.add_argument(
        "gateway_action",
        choices=("on", "off", "status"),
        help="on=open proxy (no live takeover); off=close + restore; status=inspect",
    )
    sp.add_argument(
        "--no-restart",
        action="store_true",
        help="Only write config; do not restart CC Switch app",
    )
    sp.set_defaults(func=cmd_gateway)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

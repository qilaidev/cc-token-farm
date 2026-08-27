# Changelog

## 1.1.0 — 2026-08-12

### Added
- **`gateway on|off|status`**: farm-only local proxy without Claude Live takeover; restore official CLI after farming.
- **`run --restore-after`**: always run `gateway off` when the farm exits (success / fail / interrupt).
- **`docs/GATEWAY.md`**: recommended workflow for official Claude accounts (open route → farm → close route).

### Why
- Live takeover rewrites `~/.claude/settings.json` to `127.0.0.1:15721` + `PROXY_MANAGED`; forgetting to undo it breaks daily Claude CLI (502 Connect). Farming only needs the proxy port.

## 1.0.1 — 2026-07-31

### Fixed / hardened (production readiness)
- **Cost safety gates**: refuse unknown pricing + `--max-cost-usd` (budget is ineffective at $0 rates); refuse `--stream` + budget; gate `--forever` / large targets without budget; require budget for `--profile daily-2b`.
- **Config validation** before `run` (format, concurrency, modes, ranges).
- **CLI > env > default** for proxy (`CC_PROXY_URL` no longer clobbers explicit `--proxy` / config).
- **Resume ETA**: baseline tokens so tok/s is not inflated after `--resume`.
- **Budget overshoot**: shrink in-flight batch near `--max-cost-usd`.
- **Progress**: always warn on save failure; note stop reason + last error; clearer corrupt/resume messages.
- **TOML on Python 3.10**: conditional `tomli` dependency + actionable error if missing.
- Stop reason printed as `status=… reason=…` for ops log scraping.

### Docs
- `docs/PRODUCTION.md` runbook (pre-flight, long jobs, resume, rollback, exit codes).
- SECURITY cost footguns updated.

## 1.0.0 — 2026-07-31

### Added
- Initial open-source release.
- CLI subcommands: `run`, `check`, `doctor`, `models`, `estimate`, `sync-pricing`, `status`.
- Anthropic Messages + OpenAI Chat Completions via CC Switch local proxy.
- Bundled model pricing catalog (synced from CC Switch `model_pricing`).
- Live pricing merge from `~/.cc-switch/cc-switch.db`.
- Target tokens (supports `20亿` / `2B`), max cost hard stop, concurrency, RPS, backoff.
- Progress file + resume counters.
- Profiles: `smoke`, `daily-2b`.
- Pure Python 3.10+, no third-party runtime dependencies (tomli only on 3.10 for TOML).
- Optional `scripts/oauth_forwarder.py`: clean OAuth Bearer headers when CC Switch talks to Anthropic.

### Docs
- SEO/GEO-oriented README, `llms.txt`, `docs/FAQ.md`.
- Richer `pyproject.toml` metadata (keywords, classifiers, project URLs).
- CONTRIBUTING / SECURITY notes for real-traffic boundaries and cost safety.

# Changelog

## 1.0.0 — 2026-07-31

- Initial open-source release.
- CLI subcommands: `run`, `check`, `doctor`, `models`, `estimate`, `sync-pricing`, `status`.
- Anthropic Messages + OpenAI Chat Completions via CC Switch local proxy.
- Bundled model pricing catalog (synced from CC Switch `model_pricing`).
- Live pricing merge from `~/.cc-switch/cc-switch.db`.
- Target tokens (supports `20亿` / `2B`), max cost hard stop, concurrency, RPS, backoff.
- Progress file + resume counters.
- Profiles: `smoke`, `daily-2b`.
- Pure Python 3.10+, no third-party runtime dependencies.

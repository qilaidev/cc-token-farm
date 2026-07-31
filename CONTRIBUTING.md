# Contributing

## Dev setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

## Guidelines

- Keep the runtime **stdlib-only** unless there is a strong reason.
- Prefer small, focused PRs.
- Do **not** add SQLite write paths that forge usage rows; this project only generates real proxy traffic.
- When updating pricing, prefer `cc-token-farm sync-pricing` against a local CC Switch DB, then commit `src/cc_token_farm/data/pricing.json`.
- Add/adjust tests for parsing, pricing math, and CLI smoke paths.

## Release checklist

1. Bump version in `pyproject.toml` and `src/cc_token_farm/__init__.py`
2. Update `CHANGELOG.md`
3. `pytest -q`
4. Tag `vX.Y.Z`

# Contributing

感谢贡献。请先阅读 [README.md](README.md) 了解项目边界：本仓库只生成经 CC Switch 代理的**真实**流量，不伪造用量库表。

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
- 文档改动：命令/路径/示例须与当前 CLI 及 `examples/` 一致；勿编造性能数字或用户案例。

## Docs touchpoints

| File | Role |
|------|------|
| `README.md` | 主文档 / SEO |
| `llms.txt` | AI 搜索摘要 |
| `docs/FAQ.md` | FAQ |
| `pyproject.toml` | description / keywords / urls |

## Release checklist

1. Bump version in `pyproject.toml` and `src/cc_token_farm/__init__.py`
2. Update `CHANGELOG.md`
3. `pytest -q`
4. Tag `vX.Y.Z`

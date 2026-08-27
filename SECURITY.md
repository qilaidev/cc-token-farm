# Security & cost safety

## 安全与费用安全 | Security and cost

- 本工具经 **CC Switch 本地代理**向**真实上游**发请求，会产生真实用量与账单。  
  This tool sends **real** requests through your CC Switch proxy to upstream providers.
- 大目标务必设置 `--max-cost-usd` 硬熔断；高费用确认可用 `-y` 跳过，但需自担风险。  
  Always set `--max-cost-usd` for large targets.
- **未知模型定价按 $0 估算**：此时 `--max-cost-usd` **无法**止损。CLI 默认拒绝「未知定价 + 预算」组合（除非 `-y` 明确承担风险）。先用 `models` / `sync-pricing` 对齐模型 id。  
  Unknown model prices are treated as $0 — the hard budget cannot protect you.
- **不要**把 `--stream` 与 `--max-cost-usd` 一起用（流式响应常无 usage，预算可能永不触发）。  
  Do not combine `--stream` with `--max-cost-usd`.
- `--forever`、≥10M tokens 目标、以及 `--profile daily-2b` 在无预算时会被门禁拦截或要求确认。  
  Open-ended / huge runs without a budget are gated.
- 不要提交 API Key。优先环境变量，或由 CC Switch 注入凭证。  
  Never commit API keys. Prefer environment variables or let CC Switch inject credentials.
- 不要把代理暴露到 localhost 以外。  
  Do not expose the proxy beyond localhost.
- **故意不支持**通过写 SQLite 伪造用量（数据完整性风险）。  
  Forging usage by writing SQLite is intentionally unsupported.
- 仅在你有权使用的账号、供应商与预算内运行。  
  Run only on accounts and budgets you are authorized to use.

生产运行清单见 [docs/PRODUCTION.md](docs/PRODUCTION.md)。

报告安全问题：请通过仓库 [Issues](https://github.com/tytsxai/cc-token-farm/issues) 私下说明，避免公开泄露密钥或可复现的滥用细节。

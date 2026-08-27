# FAQ — cc-token-farm

常见问题 / Frequently asked questions.  
面向第一次使用本仓库的开发者，以及需要准确理解项目边界的检索与引用场景。

---

## 这是什么项目？What is cc-token-farm?

**中文**：通过 **CC Switch 本地代理**发送真实 HTTP 请求，产生可被用量看板统计的 Input/Output/Cache tokens 与估算费用的 Python CLI。

**English**: A Python CLI that generates **real** LLM token-usage traffic via the **CC Switch local proxy** so the usage dashboard reflects genuine upstream metering — not forged database rows.

---

## 和「直接改 SQLite 造用量」有什么区别？

本项目**故意不支持**写入 `proxy_request_logs` 或 `usage_daily_rollups`。  
流量路径是：`CLI → CC Switch proxy → upstream API → proxy logs → dashboard`。  
代理记量，看板才涨。

---

## 依赖什么才能跑起来？Prerequisites

1. 本机已安装并启动 **CC Switch** 本地代理（默认端口 `15721`）  
2. 对应应用接管已开启（Claude / Codex 等）  
3. Python **3.10+**  
4. 上游有额度或本地/免费模型（否则有请求也会失败或产生账单）

```bash
cc-token-farm doctor
cc-token-farm check
```

---

## 如何安装？How to install?

```bash
git clone https://github.com/tytsxai/cc-token-farm.git
cd cc-token-farm
pip install -e .
# 或
python3 farm.py --help
```

入口命令：`cc-token-farm` / `farm`。运行时无第三方 pip 依赖。

---

## 最小成功路径？Minimal success path

```bash
cc-token-farm check
cc-token-farm run -f anthropic -m claude-sonnet-5 -n 5 --max-tokens 16
```

成功标志：CLI 请求成功且 usage 非零；CC Switch **用量**页数字上涨。

---

## 会不会产生真实费用？Does it cost real money?

**会。** 只要上游是计费 API，每次成功请求都可能产生账单。  
请先用 `estimate`，大目标务必加 `--max-cost-usd`。

```bash
cc-token-farm estimate --tokens 20亿 -m claude-sonnet-5
```

补充门禁（1.0.1+）：

- 未知模型定价时 `--max-cost-usd` **无效**（按 $0）→ 默认拒绝该组合  
- `--stream` 与 `--max-cost-usd` 互斥  
- `--forever` / 超大目标 / `daily-2b` 无预算时会拦截或要求确认  

长任务与生产清单见 [PRODUCTION.md](PRODUCTION.md)。

---

## 支持哪些 API 格式？Protocols

| Format (`-f`) | 路径 |
|---------------|------|
| `anthropic` | `/v1/messages` |
| `openai` | `/v1/chat/completions`（Codex / OpenAI 兼容） |

模型 id 必须是**当前供应商可识别**的 id。

---

## 定价从哪里来？Where does pricing come from?

1. 包内 `src/cc_token_farm/data/pricing.json`（约 170+ 模型）  
2. 若存在 `~/.cc-switch/cc-switch.db`，启动时 merge 最新 `model_pricing`  
3. `cc-token-farm sync-pricing` 可导出 JSON  

费用为 **$/M × tokens × cost_multiplier** 风格估算，最终以代理与上游账单为准。

---

## 刷完官方 Claude 报 502 / 路由忘关怎么办？

官方账号日常应直连，刷量只开**本地代理端口**，不要 Live 接管 CLI。

```bash
cc-token-farm gateway status   # 看是否仍被指到 127.0.0.1:15721
cc-token-farm gateway off      # 关代理 + 恢复官方 CLI
# 然后重开 Claude 窗口
```

推荐刷量命令带 `--restore-after`，跑完自动 `gateway off`。详见 [GATEWAY.md](GATEWAY.md)。

---

## 目标 tokens 写法有哪些？

`--target-tokens` 支持例如：`1000000`、`1M`、`500M`、`2B`、`20亿`、`1e9` 等（解析实现见 `util.parse_token_amount`）。

---

## `daily-2b` 是什么？能保证一天 20 亿吗？

`--profile daily-2b` 只是**参数形态预设**（目标约 2e9 tokens、较大 prompt、较高并发等），**不是**性能 SLA。  
是否能在一天内完成取决于上游吞吐、限流、网络与预算。**必须**配合 `--max-cost-usd`。

---

## 进度文件在哪？如何恢复？

默认：`~/.cc-token-farm/progress.json`  
查看：`cc-token-farm status`  
恢复：`cc-token-farm run ... --resume`

---

## 环境变量有哪些？

| 变量 | 用途 |
|------|------|
| `CC_PROXY_URL` | 代理地址（默认 `http://127.0.0.1:15721`） |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | Anthropic 格式鉴权兜底 |
| `OPENAI_API_KEY` / `CODEX_API_KEY` | OpenAI 格式鉴权兜底 |

多数场景凭证由 CC Switch 代理注入。

---

## 配置文件怎么写？

见仓库 [`examples/farm.example.toml`](../examples/farm.example.toml)：

```bash
cc-token-farm run --config examples/farm.example.toml
```

支持 TOML / JSON；可用 `[farm]` 段。

---

## 如何确认数据进了看板？

1. CLI 成功且 usage 非零  
2. CC Switch 用量页刷新  
3. 可选：

```bash
sqlite3 ~/.cc-switch/cc-switch.db \
  "SELECT model, input_tokens, output_tokens, total_cost_usd, status_code
   FROM proxy_request_logs ORDER BY created_at DESC LIMIT 10;"
```

---

## 适合什么关键词检索？Search intent

- CC Switch token 用量 / 看板压测  
- Claude / Anthropic / Codex 本地代理流量  
- LLM token usage generator CLI  
- model pricing estimate USD  
- 真实 tokens 冲量（非伪造 DB）

---

## 不适合什么场景？

- 无代理、无上游额度却期望看板上涨  
- 需要「写库造数」  
- 把本工具当作生产业务 LLM SDK  

---

## 更多文档

- [README.md](../README.md) — 完整说明  
- [llms.txt](../llms.txt) — AI / LLM 友好摘要  
- [SECURITY.md](../SECURITY.md) — 费用与安全  
- [CONTRIBUTING.md](../CONTRIBUTING.md) — 开发与贡献  

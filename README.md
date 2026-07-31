# cc-token-farm

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-green.svg)](https://www.python.org/downloads/)

通过 **CC Switch 本地代理** 稳定产生**真实** tokens 用量（Input / Output / Cache / 请求数 / 估算费用）。

面向用量看板压测、模型与定价对齐验证、本地代理链路健康检查。  
**不伪造 SQLite**，只走代理真实转发——代理记量，看板才涨。

```
CLI  ──HTTP──►  CC Switch proxy (127.0.0.1:15721)  ──►  Upstream API
                         │
                         ▼
              proxy_request_logs / Usage dashboard
```

## Features

- **双协议**：Anthropic `/v1/messages`、OpenAI `/v1/chat/completions`
- **模型感知**：内置 170+ 模型定价；可从 `~/.cc-switch/cc-switch.db` 热同步
- **价格感知**：运行前/运行中估算 USD；`--max-cost-usd` 硬熔断
- **高吞吐目标**：支持 `20亿` / `2B` 等目标 tokens；并发、RPS、间隔、退避
- **Cache 友好**：Anthropic `cache_control`，便于观察 cache read/creation
- **可恢复**：进度写入 `~/.cc-token-farm/progress.json`，支持 `--resume`
- **运维命令**：`check` / `doctor` / `models` / `estimate` / `status` / `sync-pricing`
- **零第三方依赖**（运行时纯标准库，Python 3.10+）

## Install

```bash
# from source
git clone https://github.com/tytsxai/cc-token-farm.git
cd cc-token-farm
pip install -e .

# or run without install
python3 farm.py --help
```

Entry points after install: `cc-token-farm` / `farm`

## Prerequisites

1. 打开 **CC Switch**，启动本地代理
2. 开启对应应用接管（Claude / Codex …）
3. 确认端口（默认 `15721`）
4. 本机自检：

```bash
cc-token-farm doctor
cc-token-farm check
```

## Quick start

```bash
# 列出模型与单价（对齐看板定价表）
cc-token-farm models -q sonnet
cc-token-farm models --cheapest --limit 15

# 估算 20 亿 tokens 费用（先算再跑！）
cc-token-farm estimate --tokens 20亿 -m claude-sonnet-5 -m deepseek-v4-flash -m gpt-5-nano

# Claude 格式小批量
cc-token-farm run -f anthropic -m claude-sonnet-5 -n 20 --max-tokens 16

# 抬高 input
cc-token-farm run -m claude-sonnet-5 -n 50 --prompt-chars 4000 --max-tokens 16 -c 2 -i 0.2

# 按目标 tokens 停 + 费用硬顶
cc-token-farm run -m claude-sonnet-5 --target-tokens 1M --max-cost-usd 3 -c 4 --yes

# 日目标 20 亿形态（务必设预算）
cc-token-farm run --profile daily-2b -m claude-sonnet-5 --max-cost-usd 100 --yes

# Codex / OpenAI 格式
cc-token-farm run -f openai -m gpt-5.2 -n 20

# 多模型轮询
cc-token-farm run -m claude-sonnet-5 -m claude-haiku-4-5-20251001 -n 30 -c 2

# Anthropic cache tokens
cc-token-farm run -f anthropic -m claude-sonnet-5 --cache -n 10 --max-tokens 16
```

配置文件示例见 [`examples/farm.example.toml`](examples/farm.example.toml)：

```bash
cc-token-farm run --config examples/farm.example.toml
```

## Commands

| Command | Purpose |
|---------|---------|
| `check` | 探测代理是否可达 |
| `doctor` | 代理 + CC Switch DB + 定价 + 最近请求 |
| `models` | 列出模型与 $/M 单价 |
| `estimate` | 按目标 tokens 估费用 |
| `run` | 真正打流量 |
| `status` | 查看进度快照 |
| `sync-pricing` | 从 CC Switch DB 导出定价 JSON |

### `run` 关键参数

| Flag | Meaning |
|------|---------|
| `-f anthropic\|openai` | 请求格式 |
| `-m MODEL` | 模型（可重复轮询） |
| `-n / --count` | 固定次数 |
| `--target-tokens` | 目标 tokens：`20亿` `2B` `500M` `1e9` |
| `--forever` | 持续到 Ctrl+C |
| `-c / --concurrency` | 并发 |
| `-i / --interval` | 批间隔秒 |
| `--rps` | 全局限速 |
| `--max-tokens` | 输出上限（建议小） |
| `--prompt-chars` | 填充 input 长度 |
| `--cache` | Anthropic prompt cache |
| `--max-cost-usd` | **费用硬停** |
| `--cost-multiplier` | 对齐供应商倍率 |
| `--resume` | 从进度文件恢复计数 |
| `--profile smoke\|daily-2b` | 预设 |
| `-y / --yes` | 跳过高费用确认 |
| `--dry-run` | 不发真实请求 |

环境变量：`CC_PROXY_URL`、`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`、`OPENAI_API_KEY` / `CODEX_API_KEY`

## 模型与定价

看板计费依赖 **模型 id + 单价表**。本工具：

1. 内置 `src/cc_token_farm/data/pricing.json`（从 CC Switch `model_pricing` 同步）
2. 若本机有 `~/.cc-switch/cc-switch.db`，启动时自动 merge 最新价
3. 估算与运行统计使用同一套 $/M 公式：

```
cost = (in·P_in + out·P_out + cache_read·P_cr + cache_creation·P_cc) / 1e6 × multiplier
```

同步本机定价：

```bash
cc-token-farm sync-pricing -o src/cc_token_farm/data/pricing.json
```

常见「天沿」/最新别名（会映射到定价表）：

| 你填的 model | 说明 |
|--------------|------|
| `claude-sonnet-5` | Sonnet 5 |
| `claude-opus-5` | Opus 5（面板名；单价对齐最新 Opus 系） |
| `claude-fable-5` / `claude-mythos-5` | 更高价档 |
| `gpt-5.2` / `gpt-5.6` / `gpt-5-nano` | GPT 5.x |
| `deepseek-v4-flash` | 低价高吞吐 |

> 模型名必须是**当前供应商可识别**的 id，否则上游失败，看板不记有效量。

## 冲高用量（含「每天 20 亿」）现实说明

`20亿 = 2e9 tokens` **可以**作为目标参数，但是：

1. **真实上游 = 真实账单**。先用 `estimate`：
   ```bash
   cc-token-farm estimate --tokens 20亿 -m claude-sonnet-5
   cc-token-farm estimate --tokens 20亿 -m deepseek-v4-flash
   cc-token-farm estimate --tokens 20亿 -m gpt-5-nano
   ```
2. 必须设 `--max-cost-usd` 硬熔断。
3. 策略建议：
   - **input 为主**：大 `--prompt-chars` + 小 `--max-tokens`
   - Anthropic 开 `--cache` 抬 cache tokens
   - 选看板可识别且单价可接受的模型
   - 并发 + RPS 控制，避免 429 / 代理打挂
4. 吞吐粗算：若稳定 5k tok/s，2e9 ≈ 4.6 天；要日内完成需要更高有效吞吐与更长 prompt。

示例（请改预算与模型）：

```bash
cc-token-farm run \
  -f anthropic -m claude-sonnet-5 \
  --target-tokens 20亿 \
  --prompt-chars 8000 --max-tokens 16 \
  --cache -c 8 --interval 0.05 \
  --max-cost-usd 100 \
  --yes
```

## 如何确认进了看板

1. CLI 出现 `[OK] http=200` 且 usage 非零  
2. CC Switch **用量**页刷新上涨  
3. 可选 SQL：

```bash
sqlite3 ~/.cc-switch/cc-switch.db \
  "SELECT model, input_tokens, output_tokens, cache_read_tokens, total_cost_usd, status_code
   FROM proxy_request_logs ORDER BY created_at DESC LIMIT 10;"
```

## 明确不做的事

- ❌ 直接改 `proxy_request_logs` / `usage_daily_rollups` 伪造数据  
- ❌ 绕过代理写库  
- ❌ 保证「零成本 20 亿」（除非上游本身免费/本地模型）

## Development

```bash
pip install -e ".[dev]"
pytest -q
python -m cc_token_farm doctor
```

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

本工具会产生**真实 API 调用与费用**。请仅在你有权使用的账号/供应商与预算内运行。作者不对滥用、超支或账号风控后果负责。

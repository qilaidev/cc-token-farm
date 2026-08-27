# cc-token-farm

**CC Switch Token Usage Generator / Traffic CLI**  
通过 [CC Switch](https://github.com/farion1231/cc-switch) 本地代理，稳定产生**真实** LLM tokens 用量（Input / Output / Cache / 请求数 / 估算费用）。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-green.svg)](https://www.python.org/downloads/)
[![Zero runtime deps](https://img.shields.io/badge/runtime_deps-stdlib_only-brightgreen.svg)](#技术栈--tech-stack)

> **一句话**：`cc-token-farm` 是面向 **CC Switch** 的模型感知、价格感知 CLI，用真实 HTTP 请求冲高代理用量看板，**不伪造 SQLite**。

---

## 项目是什么 | What is this

| | 说明 |
|---|---|
| **项目类型** | Python CLI / 本地工具（console utility） |
| **核心用途** | 经 CC Switch 本地代理（默认 `127.0.0.1:15721`）发送 Anthropic / OpenAI 兼容请求，产生可被看板统计的真实 token usage |
| **英文定位** | Model-aware, price-aware token usage traffic generator for CC Switch local proxy |
| **不做的事** | 不直接改 `proxy_request_logs` / `usage_daily_rollups`；不绕过代理写库 |

```
CLI (cc-token-farm)
    │  HTTP
    ▼
CC Switch local proxy  (127.0.0.1:15721)
    │  forward
    ▼
Upstream API (Claude / Codex / OpenAI-compatible …)
    │
    ▼
proxy_request_logs → Usage dashboard
```

代理记量，看板才涨——这是设计前提。

---

## 解决什么问题 | Problems solved

| 痛点 | 本工具做法 |
|------|------------|
| 用量看板需要**真实**流量验证 | 走代理真实转发，usage 字段来自上游响应 |
| 大目标 tokens 前不知道大概花多少钱 | `estimate` + 内置/本机合并定价表 |
| 冲量时容易 429 或打挂代理 | 并发、RPS、间隔、连续失败熔断、退避 |
| 模型 id / 单价与看板不一致 | `models` / `sync-pricing`，可从 `~/.cc-switch/cc-switch.db` 热同步 |
| 长任务中断 | 进度写入 `~/.cc-token-farm/progress.json`，支持 `--resume` |

---

## 适合谁使用 | Who is it for

- **CC Switch 用户**：要压测/对齐「用量」页、模型定价与请求链路  
- **本地代理联调者**：确认 Claude / Codex 等接管后代理可达、记量正确  
- **需要可控成本冲量的开发者**：按次数、目标 tokens 或持续运行，并设 `--max-cost-usd`  
- **不适合**：想零成本伪造看板数据、或没有上游额度/本地模型的场景  

---

## 核心功能 | Features

- **双协议**：Anthropic `/v1/messages`、OpenAI `/v1/chat/completions`
- **模型与定价**：内置约 170+ 模型单价；本机有 CC Switch DB 时自动 merge
- **费用感知**：运行前 `estimate`，运行中估算 USD，`--max-cost-usd` 硬熔断
- **目标模式**：固定次数 / `--forever` / `--target-tokens`（支持 `20亿`、`2B`、`1M` 等）
- **吞吐控制**：并发、间隔、jitter、全局 RPS、失败阈值与退避
- **Cache 友好**：Anthropic `cache_control`，便于观察 cache read / creation
- **可恢复**：进度文件 + `--resume`
- **运维子命令**：`check` · `doctor` · `models` · `estimate` · `status` · `sync-pricing` · `run`
- **零第三方运行时依赖**：Python **3.10+** 标准库即可

---

## 技术栈 | Tech Stack

| 项 | 内容 |
|----|------|
| 语言 | Python 3.10+ |
| 依赖 | 运行时 **stdlib only**（`urllib`、`argparse`、`tomllib`、`sqlite3` 等） |
| 协议 | Anthropic Messages API · OpenAI Chat Completions |
| 集成 | CC Switch 本地代理 + 可选 `~/.cc-switch/cc-switch.db` 定价同步 |
| 打包 | `pyproject.toml` / setuptools；入口 `cc-token-farm`、`farm` |
| 许可 | MIT |

---

## 快速开始 | Quick Start

### 1. 前置条件 (Prerequisites)

1. 安装并打开 **CC Switch**，启动本地代理  
2. 开启对应应用接管（Claude / Codex 等）  
3. 确认端口（默认 **15721**，可用环境变量 `CC_PROXY_URL` 覆盖）  
4. 本机自检：

```bash
cc-token-farm doctor
cc-token-farm check
```

### 2. 安装 (Install)

```bash
git clone https://github.com/tytsxai/cc-token-farm.git
cd cc-token-farm
pip install -e .

# 或不安装，直接用源码启动器
python3 farm.py --help
```

安装后命令：`cc-token-farm` 或 `farm`。

### 3. 最小可跑示例 (Minimal run)

```bash
# 列出模型与单价
cc-token-farm models -q sonnet
cc-token-farm models --cheapest --limit 15

# 先估算再跑（强烈建议）
cc-token-farm estimate --tokens 1M -m claude-sonnet-5

# 小批量真实请求（Claude / Anthropic 格式）
cc-token-farm run -f anthropic -m claude-sonnet-5 -n 20 --max-tokens 16

# 冒烟预设（约 3 次请求）
cc-token-farm run --profile smoke -m claude-sonnet-5
```

配置文件示例：[`examples/farm.example.toml`](examples/farm.example.toml)

```bash
cc-token-farm run --config examples/farm.example.toml
```

---

## 使用场景 | Use cases

| 场景 | 示例方向 |
|------|----------|
| 看板压测 / 联调 | 小 `count` 或 `--profile smoke`，确认 Usage 页上涨 |
| 模型与定价对齐 | `models` + `estimate`，对照 CC Switch 定价表 |
| 代理健康检查 | `doctor` / `check`，再发少量 `run` |
| 目标 tokens 冲量 | `--target-tokens 1M` + `--max-cost-usd` |
| 多模型轮询 | 多次 `-m`，观察不同模型记量 |
| Cache 记量观察 | `-f anthropic --cache` |

更多示例与 FAQ 见 [docs/FAQ.md](docs/FAQ.md)。

---

## 限制与注意事项 | Limitations & caveats

1. **真实上游 = 真实账单**。本工具不提供「零成本伪造 20 亿 tokens」。  
2. 大目标务必设 **`--max-cost-usd`**；高费用会提示确认，可用 `-y` 跳过（请谨慎）。  
3. **模型 id 必须是当前供应商可识别的 id**，否则上游失败，看板不记有效量。  
4. 依赖本机 **CC Switch 代理已启动**；默认代理 `http://127.0.0.1:15721`。  
5. 不保证日内完成「20 亿 tokens」——取决于上游吞吐、限流与 prompt 体积。  
6. 进度与费用为**估算对齐看板公式**，最终以代理/上游账单为准。  

详见 [SECURITY.md](SECURITY.md)。

---

## 常用命令 | Commands

| 命令 | 作用 |
|------|------|
| `check` | 探测本地代理是否可达 |
| `doctor` | 代理 + CC Switch DB + 定价 + 最近请求健康检查 |
| `models` | 列出模型与 $/M 单价（可对齐看板） |
| `estimate` | 按目标 tokens 估算 USD |
| `run` | 发送真实流量 |
| `status` | 查看进度快照 |
| `sync-pricing` | 从 CC Switch DB 导出定价 JSON |

### `run` 关键参数

| Flag | 含义 |
|------|------|
| `-f anthropic\|openai` | 请求格式 |
| `-m MODEL` | 模型 id（可重复轮询） |
| `-n` / `--count` | 固定次数 |
| `--target-tokens` | 目标 tokens：`20亿` `2B` `500M` `1e9` |
| `--forever` | 持续到 Ctrl+C |
| `-c` / `--concurrency` | 并发 |
| `-i` / `--interval` | 批间隔（秒） |
| `--rps` | 全局限速 |
| `--max-tokens` | 输出上限（冲量时建议小） |
| `--prompt-chars` | 填充 input 长度 |
| `--cache` | Anthropic prompt cache |
| `--max-cost-usd` | **费用硬停** |
| `--cost-multiplier` | 对齐供应商倍率 |
| `--resume` | 从进度文件恢复计数 |
| `--profile smoke\|daily-2b` | 预设 |
| `-y` / `--yes` | 跳过高费用确认 |
| `--dry-run` | 不发真实请求 |

**环境变量**：`CC_PROXY_URL`、`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`、`OPENAI_API_KEY` / `CODEX_API_KEY`  
（多数情况下凭证由 CC Switch 代理侧注入，CLI 也可兜底占位。）

### 更多 `run` 示例

```bash
# 抬高 input
cc-token-farm run -m claude-sonnet-5 -n 50 --prompt-chars 4000 --max-tokens 16 -c 2 -i 0.2

# 目标 tokens + 费用硬顶
cc-token-farm run -m claude-sonnet-5 --target-tokens 1M --max-cost-usd 3 -c 4 --yes

# 日目标 20 亿形态（务必设预算）
cc-token-farm run --profile daily-2b -m claude-sonnet-5 --max-cost-usd 100 --yes

# OpenAI / Codex 格式
cc-token-farm run -f openai -m gpt-5.2 -n 20

# 多模型轮询
cc-token-farm run -m claude-sonnet-5 -m claude-haiku-4-5-20251001 -n 30 -c 2

# Anthropic cache tokens
cc-token-farm run -f anthropic -m claude-sonnet-5 --cache -n 10 --max-tokens 16
```

---

## 模型与定价 | Models & pricing

看板计费依赖 **模型 id + 单价表**。本工具：

1. 内置 `src/cc_token_farm/data/pricing.json`（从 CC Switch `model_pricing` 同步）  
2. 若本机有 `~/.cc-switch/cc-switch.db`，启动时自动 merge 最新价  
3. 估算与运行统计同一套 $/M 公式：

```
cost = (in·P_in + out·P_out + cache_read·P_cr + cache_creation·P_cc) / 1e6 × multiplier
```

同步本机定价：

```bash
cc-token-farm sync-pricing -o src/cc_token_farm/data/pricing.json
```

常见别名会映射到定价表（如 `claude-sonnet-5`、`claude-opus-5`、`gpt-5.2`、`deepseek-v4-flash` 等）。完整列表以 `models` 输出为准。

> 模型名必须是**当前供应商可识别**的 id，否则上游失败，看板不记有效量。

---

## 官方账号：刷完务必关路由 | Gateway (recommended)

官方 Claude 账号日常应**直连**，不要长期 Live 接管。  
`cc-token-farm` 只需要本地代理端口，**不必**改 `~/.claude/settings.json`。

```bash
cc-token-farm gateway on          # 开代理、禁止 Live 接管、保持 CLI 官方
cc-token-farm run -m claude-opus-5 --target-tokens 1M --max-cost-usd 50 --yes --restore-after
cc-token-farm gateway status      # farm_ready + official_ok
# 未加 --restore-after 时手动：
cc-token-farm gateway off         # 关代理 + 恢复官方 CLI
```

完整说明：[docs/GATEWAY.md](docs/GATEWAY.md)

---

## 高用量策略（含「每天 20 亿」）| High-volume notes

`20亿 = 2e9 tokens` **可以**作为 `--target-tokens` 参数，但是：

1. **先估费用**  
   ```bash
   cc-token-farm estimate --tokens 20亿 -m claude-sonnet-5
   cc-token-farm estimate --tokens 20亿 -m deepseek-v4-flash
   cc-token-farm estimate --tokens 20亿 -m gpt-5-nano
   ```
2. **必须**设 `--max-cost-usd`。  
3. 策略建议：  
   - input 为主：大 `--prompt-chars` + 小 `--max-tokens`  
   - Anthropic 开 `--cache`  
   - 选看板可识别且单价可接受的模型  
   - 控制并发与 RPS，避免 429 / 代理过载  
4. 吞吐粗算：若稳定约 5k tok/s，2e9 ≈ 数天量级；日内完成需要更高有效吞吐与更长 prompt。

```bash
cc-token-farm run \
  -f anthropic -m claude-sonnet-5 \
  --target-tokens 20亿 \
  --prompt-chars 8000 --max-tokens 16 \
  --cache -c 8 --interval 0.05 \
  --max-cost-usd 100 \
  --yes
```

---

## 如何确认进了看板 | Verify dashboard usage

1. CLI 出现成功日志且 usage 非零（如 `[OK] http=200`）  
2. CC Switch **用量**页刷新上涨  
3. 可选 SQL：

```bash
sqlite3 ~/.cc-switch/cc-switch.db \
  "SELECT model, input_tokens, output_tokens, cache_read_tokens, total_cost_usd, status_code
   FROM proxy_request_logs ORDER BY created_at DESC LIMIT 10;"
```

---

## 明确不做的事 | Out of scope

- ❌ 直接改 `proxy_request_logs` / `usage_daily_rollups` 伪造数据  
- ❌ 绕过代理写库  
- ❌ 保证「零成本 20 亿」（除非上游本身免费/本地模型）

---

## 文档与元数据 | Docs

| 资源 | 说明 |
|------|------|
| [docs/FAQ.md](docs/FAQ.md) | 常见问题（中英） |
| [docs/PRODUCTION.md](docs/PRODUCTION.md) | 生产就绪清单、长任务、恢复与回滚 |
| [llms.txt](llms.txt) | 给 AI 搜索 / LLM 抓取的项目摘要 |
| [CHANGELOG.md](CHANGELOG.md) | 版本记录 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南 |
| [SECURITY.md](SECURITY.md) | 安全与费用安全 |

---

## Development

```bash
pip install -e ".[dev]"
pytest -q
python -m cc_token_farm doctor
```

生产/长任务上线前请走 [docs/PRODUCTION.md](docs/PRODUCTION.md) 检查清单。

---

## License

MIT — see [LICENSE](LICENSE).

---

## Disclaimer

本工具会产生**真实 API 调用与费用**。请仅在你有权使用的账号、供应商与预算内运行。作者不对滥用、超支或账号风控后果负责。

This tool incurs **real API usage and cost**. Run only on accounts and providers you are authorized to use, within budget.

# Production readiness — cc-token-farm

本工具是**本机 CLI**（经 CC Switch 本地代理发真实上游流量），不是常驻 HTTP 服务。  
「生产就绪」= **可安装、可安全跑长任务、可恢复、可回滚、可观测、可止损**。

---

## 1. 上线前检查清单 (Pre-flight)

| # | 检查 | 命令 / 动作 |
|---|------|-------------|
| 1 | Python 3.10+ | `python3 --version` |
| 2 | 安装可复现 | `pip install -e ".[dev]"` 或 `pip install .` |
| 3 | 单元/冒烟 | `pytest -q`；`cc-token-farm --version` |
| 4 | 代理与定价健康 | `cc-token-farm doctor` |
| 5 | 代理可达 | `cc-token-farm check` |
| 6 | 模型 id 可识别 | `cc-token-farm models -q <id>` |
| 7 | 费用预估 | `cc-token-farm estimate --tokens <N> -m <id>` |
| 8 | 冒烟真实流量 | `cc-token-farm run --profile smoke -m <id>` |
| 9 | 硬预算 | 大目标 / forever **必须** `--max-cost-usd` |
| 10 | 进度路径可写 | 默认 `~/.cc-token-farm/progress.json` |

**禁止**在未设预算时用 `--profile daily-2b` 或 `--forever` 对着付费上游直接跑。

---

## 2. 推荐生产运行方式

### 2.1 固定预算冲量

```bash
cc-token-farm run \
  -f anthropic -m claude-sonnet-5 \
  --target-tokens 1M \
  --prompt-chars 4000 --max-tokens 16 \
  -c 4 --interval 0.2 \
  --max-cost-usd 5 \
  --progress-file ~/.cc-token-farm/prod-1m.json \
  --yes
```

### 2.2 长任务 + 断点续跑

```bash
# 启动
cc-token-farm run ... --progress-file ~/.cc-token-farm/job.json --max-cost-usd 50

# 中断后查看
cc-token-farm status --progress-file ~/.cc-token-farm/job.json

# 续跑（累计 tokens/cost）
cc-token-farm run ... --resume --progress-file ~/.cc-token-farm/job.json --max-cost-usd 50
```

### 2.3 配置文件

```bash
cp examples/farm.example.toml ~/farm-prod.toml
# 编辑 proxy / model / max_cost_usd / progress_file
cc-token-farm run --config ~/farm-prod.toml
```

Python 3.10 使用 TOML 时需 `tomli`（`pyproject` 已按版本条件声明）；也可改用 JSON。

### 2.4 后台 / nohup（可选）

```bash
nohup cc-token-farm run --config ~/farm-prod.toml --yes \
  > ~/logs/cc-token-farm.out 2>&1 &
echo $! > ~/logs/cc-token-farm.pid
```

停止：`kill $(cat ~/logs/cc-token-farm.pid)`（会写 `stopped` 进度，可用 `--resume`）。

---

## 3. 安全基线 (Cost & security)

| 规则 | 说明 |
|------|------|
| 硬预算 | `--max-cost-usd` 是唯一进程内费用硬停；并发下可能略超一截，临近预算会自动降 batch |
| 未知定价 | 未收录模型单价按 $0，**无法**靠预算保护 → 默认拒绝 `unknown + max-cost`（除非 `-y` 且自担风险） |
| stream | `--stream` 常无 usage，**禁止**与 `--max-cost-usd` 同用 |
| forever / 大目标 | 无预算时非交互拒绝；交互需确认或 `-y` |
| daily-2b | **强制** `--max-cost-usd`（`-y` 可强行跳过，不推荐） |
| 密钥 | 不写进仓库；优先 CC Switch 注入或环境变量 |
| 代理 | 仅 localhost；勿把 `15721` 暴露公网 |

详见 [SECURITY.md](../SECURITY.md)。

---

## 4. 可观测性

| 信号 | 来源 |
|------|------|
| 逐请求日志 | stdout：`[OK]/` / `[FAIL]` + usage |
| 汇总行 | 结束时 `req=… tok=… cost≈… p95=…` |
| 停止原因 | `status=… reason=max_cost_usd|fail_threshold|target_tokens|interrupt|…` |
| 进度快照 | `progress.json`（约 2s 落盘，原子 replace） |
| 看板 | CC Switch 用量页 / `proxy_request_logs` |
| 健康 | `doctor` / `check` / `status` |

无 Prometheus 等远程指标——与本机 CLI 体量匹配。需要外挂监控时，对日志做 `reason=` / `cost≈` 关键字告警即可。

---

## 5. 失败模式与处理

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| exit 2 + proxy | 代理未起 / 端口错 | 启 CC Switch；`CC_PROXY_URL` 或 `--proxy` |
| 连续 FAIL 后 stop | 模型 id 错、接管关、上游 4xx/5xx | `doctor`；核对 `-m` / `-f`；调低并发 |
| cost 一直 ≈$0 | 未知定价或免费模型 | `models` / `sync-pricing`；勿依赖预算停 |
| resume ETA 乱跳 | 旧版用累计 tokens/全时 → 已用 baseline 修 | 升级后正常；看 this-run tok/s |
| progress 损坏 | 磁盘满 / 手工改坏 | 换 `--progress-file` 或删坏文件重开 |
| 预算后仍多几笔 | 并发 in-flight | 预期内；降 `-c` 或设更紧预算 |

退出码约定（摘要）：

| code | 含义 |
|------|------|
| 0 | 成功完成 / 预算触顶且有成功请求 / 用户停止但已有成功 |
| 1 | 失败（全失败、熔断等） |
| 2 | 配置/代理/安全门禁拒绝 |
| 130 | 用户中止（无成功时）或确认拒绝 |

---

## 6. 备份与回滚

| 资产 | 建议 |
|------|------|
| 进度文件 | 长任务前 `cp progress.json progress.json.bak` |
| 定价 JSON | `sync-pricing -o …` 后纳入自己的配置备份 |
| 配置 TOML/JSON | 版本管理或本机备份 |
| 本工具代码 | git tag / 固定 commit 安装 |
| CC Switch DB | 属上游应用；本工具只读 `model_pricing` |

**回滚运行**：停进程 → 换回上一版 `pip install` / git checkout → 用备份 progress 或新文件启动。  
本工具不写代理业务库，回滚无迁移风险。

---

## 7. 质量门槛

- CI：多版本 Python `pytest` + CLI smoke（见 `.github/workflows/ci.yml`）
- 合并前本地：`pytest -q` 全绿
- 发布前：对本机真实代理跑 `--profile smoke` 一次

---

## 8. 明确不做的「生产能力」

- 不提供多租户 / 远程 API 服务
- 不伪造 SQLite 用量
- 不保证日内完成「20 亿 tokens」
- 不替代上游账单与 CC Switch 看板作为最终账本

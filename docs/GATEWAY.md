# Gateway 模式：刷量开路由、用完关路由（官方账号专用）

## 问题从哪来

官方 Claude 账号正常应**直连** Anthropic（钥匙串 OAuth），**不要**长期走 CC Switch Live 接管。

| 模式 | Claude CLI | cc-token-farm | 风险 |
|------|------------|---------------|------|
| 日常办公 | 直连官方 | 不用 | 无 |
| 错误刷量 | Live 接管 → `127.0.0.1:15721` | 走代理 | 刷完忘关 → CLI 502 / 凭证错乱 |
| **推荐刷量** | **始终直连官方** | 只打本地代理端口 | 刷完 `gateway off` 即可 |

**关键点**：`cc-token-farm` 自己连 `http://127.0.0.1:15721`，**不需要**改 `~/.claude/settings.json`。  
Live 接管才会把 CLI 写成 `ANTHROPIC_BASE_URL=http://127.0.0.1:15721` + `PROXY_MANAGED`，这是事故根源。

---

## 最省心三板斧

```bash
# 1) 开刷量网关：只开代理，强制不接管 Claude CLI
cc-token-farm gateway on

# 2) 刷量（强烈建议加 --restore-after，跑完自动关）
cc-token-farm run -m claude-opus-5 --target-tokens 1M --max-cost-usd 50 --yes --restore-after

# 3) 若没加 --restore-after，手动关
cc-token-farm gateway off

# 随时查看
cc-token-farm gateway status
```

期望 `status` 在刷量中：

- `farm_ready = True`（15721 在听）
- `official_ok = True`（Claude CLI **没有**被指到 15721）

刷完后：

- `port 15721 = down`
- `Claude ANTHROPIC_BASE_URL = (unset)`
- `official_ok = True`

然后**新开或重启** Claude 窗口（旧进程可能仍缓存 env）。

---

## 命令说明

| 命令 | 作用 |
|------|------|
| `gateway on` | `enableLocalProxy=true`，`proxy_enabled=1`，**`live_takeover=0`**，剥掉 CLI 上的 15721/PROXY_MANAGED，必要时重启 CC Switch |
| `gateway off` | 关代理全部标志、从 `proxy_live_backup` / 本地备份恢复 CLI、停 oauth_forwarder(18999)、重启 CC Switch |
| `gateway status` | 一眼看路由 / 端口 / CLI 是否被劫持 |
| `run --restore-after` | 无论成功失败中断，结束时自动 `gateway off` |

---

## 不要做的事

1. 为了 farm **不要**在 CC Switch 里开「Claude Live 接管」（会改 settings.json）
2. 不要依赖长期挂着的 `oauth_forwarder :18999`（进程一挂就是 Connect 502）
3. 刷完不要只关 farm 进程、却留着 Live 接管
4. 出 502 时先 `gateway status`，再决定 `on` / `off`，不要乱重启半套配置

---

## 故障对照

| 现象 | 原因 | 处理 |
|------|------|------|
| `502 … client error (Connect)` | 代理开了但上游挂了 / CLI 指到 15721 但代理没起来 | `gateway status` → 需要刷量则 `on`，要办公则 `off` |
| `401 OAuth revoked` | provider 旧 token | 在 CC Switch 重登 Claude Official，或让 CLI 走钥匙串官方直连（`gateway off`） |
| `429 rate_limit` | 官方限流 | 等窗口；链路是通的 |
| 刷完 CLI 仍异常 | 旧 Claude 进程 env 未刷新 | `gateway off` 后重开终端 / Claude 窗口 |

---

## 和「路由」的关系（你的场景）

- **刷量**：临时开 CC Switch **本地代理端口**（给 farm 用）
- **日常**：关代理 + CLI 官方直连（**禁止**路由挂在官方账号上）
- 本模块保证：**开代理 ≠ 劫持 Claude CLI**；**关代理 = 可恢复**

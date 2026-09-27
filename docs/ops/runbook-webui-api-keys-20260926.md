# Runbook：LINGCLAUDE_API_KEYS 注入（引擎 8700 + webUI 双端）

> 2026-09-26 灵克会话产出。审计 P1#1（生产引擎与 webUI 均未配置 API 密钥 → fail-closed 全 401）的运维修复手册。
> 所有行为描述均以源码实测为准：`lingclaude/api.py:54-86`、`webui-server/src/main.rs:124-133`。

## 0. 现状基线（2026-09-26 实测裁决）

| 项 | 状态 |
|---|---|
| 引擎 `api.py --port 8700` | ✅ 运行中（pid 3443416，08:55 起），自身 netns 实测 8700 LISTEN |
| `13458` | ✅ **trae_proxy**（pid 411708，`trae_proxy.py:488` 硬编码）——**不是 webUI** |
| `23458` | ❌ 无监听（上次测试临时端口，进程已退） |
| webUI | ❌ 无常驻实例；~~`main.rs:96` 默认端口恰为 13458~~（**2026-09-27 更新：默认端口已统一为 23458**，Rust/CLI/openapi 三处同值；13458 是 trae_proxy 硬编码端口的历史冲突源） |

## 1. 生成密钥

```bash
openssl rand -hex 32   # 每端一份；引擎可持多把（逗号分隔），webUI 只认一把
```

## 2. 引擎端（8700）注入

引擎行为（`api.py:57-86`）：
- 逗号分隔多密钥；**逐请求重读 env**（热轮换，`api.py:76`）；空/缺失 → 全 401 fail-closed；`hmac.compare_digest` 常时比较。
- 校验头：`X-API-Key`（`api.py:54`）。

```bash
# 方式 A：systemd（推荐）
#   /etc/systemd/system/lingclaude-engine.service 追加：
#   Environment="LINGCLAUDE_API_KEYS=<key1>,<key2>"
systemctl restart lingclaude-engine
# 方式 B：shell 前台启动
LINGCLAUDE_API_KEYS=<key1> python3 lingclaude/api.py --port 8700
```

> 外部改 env 对已运行进程无效（env 属进程自身）；进程内热轮换仅限夹具/同进程调用方（`os.environ` 突变）。生产轮换 = 改 unit 文件 + 重启。

**验证**：
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8700/status            # 期望 401
curl -s -o /dev/null -w "%{http_code}\n" -H "X-API-Key: <key1>" http://127.0.0.1:8700/status  # 期望 200
```

## 3. webUI 端注入 + 启动

⚠ **头号 footgun**：webUI 把 `LINGCLAUDE_API_KEYS` **整串当单个密钥**发引擎（`main.rs:126` 原样读取，无逗号拆分）。webUI 端 env 必须只放**一把**密钥，配成列表 = 原样发出 = 引擎 401。

```bash
cd /home/ai/lingclaude/webui-server
export LINGCLAUDE_API_KEYS=<key1>            # 单值！必须是引擎列表中的一员
export LINGCLAUDE_WEBUI_AUDIT_LOG=/home/ai/lingclaude/.lingclaude/webui-audit.jsonl  # 可选，默认 cwd/.lingclaude/
# 远程访问才需要（默认 loopback-only，loopback 恒放行）：
export LINGCLAUDE_WEBUI_ALLOWED_HOSTS=<远程IP或主机名>
cargo run --release -- 23458                  # 端口显式给，避开 13458(trae_proxy)
```

相关默认值（`main.rs`）：`LINGCLAUDE_BASE` 默认 `http://127.0.0.1:8700`；client 连接超时 5s / 流读空闲上限 60s。

## 4. 启动后验证清单

| 步骤 | 命令 | 期望 |
|---|---|---|
| 存活 | `curl http://127.0.0.1:23458/health` | 200 `{"ok":true,...}`（R1 新端点，免鉴权） |
| 规格 | `curl http://127.0.0.1:23458/openapi.json` | 200 OpenAPI 3.1 JSON（R1 新端点） |
| 鉴权门 | `curl http://127.0.0.1:23458/status` | 401（fail-closed 正常） |
| 审计健康 | 上一步后 `curl http://127.0.0.1:23458/health` | `audit_opened:true`、`audit_requests_logged` 递增 |
| handoff | `curl http://127.0.0.1:23458/mint` | 得 `http://127.0.0.1:23458/?token=<uuid>` |
| 浏览器 | 打开 mint 返回的 URL | 302 种 cookie → SPA |
| 链路 | SPA 发一条消息 | `/chat` SSE 正常；若 webUI 未注入密钥且引擎已配 → 前端见 `event:error`（`lingclaude engine HTTP 401`） |

## 5. 故障速查

| 症状 | 根因 | 处置 |
|---|---|---|
| `/chat` 前端报 `lingclaude engine HTTP 401` | webUI 未注入 / 注入了逗号列表整串 | 按 §3 单值重配 |
| 引擎侧全 401 | 引擎 env 空（fail-closed） | 按 §2 注入并重启 |
| 端口占用 `Address already in use` | 13458 被 trae_proxy 占用（硬编码） | 换 23458 或其他端口 |
| 远程访问 403 `host not allowed` | host_guard 白名单（DNS rebinding 防护） | `LINGCLAUDE_WEBUI_ALLOWED_HOSTS` 注入远程地址 |
| `/health` 显示 `audit_opened:false` | 审计文件打开失败（目录不存在/权限） | 建目录或改 `LINGCLAUDE_WEBUI_AUDIT_LOG`；条目丢弃会计入 `audit_write_errors` |

## 6. 验证矩阵（每次变更/部署后跑一遍）

| 层 | 命令 | 通过判据 |
|---|---|---|
| 单元+集成 | `cd webui-server && cargo test` | 25 passed（含 openapi 覆盖/鉴权/审计回归） |
| 全链路 E2E | `cd webui-server && cargo build && python3 tests/e2e_smoke.py` | `E2E 全绿 9/9`；mock 引擎默认 :18700（避让生产 8700），可用 `E2E_WEBUI_PORT`/`E2E_ENGINE_PORT` 覆盖 |
| E2E 覆盖面 | — | openapi 嵌入回退 → 401 fail-closed → mint → handoff 换 cookie（一次性语义）→ status → /chat SSE(text/done) → /live → audit 落盘+计数 |

E2E 脚本内置忠实 mock 引擎（复刻 api.py `/ask/stream` SSE 契约），无需真引擎即可验证 webUI 全部用户旅程；真引擎联调见 §2/§3 注入步骤。

---
关联：审计报告 `.audit/webui_audit_e2e_20260926.md`（P1#1）· R1 规格 `webui-server/openapi.json` · 提交 149377d

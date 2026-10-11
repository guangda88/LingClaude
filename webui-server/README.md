# webui-server — 灵克浏览器 GUI（23458）

> 2026-10-10 W2 叙事修正：灵克**有浏览器 GUI**，并非「纯 CLI」。本目录是 Rust daemon，
> 前端 = `../webui`（Preact+Vite，经 rust-embed 编进二进制，见 `webui.rs:12`）。

## 架构（一条主链 + 一个 BFF）

```
浏览器 ──23458──▶ webui-server(Rust, loopback-only)
                    ├─ /chat + /chat/permission + /chat/stop
                    │    └─桥接▶ 灵克引擎 api.py :8700（/ask/stream /permission /sessions/{id}/stop）
                    └─ /sessions /projects /fs/*  ──▶ 本地 store（daemon BFF，不经过 8700）
```

- 主链契约零漂移核验（2026-10-10）：chat_api.rs ↔ api.py 逐端点对齐，
  鉴权 `X-API-Key`（chat_api.rs:130 → api.py:56），两侧共用环境变量 `LINGCLAUDE_API_KEYS`。
- 会话列表/搜索/解析前端已实装：`webui/src/api.ts:340/361/372/398`。

## 部署（systemd 托管，W1 新增）

```bash
bash scripts/install_lingk_systemd.sh    # 幂等：装 unit + enable lingk.target 四服务聚合
# 卸载: systemctl --user disable --now lingk.target lingk-webui.service
```

| 检查 | 命令 |
|---|---|
| 服务健康 | `systemctl --user is-active lingk-webui.service` |
| 四服务总览 | `python3 scripts/bootstrap_env.py`（宿主跑） |
| 页面 | http://127.0.0.1:23458 （loopback-only） |

## 前端过期告警

dist 编进二进制：`webui/src` 有文件新于二进制时需重建（安装器会自动告警）：

```bash
cd webui && npm run build && cd ../webui-server && cargo build --release
```

## 安全要点

- 仅绑 127.0.0.1（main.rs 生产默认），不对局域网暴露。
- Cookie 按端口作用域（`atomcode_webui_23458`，main.rs:465 测试锁定）。
- 审计 JSONL：unit 固定到 `logs/webui-audit.jsonl`（`LINGCLAUDE_WEBUI_AUDIT_LOG`）。
- 引擎开鉴权时：建 `%h/.lingclaude/webui.env`（`LINGCLAUDE_API_KEYS=key`）并在
  `scripts/systemd/lingk-webui.service` 取消 `EnvironmentFile` 注释。

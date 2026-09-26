# WebUI daemon 化重建 — 构建与远程访问指引（2026-09-27）

> 本轮把 lc webui-server 从 9 路由壳补齐为 AtomCode daemon 同构的 39 路由完整后端，
> 前端（atomcode 同步版）35 端点契约全覆盖。此前「页面完全不能用」的根因：
> 前端调的 `/sessions` `/projects` `/config` `/models` `/approval_mode` 等 26 个
> 端点全部 404。

## 一、宿主构建（沙箱 1GiB RLIMIT 跑不了，必须宿主执行）

```bash
# 1. 前端（若 webui/src 有改动；品牌残留修复后需重建一次）
cd /home/ai/lingclaude/webui && npm run build

# 2. webui-server（release；rust-embed 构建期重新嵌入 dist）
cd /home/ai/lingclaude/webui-server && cargo build --release
```

## 二、启动

```bash
# 本机使用（默认 127.0.0.1，安全基线）
lingclaude webui --with-engine

# 远程访问（显式动作：0.0.0.0 监听 + token + host 白名单三重防护仍在）
lingclaude webui --with-engine --remote
```

- 端口默认 **23458**（13458 与宿主 trae_proxy 硬编码冲突，本轮已改默认值）
- `--remote` 只改 bind；局域网 IP 白名单由 cli 自动注入（`LINGCLAUDE_WEBUI_ALLOWED_HOSTS`）
- 启动输出会打印带 token 的 URL（`http://127.0.0.1:23458/?token=...`）

## 三、远程访问（另一台 PC/手机）

```text
http://<主机IP>:23458/?token=<启动输出的token>
```

- 主机 IP 由启动时自动枚举注入白名单（`100.66.1.8` 这类网段直连即可）
- 仍 403 时的手动兜底：
  `LINGCLAUDE_WEBUI_ALLOWED_HOSTS=<IP> lingclaude webui --remote`

## 四、本轮新增能力（39 路由 vs 原 9）

| 类 | 端点 | 说明 |
|---|---|---|
| 会话 | GET/POST /sessions、/sessions/search、/sessions/resolve/:id、/projects/:hash/sessions(/:id GET/PATCH/DELETE)、/projects、DELETE /projects/:hash、/chat/active | 真持久化：`~/.lingclaude/webui-sessions/<hash>/<id>.json`，/chat done 时自动落盘 user/assistant 消息 |
| 项目 | GET /project、POST /cd | daemon cwd 语义（recent_dirs/previous_dir），对齐 atomcode |
| 配置 | GET /config、POST /config/reload、GET /models | 数据源 cli 注入 env（Rust 不解析 YAML）；has_api_key 恒布尔不泄密钥 |
| 杂项 | /skills、/mcp/status、/tunnel/status、/approval_mode、/permission/mode、/fs/list、/fs/mkdir、/command | 无引擎对应能力的返回契约内诚实形状（绝不 404/500） |
| live | /live/message、/live/stop、/live/permission、/live/user-input、/live/provider、/live/compact、/live/reasoning_effort、/live/switch_session、/live/mcp/trust | sync 模式（URL ?sync=1）跨标签页广播总线 |

安全不变式：一次性 handoff token、port-scoped cookie、host 白名单、
fail-closed 401、审计 JSONL 全部保留；远程暴露必须 `--remote` 显式开启。

## 五、验证清单

- Rust：`cargo test`（沙箱内联测 + 宿主全量回归）
- Python：`pytest -k "webui or cli"`（本轮 164 passed，2 存量失败为 P4.1 遗留、HEAD 同红）
- E2E：`scripts/webui_e2e_smoke.sh`（mock 引擎九步冒烟）
- 浏览器：侧边栏会话列表/项目切换/设置页/模型选择器不再是空壳

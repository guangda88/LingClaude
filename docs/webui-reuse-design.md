# LingClaude WebUI 复用设计 — 拆解 AtomCode webui 整体复用

**日期**: 2026-08-21
**作者**: AtomCode
**性质**: 架构设计（灵元思维拆解 → 复用映射 → 实施方案）
**关联**: gap_analysis/GAP_ANALYSIS_20260821.md · thread 190a4d71 · lingclaude ROADMAP P2-3

---

## 一、灵元思维拆解（第一性原理）

### 本体论 — webui 是什么

> 所有的一切，都是信息的出入导致状态之流转。

webui 的本质是**一条信息通道**：用户在浏览器输入信息（chat 请求 / 审批决策）→ 状态流转（agent 引擎执行）→ 流转结果实时推出（SSE）。没有 UI 组件、没有框架，只有**信息出入**。

### 认识论 — 如何知（四个词拆解）

| 词 | 定义 | 在本项目中的落点 |
|---|---|---|
| 主体 | 谁在流转 | lingclaude QueryEngine / AtomCode CodingRuntime |
| 目标 | 流转向何处 | 用户的对话回合（turn） |
| 信息 | 出入的内容 | chat 请求 / SSE 事件 / 审批决策 |
| 状态 | 流转的结果 | session 快照 / 实时 agent 状态 |

### 方法论 — 什么不变 → 砍到最薄 → 变化变成插片

**什么不变**（可直接复用，砍到最薄）：
1. HTTP 静态服务 + SPA fallback（rust-embed 嵌入 dist）
2. token→HttpOnly Cookie 鉴权握手（`/?token=` 一次性 → cookie）
3. SSE 传输协议（`/chat` 流式事件通道）
4. `/live` 实时状态同步通道

**什么变化**（变成插片）：
1. 前端 UI 组件（AtomCode 的 Preact 组件 → lingclaude 复用/替换）
2. 后端事件源（AtomCode `CodingRuntimeEvent` → lingclaude engine 事件）
3. 运行时上下文（provider/model/session 语义差异）

### 实践论 — 如何验

- 每个复用件有可验证入口：静态服务 `curl /index.html`、鉴权 `curl -I '/?token=X'` 见 Set-Cookie、SSE `curl -N /chat` 见 `event:` 流
- 可重复：任意人照此文档可得到同样的拆分与复用结果

---

## 二、现状基线（今日实测）

### AtomCode webui（复用源，Rust + Preact）

| 层 | 文件 | 内容 |
|---|---|---|
| 后端静态 | `atomcode-daemon/src/webui.rs` | `RustEmbed` 打 dist 进二进制；`asset_or_index` SPA fallback；`serve_webui` |
| 后端鉴权 | `atomcode-daemon/src/auth_token.rs` | 一次性 token 存储 + 鉴权中间件；`WEBUI_COOKIE="atomcode_webui"`；**per-instance `atomcode_webui_<port>` 防多实例互踩** |
| 后端 handoff | `atomcode-daemon/src/lib.rs:1586` | `serve_webui_index`：`/?token=` → `Set-Cookie: atomcode_webui_<port>=<token>; Path=/; HttpOnly; SameSite=Strict`（Secure 有意省略：明文 HTTP localhost/LAN） |
| 后端实时 | `atomcode-daemon/src/live_api.rs` | `/live` transport + `/chat` turn 构造；`CodingRuntimeEvent` → SSE 事件流；`/chat/permission` 交互审批 |
| 前端 | `webui/`（Preact 10 + vite 8 + tailwind 3 + marked + dompurify） | `src/app.tsx` / `api.ts` / `components` / `i18n.ts` / `settings.tsx`；`vite build → dist` |
| 打包 | `Cargo.toml` | `rust-embed = "8"` + `mime-guess`；dist 目录经 RustEmbed 编进二进制 |

### lingclaude（复用目标，Python）

| 项 | 现状 |
|---|---|
| 入口 | `python3 -m lingclaude.api`（FastAPI :8700，X-API-Key 认证）、`python3 -m lingclaude.cli` |
| 依赖 | tiktoken / aiohttp / pyyaml / mcp（fastapi/uvicorn 为运行时必需） |
| 引擎 | `engine/`（query_engine / tool_pipeline / coding）+ `core/session.py` |
| 部署 | pip install -e .；systemd 用户服务（lingclaude-session-sync 等）；`lingclaude run -i` 常驻 |
| Web 现状 | 无 webUI（gap_analysis 确认 ❌）；仅 HTTP API 8700 |

---

## 三、能力分层 + 复用映射

| 层 | AtomCode 组件 | lingclaude 复用策略 | 改动量 |
|---|---|---|---|
| **L1 静态服务** | `webui.rs` RustEmbed + SPA fallback | **整体复用**：独立 Rust webui 二进制（或 AtomCode daemon 内嵌），dist 由 rust-embed 编入 | 0（机制） |
| **L2 鉴权** | `auth_token.rs` + `serve_webui_index` | **整体复用**：`/?token=` 一次性 → `HttpOnly; SameSite=Strict`；cookie 名 `atomcode_webui_<port>` | 0（机制） |
| **L3 传输** | `live_api.rs` `/chat` SSE + `/live` | **复用协议**：SSE `event:` 流格式原样移植；事件源换 lingclaude engine | 中（适配层） |
| **L4 事件源** | `CodingRuntimeEvent`（agent/approval/usage/tool 事件） | **改造插片**：新增 `lingclaude → CodingRuntimeEvent` 适配器，映射 engine 事件 | 高（新写） |
| **L5 前端** | `webui/` Preact 全套 | **复用 dist + 改 API 层**：`src/api.ts` 指向 lingclaude 会话接口；组件可原样 | 低 |
| **L6 审批** | `/chat/permission` 交互审批 | **复用协议**：映射 lingclaude governance / verification_gate | 中 |

**核心架构决策**：lingclaude 是 Python，rust-embed 需 Rust 二进制。采用 **Rust webui server 独立二进制**（`lingclaude-webui`，可内嵌 AtomCode daemon 或独立 crate），通过 HTTP 桥接 lingclaude 引擎（8700 API 或进程内 JSON-RPC），dist 由 rust-embed 编入二进制——与 AtomCode 完全同构。

---

## 四、前端复用（Preact + vite → dist → rust-embed）

**复用源**：`/home/ai/atomcode-src/webui/`（Preact 10 + vite 8 + tailwind 3）

```
webui/                      # 复用 AtomCode 前端工程（拷贝 + 改 api.ts）
├── package.json            # preact/vite/tailwind 原样
├── vite.config.ts          # base:'./', build.outDir='dist' 原样
├── src/
│   ├── api.ts              # ← 改写：/chat SSE + /live 端点指向 lingclaude-webui
│   ├── app.tsx             # 原样（或按 lingclaude 品牌微调）
│   ├── components/         # 原样复用
│   ├── i18n.ts / settings.tsx  # 原样
│   └── main.tsx            # 原样
└── dist/                   # vite build 产物 → rust-embed 编入二进制
```

**rust-embed 打包**（与 AtomCode 同构）：

```rust
use rust_embed::RustEmbed;

#[derive(RustEmbed)]
#[folder = "../webui/dist"]     // vite build 产物目录
struct WebuiAssets;

pub fn asset_or_index(path: &str) -> Option<Cow<'static, [u8]>> {
    // SPA fallback：未知路径回 index.html（webui.rs 原样复用）
}
```

---

## 五、鉴权设计（/?token= 一次性 → HttpOnly Cookie）

**协议**（与 AtomCode `serve_webui_index` 完全一致）：

```
1. 启动 webui server → mint 一次性 token（UUID，仅存内存）
2. 打开浏览器 → GET /?token=<uuid>
3. 校验 token 有效 → 302 重定向到 /（剥离 token）
   Set-Cookie: atomcode_webui_<port>=<uuid>; Path=/; HttpOnly; SameSite=Strict
4. 浏览器后续请求（含 SSE）自动携带 cookie → 中间件校验
5. 非 token 请求（SPA 导航 / enforce_token=false）直通静态服务
```

**防多实例互踩**（关键设计，源自 AtomCode 实测坑）：

- cookie 名 **`atomcode_webui_<port>`**（port-scoped）
- RFC 6265：localhost cookie 忽略端口，若裸名 `atomcode_webui` 会跨实例共享——第二个实例的 handoff 会覆盖第一个的 cookie，导致第一实例全部 401
- 实现：`cookie_name = format!("atomcode_webui_{}", port)`，**禁止直接使用裸名**（AtomCode 已用 crate-private 常量防 footgun）

**安全属性**：
- `HttpOnly`：阻止 JS/扩展读取（CWE-598/522）
- `SameSite=Strict`：阻止跨站携带
- `Secure` 有意省略：明文 HTTP localhost/LAN
- 一次性：token 只用于 handoff，验证后即从内存移除（或单次有效）

---

## 六、SSE /chat 流式 + /live 实时同步接口映射

### /chat（流式对话，SSE）

**请求**（POST，cookie 鉴权）：
```json
{
  "session_id": "optional",
  "message": "用户输入",
  "images": ["base64", "..."],
  "model": "optional override",
  "mode": "auto"
}
```

**响应**（`text/event-stream`，逐事件推送）：
```
event: runtime_info     data: {"provider":"...","model":"...","config_revision":...}
event: agent            data: {"type":"text","content":"..."}
event: agent            data: {"type":"tool_use","name":"bash","input":{...}}
event: agent            data: {"type":"tool_result","call_id":"...","output":"..."}
event: approval         data: {"session_id":"...","pending":true}   // /chat/permission
event: done             data: {}
```

**事件源映射**（L4 适配器，lingclaude → 通用事件）：

| lingclaude 引擎事件 | 映射为 SSE event |
|---|---|
| `engine/query_engine` 输出文本 | `agent: {type:text}` |
| `engine/tool_pipeline` 工具调用 | `agent: {type:tool_use}` + `{type:tool_result}` |
| `verification_gate` 审批 | `approval:` + 等待 `/chat/permission` |
| session 切换/用量 | `runtime_info:` / `usage:` |

### /live（实时状态同步，SSE）

```
GET /live?session_id=xxx
→ 持续推送：agent 状态、tool 进度、session 快照、usage
→ 复用 AtomCode live_api.rs 的 transport 结构，事件源换 lingclaude
```

### 接口清单

| 端点 | 方法 | 功能 | 鉴权 |
|---|---|---|---|
| `/?token=` | GET | 一次性 token → HttpOnly Cookie handoff | 无（token 本身） |
| `/` + SPA 路由 | GET | 静态资源（rust-embed） | cookie |
| `/chat` | POST | SSE 流式对话 | cookie |
| `/chat/permission` | POST | 交互审批决策 | cookie |
| `/live` | GET | SSE 实时状态同步 | cookie |
| `/status` | GET | 运行状态/模型/会话 | cookie |

---

## 七、实施计划

| 阶段 | 内容 | 对应任务 |
|---|---|---|
| P0 | 复用前端工程（webui/ → lingclaude）+ dist 构建 | #4 |
| P0 | rust-embed 静态服务 + SPA fallback（Rust 二进制） | #4 |
| P0 | 鉴权：`/?token=` handoff + `atomcode_webui_<port>` cookie | #5 |
| P1 | /chat SSE + /live 传输层（协议复用） | #6 |
| P1 | lingclaude 事件源适配器（engine → SSE event） | #6 |
| P2 | /chat/permission 审批对接 governance | 后续 |
| P2 | lingclaude 品牌化 UI 微调 | 后续 |

---

## 附录 — 复用源文件索引

| 源（AtomCode） | 目的（lingclaude） |
|---|---|
| `atomcode-daemon/src/webui.rs` | RustEmbed 静态服务 + SPA fallback |
| `atomcode-daemon/src/auth_token.rs` | token store + 中间件 + `atomcode_webui_<port>` |
| `atomcode-daemon/src/lib.rs` `serve_webui_index` | `/?token=` → cookie handoff |
| `atomcode-daemon/src/live_api.rs` | `/chat` + `/live` transport 结构 |
| `webui/`（vite.config.ts + package.json + src/） | 前端工程（改 api.ts） |

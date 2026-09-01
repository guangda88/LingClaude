# LingClaude WebUI 事件协议 v0.1

**版本**: 0.1.1
**生效日期**: 2026-08-22（0.1.1 修订 2026-09-01，对齐 webui-server 实际实现）
**状态**: 草案（待灵克确认）
**关联**: `docs/webui-reuse-design.md` · `docs/CODEX_HARNESS_INSIGHTS.md`

> **0.1.1 修订说明**：0.1.0 是设计先行版本，多处与实现不符。本版逐条对齐
> 实现现状，未实现的机制显式标注「未实现」，不再让文档承诺不存在的行为。

---

## 一、协议概述

本协议定义 lingclaude-webui（Rust server）与 lingclaude 引擎（8700 HTTP API）之间的**事件驱动通信契约**。

- **向后兼容承诺**：服务端可在同 major version 内追加字段，不得删除或重命名已有字段
- **版本号传递**：`x-protocol-version` header / `426 Upgrade Required` 机制 — **未实现**（见 §八待决议 1）

---

## 二、端点清单

| 端点 | 方法 | 认证 | 协议 | 说明 |
|---|---|---|---|---|
| `/chat` | POST | Cookie `atomcode_webui_<port>` | SSE | 流式对话（桥接引擎 `/ask/stream`） |
| `/chat/stop` | POST | Cookie | JSON | 停止会话（语义受限，见 §3.2） |
| `/chat/permission` | POST | Cookie | JSON | 审批决策（桥接引擎 `/permission`） |
| `/live` | GET | Cookie | SSE | 实时状态同步（服务端轮询引擎） |
| `/status` | GET | Cookie | JSON | **webui-server 自身状态**（非引擎状态） |
| `/mint` | GET | 无（token 即凭证） | **text/plain** | 一次性 token URL |
| `/?token=` | GET | token | 302 | token→会话 cookie handoff |

补充约定（0.1.1 起）：

- **`/live/events` 不是对外端点**：它是 webui-server → 引擎的内部轮询路径（§4.2），
  0.1.0 误列于此。前端直接请求会得到 404。
- **静态资源公开**：带扩展名的路径（js/css/ico，含 `index.html`）不含秘密，免鉴权；
  数据面由 API 层的 cookie 校验保护。
- **未实现端点显式 404**：前端已引用但服务端未实现的 API 前缀
  （`/auth*`、`/approval_mode*`、`/command*`、`/sessions*`、`/config*`、`/fs/*`、`/live/*`）
  返回 `404 + {"type":"error","message":"endpoint not implemented..."}`，
  不再回退到 index.html 掩盖问题。

---

## 三、事件 Schema

### 3.1 `/chat` SSE 事件流

客户端 POST `/chat`，服务端以 `text/event-stream` 逐事件推送。

**请求体**：
```json
{
  "message": "用户输入",
  "session_id": "可选 — 前端 requestId，当前被接受但不消费（引擎无会话存储）",
  "images": [{"media_type": "...", "data": "..."}],
  "model": "可选 — 当前被忽略",
  "mode": "auto | build | plan — 当前被忽略"
}
```

> **0.1.1 变更**：① `images` 放宽为对象数组（此前声明为 string 数组，前端传对象
> 直接 400）；② `session_id` 不再被桥接层伪装成 `context` 注入引擎 prompt
> （每条消息 id 不同，注入只产生 `上下文：session=<随机串>` 噪声）；③
> `model`/`mode` 被忽略显式化。

**响应事件形状**：

引擎产生的 SSE 事件按 `data:` 行**原样转发**，SSE `event:` 字段一律为 `message`
（真实事件类型在 data JSON 的 `type` 字段中）。0.1.0 所列具名事件
（`runtime_info`/`tool_start`/`text`…）是引擎侧语义，桥接层不重命名；
其中 `runtime_info` 当前引擎不产生、桥不代造。

桥接层自身产生的错误事件（**必经 serde 构造合法 JSON**，0.1.1 起）：

| event | data | 说明 |
|---|---|---|
| `error` | `{"type":"error","message":"桥接失败原因"}` | 桥接/引擎不可达/引擎非 2xx |

> 0.1.0 时代错误事件由字符串拼接产生，引擎错误体含引号时整体成为非法 JSON，
> 被前端 `JSON.parse` 静默吞掉 — 此缺陷已修复，禁止回退到手工拼 JSON。

**超时**（0.1.1 起，0.1.0 承诺 504 但桥接 client 无任何超时）：
- 连接超时 5s → `error` 事件 `engine unreachable`
- 单次读超时 60s（流空闲上限）→ `error` 事件

### 3.2 `/chat/stop` 响应

**成功（引擎 2xx）**：
```json
{
  "stopped": true,
  "session_id": "请求原样回显",
  "engine": { "引擎 Session.to_dict_redacted() 原文，供排查" }
}
```

**引擎 404（会话不存在）**：
```json
{
  "stopped": false,
  "session_id": "...",
  "reason": "engine has no session with this id (frontend requestId is not an engine session_id)"
}
```

> **已知语义缺口（待引擎侧补）**：前端传的是自身 `crypto.randomUUID()` requestId，
> 引擎 `/sessions/{id}/stop` 只认自己签发的 session_id ⇒ 当前必然 404。
> 真实终止要等引擎 `/ask/stream` 暴露会话注册（0.1.0 §八-2 的延续）。

### 3.3 `/chat/permission` 响应

```json
{
  "success": true,
  "decision": "allow"
}
```

`proposal_id` 字段 0.1.0 有述、实现未返回 — 删除承诺（如引擎未来返回则透传）。
桥接失败 502（fail-closed，绝不静默放行审批）。

---

## 四、`/live` 事件 Schema

### 4.1 `/live` SSE 首次快照

```json
{
  "type": "snapshot",
  "messages": [],
  "session_id": "回显查询参数 session_id（0.1.1 起，此前恒为空串）",
  "project_hash": "",
  "provider": "lingclaude",
  "mode": "build",
  "engine": {
    "status": "online",
    "version": "0.2.2",
    "projects": ["proj-a", "proj-b"],
    "auth_required": false
  }
}
```

`messages` 恒为空数组 — 引擎当前无可查询的会话历史端点（见 §八待决议）。

### 4.2 `/live/events?since=N`（**内部路径**：webui-server → 引擎）

**请求**：`GET {lingclaude_base}/live/events?since=0`（由 `/live` SSE 循环每 3s 发起）
**响应**：
```json
{
  "events": [
    { "seq": 1, "type": "tool_start", "name": "bash", "ts": 1724300000.0 }
  ],
  "latest": 1
}
```

转发行为：有新事件 → 逐条以 `event: <type>` 转发；**本 tick 无新事件才推心跳**
（0.1.0 写的是「无新事件时」，实现最初每 3s 无条件发，0.1.1 起对齐文档）：
```json
{"ts": 1724300003}
```

---

## 五、Cookie 与安全

### 5.1 Cookie 命名（防多实例互踩）

- 格式：`atomcode_webui_<port>`（如 `atomcode_webui_13460`）
- **禁止使用裸名 `atomcode_webui`**（RFC 6265 localhost cookie 忽略端口，会导致跨实例 cookie 覆盖）
- 属性：`Path=/; HttpOnly; SameSite=Strict; Max-Age=86400`（0.1.1 起带 Max-Age）
- Secure 省略：明文 HTTP localhost/LAN 场景无需

### 5.2 认证链（0.1.1 起真实生效）

0.1.0 的问题是 cookie 只种不验 — 全服务没有任何校验点，`/chat/permission`
（审批放行）对本机任意进程裸奔。现状：

1. CLI GET `/mint` → 一次性 handoff token（**TTL 5 分钟**，未消费存量上限 128，防 drive-by 刷 `/mint` 膨胀内存）
2. 浏览器 GET `/?token=<uuid>`（在鉴权中间件内单点消费）→ 签发**会话 cookie**（TTL 24h，绝对过期）→ 302 剥离 token 参数
3. 之后所有非静态请求经中间件校验会话 cookie，失败 → **401**：
   - API 路径（`/chat*`、`/live`、`/status`）回 JSON `{"type":"error",...}`
   - 页面路径回 HTML 提示「使用 CLI 输出的带 token 链接进入」
4. handoff token 单次有效；无效 token + 已有有效会话 → 放行（老链接不踢已登录用户）；无效 token + 无会话 → 302 回 `/` → 401 页

### 5.3 引擎侧前置条件（部署注意）

引擎 `verify_api_key` 在 `LINGCLAUDE_API_KEYS` 未设置时对一切请求 401
（fail-closed）。webui-server 读取同名 env 并以 `X-API-Key` 透传。**两侧必须
配同一份 key**，否则 `/chat` 全链路 401（桥接层会以 `error` 事件明示，
不会静默）。

---

## 六、错误码约定

| HTTP 状态 | SSE event | 含义 | 0.1.1 现状 |
|---|---|---|---|
| 400 | `error` | 请求体格式错误 | ✅ |
| 401 | `error` | Cookie 无效或过期 | ✅ 0.1.1 起真实生效 |
| 404 | — | 端点不存在 | ✅ 未实现端点前缀显式 404 JSON |
| 426 | — | 协议版本不兼容 | ❌ 未实现（§八-1） |
| 502 | `error` | 引擎桥接失败 | ✅ |
| 504 | `error` | 引擎超时 | ✅ 以 connect 5s / read 60s 超时实现 |

---

## 七、版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| 0.1.0 | 2026-08-22 | 初始草案（设计先行，多处未实现） |
| 0.1.1 | 2026-09-01 | 对齐实现：鉴权链真实生效（401）、handoff/会话 TTL 与上限、错误事件 serde 化、stop 语义诚实化、`/live/events` 移出对外清单、心跳条件化、超时落地、未实现端点 404 JSON |

---

## 八、待决议事项（灵克确认）

1. **版本号 header**：`x-protocol-version` + 426 机制是否要落地？（当前未实现）
2. **引擎会话语义**：`/ask/stream` 需要签发可查询/可停止的 session_id，
   `/chat` 的 `session_id`、`/chat/stop`、`/live` 的 `messages` 才有真实语义
   — 这是当前 webUI「会话连续性是假的」的根因。
3. **`images` 消费**：引擎不支持多模态输入，桥接层忽略之 — 是否需要显式 400？
4. **`runtime_info` 事件**：是否由引擎产生（模型/provider 元数据握手）？

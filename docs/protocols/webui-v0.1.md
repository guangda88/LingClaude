# LingClaude WebUI 事件协议 v0.1

**版本**: 0.1.0
**生效日期**: 2026-08-22
**状态**: 草案（待灵克确认）
**关联**: `docs/webui-reuse-design.md` · `docs/CODEX_HARNESS_INSIGHTS.md`

---

## 一、协议概述

本协议定义 lingclaude-webui（Rust server）与 lingclaude 引擎（8700 HTTP API）之间的**事件驱动通信契约**。

- **向后兼容承诺**：服务端可在同 major version 内追加字段，不得删除或重命名已有字段
- **版本号传递**：客户端通过 `x-protocol-version` header 声明支持的协议版本；服务端在响应中回显该 header
- **未知版本处理**：客户端请求 `x-protocol-version: 0.2` 而服务端仅支持 0.1 → 返回 `426 Upgrade Required` + `protocol-version: 0.1` header

---

## 二、端点清单

| 端点 | 方法 | 认证 | 协议 | 说明 |
|---|---|---|---|---|
| `/chat` | POST | Cookie `atomcode_webui_<port>` | SSE | 流式对话 |
| `/chat/stop` | POST | Cookie | JSON | 停止当前会话 |
| `/chat/permission` | POST | Cookie | JSON | 审批决策 |
| `/live` | GET | Cookie | SSE | 实时状态同步 |
| `/live/events` | GET | Cookie | JSON | 增量事件轮询 |
| `/status` | GET | Cookie | JSON | 引擎状态 |
| `/mint` | GET | 无 | JSON | 一次性 token |
| `/?token=` | GET | token | 302 | token→cookie handoff |

---

## 三、事件 Schema

### 3.1 `/chat` SSE 事件流

客户端 POST `/chat`，服务端以 `text/event-stream` 逐事件推送。

**请求体**：
```json
{
  "message": "用户输入",
  "session_id": "可选，新建会话时省略",
  "images": ["base64-encoded-png", ...],
  "model": "可选，覆盖默认模型",
  "mode": "auto | build | plan"
}
```

**响应事件形状**（按顺序出现）：

| event | data | 说明 |
|---|---|---|
| `runtime_info` | `{"provider":"...","model":"...","protocol_version":"0.1"}` | 握手完成 |
| `tool_start` | `{"id":"uuid","name":"bash","arguments":{...}}` | 工具调用开始 |
| `text` | `{"content":"部分回答","delta":true}` | 文本增量（streaming） |
| `tool_result` | `{"id":"uuid","name":"bash","success":true,"output":"结果"}` | 工具执行结果 |
| `approval` | `{"session_id":"...","pending":true,"tool":"bash"}` | 需要审批 |
| `warning` | `{"message":"上下文即将溢出"}` | 警告（非错误） |
| `error` | `{"message":"具体错误"}` | 错误（不再继续） |
| `done` | `{}` | 对话完成 |

**错误处理**：
- 桥接失败 → `error` 事件 + SSE stream 关闭
- 引擎超时 → `error` 事件 `{"message":"timeout after 60s"}`
- 引擎 401 → `error` 事件 `{"message":"engine HTTP 401: ..."}`

### 3.2 `/chat/stop` 响应

```json
{
  "stopped": true,
  "session_id": "abc-123",
  "final_turns": 5
}
```

### 3.3 `/chat/permission` 响应

```json
{
  "success": true,
  "decision": "allow",
  "proposal_id": "optional-uuid"
}
```

---

## 四、`/live` 与 `/live/events` 事件 Schema

### 4.1 `/live` SSE 首次快照

```json
{
  "type": "snapshot",
  "messages": [],
  "session_id": "",
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

### 4.2 `/live/events?since=N` 增量事件

**请求**：`GET /live/events?since=0`
**响应**：
```json
{
  "events": [
    {
      "seq": 1,
      "type": "tool_start",
      "name": "bash",
      "ts": 1724300000.0
    },
    {
      "seq": 2,
      "type": "tool_result",
      "name": "bash",
      "success": true,
      "duration_ms": 120,
      "ts": 1724300000.1
    }
  ],
  "latest": 2
}
```

**心跳保活**（无新事件时每 3s 一次）：
```json
{"type": "heartbeat", "ts": 1724300003.0}
```

---

## 五、Cookie 与安全

### 5.1 Cookie 命名（防多实例互踩）

- 格式：`atomcode_webui_<port>`（如 `atomcode_webui_13460`）
- **禁止使用裸名 `atomcode_webui`**（RFC 6265 localhost cookie 忽略端口，会导致跨实例 cookie 覆盖）
- 属性：`Path=/; HttpOnly; SameSite=Strict`
- Secure 省略：明文 HTTP localhost/LAN 场景无需

### 5.2 Token Handoff（`/?token=`）

1. 客户端 GET `/mint` 获取一次性 token
2. 客户端 GET `/?token=<uuid>`
3. 服务端验证 token（单次有效）→ 302 重定向到 `/` + `Set-Cookie`
4. Token 消费后即从内存移除

---

## 六、错误码约定

| HTTP 状态 | SSE event | 含义 |
|---|---|---|
| 400 | `error` | 请求体格式错误 |
| 401 | `error` | Cookie 无效或过期 |
| 404 | — | 端点不存在 |
| 426 | — | 协议版本不兼容（含 `protocol-version` header） |
| 502 | `error` | 引擎桥接失败 |
| 504 | `error` | 引擎超时 |

---

## 七、版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| 0.1.0 | 2026-08-22 | 初始草案（对齐当前实现） |

---

## 八、待决议事项（灵克确认）

1. **版本号 header 命名**：`x-protocol-version`（我方提议）vs 其他方案？
2. **`/chat/stop` 端点**：当前 8700 未实现，需灵克补 `/sessions/:id/stop`
3. **协议版本前缀**：是否需要在所有 SSE event 的 data 中加入 `"protocol_version": "0.1"`？
4. **向后兼容测试**：是否需要加 snapshot 测试（keyless replay）？

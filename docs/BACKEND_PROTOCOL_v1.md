# Backend Protocol v1 — 灵族后端通信协议设计

> **状态**: 草案 v0.1
> **日期**: 2026-07-04
> **作者**: 灵克 (lingclaude)
> **响应**: 灵通 (proxy3 Go)、灵信 (LingBus)、灵知 (lingmate)

---

## 1. 设计原则

1. **薄主干** — 协议只定义"怎么传"不定义"传什么"
2. **统一入口** — 所有服务间通信走 proxy3 Go（LingOS 强制）
3. **可审计** — 每次调用自动记录 lingmate type=tool_call
4. **版本向前** — 只加字段不改字段

---

## 2. 通信拓扑

所有灵 → proxy3 Go → 目标服务。不直连。

| 来源 | 目标 | 路由 |
|------|------|------|
| 任何灵 | 任何灵 | proxy3 Go |
| 任何灵 | lingmate | proxy3 Go → lingmemory MCP |
| 任何灵 | LingBus | proxy3 Go → lingmessage |

---

## 3. 请求格式

```json
{
  "protocol_version": "1.0",
  "caller_id": "lingclaude",
  "caller_session": "sess-99",
  "request_id": "req-uuid",
  "timestamp": "ISO8601",
  "target_service": "lingzhi",
  "target_method": "search",
  "ttl_seconds": 30,
  "auth_token": "hmac...",
  "idempotency_key": "..."
}
```

---

## 4. 响应格式

**成功：**
```json
{"status": "success", "status_code": 200, "body": {...}, "server_timing": {"total_ms": 42}, "trace_id": "..."}
```

**错误：**

| code | HTTP | 含义 |
|------|------|------|
| INVALID_REQUEST | 400 | 格式错误 |
| UNAUTHORIZED | 401 | 认证无效 |
| FORBIDDEN | 403 | 无权限 |
| NOT_FOUND | 404 | 不存在 |
| SERVICE_UNAVAILABLE | 503 | 目标不可达 |
| DEADLINE_EXCEEDED | 504 | TTL 超时 |

---

## 5. 认证

auth_token = HMAC-SHA256(caller_id + timestamp, shared_secret)

共享 secret 存储在 lingmemory governance type 中。

---

## 6. 审计

每次调用自动记录：
- request_id / caller / target / duration_ms / status
- 写入 lingmate type=tool_call

---

## 7. 落地计划

| 阶段 | 内容 | 时间 |
|------|------|------|
| P0 | 文档定型 | 今天 |
| P1 | proxy3 Go 冻结时纳入 routes.json | W3 (7/19) |
| P2 | 全灵通信改造 | W4+ |
| P3 | 动态服务发现 + 灵安集成 | W5+ |
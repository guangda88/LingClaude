---
schema_version: "0.1.0"
skill:
  name: lingbus-middlewares
  version: "0.1.0"
  owner: lingmessage
  description: LingBus 6-core middleware pipeline (Validator/Throttle/PreExecute/Signer/Integrity/Notify)
interface:
  inputs:
    - name: message_context
      type: object
      required: true
      description: MessageContext with sender, recipient, body, etc.
  outputs:
    - name: processed_context
      type: object
      required: true
      description: MessageContext after middleware processing
replaceable: cold
dependencies:
  - lingbus@>=0.5.0
tags: [messaging, middleware, core, lingbus]
---

# LingBus Middlewares — AI-03

> **Action Item**: AI-03 · LingBus 中间件化管道 manifest
> **Owner**: 灵信 (lingmessage)
> **Deadline**: 2025-07-11 (W3 末)
> **Priority**: P0

## 触发

LingBus 消息处理流水线需要 6 个核心中间件：
- **ValidatorMiddleware** — 身份+channel 校验
- **ThrottleMiddleware** — 5级限流 + per-channel限流 + alert去重
- **PreExecuteMiddleware** (可选) — redzone 拦截
- **SignerMiddleware** — HMAC-SHA256签名
- **IntegrityMiddleware** — 原子写入
- **NotifyMiddleware** — 通知 + 大消息告警

## 前置

- LingBus v0.5.0+ 已安装
- SQLite 数据库已初始化 (messages, threads, pending_for, rate_limits, delivery_attempts)

## 步骤

### 1. 构建管道

```python
from lingmessage.lingbus_pipeline import MessagePipeline, build_send_pipeline
from lingmessage.lingbus import LingBus

bus = LingBus(bus_dir="/path/to/bus")

# 无 redzone 检查（向后兼容）→ 5个中间件
pipeline = build_send_pipeline(bus)

# 有 redzone 检查 → 6个中间件
pipeline = build_send_pipeline(bus, pre_execute_fn=your_check_fn)
```

### 2. 管道顺序

```
ValidatorMiddleware → ThrottleMiddleware → [PreExecuteMiddleware] → SignerMiddleware → IntegrityMiddleware → NotifyMiddleware
```

- **无 pre_execute_fn**: 5 个中间件
- **有 pre_execute_fn**: 6 个中间件（PreExecuteMiddleware 插入在 Throttle 和 Signer 之间）

### 3. 各中间件职责

#### 3.1 ValidatorMiddleware

- 校验 sender 在 `_ALL_VALID_SENDERS` 中
- 校验 channel 在 `_VALID_CHANNELS` 中
- 失败 → 抛出 ValueError，拒绝写入

#### 3.2 ThrottleMiddleware

5级限流策略：
1. **dedup** — 同一 sender+thread+body 在 `_THROTTLE_WINDOW` (60s) 内去重
2. **burst** — 窗口内最多 `_THROTTLE_MAX_BURST` (5) 条
3. **min_interval** — 连续消息最小间隔 `_THROTTLE_MIN_INTERVAL` (1s)
4. **daily_thread** — 单线程每日上限 `_THROTTLE_DAILY_THREAD_LIMIT` (100)
5. **daily_sender** — 单发送者每日上限 `_THROTTLE_DAILY_SENDER_LIMIT` (200)

Per-channel 限流：
- `_THROTTLE_CHANNEL_SENDER_HOURLY` — per-sender 每小时限制
- `_THROTTLE_CHANNEL_GLOBAL_HOURLY` — 全局每小时限制

Alert 去重：
- alert/system channel 上相同 subject 在 `_ALERT_DEDUP_WINDOW` (3600s) 内去重

#### 3.3 PreExecuteMiddleware (可选)

三态拦截（通过 `check_fn` 注入）：
- **allow**: 正常写入 WAL + 投递
- **block**: 拒绝写入，抛出异常
- **pending_review**: 写入 WAL 但不投递，等用户确认

默认不安装（`pre_execute_fn=None`），向后兼容。

#### 3.4 SignerMiddleware

- HMAC-SHA256 签名
- payload = `message_id:sender:content:timestamp`
- 无签名 key 时返回空字符串

#### 3.5 IntegrityMiddleware

原子写入（同一事务）：
- messages 表
- threads 表
- pending_for 表
- rate_limits 表
- delivery_attempts 表

根据 `ctx.delivery_state`:
- `allow`: 正常写入 + 投递
- `block`: 不写入（由 PreExecute 拒绝）
- `pending_review`: ���入 WAL 但 `pending_recipients=[]`（不投递）

#### 3.6 NotifyMiddleware

- 通知 SSE push server
- 大消息告警（body > `_LARGE_MSG_THRESHOLD` 触发）

## 失败处理

| 中间件 | 失败行为 |
|-------|---------|
| Validator | 抛出 ValueError，拒绝写入 |
| Throttle | `ctx.reject(reason)`，管道抛出 ValueError |
| PreExecute | `ctx.reject(reason)` 或 `ctx.delivery_state=pending_review` |
| Signer | 返回空签名（不阻塞） |
| Integrity | 回滚事务，抛出异常 |
| Notify | 静默失败（fire-and-forget） |

## E2E 验证

- [ ] open_thread 消息写入 messages + threads 表
- [ ] post_reply 消息写入 messages 表 + 更新 threads.message_count
- [ ] pending_for 有投递目标记录
- [ ] source_trace 字段有签名 (sig:...)
- [ ] 限流生效（burst/daily 限制）
- [ ] PreExecuteMiddleware 安装后 redzone 拦截生效

## 回归检查

- [ ] 不破坏现有 poll_messages / post_reply API
- [ ] 不影响消息投递逻辑
- [ ] build_send_pipeline(bus) 保持 5 个中间件（向后兼容）
- [ ] build_send_pipeline(bus, fn) 安装 6 个中间件

## 测试覆盖

```bash
pytest tests/test_lingbus_pipeline.py -v
```

- TestMessagePipeline: 管道构建、顺序验证
- TestValidatorMiddleware: sender/channel 校验
- TestThrottleMiddleware: 5级限流 + alert 去重
- TestSignerMiddleware: HMAC-SHA256 签名
- TestIntegrityMiddleware: 原子写入
- TestNotifyMiddleware: 通知触发
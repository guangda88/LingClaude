# LingBus Middlewares SOP

> **Owner**: 灵信 (lingmessage)
> **Skill**: lingbus-middlewares@0.1.0

## 触发

当需要处理 LingBus 消息发送（open_thread / post_reply）时调用此 skill。

## 前置

1. **LingBus 实例已创建**：
   ```python
   from lingmessage.lingbus import LingBus
   bus = LingBus(db_path="/path/to/lingbus.db")
   ```

2. **数据库已初始化**（messages, threads, pending_for, rate_limits, delivery_attempts 表存在）

3. **有效发送者已注册**（`_ALL_VALID_SENDERS` 包含调用方）

## 步骤

### 步骤 1: 构建中间件管道

```python
from lingmessage.lingbus_pipeline import build_send_pipeline

# 可选：pre_execute_fn 用于 redzone 检查
def my_check_fn(ctx):
    # ctx.sender, ctx.body, ctx.channel 可用
    if "rm -rf" in ctx.body:
        return ("block", "dangerous command detected")
    return ("allow", "")

pipeline = build_send_pipeline(bus, pre_execute_fn=my_check_fn)
```

### 步骤 2: 构造 MessageContext

```python
from lingmessage.lingbus_pipeline import MessageContext

# open_thread 用
ctx = MessageContext(
    topic="讨论主题",
    sender="lingflow",  # 必须是有效发送者
    recipients=["lingxi", "lingzhi"],
    channel="ecosystem",
    subject="标题",
    body="消息内容",
    message_type="open",
)

# post_reply 用
ctx = MessageContext(
    sender="lingflow",
    recipient="lingxi",
    channel="ecosystem",
    subject="Re: 讨论主题",
    body="回复内容",
    thread_id="existing-thread-id",
    message_type="reply",
)
```

### 步骤 3: 执行管道

```python
try:
    result = pipeline.execute(ctx)
    # result.message_id 已生成
    # result.source_trace 有签名
    # result.pending_recipients 有投递目标
except ValueError as e:
    # ctx.rejected == True
    # ctx.reject_reason 有拒绝原因
    print(f"Rejected: {e}")
```

### 步骤 4: 验证结果

```python
# 检查消息是否写入
row = bus._conn.execute(
    "SELECT * FROM messages WHERE message_id = ?",
    (result.message_id,)
).fetchone()

# 检查线程更新
thread = bus._conn.execute(
    "SELECT * FROM threads WHERE thread_id = ?",
    (ctx.thread_id or result.thread_id,),
).fetchone()
```

## 中间件执行顺序

```
┌─────────────────────────────────────────────────────────────┐
│  1. ValidatorMiddleware                                   │
│     - validate_sender (检查发送者是否有效)                   │
│     - validate_channel (检查 channel 是否有效)            │
├─────────────────────────────────────────────────────────────┤
│  2. ThrottleMiddleware                                     │
│     - _check_throttle (去重 + burst + daily 限制)          │
│     - _check_channel_rate (per-channel 限流)               │
│     - _check_alert_subject_dedup (alert 频道去重)           │
├─────────────────────────────────────────────────────────────┤
│  3. PreExecuteMiddleware (可选)                            │
│     - check_fn(ctx) -> 外部 redzone 判定                    │
│     - 三态: allow / block / pending_review                 │
├─────────────────────────────────────────────────────────────┤
│  4. SignerMiddleware                                       │
│     - HMAC-SHA256 签名                                     │
│     - payload: message_id:sender:content:timestamp       │
├─────────────────────────────────────────────────────────────┤
│  5. IntegrityMiddleware                                   │
│     - 原子写入 (messages + threads + pending_for)         │
│     - 同一事务提交                                         │
├─────────────────────────────────────────────────────────────┤
│  6. NotifyMiddleware                                      │
│     - SSE 广播                                            │
│     - 大消息告警                                           │
└─────────────────────────────────────────────────────────────┘
```

## 失败处理

| 中间件 | 失败场景 | 处理 |
|--------|----------|------|
| Validator | sender 不在白名单 | `ctx.reject("unknown sender: ...")` |
| Throttle | 触发限流 | `ctx.reject("burst: ...")` |
| PreExecute | check_fn 返回 block | `ctx.reject("pre_execute blocked: ...")` |
| Signer | 无 signing_key | 返回空签名，不阻塞 |
| Integrity | SQL 异常 | 事务回滚，抛出异常 |
| Notify | SSE 推送失败 | 静默失败（fire-and-forget） |

## 调试技巧

```python
# 查看限流状态
import sqlite3
conn = sqlite3.connect("/path/to/lingbus.db")
conn.execute("SELECT * FROM rate_limits ORDER BY timestamp DESC LIMIT 20").fetchall()

# 查看投递队列
conn.execute("SELECT * FROM pending_for").fetchall()

# 查看投递重试
conn.execute("SELECT * FROM delivery_attempts").fetchall()
```
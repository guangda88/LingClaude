# 熔断闸 event_sink → ly_state_events 接线文档

> 状态：已批准（建闸期文档件 2，2026-09-25）
> 落点：`lingclaude/core/plugin_lifecycle.py` `CircuitBreaker._sink`（:81, :89, :117-121）
> 目的：熔断事件经 sink 外发落 `ly_state_events` 账（重启不丢、绕过风暴期不可达，best-effort 纪律不变）

## 1. 现状

`CircuitBreaker.__init__(..., event_sink: Optional[Callable[[dict], None]] = None)` 已留接线桩
（:81 参数 / :89 存槽 / :117-121 调用），当前**默认无人装配**。熔断事件只走 `logger.warning`
（:115-116），不落账。docstring（:68）明确标注「后续对接 ly_state_events 落账：重启不丢、绕过风暴」。

`_emit` 产出的记录形态（:112-114）：

```python
rec = {
    "event": str,      # breaker_open / breaker_close / probe_failed / probe_allowed / denial...
    "layer": str,      # fiber | domain | global
    "target": str|None # fiber 名或缝域
    "ts": float,       # time.time()
    **detail,          # 各事件附加字段（fiber=、failures=、threshold= 等）
}
```

## 2. 接线目标：ly_state_events 表结构

`state_store.py:152-163` 的 DDL（LingYiBackend.ensure_schema 幂等建表）：

```
ly_state_events (
    record_type, key, event_type, from_state, to_state, actor, data, timestamp
)
```

## 3. 字段映射（sink dict → ly_state_events 行）

| ly_state_events 列 | 来源（sink rec） | 说明 |
|---|---|---|
| record_type | 固定 `breaker` | 熔断事件独立 record_type，与迁移/状态事件分离 |
| key | `f"{layer}:{target or 'global'}"` | 如 `fiber:cap_infer` / `domain:gov` / `global:global` |
| event_type | `rec["event"]` | breaker_open/close/probe_* /denial |
| from_state | 当前熔断状态 | sink 不带状态时由接线器自行维护 |
| to_state | 目标熔断状态 | CLOSED→OPEN→HALF_OPEN→CLOSED 语义 |
| actor | 固定 `lifecycle_circuit_breaker` | 出账主体 |
| data | `json.dumps({k:v for k,v in rec.items() if k not in {"event","layer","target","ts"}})` | 附加详情 |
| timestamp | `rec["ts"]` 转 ISO | 统一时间戳 |

## 4. 接线器实现草案（小件，随建闸期④或闭合期落地）

```python
# lingclaude/core/breaker_event_sink.py（草案）
"""CircuitBreaker event_sink → ly_state_events 接线器。

best-effort：sink 异常只记日志，绝不阻断闸（与插件_lifecycle 纪律一致）。
依赖 LingYiBackend 可写（DSN 就绪）；不可写时静默降级为仅日志。
"""
import json, logging
from datetime import datetime, timezone
logger = logging.getLogger(__name__)

def build_breaker_sink(backend) -> callable:
    """返回可注入 CircuitBreaker(event_sink=...) 的 sink。"""
    def sink(rec: dict) -> None:
        if backend is None:
            return
        try:
            event_type = rec.get("event", "breaker_event")
            key = f"{rec.get('layer','?')}:{rec.get('target') or 'global'}"
            data = {k: v for k, v in rec.items()
                    if k not in {"event", "layer", "target", "ts"}}
            ts = rec.get("ts", 0.0)
            # 内部经 async 桥接写 LingYiBackend.append（复用 state_store 事件路径）
            _append_async(backend, "breaker", key, event_type,
                          from_state="", to_state="",
                          actor="lifecycle_circuit_breaker",
                          data=json.dumps(data, ensure_ascii=False),
                          ts=ts)
        except Exception:  # noqa: BLE001
            logger.exception("breaker_sink: 落账失败（忽略，闸不受影响）")
    return sink
```

> 注：`_append_async` 复用 `LingYiBackend` 事件追加路径（`state_store.py` 内已实现的
> `INSERT INTO ly_state_events`），避免重复建连；具体 asyncio 桥接方式随建闸④/闭合期
> 落地时按既有 `asyncio.run` 惯例实现（与 `StateStore.save` 双写路径 :260-266 同款）。

## 5. 装配点

`LifecycleManager.__init__` 构造 `CircuitBreaker` 时注入（`plugin_lifecycle.py:368`）：

```python
self.breaker = breaker if breaker is not None else CircuitBreaker(event_sink=build_breaker_sink(lingyi_backend))
```

- 若 LingYiBackend 不可用（无 DSN / asyncpg 缺失）→ `build_breaker_sink(None)` 返回空 sink，
  与 `StateStore` 自动回落 JsonFileBackend 同纪律（`state_store.py:17,241-246`）。
- 风暴期（熔断已 OPEN）时网关可能不可达，sink 的**本地异步缓冲/重试**作为后续增强项，
  本期不引入（best-effort 语义：账可能短暂缺失，但不阻断闸）。

## 6. 验收判据

1. 注入 sink 后，制造一次 `breaker_open`（注入紧阈值 CircuitBreaker，如 fiber_threshold=1）→
   `ly_state_events` 出现 record_type=`breaker`、event_type=`breaker_open` 的行；
2. sink 抛异常时不阻断闸自身动作（回归 17/17 熔断测试仍绿）；
3. LingYiBackend 不可写时静默降级，无 traceback 泄漏到调用方。

## 7. 与相关账本的关系

- `ly_state_events` 是三原语之一（2T3A），熔断事件入账后即成为**插片风暴的审计真相源**——
  后续「插片生命周期也入 2T3A 事件（分形记账）」沿同一路径扩展；
- 与双写协议文档（`state_migration_dualwrite_protocol.md` §5.3 revert 事件）共用同一事件表，
  事件表是唯一追加真相源（codex 合议：events 追加日志为唯一真相源）。

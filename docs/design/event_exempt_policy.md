# 免事件化白名单 event_exempt 政策（建闸期④）

> 落地：synthesis-20260925 评审合议「修正版 A」裁定（user 裁定原文见 2T3A 卷宗）
> 实现：`lingclaude/core/event_exempt.py` + `tests/test_event_exempt.py`（10 cases）

## 一、裁定三条款

1. **豁免的是中间态事件，不是边界事件**——白名单热路径只记
   `enter`（入）/ `exit`（出，含耗时）/ `error`（错，含异常摘要）三类边界事件。
   中间过程的每次过缝、每次状态微变不落账。
   **任何白名单动作至少留 3 条账，0 条才是违规。**
2. **豁免登记在册、可撤销**——豁免表是 StateStore 的一条 record
   （`record_type=event_exempt_whitelist`，key=`global`），
   成员含 fiber+理由+批准人+到期复查日+支撑证据。
   到期未复查自动失效回全记账——豁免本身要被审计（Goodhart 防御）。
3. **error 事件永不豁免**——白名单路径出错时升格全粒度（`escalated=true`）。
   平峰期省账，故障期全账。

## 二、三物件边界表（互斥作用面，防止再混淆）

| 物件 | 管 | 语义 | 落地 |
|---|---|---|---|
| 熔断闸白名单（`plugin_lifecycle.CircuitBreaker.whitelist`） | **断路** | 风暴期不熔断谁（保留最小可服务子集） | `53a0166` |
| **免事件化白名单（`core/event_exempt.py`）** | **记账** | 谁的中间态不逐条落账（必留边界三事件） | 本提交 |
| 迁移冻结（`core/state_reconcile.py` frozen） | **切换** | has_drift 时禁止读切换 | `ef23407` |

## 三、命名隔离条款

- `whitelist` 一词在 **lifecycle 语境**专指**熔断豁免**（断路侧）
- `event_exempt` 一词在 **event 语境**专指**记账豁免**（账本侧）
- 两者禁止跨用。代码评审与文档引用时先问语境。

## 四、三值判定 verdict

| verdict | 含义 | 落账行为 |
|---|---|---|
| `record_full` | 全粒度（不在表/已到期/phase=error/表损坏 fail-open） | 写事件，granularity=full |
| `record_boundary` | 在表 fiber 的 enter/exit | 写事件，granularity=boundary |
| `record_exempt` | 在表 fiber 的中间态 | **不写**，emit 返回 False |

## 五、fail-safe 方向铁律

任何异常（表损坏/读取失败/判定出错/落账失败）一律回退**全记账**或安全返回——
**豁免失效只会多记，不会漏记：账本宁可膨胀，审计不可瞎掉**
（铁律「一切真相外化为可查询结构」的账本侧表述）。

## 六、与主干的边界

- `state_store.py` 是 L1 主干冻结区成员（只许修 bug 不许加功能）——
  本机制**一行不动主干**；「事件写入点前置检查」落在
  `event_exempt.emit_boundary_event` 公共入口内部实现
- 落账位：`plugin_events/<fiber>/<ts>` record（json 后端文件态、lingyi 双写自动继承）
- 对账 lineage 骨架（`LingYiBackend.save` 内嵌 `'update'` 事件）**永不豁免**——
  event_exempt 只作用于边界事件面，不触碰对账事件流

## 七、加白操作（SOP）

```python
from lingclaude.core.event_exempt import add_exemption, revoke_exemption
from lingclaude.core.state_store import StateStore

store = StateStore()
# 加白（必须给理由+证据，默认 30 天复查）
add_exemption(store, "tool_pipeline",
              reason="实测单日 4.2 万次调用，逐条落账日增 38MB",
              evidence={"calls_per_day": 42000, "ledger_growth_mb": 38},
              review_days=30, added_by="lingke")
# 撤销
revoke_exemption(store, "tool_pipeline")
```

到期复查：`review_due` 过期后 `should_record` 自动返回 `record_full`，
并 logger.warning 提示复查——续期须重走加白 SOP（更新理由与证据）。

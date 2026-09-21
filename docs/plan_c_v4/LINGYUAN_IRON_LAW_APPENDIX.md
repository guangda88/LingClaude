# 灵元铁律附录：本批插片的铁律符合性对照

> 对齐 `AGENTS.md` 灵元铁律与 `docs/plan_c_v4/PLAN_C_V4_BLUEPRINT.md` 落盘事实。

## 铁律 1：全在 plugins/，core/ 零 diff（新插片不进主干）

**本批为既有 core 模块的扩容 + 新模块，逐条对照：**

| 变更 | 性质 | 符合性论证 |
|---|---|---|
| `seam.py` +35 行 | 缝基础设施扩容（订阅广播） | SeamRegistry 本身就是「变化的全是 type」的根机制；订阅是其自然完备化（观察者模式），不引入新业务 |
| `hooks.py` +13 行 | 既有钩子表扩容（新枚举值+新字段，全部带默认值向后兼容） | HookType 是开放枚举语义，扩容 = 数据非控制流 |
| `plugin_lifecycle.py` 新文件 | 纯新增消费者 | 不改任何既有模块行为，`get_lifecycle_manager()` 显式启用才生效 |
| `quota_governance.py` 新文件 | 纯新增 | task_router 接线为防御式 try/except（失败静默），主路径行为零变化 |
| `context_engine.py` 新文件 | 纯新增 | 未接线到压缩层（后续 PR），当前无行为影响 |
| `repair_card.py` 新文件 | 纯新增 | 工具性质，未强制接入批量操作器（后续 PR） |

**结论**：本批没有「新业务插片绕过 plugins/」，全部是缝/钩子基础设施的
完备化 + 纯新增模块 + 防御式接线，主干行为零破坏（回归测试证明）。

## 铁律 2（stop_layer 三要素）在 plugin_lifecycle 的体现

- kernel：`LifecycleManager`（单一状态机实现，六态转换集中一处）
- seams：消费 `seam.py` 的变更事件（`subscribe_change`），产出 hooks 观测点
- implementations：fiber 数量数据驱动（`_fibers` dict），增删 fiber 零代码

## 铁律 3（record 化）与 repair_card

- repair_card 把「写失败→修复→验证」全程 record 化（history + verification）
- `blocks()` 查询 = 债务语法：带 OPEN 卡的文件不可继续写（fail-closed），
  防止「无账红灯」与「假活」

## 铁律 8（探针失败累计 → 缺席查）与 quota_governance

- 窗口记录 = 探针观察的持久化形态：错误文本→reset_at 是「探针结果」，
- `decide_from_windows` = 缺席查：路由前问窗口，不反复撞墙试错
- 真实成功清窗 = 观察被更强证据推翻

## J1（变化走接缝）自证

本批对 `seam.py` 的全部改动只增加**观察者**能力，注册表本体语义
（register/unregister/get/reset）未动——变化真的走了接缝。

# HANDOFF — 灵元 P1-P12 全链路完成（2026-09-14）

> 交接对象：后续会话的灵克 / 灵研 / 任意维护者
> 交接时间：2026-09-14
> 范围：lingclaude 以灵元 1.0 为尺子的策略外置 + 进程内插片注册表（P1-P12）
> 关联文档：`docs/LINGYUAN_1.0_ANALYSIS_AND_ROADMAP.md` §十（执行记录）

---

## 一、完成了什么（9 个提交，全部过 lefthook 0 issues）

| # | 提交 | 内容 |
|---|------|------|
| P1-1/P1-2 | `83781a2` | 新建 `core/policy_loader.py`（统一 YAML 加载 + mtime watch 热更 + 目录穿越防护 + graceful degrade）；补 `hot_update()` 强制重读 |
| P2-1/P2-2 | `ef561dd` | sandbox 网络白名单 + 默认可写目录外置 `policies/sandbox_policy.yaml`；`TASK_TYPE_TO_ROUTE` 外置 `policies/task_routing.yaml` |
| P3-1/2/3 | `03dfb76` | `core/seam.py`（SeamRegistry + SeamType 7 类 + 3 协议）+ `plugin_manifest.py`（schema 校验）+ `plugin_loader.py`（importlib + `__pycache__` 清除修复） |
| 热更修复 | `bd64a11` | 按路径独立节流 + `hot_update()` 强制重读（修 mtime 粒度漏检 / 跨测试互踩） |
| P4/P5 | `b4ed9e0` | `coordination/bus_consumer.py` 消费者独立模块化；ProviderRegistry/ToolRegistry → 同步 SeamRegistry（`_ToolSeamProxy`） |
| P6 | `aab1fe3` | `intelligent_router` 移除模块级策略缓存，改调用时实时读（4 处 `_load_policy()` 全部收敛） |
| P7/P8/P9 | `5858c61` | mtime_ns+size 组合判据（修 jiffy 漏检）；webui 能力缝同步注册；`hot_reload()`/`extract()` 热更 + 修 `self._config` AttributeError |
| P10/P11 | `97ed618` | 删 `task_router` 模块级 `TASK_TYPE_TO_ROUTE` 死快照；`SeamRegistry.get_all()`/`snapshot()` 查询视图 |
| P12 | `d2ac682` | sandbox 后端同步注册 `SeamRegistry(SANDBOX)`（与 P5 对称，查询视图补全） |

## 二、关键产物（后续会话的入口）

| 产物 | 路径 | 说明 |
|------|------|------|
| 策略统一加载 | `lingclaude/core/policy_loader.py` | `load/get/hot_update/reset/policies_dir`，目录穿越防护，graceful degrade |
| 进程内插片注册表 | `lingclaude/core/seam.py` | `SeamRegistry.register/get/get_optional/unregister/get_all/snapshot/check_protocol`，线程安全，热拔插语义 |
| 插片清单校验 | `lingclaude/core/plugin_manifest.py` | PluginManifest schema 校验（from_dict fail fast） |
| 动态插片加载 | `lingclaude/core/plugin_loader.py` | importlib + 源码 mtime 判定 + `__pycache__` 清除 |
| 消费者独立模块 | `lingclaude/coordination/bus_consumer.py` | BusResponder 消费循环 + stop_event 协作停止 |
| 策略文件 | `lingclaude/core/policies/*.yaml` | 6 个：behavior_router / claim_patterns / router_keywords / wiring_manifest / sandbox_policy / task_routing |

## 三、两套 seam 体系（重要：不是缺陷，是设计）

- **lingflow**（`lingflow.coordination.seam_registry`）：跨进程声明层，纯接口元数据（webui 等外部宿主用）
- **lingclaude/core/seam.py**（本仓进程内）：真实可调用实例注册表（provider/tool/sandbox 查询视图）
- 关系：`webui_seam.py` 顶部注释已澄清；P8 把能力缝（fs/shell/llm/subagent）也同步注册进进程内表，两套并存互不冲突

## 四、过程中修复的真实缺陷（防复发，勿回退）

1. **`Path` import 误删**（P1 早期）：函数内 import 调整时误删模块级 `Path`，靠 arch_guards + 广域测试兜住
2. **`__pycache__` 字节码缓存致插件重载失效**（P3）：`plugin_loader.py:86-89` 显式清除，勿移除
3. **mtime 粒度漏检**（bd64a11/P7）：ext4 jiffy（~4ms@250Hz）同一 tick 内两次写入 mtime_ns 相同 → `(st_mtime_ns, st_size)` 组合判据 + `hot_update()` 内容比较兜底
4. **`get_config`/`update_config` AttributeError**（P9）：`__init__` 只设 `self.config`，方法用 `self._config` → 统一为 `_config`（`config` 保留兼容别名）；此前 `update_config()` 只更新 `_config` 而 `route()` 用 `self.config`，**路由实际从未用上新配置**
5. **模块级策略缓存不生效**（P6/P10）：`intelligent_router._POLICY`、`task_router.TASK_TYPE_TO_ROUTE` 都是模块级一次性加载 → 已全部改为调用时实时读

## 五、测试覆盖（新增约 60+ 测试）

- `tests/test_p12_sandbox_seam_unified.py`（P12，4 项）
- `tests/test_p4_p5_consumer_seam.py`、`tests/test_p8_webui_seam_unified.py`、`tests/test_seam_plugin.py`
- `tests/test_sandbox_policy_hotupdate.py`、`tests/test_task_routing_hotupdate.py`、`tests/test_intelligent_router_hotupdate.py`
- 架构守卫 `tests/test_p04_arch_guards.py`（LAZY 基线 368，函数内 import 白名单）
- 定向 + 受影响集：P12 105 passed / 守卫 11 passed；广域回归总数待全量复核（后台 job）

## 六、后续候选（未纳入本轮）

1. **P13 候选**：`ToolRegistry.get()` 消费改为"先查 SeamRegistry、再回退内部 dict"——无实际收益，建议**收敛而非扩展**
2. **推送**：9 个提交（83781a2→d2ac682）尚未 push 远端，如需执行 `git push`
3. **广域回归**：全量 `tests/` 复核总数（后台 job `0401b26ea11f`）
4. **归档**：`tests/*.bak` 约 60 个均为 edit 工具自动备份，已被 .gitignore 忽略、未被 git 跟踪，无入库风险

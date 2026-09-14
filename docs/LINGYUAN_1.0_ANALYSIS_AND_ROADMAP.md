# 灵元 1.0 哲学对照 lingclaude —— 现状差距分析与重构路线图

> **来源**：灵元 V1.0 源文档 (`/home/ai/lingmate/灵元V1.0.md`) + 灵克全量代码扫描 (43,586 行) + 灵元 1.0 重构方案 (`docs/theory/LINGYUAN_1.0_REFACTOR.md`)
> **日期**：2026-09-13
> **性质**：以灵元 1.0 为唯一尺子，照 lingclaude 全架构，产出差距清单与可执行路线图

---

## 一、灵元 1.0 哲学核心（你的“尺子”）

| 维度 | 定义 |
|------|------|
| **本体** | 出入 = 空间（records 存在），流转 = 时间（transition 发生） |
| **极薄主干** | 仅 2T3A：`records/events` + `create/transition/query` |
| **方法论** | 1. 找不变 2. 砍到最薄 3. **变化全变插片** |
| **判据** | 去掉该功能还能跑？能 → 插片；不能 → 主干不够薄 |
| **替换分级** | `false`(默认不可换) / `cold`(重启) / `warm`(子进程) / `hot`(进程内动态) |
| **激进度** | 比 DSH Cordis 更激进：**主干 ≤ 8000 行、接缝在 seam.py 单表、治理/持久化/自优化全做插片** |

> **灵元一句话**：所有的一切，都是信息的出入导致状态之流转。主干只跑循环，插片决定行为。

---

## 二、以灵元 1.0 为尺子，照 lingclaude —— 不符合项清单（实证版）

| # | 违反项 | 灵元 1.0 要求 | lingclaude 现状 | 证据位置 |
|---|--------|-------------|----------------|----------|
| **1** | **主干倒装插片（最严重）** | 主干零 import 插片 | `core/` 直接 import `engine/` 7 处 + 20 处循环依赖风险 | `query_engine.py:26`, `tool_executor.py:13`, `tool_call_executor.py:77`, `mcp_tools.py:11/64/122` |
| **2** | **同一概念多处实现** | 一处实现，插片复用 | system_prompt 双实现、FileOps/FileEditTool 双路径、tool_router/mcp_tools 双过滤 | `system_prompt_builder.py` vs `message_builder.py`，`file_ops.py` vs `file_edit.py` |
| **3** | **死接线 / Mock / Stub** | 插片可替换 = 真实可换 | `hybrid_router/local_provider` 零调用、`governance_router` 调不通真引擎、`_loop_detector` hasattr 永真、`acp.py` 默认 8901 空转 | `model/__init__.py:25`, `governance/router.py`, `coding.py:770`, `engine/subagent/acp.py` |
| **4** | **跨仓硬依赖** | 跨仓 = 显式插片契约 | `self_optimizer` 直依 `lingminopt`、`webui_seam` 跨仓导 `lingflow` | `optimizer.py`, `webui_seam.py` |
| **5** | **主干过厚** | 仅 loops/policy/persist/surface 4 核心 | 权限/治理/优化/持久化全硬编码在 `coding.py` 等主干文件 | `coding.py:770` 典型 |
| **6** | **无统一注册表** | seam.py 单表 | 无全局注册表，插片靠 import 硬绑定 | 整个代码库无 `Seam.register/get` |
| **7** | **插片无自包含目录** | plugins/ 每类独立 | 能力散落在 `engine/` `core/` `model/` `governance/` `self_optimizer/` | 目录结构 |
| **8** | **热替换能力为零** | `replaceable: hot/warm/cold` | 仅 LACP manifest 有枚举，**运行时零加载器/隔离/状态迁移** | `lacp/manifest.py:69-80` |

---

## 三、lingclaude 的“插片”能否做到热重载、热拔插？

**现状：完全不能。只有“冷插片”文档雏形。**

| 能力 | 现状 | 缺口 |
|------|------|------|
| **冷插拔** (重启生效) | ❌ | 无 `unload` 清理、无依赖解析、无版本协商 |
| **温插拔** (子进程 swap) | ❌ | 无 subprocess 隔离方案、虽有 `mcp_proxy` 但未作插片运行时 |
| **热插拔** (进程内动态) | ❌ | 无 `importlib.reload` 安全机制、无模块版本共存、无状态序列化 |
| **依赖拓扑** | ❌ | `manifest.dependencies` 仅声明，无加载顺序/循环检测/冲突解决 |
| **回滚/灰度** | ❌ | 无 canary、无流量镜像、无一键回滚 |
| **插片间通信** | ❌ | 无插片总线、无契约校验、无熔断/限流 |

**根因**：`Replaceable.HOT/WARM/COLD` 只是枚举值，**运行时零代码支撑**。

---

## 四、重构后的插片热替换能力矩阵（按方案优化后）

| 级别 | 能力 | 实现路径 | 预估工时 | 交付节点 |
|------|------|----------|----------|----------|
| **Cold** (冷插拔) | 修改插片代码 → 重启进程生效 | `seam.py` 注册表 + `runtime.py` 装配入口 | **P0 即刻** (随 seam.py 落地) | ✅ v0.6.0 |
| **Warm** (温插拔) | 运行时 `unload` 旧插片 → `load` 新插片，**子进程级隔离**，无需重启主进程 | 复用 `mcp_proxy` 的 stdio/http transport，插片跑在独立子进程 | **P2 两周** (plugins/ 目录重组时同步做) | ✅ v0.7.0 |
| **Hot** (热插拔) | 进程内 `importlib.reload` + 状态迁移，**零停机、零状态丢失** | 模块隔离命名空间 + 状态序列化/反序列化钩子 + 版本共存 | **P3 一月+** (需专门攻关) | 🔄 v0.8.0+ |

### 关键基建（重构方案已铺路）

| 基建项 | 方案位置 | 作用 |
|--------|----------|------|
| **唯一注册表** | `core/seam.py` | `Seam.register/get/list` — 插片按 `SeamType` 挂载，主干只查注册表 |
| **插片自包含目录** | `lingclaude_plugins/{llm,tool,exec,subagent,persist,optimizer,governance,transport,lacp}/` | 每插片含 `manifest.yaml` + `install.py` + `handler.py` + `test_*.py` |
| **插片契约** | `lacp/manifest.py` 的 `Plugin` 类 | `replaceable: cold/warm/hot` + `dependencies` + `interface` + `signature` |
| **装配入口** | `core/runtime.py` | 启动时 `import plugins.xxx; install_xxx()`，**主干零 import 插片实现** |
| **工具注册解耦** | `engine/tools.py` 的 `ToolRegistry.register_handler(handler_name, fn)` | `ToolDefinition` 只存 `handler_name`，运行时按名解析，**同一 schema 可换 handler** |

### Warm 级（子进程隔离）— **P2 可落地，推荐主推**

```python
# plugins/tool/bash_native/__init__.py
from lingclaude_core.seam import Seam
from lingclaude.engine.mcp_proxy import register_server

class BashNativePlugin:
    def __init__(self):
        self._proc = None
    
    def install(self):
        # 走 MCP stdio transport — 子进程隔离，天然 warm 可替换
        register_server(
            key="tool:bash_native",
            name="bash_native",
            agent_id="lingclaude",
            tools=("bash", "bash_lingxi"),
            transport="stdio",
            command=["python", "-m", "lingclaude_plugins.tool.bash_native.server"],
        )
        Seam.register(Seam.TOOL, "bash_native", self)
    
    def unload(self):
        # 杀子进程、清注册表、清 handler 映射
        if self._proc: self._proc.terminate()
        Seam._registry[Seam.TOOL].pop("bash_native", None)
        # ToolRegistry 也要清 handler_name 映射

# 运行时热替换：
# loader.unload("bash_native")  # 杀旧子进程
# loader.load(new_manifest, new_code)  # 起新子进程，注册新 handler
# 主进程 ToolRegistry 只改 handler_name 映射，零停机
```

> **为什么 Warm 够用**：
> - `bash`/`ast_replace`/`git`/`lsp` 等高危/高延迟工具**本就该跑在隔离进程**
> - 复用现成 `mcp_proxy`（已支持 stdio/http 双 transport）
> - 状态在主进程（`ToolRegistry` 只存 schema），子进程无状态 → 替换极简

### Hot 级（进程内动态）— **有代价，按需做**

| 难点 | 解法 | 代价 |
|------|------|------|
| 模块版本共存 | `importlib.util.spec_from_loader` 创建隔离命名空间 `plugins.v2.bash_native` | 复杂度↑、内存↑ |
| 状态迁移 | 插片必须实现 `serialize_state()` / `deserialize_state(v)` 钩子 | **插片作者负担重** |
| 依赖图热更新 | 拓扑排序 + 循环检测 + 原子切换 | 需专门 `PluginLoader.hot_replace()` |
| 线程安全 | 替换瞬间加全局锁或 RCU | 延迟抖动 |

**建议**：**仅对高频、低延迟、无状态工具（`read`/`grep`/`glob`/`web_fetch`）做 Hot**，其余全走 Warm。

---

## 五、下一步优化方向（按 P0-P3 可执行路线）

### P0 即刻（今天能动，~200 行，零行为变化）
| 任务 | 文件 | 产出 |
|------|------|------|
| 1. 建立 `core/seam.py` 单注册表 | 新建 | 主干-插片唯一边界 |
| 2. 消除 7 处倒装 import 全下沉到 `runtime.py` | `query_engine.py` 等 7 文件 | **主干零 import 插片** |
| 3. 修 `coding.py:770` `_loop_detector` 必初始化 | `coding.py:52-76` | 5a/5b 熔断真生效 |
| 4. 合并 `message_builder` → `system_prompt_builder` facade | 两文件 | 消除重复实现 |

### P1 一周（死接线清零）
- `governance_router` 修真实调用 OR 标记 deprecated
- `subagent/acp.py` 注册前强制 `set_endpoint()` 未设报错
- `FileOps`/`FileEditTool` 合并，只保留后者
- 跨仓依赖显式化：`lingminopt`/`lingflow` 抽为显式插片契约

### P2 两周（主干真正变薄）
- 新结构落地：`lingclaude_core/` ≤ 8000 行（loops/policy/persist/surface + seam + provider_proto + trigger_proto + runtime）
- 插片重组：`lingclaude_plugins/` 目录，**每类独立、零耦合、可独立发版**
- 现有 43,586 行主包 → 主干 6000 行 + 插片 N 个目录

### P3 一月（全能力插片化）
- 7 类 trigger 各自成 provider（替代 `self_optimizer` 硬编码）
- 4 档 sandbox policy 抽到 `plugins/exec/policy/`
- capability_seam 补全（5 seam + 双签）
- 治理/持久化/自优化全成 seam 插片

---

## 六、按方案优化后：**绝对能做到“不动主干”加载卸载插片**

| 场景 | 方案 | 主干改动 |
|------|------|----------|
| 新增工具 | 写插片目录 + `manifest.yaml` + `install.py` | **0 行** |
| 修复工具 bug | 改插片代码 → `loader.reload("tool:bash_native")` | **0 行** |
| 替换 LLM Provider | 新插片注册同 `SeamType.LLM` 不同 name | **0 行** |
| 升级沙箱策略 | 新 `sandbox_policy` 插片挂 `Seam.EXEC_SANDBOX` | **0 行** |
| 切换治理算法 | 新 `governance_v3` 插片挂 `Seam.GOV_ALGO` | **0 行** |

**核心保证**：`core/seam.py` + `core/runtime.py` + `core/loops.py` + `core/policy.py` + `core/persist.py` + `core/surface.py` + `core/provider_proto.py` + `core/trigger_proto.py` — **这 8 个文件 ≤ 8000 行，永远不因插片增减而改动**。

---

## 七、插片质量门禁（灵元测试哲学落地）

每个插片**必须**自带：

```
plugins/tool/bash_native/
├── manifest.yaml          # LACP manifest (replaceable: warm)
├── install.py             # Seam.register 入口
├── handler.py             # 真实实现
├── test_bash_native.py    # 插片测试层（灵元 §4.2 第 2 层）
└── contract_test.py       # 从 manifest.interface 自动生成契约测试
```

> **灵元 1.0 尺子**：插片无测试 = 非法插片。`PluginLoader.load()` 应在注册前跑 `pytest test_<plugin>.py -q` 绿才允许挂载。

---

## 八、本周可落地的第一步行动

```bash
# 1. 创建 seam.py（主干唯一注册表）
cat > lingclaude/core/seam.py << 'EOF'
class Seam:
    _registry: dict[str, dict[str, Any]] = {}
    @classmethod
    def register(cls, seam_type: str, name: str, impl: Any) -> None:
        cls._registry.setdefault(seam_type, {})[name] = impl
    @classmethod
    def get(cls, seam_type: str, name: str) -> Any:
        return cls._registry.get(seam_type, {}).get(name)
    @classmethod
    def list(cls, seam_type: str) -> list[str]:
        return list(cls._registry.get(seam_type, {}).keys())
EOF

# 2. 在 runtime.py 统一装配（示例）
from lingclaude_plugins.llm.openai_compatible import install as install_openai
from lingclaude_plugins.tool.bash_native import install as install_bash
install_openai(); install_bash()
# 主干从此不知有 OpenAI/原生 bash，只知 Seam.get()
```

---

## 九、一句话总结

> **灵元 1.0 的本质 = 主干只跑循环，插片决定行为**。当前 lingclaude 把“模型循环 + 权限 + 持久化 + 治理 + 优化”打成厚主干——**按上述 P0-P3 路线 6-12 月可达成：主干 8000 行、插片按 plugins/ 目录组织、每个 seam 类型可独立发版、原有功能零回归**。
>
> **P0 三项（seam + 倒装消除 + `_loop_detector`）今天就能动**——3 个文件、~200 行代码、零行为变化、纯结构整理。
>
> **按 `LINGYUAN_1.0_REFACTOR.md` 路线图走完 P2，Warm 级热插拔随架构天然到位**；Hot 级再投一个月专项攻关。**主干从此只管循环，插片想怎么换怎么换。**
---

## 十、执行记录（2026-09-14：P1-P12 全部完成）

> 本节是路线图落地记录（按上文 P0-P3 差距清单收敛而来，P1-P12 为本仓具体拆解）。
> 日期：2026-09-14。全部提交均过 lefthook（audit-record / linggit-audit / workspace-snapshot），工作树干净。

### 阶段一：策略统一 + 外置（P1-P2）

| 项 | 内容 | 提交 |
|----|------|------|
| P1-1/P1-2 | 新建 `core/policy_loader.py`（统一 YAML 加载 + mtime watch 热更 + 目录穿越防护 + graceful degrade）；补 `hot_update()` 强制重读 | `83781a2` |
| P2-1/P2-2 | sandbox 网络白名单 + 默认可写目录外置 `policies/sandbox_policy.yaml`；`TASK_TYPE_TO_ROUTE` 外置 `policies/task_routing.yaml` | `ef561dd` |

### 阶段二：进程内插片注册表（P3）

- `core/seam.py`：`SeamRegistry`（register/unregister/get/get_optional/热拔插）+ `SeamType` 7 类 + `ProviderPlugin`/`ToolPlugin`/`SandboxPlugin` 协议
- `core/plugin_manifest.py`：PluginManifest schema 校验（from_dict fail fast）
- `core/plugin_loader.py`：importlib 动态加载 + **`__pycache__` 字节码清除修复**（插件重载失效根因）
- 提交：`03dfb76`

### 阶段三：消费者拆分 + 接入真实注册表（P4-P5）

| 项 | 内容 | 提交 |
|----|------|------|
| P4 | 新建 `coordination/bus_consumer.py`（BusResponder 消费循环独立模块化，引擎进程也可消费 LingBus 任务）；`api.py` 接入消费者 | `b4ed9e0` |
| P5 | `ProviderRegistry.register/reset` → 同步 SeamRegistry(PROVIDER)；`ToolRegistry.register/unregister/reset` → 同步 SeamRegistry(TOOL)（`_ToolSeamProxy` 满足 ToolPlugin 协议） | `b4ed9e0` |

### 阶段四：热更可靠性 + 消费面（P6-P9）

| 项 | 内容 | 提交 |
|----|------|------|
| P6 | `intelligent_router` 移除模块级一次性 `_POLICY` 缓存，改调用时 `_load_policy()` 实时读（4 处 `_load_policy()` 全部收敛） | `aab1fe3` |
| P7 | 策略检测粒度 `st_mtime_ns` + `st_size` 组合判据（修 ext4 jiffy 粒度漏检）；`hot_update()` 内容比较兜底 | `5858c61` |
| P8 | webui 能力缝（fs/shell/llm/subagent）同步注册进程内 SeamRegistry；澄清两套 seam 体系（lingflow=跨进程声明层 / core/seam.py=进程内插片表） | `5858c61` |
| P9 | `BehaviorRoutingConfig.hot_reload()` + `DeclarationExtractor.extract()` 热更；**修 `get_config`/`update_config` 的 `self._config` AttributeError 真实缺陷**（路由此前从未用上新配置） | `5858c61` |

### 阶段五：死快照清除 + 查询视图补全（P10-P12）

| 项 | 内容 | 提交 |
|----|------|------|
| P10 | `task_router` 删除模块级 `TASK_TYPE_TO_ROUTE` 死快照（`resolve()` 早已实时读，死快照零生产引用） | `97ed618` |
| P11 | `SeamRegistry.get_all()`（副本）+ `snapshot()`（全类型热拔插状态只读观测） | `97ed618` |
| P12 | sandbox 后端同步注册 `SeamRegistry(SANDBOX)`（与 P5 provider/tool 对称），统一查询视图补全 | `d2ac682` |

### 关键质量指标

- 全链路提交：`83781a2` → `ef561dd` → `03dfb76` → `bd64a11` → `b4ed9e0` → `aab1fe3` → `5858c61` → `97ed618` → `d2ac682`（9 提交，全部 0 issues）
- 过程中发现并修复 5 个真实缺陷：`Path` import 误删、`__pycache__` 缓存致插件重载失效、mtime 粒度漏检（jiffy 同 tick 漏写）、`update_config`/`get_config` AttributeError、模块级策略缓存不生效
- `policies/` 已有 6 个 YAML（behavior_router / claim_patterns / router_keywords / wiring_manifest / sandbox_policy / task_routing），全部走 PolicyLoader 统一热更
- 定向 + 受影响集累计：P12 105 passed / 守卫 11 passed；广域回归总数待全量复核

### 后续候选（未纳入本轮）

- **P13 候选**：`ToolRegistry.get()` 消费改为"先查 SeamRegistry、再回退内部 dict"——目前无实际收益，建议**收敛而非扩展**
- **推送**：9 个提交尚未 push 远端，如需可 `git push`
- **广域回归**：全量 `tests/` 复核 247+ 总数

---

## 十一、执行记录（2026-09-14：S1-S6 + P14-P17 深度优化）

> 依据四家审计（codex/atomcode/opencode/灵克）收敛诊断，本轮按**灵元三步法**
> （先砍到最薄 → 再接缝消费 → 后活）落地。全部提交过 lefthook 三钩子，工作树干净。

### 阶段六：砍薄 + 接缝消费 + 幻觉治理（S1-S6）

| 项 | 内容 | 提交 |
|----|------|------|
| S1 | 清理 91 个工作区 `.bak` + git rm 误入库备份（备份归零纪律，git 即备份） | `86489ef` |
| S2 | **SeamRegistry 消费面打通**（热拔插真正生效）：bash.py `get_optional(SANDBOX,'default')` 优先、sandbox_provider 注册 default 别名、ProviderRegistry 已注册实例优先 | `dd2fd26` |
| S3 | 主干倒装清零：mcp_tools 模块级 import 改函数内延迟（G1/G3 守卫同步） | `dd2fd26` |
| S4 | **PluginLoader 从库变机制**：`load_plugins_from_dir` + wiring.assemble 生产调用点 | `dd2fd26` |
| S5 | 幻觉治理 **Cross-reference claims**（v1 文档 P0-1）：Claim 增加 source_span/text + 证据存在性检查 | `dd2fd26` |
| S6 | 方案文档（可行性方案 + 行动计划 + 验收尺度） | `dd2fd26` |

### 阶段七：状态收敛 + 单源化 + 幻觉治理增强（P14-P17）

| 项 | 内容 | 提交 |
|----|------|------|
| P14 | `dementia_detector.CognitiveState` 改名 `DegradationLevel`（与 governance S0-S6 `CognitiveState` **去混淆**——不同维度不同 type），保留兼容别名 | `625985c` |
| P15 | **Mixin↔SPECS 装配完整性契约**：SPECS 每个 handler_attr 可解析 + 插片无反向依赖 coding.py + 插片文件 ≤200 行防回潮（实证：10 Mixin 早已拆至 `engine/tool_handlers/`，审计"10 Mixin 焊死"过时） | `625985c` |
| P16 | **L10-A 单源契约**：`claim_patterns.yaml` 权威 + `_FALLBACK_PATTERNS` 降级镜像防漂移；Provider 双写实证为**分层架构**（构造器层+实例层），非冗余副本 | `625985c` |
| P17 | **PriorVerifier Cross-reference claims**：声明类型↔工具证据语义映射（commit_claim↔git_push 等），`_finalize_turn` 从 journal 取真实工具名传入（fail-soft）；**修 hard_unverified 分支未排除有据声明的缺陷** | `625985c` |

### 关键质量指标（本轮追加）

- 提交：`86489ef` → `dd2fd26` → `625985c`（3 提交，全部 0 issues）
- 新增测试：S1-S6 17 项 + P14-P17 13 项 = **30 项**
- 受影响集回归：**267 passed, 1 skipped**（EXIT=0）；arch guards 7 passed（LAZY 基线未突破）
- SeamRegistry.get 生产消费点：**0 → 3**（bash 沙箱 / provider / wiring 插件加载）——热拔插从"可观测"变"可生效"
- `.bak` 归零纪律保持：本轮 edit 产生的 14 个 .bak 已清理，无入库

### 后续候选（未纳入本轮）

- **P18 候选**：`coding.py._setup_tools()` 42 行硬编码装配改走 wiring.assemble 或 seam 查询（高杠杆但需 CLI 回归专项）
- **P19 候选**：`PriorVerifier` 的 `_TOOL_ACTION_EVIDENCE_MAP` 随工具名演化维护（当前前缀匹配已容错）
- **推送**：`83781a2` → `625985c` 共 13 个提交尚未 push 远端
- **广域回归**：全量 `tests/` 复核（后台进行中）

## 十二、执行记录（2026-09-14：Q1/Q2/Q3 —— 灵元尺子再照后补齐消费面）

> 五家审计（cc/codex/opencode/atomcode）共识：策略层已达标、倒装已清零、.bak 归零，
> 未达标集中在「主干消费面」（Tool 槽位 0 消费）、「状态收敛」（3×TaskStatus 同名）、
> 「系统提示被治理标记自伤」。本轮按灵元三步法（先砍薄 → 再接缝消费 → 后活）补齐。

### Q1：Tool 热路径走 seam（热拔插从登记处变消费面）

- **改动**：`engine/tools.py` `ToolRegistry.execute` 优先查 `SeamRegistry.get_optional(TOOL, name)`；
  自注册 `_ToolSeamProxy`（指向本 registry）跳过防死循环；miss 回退内部 handler（graceful degrade）
- **效果**：`SeamRegistry.register(TOOL, "bash", new_proxy)` 后，`execute("bash")` 立即用新实例——
  **热拔插 tool 真正生效**（此前只有 register 无 get 消费，是影子表）
- **陷阱修复**：`_ToolSeamProxy.execute` 委托回 registry → 若 execute 直接走自注册影子会无限递归；
  通过 `isinstance(proxy, _ToolSeamProxy) and proxy._registry is self` 识别并跳过

### Q2：3×TaskStatus 同名歧义归零（不同维度不同 type）

- **背景**：task_aggregation（英文枚举）/ task_scheduler（中文枚举）/ handover（交接检查点）
  各自定义同名 `TaskStatus`，值域完全不同——「两状态机必然不一致」的温床
- **改动**：分别改名 `AggregationTaskStatus` / `SchedulerTaskStatus` / `HandoverTaskStatus`，
  保留 `TaskStatus = 新名` 兼容别名（外部引用不断）；`grep 'class TaskStatus'` = 0
- **依据**：灵元「不同维度不同 type」——同名歧义应消除，而非强行合并不同语义域

### Q3：系统提示渲染净化（修复 5ff2ba1 提交消息自伤）

- **背景**：`5ff2ba1` 提交消息含 `⚠[工具结果未验证]`，system_prompt_builder 用 `git show -s`
  注入最近提交到 SESSION_CONTEXT → test_adaptive「健康状态无 ⚠」断言失败
- **改动**：`core/system_prompt_builder.py` 渲染侧过滤治理标记（⚠/💡 → !），
  **不篡改 git 历史**，只净化系统提示渲染
- **效果**：提交消息是事实（保留），系统提示上下文去除干扰符号（健康状态无 ⚠）

### 质量指标（本轮）

- 提交：`291d1a0`（8 文件，+257/-6，lefthook 3 钩子全过，灵督 0 issues）
- 新增契约测试：**10 项**（Q1 4 + Q2 3 + Q3 3）
- 定向回归：**106 passed**（Q1/Q2/Q3 + 守卫 + adaptive + message_builder + prior_verifier + seam）
- `.bak` 归零：本轮 edit 产生的 4 个 .bak 已清理

### 后续候选

- **Q4**：`query_engine.py` 814 行厚模块瘦身（循环/工具/提交三职责按 wiring 装配模式继续拆）
- **Q5**：`coding.py._setup_tools()` 42 行硬编码装配改走 wiring.assemble（P18 方向，需 CLI 回归专项）
- **推送**：`83781a2` → `291d1a0` 共 14 个提交尚未 push 远端

# 灵元 1.0 重构方案（2026-09-09）

> 来源：本会话 4 个分层 Plan agent 实证 + lingclaude 全源码扫描（43,586 行）+ 用户原话
> "减薄不变的是主干，所有变的是插片。甚至比 DSH 还要激进。"
> 关联：docs/theory/LINGCLAUDE_VS_PEERS.md · docs/theory/SYSTEMS_THEORY_SYNTHESIS.md

---

## 一、哲学命题（用户原话 + 实证解释）

| 命题 | 实证含义 |
|---|---|
| "减薄不变的是主干" | 主干 = 核心循环（model call / tool exec / session 持久化 / permission 决策）——**不依赖任何具体 provider / tool / sandbox / LACP** |
| "所有变的是插片" | provider / tool / sandbox / LACP transport / storage backend / optimizer trigger / memory backend / 治理算法——**全部可替换**，且**替换时主干零修改** |
| "比 DSH 还要激进" | DSH 是 Cordis 50 个独立包（每个能力一个包 + Boot.context 组合）；lingclaude 比它激进 = 主干更薄、接缝更标准、插片接口更严格 |

## 二、当前架构违背灵元 1.0 的实证（H17 校正纪律）

### 2.1 主干倒装插片（最严重违反）

`lingclaude/core/` 直接 import `lingclaude/engine/` — **主干依赖插片实现**，插片换了主干得改：

```
lingclaude/core/query_engine.py:26    → engine.tool_router (ToolRouter 工厂)
lingclaude/core/tool_executor.py:13    → engine.mcp_proxy
lingclaude/core/tool_call_executor.py:77 → engine.verification_gate
lingclaude/core/mcp_tools.py:11,64,122 → engine.tool_router + engine.tools + engine.mcp_client + engine.mcp_proxy
```

合计 **7 处倒装 + 20 处循环依赖风险**（engine → core）。

### 2.2 同一概念多处实现

| 概念 | 多处实现 | 后果 |
|---|---|---|
| **system prompt 装配** | `system_prompt_builder.py` + `message_builder.py` 各自独立 extras.append 链 | 行为/经验注入改一处必漏一处 |
| **tool read/write/edit** | `FileOps` (file_ops.py) + `FileEditTool` (file_edit.py) | 双重路径，read 行为可能不一致 |
| **tool router 过滤** | `tool_router.py` (类别打分) + `mcp_tools.py` (工具注册) | 过滤规则散在两边 |

### 2.3 死接线 / Mock / Stub（前面报告的硬债务）

| 项 | 实证 |
|---|---|
| `model/hybrid_router.py` + `local_provider.py` | `__init__.py` 导出但零调用 |
| `governance/router.py` | 调 `engine.propose/vote/resolve`，v2 实际 API 是 `create_proposal/raise_objection/check_deadlines` —— router 调不通真实引擎 |
| `coding.py:770` `_loop_detector hasattr 永真` | `__init__` 未初始化，`observe_denial` 永不触发（5a/5b 接线缺陷） |
| `engine/subagent/acp.py` | 端口 8901 默认未跑，acp 后端空转 |
| `_apply_params` `lock_cm = None` 提前 return | `finally` 的 `if lock_cm is not None` 永不触发（锁释放路径有漏洞） |

### 2.4 跨仓硬依赖

- `self_optimizer/optimizer.py` 直接 `from lingminopt import ...` —— 灵极优宕则 optimizer 崩
- `webui_seam.py` 从 `lingflow/lingflow/coordination/seam_registry.py` 跨仓导入

---

## 三、灵元 1.0 重构目标

### 3.1 主干新结构（目标 ≤ 8000 行 Python）

```
lingclaude_core/  （≤ 30 个 .py，约 6000 行）
├── loops.py              ← ModelLoop（model call + tool exec + finalize 唯一编排）
├── policy.py            ← PermissionGate（deny/approve/record 唯一决策点）
├── persist.py           ← SessionStore + Journal（append + checkpoint）
├── surface.py           ← ToolRegistry + ToolRouter（注册表 + 路由，但调用由 loops 驱动）
├── provider_proto.py    ← LLMProvider ABC（4 接口）+ Message/Config 数据契约
├── seam.py              ← 唯一的 Provider/Backend 注册点（替代倒装 import）
├── trigger_proto.py     ← Trigger ABC（替代 self_optimizer 的 7 类硬编码）
└── runtime.py           ← 入口：装配所有插片为可执行 engine（不持有任何插片代码）
```

### 3.2 插片新结构（每个都自包含，零主干依赖）

```
lingclaude_plugins/  （每类独立目录，零耦合）
├── llm/
│   ├── openai_compatible/   ← Provider 实现（urllib 直连 + 流式 + 多模态）
│   ├── anthropic/          ← Provider 实现
│   ├── llm_proxy/          ← 独立 :8900 四边界服务（已存在，迁过来）
│   └── intelligent_router/ ← GLM 二级路由（已存在，迁过来）
├── tool/
│   ├── bash_native/        ← BashExecutor + 词边界黑名单 + 4 档 sandbox
│   ├── bash_lingxi/        ← BashlingxiExecutor（独立 MCP 客户端）
│   ├── mcp_stdio/          ← MCP stdio
│   ├── mcp_http/           ← MCP http
│   ├── mcp_oauth/          ← OAuth
│   ├── tool_router_keyword/ ← 类别打分
│   └── codeintel/          ← call graph（已存在）
├── exec/
│   ├── sandbox_bwrap/      ← bwrap 实现
│   ├── sandbox_noop/       ← noop 实现
│   └── sandbox_policy/     ← 4 档策略
├── subagent/
│   ├── inprocess/          ← 复用旧 SubAgent
│   ├── acp/                ← HTTP JSON 客户端（端点可配）
│   └── manager/            ← 注册表 + depth_limit
├── persistance/
│   ├── journal_append_only/ ← SessionJournal（已存在，迁过来）
│   ├── session_json/       ← JSON 序列化
│   └── session_yaml/       ← YAML 序列化
├── optimizer/
│   ├── trigger_xxx/        ← 7 类 trigger 各成独立 provider
│   ├── search_lingminopt/  ← 贝叶斯搜索
│   └── rule_extractor/     ← 规则学习
├── governance/
│   ├── proposal_lifecycle/ ← 8 状态机
│   ├── governance_v2/       ← 异议制
│   └── cognitive_state/    ← S0-S6
├── transport/
│   ├── webui_seam/         ← 现状（修 seam_registry 跨仓）
│   ├── bus_lingbus/        ← 多 agent 总线
│   └── http_api/           ← FastAPI :8700
└── lacp/                  ← 现状插件化（已部分对位）
```

### 3.3 接缝（核心：唯一主干-插片边界）

```python
# lingclaude_core/seam.py  ——  全局唯一注册表（替代倒装 import）
class Seam:
    """注册表：插片按 SeamType 注册，主干按 SeamType 查询。

    设计原则：主干从不 import 插片层;插片按 SeamType 挂到 Seam。
    """
    TYPE_LLM = "llm_provider"
    TOOL = "tool"
    EXEC_SANDBOX = "exec_sandbox"
    SUBAGENT = "subagent"
    PERSIST = "persistence"
    OPT_TRIGGER = "optimizer_trigger"
    GOV_ALGO = "governance"
    TRANSPORT = "transport"

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


# lingclaude_core/loops.py  ——  主干不 import 任何具体 provider
class ModelLoop:
    def __init__(self, provider: LLMProvider):
        # provider 只接 ABC,不接具体实现
        self._provider = provider

    async def step(self, messages, tools):
        return await self._provider.complete(messages, tools=tools)


# lingclaude_plugins/llm/openai_compatible/__init__.py  ——  插片向 Seam 注册
from lingclaude_core.seam import Seam

class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, api_key, base_url, model):
        ...

def install() -> None:
    Seam.register(Seam.TYPE_LLM, "openai_compatible", OpenAICompatibleProvider(...))

# lingclaude_runtime.py  ——  装配入口
from lingclaude_plugins.llm.openai_compatible import install as install_openai
from lingclaude_plugins.llm.anthropic import install as install_anthropic
install_openai()
install_anthropic()

# 现在 loops.py 不知道有 OpenAI,但能 Seam.get(Seam.TYPE_LLM, 'openai') 拿到
```

---

## 四、当前技债归入灵元 1.0（每条都对应一个"插片"）

| 现有技债 | 灵元 1.0 归位 |
|---|---|
| `model/hybrid_router.py` 死接线 | 抽到 `plugins/llm/hybrid_router/` — 既然没人调，作为 plug-in starter 留下 |
| `model/local_provider.py` 死接线 | 抽到 `plugins/llm/local_provider/`（占位，但 seam 形式合规） |
| `LocalModelProvider` 路径硬编码 | 同上，运行时尝试加载，失败走回退（但通过 seam 报错而非静默回退） |
| `governance_router.py` 调不通真实引擎 | 抽到 `plugins/governance/router/` — 修 API 命名 OR 标记 deprecated |
| `coding.py:770` `_loop_detector` hasattr 永真 | 修主干：`__init__` 必初始化 `self._loop_detector = _ToolLoopDetector()` |
| `engine/subagent/acp.py` 默认 endpoint 无服务 | 抽到 `plugins/subagent/acp/` — 注册前必须显式 set_endpoint()，未设时报错而非空转 |
| `_apply_params` lock_cm 锁释放漏洞 | 抽到 `plugins/optimizer/daemon/` + 修主干的 apply 流程用 `with lock_ctx` 而非变量传递 |
| `message_builder.py` 重复 `system_prompt_builder.py` | **合并** —— 只保留 `system_prompt_builder.py`，`message_builder` 改 facade 委托 |
| `FileOps` vs `FileEditTool` 重复 | 抽到 `plugins/tool/file_ops/` — 只保留 `FileEditTool`，`FileOps` 标记 deprecated |
| `KGFactChecker._mock_search` 固定返回 found=True | 抽到 `plugins/audit/`，mock 走 fixture 而非硬编码 |
| `l10_a_post_audit.py` L10-B mock | 同上 |
| `self_optimizer/optimizer.py` 跨仓依赖 lingminopt | 抽到 `plugins/optimizer/search_lingminopt/` — 跨仓依赖明确化为插片契约 |
| `webui_seam.py` 跨仓依赖 lingflow/lingflow | 抽到 `plugins/transport/webui/` — seam_registry 也复制为本地接口 |
| `query_engine.py` import `engine.tool_router` 等 7 处 | **零倒装**：主干只剩 seam.py；其余 7 个 import 全部下沉到 runtime.py |
| `daemon.py` `AGENT_MAX_TOOL_ROUNDS = 10` 硬编码 | 抽到 `plugins/optimizer/triggers/` —— 各 trigger 自带配置 |

---

## 五、优化路线（按 ROI × 风险排序）

### 5.1 优先级路线图

| 阶段 | 项 | 工作量 | 风险 | 收益 |
|---|---|---|---|---|
| **P0 即刻** | `core/seam.py` 注册表建立 + 把 7 个倒装 import 全下沉到 runtime | 200 行 | 低（主干加 1 文件 + 改 import 路径） | **主干-插片边界清晰**，未来所有插片改动零主干改动 |
| **P0 即刻** | `_loop_detector` `__init__` 必初始化（修 coding.py:770 hasattr 永真） | 5 行 | 极低 | 5a/5b 真生效 |
| **P0 即刻** | 合并 `message_builder` → `system_prompt_builder` facade | 100 行 | 中（两套相似代码需统一测试） | 消除重复实现 |
| **P1 一周** | `FileOps` / `FileEditTool` 重复合并 | 80 行 | 中 | 读/写/编辑行为一致 |
| **P1 一周** | 抽取 `governance/router.py` 真实调用 OR 删除 | 40 行 | 低 | 死接线清零 |
| **P1 一周** | `subagent/acp.py` 注册前必须显式 set_endpoint | 30 行 | 低 | acp 后端空转 → 显式报错 |
| **P2 两周** | 主干新结构（8000 行）实施 | 2000 行 | 高（破坏性） | **主干真的"薄"** |
| **P2 两周** | 插片按 plugins/ 目录重组 | 1500 行 | 高 | **每个插片可独立发版** |
| **P2 两周** | 跨仓依赖显式化（lingminopt / lingflow） | 100 行 | 中 | 跨仓接口稳定可治理 |
| **P3 一月** | 7 类 trigger 各自成 provider（替代 self_optimizer 硬编码） | 500 行 | 中 | trigger 可拔插 |
| **P3 一月** | 4 档 sandbox policy 抽到 `plugins/exec/policy/` | 200 行 | 低 | policy 与执行解耦 |
| **P3 一月** | capability_seam 补全（5 seam + 双签） | 300 行 | 中 | DSH-style 插件化局部引入 |

### 5.2 实施顺序（增量可发布）

```
v0.6.0  P0: seam.py + 7 倒装消除 + _loop_detector 修 + message_builder 合并
        回归测试不破 — 主干只增加 1 文件 + 改 import 路径
v0.6.1  P1: 死接线清理（router/acp/FileOps）+ 跨仓依赖显式化
        API 仍兼容，只在 SeamRegistry 暴露新增 seam 即可
v0.7.0  P2: 主干拆 + 插片重组
        这是破坏性 — 需要版本分支 + 完整迁移测试
        先冻结 0.5.x 分支
v0.8.0  P3: 7 类 trigger / 4 档 sandbox 抽 provider
        完全 optional，存量自_optimizer 仍工作
```

---

## 六、灵元 1.0 vs DSH Cordis：什么更激进

| 维度 | DSH Cordis | 灵元 1.0（目标态） |
|---|---|---|
| 插件化范围 | 50 个独立包 + Boot.context 组合 | **不只 50 个，是 N 个**，且接缝标准（seam.py） |
| 主干大小 | DSH shell + cordis framework = ~10K TS | **目标 ≤ 8000 行 Python**（含 seam + loops + policy + persist + surface） |
| 主干 vs 插片边界 | DSH 是框架包 + 插件包（包级接缝） | **接缝在 seam.py 单一注册表**（更细粒度，单能力可替换） |
| 插片抽象 | 每个 Cordis 包自带 service interface | **SeamType 枚举**（6 类）+ `Seam.register/get/list` 三方法 |
| 主干抽象机制 | TypeScript 装饰器 + reflect-metadata | **Python Protocol + ABC**（types.py 提供协议骨架） |
| 治理位置 | Cordis 自身的"plugin lifecycle" | **governance 作为插片**（proposal_lifecycle / governance_v2 / cognitive_state 都成 seam） |
| 自我优化位置 | DSH 无 | **自优化也是插片**（optimizer/trigger_xxx / search_lingminopt / rule_extractor） |

**激进点**：DSH 把"能力"作为包拆；灵元 1.0 把"能力、治理、持久化、自我优化"**全部作为接缝可替换**——主干的"模型循环 + 权限决策 + 持久化"三点是不变的，其他都可换。

---

## 七、H17 闭环自查

**本方案的局限性**：
- 上面所有优化项都是"实证驱动 + grep 出文件:行号"——但**没有量化收益**（不能保证插件化后跑分比现在快）
- P2 主干重构是高风险操作，**可能破坏现有测试**——必须完整迁移测试 + 灰度回滚
- seam.py 的设计**借鉴 DSH seam_registry**——但跨仓依赖仍在（lingminopt / lingflow 仍是 lingclaude 外的仓）

**未来需要做的实证**：
1. **重构前后 benchmark 对比**：跑分、内存、token 消耗都要量化（**不要"应该会好"**）
2. **测试覆盖完整性**：重构前达到 ≥95% 覆盖率（P1 阶段目标）
3. **灰度方案**：先在新功能用 seam，旧代码保留 `import` 别名做向后兼容（不要 break API）

---

## 八、一句话总结

灵元 1.0 的本质 = **主干只跑循环，插片决定行为**。当前 lingclaude 的循环被打成"模型循环 + 权限 + 持久化 + 治理 + 优化"的厚主干——**主干该有的权限逻辑被插片硬编码污染**（coding.py:770 是典型）。

按本路线图 6-12 月能完成 P0+P1+P2——**届时主干 8000 行、插片按 plugins/ 目录组织、每个 seam 类型可独立发版、跨仓依赖显式化、原有功能零回归**。

P0 三项（seam + 倒装消除 + `_loop_detector`）**今天就能动**——3 个文件、~200 行代码、零行为变化、纯结构整理。

要我现在动 P0 吗？

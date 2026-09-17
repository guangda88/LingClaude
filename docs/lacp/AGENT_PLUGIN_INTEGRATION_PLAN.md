# 灵族 Agent 插片化集成方案（第一层：12 子 + 8 对外工程 → lc AGENT 缝）

> **状态**：可行性方案 v1.0（2026-09-17 用户裁定：第一层先实现，每行代码遵守铁律与守卫）
> **目标**：将 20 个实体（12 子成员 + 8 对外工程项目）集成为 lingclaude 的
> SeamType.AGENT 插片——可随时调用、可拔插、可审计、可记账。
> **依据**：铁律 1-4（本体）+ J1-J5（判据）+ M1-M5（守卫）+ 候选铁律 6（信任等级）
> + 候选铁律 5（双向互认）+ 候选铁律 8（可隔离故障域）。
> **现状实测**（2026-09-17，方案的事实底座）：
> - `core/seam.py:45`：SeamType.AGENT 缝已存在（注释即"外部 Agent 插片，对标 Ekko Studio 挂载"）；
> - `core/seam.py:102-126`：AgentSeam/CrewSeam 协议已定义（run/abort/status + create_crew/dispatch）；
> - `core/seam.py:138`：SeamType.AGENT→AgentSeam 映射已注册；
> - `engine/subagent/mcp.py:67`：MCPSubagentBackend（stdio/http 双传输）已实装；
> - `core/seam.py:56-79`：Provider 协议（create/execute/available/wrap）已实装。
> **结论**：集成面 100% 就绪，缺的只是 20 个插片本体 + 逐个入册。本方案全部是
> "填插片"，不改主干——这正是铁律 1 的用武之地。

---

## 一、集成架构：一套协议，两种传输，20 个插片

### 1.1 统一接缝模型

```
lc 主干（零 diff）
  └─ SeamType.AGENT（AgentSeam 协议：run/abort/status）
      ├─ MCP 传输（MCPSubagentBackend，stdio/http）   ← 12 子成员走这条（各有 MCP/CLI）
      └─ 直调传输（SubagentBackend 本地协议）          ← 8 对外工程走这条（多为脚本/服务）
```

**关键设计**：每个成员/项目 = 一个 `AgentPlugin` 插片 = **一个 Python 包 +
一个 manifest**。插片不关心对方内部是 CLI、HTTP 服务还是 MCP server——它只实现
AgentSeam 协议的三个动作（run/abort/status），传输细节封装在插片内。

### 1.2 20 个实体的分类与传输选型

| 组 | 成员 | 传输 | 信任等级（候选铁律 6） | 拔插等级 |
|----|------|------|----------------------|---------|
| 12 子·任务成员（8） | lingflow/lingresearch/lingzhi/lingminopt/lingweb/lingyang/lingcreate/lingtongask | MCP stdio/http | **T2 契约审计**（各自仓库，lc 无修复权） | L1 替换 |
| 12 子·基础设施（3） | lingmessage(LingBus)/lingxi(MCP终端)/zhibridge(网关) | MCP stdio 或直调 | **T1 全审计**（协议即灵元自身生态，但代码不在 lc 仓） | L1 替换 |
| 12 子·安全（1） | lingan | 直调（安全门须低延迟） | **T1 全审计** | L2 缺席降级（缺席=安全降级策略，禁跳过） |
| 8 对外工程 | 灵康/灵律/灵声/灵视/灵触/灵戴/灵商/灵依 | 直调（多为独立应用） | **T3 只观测**（外部黑盒，只记健康探针） | L3 缺席裸奔 |

**信任等级依据**（候选铁律 6 三级）：12 子代码在灵族仓（可读可审计其履约）→ T2；
LingBus/lingxi/zhibridge 是灵族基础设施但不在 lc 仓 → T1 降为 T2 亦可，取 T1 因协议
同源；lingan 安全门必须可全审计（安全边界不可黑盒）→ T1；8 对外工程是应用黑盒 → T3。

---

## 二、插片协议 schema（每个插片必含的铁律要素）

每个 AgentPlugin 的 manifest（`agent_<name>/manifest.agent.json`）必须包含：

```json
{
  "name": "agent_lingflow",
  "target": "lingflow",
  "version": "1.0.0",
  "trust_level": "T2",
  "plug_level": "L1",
  "transport": {"kind": "mcp", "mode": "stdio", "command": ["python3", "-m", "lingflow_mcp"]},
  "stop_layer": {
    "kernel": "MCP stdio → lingflow 工作流引擎",
    "seams": ["model_provider"],
    "implementations": 1
  },
  "health_probe": {"interval_s": 300, "timeout_s": 5},
  "state_record": "agent_run:<name>:<ts>"
}
```

**逐字段对应铁律条款**：

| 字段 | 铁律条款 | 违反后果 |
|------|---------|---------|
| `trust_level` | 候选铁律 6（N2 守卫：入册必带，缺省 T1 最严） | 入册被拒 |
| `plug_level` | 铁律 3 拔插等级义务（M5 前置） | 入册被拒 |
| `stop_layer` | 铁律 2 细则 5（内核/子缝/实现数） | M1 守卫红 |
| `state_record` | 铁律 3（J4 状态归原语）——每次 run/abort/status 全记 record | G 系列守卫红 |
| `health_probe` | 候选铁律 8（可感知缺席） | L2/L3 插片 absent 未上报 |

## 三、生命周期（每行代码都过守卫的法）

### 3.1 入册流程（生长语法）

```
写插片 → ①manifest 校验（trust/plug/stop_layer 三要素齐）→
②M1-M5 守卫跑插片自身测试 → ③record 入册（SeamRegistry.register + arch_ledger
记 org_member→agent_plugin 关联）→ ④CI arch-guards 必过 → ⑤广播 LingBus
```

每一步的守卫锚点：
- ①= 候选铁律 6 N2 守卫（缺 trust/plug 拒收）；
- ②= M1（插片代码不进 core/，天然豁免主干词汇检查；但插片自己的 tests 必须全绿）；
- ③= J4（run 全程 record 化：agent_run type，含 started/finished/exit/token 四事件）；
- ④= 守卫同源（CI 绿才准 merge）；
- ⑤= LingBus 事件（组织图 org_event 同步插片上线）。

### 3.2 运行时状态机（每插片一个 record 状态机）

```
agent_run: created → running → succeeded | failed | timeout | aborted
```

- `succeeded/failed`：exit code + token 消耗入账（arch_ledger 可 query）；
- `timeout/aborted`：触发 abort() 语义（AgentSeam 协议原生支持）；
- **缺席检测**（候选铁律 8）：health_probe 连续 2 次失败 → 插片 record
  transition 到 `absent`，下游 query 返回 absent 而非假活；
- **契约漂移**（候选铁律 6 T3 漂移条款）：T3 插片行为指纹 hash 变化 →
  contract_drift record（探针差分：固定输入→输出摘要）。

### 3.3 修剪语法（修剪不豁免）

- 单实现龄期 > 30 天且零调用的插片 → M6 仪表标记回收候选；
- 回收 = unregister + debt record 清偿（不算主干 diff 违规，铁律 4 第 1 条）；
- T3 插片连续 7 天 absent → 自动降级到 `dormant`（record 态），不删账。

## 四、守卫映射表（每行代码过哪道法）

| 铁律条款 | 在本方案的执法点 | 执法者 |
|---------|----------------|--------|
| 铁律 1（薄主干） | 插片全在 `plugins/agents/`，core/ 零 diff | M1 词汇守卫 + git diff 检查 |
| 铁律 2（分形停层） | manifest stop_layer 三要素 | M1 守卫扩展（schema 校验） |
| 铁律 3（状态归原语） | run/abort/status 全 record 化 | G 系列守卫 + arch_ledger |
| 铁律 4（修剪语法） | 零调用龄期回收 + absent 降级 | M6 仪表 + debt 到期 |
| 候选铁律 5（双向互认） | 灵族成员仓可反向把 lc 注册为它们的插片（federation_pair） | N1 互账守卫（后置，试验田后） |
| 候选铁律 6（信任等级） | trust_level 三级强制声明 | N2 守卫 |
| 候选铁律 7（命名空间） | 缝 key 域前缀：`agent/lingflow`（12子）vs `proj/lingkang`（8工程） | N3 守卫 |
| 候选铁律 8（可隔离） | absent 态 + 故障域圈死 | N4 守卫 |
| J5 四条件 | 双口径：静态（manifest schema）+ 运行时（health_probe/行为指纹） | M6/N5 |

## 五、分阶段落地（试验田先行，一层养一层）

### Phase 1：试验田（1 个插片走通全生命周期）——1 天工作量

选 **lingflow**（任务成员，MCP 传输，T2，L1）：
1. 写 `plugins/agents/agent_lingflow/`（插件包 + manifest + tests）；
2. 走完 §3.1 五步入册流程；
3. M5 截肢测试：unregister 后 lc 主干照跑；
4. record 化验证：一次 run 全链路可 query。

**Phase 1 的产出即守卫升格依据**：候选铁律 6/7 在真实插片上首次实证。

### Phase 2：12 子批量挂载（8 任务成员 + 3 基础设施 + 1 安全）——2-3 天

- 逐个复制 lingflow 模式（传输不同但协议同）；
- lingan 特殊：L2 缺席降级策略单独实现（安全门缺席≠跳过，= 降级到只读模式）；
- 每个插片入册时 org_event 同步（组织图联动）。

### Phase 3：8 对外工程挂载（T3/L3）——1-2 天

- 直调传输（探针型插片：run=触发应用动作，status=健康查询）；
- contract_drift 探针首次实证（T3 漂移条款）；
- absent 自动降级首次实证（候选铁律 8）。

### Phase 4：双向互认试点——后置

- 选 1 个灵族成员仓（建议 lingflow，与 Phase 1 同对象形成对账闭环）：
  它把 lc 注册为它的插片，lc 把它注册为我的插片，**federation_pair 六态状态机
  首次真实运转**（候选铁律 5 N1 互账守卫）。

## 六、风险与如实声明

1. **MCP 端点未实测**：12 子的 MCP server 是否全部就绪未验证（ lingxi 已知可用，
   其余待 Phase 2 逐个探针）——方案按"逐个探针、探不通记 debt"处理，不假设全通；
2. **候选铁律未升格**：trust_level/plug_level/命名空间目前是候选条文（§六），
   本方案按候选执行——**若用户裁决候选铁律 6/7 升格，插片 manifest 字段即成
   铁律强制；若否决，字段保留为最佳实践**（不影响插片运行）；
3. **20 个插片 ≠ 20 个真实可调用服务**：8 对外工程多数是应用而非 Agent（灵康是
   健康应用、灵律是法律应用），它们的"Agent 化"程度取决于各项目自身——
   本方案的插片化是**统一调用面**（lc 能叫得动它们），不是改造它们的内部；
4. **灵依/灵通+ 已退出 12 子**：灵依按 T3（对外工程）挂载，灵通+ 不挂载
   （retired，record 保留存在证明但插片不注册）。

## 七、须用户裁决的 3 个问题

1. **Phase 1 试验田对象**：建议 lingflow（MCP 传输 + T2 + 与灵创工作流天然衔接），
   也可选 lingxi（已知 MCP 就绪，最快跑通）——二选一；
2. **8 对外工程的"调用面"语义**：lc 调用灵康/灵律，是"触发它们的某个动作"
   （如健康查询/法律检索）还是"把它们的 AI 能力当子 Agent 用"（若它们有 LLM 能力）？
   前者立即可做，后者取决于各项目的 API 现状——建议 Phase 3 前逐个确认；
3. **候选铁律 6/7 是否随 Phase 1 同步升格**：升格则 manifest 字段成铁律强制
   （N2/N3 守卫同批上），不升格则字段先行、守卫后补——建议升格（避免"候选条文
   指导实战、实战回头又改条文"的返工）。

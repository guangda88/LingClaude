# 向外扩展第三层方案：能力域插片化（浏览器/Hermes/工作流/桌面/训推）

> **状态**：可行性方案 v1.0（2026-09-18，承接第一层 20 实体接入——lingxi 试验田已通，
> 12 子 + 8 对外工程批量接入由 lc 接手）
> **定位**：四层宏图的**第三层**——把"能力域"（浏览器、工作流、桌面、训推、Hermes 类
> 面板）集成为 lc 插片。**第一层权威定义（用户 2026-09-18 裁定）= 把现有灵族 Agent
> 与对外工程项目作为插片接入 lc，共 20 实体（12 子 + 8 对外工程），lingxi 为首片；
> 信任分级锁定：12 子 T2 / 基础设施 T1 / lingan T1 / 8 对外工程 T3，拔插默认 L1、
> T3 工程 L3 缺席裸奔**。本层复用同一套铁律 5-8 语法，缝 key 域前缀换 `cap/`（能力域）
> 与 `os/`（桌面域）。
> **铁律底座**：铁律 1-4（本体）+ 铁律 5-8（已升格，含操作域/时效域 work_claim 三域）
> + N1-N7 守卫 + M1-M6 + J5 四条件。
> **复用资产**：mcp-wrap skill（封装模板）、bus_bridge（LingBus→插片路由）、
> work_claim/worktree_node（并行修改防护）、agent_lingxi 三件套（插片样板）。

---

## 一、现状实测（2026-09-18，方案的事实底座）

| 能力域 | 现状 | 结论 |
|--------|------|------|
| 浏览器 | `plugins/tools/web` 有 manifest（stop_layer 三要素齐），无 CDP/playwright 引擎插片 | 有查询面，**缺动作面**（Hermes WebUI 实测过 headless 截图，可复用路径） |
| 工作流 | `SeamType.CREW` + CrewSeam（create_crew/dispatch/status）已在 core/seam.py:144-146 | **协议就绪**，缺首个真实 crew 插片（lingflow 工作流引擎是天然候选） |
| 桌面/OS | core/seam.py 无 SeamType.RESOURCE，无任何 OS 类接缝 | **第四层预研提前到第三层落地**——只落"资源即插片"（探针型），不碰完整 OS 插片化 |
| 训推 | llama-server 常驻插片在灵元栈（MEMORY 记载 engine.py 已升级 OpenAI HTTP 调用），13 模型端口 8102-8114 | **还债**：把灵元栈插片正式封 `cap/infer` 挂进 lc（当前是旁路，未走 SeamRegistry） |
| Hermes 类面板 | Hermes WebUI（ref-hermes-webui，快照 11.1k star）：统一界面多 Agent 切换 | **对偶实证**：它把各家 Agent 包成面板=单向平台形态；lc 用 bus_bridge + 铁律 5 可反做"lc 即面板插片" |

**关键判断**：第三层不是"新建 5 个能力"，是**把已存在的能力面（CrewSeam 协议、web 查询、
灵元栈推理、CDP 引擎）逐个封成合规插片 + 补 2 块真正缺的（RESOURCE 探针、浏览器动作面）**。
工作量主要在 manifest 与测试，不在引擎本体。

---

## 二、插片清单（五个域，逐个铁律要素）

### 2.1 清单总表

| 缝 key（铁律 7） | 目标 | 传输 | 信任（铁律 6） | 拔插 | 新增/复用 |
|------------------|------|------|---------------|------|----------|
| `cap/infer` | 灵元栈 llama-server 常驻（8102-8114） | http（OpenAI /v1） | **T2 契约审计**（灵元栈是独立仓，协议 OpenAI 标准） | L1 替换 | **复用**（封插片+record 化，还 MEMORY 债） |
| `cap/crew` | lingflow 工作流引擎（CrewSeam 首插片） | 直调/MCP | **T2 契约审计** | L1 替换 | 新增（CrewSeam 协议已就绪） |
| `cap/browser` | CDP/playwright 引擎（截图/操作/读 DOM） | 直调（CDP 9228 现成） | **T2**（引擎仓内可读） | L1 替换 | **新增**（web 有查询无动作，补动作面） |
| `os/resource` | GPU/CPU/内存/磁盘探针 | 直调（ps/vmstat/df） | **T3 只观测**（OS 黑盒） | L3 缺席裸奔 | **新增 SeamType.RESOURCE**（第四层预研） |
| `cap/hermes` | Hermes 面板（多 Agent 统一界面，可选） | http/MCP | **T3 只观测**（外部面板黑盒） | L3 缺席裸奔 | 可选（对偶实证，最低优先级） |

### 2.2 逐域说明

**cap/infer（还债，优先级最高）**：
灵元栈 engine.py 已升级 llama-server 常驻插片（MEMORY 记载：OpenAI HTTP 调用、
_health_check 不杀进程、13 模型端口由 plugins.yaml 驱动）。当前它**旁路于 lc 主干**
（直接 HTTP 调用，未走 SeamRegistry/record 化）。本层把它封成 `cap/infer` 插片：
- manifest：transport.kind=http，base_url 走 8102-8114，health_probe=GET /v1/models；
- record 化：每次推理请求记 `infer_run` record（model/tokens/latency/exit），J4 语义；
- 缺席语义：llama-server 挂了→absent（候选铁律 8），lc 主干不崩（L1 可替换为其他推理端点）。
**这是"第三层养第四层"的第一块砖——训推域插片化后，OS 资源探针（os/resource）自然有
"该监控什么"的锚点（哪张 GPU 喂哪个模型）。**

**cap/crew（CrewSeam 首插片）**：
CrewSeam 协议（create_crew/dispatch/status）已写在 core/seam.py，但**零实现**（铁律 4
回收候选：单实现=零实现的缝，龄期超线即回收候选）。落 lingflow 工作流引擎为首插片，
让 CrewSeam 从"空协议"变成"有血肉的缝"。lingflow 已是 12 子（第一层接入 lc），
本插片是**同仓双向**：lc 的 crew 能力调 lingflow，lingflow 的任务能力也经 First 层
（agent/lingflow）回投 lc——铁律 5 双向互认的同仓实例。

**cap/browser（补动作面）**：
现有 `web` 工具是查询面（fetch/extract），Hermes 实测证明 CDP headless 截图路径可行
（会话简报 CDP 9228 在监听 chrome 窗口版）。本插片封 playwright/CDP 引擎为动作面：
- 能力：page_load/screenshot/click/fill/evaluate（5 原语起步，对齐 Hermes 实测过的路径）；
- 探针：CDP 端点可达性 + /json/list 返回 tab 列表；
- T2 因为 CDP 引擎是本机可读进程；缺席 L1（换 playwright headless）。
**注意**：浏览器动作是**写操作**（点/填/发），record 化必须含"动作前后 DOM 快照 hash"
（J5 行为级：成功不能只锚定 exit，要锚定"页面状态确实变了"）。

**os/resource（第四层预研，SeamType.RESOURCE 新增）**：
只落"资源即插片"（探针型），**不做完整 OS 插片化**（那是第四层终局，需候选区已升格的
铁律 8 故障域语法全量支撑，本层先吃"探针"这块）。
- 探针四类：gpu（nvidia-smi 若可用，否则 df/vmstat 内存锚点）、cpu（loadavg）、
  mem（free -b）、disk（df -B1）；
- T3 只观测：OS 是黑盒，只记探针 record（`resource_probe` type），不判"OS 违规"；
- 缺席语义 L3：某探针数据源消失（如无 GPU）→该探针 absent，域内其他探针不受影响
  （故障域=单探针，圈死最细粒度）；
- **这是 N4"缺席查"守卫的第一个硬件级实例**（硬件缺席物理事实，铁律 8 第 1 条）。

**cap/hermes（对偶实证，可选）**：
Hermes WebUI 把 CodeX/DeepSeek/Claude/Grok 包成统一面板=单向平台形态。lc 用
bus_bridge + 铁律 5 可反做"lc 即面板插片"——把 lc 注册为某个面板的插片。本层列为
**可选对偶实验**（验证"可把我变成别人的插片"这条命题），非核心路径。

---

## 三、守卫映射（每行代码过哪道法）

| 铁律/守卫 | 在第三层的执法点 |
|----------|----------------|
| 铁律 7（域前缀） | 五插片缝 key 全带 `cap/` 或 `os/` 前缀，N3 守卫拒收裸 key |
| 铁律 6（信任等级） | T2×3（infer/crew/browser）+ T3×2（resource/hermes），N2 双声明拒收缺省 |
| 铁律 8（三域） | 故障域：单探针独立圈死；操作域：work_claim 锁住 cap/browser 的 DOM 写操作（写前认领）；时效域：TTL 过期探针自动 absent |
| N4（三查） | 缺席查：llama-server 挂→infer absent；互斥查：同 tab 双写 browser 即警；时效查：resource 探针超时→remind 夺锁 |
| N5（契约漂移） | infer/crew 是 T2，OpenAI/MCP 契约若单方漂移（如 llama-server 换版本改字段）→contract_drift record，旧结论标 stale |
| 铁律 3/J4 | 每次 cap 域动作记 record（infer_run/crew_run/browser_action/resource_probe） |
| M5（截肢） | 逐插片 unregister 验证主干照跑（第一层 M5 同款） |
| mcp-wrap skill | 五插片封装全走该模板（输入 7 项/四件套/入册五步/踩坑 7 条） |

---

## 四、分阶段落地（一层养一层）

### Phase A：cap/infer 还债（1 天）——最高优先级
封灵元栈为 `cap/infer` 插片，record 化推理调用。**这是"第三层养第四层"的第一块砖**，
做完 OS 资源探针才有"监控谁"的锚点。mcp-wrap skill 直接套。

### Phase B：cap/crew + cap/browser（2 天）
- cap/crew：封 lingflow 工作流为 CrewSeam 首插片（让空协议有血肉，铁律 4 回收候选解除）；
- cap/browser：封 CDP/playwright 为浏览器动作面（写前 work_claim 认领 + DOM 快照 hash 锚定成功）。

### Phase C：os/resource（1-2 天）
SeamType.RESOURCE 新增（**这是本层唯一的 core/ 改动**——加一类 SeamType，走铁律 1 的
"新增类型须走接缝/插片协议"，不算主干 diff 违规）+ 四探针插片 + N4 缺席查硬件实例。

### Phase D：cap/hermes 对偶（可选，1 天）
把 lc 注册为某面板插片，验证铁律 5"可把我变成别人的插片"（对偶实证，非核心）。

---

## 五、如实声明（风险与边界）

1. **SeamType.RESOURCE 是 core/ 唯一改动**（Phase C）——加一类枚举走接缝协议，
   符合铁律 1"新增类型须走接缝/插片"，但须过 M3 依赖方向 + M5 截肢双绿终审；
2. **灵元栈当前状态未实测**：MEMORY 记载 llama-server 常驻插片已升级，但 13 模型端口
   是否全在线、engine.py 实际形态须 Phase A 开工前探测（探不通记 debt，不假设全通）；
3. **cap/hermes 的对偶试点依赖 lingxi 回报**：lingxi 若已把 lc 注册为它的插片
   （双向互认试点），cap/hermes 有现成对偶实体；若未回报，Phase D 顺延；
4. **12 子接入（第一层批量）由 lc 接手中**——本层 cap/crew 依赖 lingflow 插片已挂
   （agent/lingflow），若 lc 尚未完成，Phase B 的 cap/crew 顺延至 agent/lingflow 在位。

## 六、须用户裁决的 2 个问题

1. **Phase C 的 SeamType.RESOURCE 新增**：同意加一类枚举进 core/seam.py
   （走铁律 1 接缝协议，M3+M5 终审）？还是先全用 T3 探针插片不改枚举（把 RESOURCE
   语义留在 manifest 的 transport.kind=probe，枚举后补）？
   建议**前者**（枚举一等公民，N4/N5 守卫好挂；后者是过渡态，后期还要迁一次）。
2. **Phase A 是否开工**：cap/infer 还债依赖灵元栈现状实测（MEMORY 记载 llama-server
   已升级，但 13 端口是否全在线未探）。建议先跑一次 13 端口存活探测（nvidia-smi/
   /v1/models 逐一 GET），探通多少封多少，探不通记 debt——**要不要我现在就跑？**

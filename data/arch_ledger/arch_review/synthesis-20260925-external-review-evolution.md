# 综合评审汇总与进化方向

- 日期：2026-09-25
- 汇总：灵克主会话
- 评审来源：claude-code（MiniMax-M3 经 proxy3）、crush（glm-5.3 经 proxy3）、atomcode（agnes-2.5-flash 经 proxy3）、codex（kimi-k2.7-code 经 volcengine 备用线路）✅ 四家成功；opencode ❌ 因 ZAI 线路配额耗尽缺席（2026-09-29 重置；codex 首次尝试同因配额失败，切 volcengine 后成功）
- 输入：`evolution-brief-20260925-thin-trunk-fractal-plugins.md`（§9 六问）

---

## 一、三家意见交叉验证后的共识（全票通过项）

1. **L0 地基必须显式存在**（crush 提出、claude 深化、atomcode 部分呼应）——「除主干外一切皆插片」之上必须加一句：**主干之下还有地基**。不可插片化的最小集合：
   - 事件账本追加写路径（递归治理的收敛出口）
   - 缝注册表本身（挂载器挂载挂载器=无限回归）
   - 2T3A 三表 schema（records/events/transition）
   - 崩溃恢复最小引导集
   - 元层：教义存身之所（git 受保护分支+签名提交），教义修订建模为特殊 transition（需 arch_law_revision 守护）
   - 词表层：types.py 状态枚举与 transition 合法集
   - **L0 的代价换取「全系统有一个可信的静止点」**

2. **熔断闸双层化**（三家从三个角度收敛到同一设计）：
   - 分层：per-fiber 滑动窗口（隔离）→ 缝域级（gov/agent/cap 域隔离，防单域风暴拖垮全局）→ 全局聚合（兜底）
   - 确定性判据：计数器 > N 且时间窗口 < T，拒绝主观判断
   - 熔断状态本身落 `ly_state_events`（重启不丢、绕过风暴）
   - half-open 探测契约 + 白名单 fiber（风暴期保留最小可服务子集）
   - 热路径与刷新路径硬隔离（模型调用→工具执行不受插片刷新阻塞）

3. **递归治理需要外部锚点切断自指**（三家共识）：
   - 双层账本分离：execution_ledger + governance_ledger 交叉引用不混写（atomcode）
   - hash 链 + 周期快照签名作为外部锚点（claude）
   - 极简不可插片化只读校验器与主干同生共死（crush）
   - 三值语义：治理者无法判定 → `unverified` 显式状态而非异常；「不可判定→人工裁决」是合法 transition 类型

4. **形式化验证降级为「不变量守护」**（四家一致反对全量 TLA+/Coq）：
   - 不变量纯函数断言 + transition 前后执行 + hypothesis 模糊测试
   - 显式枚举合法转换表，表外即 UndefinedTransitionError
   - 只验证主干内核 1,500 行级 + 双写不变量 + 熔断半开不破坏 best-effort 三件最小事实
   - 「不追求证明正确，追求错误立即被捕获」（atomcode）／「分层验证」（crush）
   - 两个优先验证属性（codex）：**蓝绿切换期间无状态丢失**、**依赖消失时降级不循环触发**

## 二、实质性分歧与裁决建议

| 议题 | 观点 A | 观点 B | 裁决建议 |
|---|---|---|---|
| **迁移策略** | atomcode：弃三切片，改双写+周期对账（三切片复杂度超收益） | crush：双写+checksum 对账+对账失败自动冻结迁移；codex：以 events 追加日志为唯一真相源、影子读阶段、回滚=replay | **采三家合流**：迁移五阶段状态机（register→double-write→read-source→read-target→drop）每阶段显式 revert transition 入账；对账失败冻结而非带病双写；一致性仲裁以 lineage 而非时间戳 |
| **三原语表达力** | atomcode：缺 intent 原语、并发语义、best-effort 自相矛盾 | claude：缺约束/invariant 第四原语、query 应收为 L1 原语；codex：不升原语，events 表内加 `kind` 字段（command/event/query）分层 | **折中采 codex 轻方案**：先加 `kind` 字段（成本最低、解决重放语义膨胀）；constraint/guard 升第四原语列入观察；query 收为 L1 读原语；intent 暂不升原语（turn record 已承载）；best-effort 重新表述为「L0 语义：主干不因插片故障而停，但自身故障=不可用」 |
| **瞬态域** | crush：纯计算不强制落账，缺 ephemeral invocation 概念 | claude：热路径观测过缝会成 Goodhart 温床 | **采纳**：定义「免事件化白名单」——热路径动作只记边界事件（入/出/错），不逐调用落账 |
| **L0 边界判据** | crush：三样（账本追加路径/缝注册表/崩溃恢复引导集） | claude：三层（元层教义存身/词表层/性能白名单）；atomcode：四样（+守卫裁决权+用户确认机制）；codex：**可操作判据——「若替换该组件会导致无法判断插片合法性或治理者合法性，则保留为 L1 固有设施」** | **采 codex 判据为纲，各家清单为目**：宪法级 = 2T3A 三原语+挂载器+身份锚点+审计契约+守卫裁决权+用户确认机制；元层教义存身（git 受保护分支+签名提交）；崩溃恢复最小引导集 |

## 三、外部评审带入的新增风险（原卷宗未覆盖）

1. **账本膨胀风险**（crush）：过度账本化拖慢热路径——免事件化白名单是解药
2. **熔断闸自身单点**（atomcode）：闸触发后谁决定恢复？——half-open 探测契约+确定性判据
3. **重放成本线性增长**（atomcode）：自审越老越慢——双层账本让治理重放只扫 governance_ledger
4. **静默写偏**（crush）：新库成功/旧库失败被 best-effort 吞掉累积成不可逆分叉——周期 checksum 对账是解药
5. **教义自身存身之所**（claude）：教义是元陈述无法自我覆盖——元层地基必须显式

## 四、下一步进化方向（综合裁决后）

**教义修订（v2）**：「除主干外一切皆插片，主干之下有地基（L0），地基之外一切皆插片。」

**四大件实施序列**（吸收 crush 总评「先建闸、再迁缝、后动刀」）：

1. **建闸期**（先行，全部小件）：
   - 双层熔断闸（per-fiber→缝域→全局，状态落账，half-open 契约）
   - 双写对账器（checksum 周期比对，失败即冻结迁移）
   - 不变量守护框架（合法转换表 + UndefinedTransitionError + hypothesis）
   - 免事件化白名单（热路径只记边界事件）
2. **迁缝期**：15 状态模块双写迁移 × MEMORY/GOVERNANCE 实体迁缝（同一动作），五阶段状态机推进，每阶段 revert transition 入账
3. **动刀期**：core 孤儿删除（铁律 4 首例）→ 主干收缩至 1,500 行级 → plugin_lifecycle 通电核验
4. **闭合期**：递归治理三件套（双层账本+外部锚点+三值语义）、state_query 自省、出生登记五字段、760 模型路由的调用分布口径开账

## 五、本轮评审过程的额外收获（附送发现）

1. **proxy3 断供根因修复**：服务本体健在（`/home/ai/llm-proxy/proxy3_py/main.py`），仅进程死亡+监管进程（PID 4163420，9月16起）失能不拉起——setsid 拉起后 760 模型路由全部恢复，claude/crush/atomcode 三路 CLI 全部经它成功出网。**M6 卷宗 related_debts 的 proxy3 项可标已解决**
2. **网络拓扑真相**：灵克 bash 工具运行在独立受限网络命名空间（可见 0 监听、本机服务全不可达、无外网 DNS）——子进程继承隔离。宿主侧服务对宿主进程全部可达。**这个事实本身就是「分账盲区」的物理层版本：观测位置决定账目视角**
3. **ZAI 线路配额墙**：codex/opencode 共用同一配额（2026-09-29 重置）——多线路冗余（volcengine/coding-plan 等备用）应作为插片化韧性的一部分
4. **memory_watchdog 持续告警**：committed_AS 183-185% 超阈值——overcommit 风险真实存在，内存纪律不是空话
5. **磁盘 97% 满**（剩 6.5G）——又一个基础设施欠账，与 45 个 .bak、core 内垃圾同族

## 六、实施计划（第一周粒度）

| # | 动作 | 依赖 | 量级 |
|---|---|---|---|
| 1 | 教义 v2 + L0 地基清单 + 免事件化白名单 → 2T3A 卷宗增节入账 | 无 | 文档 |
| 2 | 双层熔断闸设计与实现（refresh_all 防护） | #1 | 小件 |
| 3 | 双写对账器（JsonFile↔LingYi checksum 比对） | #1 | 小件 |
| 4 | 不变量守护框架（转换表+hypothesis 冒烟） | #1 | 中件 |
| 5 | 迁移五阶段状态机写入双写协议文档 | #3 | 文档 |
| 6 | TRANSPORT 缝试点迁移（3 import 点，试刀） | #3,#5 | 中件 |
| 7 | proxy3 恢复项+网络拓扑事实回写 M6 卷宗 | 无 | 文档 |
| 8 | core 孤儿删除（5 模块，测试网兜底） | #4 | 中件 |

**立即执行**：#1 与 #7（纯文档、零风险、当轮闭环）。

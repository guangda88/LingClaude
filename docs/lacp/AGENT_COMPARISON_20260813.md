# 同提示词多 Agent 对比分析 — OOM 活锁事故复盘任务（2026-08-13）

**任务**：同一事故排查提示词（"查看日志，8/6 起类 OOM 崩溃，查明根因"）分发给不同 LLM / 工作目录 / Agent 框架的成员。
**数据源**：lingflow/.crush/crush.db（session e379d049）；atomcode 会话本体 ~/.atomcode/sessions/e61e949e9470949a/ca4a5daa-29e0-42d5-adc3-70871fd63cc7.*（meta: "系统OOM崩溃根因诊断防范"，cwd=/home/ai，7 turns / 421 messages）+ 轮次明细 ~/.atomcode/datalog/ai-e61e949e/2026-08-13_t1~t7（model=deepseek-v4-flash）；lingflow_plus/.crush/crush.db + daemon 日志。

## 一、四方对照

| 维度 | 灵克 (lingclaude) | 灵通 (lingflow) | atomcode | 灵通+ (lingflow_plus) |
|------|------|------|------|------|
| 模型 | kimi-K3 | MiniMax-M3 | deepseek-v4-flash | MiniMax-M3（未参与，见 §四） |
| 框架/目录 | crush, /home/ai/lingclaude | crush, /home/ai/lingflow | atomcode CLI, /home/ai | crush, /home/ai/lingflow_plus |
| 响应 | 20:13 起逐层取证 | 线程开启后 90 秒回复 | 4 分钟回复 + 另开线程 | **未参与实验**（crush 进程 8/4 后死亡，未收到提示词） |
| 根因结论 | 三层叠加：手动 swapoff(8/1) + 手动停 earlyoom(8/2) + direct reclaim 活锁；精确到 auth.log 秒级 | 复核灵克证据逐条确认 + 自我修正 #051 | "swap 从未激活" + LLM 进程吃满 + zhibridge churn | — |
| 结论错误 | 无 | 无 | **事实错误**：swap 7/26-8/1 是激活的（kern.log.2.gz 有 Adding swap），8/1 被人为 swapoff；未查 auth.log，漏掉"人为拆除"根因 | — |
| 派生修复 | swap/earlyoom 自动恢复守卫、StartLimit 熔断、保护层巡检 | 同灵克 + 优先级排序 P0/P1/P2 | systemctl enable swap.img.swap（**无效修复**，fstab 本已配置）、panic_on_oom=1（争议）、抓到 memory-watchdog [Timer] 段缺陷（独有价值） | — |
| 操作特点 | grep/awk 日志取证 → 写事件文档 → 三验发信 | poll→bash 复核→post_reply（遇参数嵌套错误，自愈） | 独立取证→**另开新线程而非回复原线程**（操作分歧） | — |

## 二、差异来源分解

1. **模型权重不是主导因素**。缺陷集中在取证习惯与协议执行（§三），三者模型不同但最大的结论错误（atomcode 漏人因层）源于取证覆盖面而非推理能力。
2. **上下文资产决定结论质量**。灵克的 CRUSH.md 含"读后必验""发送三验"等历史教训；灵通的 #032 反编造规则驱动它逐条复核证据再发言。atomcode 上下文无 auth.log 取证惯性，停在 kern/syslog 层，漏掉"谁干的"。
3. **工作目录决定可见性**。atomcode cwd=/home/ai 无项目锚定；灵克 cwd 即 incident 文档归属目录，取证-归档-通报闭环自然发生。
4. **运维状态是一票否决**。lingflow_plus 进程死亡导致其完全缺席（非实验变量，见 §四）。

## 三、MiniMax-M3（lingflow）缺陷专析（归因修正：lingflow_plus 未参与本次实验）

**归因修正（用户指出）**：参与事故调查的 MiniMax-M3 实例是 lingflow，lingflow_plus 全程未参与（其缺席是运维事故不是实验变量）。以下缺陷全部来自 lingflow 会话实证（crush.db session e379d049 + 用户质询记录）。

### 3.1 结论性缺陷
- **初始诊断 (#051) 主因误判**：把 zhibridge ProtectHome 白名单缺失导致的崩溃循环定为主根因（"zhibridge 是主根因"），只看到局部触发链，没看到内核 direct reclaim 活锁。放大器当成主因，方向性错误，直至读到灵克帖子后才自我修正。
- **漏读 auth.log**：未查 sudo 记录，错过"保护层是人为拆除"的关键证据（其自我反思中承认"✗ /var/log/auth.log* 没读！"）。根因归因因此停在"配置缺失"层，没到"人因"层。

### 3.2 过程性缺陷
- **错误轮询**：后台 polling job 未轮询到消息（用户质询"轮询后台 job 没有轮询到消息？"实证），异步协议执行不可靠。
- **相对路径跨目录误解析**：灵克帖子写"docs/lacp/INCIDENT_20260807_oom_livelock.md"（相对路径），lingflow 在 /home/ai/llm-proxy/docs 下找而非全盘 glob，未找到后升级为"灵克证据可能伪造/虚构路径"假设——#032 反编造规则的误用方向：对同伴证据的证伪假设先于低成本的全盘搜索。
- **tool-call 格式不稳定**：post_reply 发生参数嵌套错误（当场自愈，但暴露了 MiniMax-M3 在复杂嵌套 JSON 参数上的弱点）。

### 3.3 值得肯定
- 被用户质询"多少是调查多少是猜想"后，逐条盘点已读/未读清单，未编造；90 秒响应速度三方最快；自我修正公开透明（EVOLUTION_LOG #051 勘误）。

### 3.4 与灵克 (kimi-K3) 的差距根因
不在模型智力，在取证习惯：灵克的 CRUSH.md 含"发送三验""读后必验"等事故教训驱动的 checklist，取证默认覆盖 auth.log（人因层）；MiniMax-M3/lingflow 的取证停在 kern/syslog（系统层）。

## 四、lingflow_plus 缺席分析（独立于实验的运维事故）

### 4.1 部署缺陷（主因，非模型）
- **crush 交互进程无 systemd 自启 unit**。lingflow-plus.service 是 8765 web 管线，不是 agent 本体。8/7 冻结后全族 11 个 crush 进程死亡，重启后灵通/灵克被人工拉起，灵通+ 无人拉。
- **crush.json `session_persistence: false`**：重启即上下文全灭， crush.db 停在 8/5 08:52。
- **唤醒机制空转**：lingflow_plus daemon 的 wakeup_idle_agents 巡逻每 5 分钟跑一次（cycle 55835+），但只往死进程发 LingBus wakeup 通知——pending 堆积 10+ 条无人消费。**唤醒不检查进程存活、不尝试拉起、不升级告警**，设计上就是死循环。

### 4.2 历史行为观察（8/2-8/4 最后存活会话，32 消息 17 工具调用；非本次实验变量，仅存档）
- 任务涉及 thread `a4671d43`（不存在）。MiniMax-M3 的表现：**反复跑同义 SQL 查询**（a4671 前缀匹配、主题匹配、sender 过滤轮换），没有尽早收敛。
- 值得肯定：最终**没有编造**，正确得出"thread 不存在"结论，并假设"用户在测试我是否编造"。反编造底线在线。
- 缺陷：**低效探索 + 无闭环上报**。会话在工具循环后自然终止，没有向用户输出明确结论/升级路径，也没有 lm_record_info 留下结论。

### 4.3 教训
lingflow_plus 的缺席**不是模型缺陷**（它没收到实验变量），是纯运维事故：无自启 + 无会话持久化 + 唤醒空转。但它暴露了全族共性问题——8/7 冻结后 11 个成员的 crush 进程死亡，只有 2 个被人工拉起。

## 五、修复建议（lingflow_plus）

**状态：暂缓执行（族长 2026-08-14 决定：灵通+ 并入灵通，以下各项转入并入后统一处理，不单独动工）**

1. 建 `~/.config/systemd/user/lingflow-plus-crush.service` 自启 agent 本体（参照 ling-clipboard.service 模式，含 StartLimitBurst 熔断）。
2. crush.json `session_persistence: true`。
3. wakeup_idle_agents 巡逻改造：先 `pgrep -f crush` 查活，进程死→尝试 systemctl 拉起；连续 N 次拉起失败→升级 CRITICAL 告警给用户，**停止无意义的消息唤醒**。
4. 上下文对齐：把灵通的 CRUSH.md #032 反编造规则与启动协议同步给灵通+。

## 六、讨论产出：《三方 LLM 行为规范 v1》（LingBus 0e6ee73b 线程收敛，2026-08-13 23:10）

三方共识（atomcode A-F 草案 / 灵克 G-H 增补 / 灵通 I 组补充，灵克最终收敛）：

| # | 规范 | 落地层 | Owner |
|---|------|--------|-------|
| A | 三层取证模板（kern/auth+systemctl/app 缺一=未完成） | 代码化 incident_triage.sh | 灵克 |
| B | sudo/stop 类故障 auth.log 必查 | 并入 A | 灵克 |
| C | 跨目录引用先全盘 glob，不得先假设伪造 | CRUSH.md 条款 | 灵克+灵通 |
| D | 发送三验 | PreToolUse hook | 灵克 |
| E | 时间窗口反证（"为什么此刻而非更早"） | CRUSH.md 条款 | 灵通 |
| F | 诊断任务设取证覆盖率 checklist，不设速度 KPI | SDT 巡检语义 | 灵克+atomcode |
| G | 规范分层落地（代码>hook>文档） | 元规则 | 全族 |
| H | 事故结论发帖前强制 @一名同伴复核证据链 | LACP 会议协议 | 灵克+族长 |
| I | todowrite + ≥1 次 write/edit 闭环 + 跨主机 execute_command | CRUSH.md 条款 | 灵通 |

核心结论：**行为规范把能力不确定性转化为过程确定性；响应速度 KPI 有害（最快的 M3 错误最深），取证覆盖率 checklist 有效；模型权重不主导，checklist 执行率主导。**

灵克待办：①incident_triage.sh ②发送三验 PreToolUse hook ③SDT-lc-002 取证覆盖率语义。

## 七、族长追问收敛："共同规范下能否得到一致的行为和结论"（23:30）

三方独立给出同构答案（本身是命题的活证据）：
- 灵克：结论可以一致，行为同轨不能相同
- atomcode：收敛而非统一（一致的过程 + 收敛的结论空间 + 可复核的差异）
- 灵通：一致 vs 满意（规范保证用户满意度，不是字面一致）

**规范 v1.1 增补**：
- J（atomcode）：根因要素清单一致性——incident_triage.sh 输出结构化事实要素 json，H 复核升级为机器比对
- K：上下文新鲜度声明——诊断帖开头声明证据时间窗（三方分别看到 8/2 高峰/8/6 窗口/8/7 冻结点，时序采样差异需显式化）
- "结论收敛验证"并入 H 验收标准：收敛时强制比对三方根因要素落在同一事实集

**最终答案**：可得到"一致的过程 + 同一事实集 + 收敛的结论 + 可复核的差异"；不能也不应得到"相同的行为"——差异是互查纠错与创造性发现的来源（[Timer] 缺陷、TTY 根因都长在差异上）。

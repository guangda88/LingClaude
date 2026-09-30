# 2026-09 Agent 产品潮四外部 Agent 联审综合报告

> 2026-10-01 灵克(lingclaude) 委托 cc(Claude Code/sonnet)、codex(-p kimi)、opencode(zen-free)、crush 四个外部 agent 独立作答，原始答案见 `data/tmp/ans_{cc,codex,opencode,crush}.md`。本文件为综合汇总。
> 调研对象：Grok Bot（xAI/Cursor）、Muse（Meta）、Cue（Manus 2.0）、Dots（OpenAI）、DSH 桌面版（DeepSeek Harness）。

## 一、四家一致确认的行业趋势（共识度排序）

### 趋势1：授权判定从 prompt 约定下沉为网关层的策略对象（4/4 一致，权重最高）
- Muse Sentinel：模型只见引用值、永不见真实密码；Dots 动作四档（自动/预审批/先问/交还人）；DSH 调用前机器审查。
- 本质：**"能不能做"的裁决点钉在可观测、可记账的单次工具调用上**，安全从软约束（提示词）变硬约束（网关代码）。cc 表述："授权判定从对话边界下沉到工具调用数据点"。

### 趋势2：常驻责任实体 + 独立执行环境（4/4 一致）
- Grok Bot Routines（cron/webhook/PR/CI 触发）、Dots 常驻、Muse 长期目标；每 agent 独立 VM/电脑成为标配。
- 本质：agent 从"一次性会话"升级为"有身份、有职责、有时钟的责任主体"，溯源/追责/爆炸半径隔离有了物理锚点。

### 趋势3：薄主干 + 插件化 + 结构化编排（4/4 一致）
- DSH Cordis"万物皆插件"、实验性插件 opt-in 发布制；编排层弃 stdout 包装转 SDK（Omnara pivot 教训）。
- 本质：契约本身成为产品——工具 schema、错误码、权限返回结构是可消费资产。

## 二、对 lc 的进化建议（四家交叉验证后收敛为五条）

### 建议1：守卫升级为四档策略引擎，接 PreToolUse hook（cc/codex/crush/opencode 四家独立提出，最高优先）
- 现状守卫（连败3次硬中断、建议-执行分离）是硬规则/自然语言约定，颗粒太粗。
- 做法：`policy.yaml` 按「工具 × 资源类型 × 风险级」定义四档（read-only 自动 / reversible-write 预审批 / external-send 先问 / destructive 交还人+拒绝），在灵犀 execute_command 入口和 PreToolUse hook 查表路由；**每次裁决连同策略 ID 写台账**——审计从"记动作"升级为"记裁决链"（返审能看到"为什么放行"）。
- crush 补充：风险档位与验证台账强制绑定，副作用调用落盘前必须产出证据入 core/verify_ledger。

### 建议2：凭证分层——模型永不见明文（cc/codex/opencode 三家提出）
- 做法：模型只持凭证句柄（`cred:laya_api`）或成员级短时令牌（TTL ≤ 会话），真实密钥在 proxy3/8765 代理侧注入，台账记句柄/令牌 ID 而非明文。
- 直接堵住"日志/上下文泄漏密钥"这一自托管 agent 最常见事故；与 free_pool 鉴权链天然对接。

### 建议3：最小执行域 capability 化，不引入 VM（cc/crush/opencode 三家提出）
- 做法：把 SeamRegistry 强化为执行域对象 `{read_paths, write_paths, net_allowlist, side_effect_class}`，按成员/子 agent 强制校验；高风险操作绑定影响面标签。
- cc 的"发证式"变体：每次 ToolCall 签发带 scope+TTL+次数上限+目标哈希的 capability grant，执行器只认 grant。
- 共识：**隔离做在进程/路径/命名空间层，Muse 式 VM-per-agent 对自托管是过度工程**。

### 建议4：记忆可纠正——supersede 链而非原地改写（cc/codex/crush 三家提出）
- 每条记忆/journal 带 prev_digest/哈希链指针，纠正是追加 supersede 记录，verify_claim() 沿链取最新有效值；可导出、可离线校验。
- cc：台账条目加内容哈希构成 hash chain；crush：记忆加 confidence/last_verified/source_evidence 字段，守卫触发自动降权未验证记忆。
- 本质：把"验证台账纪律"从模型自觉升级为数据结构强制。

### 建议5：常驻化的正确姿势——总线与台账常驻，模型不自主巡逻（cc/codex/crush 三家提出）
- 可做：LingBus 触发器注册表（`bus.subscribe(trigger={cron|webhook|git_hook|ci}, routine=...)`），每个 routine 带 owner/SLA/幂等键，触发先写带 trace_id 的 journal；SDT 升级为一等常驻责任对象。
- crush 补充 Duty State（idle/read_only/busy/risky）：busy 时默认关闭跨域写，self_audit_trigger 指纹变化自动降级职责档位，H17 闭环后回升。
- cc 补充中断闩锁：常驻任务必须能被一条命令停掉，且"停"的动作入台账。
- 共识：**主动性由调度器驱动，不由模型驱动**（Dots 空闲自主调研不抄）。

## 三、四家一致认定的陷阱（共识度最高的部分）

| 陷阱 | 判词 | 四家态度 |
|------|------|----------|
| **DSH 同模型自动授权审查** | 伪守卫/自证谬误：模型被注入时审查器同样被注入，共谋失效+延迟翻倍+"已审查"虚假安心 | 4/4 否定（本报告最一致结论）。正确姿势=规则引擎+异构模型+必要时人，三者结论分别入账；模型审查仅兜长尾 |
| **Cue 式独立 email/phone/wallet** | ToC 融资叙事，自托管场景 agent 共享主人身份，价值密度趋零，纯增攻击面 | 4/4 否定。lc 只需内部可归因 ID，不需要社会身份 |
| **Dots 空闲主动调研** | 烧 token+状态腐化+污染 LingBus 信噪比；无人纠偏时目标漂移 | 4/4 否定/谨慎。保留显式触发（cron/webhook），不做模型自主发起的探索 |
| **模型-harness 联合训练** | 闭源厂商护城河，自托管无法闭环 | 2/2 提及者否定。lc 护城河在治理层不在权重 |
| **为"万物皆插件"重写主干** | Cordis 统一前提是厂商控制模型与发布渠道；lc 薄主干已正确 | cc 否定。保持小步快跑 |

## 四、一句话总判

四家外部 agent 的答案高度收敛：**这一轮产品爆发的实质是"治理的工程化"（权限对象、凭证分层、最小执行域、可纠正记忆），而非"agent 更像人"**。lc 的既有骨架（薄主干+铁律+台账+hook+LingBus）恰好站在正确一侧——进化路径是把自然语言纪律继续下沉为网关代码与数据结构（建议1-5），拒抄 C 端拟人形态（独立身份、自主巡逻、同模型自审）。

## 五、落地优先级建议（灵克综合裁量）

1. **P0 建议1**（四档策略引擎）：改造成本最低（已有 hook 基础设施），收益最高，且直接兑现 lc 守卫失效审计的"输出前自检"缺口——用确定性门禁而非模型自审来补。
2. **P1 建议2**（凭证句柄化）：proxy3 已有鉴权链，增量小；堵最常见的密钥泄漏事故。
3. **P1 建议5**（触发器注册表+Duty State）：把 SDT 产品化，配合已有的 self_audit_trigger。
4. **P2 建议4**（supersede 链）：动数据结构，需迁移方案，缓一步。
5. **P2 建议3**（执行域对象化）：与 SeamRegistry 演进合并做。

> 附：四份原始答案中各家独有亮点（未入正文但值得留档）——cc 的 hash chain 台账与中断闩锁；codex 的成员级短时令牌 TTL 设计；crush 的 Duty State 生命周期与"自动档泛化"警告（不得牺牲发送三验）；opencode 的 risk_table.json 查表实现路径（crush.json hook 配置）。

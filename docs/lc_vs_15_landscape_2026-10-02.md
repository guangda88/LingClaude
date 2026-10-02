# lc × 15 家 Agent/Harness 横向对比（2026-10-02）

> 方法：lc 侧锚点全部来自 2026-10-02 深度审计实测（90,394 行逐文件 AST 解析 + 15 项布线断言 + 锚点级源码读取，见 `docs/lc_deep_audit_2026-10-02.md` 口径）；外部数据 = 09-30 景观报告 + 10-01/02 时效复核（aiidelist 三家对比、memeburn 48 小时 Slack 大战、teslanorth Grok 编码工具更新、laozhang 价格表、ibtimes 云计算机专题）。
> 对比集（四赛道 15 家 + lc 基准）：托管个人——Muse/Dots/Cue；托管企业编码——Grok Bot/Copilot Autopilot/Devin/Codex；编码原生 harness——Claude Code/Cursor 3/DSH/Antigravity/AtomCode；自托管/编排——OpenClaw/Hermes/编排器框架族（amux·Claude Squad·Conductor / Superpowers·GSD·GSTACK）。

## 一、对比矩阵

| # | 产品 | 执行环境隔离 | 凭证·权限 | 记忆 | 常驻触发 | 生态分发 | 模型灵活 |
|---|------|-------------|-----------|------|----------|----------|----------|
| 1 | Muse | 专属 Secure VM | Sentinel 出口审查+凭证对模型隐藏+审批+行动轨迹 | 学习型，不可纠正 | 关闭续跑+主动建议 | 小企业连接器 16+ | 仅 Spark（1.3 跨 harness 训练，省 20% 工具调用） |
| 2 | Dots | 每 dot 云电脑 | 四档规则+auto-review | **不可查看/删改（官方 FAQ 实锤）** | 24/7+只读调研 | ChatGPT/Codex/Slack/Teams/4000 应用 | 仅 GPT-6 Astra |
| 3 | Cue | 每 agent 独立电脑+身份 | 内建 | 内建 | 会话型 | 邀请制 | 内部 |
| 4 | Grok Bot | **账户级共享电脑（官方：别当安全边界）** | 技能内审批边界 | 角色上下文复利 | Routines(cron/事件)+Team Bots | 工程模板市场+Cursor 交接+GitHub/Origin | Grok 4.7 原生懂 harness |
| 5 | Autopilot | 租户内 workspace | 企业治理/审计 | 租户记忆 | M365 事件 | Agent 365 分发 | Copilot 系 |
| 6 | Devin | 云沙箱 | 质量门 | 任务级 | 会话型 | 品牌+SLA | 内部 |
| 7 | Codex | 每任务 Linux 容器+worktree | 审批 | 会话+Skills（Figma/Linear/Vercel 一方） | Automations(cron) | Agents API 五层基建 | 仅 OpenAI |
| 8 | Claude Code | 本地+worktree+checkpoint | hooks+审批 | CLAUDE.md+会话 | GitHub 事件 | 插件生态+SDK | 多 provider |
| 9 | Cursor 3 | 云 computer+共享 context 文件 | 权限 | Projects 共享文件 | 协调器常驻 | IDE 用户基数 | Composer 为主（11-12 断供 OpenAI） |
| 10 | DSH | DSec 三层沙盒 | 调用前同模型审查；**生态无签名验证（社区审计明示需补）** | 会话+插件 | 自动化任务插件 | npm/cordis.patch 分发+市场（3.5k 目录/2200+） | DeepSeek 为主+主流接入 |
| 11 | Antigravity | 云执行 | 内建 | 团队面板 | 内建 | Google 分发 | Gemini |
| 12 | AtomCode | hook 门禁+rewind | 审批+marketplace | 会话+rewind | 命令驱动 | 插件市场+ACP | 多 provider |
| 13 | OpenClaw | 用户自管机 | 自管 | markdown 平面 | 网关常驻 | 开源社区 | 多 provider |
| 14 | Hermes | 本地后端可换 | MCP+自管 | 情景/语义/技能三层 | 内建调度 | 开源 | 多后端 |
| 15 | 编排器/框架族 | worktree/tmux | 各自门禁 | 无（状态到盘） | 任务板/cron | GitHub 星标社区 | 任意 |
| 16 | **lc（基准，自托管）** | 进程级 landlock+目录级沙箱规则+net_allowlist 网闸（directory_rules 激活退注释态）；无 VM/容器级 | vault AES-256-GCM 字段级+根密钥三链+池句柄化+env 收窄；四档策略引擎活体裁决+hook 三注入点+契约门 | supersede 哈希链+prev_digest 锚点+append-only journal+项目记忆文件（可纠正/可审计） | 触发器注册表雏形（cron/SDT 散挂，未产品化）；无 webhook/IM 事件触发 | 无对外市场——35 插片+5 插件命令仅家族内部；无社区/模板 | 6 provider+联邦六态+三档语义+降级链+BYOK/自托管 |

## 二、lc 的优势（2026-10-02 代码实锚）

1. **治理做成可审计账本——15 家唯一**。四档策略引擎（活体裁决台账在产，tool_auth_2026100x.jsonl）+ hook 三注入点（HookPoint: PRE_TOOL/AFTER_TOOL/POST_RESPONSE）+ 任务契约门 + 铁律行级台账 + 豁免复审制度（机器初筛器 D1-D4 + 人工三问 + 红清偿）。对比：Muse Sentinel 是黑盒审批层，Dots 是产品规则，Grok 是文档建议——没有第二家把治理做成本身可审计、可复审、可清偿的账本体系。
2. **记忆可纠正打在 Dots 官宣缺陷上**：OpenAI FAQ 实锤"无法查看/删除/编辑 dot 的单条记忆"；lc 是 supersede 链 + prev_digest 哈希链 + append-only journal + 项目记忆文件——用户主权记忆在数据结构层成立。
3. **凭证工程**：vault 字段级 AES-256-GCM + 根密钥三链（env→keyring→0600）+ credential_pool 句柄化 + env 收窄——Muse"模型不见密码"的自托管等价物，且台账记句柄不记明文。
4. **多成员真隔离**：Grok Bot 官方承认 Bots 共享电脑"别当安全边界"；lc 的 12 成员 + LingBus 签名消息 + 目录级沙箱 + net_allowlist 网闸在架构上按成员划界（尽管激活刚退回注释态）。
5. **模型不锁定**：6 provider + 联邦六态 + 三档语义（fast/standard/strong）+ 降级链 + BYOK——对比 Dots/Cursor/Codex 单家绑定（Cursor 11-12 断供 OpenAI）。
6. **全链内部纵向证据**：journals + 台账让 lc 拥有业界普遍缺失的 longitudinal evidence 雏形（外部评测普遍承认无横向实测）。
7. **自托管用户主权 + 中文工程场景** + 自优化闭环（self_optimizer + data_flywheel + journals 失败轨迹）。

## 三、lc 的弱势（同日实锚）

1. **生态与分发——最大鸿沟**：Grok Bot 工程模板市场、DSH 插件生态（npm/cordis.patch 分发，两个社区市场，3.5k 目录/2200+ 插件）、Dots 4000 应用、Cursor IDE 用户基数；lc 的 35 插片只服务家族内部。
2. **常驻多端闭环**：Muse/Dots/Grok Bot 48 小时内全部接上 Slack（Muse 走 WhatsApp）；lc 有 task_state 投影端点（`core/session_projection.py`）但无手机/IM 消费闭环。
3. **隔离等级低一档**：进程级 landlock vs 业界 VM/容器默认；且 directory_rules 激活刚被全量门禁 20 连红退回注释态——闸门已有、通电失败一次（激活需要按目录域收紧测试，而非 default writable=/home/ai）。
4. **harness 联训不可及**：Muse Spark 1.3 跨 harness 训练（省 20% 工具调用/25% token）、Grok 4.7 原生理解 harness——lc 只能做廉价版（journals 失败轨迹→评测集）。
5. **治理缺口自留地**：classify_state 输出前接线未做（`core/evidence_protocol.py:149` 定义、全包消费方为零——无外部质疑时编造可直出）；journal-gap 债（豁免写路径未入链，due 10-09）；单重 turn 消耗峰值 8.01M（10-02 01:20，132 轮）。
6. **无对外证据**：连 launch claim 都没有——自托管天性使然，但治理优势目前不可被外部感知。

## 四、定位判定

lc 生态位 = **自托管治理型编码 agent + 家族协同**，与 OpenClaw/Hermes 同赛道但治理厚一档。横向结论：**在"信任架构"轴（凭证分层/权限策略/记忆可纠正/可审计账本）上 lc 是 16 家里最厚或并列最厚**——而 10 月初三家动向（Muse Sentinel、Dots custom rules、Grok approval boundaries）证明该轴正在被定价。**弱在分发广度与常驻形态**：生态、多端、联训、VM 级隔离有代差。

一句话：lc 赢在信任架构的深度，输在把它变成可分发资产的路径——最高杠杆动作不是继续加治理能力，而是把治理优势资产化（Preset 包/插件契约导出/journals 评测集），否则"最厚的账本"永远只有自己能看见。

## 五、附录：DSH 双向认证方案（本次对比的直接延伸，2026-10-02）

**结论**：可行，传输通道当天可通。资产二（插件契约导出）验收线升级为"DSH 作为首个外部消费者，双向认证成立"。

两侧底座：lc 侧 HMAC-SHA256 签名原语（`lacp/__init__.py`，sign_manifest 导出）+ 多签雏形（`capability_seam.py` required_signers）+ MCP server 入口（`lingclaude-mcp`）；DSH 侧内置 `@deepseek-ai/dsh-mcp-client` 支持 streamable-http + `headers.Authorization`（Bearer token 原生），第三方 mcp-manager 提供 OAuth PKCE 升级路径，生态无签名验证（`dsh-release-proof` 明示需 signature verifier 配合——lc 补位）。

四层设计：①密钥层——双边带外指纹钉住（TOFU+pin），HMAC 共享密钥起步、开放分发升级 Ed25519；②传输层——lc MCP server（streamable-http + Bearer token）+ DSH mcp-client 连入，DSH 会话出现 `mcp__lingclaude__*`；③资产层——contract.json/.lcpreset 附 HMAC 签名 + 独立 verify 工具给 DSH 侧；④裁决层——lc 消费 DSH 资产走四档引擎，外部来源默认 ask 档入台账。

三条边界：HMAC 对称性（双边够用、分发需非对称升级）；代码不互载（lc 不跑 DSH 的 JS/Cordis 代码，消费=清单级审计+MCP 工具调用）；lc MCP server 认证中间件为 M1 主要工作量。

里程碑：M1 lc MCP server 加 token 认证+指纹钉住（1 天）→ M2 DSH 侧接入+双向 demo（1 天，需 DSH 环境配合）→ M3 签名验证器独立工具包（1 天）。达成即同时命中"鸿沟突破验收线"：非 lc 成员成功消费 lc 资产，且消费者是 15 家里治理话语最重的 harness。

## 原始材料索引

- lc 侧：`docs/lc_deep_audit_2026-10-02.md`（深度审计）、任务台账 `data/arch_ledger/arch_audit_task/`、活体台账 `tool_auth_20261002.jsonl`
- 外部：aiidelist.com（10-02 三家对比）、memeburn（10-02 48 小时 Slack）、teslanorth（10-01 Grok 编码工具）、laozhang.ai（10-01 价格表）、ibtimes.sg（10-01 云计算机专题）、github.com/deepseek-ai/deepseek-harness（MCP client README）、DSH 社区插件审计文档与两个市场插件

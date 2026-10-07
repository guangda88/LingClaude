# 灵克 × 飞书配合方式：五家 Agent 评审综合方案（终稿）

> 评审团：cc (MiniMax-M3.1-Flash) / codex (0.160.1) / oc (opencode 1.18.34 zen-free) / ac (atomcode headless) / lc 自答
> 统一提示词同题下发，各自独立作答后综合。
> 原始答案与材料归档：`~/yitang/docs/lc_feishu_review_answers/`（cc 23KB / codex 9.9KB / oc 9.3KB / ac 8.8KB / lc 3.9KB + 三份材料）。
> 输入材料：豆包工作清单、八大 Agent 架构启发、lc 弱势实证 17 条（W1-W7/S1-S5/B1-B5）。

## 一、五家答案概览

| 家 | 篇幅 | 用时 | 风格 | 最锐利的观点 |
|---|---|---|---|---|
| cc | 23,125 字 | 220s | 评审员式，先纠错再排期 | **「S1 是 S4 的表现」：8 条路径不是 8 次独立失败，是同一个根因（无身份）被试了 8 次** |
| codex | 9,881 字 | 96s | 一页纸行动派 | **「任何无凭证阶段继续深挖 GUI 绕过的投入，都是 B3 意义上的重复踩坑」** |
| oc | 9,334 字 | 重试后成功 | 架构图纸派 | **「单应用 + 内部路由」：不为 12 个成员建 12 个飞书应用** |
| ac | 8,813 字 | 251s | 矩阵控 | **「绕过飞书反自动化防御既不现实也不正当，正确路线是换通道」+ 17 条全覆盖矩阵** |
| lc | 3,892 字 | — | 当事人 | **访客→二等公民→家庭成员三级跳** |

过程注记：oc 首轮把答案打到 stdout 未落盘即退出（127s），显式加「必须 write_file 落盘」约束后重试一次成功——**给 oc 派活必须显式要求产物落盘**（新行为学发现）。

## 二、跨家共识（按支持家数排序）

| # | 共识 | 支持家 |
|---|---|---|
| 1 | 根因是**身份缺失**（无 OpenAPI 凭证），不是自动化技巧不足；根治唯一路径=主人注册自建应用 | 5/5 |
| 2 | 三阶段路线一致：短期双通道工程化+行为硬化 → 中期凭证+API → 长期灵字辈基础设施 | 5/5 |
| 3 | 行为层 B1-B5 是**零成本最高 ROI**，先修：坑表前置检索、禁重写已验证资产、熔断机械化 | 5/5 |
| 4 | 熔断必须**机械化**（计数器/退出码），不能靠自觉；死路表要**自动更新**（接熔断器写入端） | 4/5 |
| 5 | 凭证申请要**模板化**：无凭证时技能直接输出申请单给主人，而不是反复失败 | 3/5 |
| 6 | MVP=单应用+声明式技能注册+三层记忆文件化+人工确认回流 | 4/5 |
| 7 | 审计计量 JSONL（每任务一行：attempts/result/duration/tokens） | 3/5 |
| 8 | 短期**绝不偷 cookie**：X11 剪贴板+手动粘贴链路里没有凭证可偷，是安全底线不是妥协 | 2/5 明示，其余默认遵守 |

## 三、独家贡献（仅一家提出，裁决与采纳）

| 来源 | 贡献 | 裁决 |
|---|---|---|
| cc | S2 重新定性：**飞书粘贴处理器的能力边界**（真人粘贴也转不了表格/图片），不是「防御」 | ✅ 采纳，修正 lc 材料的错误归类 |
| cc | 差异化定位「飞书之外的半个身位」：把**本地目录（代码仓/实验产物）变成带表格/图/机械校验结论的飞书文档**——豆包结构上做不到 | ✅ 采纳为 lc 飞书身份卖点 |
| cc | 「最后一厘米最小化」：一次任务=一个 docx=一次拖拽；交付话术固定三行不给第四步（决策比动作贵） | ✅ 采纳 |
| cc | 凭证**不进 skills 目录**（会被读进 Agent 上下文），放 `~/.config/lingclaude/feishu.json` + 600 | ✅ 采纳（安全关键） |
| cc | `lcattempt` 契约（退出码 75=熔断换层）+ `lcverify` 交付物机械校验 | ✅ 采纳 |
| codex | `FEASIBILITY.md` 可行性矩阵：路径×结果×失败层×重试许可——「把会话记忆变成资产记忆」 | ✅ 采纳（与死路表合一） |
| codex | `PASTE_PROFILE` 粘贴写作子集（仅 H2/bullet/段落，表格/图/代码一律走 docx） | ✅ 采纳 |
| codex | RESULT 落盘契约一次消灭 W5/W6/W7 | ✅ 采纳 |
| codex | 上下文预算纪律（外部内容先落盘、单次读 ≤200 行、长文档 SubAgent 只回摘要） | ✅ 采纳 |
| codex | 用失败热力图**决定中期开哪些 scope**（数据驱动授权） | ✅ 采纳 |
| oc | 单应用 + `router.py` 按 @成员分发 + `registry.yaml` 声明式技能市场 | ✅ 采纳（否决 lc 原案「12 成员各配插件」） |
| oc | 记忆先 grep 后 embedding（架构正确性优先） | ✅ 采纳 |
| ac | 产物**双写镜像** `_mirror/`（md+docx），防云端被改/删/权限回收 | ✅ 采纳 |
| ac | 任务回执四段式：结论｜产物链接｜风险/降级说明｜耗时+token | ✅ 采纳 |
| ac | 凭证健康监控 cron（token 2h 过期探测+IM 告警） | ✅ 采纳 |

## 四、分歧点裁决

| 分歧 | A 方案 | B 方案 | 裁决 |
|---|---|---|---|
| docx 生成器 | python-docx（lc/ac 实战过） | pandoc（cc 推荐） | **pandoc 为主**（3.1.3 本机已验证存在），python-docx 兜底精调 |
| 中期 API 实现 | 官方 lark-mcp 零代码 | 自写 feishu_client.py + mock 先行 | **先 MCP 速赢验证 scope**，盲区再补自写 client（mock 先行可与凭证申请并行） |
| 12 成员身份 | lc 原案：每成员一套插件 | oc 案：单应用+路由 | **oc 案胜**（12 应用=12 倍审批/维护/越权面） |
| 粘贴链实现 | ac 案：xclip→CDP 合成 ctrl+v | v5 实战案：xclip(主shell)+xdotool XTEST(:1)+CDP bringToFront(后台job) | **v5 实战案**（唯一被实证成功的组合） |

## 五、合并后最终路线图

### D0（今天，0.5 天，零依赖）
1. 死路表+熔断器合一：`skills/feishu-doc/FEASIBILITY.md` + `bin/lcattempt`（熔断自动追加死路记录）【cc Top1 + codex TOP2】
2. 凭证申请单模板 `docs/feishu_credential_request.md`——技能检测到无凭证时直接输出【codex TOP1】
3. `bin/preflight`：9228/ulimit/DISPLAY/pandoc 一条命令探测输出 JSON【lc】

### D1-D2（本周）
4. 技能重构 `skills/feishu-doc/`：SKILL.md 第一屏=死路表；PASTE_PROFILE；`md2docx.sh`(pandoc)；RESULT 落盘契约；交付三行话术【codex+cc】
5. `metrics.jsonl` 启动（lcjob 收尾自动追加）【cc+codex+ac】
6. `bin/lcverify` 交付物机械校验【cc】
7. 用新纪律重跑一次真实飞书任务对比 token（预期 ≥50% 降幅）【codex】

### D3-D7（等主人 30 分钟）
8. 主人注册自建应用（scope 按失败热力图最小化起步）→ `~/.config/lingclaude/feishu.json`(600)
9. lark-mcp 接灵犀总线 → Path C e2e ×10：新建→blocks→图片→设权限→API 读回质检【lc/codex】
10. 凭证健康监控 cron【ac】

### W2-W4（MVP）
11. `ling-feishu/` 单应用机器人：manifest + router + registry.yaml（/文档 /表格 /任务 /妙记 /知识）+ 三层记忆 + audit.jsonl + 高危人工确认【oc 架构+codex 角色】
12. 差异化主打：`/文档` = 本地目录 → 带表格/图/机械校验结论的飞书文档【cc 3.4】
13. 产物双写 `_mirror/` + 回执四段式【ac】

### 长期
14. 灵信飞书事件桥；12 成员按 codex 矩阵分权（灵通/灵知只读、灵克/灵研/灵创读写、灵安守门、其余本期不接）
15. 失败热力图季度复盘 → scope 增量申请

## 六、给主人的唯一请求

全案唯一无法自动化的一步：**飞书开放平台注册企业自建应用（约 30 分钟）**，开通 5 个最小 scope（docx 读写、drive 上传、docx 导入、知识库读、im 发送）。申请单已模板化，点头即发。在此之前，灵克以「docx+一次拖拽」的最低人工成本形态维持运转。

## 七、本轮过程资产（副产品）

- 五家同题答案与材料：`~/yitang/docs/lc_feishu_review_answers/`
- 四家 CLI 无头模式配方：`claude -p "$(cat prompt)" --add-dir <dir> --permission-mode acceptEdits` / `codex exec "$(...)" --sandbox workspace-write --skip-git-repo-check` / `opencode run "$(...)"（必须显式要求落盘）` / `atomcode -p "$(...)"`
- 新增行为学发现：①oc 不给落盘指令就不落盘；②主 shell bash 有 120s 硬超时（sleep 360 会 EXECUTION_ERROR），长 sleep 应放后台 job

# 交接 — 20261008《与长寿智能人谈天》四家 panel 收稿 + v2.1 终稿（新会话讨论入口）

> ⚡ 预算/预警/硬停统一口径见 `docs/runbooks/BUDGET_GUARDRAILS.md`（2026-10-08 固化：
> "500K 硬停"实为无消费方的死标签，会话硬上限 = 20M PAUSE——旧口径勿再引用）。

> 交接人：灵克（lingclaude，本会话）
> 基线：HEAD `f6d8ce4`（2026-10-08 17:13），分支 master，本会话零新提交
> 产物目录：`docs/research/panel_20261008_changshou/`（**untracked 未提交**，是否入库由用户定）
> 现场无关改动：`lingclaude/engine/tool_registration.py`（M）非本会话所为，本会话全程未动

## 一、任务一句话

用户将以 `FINAL_synthesis_v2.1.md` 为底稿继续讨论。**新会话第一步：读本文件 §三/§四 + v2.1 稿即可开聊，不必重读四份外稿。**

## 二、产物清单（2026-10-08 17:16 实测 ls，全部存在）

| 文件 | 大小 | 说明 |
|---|---|---|
| `FINAL_synthesis_v2.1.md` | 25.7KB | **讨论底稿**（四方全收稿版，含 oc 补录 14 处修订 + §12 八方仲裁表 + §13 十项 M0 待核验清单） |
| `oc_rescued_evidence.md` | 115.9KB | oc 证据底稿（opencode.db event 表救回，26 次工具调用全程快照，可溯源复核） |
| `FINAL_synthesis.md` | 21.7KB | v2.0（三方版，留档） |
| `answer_ac.md` | 62KB | ac 稿（740 行，5 项一手实测，四家最扎实） |
| `answer_codex.md` | 23.7KB | codex 稿（记忆卡修订链/导出包规格/双时间轴） |
| `answer_atomcode.md` | 21.2KB | atomcode 稿（双轴时间线/时代锚点题库，3 轮联网） |
| `LESSONS.md` | 6KB | **五节踩坑与正确配方（重跑外部 CLI 前必读）** |
| `materials/` + `unified_prompt.md` | — | 四家统一提示词 + 输入材料浓缩 |
| `run_on_host.sh` / `logs_*.txt` / `synthesis_draft.md` | — | 第一轮宿主脚本/日志/v1 骨架（历史留档） |

**oc 无终稿**：调研完成（18min，28+ 来源）但落盘步骤未执行（已知毛病），exit 0 无产物；证据已全量救回并提炼进 v2.1。

## 三、核心结论速览（出处见 v2.1 各节标注）

1. **ac 三大纠错**：① 四源材料引用的"禁模拟老年人亲属"条款**仅存在于征求意见稿**，21 号令正式稿（2026-07-15 施行）收窄至未成年人，ac 实取 gov.cn 公报全文并做义务矩阵（22 条上线安全评估 / 26 条算法备案最易漏，罚则 1-20 万）；② OpenRouter 免费池实测仅 16 free，四源推荐 ID 大面积失效 → 策略改为**付费极低价 deepseek-v4-flash-0731 为主**（单次访谈全链路 <0.01 元）、免费池仅作非隐私降级；③ 音色克隆安全设计：TTS 只复读长者本人原话（timecode 命中校验，越界拒绝）、样本取自访谈录音、身故自动失效。
2. **oc 10 项补录**（全部 ✅ 标注进 v2.1）：CosyVoice 3 升并列首选（3s 零样本/18 方言 instruct/150ms 流式）；工信部适老化原文参数（字≥30dp、行距≥1.3、对比度 4.5:1、验证码需语音替代）；小智回忆录竞品官网全文（家属代述逝者形态、"不用来训练AI"）；21 号令央视官方解读（"不涉及持续性情感互动不在管理范围内"→ B1 纯问答或落范围外）；IMA OpenAPI 端点/Header/接口边界坐实（只增查、无改删导）等。
3. **仲裁结论**：四方（ac/codex/atomcode/oc证据 + lc 前轮实测）**零推翻性冲突**，全部属强化/细化/新增佐证。

## 四、遗留三个讨论锚点（新会话从这里接）

1. **TTS 双引擎并列首选**（GPT-SoVITS vs CosyVoice 3，按方言/流式需求分场景）是否采纳 → v2.1 §6.3
2. **B1 豁免定性**（央视解读有利，但需属地网信确认）是否影响 M0 排期 → v2.1 §2.1 + §13⑪
3. **小智"家属代述逝者"形态**跟不跟（合规敏感：涉逝者声音/人格权益）→ v2.1 §1.2
4. 若启动 M0：按 v2.1 §13 十项待核验清单逐项打钩（语料抓取脚本/IMA 探针/ASR 实测集等）

## 五、SOP 速查（重跑外部 CLI 前先读 LESSONS.md 全文）

- 四家配方：`claude -p` / `codex exec --sandbox workspace-write` / `opencode run` / `atomcode -p`；一律 `run_in_background` **纯形态**（命令开头命中白名单，禁任何 `echo;`/`timeout`/`nohup` 包装——包装会掉进隔离沙箱制造"全网断"假象，LESSONS §二）
- prompt 注入用 `$(cat 文件)`；落盘必须显式写入指令 + **oc 需双保险**（显式指令仍可能不落盘，exit 0 后要 ls 验收，LESSONS §四）
- 证据取证：正文在 `opencode.db` **event 表**，`aggregate_id like 'ses_xxx%'` 前缀过滤 + 同 part 去重；storage/ 目录为空是常态；先 `cp` 到 /tmp 再只读查询（LESSONS §五）
- 网络中断协议（用户指令）：**等 3 分钟重试，至少 2 轮**
- 后台队列 FIFO：一个长 job 会堵住后续所有 job，等待期用有效产出消磨，勿密集轮询

## 六、受阻项（非本任务引入，原样移交）

- `sub_agent` 双路不可用：acp 未配端点 + inprocess 上游 400（连最小测试都失败）→ 已验证替代路径 = 宿主 CLI 四家插片（本 panel 即产物）
- `bash_lingxi` stdout 全空（echo 都丢），不可用
- `web_search` 持续返回空结果；`web_fetch` 直连可用（oc/ac 的联网检索走的是各自 CLI 通道，不受影响）

## 七、面板与收尾状态

- 任务面板已清空（4 项 completed + 1 项失效 cancelled）
- panel 目录 untracked：**建议**用户讨论定稿 v2.x 后随最终方案一并提交入库（避免半成品进历史）；若新会话在不同容器恢复，需先确认该目录已提交或已随宿主持久化

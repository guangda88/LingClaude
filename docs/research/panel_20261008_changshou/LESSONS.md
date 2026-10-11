# 外部 Agent 面板调用——踩坑与正确配方（20261008）

> 背景：《与长寿智能人谈天》四方调研面板。第一轮四家全灭（13:02-13:07），第二轮按正确配方三家起活。
> 根因是本机已在案的两个机制被我违反，此文档为防复发锁。

## 一、第一轮翻车的真相：不是网络断了，是我污染了白名单判定

### 机制（来源 .atomcode/memory.md:15 + lingclaude/core/policies/sandbox_policy.yaml:19-25）
- 本机 bash 工具默认 `bwrap --unshare-net --ro-bind /`（无网+根只读）
- 但**白名单形态**的命令共享主 netns、可写协作目录：
  - `atomcode` / `codex exec` / `crush run` / `claude -p` / `opencode run`（五条，命令开头即判）
  - 另有只读网络探针：`curl -sI`、`git` 全链等

### 铁律：白名单是【全链/开头】判定，包装即掉出
| 形态 | 结果 |
|---|---|
| `claude -p "..."` 纯开头 | ✅ 主 netns，能出网 |
| `cd xx && claude -p "..."` | ❌ 整条掉出白名单 → unshare-net → DNS 全挂 |
| `timeout 900 claude -p "..."` | ❌ 同上 |
| `nohup claude -p ... > log 2>&1 &` | ❌ 同上 + 根只读写不了 log |
| `echo "===="; curl -sI url` | ❌ echo 不豁免，整条隔离 → **测出的"断网"是假的** |

### 第一轮四家死法对账（logs_*.txt 存档）
| 家 | 死因 | 对应机制 |
|---|---|---|
| claude(ac) | `API Error: ESERVFAIL`（DNS） | `timeout 900` 包装 → 掉出白名单 |
| codex | `Read-only file system`（写 ~/.codex 失败） | 包装掉名单 → ro-bind 生效 |
| opencode | `FileSystem.open ~/.local/share/...` 失败 | 同上 |
| atomcode | `DNS error: Device or resource busy` | 同上 |

### 更深的坑：用污染观测下「全网断」结论
第一轮诊断时我用 `echo "===="; curl` 复合链测出「外网全 000、localhost 全拒、ss 无监听」，
据此向用户报告「沙箱环境级无网络」。实际纯净 `curl -sI https://www.baidu.com` = **200 OK**。
这违反 SANDBOX_OBSERVATION_RUNBOOK 铁律 O1：「极端结论出现时先怀疑测量仪器」。
9/30、10/1 已两次犯过同类错（memory.md:30 在案），本次是**第三次复发**。

## 二、正确配方（第二轮验证有效）

### 启动（必须 run_in_background = 沙箱外通道 + 命令开头命中白名单）
```bash
# ✅ 正确形态：prompt 用 "$(cat 文件)" 注入，追加输出要求，无任何包装
claude -p "$(cat prompt.md)\n\n输出写入 /tmp/panel_ac.md"
codex exec "$(cat prompt.md)\n\n..." --sandbox workspace-write --skip-git-repo-check
opencode run "$(cat prompt.md)\n\n（oc 铁律：不给落盘指令就不落盘，必须显式写明输出文件）"
atomcode -p "$(cat prompt.md)\n\n..."
```

### 生命周期管理
- 长任务用 `run_in_background`，返回 job_id 后**不要密集轮询**（会触发原地打转熔断）
- 用 `sleep N` 后台任务垫间隔，或一次合并检查多个 job_status + `ls /tmp/panel_*.md`
- codex/opencode 长任务 5-15 分钟属正常（0923 面板 900s 超时护栏可参考）

### 已知残留问题
- `claude -p` 第二轮纯形态仍 exit 2 秒退（13:0x），与包装无关，原因待查（PONG 微测 9f2150de 排查中）
- atomcode 第二轮启动后 pending 排队较久，需观察是否真跑起来

## 三、面板工作流标准 SOP（下次直接照抄）
1. 材料+统一提示词落盘 `docs/research/panel_<date>_<topic>/`（materials/ 子目录）
2. 输出文件统一 `/tmp/panel_<家>.md`（白名单可写路径）
3. 四家 run_in_background 纯形态拉起，timeout ≥ 2400s
4. 间隔检查（≥3分钟一次），不轮询
5. 收稿后 `ls -la /tmp/panel_*.md` 核对非空，读稿做仲裁汇总
6. 复盘沉淀回本目录 LESSONS.md


## 四、第三轮补录（lc，16:30-16:55）
- ac/oc 用 run_in_background 纯形态拉起：ac ✅ 8分钟落盘 /tmp/panel_ac.md 且同步副本 answer_ac.md；oc ❌ exit 0 但未落盘——stdout 末句 "Research done. Now writing the deliverable."，**写入动作没执行**。教训：oc 即使给了显式落盘指令，也要在交付检查里核对文件存在+非空，缺了先用其 tool-output（~/.local/share/opencode/tool-output/）评估能否救回，再考虑重跑。
- 后台队列是 FIFO：一个长任务会堵住后面的 sleep 垫片（全部 pending）。等待期用"有效工作消化时间"（精读已收稿件、预写综合稿），比反复 job_status 更防原地打转熔断。
- 前台 sleep 单条上限 120s（bash 工具超时），`sleep 115; 检查` 是合规垫片形态（命令开头 sleep 不在白名单，但不触网，无碍）。
- 本轮 Final synthesis 用 ac 稿做骨架（一手验证最多），codex(修订链/导出物规格)与 atomcode(导出规格/路由复用建议) 增量并入，仲裁表收进 FINAL_synthesis.md §12。

## 五、oc 证据取证配方（2026-10-08 v2.1 补录，新一轮教训）
- **现象**：oc（opencode run）exit 0 但声称的交付物未落盘——stdout 最后一句是"now writing the deliverable"，写入动作随会话结束丢失。
- **取证路径（已验证）**：正文不在 storage/（为空），在 `~/.local/share/opencode/opencode.db` 的 **event 表**（aggregate_id=完整sessionID，type=message.part.updated.1）。注意：① session 表的 id 是截断的（ses_ee5585df），event 表是全量（ses_ee5585df...fffe）→ **必须用 like '前缀%'** 不能等值匹配；② 同一 part.id 多次更新 → 按 id 保留最新版去重；③ 只读查询先 `cp db /tmp/` 再开（WAL 库直接连原文件有风险）。
- **副产品**：tool-output/ 目录下按时间戳的大 JSON = oc 抓的原始 API 响应（OpenRouter/Zen 模型清单），可与正文互证。
- **SOP 增补**：给 oc 的落盘指令必须在 prompt 里**双保险**——"最后一步用 write 工具写文件 + 写完后 cat 该文件确认行数"，stdout 收到确认行才算收稿。

## 六、R3 补录（2026-10-10，EXTERNAL_AGENT_DISPATCH_RUNBOOK 立档后首次全程合规）
- 四家纯形态派发实测时效：codex ~2min / ac ~4min / cc ~5min 落盘——R2「全灭」确认为包装形态所致假象。
- oc 取证配方两处结构修正（本次实测）：①text 正文在 `data.part` 嵌套内（非 data 顶层）；②`part.time` 为 int 非 dict（防御 isinstance 再取）；③session 匹配仍用 aggregate_id like 'ses_xxx%' 前缀。
- oc 新死法：研究循环超时未成文（exit 0，event 表证实 text 仅 prompt 回显+开场白，其余全是 tool 事件）——对策=**时间盒条款**追加进 prompt：「本地工具调用≤4次，成文优先于完备」。
- 权威 SOP 已升级为 docs/runbooks/EXTERNAL_AGENT_DISPATCH_RUNBOOK.md（铁律10条+配方速查+故障取证），本文档保留为历史档案与取证细节来源。
- R3 后记（10-10 深挖）：oc exit 0 未落盘的最终根因=**headless 权限拒绝**（permission.edit 默认 ask，无人批准→write 自动拒），正文在 event 表 `state.input.content` 完整救回（6538B）。修复=项目级 opencode.json 预置 permission.edit=allow。至此 oc 三死法齐：研究循环/权限拒/假报成文，权威速查见 Runbook §六。

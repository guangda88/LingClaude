# 外部 Agent 派发 Runbook（四家面板调用标准机制）

> 版本: 2026-10-10 v1.1 ｜ 上游依据: docs/research/panel_20261008_changshou/LESSONS.md（10-08 实证）
> 本文档是外部 Agent（cc/codex/oc/ac）调用的**唯一权威 SOP**。派发前必读，违反任何一条铁律的后果都已付过学费（10-08 第一轮四家全灭 + 10-10 R2 复发一次）。

## 一、核心机制（为什么必须纯形态）

本机 bash 工具默认 `bwrap --unshare-net --ro-bind /`（无网+根只读）。
**白名单按【命令开头】判定，命中即整条落在主 netns（能出网），包装即整条掉出（无网+只读）：**

- ✅ 白名单五条：`atomcode` / `codex exec` / `crush run` / `claude -p` / `opencode run`
- ✅ 只读网络探针：`curl -sI`、`git` 全链
- ❌ 任何前缀/包装（`cd &&` / `timeout` / `nohup` / `env X=Y` / 管道 / bash 脚本 / `echo;`）→ unshare-net → **DNS 全挂 + 写 HOME 报错**

⚠ 由此推出的观测纪律：**在包装形态（隔离沙箱）里测端口/网络，结果一律不可信**——隔离 netns 看不到主 netns 监听，「connection refused」「全网断」多为假象（10-10 R2 曾据此误诊 proxy3 活尸并误杀进程）。服务健康以宿主侧 `service_health_snapshot.py` / 服务自身日志为权威。

## 二、铁律表（逐条血泪）

| # | 铁律 | 违反后果 |
|---|---|---|
| 1 | 只用 `run_in_background` 派发（沙箱外通道），timeout ≥ 2400s | 前台 bash 120s 上限截杀 |
| 2 | 命令开头即白名单形态，**零包装**（env 前缀、cd、timeout、管道、脚本都算包装） | 整条掉沙箱 |
| 3 | prompt 用 `"$(cat 文件)"` 注入，不手打长串 | 转义灾难 |
| 4 | 输出统一**项目内** `data/cross_agent/<date>_round<N>/deliverable_<家>.md`，prompt 内显式落盘指令（v1.1 起，弃 /tmp） | /tmp 触发 oc `external_directory` 自动拒 + 定期被清理丢稿 |
| 5 | oc 三重保险：①输出路径写**项目内**（`/tmp/*` 属 external_directory，headless 自动拒——R3 探针实锤，这才是三次未落盘的真根因）②`opencode.json` 预置 `permission.edit=allow`（已落地）③prompt 双保险（write+读回+DELIVERED 行），无 DELIVERED → §五取证救稿 | oc exit 0 但不落盘（三次实证） |
| 6 | 不密集轮询 job_status；等待期做有效工作或 `sleep 115` 垫片 | 原地打转熔断 |
| 7 | 后台队列是 FIFO，长任务堵后面所有任务；检查合并成一条 | 排队假死误判 |
| 8 | 沙箱内观测到「断网/端口拒」先怀疑测量形态，换纯形态复测再下结论 | 假阳性误诊（SANDBOX_RUNBOOK O1） |
| 9 | claude 优先显式 `--model sonnet`（默认路由可能落到会崩的模型） | 内存崩/秒退 |
| 10 | codex 加 `--sandbox workspace-write --skip-git-repo-check` | 写 HOME 失败退出 |

## 三、标准流程 SOP（照抄即可）

```
1. 建目录 data/cross_agent/<date>_round<N>/，写四份 prompt_*.md（同一任务正文），输出路径=同目录 deliverable_<家>.md（项目内！）
2. 四条 run_in_background 纯形态并行拉起（配方见 §四），记 job_id
3. 等待期转有效工作（预写汇总骨架/读历史稿件），≥3min 才查一次，检查合并一条：
   ls -la data/cross_agent/<date>_round<N>/deliverable_*.md
4. 收稿验收：文件存在+非空+行数合理；oc 无 DELIVERED 行→走 §五取证
5. 汇总仲裁：以一手事实最多者为骨架，各家增量并入，分歧列表格
6. 复盘沉淀：新坑回写本文件 + LESSONS.md，project_memory 加陷阱条目
```

## 四、四家配方速查（2026-10-10 实测版）

```bash
# claude（显式模型提稳）
claude -p "$(cat prompt_cc.md)
输出写入 data/cross_agent/<date>_round<N>/deliverable_cc.md" --model sonnet

# codex
codex exec "$(cat prompt_codex.md)
输出写入 data/cross_agent/<date>_round<N>/deliverable_codex.md" --sandbox workspace-write --skip-git-repo-check

# opencode（前提：项目级 opencode.json 已配 permission.edit=allow + 输出路径项目内，见铁律4/5；prompt 尾部带双保险指令）
opencode run "$(cat prompt_oc.md)"

# atomcode
atomcode -p "$(cat prompt_ac.md)"
```

已知残留：claude 偶发纯形态 exit 2 秒退（10-08 在案，原因未查，重试一轮再弃）；atomcode 启动排队可能较久（FIFO）。

## 五、故障取证

- **oc 未落盘**：正文在 `~/.local/share/opencode/opencode.db` event 表（type=message.part.updated.1，aggregate_id 用 `like 'ses_xxx%'` 前缀匹配；先 cp 到 /tmp 再查）；tool-output/ 大 JSON 可互证
- **全灭/秒退**：先查本 Runbook §二铁律逐条对账（10-10 R2 即 env 前缀+包装复发），再查各家日志，**不要先怀疑网络**
- **需要网络探测**：只许纯 `curl -sI <url>` 单链，不许任何复合

## 六、历史复发记录（证明机制必要性）

- 2026-10-08 第一轮：四家全灭（timeout/nohup 包装）→ LESSONS.md 立档
- 2026-10-10 R2：复发（echo 管道喂 oc、env 前缀跑 codex、包装跑 ac）→ 误诊「四家全灭+proxy3 活尸+无出口」三连假阳性 → 本 Runbook 立档
- 结论：**教训不进 runbook 就会复发**。派发前 `grep -l "EXTERNAL_AGENT" docs/runbooks/` 找到本文，照 §四抄。
- 2026-10-10 R3：四家纯形态 2-5 分钟全交稿（codex ~2min / ac ~4min / cc ~5min），配方修复证实有效。但 oc 两轮均 exit 0 未落盘——取证揪出**第三死法：headless 权限拒绝**——oc 默认 `permission.edit=ask`，非交互无人批准 → write 被自动拒（event 表 seq258: "The user rejected permission to use this specific tool call"），正文其实已生成。修法探针两步定位：探针1（写 /tmp）仍拒，stderr 明示 `permission requested: external_directory (/tmp/*); auto-rejecting` → **真根因=/tmp 属项目外目录，非 edit 权限**；探针2（写项目内）24 秒通过（stdout=PROBE2-WRITE-OK）。终修=输出路径全面改项目内（铁律4）+ `opencode.json` permission.edit=allow 双保险（已落地，bash 142 次调用实测本就放行，未扩大面）。存量稿从 event 表 `state.input.content` 救回（6538 字符完整，已归档 deliverable_oc.md）。
- oc 三种死法速查：①研究循环不成文（时间盒条款治）②权限拒绝写不进（opencode.json 治）③stdout 声称成文但实际没写（event 表取证治）。**判定交付只认文件非空+DELIVERED 行，不认 stdout 叙事。**
- 2026-10-16 RSI轮：cc/codex/ac 纯形态 3.5-4.5min 全交稿；oc 两轮均败且 db 无新 session（死在会话创建前）——首轮 219s exit 1、重试 1.1s 秒死；现场发现 10-11 僵驻 `opencode serve --port 4099` 常驻进程，疑似占锁/抢 DB → **oc 第 4 种死法候选：stale serve 僵尸进程**。（→ 2026-10-17 晨已证伪并破案，见下条）
- 2026-10-17 晨 oc 复盘（**第4死法证伪，真凶=第5死法：env 污染型 JSON 崩溃**）：杀僵尸后 probe 仍 1.0s 秒死 → 僵尸清白（且该 serve 系 oc-serve.service 正当部署，Restart=always 会复活，**禁止再误杀**）。真凶实证：`~/.ling_keys.env:60` 行尾 `#` 注释被 env 装载器灌进 `PROXY3_LING_SHARED_KEY` 值 → oc 配置 `${env:PROXY3_LING_SHARED_KEY}` 展开成脏文本炸毁 JSON → 启动即崩（三次探针 stderr 同尺寸 62037B 逐字节一致=env 静态污染指纹）。修法：①keys 文件行尾注释只许独立成行（已修，备份 .ling_keys.env.bak.fix_20261017_070952）②oc 全局配置 apiKey 临时明文化绕开本代 runner 的脏 env 快照（备份 opencode.json.bak.plain_20261017_071055；lingclaude 重启后可改回 `${env:...}`）③派发前 env 自检：`python3 -c "import os;assert '#' not in os.environ.get('PROXY3_LING_SHARED_KEY','')"`。修复后 6 天断流终结（首个新 session ses_eb90435e）。完整证据链：data/cross_agent/20261016_rsi_round1/oc_forensics/DIAGNOSIS_oc_death4.md。**观测陷阱双坑再实证**：主会话 bwrap 内 curl/ss 网络观测全假象（铁律 O1）、Bun 假 MemoryExhaustion（沙箱 1GiB 内存上限）——oc 类诊断一律走 run_in_background 通道取证；zen-free 档步进极慢（~34 token/步），oc 派发 timeout ≥1800s。

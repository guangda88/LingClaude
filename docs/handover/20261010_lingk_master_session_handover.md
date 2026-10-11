# 灵克主会话交接快照（2026-10-10 17:2x）

> 交接源：灵克 master 分支主会话，302+ 轮，input_tokens 1187万/800万（超预算 48%）→ 退役。
> 继任会话：你。读完本文档后按 §六 自验，然后待命接指令。

## 一、今日（2026-10-10）工作全景（全部已完成并验证）

| 轮次 | 主题 | 关键交付 |
|---|---|---|
| R1 | 灵克 vs CodeArts 分析验证 | 实测 386文件/92,730行（报告夸大为500/21万）；docs/INDEX.md 381篇索引+scripts/docs_index.py --check；task_router 覆盖率 67%→75%（补4用例） |
| R2 | 四项补救 | CI coverage job（≥70 gate，`scripts/ci_coverage_gate.py` 本地等价）；minimax 定时炸弹用例修复（夹具硬编码 2026-10-06 已过期→now+8d 动态化）；journal 债务 add+resolve 闭环；AGENTS.md:69 升格注记（gitignore 本地文件） |
| R3 | 外部 Agent 调研+机制 | **白名单纯形态机理**（命令开头 `claude -p`/`codex exec`/`opencode run`/`atomcode -p` → 主 netns 能出网；任何包装→隔离沙箱全网断）；Runbook v1.1=docs/runbooks/EXTERNAL_AGENT_DISPATCH_RUNBOOK.md；LESSONS.md §六；四家交付稿在 data/cross_agent/20261010_round3/ |
| W1 | 部署复杂度 | scripts/systemd/{lingk-webui.service,lingk.target} + scripts/install_lingk_systemd.sh（幂等含前端新鲜度告警）+ bootstrap_env.py --print-remediation（保持只读 doctor） |
| W2-A | GUI 叙事修正 | webui-server 对话主链已桥接灵克引擎（chat_api.rs→api.py:468/563/360 三端点+X-API-Key 零漂移实测）；webui-server/README.md；sessions 前后端已全量实装（cc 稿"需开发"判断是漏看） |
| W6 | 冷备降级链 | scripts/yai/{credentials_snapshot.sh,yai_burnout_monitor.py,yai_relogin_onelink.sh,README.md}；安装器内置第0份快照；fallback 链既有资产确认（route_selector.py:214+链尾:165） |
| R9×3 | 接力实验 | 一跑修4bug（PATH缺~/.local/bin→resolve_bin；无网netns→run_in_background托管；log前置；_headless_turn吞错repl_turn.py:188）；二跑零干预4分钟闭环；三跑可见版（tmux `run -i`） |

## 二、未提交改动（全部待用户审，勿擅自 commit）

新文件：scripts/{docs_index.py, ci_coverage_gate.py, bootstrap_env.py, install_lingk_systemd.sh, systemd/lingk-webui.service, systemd/lingk.target, yai/*4件}、docs/INDEX.md、docs/runbooks/EXTERNAL_AGENT_DISPATCH_RUNBOOK.md、webui-server/README.md、data/coverage/baseline_20261010.md、data/cross_agent/20261010_round3/*
修改：.github/workflows/ci.yml（+coverage job）、tests/test_task_router.py（+4用例+夹具动态化）、scripts/session_relay_orchestrator.sh（PATH 修复）、scripts/relay_queue.json
遗留 M（前会话的，勿碰）：lingclaude/core/*、sandbox_policy.yaml 等 T9/T10/T11 批次

## 三、宿主待办（用户五步，沙箱不可执行）

1. `bash scripts/install_lingk_systemd.sh`（幂等装配，内置冷备快照）
2. `python3 scripts/bootstrap_env.py`（期望 4/4 tcp=UP）
3. W6 双 cron（命令见 scripts/yai/README.md「宿主安装」节）
4. GUI 走查：浏览器 127.0.0.1:23458
5. （可选）前端重建：8 个未编译文件进二进制 `cd webui && npm run build && cd ../webui-server && cargo build --release`

## 四、关键陷阱（血泪浓缩，违反必翻车）

1. **长驻进程必须在主 netns 拉起**：run_in_background 是白名单通道；普通 bash 拉的进程在无网 netns，拉出的会话必空响应
2. **外部 Agent 派发必走 Runbook v1.1**：零包装；oc 输出路径必须项目内（/tmp=external_directory 自动拒，三死法真凶）
3. **/tmp 会被清理**：正式产物一律项目内（docs/handover/ 惯例）
4. **proxy3 沙箱内探活不可信**（netns 假阳性，本会话曾误杀 PID 2488303——教训已入 project_memory）；以宿主 service_health_snapshot 为准
5. bash 工具单调用 ≤120s；`pkill -f` 会自匹配（命令文本含目标字样时自杀，用 `[x]` 字符类或拆调用）
6. YAI 路由现状：JWT 已复活（suppress_origin 补丁），凭据 /tmp/yai_tokens.env；TRAE 已判死摘除

## 五、活跃进程

- R9 daemon PID 3197759（run_in_background 托管，2h 窗口约 18:29 到期；长期值守建议宿主 install_relay_systemd.sh）
- tmux socket /tmp/tmux-lingrelay.sock：窗口 relayB（上轮可见实验会话，待命中）

## 六、继任会话自验（读完立即做）

把以下四题答案写入 /tmp/handover_ack_20261010/REPLY.md，然后待命：
1. W1 交付的两个 systemd 单元叫什么？
2. R9 二跑耗时多少、干预多少？
3. opencode 三连写盘失败的真凶是什么？
4. 宿主待办第 1 步执行什么命令？

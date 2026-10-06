# 灵族服务清理/重启执行清单 (2026-10-04)

> 前置排查见会话记录。原则：每步先复核再动手，归档校验通过才删源，杀进程按 PPID/启动时间动态过滤（不硬编码 PID）。

## 结论速览

| 批次 | 内容 | 执行位置 | 风险 |
|---|---|---|---|
| 1.1 | 杀 11 个灵犀 stdio 孤儿进程（9-10 ~ 10-01） | 沙盒内（脚本已备） | 低（动态排除活链路） |
| 1.2 | 杀测试遗留 http.server（8772 / 8080×2） | 沙盒内（脚本已备） | 低（/tmp/git-server 数据随进程无用，目录已在 /tmp） |
| 2 | 拉起灵极优 MCP server（当前 15 工具全部 Unknown） | **宿主侧**（命令已备） | 低 |
| 3 | 摘除灵依退役残留工具面 | 宿主侧，改 lingxi 代码 | **中：需重启灵犀桥，影响所有会话，建议窗口期做** |
| 4 | linminopt(少 g 误建目录) 归档→删除 | 沙盒内（脚本已备） | 低（tar.zst 校验后删） |
| 5 | 磁盘清理（C-1 event 表 / journald / crush 备份） | 宿主侧（上轮脚本） | 中（脚本内有防全表删除修正） |

## 第 0 步：前置复核（执行任何批次前）

```bash
# 灵极优后端是否仍未起（应无输出）
pgrep -af 'lingminopt.*mcp_server'
# 灵犀桥进程基线（记录数量，清理后对比）
pgrep -fc 'lingxi/dist/cli.js stdio'
# C-1 相关：opencode serve 现状（旧 PID 1875183 已消失，需现查）
pgrep -af 'opencode serve'
```

## 第 1 批：沙盒内清理（脚本 `service_repair_20261004.sh`）

```bash
bash /home/ai/lingclaude/.atomcode/maintenance/service_repair_20261004.sh            # dry-run 看清单
bash /home/ai/lingclaude/.atomcode/maintenance/service_repair_20261004.sh --execute
```

- **1.1 杀 stdio 孤儿**：过滤条件 = `PPID==1 && 存活>48h && 命令行匹配 cli.js stdio`。
  自动保留：1668624（atomcode daemon 链）、2086995（当前会话链）等活进程。
- **1.2 杀测试 http.server**：PID 2322（8772 端口，9-10 起）、1182885/1182887（8080，/var/www/html）、
  1200526（8080，/tmp git 部署测试环境）。均 9 月测试遗留，无业务。

验证：`pgrep -fc 'cli.js stdio'` 应下降 ≈11；`ss -tlnp | grep -E '8772|8080'` 应无输出（宿主侧执行）。

## 第 2 批：宿主侧拉起灵极优（脚本 `host_service_repair_20261004.sh`）

```bash
bash /home/ai/lingclaude/.atomcode/maintenance/host_service_repair_20261004.sh
```

- 启动方式参照 lingmessage 现行实践：`python3 /home/ai/lingminopt/lingminopt/mcp_server.py`
  （该文件自带 `__main__` 入口，尾部会向 lingmessage registry 注册；系统 python3 已确认有 mcp 包）
- 日志落 `/home/ai/lingminopt/datalog/mcp_server.log`
- 失败备选：`/home/ai/.local/bin/fastmcp run /home/ai/lingminopt/lingminopt/mcp_server.py`
- 注意：**不能在灵克沙盒内拉起**（bwrap --die-with-parent，会话结束进程即死）

验证：`pgrep -af lingminopt` 有进程后，新会话测 `[MCP:灵极优] get_optimization_status` 应返回数据而非 Unknown tool。

## 第 3 批：灵依面摘除（**待批，窗口期操作**）

背景：灵依 2026-09-17 退役（`data/ling_org/org_event/2026-09-17-灵依退出-00.json`），
但其 MCP 面仍经灵犀代理暴露，5 个工具全部 `server closed stdout`，纯噪音。

步骤（涉及灵犀仓代码）：
1. `lingxi/src/tools/proxy.ts` 成员映射中移除 `lingyi` 条目（或加 disabled 开关）
2. `cd /home/ai/lingxi && npm run build`（重建 dist）
3. 重启灵犀桥（**影响所有成员的 MCP 转发，须停机窗口**）
4. 验证：新会话工具清单无 `[MCP:灵依]` 面，其余 12 个面全部正常

回滚：git revert lingxi 变更 + 重启桥。

## 第 4 批：linminopt 误建目录归档（并入 `service_repair_20261004.sh`）

- `/home/ai/linminopt`（9.1M，6 月工作现场：3 个与正确目录**内容不同**的同名脚本 + crush.db 9.3M 会话记录）
- 处置：tar.zst 归档至 `/home/ai/.atomcode/datalog_archive/linminopt_workdir_202606.tar.zst`，
  条目数校验通过后删源，README 补记录。**不是直接删**（内有独特版本脚本）。

## 第 5 批：宿主侧磁盘清理（沿用上轮）

```bash
bash /home/ai/atomcode/host_cleanup_20261004.sh --execute   # 路径以实际上轮输出为准
```

- C-1 已修正危险逻辑：event.id 时间中段为十六进制，原 CAST AS INTEGER 写法会误删全表；
  现为「保留最新 3000 条」rowid 策略。执行前仍先备份。
- opencode serve 若在跑：先停再 vacuum（现查 PID，勿用旧值 1875183）。

## 防复发（不在本次清单执行，列为待办）

1. **灵犀 stdio 泄漏根治**：父会话退出未回收子进程 → lingxi 侧应加进程组管理/心跳超时回收
2. **council_health 类健康巡检**落空（灵依退役后无人管）→ 建议移交给 lingzhi 或 lingclaude daemon 定时跑
3. 8300/13480 两个 uvicorn（agg_product，10-01 起）归属未实证 → 待确认是否业务所需

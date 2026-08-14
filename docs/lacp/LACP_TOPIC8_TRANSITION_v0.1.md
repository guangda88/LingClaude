# LACP v0.6.0+ 议题8 Transition 章节

> **状态**: 草案 v0.1 — 灵克主理，待灵安 review（议题8 C6 任务）
> **依据**: 议题8 决议 v0.9（fdd3c9a4 thread，2026-08-14 19:58 CST）
> **性质**: LACP v0.6.0 冻结（7/30 council）后的首个 transition 增补，不改冻结章节语义，新增"合并过渡期"规则

---

## T1. 适用范围

本章节仅适用于**议题8 合并过渡期**（2026-08-14 ~ 2026-08-31），即灵通+（lingflow_plus）并入灵通（lingflow）期间。Phase C（8/31）完成后本章节自动失效，相关持久规则沉淀至对应主章节。

## T2. 过渡身份规则

1. **双身份共存期**：8/14-8/30 期间 lingflow_plus 身份继续有效（运行中 daemon/SDT 不中断），lingflow 逐步接管模块所有权。
2. **身份切换点**：Phase C（8/30-8/31）lingflow_plus CRUSH.md 标记为历史（指向 lingflow/CRUSH.md），此后 LingBus 发送者身份 lingflow_plus 视为非法（灵信侧校验）。
3. **导入代理**：迁移模块在 lingflow_plus/ 原位保留 `from lingflow.* import *` 代理，代理层存活期 = 过渡期，8/31 后删除。

## T3. 写授权过渡规则（verify_write_auth 扩展）

1. **合并保护路径**：`lingflow/`、`lingclaude/governance/`、`lingresearch/scripts/` 在过渡期内纳入受保护写入范围（同 CRUSH.md/AGENTS.md 级别）。
2. **新增授权来源**：`merge_reviewer_ack` — 决议 v0.9 指定的 reviewer（灵克/灵安）在 fdd3c9a4 thread 对特定 PR 的 ack 消息，构成对该 PR 文件列表的写授权，TTL 48h。
3. **审计标记**：过渡期 write_auth_log 记录填 `merge_topic = "topic8"`，8/31 后该字段停止写入（保留历史）。

## T4. 数据迁移规则

1. **lingmemory record**：owner 变更采用 `owner + owner_history` 双字段方案（灵研 migrate_lingflow_plus_records.py 实现），迁移必须经灵安 L10-D gate 真实调用（非自声明）。
2. **LingBus owner 表**：灵信联署，迁移前导出 pending 消息，迁移后验证新 owner 可正常 poll/reply（零消息丢失）。
3. **归档**：全量压缩 + sha256 校验和备案至 `/home/ai/archive/lingflow_plus_20260831/`，归档后只读（chmod -R 444），灵安完整性验证。

## T5. 回滚规则

任一 Phase 失败时：还原 systemd service 文件 + 删除导入代理 + 恢复 lingflow_plus/ 原位文件（`.bak_merge_topic8` 备份）。回滚由灵克/灵安双签确认，族长终核。

## T6. 与冻结章节的关系

本章节不修改 v0.6.0 冻结内容（§1-§6）。冲突时以冻结章节为准；本章节仅补充冻结期未覆盖的"成员合并"场景。

---

— 灵克（lingclaude），2026-08-14，议题8 C6 任务交付

# docs/audit 审计报告索引

> 本目录收录 lingclaude 各轮审计报告。命名约定：`AUDIT_YYYYMMDD_主题.md`。
> 最新一轮严格审计：2026-09-28（见下）。
>
> **产物目录约定**：外部 Agent（codex/opencode/atomcode 等）审计产物统一落 `.audit/`（项目根，base_dir 内，避免沙箱路径拒绝）；本目录 `docs/audit/` 只收录**正式归档的审计报告**（经人工复核后的版本），外部 Agent 的原始输出先放 `.audit/` 再择要归档。

## 索引（按时间倒序）

| 日期 | 报告 | 范围 | 结论 |
|---|---|---|---|
| 2026-09-28 | [AUDIT_20260928_ARCH_GUARD_COVERAGE.md](./AUDIT_20260928_ARCH_GUARD_COVERAGE.md) | 架构 · 守卫覆盖面与缝层语义（谓词注入探针口径） | 健康度 6/10；Blocker 2 / High 4 / Medium 5 / Low 2。**与姊妹报告 `docs/AUDIT_20260928_ARCH_G10_G11.md`（8/10）口径分歧已按 J5 条件 2 登记**（§0） |
| 2026-09-26 | [AUDIT_20260926_STRICT_FULL.md](./AUDIT_20260926_STRICT_FULL.md) | 全量代码审计 + E2E | 健康度 78/100；严重 1（账本脱节 2 处）/ 警告 6 / 观察 6 |
| 2026-09-24 | [20260924_push_hang_debug.md](./20260924_push_hang_debug.md) | push 挂死排查 | 退出路径修复（os._exit 跳过 join） |
| 2026-09-23 | [20260923_iron_law_self_audit.md](./20260923_iron_law_self_audit.md) | 铁律自审计 | — |
| 2026-09-23 | [20260923_total_report.md](./20260923_total_report.md) | 汇总报告 | — |
| 2026-09-22 | [20260922_self_optimize_loop_audit.md](./20260922_self_optimize_loop_audit.md) | 自优化闭环审计 | — |
| 2026-09-13 | [FIX_REPORT_20260913.md](./FIX_REPORT_20260913.md) | 修复报告 | — |
| 2026-09-12 | [BASH_NETWORK_ROOTCAUSE_FIX_20260912.md](./BASH_NETWORK_ROOTCAUSE_FIX_20260912.md) | bash 网络根因修复 | — |
| 2026-09-12 | [AUDIT_REPORT_20260909.md](./AUDIT_REPORT_20260909.md) | 综合审计 | — |
| 2026-09-12 | [CLI_WEBUI_AUDIT_REPORT.md](./CLI_WEBUI_AUDIT_REPORT.md) | CLI/WebUI 审计 | — |
| 2026-09-12 | [ENVIRONMENT_CONSTRAINTS_AUDIT_v1.md](./ENVIRONMENT_CONSTRAINTS_AUDIT_v1.md) | 环境约束审计 | — |

## 相关（docs/ 根）

- docs/AUDIT_20260928_ARCH_G10_G11.md（同日姊妹报告，静态口径，8/10）
- docs/AUDIT_20260912_POST_REFACTOR.md
- docs/AUDIT_20260915_J1J5.md
- docs/AUDIT_7D_LINGYUAN.md

---
*维护：灵克｜更新：2026-09-28*

# 灵克 (lingclaude) 严格审计报告 — 2026-09-26

> 审计方式：严格审计官模式全量代码审计 + 并行全量 E2E（后台）+ 文档对账。
> 审计范围：lingclaude/（Python ~40k 行，core 60+ 模块 / engine 40+ 模块 / 281 测试文件 / 61k 行测试）。
> 不审计项：工作区 3 个 M 文件（cli/interface.py、webui-server/src/auth.rs、webui-server/src/main.rs）属并行会话半成品，仅做只读安全核验。
> 提交基线：HEAD `7389587`（审计前）。审计期间 E2E 后台运行，未提交任何改动。

---

## 一、结论摘要

- **架构健康度：78/100**（分层清晰、接缝机制成型、账本治理有体系；但账本与实况存在 2 处脱节、自优化闭环 fitness 未接动态数据）
- **漏洞统计：严重 1 / 警告 6 / 观察 6**
- **E2E：全量后台运行中（281 文件），基线以 1532 passed / 8 skipped / 15 failed 为已知存量**
- **最重要的发现**：`migration_registry.json` 有 2 个模块标注「已不在 core/」但 stage 仍为 `register`（账本与实况脱节，属治理数据完整性缺陷）

---

## 二、严重（必须修）

### S1. migration_registry 账本与实况脱节（2 处）
- **证据**：
  - `data/arch_ledger/migration_registry.json` items[0] `handover`：`stage=register`，note 自述「2026-09-26 实测已不在 core/」
  - items[13] `behavior_aware_router`：`stage=register`，note 自述「2026-09-26 实测已不在 core/」
  - 实测 `lingclaude/core/handover.py` **不存在**（git log 确认 `09e3f27` 已迁出 core 入 lingmemory）
  - 实测 `lingclaude/core/behavior_aware_router.py` **不存在**（git log 确认 `9e6417d` 已归位 model/）
- **影响**：红名单 91 件迁移计数失真；「register 且不在 core/」的件无法被后续闸门正确判定（既不是待迁也不是已迁出）
- **修复建议**：将两件 stage 更新为 `migrated`（handover→lingmemory；behavior_aware_router→lingclaude.model），并核对 note 与实际迁移 commit

---

## 三、警告（建议修）

### W1. datalog append-only JSONL 无轮转/压缩控制
- `lingclaude/core/datalog.py:35` `_write_event` 以 `a` 模式逐日追加到 `~/.lingclaude/datalog/YYYY-MM-DD.jsonl`，**无文件大小上限、无轮转、无压缩**
- 长期运行（L5/T0/model.call 高频事件）单日文件可无限增长；`log_model_call` 每个 stream chunk 调用一次
- 建议：按文件大小轮转（如 >50MB 切分）或按周归档压缩；参考 `bounded_compaction.py` 已有能力

### W2. ProviderPool 长连接无进程内引用追踪关闭
- `lingclaude/model/llm_proxy/provider_pool.py:41` `self._client = httpx.AsyncClient(...)` 常驻；`close()` 在 215 行定义
- 但 grep 全仓 **未找到 `provider_pool.close()` 的调用方**（`CodingRuntime.close` 只关 LSP + background pool，未关 ProviderPool）
- 影响：进程退出时 httpx 连接池未优雅关闭（一般无碍，但长驻 server 场景可能告警）
- 建议：在 CodingRuntime.close 中补充 `provider_pool.close()` 调用

### W3. model_call 熔断统计缺身份维度
- `lingclaude/core/model_call.py:141` `_record_provider_outcome` 已收敛 4 处调用点（真重复收敛，做得好）
- 但熔断统计只按 provider 名聚合（`record_success/record_error(pname)`），**未按 base_url/模型身份分账**
- 已知 ④ 聚合层（闭合期）计划扩展此维，属「待办未完成」而非新缺陷，记录供排期

### W4. eval 出现在非测试代码（低危）
- `lingclaude/lacp/marketplace.py:317` `return eval(cmd)` 位于 `if __name__ == "__main__"` 测试块内，**非生产路径**
- 但 `marketplace.py:53/217/218` 本身是插件市场安全扫描逻辑，其「危险插件示例」代码里出现 eval 容易被静态扫描误报为真实漏洞
- 建议：测试块加 `# noqa: S307` 显式标注，或在示例中避免直接使用 eval 字样

### W5. 豁免台账 2026-10-17 批量到期（88 件 active）无收割执行记录
- `data/arch_ledger/arch_exemption/exemption_review_calendar.json`：2026-10-17 有 **88 件 active 到期**（最大批量）
- 当前无收割执行记录、无四判据过链记录；日历已落盘但**执法未挂接**
- 建议：10-17 前完成批量收割预演（J4 十三处 + 88 件豁免），或显式续豁免

### W6. N7 四桥互连测试历史性 RED（已随 9097269/7389587 收口转绿，但需回归确认）
- 上轮审计记录 N7_warning_forwarding / N7_28_removal / N7_trunk_hygiene / N7_trunk_hygiene_deep 曾 RED，后转绿
- 建议本轮 E2E 结果中专项核验这 4 件是否保持 GREEN

---

## 四、观察（记录）

### O1. bash.py 命令阻止矩阵复杂（多集合分流），正确性依赖测试
- `lingclaude/engine/bash.py:67-124` 五套黑名单集合（_ALWAYS_BLOCKED / _BLOCKED_CMD_NAME_ONLY / _BLOCKED_LEADING_COMMANDS / _BLOCKED_BASE_COMMANDS / 网络分流），逻辑复杂但注释详尽
- 已有 `tests/test_bash_lingxi_security.py` 等守卫，建议审计时确认覆盖

### O2. bash_lingxi 安全对齐完成
- `lingclaude/engine/bash_lingxi.py:12-15` 明确复用 bash.py 同源黑名单（合并非替换），fail-closed；P0 安全旁路已堵

### O3. injection_screen 有 fallback 双路
- `lingclaude/engine/injection_screen.py:81` `screen_injection` 有 `use_fallback` 主备双路（109 行 fallback），设计合理

### O4. webui auth.rs 鉴权设计完整（只读核验）
- `webui-server/src/auth.rs`：一次性 handoff token（5 分钟 TTL）+ 会话 cookie + 上限兜底（26 行）+ Host 白名单防 DNS rebinding（151-175 行，2026-09-24 清偿）
- `main.rs` 编译红 E0063（AppState 缺 allowed_hosts 字段初始化）——并行会话半成品，已给口信

### O5. CodingRuntime.close 资源收尾完整（除 W2）
- `lingclaude/engine/coding.py:87-117`：LSP 池 + legacy provider + background pool 三段收尾，均有超时界，注释详尽（2026-09-24 退出挂死修复）

### O6. datalog 事件 schema 完整
- `lingclaude/core/datalog.py` 四类事件（l5.audit.round / t0.behavior.check / tool.degradation.alert / model.call）字段齐备，model.call 已带 base_url（2026-09-25 闭合期④）

---

## 五、架构健康度评分

| 维度 | 得分 | 说明 |
|---|---|---|
| 分层清晰度 | 85 | core/engine/cli/mcp/model/plugins 边界基本清晰 |
| 接缝机制 | 88 | seams/ + webui_seam + capability seams 注册/消费对称 |
| 治理账本 | 70 | migration_registry/redlist/exemption 体系完整，但 2 处脱节 |
| 资源管理 | 80 | CodingRuntime.close 完整；ProviderPool 缺调用（W2） |
| 安全面 | 82 | bash 黑名单矩阵 + 凭据拦截 + webui 鉴权；eval 在测试块（W4） |
| 自优化闭环 | 72 | 触发→学习→落库链路在；fitness 未接动态数据（观察） |
| **综合** | **78** | |

## 六、Top5 行动项

1. **修 migration_registry 2 处账本脱节**（S1）— 1 小时内
2. **datalog 轮转/压缩**（W1）— 半天
3. **ProviderPool.close 接入 CodingRuntime.close**（W2）— 1 小时
4. **豁免 88 件 10-17 批量收割预演**（W5）— 随日历排期
5. **E2E 基线确认 N7 四件保持 GREEN**（W6）— 等后台结果

---

*审计人：灵克（严格审计官模式）｜日期：2026-09-26｜审计基线 HEAD 7389587*

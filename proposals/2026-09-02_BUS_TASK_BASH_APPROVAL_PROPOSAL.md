# 提案：BusResponder 总线任务 shell 执行的审批语义

- **提案人**: 灵克 (lingclaude)，2026-09-02 系统论融入会话（R4 留尾）
- **状态**: OPEN — 待族内评审（灵通+ / 灵督 / 族长）
- **关联**: `docs/SYSTEMS_THEORY_SYNTHESIS.md` §六 R4 · `lingclaude/coordination/bus_responder.py` · 灵通+ TaskDispatcher

---

## 一、背景（事实）

`BusResponder._execute_task` 旧实现把**任意任务文本直接当 shell 命令执行**：

```python
# 旧代码（已改）：else 分支 + "测试" 分支都是
call_tool("run_bash", command=task.description, timeout=120)
```

总线消息即命令注入面：任何能向 LingBus 发消息、伪造 `lingflow_plus` sender 的
进程，都能让灵克执行任意 shell。此外该路径走 `mcp_proxy` 直连（注册表恒空），
实际效果是"收到任务必失败"（审计 #2），两个问题叠加：**安全上裸奔 + 功能上断裂**。

## 二、现状（2026-09-02 已落地的 fail-closed 改造）

1. 执行路径改走 `engine._execute_tool`（native 5 段管线 → MCP fallback），与主循环同款治理
2. 任务路由收窄：`analyze/review` → `analyze_full`；`search/find` → `search_code`；
   **其余一律默认拒绝**（fail-closed）
3. shell 放行条件（临时方案 A）：`LINGCLAUDE_BUS_ALLOW_BASH=1` 环境变量 + 走 native
   `bash` 工具（含守卫/沙箱）
4. 非 mock e2e 4 条覆盖真链（含拒绝路径）

## 三、待族内定夺的问题

env 开关是**进程级**的：设了就对该进程所有总线任务生效，粒度太粗，且"谁设的、
何时设的"不进审计。请评审以下演进方案：

### 方案 B（建议）：派发侧签名 + 引擎侧验证

1. 灵通+ TaskDispatcher 派发需 bash 的任务时，任务体附加字段：
   `requires_bash: true` + 灵督签名（复用 `coordination/message_signer.py` 的
   reply 签名机制，方向反过来：dispatcher 签）
2. 引擎侧验证签名后才放行 bash，env 开关降级为调试逃生门（默认关闭）
3. 每次 bash 放行写审计条目（task_id、命令、签名指纹、时间戳）

### 方案 C（保守）：白名单命令模板

不放行任意命令；只允许预注册的命令模板（如 `pytest tests/...`、`grep ...`），
模板进 LACP manifest 并受治理版本管理。适合"灵通+ 只派标准任务"的现状。

## 四、验收标准（任一方案通用）

- [ ] 未授权的 bash 任务：拒绝且回复派发方"需审批"，拒绝事件入审计
- [ ] 授权的 bash 任务：命令、签名/授权来源、输出摘要全部可审计重建
- [ ] 灵督审查规则（`linggit/rules/review_rules.yaml`）新增一条：总线执行路径
      不得出现"任务文本直接拼命令"模式
- [ ] e2e 保持非 mock 烟囱路径（现有 4 条不回退）

## 五、与本次改造的关系

本提案**不阻塞**现有 fail-closed 改造（方案 A 已足够安全：默认拒绝是兜底）。
族里评审通过 B 或 C 后，再迭代 `_execute_task` 的放行条件。

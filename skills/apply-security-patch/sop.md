# apply-security-patch — SOP

## 触发
- LingBus 收到 SEC-xxx 类 P1 安全审计发现
- 用户授权 apply gap patch (e.g. Gap-3 scheduled-state auth bypass)

## 前置
- 目标文件存在且在 owner 范围（用 LACP WSB hook 检查）
- 已有 patch 内容 / 已有 patch 草稿
- 已有 commit message 草稿

## 步骤

1. **Read target** — 读目标文件原内容, 定位 patch 位置（行号）
2. **Pre-write audit** — 列出 patch 将修改的代码段，让用户确认
3. **Apply patch** — 用 `edit` 工具替换（**严禁** `write` 全量覆盖）
4. **AST check** — `python3 -c "import ast; ast.parse(open(file).read())"` 验证语法
5. **Smoke test** — 运行目标文件的现有测试（如有）
6. **Emit trace** — `lacp.emit_trace(phase=EXECUTE, target_plugin="apply-security-patch@0.1.0", ...)`
7. **Commit** — `git add + commit` (commit message 包含 patch ref)
8. **Verify** — 见 `verify.md`

## 失败处理

| 失败 | 处理 |
|------|------|
| AST fail | 回退 patch, 报告用户 |
| 测试 fail | 不 commit, 报告用户 + 暂存 patch 到 `/tmp/patch_<ts>.diff` |
| commit hook reject | 检查审计失败原因, 修 commit message 或拆分 |
| hook 拦截（非 owner 目录） | 报告用户 + 让目标 owner 灵执行 |

## 实际案例

2026-06-27 Session 97 — Gap-3 SEC-001 P0-3 fix
- target_file: `/home/ai/meeting/meeting.py`
- patch_logic: scheduled state 内部分支走 attendees 直更 + 外部分支返回 "external_agent_must_join_explicitly"
- commit: `65636db init: meeting registry baseline + Gap-3 SEC-001 P0-3 fix`

LACP trace: `trace.phase=EXECUTE, actor=lingclaude, executor=crush-cli@0.79.1, outcome=PASS`
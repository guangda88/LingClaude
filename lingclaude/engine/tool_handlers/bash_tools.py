"""bash / bash_lingxi 工具 handler（依赖 self.bash / self.bash_lingxi）。"""

from __future__ import annotations

from typing import Any

from lingclaude.core.types import ToolResult


# 路径形 token 提取正则——单一来源，两个 handler 共用，禁止二次分叉。
# 修复 2026-09-12（codex 审计 P0-2）：_bash_lingxi_handler 此前用了非 raw string
# 的副本（`\\\\`），导致正则被二次转义、只匹配出 `~/.` 而漏掉 `/ssh/config`，
# sensitive_path_gate 形同虚设。现抽为模块级常量，与 _bash_handler 完全同源。
# 设计：只提取路径形 token（~ / ./ ../ 前缀），且 / 前不是单词字符
# （否则 echo "abc/def" 会把 /def 误当路径；引号内真实路径 cat "/home/x" 仍会命中）。
_PATH_TOKEN_RE = r'(?:~|(?<![\w])/|\.\.?/)[\w./~-]+'


def _bash_triage(result: Any) -> dict[str, Any]:
    """NanoJev 契约消费层（消费点②）：bash 非零退出码的 0 LLM token 分诊。

    把 stderr+stdout 喂 failure_triage.triage_and_dispose（纯正则主分类，
    未分类走 spec_decision 兜底门控），返回判定 dict 挂到工具结果上——
    模型据此直接执行确定性处置（安装/重试/停止循环），不烧推理 token。
    分诊内核故障 → 返回最小骨架（不反噬 bash 结果本身）。
    """
    try:
        from lingclaude.engine.failure_triage import triage_and_dispose
        err_text = f"{getattr(result, 'stderr', '')}\n{getattr(result, 'stdout', '')}"
        return triage_and_dispose(err_text).to_dict()
    except Exception:  # noqa: BLE001 — 分诊故障不影响 bash 结果返回（fail-soft）
        return {"triage_class": "error", "action": "none",
                "escalate_to_llm": True, "confidence": 0.0,
                "detail": "分诊内核不可用，交 LLM 判断"}


def _check_sensitive_in_command(command: str) -> tuple[str | None, str | None]:
    """扫路径 token → gate 判定。返回 (blocked_path, reason)；无拦截返回 (None, None)。

    2026-09-11（四位监督审计 P0-3）：传完整 command，让 gate 区分
    metadata（test -f / ls → 放行）vs read（cat → 拦）。
    2026-09-12（codex 审计 P0-2）：两 handler 共用此函数，消除正则分叉。
    """
    from lingclaude.engine.sensitive_path_gate import check_sensitive_path
    import re

    for p in re.findall(_PATH_TOKEN_RE, command):
        if len(p) > 1:
            is_sensitive, reason = check_sensitive_path(p, command=command)
            if is_sensitive:
                return p, reason
    return None, None


class BashToolsMixin:
    """bash / bash_lingxi 工具 handler（依赖 self.bash / self.bash_lingxi）。"""

    def _bash_handler(self, command: str, **_kwargs: Any) -> ToolResult[dict[str, Any]]:
        # T0-2: sensitive_path_gate 检查 bash 命令中的路径
        blocked, reason = _check_sensitive_in_command(command)
        if blocked:
            return ToolResult.err(
                f"Path blocked by sensitive_path_gate in command: {blocked} ({reason})",
                tool_name="bash",
            )
        result = self.bash.run(command)
        payload: dict[str, Any] = {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration": result.duration,
        }
        # NanoJev 契约消费层（消费点② 测试失败分诊，2026-09-22）：
        # 非零退出码时 0 LLM token 分诊（依赖缺失/瞬时/死循环/语法），
        # 处置指令挂到结果上（模型可见，直接执行确定性动作而非烧推理）。
        if result.exit_code not in (0, 126, 127):
            payload["triage"] = _bash_triage(result)
        return ToolResult.ok(payload)

    def _bash_lingxi_handler(self, command: str, **_kwargs: Any) -> ToolResult[dict[str, Any]]:
        # P0 安全对齐(2026-09-11): 此前 bash_lingxi handler 无 sensitive_path_gate
        # 也无默认黑名单, 是全工具面最明显的安全旁路(codex 审计命中)。
        # 现与 _bash_handler 走同一套敏感路径检查(credential exfiltration 防御)。
        blocked, reason = _check_sensitive_in_command(command)
        if blocked:
            return ToolResult.err(
                f"Path blocked by sensitive_path_gate in command: {blocked} ({reason})",
                tool_name="bash_lingxi",
            )

        result = self.bash_lingxi.run(command)
        return ToolResult.ok(
            {
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration": result.duration,
            }
        )

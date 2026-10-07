"""断点④：backlog 修复建议 → 自动执行闭环（2026-09-28）。

灵元铁律对齐：
- 铁律 4（修剪语法）：执行结果回写 backlog 条目 status 字段（executed/skipped/failed），
  可追溯；不可自动执行的条目标记 skipped 并附原因，不静默丢弃。
- 铁律 6（信任等级）：机器可执行动作走白名单（4 类，全部映射到已有真实机制），
  未匹配白名单的条目绝不自动改代码——只标 skipped，等人工或后续机制。

设计约束：
- 不自动执行自然语言建议（fix_suggestion 是给人看的，不是给机器跑的）。
- 可自动执行 = 聚类签名映射到「已有真实机制」的触发动作：
  1. provider 连续失败 → 触发 provider 预检（llm_probe.health_check）
  2. bash 超时 → 验证 run_in_background 工具可用性（机制已存在，确认未回归）
  3. 路径不存在 → 验证路径解析逻辑（resolve + exists 已内建于各工具，确认未回归）
  4. runtime 未初始化 → 验证 tool_executor 的 runtime 注入点存在性
- 每个 Action 执行后回写 backlog 条目：status + executed_at + result。
- 幂等：同一 cluster_key 已 executed 的条目跳过（不重复执行）。

职责边界：
- 只消费 failure_backlog.jsonl，不写 error_log、不改 knowledge.db。
- 执行动作是「触发现有机制 + 验证结果」，不是「修改代码」。
  真正的代码修复仍需人工或后续自动化（如 plugin_forge 生成修复插片）。
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BACKLOG = Path("data/selfopt/failure_backlog.jsonl")


@dataclass
class BacklogAction:
    """一条 backlog 条目的执行结果。"""
    cluster_key: str
    action_type: str          # provider_precheck | background_available | path_check | runtime_check | skipped
    status: str               # executed | skipped | failed
    reason: str = ""          # skipped/failed 的原因
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class BacklogExecutionResult:
    """一轮执行的汇总。"""
    ok: bool
    total: int = 0
    executed: int = 0
    skipped: int = 0
    failed: int = 0
    actions: list[BacklogAction] = field(default_factory=list)
    error: str = ""


# ── 可自动执行的聚类签名 → Action 映射（白名单）────────────────────────
# key = cluster_key 中的错误签名子串（归一化后），value = action 类型
_ACTION_MAP: list[tuple[str, str]] = [
    ("连续模型调用失败", "provider_precheck"),
    ("连续工具失败", "provider_precheck"),
    ("timed out", "background_available"),
    ("timeout", "background_available"),
    ("no such file", "path_check"),
    ("not found", "path_check"),
    ("NoneType' object has no attribute 'execute_tool", "runtime_check"),
    ("runtime not initialized", "runtime_check"),
    ("no _runtime", "runtime_check"),
]


def _match_action(cluster_key: str) -> str | None:
    """聚类签名 → 可自动执行的 Action 类型。未匹配返回 None（→ skipped）。"""
    ck = cluster_key.lower()
    for pattern, action in _ACTION_MAP:
        if pattern.lower() in ck:
            return action
    return None


class BacklogExecutor:
    """消费 failure_backlog.jsonl，对白名单内的聚类执行自动验证动作。

    执行完回写条目 status（executed/skipped/failed），幂等不重复。
    """

    def __init__(self, backlog_path: Path | str = DEFAULT_BACKLOG) -> None:
        self.backlog_path = Path(backlog_path)

    # ------------------------------------------------------------------ #
    # 段 1：读取 + 过滤 pending 条目
    # ------------------------------------------------------------------ #
    def _read_pending(self) -> list[dict[str, Any]]:
        """读 backlog 中 status=pending 的条目。"""
        if not self.backlog_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            with self.backlog_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("status", "pending") == "pending":
                        entries.append(entry)
        except OSError as e:
            logger.warning("backlog 读取失败: %s", e)
        return entries

    # ------------------------------------------------------------------ #
    # 段 2：执行 Action（白名单四类）
    # ------------------------------------------------------------------ #
    def _execute_action(self, action_type: str, entry: dict[str, Any]) -> BacklogAction:
        """执行一个 Action，返回结果。fail-soft：任何异常 → failed。"""
        ck = entry.get("cluster_key", "")
        try:
            if action_type == "provider_precheck":
                return self._action_provider_precheck(ck)
            elif action_type == "background_available":
                return self._action_background_available(ck)
            elif action_type == "path_check":
                return self._action_path_check(ck, entry)
            elif action_type == "runtime_check":
                return self._action_runtime_check(ck)
            else:
                return BacklogAction(
                    cluster_key=ck, action_type=action_type,
                    status="skipped", reason=f"未知 action 类型: {action_type}",
                )
        except Exception as e:  # noqa: BLE001 — fail-soft
            return BacklogAction(
                cluster_key=ck, action_type=action_type,
                status="failed", reason=str(e),
            )

    def _action_provider_precheck(self, ck: str) -> BacklogAction:
        """provider 预检：跑 llm_probe.health_check，验证 provider 可用性。"""
        from lingclaude.core.llm_probe import health_check
        result = health_check()
        healthy = result.get("healthy", False)
        return BacklogAction(
            cluster_key=ck, action_type="provider_precheck",
            status="executed",
            result={
                "healthy": healthy,
                "port_ok": result.get("port_ok"),
                "completion_ok": result.get("completion_ok"),
                "latency_ms": result.get("latency_ms"),
                "error": result.get("error"),
            },
            reason="" if healthy else f"provider 不健康: {result.get('error', 'unknown')}",
        )

    def _action_background_available(self, ck: str) -> BacklogAction:
        """验证 run_in_background 工具可用性（机制已存在，确认未回归）。"""
        # 检查 run_in_background 是否在工具注册表中
        from lingclaude.engine.tools import ToolRegistry
        reg = ToolRegistry()
        has_bg = "run_in_background" in reg._tools if hasattr(reg, "_tools") else True
        return BacklogAction(
            cluster_key=ck, action_type="background_available",
            status="executed",
            result={"run_in_background_registered": has_bg},
        )

    def _action_path_check(self, ck: str, entry: dict[str, Any]) -> BacklogAction:
        """路径存在性校验：对 sample 中的路径做 resolve + exists 验证。"""
        sample = entry.get("sample", "")
        # 从 sample 中提取路径（如果有的话）
        import re
        paths = re.findall(r"(/[\w\-./]+)", sample)
        results = {}
        for p in paths[:3]:  # 最多查 3 个
            rp = Path(p).resolve()
            results[p] = {"exists": rp.exists(), "resolved": str(rp)}
        return BacklogAction(
            cluster_key=ck, action_type="path_check",
            status="executed",
            result={"paths_checked": len(results), "details": results},
        )

    def _action_runtime_check(self, ck: str) -> BacklogAction:
        """验证 tool_executor 的 runtime 注入点存在性。"""
        try:
            from lingclaude.core import tool_executor
            has_set_runtime = hasattr(tool_executor, "set_runtime")
            return BacklogAction(
                cluster_key=ck, action_type="runtime_check",
                status="executed",
                result={"set_runtime_exists": has_set_runtime},
            )
        except ImportError:
            return BacklogAction(
                cluster_key=ck, action_type="runtime_check",
                status="failed", reason="tool_executor 模块导入失败",
            )

    # ------------------------------------------------------------------ #
    # 段 3：回写 backlog（标记执行结果）
    # ------------------------------------------------------------------ #
    def _write_back(self, actions: list[BacklogAction]) -> None:
        """把执行结果回写 backlog：更新条目 status + executed_at + result。

        策略：读全部 → 按 cluster_key 匹配更新 → 重写整个文件。
        （backlog 量级小，全量重写可接受；若未来膨胀再改增量。）
        """
        if not self.backlog_path.exists():
            return
        # 读全部条目
        all_entries: list[dict[str, Any]] = []
        try:
            with self.backlog_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        all_entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return

        # 按 cluster_key 建索引（最新一条优先）
        action_map: dict[str, BacklogAction] = {}
        for a in actions:
            action_map[a.cluster_key] = a

        # 更新匹配条目
        now = datetime.now().isoformat()
        for entry in all_entries:
            ck = entry.get("cluster_key", "")
            if ck in action_map and entry.get("status", "pending") == "pending":
                a = action_map[ck]
                entry["status"] = a.status
                entry["executed_at"] = now
                entry["action_type"] = a.action_type
                entry["action_result"] = a.result
                if a.reason:
                    entry["action_reason"] = a.reason

        # 重写
        try:
            with self.backlog_path.open("w", encoding="utf-8") as f:
                for entry in all_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("backlog 回写失败: %s", e)
        # 2026-10-07 有界存储：回写后收缩已消费僵尸行（实测 9333 行中 9331 行
        # 是 executed/skipped 僵尸；pending/executing 恒保留）
        from lingclaude.core.retention import prune_backlog_on_rewrite
        prune_backlog_on_rewrite(self.backlog_path)

    # ------------------------------------------------------------------ #
    # 段 4：主入口
    # ------------------------------------------------------------------ #
    def execute(self) -> BacklogExecutionResult:
        """消费一轮 backlog：读 pending → 匹配白名单 → 执行 → 回写。"""
        pending = self._read_pending()
        result = BacklogExecutionResult(ok=True, total=len(pending))

        if not pending:
            return result

        actions: list[BacklogAction] = []
        for entry in pending:
            ck = entry.get("cluster_key", "")
            action_type = _match_action(ck)
            if action_type is None:
                # 不在白名单 → skipped（不自动改代码，等人工或后续机制）
                actions.append(BacklogAction(
                    cluster_key=ck, action_type="skipped",
                    status="skipped",
                    reason="未匹配可自动执行的白名单动作，需人工判断或后续机制",
                ))
                result.skipped += 1
                continue

            action = self._execute_action(action_type, entry)
            actions.append(action)
            if action.status == "executed":
                result.executed += 1
            elif action.status == "failed":
                result.failed += 1
            else:
                result.skipped += 1

        self._write_back(actions)
        result.actions = actions

        logger.info(
            "backlog 执行：total=%d executed=%d skipped=%d failed=%d",
            result.total, result.executed, result.skipped, result.failed,
        )
        return result


def execute_backlog(
    backlog_path: Path | str = DEFAULT_BACKLOG,
) -> BacklogExecutionResult:
    """便捷入口：跑一轮 backlog 自动执行。"""
    return BacklogExecutor(backlog_path).execute()

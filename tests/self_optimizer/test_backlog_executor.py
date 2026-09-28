"""断点④：backlog 修复建议 → 自动执行闭环 测试（2026-09-28）。

覆盖：
- 白名单匹配：provider/timeout/path/runtime 四类签名正确映射
- 未匹配白名单 → skipped（不自动改代码）
- 执行结果回写：status/executed_at/action_result 正确更新
- 幂等：已 executed 的条目不重复执行
- backlog 不存在/损坏 → fail-soft 不抛异常
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.self_optimizer.backlog_executor import (
    BacklogExecutor,
    _match_action,
    execute_backlog,
)


# ── 白名单匹配 ─────────────────────────────────────────────────────────

def test_match_provider_precheck():
    assert _match_action("provider::连续模型调用失败 <N> 次") == "provider_precheck"
    assert _match_action("tool_loop::连续工具失败 <N> 次") == "provider_precheck"


def test_match_background_available():
    assert _match_action("bash::timed out after <N>s") == "background_available"
    assert _match_action("bash::timeout") == "background_available"


def test_match_path_check():
    assert _match_action("read::no such file or directory") == "path_check"
    assert _match_action("grep::not found") == "path_check"


def test_match_runtime_check():
    assert _match_action("read::'NoneType' object has no attribute 'execute_tool'") == "runtime_check"
    assert _match_action("read::Tool runtime not initialized") == "runtime_check"


def test_match_unknown_returns_none():
    assert _match_action("edit::未找到匹配文本") is None
    assert _match_action("read::Tool blocked by permissions") is None


# ── 执行 + 回写 ─────────────────────────────────────────────────────────

def _write_backlog(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _read_backlog(path: Path) -> list[dict]:
    entries = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def test_execute_provider_precheck(tmp_path: Path):
    backlog = tmp_path / "backlog.jsonl"
    _write_backlog(backlog, [{
        "at": "2026-09-28T18:00:00",
        "type": "failure_cluster_fix",
        "status": "pending",
        "cluster_key": "provider::连续模型调用失败 <N> 次",
        "tool": "provider",
        "occurrences": 188,
        "sessions": 170,
        "hypothesis": "连续失败硬中断",
        "fix_suggestion": "派发前预检 provider 可用性",
        "sample": "连续模型调用失败 3 次",
    }])

    ex = BacklogExecutor(backlog)
    result = ex.execute()

    assert result.ok
    assert result.total == 1
    assert result.executed == 1
    assert result.skipped == 0

    # 回写验证
    entries = _read_backlog(backlog)
    assert entries[0]["status"] == "executed"
    assert entries[0]["action_type"] == "provider_precheck"
    assert "healthy" in entries[0]["action_result"]


def test_execute_skipped_for_non_whitelist(tmp_path: Path):
    backlog = tmp_path / "backlog.jsonl"
    _write_backlog(backlog, [{
        "at": "2026-09-28T18:00:00",
        "type": "failure_cluster_fix",
        "status": "pending",
        "cluster_key": "edit::未找到匹配文本: <PATH>",
        "tool": "edit",
        "occurrences": 5,
        "sessions": 3,
        "hypothesis": "需人工深挖根因",
        "fix_suggestion": "对该聚类取样本人工复核",
        "sample": "未找到匹配文本: /tmp/x.py",
    }])

    ex = BacklogExecutor(backlog)
    result = ex.execute()

    assert result.ok
    assert result.executed == 0
    assert result.skipped == 1

    entries = _read_backlog(backlog)
    assert entries[0]["status"] == "skipped"
    assert entries[0]["action_type"] == "skipped"
    assert "白名单" in entries[0]["action_reason"]


def test_execute_idempotent(tmp_path: Path):
    """已 executed 的条目不重复执行。"""
    backlog = tmp_path / "backlog.jsonl"
    _write_backlog(backlog, [{
        "at": "2026-09-28T18:00:00",
        "type": "failure_cluster_fix",
        "status": "executed",  # 已执行过
        "cluster_key": "provider::连续模型调用失败 <N> 次",
        "tool": "provider",
        "occurrences": 188,
        "sessions": 170,
        "hypothesis": "连续失败硬中断",
        "fix_suggestion": "派发前预检",
        "sample": "连续模型调用失败 3 次",
        "executed_at": "2026-09-28T18:01:00",
        "action_type": "provider_precheck",
        "action_result": {"healthy": True},
    }])

    ex = BacklogExecutor(backlog)
    result = ex.execute()

    # pending 为空 → 直接返回
    assert result.total == 0
    assert result.executed == 0

    # 文件未被修改
    entries = _read_backlog(backlog)
    assert entries[0]["status"] == "executed"
    assert entries[0]["executed_at"] == "2026-09-28T18:01:00"  # 未被覆盖


def test_execute_backlog_not_exists(tmp_path: Path):
    """backlog 文件不存在 → fail-soft，不抛异常。"""
    ex = BacklogExecutor(tmp_path / "nonexistent.jsonl")
    result = ex.execute()
    assert result.ok
    assert result.total == 0


def test_execute_backlog_corrupted_lines(tmp_path: Path):
    """backlog 含损坏行 → 跳过，不阻断。"""
    backlog = tmp_path / "backlog.jsonl"
    backlog.parent.mkdir(parents=True, exist_ok=True)
    backlog.write_text(
        '{"status": "pending", "cluster_key": "provider::连续模型调用失败 <N> 次", "tool": "provider"}\n'
        '{corrupted json\n'
        '{"status": "pending", "cluster_key": "edit::未找到匹配文本", "tool": "edit"}\n',
        encoding="utf-8",
    )

    ex = BacklogExecutor(backlog)
    result = ex.execute()

    assert result.ok
    assert result.total == 2  # 2 条有效
    assert result.executed == 1  # provider 被执行
    assert result.skipped == 1   # edit 被 skipped


def test_execute_multiple_mixed(tmp_path: Path):
    """混合条目：白名单内 executed，白名单外 skipped。"""
    backlog = tmp_path / "backlog.jsonl"
    _write_backlog(backlog, [
        {
            "at": "2026-09-28T18:00:00",
            "type": "failure_cluster_fix",
            "status": "pending",
            "cluster_key": "provider::连续模型调用失败 <N> 次",
            "tool": "provider",
            "occurrences": 188,
            "sessions": 170,
            "hypothesis": "连续失败硬中断",
            "fix_suggestion": "派发前预检",
            "sample": "连续模型调用失败 3 次",
        },
        {
            "at": "2026-09-28T18:00:01",
            "type": "failure_cluster_fix",
            "status": "pending",
            "cluster_key": "bash::timed out after <N>s",
            "tool": "bash",
            "occurrences": 5,
            "sessions": 3,
            "hypothesis": "超时",
            "fix_suggestion": "长任务改 run_in_background",
            "sample": "timed out after 120s",
        },
        {
            "at": "2026-09-28T18:00:02",
            "type": "failure_cluster_fix",
            "status": "pending",
            "cluster_key": "read::Tool blocked by permissions",
            "tool": "read",
            "occurrences": 44,
            "sessions": 44,
            "hypothesis": "权限闸拦截",
            "fix_suggestion": "复核 permissions 配置",
            "sample": "Tool blocked by permissions: read",
        },
    ])

    ex = BacklogExecutor(backlog)
    result = ex.execute()

    assert result.ok
    assert result.total == 3
    assert result.executed == 2  # provider + timeout
    assert result.skipped == 1   # permissions

    entries = _read_backlog(backlog)
    assert entries[0]["status"] == "executed"
    assert entries[1]["status"] == "executed"
    assert entries[2]["status"] == "skipped"


def test_execute_convenience_function(tmp_path: Path):
    """便捷入口 execute_backlog。"""
    backlog = tmp_path / "backlog.jsonl"
    _write_backlog(backlog, [{
        "at": "2026-09-28T18:00:00",
        "type": "failure_cluster_fix",
        "status": "pending",
        "cluster_key": "read::no such file: <PATH>",
        "tool": "read",
        "occurrences": 3,
        "sessions": 2,
        "hypothesis": "路径不存在",
        "fix_suggestion": "路径引用前实测存在性",
        "sample": "no such file: /tmp/x.py",
    }])

    result = execute_backlog(backlog)
    assert result.ok
    assert result.executed == 1
    assert result.actions[0].action_type == "path_check"

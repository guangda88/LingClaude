"""SessionJournal — append-only JSONL 会话日志（R5 阶段1）。

设计:
- 每条事件一行 JSON，O(1) 追加（open+write+close）
- 崩溃后最多丢最后一条未 flush 的行，不会损坏之前的记录
- journal_dir 默认 .lingclaude/journals/，可通过构造参数覆盖（测试用 tmp_path）
- 与 SessionStore 的 checkpoint 互补: checkpoint 存"恢复点快照"，
  journal 存"恢复点之间的增量"——resume 时用 journal 重建完整状态。

事件类型:
- turn_start    {session_id, prompt, timestamp}
- tool_call     {session_id, tool_call_id, name, arguments, timestamp}
- tool_result   {session_id, tool_call_id, output_preview, is_error, timestamp}
- turn_end      {session_id, final_content_preview, total_input, total_output}
- checkpoint    {session_id, round_idx, messages_count}
- journal_meta  {session_id, compact_after_turns, created_at}
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_JOURNAL_DIR = Path(".lingclaude/journals")


class SessionJournal:
    """Append-only JSONL 会话日志。

    线程安全: 内部 _lock 保护文件写入。
    每条事件是一个 dict，序列化为一行 JSON 追加到 <journal_dir>/<session_id>.jsonl。
    """

    def __init__(self, session_id: str, journal_dir: Path | None = None) -> None:
        self.session_id = session_id
        self._dir = journal_dir or DEFAULT_JOURNAL_DIR
        self._path = self._dir / f"{session_id}.jsonl"
        self._lock = threading.Lock()
        self._fh = None  # 持久化文件句柄（R5 性能优化：消除每次 open/close）

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event_type: str, data: dict[str, Any] | None = None) -> bool:
        """追加一条事件。返回 True 成功，False I/O 失败（不抛异常，best-effort）。

        性能：首次 append 打开文件句柄并复用（R5 优化），close() 或 clear() 时关闭。
        """
        entry = {
            "type": event_type,
            "session_id": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **(data or {}),
        }
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        try:
            with self._lock:
                if self._fh is None or self._fh.closed:
                    self._dir.mkdir(parents=True, exist_ok=True)
                    self._fh = open(self._path, "a", encoding="utf-8")
                self._fh.write(line)
                self._fh.flush()  # 确保对其他 reader 立即可见（多进程/测试场景）
            return True
        except OSError as e:
            logger.warning("SessionJournal append failed: %s", e)
            return False

    def close(self) -> None:
        """关闭持久化文件句柄（R5 性能优化配套）。"""
        with self._lock:
            if self._fh is not None and not self._fh.closed:
                self._fh.close()

    def load(self) -> list[dict[str, Any]]:
        """读取全部事件。文件不存在或损坏行被跳过。"""
        self.close()  # 确保 buffered write 已 flush 到磁盘
        if not self._path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            with open(self._path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        # 尾部截断行（进程被杀时最后一个 write 未完成），跳过
                        continue
        except OSError as e:
            logger.warning("SessionJournal load failed: %s", e)
        return events

    def tool_call_ids(self) -> set[str]:
        """返回已执行过的 tool_call_id 集合（副作用幂等用）。"""
        ids = set()
        for ev in self.load():
            if ev.get("type") == "tool_call" and ev.get("tool_call_id"):
                ids.add(ev["tool_call_id"])
        return ids

    def has_tool_call(self, tool_call_id: str) -> bool:
        """检查某个 tool_call 是否已执行过（幂等判断）。"""
        return tool_call_id in self.tool_call_ids()

    def tool_signatures(self) -> set[tuple[str, str]]:
        """返回已执行的 (name, arguments) 签名集合（副作用幂等用）。

        与 _ToolLoopDetector 的 (name, arguments) 对齐：
        resume 后模型可能重复调用相同工具，用签名而非 ID 去重
        （模型每次生成的 tool_call_id 不同，但 name+arguments 相同）。
        """
        sigs = set()
        for ev in self.load():
            if ev.get("type") == "tool_call" and ev.get("name"):
                sigs.add((ev["name"], ev.get("arguments", "")))
        return sigs

    def tool_results_by_signature(self) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """返回 (name, arguments) -> [tool_result 事件] 映射（幂等重放用）。"""
        results: dict[tuple[str, str], list[dict[str, Any]]] = {}
        calls_by_id: dict[str, tuple[str, str]] = {}
        for ev in self.load():
            if ev.get("type") == "tool_call" and ev.get("tool_call_id"):
                calls_by_id[ev["tool_call_id"]] = (ev["name"], ev.get("arguments", ""))
            elif ev.get("type") == "tool_result" and ev.get("tool_call_id") in calls_by_id:
                sig = calls_by_id[ev["tool_call_id"]]
                results.setdefault(sig, []).append(ev)
        return results

    def pending_side_effects(self, side_effect_tools: set[str]) -> list[dict[str, Any]]:
        """扫描 journal 找出"已发出 tool_call 但无 tool_result 且是副作用工具"的项。

        R5 阶段2:resume 流程在 checkpoint 恢复后,可能重复执行这些写类调用（写文件 /
        跑 bash / rm / curl 等）。返回列表让调用方决定"重放 / 跳过 / 用户确认"。

        Args:
            side_effect_tools: 副作用工具名集合（如 {"write", "edit", "bash", "rm", "curl"}）。
                              不传则返回空（保守）。

        Returns:
            [{tool_call_id, name, arguments, ts, status: "awaiting_result"}, ...]
        """
        if not side_effect_tools:
            return []
        pending: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        completed_ids: set[str] = set()
        for ev in self.load():
            ev_type = ev.get("type")
            tc_id = ev.get("tool_call_id")
            if ev_type == "tool_call" and tc_id:
                seen_ids.add(tc_id)
                if ev.get("name") in side_effect_tools and tc_id not in completed_ids:
                    pending.append({
                        "tool_call_id": tc_id,
                        "name": ev.get("name"),
                        "arguments": ev.get("arguments", ""),
                        "ts": ev.get("ts"),
                        "status": "awaiting_result",
                    })
            elif ev_type == "tool_result" and tc_id:
                completed_ids.add(tc_id)
                # 已完成的副作用从 pending 移除（按 ID 过滤）
                pending = [p for p in pending if p["tool_call_id"] != tc_id]
        return pending

    def clear(self) -> bool:
        """删除 journal 文件（turn 正常完成时调用，防 journal 无限增长）。"""
        self.close()
        try:
            self._path.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def truncate_after(self, timestamp: str) -> int:
        """截断 timestamp 之后的事件（回退用）。返回保留的事件数。"""
        events = self.load()
        kept = [e for e in events if e.get("timestamp", "") <= timestamp]
        try:
            with self._lock:
                self._dir.mkdir(parents=True, exist_ok=True)
                with open(self._path, "w", encoding="utf-8") as f:
                    for e in kept:
                        f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
            return len(kept)
        except OSError as e:
            logger.warning("SessionJournal truncate failed: %s", e)
            return len(kept)

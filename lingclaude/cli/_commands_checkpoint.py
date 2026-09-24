"""斜杠命令处理器（P4.1 从 cli/app.py 拆出）— _interactive_loop 嵌套闭包的外提。

原实现是 _interactive_loop 内约 280 行闭包（radon 将嵌套函数复杂度聚合进宿主，
是 F(69) 的主因）；现外提为 SlashCommandProcessor，engine/status 构造注入，
quit_requested 由 nonlocal 改为实例属性（语义不变）。
命令体自 app.py 原样迁移，仅去掉一层闭包缩进。
"""

from typing import Any

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from lingclaude.cli.repl_turn import _record_long_task_metrics
import json as _json
import shlex


def _next_fork_tag() -> str:
    """缺省 fork tag：fork-<HHMMSS>（冲突加 pid 后缀）。"""
    tag = f"fork-{time.strftime('%H%M%S')}"
    rodir = Path(".lingclaude/rollouts")
    if rodir.exists() and any(rodir.glob(f"{tag}*.jsonl")):
        tag += f"-{os.getpid() % 10000}"
    return tag

# Step 3: Tab 补全清单（F2 修复:删 /undo — handler 缺失不得留在补全里误导用户）
SLASH_COMPLETER_WORDS = [
    "/help", "/clear", "/compact", "/model", "/schedule", "/lsp",
    "/resume", "/continue", "/session", "/checkpoint", "/recover", "/rewind", "/quit",
    # 2026-09-23: C 路线统一——/fork（rollout 不可变分叉）/ /share（自包含导出）
    "/fork", "/share",
    # 2026-09-17: 任务面板（对标 AtomCode todowrite）—— /tasks /todo /plan 同义
    "/tasks",
    # 2026-09-20: 会话历史查看（TUI 优化方案 P2-1，cc 建议）—— 退出后回看入口
    "/history",
    # 2026-09-21: OpenRouter 一键接入（OAuth PKCE，学 atomcode）——
    # CodingPlan 配额耗尽时的免费池逃生门
    "/openrouter",
    # 2026-09-20: P3 全量重绘输出窗（atomcode invalidate 借鉴）
    "/resync",
]



class SlashCommandCheckpointMixin:
    """检查点/分叉/导出命令域（/checkpoint /recover /rewind /fork /share）— P5 拆分自 commands.py。"""


    def _cmd_checkpoint(self) -> None:
        engine = self.engine
        # R5 阶段1: /checkpoint 手动保存当前会话 checkpoint + journal
        persist_result = engine._session_persister.persist_session()
        if persist_result.is_ok:
            engine._get_journal().append("checkpoint", {
                "manual": True,
                "messages_count": len(engine._messages),
            })
            print(f"[checkpoint] 已保存: {persist_result.data}")
        else:
            print(f"[checkpoint] 保存失败: {persist_result.error}")

    def _cmd_recover(self) -> None:
        engine = self.engine
        status = self.status
        # R5 阶段1 的核心 API 此前没有 CLI 入口：进程被杀/中断后，
        # /resume 只能恢复历史会话，不能从中断工具轮继续。
        status.set_task("恢复中")
        try:
            result = engine.resume_interrupted()
        finally:
            status.set_task("空闲")
        _record_long_task_metrics(
            engine,
            event="slash_recover",
            outcome="ok" if result.is_ok else ("no_checkpoint" if result.code == "NO_CHECKPOINT" else "error"),
            error=None if result.is_ok else result.error,
        )
        if result.is_ok:
            print(f"[已恢复中断任务] {result.data}")
        elif result.code == "NO_CHECKPOINT":
            print("[无可恢复任务] 当前会话没有中断 checkpoint")
        else:
            print(f"[恢复失败] {result.error}")

    def _cmd_rewind(self, arg: str) -> None:
        engine = self.engine
        arg = arg.strip()
        # /rewind 无参 = 列出全部历史 checkpoint 快照
        if not arg:
            cps = engine.list_checkpoints()
            if not cps:
                print("[rewind] 当前会话没有历史 checkpoint 快照")
                print("[提示] 工具轮执行中会自动保存 roundN 快照；/checkpoint 可手动保存")
                return
            print(f"[rewind] 共 {len(cps)} 个快照（最新在前）：")
            for i, c in enumerate(cps):
                tag = c.get("tag") or "(latest)"
                print(f"  [{i}] {tag} | round={c.get('round_idx')} "
                      f"| msgs={c.get('message_count')} | {c.get('timestamp')}")
            print("[用法] /rewind <tag 或序号> 回滚到指定快照")
            return
        # 支持序号或 tag 两种定位
        target = arg
        cps = engine.list_checkpoints()
        if arg.isdigit():
            idx = int(arg)
            if 0 <= idx < len(cps):
                target = cps[idx].get("tag") or "latest"
            else:
                print(f"[rewind] 序号越界：{idx}（共 {len(cps)} 个，0-based）")
                return
        if target == "latest":
            # 指向最新带 tag 的版本；没有 tag 版本则提示
            tagged = [c for c in cps if c.get("tag")]
            if not tagged:
                print("[rewind] 无带 tag 的历史快照，无法回滚")
                return
            target = tagged[0]["tag"]
        result = engine.rewind_to(target)
        if result.is_ok:
            print(f"[已回滚] {result.data}")
        else:
            print(f"[回滚失败] {result.error}")

    def _cmd_fork(self, arg: str) -> None:
        """C 路线统一（2026-09-23）：/fork = rollout 不可变分叉（codex 对齐）。

        当前会话历史截断复制到新 rollout 文件（forked_from_id 链），
        源文件不删不改。engine 侧仅记 fork 事件并挂新 recorder；
        消息上下文原地保留（fork 出的是「可独立回放的平行史」，
        不切走当前对话——切换语义归 /resume）。
        """
        engine = self.engine
        tag = arg.strip() or _next_fork_tag()
        try:
            from lingclaude.core.rollout import get_engine_rollout
            rr = get_engine_rollout(engine, session_id=engine.session_id)
            if rr is None:
                print("[fork] rollout 不可用（存储层故障），分叉未创建")
                return
            # 触发一次 checkpoint：让当前完整上下文先落到快照 + 事件流，
            # 再从当前 ordinal 分叉（保证分叉文件首段是完整历史）
            engine._save_checkpoint(
                messages=engine._messages, round_idx=0,
                prompt=engine._messages[-1].content if engine._messages else "",
                used_tools=False, total_input=0, total_output=0,
            )
            path = rr.fork(tag)
            print(f"[fork] 已分叉: {path.name}")
            print(f"       forked_from={rr.session_id} tag={tag}（源文件保留，可 /resume 回溯）")
        except Exception as e:  # noqa: BLE001
            print(f"[fork] 分叉失败: {e}")

    def _cmd_share(self, arg: str) -> None:
        """C 路线统一（2026-09-23）：/share = 自包含导出当前会话副本。

        只读导出（JSONL，含 meta+消息），不动原会话；敏感字段走 redact。
        可选参数：目标文件路径（默认 .lingclaude/shares/share-<ts>.jsonl）。
        """
        engine = self.engine
        if not engine._messages:
            print("[share] 当前会话无消息，无可导出")
            return
        try:
            from lingclaude.core.redact import redact as _redact
            from lingclaude.core.rollout import ROLLOUT_DIR, _ts_slug
            out = Path(arg.strip()) if arg.strip() else (
                ROLLOUT_DIR.parent / "shares" / f"share-{_ts_slug()}.jsonl")
            out.parent.mkdir(parents=True, exist_ok=True)
            meta = {
                "event": "session_meta", "session_id": engine.session_id,
                "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "branch": getattr(engine, "git_branch", None) or "",
                "message_count": len(engine._messages),
            }
            lines = [json.dumps(meta, ensure_ascii=False)]
            for i, m in enumerate(engine._messages):
                d = m.to_dict() if hasattr(m, "to_dict") else {
                    "role": getattr(m, "role", ""), "content": getattr(m, "content", "")}
                d["content"] = _redact(str(d.get("content", "")))
                d["ordinal"] = i + 1
                lines.append(json.dumps(d, ensure_ascii=False, default=str))
            out.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"[share] 已导出 {len(engine._messages)} 条消息（redact 已过）→ {out}")
        except Exception as e:  # noqa: BLE001
            print(f"[share] 导出失败: {e}")

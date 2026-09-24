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



class SlashCommandHistoryMixin:
    """会话历史命令域（/history）— P5 拆分自 commands.py。"""



    def _cmd_history(self, arg: str) -> None:
        """2026-09-20（TUI 优化方案 P2-1）: /history [N] | /history show <id>。

        列表复用 SessionManager.list_sessions（按当前项目隔离）；show 用
        ${PAGER:-less -R} 打开对话记录（非 TTY 直接打印）。只读不改上下文。
        """
        from lingclaude.core.session import SessionManager, _project_dir_name

        arg = (arg or "").strip()
        mgr = SessionManager()
        try:
            sessions = mgr.list_sessions(project_path=os.getcwd())
        except Exception:  # noqa: BLE001 — cwd 不可用时退全局
            sessions = mgr.list_sessions()

        # show <id>（支持前缀）
        parts = arg.split(maxsplit=1)
        if parts and parts[0] == "show":
            if len(parts) < 2 or not parts[1].strip():
                print("[history] 用法: /history show <会话ID（可前缀）>")
                return
            sid = parts[1].strip()
            matches = [s for s in sessions if s["session_id"].startswith(sid)]
            if not matches:
                print(f"[history] 未找到会话 {sid}")
                return
            if len(matches) > 1:
                print(f"[history] 前缀 {sid} 匹配 {len(matches)} 条，请加长 ID：")
                for m in matches[:5]:
                    print(f"  {m['session_id']}")
                return
            full = matches[0]["session_id"]
            proj = matches[0].get("project_path", "")
            proj_dir = _project_dir_name(proj) if proj else "_default"
            path = mgr.save_dir / proj_dir / f"{full}.json"
            if not path.exists():
                path = mgr.save_dir / "_default" / f"{full}.json"
            try:

                data = _json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001 — 文件损坏留痕不崩
                print(f"[history] 会话记录读取失败: {e}")
                return
            lines: list[str] = [f"# 会话 {full}（{data.get('created_at', '?')}）", ""]
            for m in data.get("messages", ()) or ():
                if isinstance(m, str):
                    role, text = "user", m
                elif isinstance(m, dict):
                    role = str(m.get("role", "user"))
                    text = str(m.get("content", ""))
                else:
                    role = str(getattr(m, "role", "user"))
                    text = str(getattr(m, "content", ""))
                if not text.strip():
                    continue
                who = "🧑 用户" if role == "user" else "🤖 灵克"
                lines.append(f"{who}: {text}")
                lines.append("")
            body = "\n".join(lines)
            if not sys.stdout.isatty():
                print(body)
                return
            # PAGER env 信任面收口（V5 清偿 2026-09-24）：不再读 env——
            # .bashrc/包管理器 post-install 注入 PAGER='sh -c …' 的场景下
            # 旧实现 split() 后逐词执行注入串。/history 输出为纯文本预览，
            # 固定 less（-R 保留 ANSI 色）+ 无交互环境直接打印，够用且无攻击面。
            try:
                proc = subprocess.Popen(["less", "-R"], stdin=subprocess.PIPE)
                proc.communicate(body.encode("utf-8", errors="replace"))
            except Exception:  # noqa: BLE001 — 分页器缺席直接打印
                print(body)
            return

        # /history [N]：最近 N 条（created_at 降序）
        n = 10
        if arg:
            if not arg.isdigit():
                print("[history] 用法: /history [N] | /history show <id>")
                return
            n = max(1, int(arg))
        ordered = sorted(
            sessions, key=lambda s: str(s.get("created_at", "")), reverse=True
        )
        if not ordered:
            print("[history] 无会话记录")
            return
        print(f"[history] 共 {len(ordered)} 个会话，最近 {min(n, len(ordered))} 条：")
        for s in ordered[:n]:
            sid = str(s.get("session_id", "?"))
            print(f"  {sid[:12]}  {str(s.get('created_at', ''))[:19]}  {str(s.get('summary', ''))[:48]}")
        print("  查看: /history show <ID>")

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



class SlashCommandSessionMixin:
    """会话切换命令域（/session /resume /continue）— P5 拆分自 commands.py。"""


    def _cmd_session(self, arg: str) -> None:
        """2026-09-15（会话问题重构 P1-2）: /session [list|switch <id>]。

        会话按当前工作目录隔离（P1-2），列表带摘要（前 3 条对话）——
        快速识别每个会话的内容，替代此前只能靠 session_id 猜。
        """
        engine = self.engine

        arg = arg.strip()
        sessions = engine.session_manager.list_sessions(project_path=os.getcwd())

        if not arg or arg == "list":
            # /session 或 /session list —— 列出当前项目会话（带摘要）
            if not sessions:
                print("[会话列表] 无历史会话（当前目录下）")
                return
            print(f"[会话列表] 当前目录共 {len(sessions)} 个（最近 10 个）：")
            for s in sorted(sessions, key=lambda x: x.get("created_at", ""), reverse=True)[:10]:
                print(f"  - {s.get('session_id', '')[:8]} | {s.get('summary', '')} | {s.get('created_at', '')}")
            return

        if arg in ("switch", "sw") and len(arg.split()) < 2:
            print("[session] 用法: /session switch <session_id> 或 /session <session_id>")
            return

        # /session switch <id> 或 /session <id> —— 切换到指定会话
        target_id = arg.split(maxsplit=1)[1] if arg.startswith(("switch", "sw")) else arg
        target_id = target_id.strip()
        # 短前缀匹配
        matches = [s for s in sessions if str(s.get("session_id", "")).startswith(target_id)]
        if len(matches) == 1:
            target_id = str(matches[0]["session_id"])
        elif len(matches) > 1:
            print(f"[歧义] 有 {len(matches)} 个会话以 {target_id} 开头，请用更长的 ID：")
            for s in matches[:5]:
                print(f"  - {s.get('session_id', '')[:8]} | {s.get('summary', '')}")
            return
        if engine._session_persister.load_session(target_id):
            print(f"[会话已切换] {target_id[:8]}（{len(engine._conversation)} 轮对话；当前上下文已替换）")
        else:
            print(f"[会话切换失败] {target_id} 不存在或已损坏（当前上下文未受影响）")

    def _cmd_resume(self, name: str, arg: str) -> None:
        engine = self.engine
        # 会话恢复:/resume [ID] 恢复指定会话、/continue 恢复最近一次。
        # 复用启动参数 --resume/--continue 的同一套持久化接口（load_session），
        # 语义与启动时一致：恢复 = 替换当前上下文。
        # 2026-09-15（会话问题重构 P1-2）: 按当前工作目录过滤 —— 此前
        # list_sessions() 不带 project_path 取全局，/continue 会跨项目恢复
        # 别的目录的会话（会话泄露）。现以 os.getcwd() 为项目归属过滤。
        sessions = engine.session_manager.list_sessions(project_path=os.getcwd())
        if name == "/continue":
            # /continue = 无参恢复最近一次（对齐启动参数 --continue）
            if not sessions:
                print("[恢复失败] 没有可恢复的历史会话（当前目录下）")
                return
            latest = max(sessions, key=lambda s: s.get("created_at", ""))
            target_id = str(latest.get("session_id", ""))
            print(f"[目标] 最近会话: {target_id}（当前目录）")
        elif not arg:
            # /resume 无参 = 列出全部（对齐帮助文本承诺；这是会话 ID 的发现入口）
            if not sessions:
                print("[会话列表] 无历史会话（当前目录下）")
                return
            print(f"[会话列表] 共 {len(sessions)} 个（最近 10 个，当前目录）：")
            for s in sorted(sessions, key=lambda x: x.get("created_at", ""), reverse=True)[:10]:
                print(f"  - {s.get('session_id')} | {s.get('created_at', '')}")
            return
        else:
            target_id = arg.strip()
            # 短前缀匹配（对齐 /schedule cancel 用 8 位短 ID 的习惯）
            matches = [s for s in sessions if str(s.get("session_id", "")).startswith(target_id)]
            if len(matches) == 1:
                target_id = str(matches[0]["session_id"])
            elif len(matches) > 1:
                print(f"[歧义] 有 {len(matches)} 个会话以 {target_id} 开头，请用更长的 ID：")
                for s in matches[:5]:
                    print(f"  - {s.get('session_id')} | {s.get('created_at', '')}")
                return
        if engine._session_persister.load_session(target_id):
            # 方案C v4: 恢复成功 → SESSION_RESUME 钩子（快照来源与会话 ID 随钩子传播；
            # 无注册钩子时零开销——HookManager.trigger 对空表 no-op）
            try:
                from lingclaude.core.hooks import HookContext, HookType
                hooks_mgr = getattr(engine, "_hooks", None)
                if hooks_mgr is not None:
                    hooks_mgr.trigger(HookContext(
                        hook_type=HookType.SESSION_RESUME,
                        session_id=target_id,
                        resumed=True,
                        resumed_from_snapshot=target_id,
                    ))
            except Exception:  # noqa: BLE001 钩子失败不阻断恢复主路径
                logging.getLogger(__name__).exception(
                    "SESSION_RESUME hook failed (session=%s)", target_id)
            # 验证台账锚点注入（2026-09-21 幻觉审计治理 层3）：恢复的不是
            # 「我记得验证过」，而是带 digest 的台账原文——压缩丢证据、
            # 恢复后无依据撤回（发作B）都失去土壤。台账缺席/故障静默跳过。
            try:
                from lingclaude.core.verify_ledger import get_verify_ledger
                anchor = get_verify_ledger().anchor_block(target_id)
                if anchor:
                    engine._conversation.append(("system", anchor))
            except Exception:  # noqa: BLE001 锚点故障不阻断恢复主路径
                logging.getLogger(__name__).exception(
                    "verify anchor inject failed (session=%s)", target_id)
            print(f"[会话已恢复] {target_id}（{len(engine._conversation)} 轮对话；当前上下文已被替换）")
        else:
            print(f"[会话恢复失败] {target_id} 不存在或已损坏（当前上下文未受影响）")

"""斜杠命令处理器（P4.1 从 cli/app.py 拆出）— _interactive_loop 嵌套闭包的外提。

原实现是 _interactive_loop 内约 280 行闭包（radon 将嵌套函数复杂度聚合进宿主，
是 F(69) 的主因）；现外提为 SlashCommandProcessor，engine/status 构造注入，
quit_requested 由 nonlocal 改为实例属性（语义不变）。
命令体自 app.py 原样迁移，仅去掉一层闭包缩进。
"""

from typing import Any

from lingclaude.cli.repl_turn import _record_long_task_metrics

# Step 3: Tab 补全清单（F2 修复:删 /undo — handler 缺失不得留在补全里误导用户）
SLASH_COMPLETER_WORDS = [
    "/help", "/clear", "/compact", "/model", "/schedule", "/lsp",
    "/resume", "/continue", "/checkpoint", "/recover", "/quit",
]


class SlashCommandProcessor:
    """T1-7: 斜杠命令。handle() 返回 True 表示已消费；/quit /exit 置 quit_requested。"""

    def __init__(self, engine: Any, status: Any) -> None:
        self.engine = engine
        self.status = status
        self.quit_requested = False

    def handle(self, cmd: str) -> bool:
        """T1-7: 斜杠命令。返回 True 表示已消费；/quit /exit 置 quit_requested。"""
        parts = cmd.strip().split(maxsplit=1)
        if not parts or parts[0][:1] != "/":
            return False
        name = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        # 审计#3 修复:/quit /exit 此前无 handler → 落成用户消息发给 LLM，
        # 且补全列表还在引导用户输入（F2 删 /undo 时漏掉的同类问题）。
        if name in ("/quit", "/exit"):
            self.quit_requested = True
            return True
        if name in ("/help", "/?"):
            self._cmd_help()
            return True
        if name == "/clear":
            self.engine._messages.clear()
            self.engine._conversation.clear()
            print("[会话已清空]")
            return True
        if name == "/compact":
            self._cmd_compact()
            return True
        if name == "/model":
            self._cmd_model(arg)
            return True
        if name == "/schedule":
            self._cmd_schedule(arg)
            return True
        if name == "/lsp":
            self._cmd_lsp(arg)
            return True
        if name == "/checkpoint":
            self._cmd_checkpoint()
            return True
        if name == "/recover":
            self._cmd_recover()
            return True
        if name in ("/resume", "/continue"):
            self._cmd_resume(name, arg)
            return True
        return False

    # ---- P5 回路驱动拆分：P4.1 迁移的巨 handle (radon F(82)) 按命令分派拆方法 ----
    # 命令体自原 handle 原样机械迁移，仅 engine/status → self.engine/self.status。

    def _cmd_help(self) -> None:
        print("[斜杠命令]")
        print("  /help                  本帮助")
        print("  /clear                 清空会话上下文")
        print("  /compact               手动压缩（未达阈值时明确提示）")
        print("  /model [名称]          查看/钉住模型（--unpin 解除；--ttl N 秒后自动恢复路由）")
        print("  /schedule [表达式]      定时任务注册/列出/取消")
        print("  /lsp add|remove [参数]  LSP 服务器注册/删除（不带参数列出）")
        print("  /checkpoint             手动保存 checkpoint（R5 阶段1）")
        print("  /recover                恢复最近中断的工具轮 checkpoint")
        print("  /resume [ID]           恢复指定会话（不带 ID 列出全部；ID 支持短前缀）")
        print("  /continue              恢复最近一次会话（等价启动参数 --continue）")
        print("  /quit、/exit           退出")

    def _cmd_compact(self) -> None:
        # 审计#9 修复:此前无论是否达阈值都谎报「已触发压缩」— 实际多数
        # 时候 _compact_if_needed 内部条件不满足、什么都没做。
        from lingclaude.core.tool_executor import _estimate_message_tokens

        engine = self.engine
        msgs = engine._messages
        msg_limit = engine.config.compact_after_turns * 2
        token_threshold = int(engine.config.max_budget_tokens * 0.8)
        est_tokens = _estimate_message_tokens(msgs)
        if len(msgs) > msg_limit or est_tokens > token_threshold:
            engine._compact_if_needed()
            print(f"[已压缩] {len(msgs)} 条消息（阈值 {msg_limit} 条 / {token_threshold} tok）")
        else:
            print(f"[未压缩] 未达阈值：{len(msgs)}/{msg_limit} 条消息，~{est_tokens}/{token_threshold} tokens")
            print("[提示] 达到阈值后回合结束自动压缩；/compact 仅用于手动提前触发")

    def _cmd_model(self, arg: str) -> None:
        engine = self.engine
        status = self.status
        # /model: 显示当前/钉住模型；/model <name> 钉住模型（绕过 TaskRouter）；/model --unpin 解除钉住
        parts = arg.strip().split() if arg else []
        # /model --unpin
        if parts and parts[0] == "--unpin":
            result = engine.unpin_model()
            if result.is_ok:
                print(f"[已解除钉住] 恢复 TaskRouter 动态路由（原钉住: {result.data}）")
                status.set_pinned(False)
            else:
                print(f"[解除失败] {result.error}")
            return
        # /model <name> [--ttl N]
        if parts:
            model_name = parts[0]
            ttl = 0
            if "--ttl" in parts:
                try:
                    ttl_idx = parts.index("--ttl")
                    ttl = int(parts[ttl_idx + 1])
                except (IndexError, ValueError):
                    print("[用法] /model <name> [--ttl 秒数]")
                    return
            result = engine.pin_model(model_name, ttl_seconds=ttl)
            if result.is_ok:
                pinned_cfg = engine._pinned_model_config
                base = getattr(pinned_cfg, "base_url", "?") or "?"
                ttl_str = f" (TTL {ttl}s)" if ttl > 0 else " (会话级永久)"
                print(f"[已钉住模型] {result.data} @ {base}{ttl_str}")
                print("[提示] 支持选择器格式: /model model@provider 或 /model provider/model（对齐 opencode/crush）")
                print("[提示] 后续请求将强制使用此模型，忽略 TaskRouter 路由表；用 /model --unpin 解除")
                status.set_pinned(True)
            else:
                print(f"[钉住失败] {result.error}")
            return
        # /model (无参数): 显示当前/钉住状态
        pinned = engine.get_pinned_model_name()
        if pinned:
            pinned_cfg = engine._pinned_model_config
            base = getattr(pinned_cfg, "base_url", "?") if pinned_cfg else "?"
            import time
            ttl_remain = int(engine._pinned_model_expires - time.time()) if engine._pinned_model_expires != float('inf') else -1
            ttl_str = f" (剩余 {ttl_remain}s)" if ttl_remain > 0 else (" (会话级)" if ttl_remain < 0 else " (已过期，自动解除)")
            print(f"[当前钉住模型] {pinned} @ {base}{ttl_str}")
            print("[提示] 使用 /model --unpin 解除钉住，恢复动态路由")
        else:
            prov_cfg = getattr(engine._provider, "_config", None) if engine._provider else None
            model_name = getattr(prov_cfg, "model", "") or "?"
            base = getattr(prov_cfg, "base_url", "") or "?"
            print(f"[当前默认模型] {model_name} @ {base}")
            print("[提示] /model <name> 钉住模型（绕过 TaskRouter）；/model --unpin 解除")
            router = getattr(engine, "_task_router", None)
            if router is not None and router._providers:
                print("[可用模型] 按 provider 分组：")
                for pname, pinfo in router._providers.items():
                    if not pinfo.models:
                        continue
                    key_mark = "✓" if pinfo.api_key else "✗(无key)"
                    print(f"  {pname} [{key_mark}] ({pinfo.base_url}):")
                    for m in pinfo.models:
                        default_tag = " ←默认" if m == pinfo.default_model else ""
                        sel = f"{m}@{pname}"
                        print(f"    {m}{default_tag}  [{sel}]")
            else:
                print("[可用模型] TaskRouter 未加载或无 provider")

    def _cmd_schedule(self, arg: str) -> None:
        from lingclaude.core.scheduler import ScheduleType, get_schedule_manager

        mgr = get_schedule_manager()
        if not arg:
            # 列出任务
            tasks = mgr.list_tasks()
            if not tasks:
                print("[定时任务] 无任务")
            else:
                print(f"[定时任务] 共 {len(tasks)} 个：")
                for t in tasks:
                    print(f"  - {t.task_id[:8]} | {t.cron} | {t.query[:40]}")
        elif arg.startswith("cancel "):
            # 取消任务
            task_id = arg[7:].strip()
            if mgr.cancel(task_id):
                print(f"[已取消] {task_id}")
            else:
                print(f"[未找到] {task_id}")
        else:
            # 注册任务：/schedule @daily "查询内容"
            parts = arg.split(maxsplit=1)
            if len(parts) < 2:
                # 审计#10 修复:补上 scheduler 实际支持的 after:N / at:HH:MM
                # 接线门修复: ScheduleType 枚举作为帮助文本单一事实来源，
                # 避免文档与 _compute_next_run 解析器漂移
                predefs = "|".join(
                    t.value for t in ScheduleType if t is not ScheduleType.INTERVAL
                )
                print(f'[用法] /schedule {predefs}|{ScheduleType.INTERVAL.value}:N|after:N|at:HH:MM "任务内容"')
                print("       /schedule cancel <task_id>")
                print("       /schedule  (列出任务)")
            else:
                cron, query = parts
                try:
                    task_id = mgr.register(cron, query)
                    print(f"[已注册] {task_id[:8]} | {cron} | {query[:40]}")
                except ValueError as e:
                    print(f"[错误] {e}")

    def _cmd_lsp(self, arg: str) -> None:
        # lsp add/list/remove — 注册 LSP server 配置（对标 Crush `lsp add`）
        from lingclaude.engine.lsp_registry import list_servers, register, remove

        if not arg:
            print("[LSP 服务器] 内置 + 自定义：")
            for s in list_servers():
                mark = "内置" if s.get("default") else "自定义"
                print(f"  - {s['lang']:<12} {s['command']} {(' '.join(s.get('args', [])))} [{mark}]")
            print("用法: /lsp add <lang> --command <cmd> [--args 'a b'] | /lsp remove <lang>")
        elif arg.startswith("add "):
            rest = arg[4:].strip()
            # 审计#8 修复:手册示例用 Crush 风格 `/lsp add rust rust-analyzer`，
            # 旧实现只认 --command 语法 → 示例全部失效。两种语法都支持；
            # --args 引号改用 shlex 解析（此前 .split() 会留下残引号）。
            command = ""
            args: list[str] = []
            if "--command" in rest:
                import shlex

                lang_part, _, after = rest.partition("--command")
                lang = lang_part.strip()
                after = after.strip()
                if "--args" in after:
                    cmd_str, _, args_str = after.partition("--args")
                    cmd_tokens = shlex.split(cmd_str.strip())
                    command = cmd_tokens[0] if cmd_tokens else ""
                    args = shlex.split(args_str.strip())
                else:
                    cmd_tokens = shlex.split(after)
                    if cmd_tokens:
                        command, args = cmd_tokens[0], cmd_tokens[1:]
            else:
                import shlex

                tokens = shlex.split(rest)
                if len(tokens) >= 2:
                    lang, command, args = tokens[0], tokens[1], tokens[2:]
                else:
                    lang = tokens[0] if tokens else ""
            if not command or not lang:
                print('[用法] /lsp add <lang> <command> [args...]（Crush 风格）')
                print("       /lsp add <lang> --command <cmd> [--args 'a b']")
            else:
                try:
                    reg = register(lang, command, args)
                    print(f"[已注册] {reg['lang']} → {reg['command']} {' '.join(reg['args'])}")
                except ValueError as e:
                    print(f"[错误] {e}")
        elif arg.startswith("remove "):
            lang = arg[7:].strip()
            if remove(lang):
                print(f"[已删除] {lang}")
            else:
                print(f"[无法删除] {lang}（内置默认不可删，或不存在）")
        else:
            print("[用法] /lsp add <lang> --command <cmd> | /lsp remove <lang> | /lsp 列出")

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

    def _cmd_resume(self, name: str, arg: str) -> None:
        engine = self.engine
        # 会话恢复:/resume [ID] 恢复指定会话、/continue 恢复最近一次。
        # 复用启动参数 --resume/--continue 的同一套持久化接口（load_session），
        # 语义与启动时一致：恢复 = 替换当前上下文。
        sessions = engine.session_manager.list_sessions()
        if name == "/continue":
            # /continue = 无参恢复最近一次（对齐启动参数 --continue）
            if not sessions:
                print("[恢复失败] 没有可恢复的历史会话")
                return
            latest = max(sessions, key=lambda s: s.get("created_at", ""))
            target_id = str(latest.get("session_id", ""))
            print(f"[目标] 最近会话: {target_id}")
        elif not arg:
            # /resume 无参 = 列出全部（对齐帮助文本承诺；这是会话 ID 的发现入口）
            if not sessions:
                print("[会话列表] 无历史会话")
                return
            print(f"[会话列表] 共 {len(sessions)} 个（最近 10 个）：")
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
            print(f"[会话已恢复] {target_id}（{len(engine._conversation)} 轮对话；当前上下文已被替换）")
        else:
            print(f"[会话恢复失败] {target_id} 不存在或已损坏（当前上下文未受影响）")

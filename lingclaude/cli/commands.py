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
from lingclaude.cli._commands_checkpoint import SlashCommandCheckpointMixin
from lingclaude.cli._commands_history import SlashCommandHistoryMixin
from lingclaude.cli._commands_session import SlashCommandSessionMixin
from lingclaude.cli._commands_tasks import SlashCommandTasksMixin
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



class SlashCommandProcessor(SlashCommandHistoryMixin, SlashCommandSessionMixin,
                          SlashCommandCheckpointMixin, SlashCommandTasksMixin):
    """T1-7: 斜杠命令（P5 回路驱动拆分：按命令域分派到 4 个 mixin）。

    handle() 返回 True 表示已消费；/quit /exit 置 quit_requested。
    方法体自原 961 行巨类机械迁移，MRO 保证方法解析不变。
    """

    def __init__(self, engine: Any, status: Any, reader: Any = None,
                 submit: Any = None) -> None:
        self.engine = engine
        self.status = status
        # P1-4（2026-09-20）:/multi 多行模式的读/提交通道 —— 必须由 repl 注入：
        # reader 走 _next_input（pump/队列纪律，绝不直接 input()，H17 单读者教训）；
        # submit 把多行文本回注 ctx.queued_next（主循环既有消费机制，零新竞态）。
        self.reader = reader
        self.submit = submit
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
        # P1-4（2026-09-20）: 显式多行输入模式 —— 不依赖 Esc+Enter 键位记忆
        if name == "/multi":
            self._cmd_multi()
            return True
        # P3（2026-09-20，atomcode invalidate 借鉴）: 全量重绘输出窗
        if name == "/resync":
            self._cmd_resync()
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
        if name == "/openrouter":
            self._cmd_openrouter(arg)
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
        if name == "/rewind":
            self._cmd_rewind(arg)
            return True
        if name == "/fork":
            self._cmd_fork(arg)
            return True
        if name == "/share":
            self._cmd_share(arg)
            return True
        if name in ("/resume", "/continue"):
            self._cmd_resume(name, arg)
            return True
        if name == "/session":
            # 2026-09-15（会话问题重构 P1-2）: /session 命令 —— 列出当前
            # 项目会话（带摘要）/ 切换到指定会话。会话按当前工作目录隔离。
            self._cmd_session(arg)
            return True
        if name == "/history":
            # 2026-09-20（TUI 优化方案 P2-1）: 会话历史查看 —— 全屏 TUI 退出后
            # 滚轮回看不再可达，此命令是持久入口（复用 SessionManager 快照）。
            self._cmd_history(arg)
            return True
        if name in ("/tasks", "/todo", "/plan"):
            # 2026-09-17: 任务面板 —— 第1级（渲染）+ 第2级（状态纪律）：
            #   /tasks             列出活跃任务（in_progress 高亮 + pending）
            #   /tasks add <文本>  新增任务
            #   /tasks start <id>  置 in_progress（其他 in_progress 自动退回 pending）
            #   /tasks done <id>   完成一项（禁批量）
            #   /tasks all         全量（含 completed/cancelled）
            self._cmd_tasks(arg)
            return True
        return False

    # ---- P5 回路驱动拆分：P4.1 迁移的巨 handle (radon F(82)) 按命令分派拆方法 ----
    # 会话/检查点/任务域方法已外提到 _commands_*.py mixin，本类保留通用命令。

    def _cmd_multi(self) -> None:
        """P1-4（2026-09-20）: 显式多行输入模式。

        逐行累积，单独一行 '.' 结束并提交，Ctrl+C/EOF 放弃。
        读经 self.reader（_next_input 包装，pump/队列纪律，H17 单读者教训）；
        提交经 self.submit 回注 ctx.queued_next，由主循环既有机制消费——
        不直接碰 engine，不引入第二输入通道。
        """
        print("[多行模式] 逐行输入，单独一行 '.' 结束并提交；Ctrl+C 放弃")
        lines: list[str] = []
        while True:
            try:
                line = self.reader() if self.reader else None
            except (EOFError, KeyboardInterrupt, StopIteration):
                # StopIteration：generator 型 reader 耗尽 = EOF（场景B实测捕获，
                # 不纳入会让异常炸穿 handle() 打断主循环）
                print("[多行模式已放弃]")
                return
            if line is None:
                # reader 不可用（未注入）或返回空——放弃并提示走 Esc+Enter
                print("[多行模式] 读通道不可用，已放弃（提示：Esc+Enter 可换行）")
                return
            if line.strip() == ".":
                break
            lines.append(line)
            if len(lines) >= 500:
                print("[多行模式] 达 500 行上限，自动提交")
                break
        text = "\n".join(lines).strip()
        if not text:
            print("[多行模式] 空输入，已放弃")
            return
        if self.submit:
            self.submit(text)
            print(f"[多行模式] 已提交 {len(lines)} 行")
        else:
            print("[多行模式] 提交通道不可用，内容未提交（提示：Esc+Enter 可换行）")

    def _cmd_resync(self) -> None:
        """P3（2026-09-20，atomcode invalidate 借鉴）: 全量重绘输出窗。

        场景：resize 后怀疑失步 / 回放区有残留噪声 / 想强制刷新。
        实现从 output_source（会话历史）整体重建文档——用户无需知道
        哪条渲染路径漏了，一个原语兜底所有失步场景。
        """
        session = getattr(self, "session", None)
        resync = getattr(session, "resync", None)
        if callable(resync):
            try:
                resync()
                print("[输出窗已重绘]")
            except Exception as e:  # noqa: BLE001 — 重绘失败不阻塞会话
                print(f"[重绘失败] {e}")
        else:
            print("[重绘] 当前会话类型不支持（仅全屏 TUI 可用）")

    def _cmd_help(self) -> None:
        print("[斜杠命令]")
        print("  /help                  本帮助")
        print("  /multi                 多行输入模式（'.' 结束提交；平时用 Esc+Enter 换行）")
        print("  /clear                 清空会话上下文")
        print("  /compact               手动压缩（未达阈值时明确提示）")
        print("  /model [名称]          查看/钉住模型（--unpin 解除；--ttl N 秒后自动恢复路由）")
        print("  /schedule [表达式]      定时任务注册/列出/取消")
        print("  /lsp add|remove [参数]  LSP 服务器注册/删除（不带参数列出）")
        print("  /checkpoint             手动保存 checkpoint（R5 阶段1）")
        print("  /recover                恢复最近中断的工具轮 checkpoint")
        print("  /rewind [tag]           列出/回滚到历史 checkpoint 快照（P1 rewind）")
        print("  /resume [ID]           恢复指定会话（不带 ID 列出全部；ID 支持短前缀）")
        print("  /continue              恢复最近一次会话（等价启动参数 --continue）")
        print("  /tasks [add|start|done|all]  任务面板（对标 AtomCode：单 in_progress + 中断退回）")
        print("  /quit、/exit           退出")
        print("  /history [N]           最近 N 条会话列表；/history show <id> 查看记录")

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
                # ★ 修复: 同步写 model 字段,否则 toolbar 仍显示 stale model (L90 拼接旧 s.model + [PINNED])
                status.set_model(str(result.data))
                status.set_pinned(True)
            else:
                # 钉住失败必须清 toolbar 状态,否则老 pinned 显示残留
                # (用户以为新 pin 生效,但 toolbar 还显示旧 model + [PINNED])
                old_pinned = engine.get_pinned_model_name()
                status.set_pinned(False)
                print(f"[钉住失败] {result.error}")
                if old_pinned:
                    print(f"[!] 注意: 原钉住 {old_pinned} 仍然有效; 输入 /model --unpin 显式解除")
            return
        # /model (无参数): 显示当前/钉住状态
        pinned = engine.get_pinned_model_name()
        if pinned:
            pinned_cfg = engine._pinned_model_config
            base = getattr(pinned_cfg, "base_url", "?") if pinned_cfg else "?"
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

    def _cmd_openrouter(self, arg: str) -> None:
        """P1-8（2026-09-21）: OpenRouter 一键接入（学 atomcode 同款体验）。

        /openrouter          OAuth 授权（浏览器打开授权页，本地回调收 code）
        /openrouter status   查看接入状态（key 是否在 env/凭据仓 + 免费模型数）
        /openrouter logout   注销（删凭据仓 + 清 env）
        /openrouter models   刷新免费模型清单（:free 结尾）进 lingcode config
        授权成功后：key 落盘（0600）+ env 注入 + router api_key 热更新 +
        免费模型刷新，即刻可 /model openrouter/<:free 模型> 或 /model --unpin。
        """
        from lingclaude.model import openrouter_oauth as orx

        sub = (arg or "").strip().lower()
        if sub == "status":
            env_has = bool(os.environ.get(orx.ENV_KEY_NAME))
            saved = orx.load_saved_key()
            print(f"env {orx.ENV_KEY_NAME}: {'已设置' if env_has else '未设置'}")
            print(f"凭据仓: {'已存 key' if saved else '无存档'} ({orx._KEY_FILE})")
            try:
                from lingclaude.model.task_router import CONFIG_PATH
                cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                models = cfg["routing"]["providers"]["openrouter"].get("models", [])
                free = [m for m in models if m.endswith(":free")]
                print(f"路由清单: {len(models)} 模型（免费 {len(free)}）")
            except Exception as e:  # noqa: BLE001
                print(f"路由清单读取失败: {e}")
            return
        if sub == "logout":
            orx.clear_saved_key()
            print("已注销 OpenRouter（凭据删除 + env 清除）")
            return
        if sub == "models":
            print("正在拉取免费模型清单…")
            free = orx.fetch_free_models()
            if free is None:
                print("❌ 拉取失败（网络）——路由清单维持现状")
                return
            r = orx.merge_free_models_into_lingcode(free)
            print(f"✅ 免费 {len(free)} 个，新增 {r['added']}，路由清单合计 {r['total']}")
            return

        # 默认：OAuth 授权全流程
        if os.environ.get(orx.ENV_KEY_NAME) and not sub:
            print("OPENROUTER_API_KEY 已在环境中。重授权请先 /openrouter logout。")
            return
        print("正在连接 OpenRouter…（浏览器授权，或稍候）")
        result_holder: dict[str, Any] = {}

        def _flow() -> None:
            result_holder["r"] = orx.authorize()
            result_holder["done"].set()

        result_holder["done"] = threading.Event()
        th = threading.Thread(target=_flow, daemon=True, name="or-auth")
        th.start()
        # 等回调服务器起端口 → 打印授权 URL（authorize() 内部已生成，这里轮询等）
        deadline = time.time() + 10
        auth_url = ""
        while time.time() < deadline:
            au = getattr(orx, "_last_auth_url", "")
            if au:
                auth_url = au
                break
            time.sleep(0.1)
        if not auth_url:
            print("⚠ 回调服务器未及时就绪（10s），重试请再跑 /openrouter")
        else:
            print(f"浏览器未自动打开? 手动访问完成授权:\n{auth_url}")
        th.join(timeout=330)  # 授权等待 300s + 余量
        r = result_holder.get("r")
        if r is None:
            print("❌ 授权流程无结果（超时）")
            return
        if not r.ok:
            print(f"❌ 接入失败: {r.error}")
            return
        print("✅ 已接入 OpenRouter，key 已落盘并注入环境")
        # router 热更新 + 免费模型刷新
        try:
            router = getattr(self.engine, "_task_router", None)
            n = router.refresh_api_keys() if router else 0
            print(f"路由层已热更新（{n} 个 provider key 变化）")
        except Exception as e:  # noqa: BLE001
            print(f"路由层热更新跳过: {e}")
        free = orx.fetch_free_models()
        if free:
            rr = orx.merge_free_models_into_lingcode(free)
            print(f"新增免费模型 {rr['added']} 个（路由清单合计 {rr['total']}）。/model openrouter/<模型> 即可切换。")
        else:
            print("免费模型清单拉取失败，可稍后 /openrouter models 重试")

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
        from lingclaude.engine.lsp_registry import check_server, list_servers, register, remove

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
        elif arg.startswith("check "):
            # cc P0: /lsp check <lang> — 握手验证 server 协议可用性
            lang = arg[6:].strip()
            if not lang:
                print("[用法] /lsp check <lang> — 验证 LSP server 握手")
            else:
                print(f"[LSP 检查] {lang} ...")
                result = check_server(lang)
                if result.get("ok"):
                    caps = result.get("capabilities", {})
                    print(f"[OK] {lang} → {result['command']} 握手成功")
                    if caps:
                        print(f"      capabilities: {list(caps)[:6]}")
                else:
                    print(f"[失败] {lang}: {result.get('error', '未知错误')}")
        else:
            print("[用法] /lsp add <lang> --command <cmd> | /lsp remove <lang> | /lsp check <lang> | /lsp 列出")

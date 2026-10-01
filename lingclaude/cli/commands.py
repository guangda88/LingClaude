"""斜杠命令处理器（P4.1 从 cli/app.py 拆出）— _interactive_loop 嵌套闭包的外提。

原实现是 _interactive_loop 内约 280 行闭包（radon 将嵌套函数复杂度聚合进宿主，
是 F(69) 的主因）；现外提为 SlashCommandProcessor，engine/status 构造注入，
quit_requested 由 nonlocal 改为实例属性（语义不变）。
命令体自 app.py 原样迁移，仅去掉一层闭包缩进。
"""

from typing import Any, NamedTuple

import json
import logging
import os
import platform
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

# Step 3: Tab 补全清单 → A(2026-09-26) 改为注册表派生（见文件末尾
# SLASH_REGISTRY / SLASH_COMPLETER_WORDS）：补全、/help、handle() 三者单源，
# 根除「handler 在、补全/help 漏登」类两账本缺陷（/fork、/multi 历史先例）。



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
        """T1-7: 斜杠命令。返回 True 表示已消费；/quit /exit 置 quit_requested。

        A(2026-09-26) 注册表化：分派由 SLASH_REGISTRY 驱动（查表 → needs_args
        检查 → 调 handler），命令/别名/handler 单源。补全清单与 /help 文本
        亦从注册表派生（SLASH_COMPLETER_WORDS / _cmd_help）。
        """
        parts = cmd.strip().split(maxsplit=1)
        if not parts or parts[0][:1] != "/":
            return False
        name = parts[0].lower()
        # 审计#3 修复（语义保留）:/quit /exit 特判置 quit_requested。
        # 注册表里两条以 handler=None 登记仅供补全/帮助派生，不可分发。
        if name in ("/quit", "/exit"):
            self.quit_requested = True
            return True
        # A(2026-09-26) 歧义形状守卫（atomcode parse_slash_line 借鉴）：
        # "/" 后必须是命令形 token（[A-Za-z0-9_?:-]+）——含路径字符的
        # /Users/me、/tmp、/etc/hosts 不是命令，原样放行给模型/管道。
        if _SLASH_TOKEN_RE.match(name) is None:
            return False
        arg = parts[1] if len(parts) > 1 else ""
        entry = SLASH_REGISTRY.get(name)
        if entry is None or entry.handler is None:
            # handler=None（/quit /exit）已在头部特判消费；落到这里说明
            # 注册了无 handler 又没有特判的命令 → 不吞不炸。
            return False
        if entry.needs_args and not arg.strip():
            # atomcode needs_args 借鉴：无参无意义的命令先要参数，不空跑
            print(f"[{name}] 缺参数：{entry.arg_hint}")
            return True
        if entry.wants_arg:
            entry.handler(self, arg)
        else:
            entry.handler(self)
        return True

    # ---- P5 回路驱动拆分：P4.1 迁移的巨 handle (radon F(82)) 按命令分派拆方法 ----
    # 会话/检查点/任务域方法已外提到 _commands_*.py mixin，本类保留通用命令。

    def _cmd_clear(self) -> None:
        """A(2026-09-26) 注册表化时从 handle() 内联逻辑外提（语义不变）。"""
        self.engine._messages.clear()
        self.engine._conversation.clear()
        print("[会话已清空]")

    # ---- A(2026-09-26) 注册表适配层：三参 handler 折成统一 (self, arg) 形状 ----

    def _cmd_resume_adapted(self, arg: str = "") -> None:
        """/resume → _cmd_resume("/resume", arg)。"""
        self._cmd_resume("/resume", arg)

    def _cmd_continue_adapted(self, arg: str = "") -> None:
        """/continue → _cmd_resume("/continue", arg)（恢复最近一次）。"""
        self._cmd_resume("/continue", arg)

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

    def _cmd_image(self, arg: str = "") -> None:
        """TUI 图片粘贴（2026-10-01）：读取剪贴板图片并附到下一条消息。

        读取顺序：wl-paste（Wayland）→ xclip（X11）→ pbpaste（macOS）
        → PIL from clipboard（跨平台 fallback）。成功时将 (raw_bytes, mime)
        追加到 session._pending_images；repl.py 主循环提交时 drain，
        _build_messages 转为 base64 填入 ModelMessage，发给支持多模态的端点。

        不在 TUI 会话下打印提示并退出（不阻塞主流程）。
        """
        session = getattr(self, "session", None)
        if session is None or not hasattr(session, "register_image_attachment"):
            print("[/image] 当前会话类型不支持（仅全屏 TUI 可用）")
            return

        raw_bytes: bytes | None = None
        mime_type: str = "image/png"

        # ── 路径模式（2026-10-01）：SSH/纯终端场景剪贴板拿不到像素，
        #    截图工具先存文件，/image <路径> 直接读文件附图。
        #    PIL 验真：拒绝伪装成图片的任意文件。
        path_arg = (arg or "").strip().strip("'\"")
        if path_arg:
            p = Path(path_arg).expanduser()
            if not p.is_file():
                print(f"[/image] 文件不存在: {p}")
                return
            try:
                from PIL import Image as _PILImage
                with _PILImage.open(p) as im:  # noqa: SIM115 — 校验用，立即关
                    fmt = im.format
                    im.verify()
                raw_bytes = p.read_bytes()
                mime_type = f"image/{(fmt or 'PNG').lower()}"
            except Exception:  # noqa: BLE001 — 非图片/损坏文件
                print(f"[/image] 不是有效图片文件: {p}")
                return

        # 平台检测：按优先级尝试各剪贴板读取方案
        system = platform.system()

        # ── Wayland ──
        if raw_bytes is None:
            try:
                r = subprocess.run(
                    ["wl-paste", "-t", "image/png"],
                    capture_output=True, timeout=5,
                )
                if r.returncode == 0 and r.stdout:
                    raw_bytes = r.stdout
                    mime_type = "image/png"
            except Exception:  # noqa: BLE001 — 非 Wayland / wl-paste 不可用，正常继续
                pass

        # ── X11 ──
        if raw_bytes is None:
            try:
                r = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                    capture_output=True, timeout=5,
                )
                if r.returncode == 0 and r.stdout:
                    raw_bytes = r.stdout
                    mime_type = "image/png"
            except Exception:  # noqa: BLE001
                pass

        # ── macOS ──
        if raw_bytes is None and system == "Darwin":
            try:
                r = subprocess.run(
                    ["pbpaste"],
                    capture_output=True, timeout=5,
                )
                if r.returncode == 0 and r.stdout:
                    raw_bytes = r.stdout
                    mime_type = "image/png"
            except Exception:  # noqa: BLE001
                pass

        # ── PIL/Pillow fallback（跨平台，无需 X/Wayland） ──
        if raw_bytes is None:
            try:
                from PIL import Image
                import io
                img_buffer = io.BytesIO()
                try:
                    from PIL import ImageGrab
                    img = ImageGrab.grabclipboard()
                except Exception:  # noqa: BLE001 — Linux 无 ImageGrab
                    img = None
                if img and hasattr(img, "save"):
                    img.save(img_buffer, format="PNG")
                    raw_bytes = img_buffer.getvalue()
                    mime_type = "image/png"
            except ImportError:
                pass  # Pillow 未安装
            except Exception:  # noqa: BLE001
                pass

        if raw_bytes is None:
            print("[/image] 剪贴板无可用图片，或读取失败（需安装 wl-paste/xclip/Pillow）")
            return

        n = session.register_image_attachment(raw_bytes, mime_type)
        size_kb = len(raw_bytes) // 1024
        print(f"[/image] 已附上图片（{size_kb}KB，第 {n} 张）；输入消息后自动发送。")

    def _cmd_help(self, arg: str = "") -> None:
        """A(2026-09-26): 帮助文本从 SLASH_REGISTRY 派生（单源，防漏登）。

        /help        全量清单（注册表顺序）
        /help <cmd>  单条用法（含别名）
        """
        arg = (arg or "").strip()
        if arg:
            name = arg if arg.startswith("/") else f"/{arg}"
            entry = SLASH_REGISTRY.get(name.lower())
            if entry is None:
                print(f"[help] 未知命令 {name}（/help 查看全部）")
                return
            alts = sorted(o.name for o in SLASH_REGISTRY.values()
                          if o is not entry and o.handler is entry.handler
                          and not o.hidden)
            alias_s = f"（别名: {'、'.join(alts)}）" if alts else ""
            print(f"  {entry.name:<24}{entry.desc}{alias_s}")
            return
        print("[斜杠命令]")
        printed: set[int] = set()
        for entry in SLASH_REGISTRY.values():
            if id(entry) in printed or entry.hidden:
                continue
            printed.add(id(entry))
            names = [entry.name]
            for other in SLASH_REGISTRY.values():
                if (other is not entry and other.handler is entry.handler
                        and not other.hidden and id(other) not in printed):
                    names.append(other.name)
                    printed.add(id(other))
            print(f"  {'、'.join(names):<24}{entry.desc}")

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

    def _cmd_webui(self, arg: str = "") -> None:
        """/webui — 启动 WebUI（默认远程访问 0.0.0.0 + 引擎，后台运行不卡 REPL）。

        /webui            远程模式（默认）：0.0.0.0 + 自动拉引擎 + 打印可分享 token URL
        /webui --local    仅本机 127.0.0.1 + 开浏览器
        /webui --port N   指定 webUI 端口（默认 23458）
        /webui --no-engine 不自动拉起引擎（假定 8700 已跑）

        安全防护（webui-server auth.rs 已落地，本层叠加）：
          - /mint 按 ConnectInfo peer-IP 收口（仅本机可签发 handoff，远程靠分享 URL）
          - host_guard Host 白名单 + LAN IP 自动注入 + 缺 Host 403
          - handoff 一次性 + 5min TTL + 24h cookie；mint 限流 10 次/分钟
        """
        import shlex as _shlex
        from lingclaude.cli import app as _app

        args = _shlex.split(arg) if arg and arg.strip() else []
        remote = "--local" not in args          # 默认远程
        open_browser = "--local" in args        # 本机模式才开浏览器
        with_engine = "--no-engine" not in args  # 默认自动拉引擎
        port = 23458
        if "--port" in args:
            try:
                port = int(args[args.index("--port") + 1])
            except (IndexError, ValueError):
                print("[用法] /webui [--local] [--port N] [--no-engine]")
                return

        mode = "远程(0.0.0.0)" if remote else "本机(127.0.0.1)"
        print(f"[webui] 启动中：{mode} port={port} engine={'auto' if with_engine else 'external'}")
        if remote:
            print("[webui] 远程访问已启用——token URL 即凭证，仅分享给可信设备；"
                  "/mint 仅本机可签发，远程靠下方 URL 进入")

        def _run() -> None:
            rc = _app.launch_webui(
                port=port, engine_port=8700,
                remote=remote, with_engine=with_engine,
                open_browser=open_browser,
            )
            print(f"[webui] 服务已退出 (rc={rc})")

        t = threading.Thread(target=_run, daemon=True, name="webui-launch")
        t.start()

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


# ===========================================================================
# ===========================================================================
# A(2026-09-26): 斜杠命令注册表 —— 命令/别名/handler/参数要求单源。
#
# 补全清单（SLASH_COMPLETER_WORDS）、/help 帮助文本、handle() 分派三者均由
# 本表派生：新增命令 = 加一行 _register(...)，三处自动同步，根除「handler 在、
# 补全/help 漏登」两账本缺陷（/fork、/multi 历史先例）。
# 数据模型对标 atomcode-tuix/src/commands.rs（needs_args/hidden/aliases）。
# ===========================================================================

import re as _re

_SLASH_TOKEN_RE = _re.compile(r"^/[A-Za-z0-9_?:-]+$")


class SlashCommand(NamedTuple):
    name: str                 # "/tasks" 形式（含斜杠）
    handler: Any              # Callable[[SlashCommandProcessor, str], None]
    desc: str                 # /help 展示
    aliases: tuple = ()       # 主名同义别名
    needs_args: bool = False  # 无参数无意义 → handle() 拦截并提示用法
    arg_hint: str = ""        # needs_args 提示文案
    wants_arg: bool = True    # handler 签名带 arg？（_register 自动探测）
    hidden: bool = False      # 预留：不进补全/help 但可分发


SLASH_REGISTRY: dict[str, SlashCommand] = {}


def _h(method_name: str) -> Any:
    """按方法名取 SlashCommandProcessor 的未绑定方法（注册表延迟解析）。"""
    return getattr(SlashCommandProcessor, method_name)


def _register(
    name: str, handler: str, desc: str,
    aliases: tuple = (), needs_args: bool = False, arg_hint: str = "",
) -> None:
    """注册一条命令；别名展开为可见条目（符号形别名除外，见 hidden）。

    wants_arg 由 handler 签名自动探测（(self) → 无参；(self, arg) → 带参），
    调用方 handle() 据此选择调用形态，handler 无需迁就统一签名。
    """
    import inspect as _inspect

    fn = _h(handler)
    try:
        n_params = len(_inspect.signature(fn).parameters)
    except (ValueError, TypeError):  # pragma: no cover — 内建/异常签名按带参处理
        n_params = 2
    if n_params > 2:
        raise ValueError(
            f"{handler} 有 {n_params} 个参数：注册表统一 (self, arg) 形状，"
            "请先加适配方法（参见 _cmd_resume_adapted）"
        )
    wants_arg = n_params >= 2
    entry = SlashCommand(
        name=name, handler=fn, desc=desc, aliases=aliases,
        needs_args=needs_args, arg_hint=arg_hint, wants_arg=wants_arg,
    )
    SLASH_REGISTRY[name] = entry
    for alias in aliases:
        SLASH_REGISTRY[alias] = SlashCommand(
            # 别名条目 name=别名自身（补全派生按 name 去重，存主名会丢别名）
            name=alias, handler=fn, desc=desc,
            aliases=(), needs_args=needs_args, arg_hint=arg_hint,
            wants_arg=wants_arg,
            # 别名默认进补全（/continue /todo 是用户已知命令）；符号形
            # 别名（/?）不进 —— 补全菜单里出现 "/?" 是噪声。
            hidden=alias == "/?",
        )


# 表体顺序 = /help 展示顺序（补全清单同序派生）。
_register("/help", "_cmd_help", "本帮助；/help <命令> 查单条用法", aliases=("/?",))
_register("/clear", "_cmd_clear", "清空会话上下文")
_register("/multi", "_cmd_multi", "多行输入模式（'.' 结束提交；平时用 Esc+Enter 换行）")
_register("/resync", "_cmd_resync", "全量重绘输出窗（全屏 TUI）")
_register("/image", "_cmd_image",
          "附图到下一条消息：/image（剪贴板）或 /image <图片路径>")
# /policy 已迁斜杠插件（slash_plugins/policy.py），由模块尾 loader 挂载
_register("/compact", "_cmd_compact", "手动压缩上下文（未达阈值时明确提示）")
_register("/model", "_cmd_model", "查看/钉住模型（--unpin 解除；--ttl N 秒后自动恢复）")
_register("/schedule", "_cmd_schedule", "定时任务注册/列出/取消")
_register("/openrouter", "_cmd_openrouter", "OpenRouter 一键接入：/openrouter [status|logout|models]")
_register("/webui", "_cmd_webui",
          "启动 WebUI（默认远程 0.0.0.0+引擎，后台）：/webui [--local|--port N|--no-engine]")
_register("/lsp", "_cmd_lsp", "LSP 服务器注册/删除/握手检查：/lsp add|remove|check")
_register("/checkpoint", "_cmd_checkpoint", "手动保存 checkpoint")
_register("/recover", "_cmd_recover", "恢复最近中断的工具轮 checkpoint")
_register("/rewind", "_cmd_rewind", "列出/回滚到历史 checkpoint 快照")
_register("/undo", "_cmd_undo", "回滚工具写入的文件（文件级 rewind，M1）")
_register("/fork", "_cmd_fork", "分叉当前会话（rollout 不可变分叉）")
_register("/share", "_cmd_share", "自包含导出当前会话副本（JSONL，redact 已过）")
_register("/resume", "_cmd_resume_adapted", "恢复指定会话；不带 ID 列出全部")
_register("/continue", "_cmd_continue_adapted", "恢复最近一次会话（等价启动参数 --continue）")
_register("/session", "_cmd_session", "列出/切换当前项目会话")
_register("/history", "_cmd_history", "最近 N 条会话列表；/history show <id> 查看记录")
_register("/tasks", "_cmd_tasks", "任务面板：/tasks [add|start|done|all]",
          aliases=("/todo", "/plan"))
# /quit /exit 无 handler：handle() 特判置 quit_requested（语义即退出）。
# /quit /exit 均保留在补全清单（旧行为：两条都在 SLASH_COMPLETER_WORDS）。
SLASH_REGISTRY["/quit"] = SlashCommand(
    name="/quit", handler=None, desc="退出", aliases=(),
    needs_args=False, arg_hint="",
)
SLASH_REGISTRY["/exit"] = SlashCommand(
    name="/exit", handler=None, desc="退出（/quit 同义）", aliases=(),
    needs_args=False, arg_hint="",
)

# A(2026-09-30) 斜杠命令插件化（方案 A）：挂载点必须在补全清单派生之前，
# 插件命令才能进 SLASH_COMPLETER_WORDS；运行中新增插件由 /policy reload 重扫。
try:
    from lingclaude.cli.slash_plugin_loader import load_slash_plugins as _lsp
    _lsp()
except Exception as _e:  # noqa: BLE001 — 插件加载失败不拖垮 CLI 启动
    print(f"[slash 插件加载失败] {_e}")

SLASH_COMPLETER_WORDS = []
_seen: set[str] = set()
for _entry in SLASH_REGISTRY.values():
    if _entry.hidden or _entry.name in _seen:
        continue
    _seen.add(_entry.name)
    SLASH_COMPLETER_WORDS.append(_entry.name)
del _entry, _seen

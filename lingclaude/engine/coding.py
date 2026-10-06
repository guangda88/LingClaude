from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4
from typing import Any

from lingclaude.core.config import lingclaudeConfig
from lingclaude.core.gray_zone import gray_zone_escalate
from lingclaude.engine.lineedit import add_history_line, ensure_readline
from lingclaude.engine.loop.tool_loop_detector import _ToolLoopDetector
from lingclaude.core.permissions import PermissionStore
from lingclaude.core.session_runtime import SessionRuntime
from lingclaude.engine.sensitive_path_gate import is_readonly_bash_command
from lingclaude.engine.verify_cadence import VerifyCadenceHook
from lingclaude.engine.tool_registration import register_all_tools
from lingclaude.self_optimizer import (
    OptimizationAdvisor,
    OptimizationTrigger,
    StructureEvaluator,
)
from lingclaude.engine.git import git_blame, git_diff, git_log, git_status
from lingclaude.engine.indexer import index_project
from lingclaude.engine.ast_edit import list_functions, replace_function_body
from lingclaude.engine.verification_gate import VerificationGate
from lingclaude.self_optimizer.learner.patterns import PatternRecognizer
from lingclaude.engine.todo import TodoStore, make_handlers as _make_todo_handlers
from lingclaude.engine.lsp_provider import StdioLspProvider, _path_to_uri
from lingclaude.engine.tool_handlers import (
    BackgroundToolsMixin,
    BashToolsMixin,
    FileToolsMixin,
    GitToolsMixin,
    LspToolsMixin,
    PlanToolsMixin,
    SearchToolsMixin,
    SkillToolsMixin,
    SubagentToolsMixin,
    TodoToolsMixin,
    WebToolsMixin,
)


def _user_input_timeout() -> float:
    """request_user_input TTY 路径超时秒数（环境变量可调，默认 120）。

    B 方案兜底：超时后降级为默认答案而非挂起。设为 <=0 表示不超时（慎用）。
    """
    import os

    raw = os.environ.get("LINGCLAUDE_USER_INPUT_TIMEOUT", "120")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 120.0
    return val if val > 0 else float("inf")


def _read_line_with_timeout(stdin: Any, timeout_s: float) -> str | None:
    """select 轮询读一行，超时返回 None（B 方案核心，替代裸 input()）。

    仅在 stdin 是真实 tty 时调用（handler 已判定）。select 不可用时回退
    input()（保持原语义，超时保护仅在不支持 select 的平台失效）。
    """
    import math
    import select

    if math.isinf(timeout_s):
        return input()
    try:
        fd = stdin.fileno()
    except (OSError, ValueError, AttributeError):
        return input()  # 无 fd（如 StringIO 伪 tty）：select 不可用，回退
    try:
        r, _, _ = select.select([fd], [], [], timeout_s)
    except (OSError, ValueError):
        return input()  # select 失败回退（不静默挂死，至少维持原行为）
    if not r:
        return None  # 超时
    return input()


def _record_tui_deadlock_incident(timeout: bool = False) -> None:
    """把 request_user_input TUI 死锁/超时事件落一条 LearnedRule（fail-soft）。

    隔壁进程 1170371 实录：TUI 模式调用 request_user_input 死锁、Ctrl+C/D
    无效只能 kill。此类死锁已发生多次，落库让断点③聚类归因能识别前兆。
    任何异常静默吞掉（logger.warning），绝不阻断工具主流程。
    """
    try:
        import logging

        from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
        from lingclaude.self_optimizer.learner.models import (
            FeedbackCategory,
            LearnedRule,
            Pattern,
        )

        kind = "timeout" if timeout else "tui_deadlock"
        kb = KnowledgeBase()
        try:
            rule = LearnedRule(
                id="request_user_input_tui_deadlock",
                name="request_user_input 死锁前兆",
                description=(
                    "request_user_input 在 full-screen TUI（prompt_toolkit 接管终端、"
                    "stdout 被替换为 _StdoutProxy）下调用会死锁：stdin 仍是 tty 故走 "
                    "input() C 级阻塞，SIGINT/Ctrl+D 无效，只能 kill 进程"
                    "（2026-09-28 隔壁进程 1170371 实录，Ctrl+C/D 均无效）。"
                    "已在 handler 入口按 stdout 非 tty 指纹熔断为降级提示，"
                    "TTY 路径加 select 超时兜底。此类死锁已发生多次。"
                    if not timeout
                    else
                    "request_user_input TTY 路径用户输入超时，已降级为默认答案"
                    "（选项 1）。用户可直接文字回复覆盖，loop 不再挂起。"
                ),
                category=FeedbackCategory.TOOL_ERROR,
                pattern=Pattern(
                    context_keywords=("request_user_input", "tui", "deadlock", kind),
                    tool_support=("request_user_input",),
                ),
                tools=("request_user_input",),
                frequency=1,
                confidence=0.9,
                quality_score=0.9,
                status="active",
            )
            kb.add_rule(rule)
        finally:
            kb.close()
    except Exception as exc:  # noqa: BLE001 — 落库失败绝不反噬工具主流程
        logging.getLogger(__name__).warning(
            "record tui deadlock incident failed: %s", exc
        )


class CodingRuntime(
    BashToolsMixin, FileToolsMixin, SearchToolsMixin, GitToolsMixin,
    SubagentToolsMixin, BackgroundToolsMixin, WebToolsMixin,
    PlanToolsMixin, TodoToolsMixin, LspToolsMixin, SkillToolsMixin,
):
    def __init__(self, config: lingclaudeConfig | None = None, model_provider: Any | None = None) -> None:
        self.config = config or lingclaudeConfig()
        # P1 (2026-09-28): model_provider 入槽 —— 第一个真插片走 Slot 重机械。
        # 主干不再持 provider 实例，改持 SlotHandle（调用时解析，根治快照泄漏）。
        # 换 provider/config model 经 SlotManager.rebuild 零重启生效（swap 协议）。
        # 初始实例仍惰性自建（api.py/bus_responder/mcp 等路径构建 CodingRuntime
        # 时不传 provider，子代理 ctx 继承到 None 直接失败）；自建失败保持 None
        # （fail-soft，子代理仍报原错误而非新异常）。
        from lingclaude.core.slot import SlotManager

        self._slot_manager = SlotManager()
        # P4: 构造入参存下来，供 SLOT_WIRING_MANIFEST 的 model_provider 工厂读取
        # （_initial_model_provider 优先用这个注入值，与 P1 内联逻辑逐字对齐）。
        self._injected_model_provider = model_provider
        # P3 (2026-09-28): 轻通道 4 件接 LightChannelRuntime —— PolicyLoader 配置读穿
        # + digest 惰性重建（策略调参零重启）。pattern_recognizer / verification_gate
        # 用保形状包装器（代理到惰性重建的内部实例），消费点零改动。
        from lingclaude.engine.light_channel_runtime import (
            LazyLoopDetector,
            LazyPatternRecognizer,
            LazyVerificationGate,
            LazyVerifyCadence,
            LightChannelRuntime,
        )

        from lingclaude.core.config import VerificationConfig

        self._light = LightChannelRuntime(
            verification_base=(
                self.config.verification
                if self.config.verification is not None
                else VerificationConfig()
            )
        )
        self._pattern_recognizer = LazyPatternRecognizer(self._light.pattern_recognizer)
        # P0: 走 from_config 工厂（主干绕过工厂裸调是 verification 配置不生效的根因）。
        # P3: verification_gate 走轻通道，config.verification 为基线 + 策略外置叠加，
        # 二者同时生效（config.yaml 为主，策略文件调参零重启覆盖）。
        self.verification_gate = LazyVerificationGate(self._light.verification_gate)
        # P4: 3 个真插片槽注册数据化（SLOT_WIRING_MANIFEST），在 _setup_tools 前完成
        # —— 工厂可能引用 config；工具装配在注册后进行。轻通道先行（时序要求）。
        from lingclaude.engine.coding_wiring import (
            CodingWiringContext,
            assemble_coding_slots,
        )

        assemble_coding_slots(
            CodingWiringContext(runtime=self, sandbox_policy=None),
            self._slot_manager,
        )
        # P4: _model_provider 已由 assemble_coding_slots setattr（attr="_model_provider"）。
        # subagent_tools 经 self._model_provider 拿 SlotHandle；句柄解析到当前代际。
        # 环 3 接线（2026-09-28）：把 model_provider 槽注册进进程级 config 热更通路，
        # config.yaml model 变化 → HotReloadTrigger.check() 自动 rebuild（零重启换 provider）。
        # 工厂外移 coding_wiring（G10 合规：__init__ 不裸调 create_provider）——
        # 热更工厂直接 load_config() 读「当前 config.yaml」（不经装配期注入快照）。
        from lingclaude.engine import tools as _tools_mod
        from lingclaude.engine.coding_wiring import _initial_model_provider_hotreload

        _tools_mod.register_config_slot(
            "model_provider", self._slot_manager, _initial_model_provider_hotreload
        )
        self._setup_tools()
        # P4: todo_store 槽已在开头统一注册（SLOT_WIRING_MANIFEST）。此处只建派生物。
        # _todo_handlers 是派生物：随源槽一次重算（变化频率绑定 todo_store）
        self._todo_handlers = _make_todo_handlers(self._todo_store)
        # P0 (2026-09-28): _lsp_provider 死槽移除 —— 恒 None 且生产走 LspSessionPool，
        # 徒占主干一个直构位。legacy 注入路径由 lsp_tools 的 getattr 兜底承载
        # （tool_handlers/lsp_tools.py:60 读 getattr(self, '_lsp_provider', None)）。

        # P0.2 (E1 修活熔断): 5b 分支的真实依赖 — 此前从未初始化,
        # execute_tool._blocks 里对 _session_runtime/_loop_detector 的双重
        # hasattr 永远为 False，observe_denial 熔断是死代码（V3 §三 E1）。
        # P3: loop_detector 走轻通道（保形状包装器，阈值外置策略可热更）
        self._loop_detector = LazyLoopDetector(self._light.loop_detector)
        self._denial_abort_log: str | None = None
        # 会话身份单源（2026-10-07 会话级隔离）：不再构造期固化
        # getattr(config,'session_id','default')（lingclaudeConfig 无该字段，
        # 旧写法恒为 "default" → todo/权限审批等会话级数据全部串台）。
        # session_id 改为动态 property（见类尾），跟随宿主 engine（set_runtime
        # 注入的 QueryEngine）的真实会话 id；resume/--continue/L2 压缩换 id 后
        # 自动跟随。无宿主的独立装配路径回落 config 字段或随机新 id（惰性）。
        # P4: session_runtime 槽已在开头统一注册（SLOT_WIRING_MANIFEST）。
        # 句柄经 getattr 透明代理，lifecycle mixin / subagent_tools 访问零改动。
        # P2 (2026-09-18): VerifyCadenceHook — 编辑后未验证 nudge + 死循环检测。
        # T0 零推理（纯字符串匹配），独立开关 LINGCLAUDE_VERIFY_CADENCE（默认开）。
        # P3: 走轻通道（保形状包装器，env 开关迁入 YAML 策略可热更）
        self._verify_cadence = LazyVerifyCadence(self._light.verify_cadence)

    # ── 会话身份单源（2026-10-07 会话级隔离）─────────────────────────────
    # 旧实现：构造期 self.session_id = getattr(config,'session_id','default')
    # → lingclaudeConfig 无顶层 session_id 字段，恒为 "default"；与宿主
    # QueryEngine（uuid4 随机 id，resume/load_session/L2 压缩会换 id）身份
    # 分裂 —— todo 面板、审批决策、flywheel 日志全部落 "default" 一个桶里。
    # 新语义：runtime 无自己的会话身份，始终委托宿主 engine；宿主换 id
    # （/resume /continue /session switch /rewind /clear L2压缩）零接线跟随。
    @property
    def session_id(self) -> str:
        host = getattr(self, "_host_engine", None)
        if host is not None:
            return str(getattr(host, "session_id", "") or "")
        cfg_sid = getattr(self.config, "session_id", "") if self.config is not None else ""
        if cfg_sid:
            return str(cfg_sid)
        # 无宿主兜底：惰性随机（api.py / bus_responder 独立装配路径），
        # 每实例稳定、实例间不串台。
        sid = getattr(self, "_fallback_session_id", None)
        if sid is None:
            sid = uuid4().hex[:16]
            self._fallback_session_id = sid
        return sid

    @session_id.setter
    def session_id(self, value: str) -> None:
        # 写穿透到宿主（engine.session_id = X 后 runtime.session_id 同步可见）；
        # 无宿主时落 fallback。l5_audit L2 压缩等「engine.session_id = 新id」
        # 调用若经 runtime 句柄书写，语义不变。
        host = getattr(self, "_host_engine", None)
        if host is not None:
            host.session_id = value
        else:
            self._fallback_session_id = value

    def set_host_engine(self, host: Any) -> None:
        """绑定宿主 engine（app/api/bus_responder 的 set_runtime 后调用）。"""
        self._host_engine = host

    def close(self) -> None:
        """释放资源（进程退出/会话结束调用）。

        - LSP provider 子进程（若已惰性初始化）
        - BackgroundTaskManager 线程池（background.py:141 shutdown 已存在但从未被调用 —
          导致解释器 shutdown 阶段 _python_exit join 线程池时抛 KeyboardInterrupt）
        """
        # LSP: 常驻会话池统一回收（codex P1-1, 2026-09-17）+ legacy 注入 provider
        from lingclaude.engine.lsp_session import get_pool

        try:
            get_pool().close_all()
        except Exception:  # noqa: BLE001 — LSP 关闭失败不影响主进程退出
            pass
        lsp = getattr(self, "_lsp_provider", None)
        if lsp is not None:
            try:
                import asyncio

                asyncio.run(lsp.shutdown())
            except Exception:  # noqa: BLE001 — LSP 关闭失败不影响主进程退出
                pass
        # 后台任务线程池
        bg = getattr(self, "_background_manager", None)
        if bg is not None:
            try:
                bg.shutdown()
            except Exception:  # noqa: BLE001 — 线程池关闭失败不影响主进程退出
                pass

    def _setup_tools(self) -> None:
        # Q5 (2026-09-14): 42 行硬编码装配收敛至 engine/coding_wiring.py 的
        # CODING_WIRING_MANIFEST（工厂 + manifest 数据化，对位 QueryEngine
        # WIRING_MANIFEST 模式）。主干不再直接构造 handler 实例 —— 新增协作者
        # = coding_wiring.py 注册工厂 + manifest 加一行，本文件 diff 为 0。
        # 回归网：tests/test_coding.py 全量（test_setup_tools_registers_all 等）。
        # 剩余硬编码仅：sandbox 策略（环境驱动）+ 工具注册（specs 表机制）
        # + plan_mode / sensitive_path_guard 接线（运行时一次性钩子）。
        import os

        sandbox_mode = os.environ.get("LINGCLAUDE_SANDBOX_MODE")
        sandbox_policy = None
        if sandbox_mode:
            from lingclaude.lacp.sandbox_policy import SandboxMode, SandboxPolicy

            try:
                sandbox_policy = SandboxPolicy(mode=SandboxMode(sandbox_mode.lower()))
            except ValueError:
                sandbox_policy = None
        # Q5: 装配主体 — 13 项协作者实例化收敛至 manifest（行为与原逐字构造一致）
        from lingclaude.engine.coding_wiring import CodingWiringContext, assemble_coding

        assemble_coding(CodingWiringContext(runtime=self, sandbox_policy=sandbox_policy))

        # T4（opencode 架构演进项）：统一注册入口 — specs 表 × runtime handlers。
        # 原 32 条内联 ToolDefinition 已机械迁至 engine/tool_registration.py
        # （scripts/extract_tool_specs.py AST 提取，黄金快照对照零失真），
        # G8 守卫锁定本文件不再出现 ToolDefinition( 内联注册。
        register_all_tools(self.registry, self)
        # 装表扫尾（2026-09-23）：LSP A/B 闸门——env 三态（off/control/treatment），
        # 默认 off 零变化；control 下移 lsp、treatment 下包 handler 记账（fail-open）。
        from lingclaude.engine.lsp_ab import apply_lsp_ab_gate

        self._lsp_ab_recorder = apply_lsp_ab_gate(
            self.registry, self.session_id  # 会话身份单源：跟随宿主 engine（2026-10-07）
        )
        self._plan_mode_active: bool = False
        # T0-1: plan_mode 接入 — 用于过滤写工具
        from lingclaude.engine.plan_mode import PlanMode
        self.plan_mode = PlanMode()
        # T0-2: 敏感路径门接入 pipeline — 覆盖全部带路径参数的工具（write/edit/ast_replace 等）
        self.tool_pipeline.add_guard(self._sensitive_path_guard)
        # NanoJev 契约消费层（消费点⑩ 工具调用风险门控，2026-09-22）：
        # PreToolUse 三原语风险分级（只读/修改/破坏性/网络/生产敏感），叠加在
        # 敏感路径门之后——只读放行零打扰、破坏性/生产敏感按权限模式询问/拒绝。
        # fail-open：门控故障 → abstain（交既有 sensitive_path_guard/权限链处理）。
        self.tool_pipeline.add_guard(self._risk_guard)

    def _stt_handler(
        self,
        duration: int = 5,
        file: str | None = None,
        backend: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not self.stt.is_available():
            return {"error": "无可用的 STT 后端（需安装 openai-whisper 或 sherpa-onnx）"}
        if file:
            stt_result = self.stt.transcribe(file, backend=backend)
        else:
            stt_result = self.stt.record_and_transcribe(duration=duration, backend=backend)
        if not stt_result.available:
            return {"error": stt_result.error}
        return {
            "text": stt_result.text,
            "backend": stt_result.backend,
            "duration": stt_result.duration,
            "language": stt_result.language,
        }

    def _index_project_handler(
        self,
        path: str = ".",
        max_files: int = 200,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = index_project(path, max_files=max_files)
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def _ast_replace_handler(
        self,
        file_path: str,
        function_name: str,
        new_body: str,
        class_name: str | None = None,
        occurrence: int = 1,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = replace_function_body(
            file_path, function_name, new_body,
            class_name=class_name, occurrence=occurrence,
        )
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def _list_functions_handler(
        self,
        file_path: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = list_functions(file_path)
        if result.is_error:
            return {"error": result.error}
        return {"functions": result.data}

    # P0-3: request_user_input tool — registered below after _setup_tools
    def _request_user_input_handler(
        self,
        header: str | None = None,
        question: str | None = None,
        mode: str = "single",
        options: list[dict[str, str]] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """P0-3: Request user input during agent loop.

        对标 AtomCode request_user_input / DSH user-questions。
        当前实现：打印提示到 stdout 并等待 STDIN 读入。
        后续可升级为 FastAPI / WebSocket 推送通知到前端。
        """
        prompt_parts = []
        if header:
            prompt_parts.append(f"[{header}]")
        if question:
            prompt_parts.append(question)
        prompt = " ".join(prompt_parts) or "请输入："

        if mode == "single" and options:
            # single 模式：渲染选项列表
            prompt += "\n"
            for i, opt in enumerate(options, 1):
                label = opt.get("label", opt.get("description", f"选项{i}"))
                prompt += f"  {i}. {label}\n"
            prompt += "请输入选项编号或直接回答："
        elif mode == "multiple" and options:
            prompt += "\n"
            for i, opt in enumerate(options, 1):
                label = opt.get("label", opt.get("description", f"选项{i}"))
                prompt += f"  {i}. {label}\n"
            prompt += "请输入选项编号（多个用逗号分隔）："

        # ── A. TUI 死锁熔断（2026-09-28）─────────────────────────────────────
        # full_tui 运行期间用 prompt_toolkit 接管终端输入（raw mode + 事件循环），
        # 并把 sys.stdout 替换为 _StdoutProxy（无 fd、isatty()=False）。此时
        # sys.stdin 仍指向真实 tty（isatty=True），若继续走 input() 会 C 级阻塞
        # 且 SIGINT 无效（隔壁进程 1170371 实录：Ctrl+C/D 都杀不掉，只能 kill）。
        # 检测指纹：stdout 已被替换为非 tty 代理 → 交互输入通道被 TUI 独占，
        # request_user_input 在此模式下必然死锁，熔断为降级提示。
        try:
            _stdout = sys.stdout
            _stdout_tty = bool(_stdout and _stdout.isatty())
        except (ValueError, OSError):
            _stdout_tty = False
        if not _stdout_tty:
            _record_tui_deadlock_incident()
            return {
                "ok": False,
                "pending": False,
                "degraded": True,
                "answer": None,
                "prompt": prompt,
                "error": (
                    "request_user_input 在当前前端（TUI/full-screen）下不可用："
                    "交互输入通道被终端 UI 独占，input() 会死锁。"
                    "请直接在对话中用文字回复（含选项编号或自由文本），"
                    "不要用 request_user_input 工具。"
                ),
            }

        print(prompt, flush=True)
        stdin = sys.stdin
        try:
            is_tty = bool(stdin and stdin.isatty())
        except (ValueError, OSError):
            is_tty = False

        if not is_tty:
            # P0-3b: 非交互上下文（LACP remote / CI / 管道）下 stdin 阻塞 =
            # 整条 agent loop 卡死。立即返回 pending，把 prompt 原样带回，
            # 由上层决定转交前端或放弃，绝不 input() 挂起。
            return {
                "ok": False,
                "pending": True,
                "answer": None,
                "prompt": prompt,
                "error": "stdin not a tty; interactive input unavailable",
            }

        # ── B. TTY 路径超时兜底（2026-09-28）────────────────────────────────
        # 裸 input() 是 C 级阻塞，无超时、SIGINT 也可能失效（终端状态异常时）。
        # 改用 select 轮询：超时（默认 120s，LINGCLAUDE_USER_INPUT_TIMEOUT 可调）
        # 后降级为默认答案（single/multiple 取选项 1），绝不无限挂起整条 loop。
        timeout_s = _user_input_timeout()
        try:
            ensure_readline()
            answer = _read_line_with_timeout(stdin, timeout_s)
            if answer is None:
                # 超时：降级为默认答案，不挂起、不 raise
                default_answer: str | list[str] | None = None
                if options:
                    default_answer = options[0].get("label")
                _record_tui_deadlock_incident(timeout=True)
                return {
                    "ok": True,
                    "answer": default_answer,
                    "mode": mode,
                    "degraded": True,
                    "timed_out": True,
                    "note": (
                        f"input timed out after {timeout_s}s; "
                        f"defaulted to first option. 用户可直接文字回复覆盖。"
                    ),
                }
            answer = answer.strip()
            add_history_line(answer)
        except (EOFError, KeyboardInterrupt):
            return {
                "ok": False,
                "pending": False,
                "answer": None,
                "error": "input cancelled",
            }

        # 解析选项编号
        selected: str | list[str] = answer
        if options and answer:
            # 尝试解析为编号
            try:
                if "," in answer:
                    indices = [int(x.strip()) for x in answer.split(",")]
                    selected = [options[i - 1]["label"] for i in indices if 0 < i <= len(options)]
                else:
                    idx = int(answer)
                    if 0 < idx <= len(options):
                        selected = options[idx - 1]["label"]
            except ValueError:
                pass  # 非数字，原样返回
        return {"ok": True, "answer": selected, "mode": mode}

    def _tool_scope(self, name: str) -> str:
        """工具的 security_scope（plan_mode 判定用）。"""
        tool = self.registry.get(name)
        return tool.data.security_scope if tool.is_ok else "read"

    @property
    def permission_store(self) -> PermissionStore:
        """T0-3: 当前会话的审批 store — webUI /permission 决策的落点与查询入口。"""
        from lingclaude.core.permissions import PermissionStore, get_permission_store

        store: PermissionStore = get_permission_store(self.session_id)
        return store

    def _gate_sensitive(self, name: str, *candidates: str | None) -> str | None:
        """T0-2: 敏感路径门 — 命中敏感标记且无审批放行时返回错误信息（fail-closed）。

        T0-3 逃生门：会话内对该工具做过 allow/always_allow 决策则放行。
        """
        from lingclaude.engine.sensitive_path_gate import check_sensitive_path
        from lingclaude.core.permissions import get_permission_store

        for cand in candidates:
            if not cand:
                continue
            is_sensitive, reason = check_sensitive_path(str(cand))
            if is_sensitive:
                store = get_permission_store(self.session_id)
                if store.explicitly_allowed(name):
                    return None
                return (
                    f"Path blocked by sensitive_path_gate: {cand} ({reason}；"
                    f"如需访问请通过审批放行 {name})"
                )
        return None

    def _sensitive_path_guard(self, tool_def: Any, ctx: Any) -> Any:
        """T0-2: pipeline 守卫 — 全部工具的 path/file_path 参数检查。

        read/glob/grep 的 pattern 级检查在各自 handler 内（含本守卫同一逃生门）。
        """
        from lingclaude.engine.tool_pipeline import GuardDecision

        candidates = [ctx.args.get("path"), ctx.args.get("file_path")]
        err = self._gate_sensitive(tool_def.name, *(str(c) for c in candidates if c))
        if err:
            return GuardDecision(decision="deny", reason=err)
        return GuardDecision(decision="abstain")

    def _risk_guard(self, tool_def: Any, ctx: Any) -> Any:
        """NanoJev 契约消费层（消费点⑩ 工具调用风险门控，2026-09-22）：
        PreToolUse 三原语风险分级（只读/修改/破坏性/网络/生产敏感）。

        叠加在敏感路径门之后——对 bash 类命令做 0 LLM token 风险分级：
        只读（等级 1）→ 放行零打扰；破坏性/生产敏感（等级 3/4/5）→
        按权限模式 ask/deny。只问/拦写执行域，读域工具 abstain（交读域
        既有权限链）。fail-open：分级故障 → abstain（不反噬工具执行）。
        """
        from lingclaude.engine.tool_pipeline import GuardDecision

        try:
            # 读域工具无命令语义，风险分级不适用（交读域权限链/敏感路径门）
            if not self._tool_blocked_readonly_exempt(tool_def.name):
                return GuardDecision(decision="abstain")
            command = ctx.args.get("command")
            if not isinstance(command, str) or not command:
                return GuardDecision(decision="abstain")
            from lingclaude.engine.risk_gate import classify_command_risk
            from lingclaude.core.permissions import get_permission_mode
            mode = get_permission_mode()
            verdict = classify_command_risk(
                command, permission_mode=mode, tool_name=tool_def.name)
        except Exception:  # noqa: BLE001 — 分级故障 fail-open，abstain 交既有权限链
            # fail-closed 契约下守卫异常=deny 全部调用；本门控是「提醒/询问」
            # 非拦截类（拦截交给 sensitive_path_guard/permissions），故障必须
            # abstain 放行，不能因风险分级内核故障拦住所有 bash 调用。
            return GuardDecision(decision="abstain")
        # 处置映射：allow→放行；ask→ask 模式下询问（strict 由门控已判 deny）；
        # deny→拒绝。只读/放行一律 abstain（不打扰）；破坏性/生产敏感触发处置。
        if verdict.action == "allow":
            return GuardDecision(decision="abstain")
        if verdict.action == "deny":
            return GuardDecision(
                decision="deny",
                reason=(
                    f"[风险门控] 命令风险等级 {verdict.risk_level}（{verdict.risk_label}）"
                    f"：{verdict.reason}。strict 模式拒绝高风险操作，请人工确认。"),
            )
        # ask：高风险（等级≥3）在 auto/ask 模式触发询问信号
        if verdict.risk_level >= 3:
            return GuardDecision(
                decision="abstain",  # 询问走既有权限 ask 通道，门控只挂提醒
                reason=(f"[风险门控提醒] 命令风险等级 {verdict.risk_level}"
                        f"（{verdict.risk_label}）：{verdict.reason}"),
            )
        return GuardDecision(decision="abstain")

    def _tool_blocked_readonly_exempt(self, tool_name: str) -> bool:
        """风险门控是否适用于该工具（bash/执行域才做命令分级）。

        仅对 bash 类命令语义工具做风险分级；其他工具 abstain（读域交
        敏感路径门/权限链，写域交写后验证门）。返回 True=可分级。
        """
        return tool_name in ("bash", "bash_lingxi", "execute")

    def _tool_blocked(self, tool_name: str, store: Any, active_mode: str, kwargs: dict[str, Any] | None = None) -> bool:
        """权限判定（P0 主链统一 2026-09-12 提取，供 execute_tool 与 ToolExecutor 快路径共用）。

        注意（harness fix 2026-09-02）：原 `store.blocks(tool_name)` 在
        单例 store 与 runtime ctx 分离时会被模式污染或审批回灌吞掉。
        改用「runtime 静态配置 ∪ store 运行时 ctx」并集作为唯一真源；
        显式放行 (always_allow) 优先于 deny 翻转（业务约定）。

        H2 (2026-09-15): ask 模式灰区拦截 — 写工具（非只读、非显式放行）在
        ask 下被拦（blocked=True），由 execute_tool 层走 gray_zone_escalate
        落盘+bus 通知（此处只做判定，不做副作用）。
        """
        from lingclaude.core.permissions import READ_ONLY_TOOLS

        effective_deny = self.permissions.deny_names | store.context.deny_names
        allowed = store.explicitly_allowed(tool_name)
        blocked = False
        rule_id = "no_match"
        if tool_name.lower() in effective_deny and not allowed:
            blocked = True
            rule_id = "config.deny_tools.exact" if tool_name.lower() in self.permissions.deny_names else "session.deny_tools.exact"
        elif active_mode == "auto":
            blocked = False
        elif active_mode == "strict" and tool_name not in READ_ONLY_TOOLS:
            blocked = not allowed
            if blocked:
                rule_id = "strict_mode.non_readonly"
        elif active_mode == "ask" and self._tool_scope(tool_name) != "read":
            # 2026-09-15 修复: bash 整体是 execute 域，ask 下一律进灰区导致
            # ps/ls/git status 等只读命令全被拦。按命令内容白名单放行
            # （fail-closed：不在名单仍拦）；敏感路径由 sensitive_path_gate 单独拦。
            if tool_name in ("bash", "bash_lingxi"):
                cmd = str(kwargs.get("command", ""))
                if is_readonly_bash_command(cmd):
                    blocked = False
                else:
                    blocked = not allowed
                    if blocked:
                        rule_id = "ask_mode.pending_approval"
            else:
                blocked = not allowed
                if blocked:
                    rule_id = "ask_mode.pending_approval"
        # R2：把 denial 喂给 DataFlywheel（不造新文件），让"同类 denial"
        # 统计可查（SYSTEMS_THEORY §一.4 摩擦台账 + R1 熔断前置）。
        # 5b：按 rule_id 喂 _ToolLoopDetector 熔断（执行层硬规则,非推理层）。
        if blocked and hasattr(self, "_session_runtime"):
            try:
                self._session_runtime.log_denial(
                    denial_kind=rule_id,
                    command_prefix=str(tool_name),
                    reason=(
                        f"mode={active_mode}; "
                        f"in_deny={tool_name.lower() in effective_deny}; "
                        f"explicitly_allowed={allowed}"
                    ),
                )
                if hasattr(self, "_loop_detector"):
                    # 5b：单次触发不烧轮次,与"denial key 2 次→暂停+升级用户"对齐
                    verdict = self._loop_detector.observe_denial(rule_id, tool_name, threshold=2)
                    if verdict == "denial_abort":
                        # P0.2: 写实例属性（__init__ 已初始化），execute_tool
                        # 返回前消费；原写 _loop_detector._denial_abort_log 全库无消费者。
                        self._denial_abort_log = (
                            f"denial_abort: rule_id={rule_id} tool={tool_name} "
                            f"consecutive={self._loop_detector._denial_streak.get(rule_id, 0)}"
                        )
            except Exception:  # noqa: BLE001 — 飞轮/熔断写入失败不阻塞拦截语义
                pass
        return blocked

    def _blocks(self, tool_name: str, kwargs: dict[str, Any] | None = None) -> bool:
        """权限判定入口（ToolExecutor 快路径预检复用；模式/store 实时读取）。"""
        from lingclaude.core.permissions import get_permission_mode, get_permission_store

        store = get_permission_store(self.session_id)
        return self._tool_blocked(tool_name, store, get_permission_mode(), kwargs)

    def _tool_scope_is_readonly(self, tool_name: str) -> bool:
        """工具是否只读域（security_scope == 'read'）— 灰区 escalate 只针对写/执行域。"""
        return self._tool_scope(tool_name) == "read"

    def _is_permission_block(self, result: dict[str, Any]) -> bool:
        """result 是否属于权限拦截（非真实执行错误）。"""
        err = result.get("error", "")
        if not isinstance(err, str):
            return False
        return "blocked by permissions" in err or "Permission" in err

    def _escalate_gray_zone(
        self, tool_name: str, kwargs: dict[str, Any], store: Any
    ) -> str:
        """灰区 escalate：落盘 pending + LingBus 通知。返回 reason 字符串。"""
        return gray_zone_escalate(
            tool_name,
            kwargs,
            session_id=getattr(self.config, "session_id", None),
        )

    def _auto_rollback_write(self, tool_name: str, args: dict[str, Any]) -> str | None:
        """P1-2 (2026-09-12): post-write 验证失败自动回滚。

        基于 file_edit 的 .bak undo 能力，覆盖 write/edit/file_create/
        file_insert/file_delete_lines（均经 file_ops/file_edit 产生 .bak）。
        返回 None=回滚成功；str=回滚失败原因。
        """
        path = args.get("path") or args.get("file_path")
        if not path:
            return f"无法回滚: 无 path 参数 (tool={tool_name})"
        try:
            result = self.file_edit.undo(str(path))
            if result.is_error:
                return f"undo 失败: {result.error}"
            return None
        except Exception as e:  # noqa: BLE001 — 回滚异常返回原因，不掩盖主错误
            return f"undo 异常: {e}"

    def execute_tool(self, name: str, **kwargs: Any) -> dict[str, Any]:
        # LINGKERNEL_v1 #2: 5 段 pipeline (pre-execute -> guards -> execute -> post -> finalize)
        def _pre_write_verify(tool_name: str, file_path: Any, content: Any) -> tuple[bool, str]:
            if not self.verification_gate.enabled:
                return True, ""
            pre = self.verification_gate.verify(tool_name, file_path=file_path, content=content)
            if pre.passed:
                return True, ""
            failed = [c for c in pre.checks if not c.get("passed", True)]
            return False, f"[验证关卡] 写入被阻止: {pre.error} | failed_checks={failed}"

        def _post_write_verify(file_path: Any) -> tuple[bool, str]:
            if not self.verification_gate.enabled:
                return True, ""
            if not file_path:
                return True, ""
            post = self.verification_gate.verify_post_write(file_path)
            if post.passed:
                return True, ""
            failed = [c for c in post.checks if not c.get("passed", True)]
            return False, f"[验证关卡] 写入后验证失败: {post.error} | failed_checks={failed}"

        rate = self.verification_gate.check_rate_limit()
        rate_ok = rate.passed
        rate_err = ("[安全限制] " + (getattr(rate, "error", "") or "rate limited")) if not rate_ok else ""

        # T0-1: plan_mode 拦截 — 只放行读域工具 + plan_mode 自身（bash 等 execute 域同封）
        if self.plan_mode.is_active and not self.plan_mode.allows(name, self._tool_scope(name)):
            return {"error": f"Tool '{name}' blocked by plan_mode (read-only mode active; call plan_mode with action=exit to leave)"}

        # T0-3: 审批回路 — webUI /permission 决策实时回灌
        # deny 决策 → 拦截；always_allow → 覆盖静态 deny_names
        from lingclaude.core.permissions import (
            READ_ONLY_TOOLS,
            get_permission_mode,
            get_permission_store,
        )

        store = get_permission_store(self.session_id)
        # T1-2 深化: 全局 mode 优先（webUI /permission/mode 可实时切换，覆盖静态 config mode）
        active_mode = get_permission_mode()

        def _blocks(tool_name: str) -> bool:
            return self._tool_blocked(tool_name, store, active_mode, kwargs)

        # P2: VerifyCadenceHook 观察点 1（执行前）— 死循环检测 + pending 验证 nudge
        verify_nudge = self._verify_cadence.observe_call(name, kwargs)

        result = self.tool_pipeline.execute(
            name,
            kwargs,
            permissions_blocks=_blocks,
            rate_check=lambda: (rate_ok, rate_err),
            pre_write_verify=_pre_write_verify,
            post_write_verify=_post_write_verify,
            # P1-2 (2026-09-12): post-write 验证失败自动回滚 — 基于 file_edit 的 .bak
            # undo 能力（write/edit/file_create/file_insert/file_delete_lines 均走此回滚）。
            rollback_callback=lambda n, a: self._auto_rollback_write(n, a),
        )
        # H2 (2026-09-15): 灰区 escalate — ask 模式写工具被拦时，不是简单拒绝，
        # 而是落盘 guard_pending.jsonl + LingBus 通知 + 结果标记 state=escalated。
        if (
            active_mode == "ask"
            and not self._tool_scope_is_readonly(name)
            and result.get("error") is not None
            and self._is_permission_block(result)
        ):
            escalated = self._escalate_gray_zone(name, kwargs, store)
            if escalated:
                result["state"] = "escalated"
                result["escalation"] = escalated
        # P0.2: 5b 熔断触发后把信号挂到本次工具结果上（模型可见），消费即清零,
        # 防止旧信号泄漏到后续无关调用。
        if getattr(self, "_denial_abort_log", None):
            result.setdefault("denial_circuit_breaker", self._denial_abort_log)
            self._denial_abort_log = None
        # P2: VerifyCadenceHook 观察点 2（执行后）— 编辑记账/验证清零；
        # nudge 挂到结果上（模型可见），setdefault 不覆盖已有信号。
        error = result.get("error")
        nudge = self._verify_cadence.observe_result(name, kwargs, error) or verify_nudge
        if nudge:
            result.setdefault("verify_nudge", nudge)
        return result

    def analyze(self, target: str = ".") -> dict[str, Any]:
        self.evaluator = StructureEvaluator(target)
        metrics = self.evaluator.get_current_metrics()

        findings = self._pattern_recognizer.recognize_from_file(target) if Path(target).is_file() else ()
        if not findings and Path(target).is_dir():
            all_findings: list[dict[str, Any]] = []
            for py_file in Path(target).rglob("*.py"):
                file_findings = self._pattern_recognizer.recognize_from_file(str(py_file))
                all_findings.extend(file_findings)
            findings = tuple(all_findings)

        metrics["pattern_findings"] = len(findings)
        metrics["findings"] = [
            {
                "file": f.get("file", ""),
                "line": f.get("line", 0),
                "name": f.get("name", ""),
                "severity": f.get("severity", ""),
                "message": f.get("message", ""),
            }
            for f in findings
        ]
        metrics["detectors"] = self._pattern_recognizer.get_statistics()
        return metrics

    def optimize(
        self, target: str = ".", goal: str = "structure", max_trials: int = 20
    ) -> dict[str, Any]:
        from lingclaude.self_optimizer.optimizer import OptimizationRequest

        request = OptimizationRequest(
            target=target,
            goal=goal,
            params={},
            config={"max_experiments": max_trials},
        )
        result = self.optimizer.optimize(request)

        if result.success:
            metrics = self.analyze(target)
            report = self.advisor.generate_report(
                goal=goal,
                target=target,
                current_metrics=metrics,
                optimization_result=result,
            )
            return {
                "success": True,
                "best_params": result.best_params,
                "best_score": result.best_score,
                "experiments": result.experiments,
                "duration": result.duration,
                "report": report,
            }
        return {
            "success": False,
            "error": result.error,
        }

    def check_and_optimize(
        self, context: dict[str, Any], target: str = ".", goal: str = "structure"
    ) -> dict[str, Any]:
        trigger = OptimizationTrigger()
        should_trigger, trigger_info = trigger.check_all_conditions(context)

        if not should_trigger:
            return {"triggered": False, "reason": "No trigger conditions met"}

        return {
            "triggered": True,
            "trigger_info": {
                "type": trigger_info.type,
                "reason": trigger_info.reason,
                "priority": trigger_info.priority,
            },
            "optimization": self.optimize(target, goal),
        }

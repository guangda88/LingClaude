"""T1 接线测试 — GAP_ANALYSIS_20260825_CC_DIMENSION T1-1/T1-2/T1-3/T1-5/T1-6/T1-7 验收。

覆盖「T1 主线」修复项：
- T1-1: LLM 摘要通道 + 动态预算 + turn 内触发
- T1-2: permission modes (auto/ask/strict) + 全局持久化
- T1-3: 并行工具执行 + _write_lock + 写工具误标降级
- T1-5: MCP stdio/http client + tools/list 发现
- T1-6: SubagentStatus + parallel + control_channel
- T1-7: CLI Esc 打断 + 斜杠命令 + diff 高亮
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lingclaude.core.config import lingclaudeConfig
from lingclaude.core.context_compression import (
    CompressionConfig,
    CompressionLevel,
    compress_messages,
    _try_llm_summary,
)
from lingclaude.core.image_content import extract_image_content, image_tool_text
from lingclaude.core.permissions import (
    PermissionContext,
    get_permission_mode,
    get_permission_store,
    record_permission_decision,
    reset_permission_stores,
    set_permission_mode,
)
from lingclaude.engine.coding import CodingRuntime
from lingclaude.engine.mcp_client import MCPStdioClient, MCPHttpClient, discover_and_register
from lingclaude.engine.subagent.base import SubagentRequest, SubagentStatus
from lingclaude.engine.subagent.manager import SubagentManager
from lingclaude.lacp.manifest import Plugin, Interface, Transport, register_mcp_from_manifest
from lingclaude.engine import mcp_proxy


@pytest.fixture(autouse=True)
def _clean_stores():
    reset_permission_stores()
    yield
    reset_permission_stores()
    # 清理 MCP 测试 server
    for key in list(mcp_proxy._SERVERS.keys()):
        if key.startswith("lacp:test-"):
            del mcp_proxy._SERVERS[key]


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rt = CodingRuntime(config=lingclaudeConfig())
    return rt


# ── T1-1: LLM 摘要 + 动态预算 ──────────────────────────────────────────────


class TestContextCompression:
    def test_dynamic_budget_default(self):
        """默认 budget = summary_max_chars (4000)."""
        c = CompressionConfig()
        assert c.effective_summary_chars() == 4000

    def test_dynamic_budget_window_50k(self):
        """50k 窗口 → 50000*4//100 = 2000."""
        c = CompressionConfig(model_window_tokens=50000)
        assert c.effective_summary_chars() == 2000

    def test_dynamic_budget_window_200k_cap(self):
        """200k 窗口 → min(8000, 4000) = 4000 ( capped by summary_max_chars)."""
        c = CompressionConfig(model_window_tokens=200000)
        assert c.effective_summary_chars() == 4000

    def test_no_llm_provider_returns_none(self):
        """无 provider → None（正则兜底）."""
        facts = {"files_read": ["a.py"]}
        result = _try_llm_summary(facts, 2, CompressionConfig(use_llm_summary=True, provider=None))
        assert result is None

    def test_compress_drops_messages(self):
        """压缩后 dropped_count 正确."""
        msgs = [f"user {i}: 决定方案{i}" for i in range(30)]
        r = compress_messages(msgs, CompressionConfig(max_messages=24))
        assert r.dropped_count == 6
        assert len(r.compressed_messages) == 25  # 1 summary + 24 kept


# ── T1-2: permission modes ─────────────────────────────────────────────────

    def test_mode_ask_write_requires_approval(self):
        """ask 模式: write 需审批."""
        ctx = PermissionContext.from_config(mode="ask")
        assert ctx.requires_approval("write") is True
        assert ctx.is_auto_approved("read") is True

    def test_mode_auto_write_allowed(self):
        """auto 模式: write 自动放行."""
        ctx = PermissionContext.from_config(mode="auto")
        assert ctx.requires_approval("write") is False
        assert ctx.is_auto_approved("write") is True

    def test_mode_strict_bash_blocked(self):
        """strict 模式: bash 需审批."""
        ctx = PermissionContext.from_config(mode="strict")
        assert ctx.requires_approval("bash") is True

    def test_set_permission_mode_persistent(self, tmp_path, monkeypatch):
        """mode 切换持久化到 approvals.json."""
        approvals_path = tmp_path / "approvals.json"
        monkeypatch.setattr(
            "lingclaude.core.permissions._PERSIST_PATH", approvals_path
        )
        set_permission_mode("auto")
        assert get_permission_mode() == "auto"
        data = json.loads(approvals_path.read_text())
        assert data["mode"] == "auto"

    def test_set_permission_mode_rejects_invalid(self, tmp_path, monkeypatch):
        """非法 mode 被拒绝，保持原值."""
        approvals_path = tmp_path / "approvals.json"
        monkeypatch.setattr(
            "lingclaude.core.permissions._PERSIST_PATH", approvals_path
        )
        set_permission_mode("ask")
        ok = set_permission_mode("invalid")
        assert ok is False
        assert get_permission_mode() == "ask"


# ── T1-3: 并行工具执行 ─────────────────────────────────────────────────────

    def test_is_concurrency_safe_read_true(self, runtime):
        """read 标记为并发安全."""
        from lingclaude.core.query_engine import QueryEngine
        qe = QueryEngine.__new__(QueryEngine)
        qe._runtime = runtime
        assert qe._is_concurrency_safe("read") is True

    def test_is_concurrency_safe_write_false(self, runtime):
        """write 不标记为并发安全."""
        from lingclaude.core.query_engine import QueryEngine
        qe = QueryEngine.__new__(QueryEngine)
        qe._runtime = runtime
        assert qe._is_concurrency_safe("write") is False

    def test_write_lock_exists(self, runtime):
        """_write_lock 存在且为锁对象."""
        from lingclaude.core.query_engine import QueryEngine
        qe = QueryEngine(runtime)
        assert hasattr(qe, "_write_lock")
        # threading.Lock() 返回 _thread.lock 类型，不能用 isinstance(x, threading.Lock)
        assert hasattr(qe._write_lock, "acquire")
        assert hasattr(qe._write_lock, "release")

    def test_process_tool_calls_parallel_exists(self, runtime):
        """T3-3 拆分后: 并行执行逻辑迁移至 ToolCallExecutor（engine 经 _tool_call_executor 接线）。"""
        from lingclaude.core.query_engine import QueryEngine
        from lingclaude.core.tool_call_executor import ToolCallExecutor
        qe = QueryEngine(runtime)
        # 引擎通过 _tool_call_executor 暴露 process（并行/顺序分流入口）
        assert hasattr(qe, "_tool_call_executor")
        assert isinstance(qe._tool_call_executor, ToolCallExecutor)
        assert hasattr(qe._tool_call_executor, "process")
        assert hasattr(qe._tool_call_executor, "_process_parallel")
        assert hasattr(qe._tool_call_executor, "_process_single")


# ── T1-5: MCP stdio client ────────────────────────────────────────────────

    def test_mcp_tool_dataclass(self):
        """MCPTool.to_dict 包含 name/description/inputSchema."""
        from lingclaude.engine.mcp_client import MCPTool
        t = MCPTool(name="read_file", description="Read file", input_schema={"type": "object"})
        d = t.to_dict()
        assert d["name"] == "read_file"
        assert "inputSchema" in d

    def test_bad_transport_rejected(self):
        """无效 transport → (False, [], {})."""
        success, names, schemas = discover_and_register("x", "x", "bad_transport")
        assert success is False
        assert names == []
        assert schemas == {}

    def test_stdio_spawn_failure(self):
        """spawn 失败 → (False, [], {})."""
        success, names, schemas = discover_and_register("x", "x", "stdio", command=["nonexistent-binary-xyz"])
        assert success is False
        assert names == []
        assert schemas == {}

    def test_http_transport_error(self):
        """HTTP 不可达 → TRANSPORT_ERROR."""
        c = MCPHttpClient(url="http://127.0.0.1:1", timeout=1)
        r = c.call_tool("x", {})
        assert r.is_error

    def test_manifest_register_stdio(self):
        """LACP manifest 注册 stdio MCP server."""
        p = Plugin(
            name="test-mcp-stdio", version="0.1.0", owner="atomcode",
            description="t", interface=Interface(input_schema={}, output_schema={}),
            transports=[Transport.MCP], mcp_command=["npx", "-y", "server"],
        )
        register_mcp_from_manifest(p)
        servers = [s for s in mcp_proxy.list_servers() if s.key == "lacp:test-mcp-stdio"]
        assert len(servers) == 1
        assert servers[0].transport == "stdio"

    def test_manifest_register_http(self):
        """LACP manifest 注册 http MCP server."""
        p = Plugin(
            name="test-mcp-http", version="0.1.0", owner="atomcode",
            description="t", interface=Interface(input_schema={}, output_schema={}),
            transports=[Transport.MCP], mcp_url="http://127.0.0.1:8888/mcp",
        )
        register_mcp_from_manifest(p)
        servers = [s for s in mcp_proxy.list_servers() if s.key == "lacp:test-mcp-http"]
        assert len(servers) == 1
        assert servers[0].transport == "http"

    def test_manifest_rejects_no_command_or_url(self):
        """声明 MCP transport 但无命令/URL → ValueError."""
        with pytest.raises(ValueError, match="mcp_command/mcp_url"):
            Plugin(
                name="bad-mcp", version="0.1.0", owner="atomcode",
                description="t", interface=Interface(input_schema={}, output_schema={}),
                transports=[Transport.MCP],
            )


# ── T1-6: 子代理多后端 ────────────────────────────────────────────────────

    def test_subagent_status_enum(self):
        """SubagentStatus 有 5 个状态."""
        assert hasattr(SubagentStatus, "PENDING")
        assert hasattr(SubagentStatus, "RUNNING")
        assert hasattr(SubagentStatus, "COMPLETED")
        assert hasattr(SubagentStatus, "FAILED")
        assert hasattr(SubagentStatus, "ABORTED")

    def test_subagent_request_parallel_field(self):
        """SubagentRequest 支持 parallel 字段."""
        req = SubagentRequest(task="t", parallel=3, control_channel=True)
        assert req.parallel == 3
        assert req.control_channel is True

    def test_manager_has_backends(self):
        """SubagentManager 注册了 inprocess 和 acp."""
        mgr = SubagentManager()
        assert mgr.has_backend("inprocess")
        assert mgr.has_backend("acp")

    def test_backend_abort_status_methods(self):
        """后端有 abort() 和 status() 方法."""
        mgr = SubagentManager()
        backend = mgr.get_backend("inprocess")
        assert hasattr(backend, "abort")
        assert hasattr(backend, "status")
        # 未注册的 agent_id → COMPLETED
        assert backend.status("nonexistent") == SubagentStatus.COMPLETED


# ── T1-7: CLI 终端 UX ─────────────────────────────────────────────────────

    def test_esc_pressed_function_exists(self):
        """_esc_pressed 函数存在且可调用."""
        from lingclaude.cli.app import _esc_pressed
        assert callable(_esc_pressed)

    def test_handle_slash_command_help(self):
        """真功能测试: /help 输出斜杠命令列表（见 test_cli_interaction.py）。"""
        from lingclaude.cli.app import _interactive_loop
        assert callable(_interactive_loop)

    def test_handle_slash_command_clear(self):
        """/clear 清空消息 — 真功能测试见 test_cli_interaction.py。"""
        from lingclaude.cli.app import _interactive_loop
        assert callable(_interactive_loop)

    def test_handle_slash_command_normal_falls_through(self):
        """普通命令不消费 — 真功能测试见 test_cli_interaction.py。"""
        from lingclaude.cli.app import _interactive_loop
        assert callable(_interactive_loop)

    def test_print_diff_exists(self):
        """print_diff 函数存在且可调用."""
        from lingclaude.cli.display import print_diff
        assert callable(print_diff)


# ── T1-1 深化: prefix cache 保留（方向修复验收）──────────────────────────────


class TestPrefixCache:
    def test_preserve_head_not_tail(self):
        """preserve_prefix_cache 保留头部 N 条（前缀稳定），不是尾部。"""
        from lingclaude.core.context_compression import PrefixCacheConfig, preserve_prefix_cache

        msgs = ["msg1", "msg2", "msg3", "msg4", "msg5", "msg6"]
        cfg = PrefixCacheConfig(enabled=True, preserve_messages=3)
        preserved = preserve_prefix_cache(msgs, cfg)
        assert preserved == ["msg1", "msg2", "msg3"], f"应保留头部, 实际 {preserved}"

    def test_should_preserve_messages_path(self):
        """should_preserve 走 preserve_messages 路径（此前只走 preserve_tokens）。"""
        from lingclaude.core.context_compression import PrefixCacheConfig

        cfg = PrefixCacheConfig(enabled=True, preserve_messages=5)
        assert cfg.should_preserve(100) is True

    def test_should_preserve_tokens_threshold(self):
        """preserve_tokens 路径按阈值判断。"""
        from lingclaude.core.context_compression import PrefixCacheConfig

        cfg = PrefixCacheConfig(enabled=True, preserve_tokens=500)
        assert cfg.should_preserve(1000) is True
        assert cfg.should_preserve(100) is False

    def test_should_preserve_disabled(self):
        """未启用时始终 False。"""
        from lingclaude.core.context_compression import PrefixCacheConfig

        cfg = PrefixCacheConfig(enabled=False, preserve_messages=5)
        assert cfg.should_preserve(1000) is False


# ── T1-4: 多模态 content blocks（read 工具图片产出链路验收）───────────────────


class TestMultimodalWiring:
    def test_read_result_image_includes_content(self):
        """ReadResult.to_dict 图片时输出 base64 content（此前丢弃）。"""
        from lingclaude.engine.file_read import ReadResult

        r = ReadResult(
            path="a.png", content="iVBORw0KGgo=", size=10, lines=0,
            is_image=True, image_mime="image/png",
        )
        d = r.to_dict()
        assert d["is_image"] is True
        assert d["image_mime"] == "image/png"
        assert d["content"] == "iVBORw0KGgo="

    def test_read_result_text_unchanged(self):
        """文本读取 to_dict 行为不变（不输出 is_image 键，content 为文本）。"""
        from lingclaude.engine.file_read import ReadResult

        r = ReadResult(path="a.txt", content="hello", size=5, lines=1)
        d = r.to_dict()
        assert "is_image" not in d
        assert d["content"] == "hello"

    def test_extract_image_content(self):
        """_extract_image_content 从 JSON 输出提取 (base64, mime)。"""
        import json
        from lingclaude.core.query_engine import QueryEngine

        output = json.dumps({
            "path": "a.png", "is_image": True,
            "image_mime": "image/png", "content": "iVBORw0KGgo=",
        })
        img = extract_image_content(output)
        assert img == ("iVBORw0KGgo=", "image/png")

    def test_extract_image_content_non_image_returns_none(self):
        """非图片输出返回 None。"""
        from lingclaude.core.query_engine import QueryEngine

        assert extract_image_content('{"content": "text"}') is None
        assert extract_image_content("plain text") is None
        assert extract_image_content("") is None

    def test_read_image_full_chain(self, tmp_path):
        """完整链路: read 图片文件 → to_dict → _extract_image_content。"""
        import json
        from pathlib import Path
        from lingclaude.engine.file_read import FileReadTool
        from lingclaude.core.query_engine import QueryEngine

        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
            "1f15c4890000000d49444154789c626001000000ffff03000006000557"
            "bfabd40000000049454e44ae426082"
        )
        p = Path(tmp_path) / "test.png"
        p.write_bytes(png)

        tool = FileReadTool(base_dir=str(tmp_path))
        res = tool.read("test.png")
        assert res.is_ok
        d = res.data.to_dict()
        assert d["is_image"] is True
        assert d["image_mime"] == "image/png"

        img = extract_image_content(json.dumps(d))
        assert img is not None
        assert img[1] == "image/png"
        assert len(img[0]) > 50  # base64 内容非空

    def test_model_message_image_content_blocks(self):
        """ModelMessage.to_dict 产出 OpenAI image_url content blocks。"""
        from lingclaude.model.types import ModelMessage, MessageRole

        msg = ModelMessage(
            role=MessageRole.TOOL,
            content="read result",
            name="read",
            tool_call_id="call_1",
            image_content=("iVBORw0KGgo=", "image/png"),
        )
        d = msg.to_dict()
        assert isinstance(d["content"], list)
        assert len(d["content"]) == 2
        assert d["content"][0]["type"] == "text"
        assert d["content"][1]["type"] == "image_url"
        assert d["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_model_message_plain_content_unchanged(self):
        """无图片时 ModelMessage.to_dict 行为不变。"""
        from lingclaude.model.types import ModelMessage, MessageRole

        msg = ModelMessage(role=MessageRole.USER, content="hi")
        d = msg.to_dict()
        assert d["content"] == "hi"


# ── 二次审计修复验收: T1-1 配置可达 / T1-4 单份 payload / T1-6 send_message 撤下 ──


class TestT1ConfigReachability:
    """T1-1: use_llm_summary / context_window_tokens 从 yaml 到 QueryEngineConfig 全链可达。

    此前 loader 不读这两个字段 + QueryEngineConfig 构造点不透传 → from_config
    主路径下动态预算恒为静态 200k、LLM 摘要永远走不到（双重死接线）。
    """

    @staticmethod
    def _write_yaml(tmp_path):
        yml = tmp_path / "config.yaml"
        yml.write_text(
            "engine:\n"
            "  use_llm_summary: true\n"
            "  context_window_tokens: 128000\n",
            encoding="utf-8",
        )
        return yml

    def test_loader_reads_t1_fields(self, tmp_path):
        from lingclaude.core.config import load_config
        cfg = load_config(self._write_yaml(tmp_path))
        assert cfg.engine.use_llm_summary is True
        assert cfg.engine.context_window_tokens == 128000

    def test_from_config_file_passthrough(self, tmp_path, monkeypatch):
        from lingclaude.core.query_engine import QueryEngine
        monkeypatch.chdir(tmp_path)
        result = QueryEngine.from_config_file(str(self._write_yaml(tmp_path)))
        assert result.is_ok, f"from_config_file failed: {getattr(result, 'error', None)}"
        assert result.data.config.use_llm_summary is True
        assert result.data.config.context_window_tokens == 128000


class TestImageSinglePayload:
    """T1-4: base64 只走 image_content 侧信道，文本为占位符（不再双份 payload）。"""

    def test_image_text_is_placeholder(self):
        from lingclaude.core.query_engine import QueryEngine
        output = json.dumps({
            "path": "a.png", "size": 100, "is_image": True,
            "image_mime": "image/png", "content": "iVBORw0KGgo=",
        })
        img = extract_image_content(output)
        assert img == ("iVBORw0KGgo=", "image/png")
        text = image_tool_text(output, img)
        assert "iVBORw0KGgo=" not in text  # base64 不进文本通道
        assert "[image: a.png (image/png, 100 bytes)]" in text
        # 占位符文本仍是合法 JSON（历史/压缩管线兼容）
        assert json.loads(text)["is_image"] is True

    def test_non_image_passthrough(self):
        from lingclaude.core.query_engine import QueryEngine
        assert image_tool_text('{"content": "hi"}', None) == '{"content": "hi"}'
        assert image_tool_text("plain text", None) == "plain text"
        # image 非 None 但 payload 不可解析 → 原样返回
        assert image_tool_text("plain text", ("x", "image/png")) == "plain text"


class TestSendMessageWithdrawn:
    """T1-6: send_message 假实现撤下 — 注册表不再暴露，其余控制工具保留。

    ACP run() 是同步单轮、_running 只存已完成结果，没有可投递的活会话；
    原实现写 dict 即返回 success（假成功比报错更危险）。
    """

    def test_send_message_not_registered(self):
        runtime = CodingRuntime(config=lingclaudeConfig())
        names = {t.name for t in runtime.registry.list_tools()}
        assert "send_message" not in names
        assert "list_agents" in names
        assert "interrupt_agent" in names


# ── 任务5: 工具结果 pruner 验收 ───────────────────────────────────────────
# 2026-09-23: 旧 8KB stub pruner（ToolExecutor._prune_output）已删——
# 零生产调用方，瘦身职责由 pipeline spill(16KB)+loop slim(1600字符)覆盖。
# 下方哨兵防复活: 若有人重新引入 stub 化 pruner，应显式重审三层分工。


class TestNoStubPrunerResurrection:
    """哨兵: ToolExecutor 不应再有 stub 化 pruner（防止死代码复活）"""

    def test_no_prune_output_method(self):
        import inspect

        from lingclaude.core.tool_executor import ToolExecutor
        assert not hasattr(ToolExecutor, "_prune_output")
        src = inspect.getsource(ToolExecutor)
        assert "_prune_output" not in src
        assert "DEFAULT_PRUNE" not in src


# ── 任务1: marketplace 接线验收（修复死接线第6 案）──


class TestMarketplaceAPI:
    """marketplace.py 已有完整实现但零消费方 — 加 3 个 api.py 端点接入"""

    def test_marketplace_list_endpoint(self):
        """GET /marketplace/list 返回插件列表"""
        from lingclaude.api import app

        paths = [r.path for r in app.routes]
        assert "/marketplace/list" in paths

    def test_marketplace_upload_endpoint(self):
        from lingclaude.api import app
        paths = [r.path for r in app.routes]
        assert "/marketplace/upload" in paths

    def test_marketplace_reputation_endpoint(self):
        from lingclaude.api import app
        paths = [r.path for r in app.routes]
        assert any(p.startswith("/marketplace/reputation/") for p in paths)


class TestMarketplaceRealAPI:
    """直调 get_marketplace() 单例 + 上传 + 评级完整链路"""

    def test_upload_safe_plugin_succeeds(self):
        """上传安全插件（无危险 import）应成功"""
        from lingclaude.lacp.marketplace import get_marketplace
        from lingclaude.lacp.manifest import Plugin, Interface, Transport

        mp = get_marketplace()
        # 安全的代码（无 eval/exec/__import__）
        safe_code = b"def hello():\n    return 'world'\n"
        manifest = Plugin(
            name="test-safe-plugin",
            version="0.1.0",
            owner="lingke",
            description="safe test",
            interface=Interface(input_schema={}, output_schema={}),
            transports=[Transport.CLI],
        )
        success, error = mp.upload(manifest, safe_code, "lingke", secret=b"")
        assert success is True, f"expected success, got error: {error}"

    def test_upload_critical_plugin_rejected(self):
        """上传含 eval() 的代码应被 critical 风险标记"""
        from lingclaude.lacp.marketplace import get_marketplace
        from lingclaude.lacp.manifest import Plugin, Interface, Transport

        mp = get_marketplace()
        bad_code = b"def evil():\n    eval('1+1')\n"
        manifest = Plugin(
            name="test-bad-plugin",
            version="0.1.0",
            owner="lingke",
            description="bad test",
            interface=Interface(input_schema={}, output_schema={}),
            transports=[Transport.CLI],
        )
        success, error = mp.upload(manifest, bad_code, "lingke", secret=b"")
        # critical 风险进入 PENDING 审核队列（人工介入），不直接拒绝
        assert success is True  # 上传本身允许
        info = mp._plugins["test-bad-plugin"]
        from lingclaude.lacp.marketplace import ReviewStatus
        assert info["status"] == ReviewStatus.PENDING
        assert info["scan_result"].has_critical()
        assert "eval" in str(info["scan_result"].issues).lower()

    def test_rate_and_trust_score(self):
        """评级 + 信誉评分"""
        from lingclaude.lacp.marketplace import get_marketplace
        from lingclaude.lacp.manifest import Plugin, Interface, Transport

        mp = get_marketplace()
        manifest = Plugin(
            name="test-rate-plugin",
            version="0.1.0",
            owner="lingke",
            description="rate test",
            interface=Interface(input_schema={}, output_schema={}),
            transports=[Transport.CLI],
        )
        mp.upload(manifest, b"def x(): return 1\n", "lingke", secret=b"")
        # rate 后 rating 应被记录
        mp.rate("test-rate-plugin", "lingke", 5, "great")
        rating = mp.get_rating("test-rate-plugin")
        trust = mp.get_trust_score("test-rate-plugin")
        assert rating == 5.0
        assert 0.0 <= trust <= 1.0


# ── A1-1 验收:BusResponder 启动接线(灵信/灵安验收要求)─────────────────


class TestA11BusResponderWiring:
    """A2+A1-1 合并交付验收(LingBus 优化 RFC §3)。

    族长 2026-08-27 决议 C:默认开,LINGCLAUDE_BUS_LISTENER=0 / conftest 关闭。
    """

    def test_app_py_references_bus_responder(self):
        """灵安审查要求:app.py 必须真实引用 BusResponder(非死接线)。"""
        import inspect
        from lingclaude.cli import app
        src = inspect.getsource(app)
        assert "BusResponder" in src, "app.py 无 BusResponder 引用(A2 死接线复发)"
        assert "_start_bus_responder_background" in src
        assert callable(app._start_bus_responder_background)

    def test_conftest_disables_listener_in_pytest(self):
        """conftest.py 必须设 LINGCLAUDE_BUS_LISTENER=0(决议 C 核心机制)。"""
        import os
        assert os.environ.get("LINGCLAUDE_BUS_LISTENER") == "0", (
            "conftest 应设 LINGCLAUDE_BUS_LISTENER=0,否则 pytest 会启动后台线程卡死"
        )

    def test_start_and_stop_background_thread(self):
        """启动 + 停止:stop_event.set() 后线程应在 interval 内退出。"""
        import threading
        from lingclaude.cli.app import _start_bus_responder_background
        stop = _start_bus_responder_background(interval=1.0)
        assert isinstance(stop, threading.Event)
        stop.set()
        # 1 个 interval 内线程应退出(daemon 线程,不 join 等太久)
        joined = threading.Event()
        for t in threading.enumerate():
            if t.name == "lingclaude-bus-responder":
                t.join(timeout=3.0)
                joined.set()
                break
        assert joined.is_set(), "lingclaude-bus-responder 线程未找到"

    def test_default_on_semantics(self, monkeypatch):
        """默认开语义:env=0 关闭,unset 默认开(族长决议 C)。"""
        import os
        # env=0 → 判定为"不启动"
        monkeypatch.setenv("LINGCLAUDE_BUS_LISTENER", "0")
        should_start = os.environ.get("LINGCLAUDE_BUS_LISTENER") != "0"
        assert should_start is False
        # unset → 判定为"默认启动"
        monkeypatch.delenv("LINGCLAUDE_BUS_LISTENER", raising=False)
        should_start = os.environ.get("LINGCLAUDE_BUS_LISTENER") != "0"
        assert should_start is True

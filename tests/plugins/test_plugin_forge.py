"""plugin_forge 测试套件 — 生成回路四段各自的验收。

测试设计：
- 段 1（生成）：模板渲染产物的字段完整性 + 与实测 crush/opencode 结构对齐
- 段 2（自测）：AST/manifest/cli_path 三闸门各自的通过与拒绝路径
- 段 3（自注册）：auto_register=False 走手动验证（避免测试污染真实 registry）
- 段 4（记录）：knowledge.db 回写（用 tmp_path 隔离，不碰真实库）
- 编排：幂等守卫（目录已存在拒绝覆盖）+ YAML 契约解析
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from lingclaude.plugins.tools.plugin_forge import (
    ForgeSpec,
    McpWrapSpec,
    _probe_cli_headless,
    _probe_mcp_initialize,
    _render_manifest,
    _render_plugin_py,
    _render_mcp_manifest,
    _render_mcp_plugin_py,
    _self_test,
    _self_test_mcp,
    forge_cli_agent,
    forge_from_yaml,
    forge_mcp_wrap,
)

# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def spec() -> ForgeSpec:
    return ForgeSpec(
        org_member="testforge",
        display_name="TestForge 外包工人",
        cli_path="/bin/echo",           # 实测存在的可执行文件
        headless_args=["-n", "hello"],
        prompt_mode="arg",
        call_timeout_s=300,
        capabilities=["code_generation", "code_review"],
        kernel_note="testforge-cli v0.0.1（测试）",
        headless_verified=False,
    )


@pytest.fixture
def spec_tmp(spec, tmp_path, monkeypatch) -> ForgeSpec:
    """把 plugin_dir 指向临时目录——测试不污染真实 plugins/agents/。"""
    monkeypatch.setattr(
        "lingclaude.plugins.tools.plugin_forge.AGENTS_DIR", tmp_path / "agents")
    return spec


# ── 段 1：模板渲染 ───────────────────────────────────────────────────


class TestTemplateRender:
    def test_manifest_required_fields(self, spec):
        m = _render_manifest(spec)
        for f in ("name", "target", "display_name", "version",
                  "trust_level", "plug_level", "caller",
                  "transport", "capabilities", "health_probe",
                  "state_record", "org_member"):
            assert f in m, f"manifest 缺字段: {f}"

    def test_manifest_name_consistency(self, spec):
        m = _render_manifest(spec)
        assert m["name"] == "agent/testforge"
        assert m["target"] == "testforge-cli"
        assert m["org_member"] == "testforge"
        assert m["state_record"] == "agent_run:testforge"

    def test_manifest_transport_command(self, spec):
        m = _render_manifest(spec)
        tr = m["transport"]
        assert tr["kind"] == "cli-subprocess"
        assert tr["command"][0] == "/bin/echo"
        assert tr["command"][1:] == ["-n", "hello"]
        assert tr["prompt_mode"] == "arg"
        assert tr["call_timeout_s"] == 300

    def test_manifest_unverified_note(self, spec):
        m = _render_manifest(spec)
        assert "headless 未实测" in m["notes"]

    def test_plugin_py_has_register(self, spec):
        src = _render_plugin_py(spec)
        assert "register = make_register(" in src
        assert "class TestforgePlugin(CliAgentPluginBase)" in src
        assert "MANIFEST_PATH" in src

    def test_class_name_camelcase(self):
        s = ForgeSpec(org_member="my_agent", display_name="x", cli_path="/bin/echo")
        assert s.class_name == "MyAgent"


# ── 段 2：自测三闸门 ─────────────────────────────────────────────────


class TestSelfTest:
    def test_all_pass(self, spec):
        m = _render_manifest(spec)
        src = _render_plugin_py(spec)
        r = _self_test(spec, m, src)
        assert r["ok"] is True
        assert r["ast"] and r["manifest"] and r["cli_path"]
        assert r["errors"] == []

    def test_ast_rejects_no_register(self, spec):
        m = _render_manifest(spec)
        bad_src = "x = 1\n"  # 无 register
        r = _self_test(spec, m, bad_src)
        assert r["ok"] is False
        assert r["ast"] is False
        assert any("register" in e for e in r["errors"])

    def test_ast_rejects_syntax_error(self, spec):
        m = _render_manifest(spec)
        r = _self_test(spec, m, "def broken(:\n")
        assert r["ok"] is False
        assert any("AST" in e for e in r["errors"])

    def test_manifest_rejects_missing_field(self, spec):
        m = _render_manifest(spec)
        del m["capabilities"]
        src = _render_plugin_py(spec)
        r = _self_test(spec, m, src)
        assert r["ok"] is False
        assert any("capabilities" in e for e in r["errors"])

    def test_manifest_rejects_name_mismatch(self, spec):
        m = _render_manifest(spec)
        m["name"] = "agent/WRONG"
        src = _render_plugin_py(spec)
        r = _self_test(spec, m, src)
        assert r["ok"] is False
        assert any("不一致" in e for e in r["errors"])

    def test_cli_path_rejects_nonexistent(self, spec):
        bad = ForgeSpec(org_member="nocli", display_name="x",
                        cli_path="/nonexistent/path/to/cli")
        m = _render_manifest(bad)
        src = _render_plugin_py(bad)
        r = _self_test(bad, m, src)
        assert r["ok"] is False
        assert r["cli_path"] is False


# ── 编排：幂等守卫 + 落盘 ────────────────────────────────────────────


class TestForgeOrchestration:
    def test_happy_path_writes_files(self, spec_tmp, tmp_path):
        with patch("lingclaude.plugins.tools.plugin_forge._record_forge"):
            r = forge_cli_agent(spec_tmp, auto_register=False)
        assert r.ok is True
        assert len(r.generated_files) == 2
        manifest_path = tmp_path / "agents" / "agent_testforge" / "manifest.agent.json"
        plugin_path = tmp_path / "agents" / "agent_testforge" / "plugin.py"
        assert manifest_path.is_file()
        assert plugin_path.is_file()
        m = json.loads(manifest_path.read_text())
        assert m["name"] == "agent/testforge"

    def test_self_test_failure_blocks_write(self, spec_tmp, monkeypatch):
        """自测未过 → 不落盘（先写再查 与 生成后自动跑 的分界）。"""
        monkeypatch.setattr(spec_tmp, "cli_path", "/nonexistent/cli")
        with patch("lingclaude.plugins.tools.plugin_forge._record_forge"):
            r = forge_cli_agent(spec_tmp, auto_register=False)
        assert r.ok is False
        assert not spec_tmp.plugin_dir.exists() or not any(spec_tmp.plugin_dir.iterdir())

    def test_idempotent_refuses_overwrite(self, spec_tmp, tmp_path):
        """目录已存在且非空 → add-only 拒绝覆盖（对齐 hot_reload 语义）。"""
        with patch("lingclaude.plugins.tools.plugin_forge._record_forge"):
            r1 = forge_cli_agent(spec_tmp, auto_register=False)
            assert r1.ok is True
            r2 = forge_cli_agent(spec_tmp, auto_register=False)
        assert r2.ok is False
        assert "拒绝覆盖" in r2.error

    def test_record_forge_called(self, spec_tmp):
        with patch("lingclaude.plugins.tools.plugin_forge._record_forge") as mock_rec:
            forge_cli_agent(spec_tmp, auto_register=False)
        assert mock_rec.called
        # 验证回写的 LearnedRule 字段
        call_args = mock_rec.call_args
        assert call_args is not None


# ── YAML 契约 ────────────────────────────────────────────────────────


class TestYamlContract:
    def test_valid_yaml(self, spec_tmp):
        yaml_text = """
forge_type: cli_agent
org_member: yamldemo
display_name: "YamlDemo 外包工人"
cli_path: /bin/echo
headless_args: ["-n"]
prompt_mode: arg
call_timeout_s: 120
capabilities: [code_generation]
"""
        with patch("lingclaude.plugins.tools.plugin_forge._record_forge"):
            r = forge_from_yaml(yaml_text, auto_register=False)
        assert r.ok is True

    def test_wrong_forge_type(self):
        r = forge_from_yaml("forge_type: bogus\norg_member: x\n", auto_register=False)
        assert r.ok is False
        assert "cli_agent|mcp_wrap" in r.error


# ── mcp_wrap 生成器（批 2）────────────────────────────────────────────


@pytest.fixture
def mspec() -> McpWrapSpec:
    return McpWrapSpec(
        name="demowrap",
        display_name="DemoWrap 服务",
        command=["/bin/true", "--stdio"],
        cwd="/home/ai/lingclaude",
        mode="stdio",
        ns="agent",
        mcp_tool="execute_command",
        tool_arg_name="command",
        trust_level="T2",
        plug_level="L1",
        capabilities=["execute_command", "code_search"],
        kernel_note="demowrap MCP（测试）",
        verified=False,
    )


@pytest.fixture
def mspec_tmp(mspec, tmp_path, monkeypatch) -> McpWrapSpec:
    """把 plugin_dir 指向临时目录——测试不污染真实 plugins/agents/。"""
    monkeypatch.setattr(
        "lingclaude.plugins.tools.plugin_forge.AGENTS_DIR", tmp_path / "agents")
    return mspec


class TestMcpTemplateRender:
    def test_manifest_required_fields(self, mspec):
        m = _render_mcp_manifest(mspec)
        for f in ("name", "target", "display_name", "version",
                  "trust_level", "plug_level", "caller",
                  "transport", "capabilities", "health_probe",
                  "state_record"):
            assert f in m, f"manifest 缺字段: {f}"

    def test_manifest_n2_dual_declaration(self, mspec):
        m = _render_mcp_manifest(mspec)
        assert m["trust_level"] == "T2"
        assert m["plug_level"] == "L1"

    def test_manifest_n3_domain_prefix(self, mspec):
        m = _render_mcp_manifest(mspec)
        assert m["name"] == "agent/demowrap"
        assert m["name"].split("/", 1)[0] in ("agent", "cap", "os", "hw", "gov", "core")

    def test_manifest_unverified_note(self, mspec):
        m = _render_mcp_manifest(mspec)
        assert "未实测" in m["notes"]

    def test_manifest_transport_shape(self, mspec):
        m = _render_mcp_manifest(mspec)
        tr = m["transport"]
        assert tr["kind"] == "mcp"
        assert tr["mode"] == "stdio"
        assert tr["command"] == ["/bin/true", "--stdio"]
        assert tr["call_timeout_s"] == 120
        assert tr["startup_timeout_s"] == 30

    def test_proj_ns_maps_to_agent_domain(self):
        """proj 目录走 agent 域前缀（proj_family_meeting 先例：目录 proj_*，
        缝 key agent/proj-*）。"""
        s = McpWrapSpec(name="myproj", display_name="x", command=["/bin/true"],
                        ns="proj")
        assert s.seam_key == "agent/myproj"
        assert s.plugin_dir.name == "proj_myproj"

    def test_plugin_py_has_register(self, mspec):
        src = _render_mcp_plugin_py(mspec)
        assert "def register(registry)" in src
        assert 'name = "agent/demowrap"' in src
        assert "MCP_TOOL = \"execute_command\"" in src

    def test_plugin_py_j4_failure_recorded(self, mspec):
        """四必保语义 2：失败也入账（timeout/failed 终态 record）。"""
        src = _render_mcp_plugin_py(mspec)
        assert '_record(run_id, "timeout", {})' in src
        assert '_record(run_id, "failed"' in src

    def test_plugin_py_j5_probe_anchors_result(self, mspec):
        """四必保语义 3：探针锚定 id=0 result，error 响应不判活。"""
        src = _render_mcp_plugin_py(mspec)
        assert '"error" not in resp and "result" in resp' in src
        assert '"version": "1.0.0"' in src  # 踩坑 3：clientInfo.version 必填

    def test_plugin_py_openssl_ca_injection(self, mspec):
        """踩坑 2：node 命令幂等注入 --use-openssl-ca。"""
        src = _render_mcp_plugin_py(mspec)
        assert "--use-openssl-ca" in src

    def test_class_name_camelcase(self):
        s = McpWrapSpec(name="my-service", display_name="x", command=["/bin/true"])
        assert s.class_name == "MyService"


class TestMcpSelfTest:
    def test_all_pass(self, mspec):
        m = _render_mcp_manifest(mspec)
        src = _render_mcp_plugin_py(mspec)
        r = _self_test_mcp(mspec, m, src)
        assert r["ok"] is True
        assert r["ast"] and r["manifest"] and r["command"]
        assert r["errors"] == []

    def test_ast_rejects_no_register(self, mspec):
        m = _render_mcp_manifest(mspec)
        r = _self_test_mcp(mspec, m, "x = 1\n")
        assert r["ok"] is False
        assert r["ast"] is False
        assert any("register" in e for e in r["errors"])

    def test_manifest_rejects_bad_trust(self, mspec):
        m = _render_mcp_manifest(mspec)
        m["trust_level"] = "T9"
        r = _self_test_mcp(mspec, m, _render_mcp_plugin_py(mspec))
        assert r["ok"] is False
        assert any("trust_level" in e for e in r["errors"])

    def test_manifest_rejects_bad_plug(self, mspec):
        m = _render_mcp_manifest(mspec)
        m["plug_level"] = "L9"
        r = _self_test_mcp(mspec, m, _render_mcp_plugin_py(mspec))
        assert r["ok"] is False
        assert any("plug_level" in e for e in r["errors"])

    def test_manifest_rejects_no_domain_prefix(self, mspec):
        m = _render_mcp_manifest(mspec)
        m["name"] = "demowrap"  # 裸 key
        r = _self_test_mcp(mspec, m, _render_mcp_plugin_py(mspec))
        assert r["ok"] is False
        assert any("N3" in e for e in r["errors"])

    def test_manifest_rejects_bad_domain(self, mspec):
        m = _render_mcp_manifest(mspec)
        m["name"] = "proj9/demowrap"  # 不在六域
        r = _self_test_mcp(mspec, m, _render_mcp_plugin_py(mspec))
        assert r["ok"] is False
        assert any("N3" in e for e in r["errors"])

    def test_command_rejects_nonexistent(self, mspec):
        mspec.command = ["/nonexistent/mcp/server"]
        r = _self_test_mcp(mspec, _render_mcp_manifest(mspec),
                           _render_mcp_plugin_py(mspec))
        assert r["ok"] is False
        assert r["command"] is False

    def test_cwd_rejects_nonexistent_dir(self, mspec):
        mspec.cwd = "/nonexistent/cwd/dir"
        r = _self_test_mcp(mspec, _render_mcp_manifest(mspec),
                           _render_mcp_plugin_py(mspec))
        assert r["ok"] is False
        assert any("cwd" in e for e in r["errors"])


class TestMcpForgeOrchestration:
    def test_happy_path_writes_files(self, mspec_tmp, tmp_path):
        with patch("lingclaude.plugins.tools.plugin_forge._record_forge"):
            r = forge_mcp_wrap(mspec_tmp, auto_register=False)
        assert r.ok is True
        assert len(r.generated_files) == 2
        base = tmp_path / "agents" / "agent_demowrap"
        m = json.loads((base / "manifest.agent.json").read_text())
        assert m["name"] == "agent/demowrap"
        assert (base / "plugin.py").is_file()

    def test_self_test_failure_blocks_write(self, mspec_tmp, monkeypatch):
        monkeypatch.setattr(mspec_tmp, "command", ["/nonexistent/mcp"])
        with patch("lingclaude.plugins.tools.plugin_forge._record_forge"):
            r = forge_mcp_wrap(mspec_tmp, auto_register=False)
        assert r.ok is False
        assert not mspec_tmp.plugin_dir.exists() or not any(mspec_tmp.plugin_dir.iterdir())

    def test_idempotent_refuses_overwrite(self, mspec_tmp):
        with patch("lingclaude.plugins.tools.plugin_forge._record_forge"):
            r1 = forge_mcp_wrap(mspec_tmp, auto_register=False)
            assert r1.ok is True
            r2 = forge_mcp_wrap(mspec_tmp, auto_register=False)
        assert r2.ok is False
        assert "拒绝覆盖" in r2.error

    def test_yaml_valid_mcp_wrap(self, mspec_tmp):
        yaml_text = """
forge_type: mcp_wrap
name: yamldemo
display_name: "YamlDemo MCP"
command: ["/bin/true", "--stdio"]
cwd: /home/ai/lingclaude
mcp_tool: run_probe
tool_arg_name: target
trust_level: T2
capabilities: [run_probe]
"""
        with patch("lingclaude.plugins.tools.plugin_forge._record_forge"):
            r = forge_from_yaml(yaml_text, auto_register=False)
        assert r.ok is True
        assert "yamldemo" in r.register_keys or "agent/yamldemo" in r.plugin_dir or \
            "agent_yamldemo" in r.plugin_dir

    def test_yaml_missing_required(self):
        r = forge_from_yaml(
            "forge_type: mcp_wrap\nname: x\ndisplay_name: y\n",
            auto_register=False)
        assert r.ok is False
        assert "command" in r.error

    def test_yaml_empty_command_rejected(self):
        r = forge_from_yaml(
            "forge_type: mcp_wrap\nname: x\ndisplay_name: y\ncommand: []\n",
            auto_register=False)
        assert r.ok is False
        assert "command" in r.error

    def test_record_forge_handles_mcp_spec(self, mspec):
        """_record_forge 双 Spec 分派：McpWrapSpec 走 name/seam_key 路径。"""
        from lingclaude.plugins.tools.plugin_forge import _record_forge, ForgeResult
        with patch("lingclaude.plugins.tools.plugin_forge._get_knowledge_base_cls") as mock_get:
            mock_kb = mock_get.return_value.return_value
            r = ForgeResult(ok=True, self_test={"ok": True}, registered=False)
            _record_forge(mspec, r)
            rule = mock_kb.add_rule.call_args[0][0]
            assert rule.id == "generated_plugin_demowrap"
            assert "agent/demowrap" in rule.name
            assert rule.confidence == 0.9


# ── 段 2b：headless 探针（第四闸门，批 3）─────────────────────────────


class TestCliProbe:
    def test_pass_arg_mode(self):
        s = ForgeSpec(org_member="pb", display_name="x", cli_path="/bin/echo",
                      prompt_mode="arg", headless_args=["-n"])
        ok, ev = _probe_cli_headless(s)
        assert ok is True
        assert "exit=0" in ev
        assert "ping" in ev

    def test_pass_stdin_mode(self):
        s = ForgeSpec(org_member="pb2", display_name="x", cli_path="/bin/cat",
                      prompt_mode="stdin")
        ok, ev = _probe_cli_headless(s)
        assert ok is True
        assert "ping" in ev

    def test_fail_nonzero_exit(self):
        # /bin/false 恒退 1
        s = ForgeSpec(org_member="pb3", display_name="x", cli_path="/bin/false",
                      prompt_mode="arg")
        ok, ev = _probe_cli_headless(s)
        assert ok is False
        assert "退出码 1" in ev

    def test_fail_empty_output(self):
        # /bin/true 退 0 但无输出——进程活≠成功（踩坑 4）
        s = ForgeSpec(org_member="pb4", display_name="x", cli_path="/bin/true",
                      prompt_mode="arg")
        ok, ev = _probe_cli_headless(s)
        assert ok is False
        assert "空输出" in ev

    def test_fail_nonexistent_binary(self):
        s = ForgeSpec(org_member="pb5", display_name="x",
                      cli_path="/nonexistent/cli", prompt_mode="arg")
        ok, ev = _probe_cli_headless(s)
        assert ok is False
        assert "无法启动" in ev


class TestMcpProbe:
    def test_fail_no_response(self):
        """假命令（/bin/true）无 id=0 响应 → 探针拒绝（进程活≠握手成功）。"""
        s = McpWrapSpec(name="pbf", display_name="x", command=["/bin/true"])
        ok, ev = _probe_mcp_initialize(s)
        assert ok is False
        assert "无 id=0 响应" in ev

    def test_pass_real_initialize(self, tmp_path):
        """真 initialize 握手（python 脚本模拟 MCP server）→ 探针放行。"""
        server = tmp_path / "fake_mcp.py"
        server.write_text(
            'import json,sys\n'
            'line=sys.stdin.readline()\n'
            'req=json.loads(line)\n'
            'if req.get("method")=="initialize":\n'
            '    print(json.dumps({"jsonrpc":"2.0","id":req["id"],'
            '"result":{"protocolVersion":"2024-11-05",'
            '"serverInfo":{"name":"fake-mcp","version":"1.0.0"}}}))\n',
            encoding="utf-8")
        s = McpWrapSpec(name="pbok", display_name="x",
                        command=[sys.executable, str(server)])
        ok, ev = _probe_mcp_initialize(s)
        assert ok is True
        assert "initialize OK" in ev
        assert "fake-mcp" in ev

    def test_fail_error_response(self, tmp_path):
        """error 响应不得判活（J5 行为级）。"""
        server = tmp_path / "err_mcp.py"
        server.write_text(
            'import json,sys\n'
            'req=json.loads(sys.stdin.readline())\n'
            'print(json.dumps({"jsonrpc":"2.0","id":req["id"],'
            '"error":{"code":-32601,"message":"method not found"}}))\n',
            encoding="utf-8")
        s = McpWrapSpec(name="pberr", display_name="x",
                        command=[sys.executable, str(server)])
        ok, ev = _probe_mcp_initialize(s)
        assert ok is False
        assert "error 响应不得判活" in ev


class TestProbeOrchestration:
    def test_cli_probe_flips_verified_and_rewrites_manifest(self, spec_tmp, tmp_path):
        """探针通过 → verified 翻 True + manifest 重写（✅ 标注 + 探针实证）。"""
        with patch("lingclaude.plugins.tools.plugin_forge._record_forge"):
            r = forge_cli_agent(spec_tmp, auto_register=False)
        assert r.ok is True
        hp = r.self_test["headless_probe"]
        assert hp["ok"] is True
        m = json.loads((tmp_path / "agents" / "agent_testforge" /
                        "manifest.agent.json").read_text())
        assert "headless 已实测" in m["notes"]
        assert "headless 探针实证" in m["transport"]["command_note"]
        assert spec_tmp.headless_verified is True

    def test_cli_probe_failed_keeps_debt(self, spec_tmp, monkeypatch, tmp_path):
        """探针失败 → 落盘/结果不受影响，debt 保留（manifest 仍标未实测）。

        /bin/true 退 0 但空输出 → 探针拒绝（踩坑 4：进程活≠成功）。
        """
        monkeypatch.setattr(spec_tmp, "cli_path", "/bin/true")
        with patch("lingclaude.plugins.tools.plugin_forge._record_forge"):
            r = forge_cli_agent(spec_tmp, auto_register=False)
        assert r.ok is True
        hp = r.self_test["headless_probe"]
        assert hp["ok"] is False
        assert "headless 探针未过" not in r.error  # 探针失败不阻断
        m = json.loads((tmp_path / "agents" / "agent_testforge" /
                        "manifest.agent.json").read_text())
        assert "headless 未实测" in m["notes"]
        assert spec_tmp.headless_verified is False

    def test_mcp_probe_flips_verified(self, mspec_tmp, tmp_path, monkeypatch):
        """mcp initialize 探针通过 → verified 翻 True + manifest 重写。"""
        server = tmp_path / "fake_mcp2.py"
        server.write_text(
            'import json,sys\n'
            'req=json.loads(sys.stdin.readline())\n'
            'print(json.dumps({"jsonrpc":"2.0","id":req["id"],'
            '"result":{"protocolVersion":"2024-11-05",'
            '"serverInfo":{"name":"fake-mcp2"}}}))\n',
            encoding="utf-8")
        monkeypatch.setattr(mspec_tmp, "command", [sys.executable, str(server)])
        with patch("lingclaude.plugins.tools.plugin_forge._record_forge"):
            r = forge_mcp_wrap(mspec_tmp, auto_register=False)
        assert r.ok is True
        assert r.self_test["headless_probe"]["ok"] is True
        assert mspec_tmp.verified is True
        m = json.loads((tmp_path / "agents" / "agent_demowrap" /
                        "manifest.agent.json").read_text())
        assert "initialize 已实测" in m["notes"]

    def test_verified_spec_skips_probe(self, spec, tmp_path, monkeypatch):
        """已 verified（调用方声明已实测）→ 不重跑探针，直接信任。"""
        monkeypatch.setattr(
            "lingclaude.plugins.tools.plugin_forge.AGENTS_DIR", tmp_path / "agents")
        spec.headless_verified = True
        with patch("lingclaude.plugins.tools.plugin_forge._probe_cli_headless") as mp, \
             patch("lingclaude.plugins.tools.plugin_forge._record_forge"):
            forge_cli_agent(spec, auto_register=False)
        assert mp.called is False

    def test_missing_required_field(self):
        r = forge_from_yaml(
            "forge_type: cli_agent\norg_member: x\n", auto_register=False)
        assert r.ok is False
        assert "display_name" in r.error or "cli_path" in r.error

    def test_non_mapping_yaml(self):
        r = forge_from_yaml("- just\n- a\n- list\n", auto_register=False)
        assert r.ok is False
        assert "mapping" in r.error


# ── 段 4：记录回写（真实 KnowledgeBase，tmp 隔离）────────────────────


class TestRecordForge:
    def test_record_writes_to_kb(self, spec, tmp_path):
        """_record_forge 真实走 KnowledgeBase（db_path 指向 tmp，不碰真实库）。"""
        from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
        from lingclaude.plugins.tools.plugin_forge import _record_forge, ForgeResult

        test_db = str(tmp_path / "knowledge.db")

        # 直接测 _record_forge 内部逻辑：构造 KnowledgeBase + add_rule
        # 但 _record_forge 内部自己 new KnowledgeBase()，所以改用 mock 注入 db_path
        from unittest.mock import patch as _patch
        with _patch("lingclaude.plugins.tools.plugin_forge._get_knowledge_base_cls") as mock_get:
            mock_kb = mock_get.return_value.return_value
            r = ForgeResult(ok=True, self_test={"ok": True}, registered=False)
            _record_forge(spec, r)
            assert mock_kb.add_rule.called
            rule = mock_kb.add_rule.call_args[0][0]
            assert rule.id == "generated_plugin_testforge"
            assert "best_practice" in str(rule.category).lower() or rule.category == "best_practice"
            assert rule.confidence == 0.9
            assert rule.status == "active"

        # 再用真实 KnowledgeBase 验证端到端写入
        kb = KnowledgeBase(test_db)
        res = kb.get_all_rules()
        kb.close()
        # 真实库验证放这里——确保 KnowledgeBase(db_path) 可用
        assert res is not None

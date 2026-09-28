"""plugin_forge — 插片生成回路：任务 → 生成 → 自测 → 自注册 → 记录。

灵元铁律对齐：
- 铁律 3（变化走接缝）：生成的产物只落在 plugins/agents/ 下，主干零 diff。
- 铁律 4（修剪语法）：每次生成回写 knowledge.db（含测试结果），低效生成可被追溯降级。
- 铁律 6（信任等级）：生成的 manifest 强制 trust_level/plug_level 声明，不默认放行。

设计约束（2026-09-28 首版）：
- 生成用模板渲染，不用 LLM 自由写代码——「结构确定、内容参数化」。
- LLM 只允许填 YAML 语义槽（display_name / kernel_note / capabilities），
  代码部分由模板保证。验证闸门可信后再放宽。
- 首版只实现 cli_agent 生成器（靶面 = crush/opencode/claude/qwen 同构薄壳，
  实测 15-19 行 plugin.py + 一份 manifest，差异面 100% 收敛在 manifest）。

回路四段接线：
  1. 生成：模板渲染 manifest.agent.json + plugin.py
  2. 自测：AST 解析 + manifest schema 校验 + CLI 路径存在性
  3. 自注册：落盘 → HotReloadTrigger.check() 拾取 → registry_loader 验证
  4. 记录：LearnedRule(category="generated_plugin") 回写 knowledge.db
"""
from __future__ import annotations

import ast
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any

logger = logging.getLogger(__name__)

LC_ROOT = Path(__file__).parents[3]
# 注意：AGENTS_DIR 必须与 registry_loader.AGENTS_ROOT 一致（包内路径），
# 不能锚到仓库根的 plugins/——那里是另一个目录。
AGENTS_DIR = Path(__file__).parent.parent / "agents"

# ── manifest 必填字段（实测 agent_crush/opencode 对齐）────────────────
_REQUIRED_MANIFEST_FIELDS = (
    "name", "target", "display_name", "version",
    "trust_level", "plug_level", "caller",
    "transport", "capabilities", "health_probe",
    "state_record", "org_member",
)
_REQUIRED_TRANSPORT_FIELDS = ("kind", "mode", "call_timeout_s", "command")

# ── 薄壳 plugin.py 模板（实测 agent_crush/plugin.py 结构）─────────────
_PLUGIN_PY_TEMPLATE = Template('''"""agent/$org_member 插片 — $target 外部 agent 桥（plugin_forge 生成，cli-subprocess 薄壳）。"""
from __future__ import annotations

from pathlib import Path

from lingclaude.plugins.agents.agent_cli_base import CliAgentPluginBase, make_register

MANIFEST_PATH = Path(__file__).parent / "manifest.agent.json"


class ${class_name}Plugin(CliAgentPluginBase):
    """agent/$org_member — $headless_desc 子进程桥（manifest 全驱动，无本地逻辑）。

    ⚠️ plugin_forge 生成（$forge_date）：$gen_note
    """


register = make_register(${class_name}Plugin, MANIFEST_PATH)
''')


@dataclass
class ForgeSpec:
    """cli_agent 生成器输入契约（YAML 语义槽）。

    首版只支持 forge_type=cli_agent；MCP 封装生成器为批2。
    """
    org_member: str                    # 短名（目录名 agent_{org_member}）
    display_name: str                  # 展示名
    cli_path: str                      # 可执行文件绝对路径
    headless_args: list[str] = field(default_factory=list)
    prompt_mode: str = "stdin"         # stdin | arg
    call_timeout_s: int = 600
    capabilities: list[str] = field(default_factory=lambda: ["code_generation"])
    kernel_note: str = ""              # 内核说明（版本/厂商）
    headless_verified: bool = False    # 是否已实测 headless
    gen_note: str = "manifest 全驱动，无本地逻辑"   # 生成备注（docstring 内）

    @property
    def target(self) -> str:
        return f"{self.org_member}-cli"

    @property
    def class_name(self) -> str:
        return "".join(w.capitalize() for w in self.org_member.split("_"))

    @property
    def plugin_dir(self) -> Path:
        return AGENTS_DIR / f"agent_{self.org_member}"


@dataclass
class ForgeResult:
    """生成回路结果（四段各自的成败 + 证据）。"""
    ok: bool
    plugin_dir: str = ""
    generated_files: list[str] = field(default_factory=list)
    self_test: dict[str, Any] = field(default_factory=dict)   # ast/manifest/cli 三项
    registered: bool = False
    register_keys: list[str] = field(default_factory=list)
    error: str = ""


# ── 段 1：生成（模板渲染）────────────────────────────────────────────

def _render_manifest(spec: ForgeSpec) -> dict[str, Any]:
    """按 crush/opencode 实测 manifest 结构渲染（字段顺序对齐，diff 最小化）。"""
    return {
        "name": f"agent/{spec.org_member}",
        "target": spec.target,
        "display_name": spec.display_name,
        "version": "0.1.0",
        "trust_level": "T2",
        "plug_level": "L1",
        "caller": "lingclaude",
        "transport": {
            "kind": "cli-subprocess",
            "mode": "exec",
            "call_timeout_s": spec.call_timeout_s,
            "command": [spec.cli_path, *spec.headless_args],
            "prompt_mode": spec.prompt_mode,
            "command_note": (
                f"plugin_forge 生成（2026-09-28）：{spec.org_member} headless "
                f"{'arg' if spec.prompt_mode == 'arg' else 'stdin'} 模式。"
                + ("headless 未实测，首次派单前需人工验证。" if not spec.headless_verified else "")
            ),
        },
        "stop_layer": {
            "kernel": spec.kernel_note or f"外部 agent 桥（{spec.target}）",
            "seams": ["family_agent"],
            "implementations": 1,
            "note": "cli-subprocess 型，共享 CliAgentPluginBase；record 家法/缺席查/fail-soft 对齐 agent_family。",
        },
        "capabilities": spec.capabilities,
        "health_probe": {
            "interval_s": 300,
            "timeout_s": 10,
            "method": "version_check",
            "absent_after": 2,
        },
        "state_record": f"agent_run:{spec.org_member}",
        "org_member": spec.org_member,
        "notes": (
            f"plugin_forge 生成插片（{spec.gen_note}）。"
            + ("" if spec.headless_verified else " ⚠️ headless 未实测——首个 run 可能返回 failed（缺席查语义，不假活）。")
        ),
    }


def _render_plugin_py(spec: ForgeSpec, forge_date: str = "2026-09-28") -> str:
    return _PLUGIN_PY_TEMPLATE.substitute(
        org_member=spec.org_member,
        target=spec.target,
        class_name=spec.class_name,
        headless_desc=" ".join(spec.headless_args) or "exec",
        forge_date=forge_date,
        gen_note=spec.gen_note,
    )


# ── 段 2：自测（AST + manifest schema + CLI 存在性）───────────────────

def _self_test(spec: ForgeSpec, manifest: dict[str, Any], plugin_src: str) -> dict[str, Any]:
    """生成后自动自测——三段闸门，任一不过即整体失败。"""
    result: dict[str, Any] = {"ast": False, "manifest": False, "cli_path": False, "errors": []}

    # AST 检查（对齐 AGENTS.md「.py 写入前 AST 检查」）
    try:
        tree = ast.parse(plugin_src)
        has_register = any(
            isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "register" for t in n.targets
            ) for n in ast.walk(tree)
        )
        if not has_register:
            result["errors"].append("plugin.py 缺 register 入口（registry_loader 契约）")
        else:
            result["ast"] = True
    except SyntaxError as e:
        result["errors"].append(f"AST 解析失败: {e}")

    # manifest schema 校验
    missing = [f for f in _REQUIRED_MANIFEST_FIELDS if f not in manifest]
    if missing:
        result["errors"].append(f"manifest 缺字段: {missing}")
    else:
        tr = manifest.get("transport", {})
        missing_tr = [f for f in _REQUIRED_TRANSPORT_FIELDS if f not in tr]
        if missing_tr:
            result["errors"].append(f"transport 缺字段: {missing_tr}")
        elif manifest["name"] != f"agent/{spec.org_member}":
            result["errors"].append(f"manifest.name 与 org_member 不一致: {manifest['name']}")
        else:
            result["manifest"] = True

    # CLI 路径存在性（生成时即可发现路径笔误）
    cli = spec.cli_path
    if Path(cli).is_file() or shutil.which(cli):
        result["cli_path"] = True
    else:
        result["errors"].append(f"CLI 路径不存在: {cli}（生成的插片健康探针将缺席查）")

    result["ok"] = result["ast"] and result["manifest"] and result["cli_path"]
    return result


# ── 段 4：记录（knowledge.db 回写）────────────────────────────────────

# 延迟绑定的 KnowledgeBase（测试可 patch 此模块属性）
_KnowledgeBase = None

def _get_knowledge_base_cls():
    """延迟 import KnowledgeBase——避免模块加载时的重依赖链。"""
    global _KnowledgeBase
    if _KnowledgeBase is None:
        from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
        _KnowledgeBase = KnowledgeBase
    return _KnowledgeBase


def _record_forge(spec: ForgeSpec, result: ForgeResult) -> None:
    """生成参数+测试结果回写 knowledge.db（fail-soft：库缺席只记日志）。"""
    try:
        from lingclaude.self_optimizer.learner.models import FeedbackCategory, LearnedRule, Pattern
        KB = _get_knowledge_base_cls()
        kb = KB()
        rule = LearnedRule(
            id=f"generated_plugin_{spec.org_member}",
            name=f"生成插片 agent/{spec.org_member}",
            description=(
                f"plugin_forge 生成 agent/{spec.org_member}: "
                f"self_test={'PASS' if result.self_test.get('ok') else 'FAIL'}, "
                f"registered={result.registered}, cli={spec.cli_path}"
            ),
            category=FeedbackCategory.BEST_PRACTICE,
            pattern=Pattern(
                context_keywords=(spec.org_member, "plugin_forge", "generated"),
            ),
            tools=("plugin_forge",),
            frequency=1,
            confidence=0.9 if result.ok else 0.3,
            status="active" if result.ok else "draft",
        )
        kb.add_rule(rule)
        kb.close()
    except Exception as e:  # 记录失败不阻断生成回路
        logger.warning("plugin_forge 记录回写失败（fail-soft）: %s", e)


# ── 编排函数：四段串成一条回路 ────────────────────────────────────────

def forge_cli_agent(spec: ForgeSpec, *, auto_register: bool = True) -> ForgeResult:
    """cli_agent 生成回路主编排：生成 → 自测 → 落盘 → 自注册 → 记录。

    :param spec: 输入契约（YAML 语义槽填充后的 dataclass）
    :param auto_register: 是否触发 hot_reload 拾取（测试可关闭走手动验证）
    :return: ForgeResult（每段成败独立可查）
    """
    result = ForgeResult(ok=False, plugin_dir=str(spec.plugin_dir))

    # 幂等守卫：目录已存在且非空 → 拒绝覆盖（add-only 语义，对齐 hot_reload）
    if spec.plugin_dir.exists() and any(spec.plugin_dir.iterdir()):
        result.error = f"目录已存在且非空: {spec.plugin_dir}（add-only，拒绝覆盖）"
        _record_forge(spec, result)
        return result

    # 段 1：生成
    manifest = _render_manifest(spec)
    plugin_src = _render_plugin_py(spec)

    # 段 2：自测（落盘前——不干净的产物不进插件目录）
    result.self_test = _self_test(spec, manifest, plugin_src)
    if not result.self_test.get("ok"):
        result.error = f"自测未过: {result.self_test.get('errors')}"
        _record_forge(spec, result)
        return result

    # 落盘（自测通过才写——这是「生成后自动跑」与「先写再查」的分界）
    spec.plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = spec.plugin_dir / "manifest.agent.json"
    plugin_path = spec.plugin_dir / "plugin.py"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plugin_path.write_text(plugin_src, encoding="utf-8")
    result.generated_files = [str(manifest_path), str(plugin_path)]

    # 段 3：自注册（hot_reload 拾取 → registry_loader 验证）
    if auto_register:
        try:
            from lingclaude.engine.hot_reload_trigger import HotReloadTrigger
            from lingclaude.plugins.agents import registry_loader
            HotReloadTrigger().check()  # 强制一轮拾取（绕过节流：新目录 diff 必触发）
            reg = registry_loader.load_all()
            registered = reg.get("registered", [])
            expect_key = f"agent/{spec.org_member}"
            result.registered = expect_key in registered
            result.register_keys = [k for k in registered if spec.org_member in k]
            if not result.registered:
                result.error = f"注册验证失败: {expect_key} 未在 registered 列表"
        except Exception as e:
            result.error = f"自注册段异常（fail-soft，产物已落盘）: {e}"
            logger.warning("plugin_forge 自注册失败: %s", e)

    result.ok = result.self_test.get("ok", False) and (
        result.registered if auto_register else True)

    # 段 4：记录
    _record_forge(spec, result)
    return result


def forge_from_yaml(yaml_text: str, *, auto_register: bool = True) -> ForgeResult:
    """YAML 输入契约 → ForgeSpec → forge_cli_agent。LLM 只允许填 YAML 语义槽。"""
    import yaml  # 局部 import（pyyaml 已是依赖）
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        return ForgeResult(ok=False, error="YAML 顶层必须是 mapping")
    if data.get("forge_type") != "cli_agent":
        return ForgeResult(ok=False, error=f"首版只支持 forge_type=cli_agent，收到: {data.get('forge_type')}")
    try:
        spec = ForgeSpec(
            org_member=data["org_member"],
            display_name=data["display_name"],
            cli_path=data["cli_path"],
            headless_args=data.get("headless_args", []),
            prompt_mode=data.get("prompt_mode", "stdin"),
            call_timeout_s=int(data.get("call_timeout_s", 600)),
            capabilities=data.get("capabilities", ["code_generation"]),
            kernel_note=data.get("kernel_note", ""),
            headless_verified=bool(data.get("headless_verified", False)),
        )
    except KeyError as e:
        return ForgeResult(ok=False, error=f"YAML 缺必填字段: {e}")
    return forge_cli_agent(spec, auto_register=auto_register)

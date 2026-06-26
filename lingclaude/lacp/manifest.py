"""LACP plugin manifest schema v0.5.0 — reference implementation.

核心定位：插片契约 (LACP plugin manifest = 灵族"插片身份证")

字段来源:
- 灵元 1.0 "薄主干+插片" — 插片必须有可声明接口才能热替换
- Agent-Native shared action 6-channel 借鉴 → transports 字段
- 灵信 R1 建议 A: output_recipient (LingBus/external/both)
- 灵信 R1 建议 B: dependencies 支持 config 形式
- 灵信 R1 建议 C: replaceable 默认 false (安全默认)
- 灵通 R3 首批 3 插片: scheduler / provider_adapter / health_filter
- 灵信 R1 首批 4 插片: MessagePipeline / 5 middleware / redzone_check / signing

字段全清单:
- name: kebab-case plugin 名
- version: semver
- owner: 灵族成员名
- description: 一句话
- interface:
    input_schema: json-schema ref or inline
    output_schema: json-schema ref or inline
    error_format: { type, severity, recovery, retry_strategy }
- transports: list of [ui, agent, http, mcp, a2a, cli]
- output_recipient: LingBus | external | both
- dependencies:
    - plugin: <name@version-range>
    - config: <key.path>      # 灵信建议
    - service: <url-pattern>
- replaceable: cold | warm | hot | false  # 默认 false (灵信建议)
- replaced_by: plugin@version | null
- signature: hmac-sha256 | none
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Union

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "0.5.0"


# === Enums ===
class Transport(str, Enum):
    """Agent-Native 借鉴: 6 种 transport channel."""
    UI = "ui"             # WebUI 给用户
    AGENT = "agent"       # 其他灵调用
    HTTP = "http"         # 外部 HTTP API
    MCP = "mcp"           # MCP 标准协议
    A2A = "a2a"           # Agent-to-Agent 协议
    CLI = "cli"           # 手动 CLI


class OutputRecipient(str, Enum):
    """灵信建议 A: 数据流向声明."""
    LINGBUS = "LingBus"
    EXTERNAL = "external"
    BOTH = "both"


class Replaceable(str, Enum):
    """灵信建议 C: 安全默认 false.

    false = 不可替换 (默认, 灵克 owner 范围外不暴露)
    cold  = kill-reload (0 成本, 启动时替换)
    warm  = subprocess swap (中等成本, 进程重启)
    hot   = in-process (高成本, 动态加载)
    """
    FALSE = "false"
    COLD = "cold"
    WARM = "warm"
    HOT = "hot"


class DependencyKind(str, Enum):
    PLUGIN = "plugin"
    CONFIG = "config"
    SERVICE = "service"


class ErrorSeverity(str, Enum):
    RETRYABLE = "retryable"    # 自动重试
    FATAL = "fatal"            # 不可恢复
    DEGRADED = "degraded"      # 降级运行


# === Data classes ===
@dataclass
class Interface:
    """插件接口契约."""
    input_schema: Union[str, dict]  # JSON Schema ref URL or inline dict
    output_schema: Union[str, dict]
    error_format: dict = field(default_factory=lambda: {
        "type": "string",
        "severity": "retryable",
        "recovery": [],
        "retry_strategy": "exponential_backoff",
    })


@dataclass
class Dependency:
    """灵信建议 B: 支持 plugin / config / service 三种依赖."""
    kind: DependencyKind
    target: str  # plugin@version / config.key.path / http://service-pattern

    def __post_init__(self) -> None:
        if self.kind == DependencyKind.PLUGIN:
            # 必须 plugin-name@version-range 格式 (支持 >=1.0.0, ^1.0, ~1.0, 1.0.0 等)
            if not re.match(r"^[\w-]+@[\d.\-a-zA-Z*>=^~ ]+$", self.target):
                raise ValueError(f"plugin dependency must be 'name@version-range', got {self.target}")
        elif self.kind == DependencyKind.CONFIG:
            # 必须 dot-separated key path
            if not re.match(r"^[\w]+(\.[\w]+)+$", self.target):
                raise ValueError(f"config dependency must be 'a.b.c' key path, got {self.target}")
        elif self.kind == DependencyKind.SERVICE:
            # URL pattern
            if not self.target.startswith(("http://", "https://")):
                raise ValueError(f"service dependency must be URL, got {self.target}")


@dataclass
class Plugin:
    """LACP v0.5.0 plugin manifest."""

    name: str
    version: str
    owner: str
    description: str
    interface: Interface
    transports: list[Transport] = field(default_factory=list)
    output_recipient: OutputRecipient = OutputRecipient.LINGBUS
    dependencies: list[Dependency] = field(default_factory=list)
    replaceable: Replaceable = Replaceable.FALSE  # 默认安全 (灵信建议 C)
    replaced_by: str | None = None
    schema_version: str = SCHEMA_VERSION
    signature: str | None = None  # hmac-sha256 of manifest

    def __post_init__(self) -> None:
        # name: kebab-case
        if not re.match(r"^[a-z][\w-]*$", self.name):
            raise ValueError(f"name must be kebab-case (start with letter, [a-z0-9_-]), got {self.name}")
        # version: semver (basic check)
        if not re.match(r"^\d+\.\d+\.\d+", self.version):
            raise ValueError(f"version must be semver (x.y.z), got {self.version}")
        # owner: 灵族成员名
        if not re.match(r"^[a-z]+$", self.owner):
            raise ValueError(f"owner must be lowercase member name, got {self.owner}")
        # name unique
        if len(set(self.transports)) != len(self.transports):
            raise ValueError(f"transports must be unique, got {self.transports}")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # enum → str
        d["transports"] = [t.value for t in self.transports]
        d["output_recipient"] = self.output_recipient.value
        d["replaceable"] = self.replaceable.value
        d["dependencies"] = [
            {"kind": dep.kind.value, "target": dep.target}
            for dep in self.dependencies
        ]
        return d

    def to_yaml(self) -> str:
        """手写 YAML 输出（避免额外依赖）."""
        d = self.to_dict()
        return _dict_to_yaml(d)


# === Validator ===
def validate_manifest(p: Plugin | dict[str, Any]) -> tuple[bool, str | None]:
    """验证 plugin manifest 是否符合 LACP v0.5.0 schema."""
    if isinstance(p, Plugin):
        d = p.to_dict()
    else:
        d = p

    # schema_version
    if d.get("schema_version") != SCHEMA_VERSION:
        return False, f"schema_version must be {SCHEMA_VERSION}, got {d.get('schema_version')}"

    # name / version / owner / description 必填
    for f in ("name", "version", "owner", "description", "interface"):
        if not d.get(f):
            return False, f"{f} is required"

    # interface 内部
    iface = d.get("interface", {})
    if not iface.get("input_schema"):
        return False, "interface.input_schema is required"
    if not iface.get("output_schema"):
        return False, "interface.output_schema is required"

    # transports: 默认空 list (即不暴露任何 transport = private plugin)
    transports = d.get("transports", [])
    if not isinstance(transports, list):
        return False, f"transports must be list, got {type(transports).__name__}"
    valid_transports = {t.value for t in Transport}
    for t in transports:
        if t not in valid_transports:
            return False, f"transport must be one of {valid_transports}, got {t}"

    # replaceable: 默认 false
    rep = d.get("replaceable", "false")
    if rep not in {r.value for r in Replaceable}:
        return False, f"replaceable must be one of {{false, cold, warm, hot}}, got {rep}"

    # output_recipient
    orcp = d.get("output_recipient", "LingBus")
    if orcp not in {r.value for r in OutputRecipient}:
        return False, f"output_recipient must be one of {[r.value for r in OutputRecipient]}, got {orcp}"

    # dependencies
    deps = d.get("dependencies", [])
    if not isinstance(deps, list):
        return False, f"dependencies must be list, got {type(deps).__name__}"
    valid_dep_kinds = {k.value for k in DependencyKind}
    for dep in deps:
        if dep.get("kind") not in valid_dep_kinds:
            return False, f"dependency.kind must be one of {valid_dep_kinds}, got {dep.get('kind')}"

    # replaced_by: 如果 replaceable != false, 应该不为 null
    if rep != "false" and d.get("replaced_by") is None:
        # 允许 null (即"现在还没替换"), 只 warning 不 reject
        pass

    return True, None


# === Signature (HMAC-SHA256) ===
def sign_manifest(p: Plugin, secret: bytes) -> str:
    """签 manifest 用于审计/防篡改."""
    body = json.dumps(p.to_dict(), sort_keys=True, ensure_ascii=False).encode()
    sig = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{sig}"


# === 简单 YAML 输出 (避免 pyyaml 依赖) ===
def _dict_to_yaml(d: dict, indent: int = 0) -> str:
    """手写 YAML serializer (支持 dict / list / str / int / float / bool / None)."""
    lines = []
    prefix = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            lines.append(f"{prefix}{k}:")
            lines.append(_dict_to_yaml(v, indent + 1))
        elif isinstance(v, list):
            if not v:
                lines.append(f"{prefix}{k}: []")
            elif all(isinstance(x, str) for x in v):
                # inline list
                items = ", ".join(v)
                lines.append(f"{prefix}{k}: [{items}]")
            else:
                lines.append(f"{prefix}{k}:")
                for item in v:
                    if isinstance(item, dict):
                        first = True
                        for ik, iv in item.items():
                            if first:
                                lines.append(f"{prefix}  - {ik}: {_yaml_scalar(iv)}")
                                first = False
                            else:
                                if isinstance(iv, dict):
                                    lines.append(f"{prefix}    {ik}:")
                                    lines.append(_dict_to_yaml(iv, indent + 3))
                                else:
                                    lines.append(f"{prefix}    {ik}: {_yaml_scalar(iv)}")
        else:
            lines.append(f"{prefix}{k}: {_yaml_scalar(v)}")
    return "\n".join(lines)


def _yaml_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    yaml_special_chars = set(":#\n\"'")
    if any(c in s for c in yaml_special_chars) or s.startswith(tuple("?!&*[]{}|<>%@`")) or s.startswith("-"):
        return json.dumps(s, ensure_ascii=False)
    return s


# === 加载/解析 ===
def load_manifest(path: str | Path) -> Plugin:
    """从 YAML 文件加载 manifest."""
    import yaml  # 延迟导入，避免强制依赖
    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return _manifest_from_dict(data)


def _manifest_from_dict(d: dict[str, Any]) -> Plugin:
    """dict → Plugin instance."""
    iface = d.get("interface", {})
    interface = Interface(
        input_schema=iface.get("input_schema", {}),
        output_schema=iface.get("output_schema", {}),
        error_format=iface.get("error_format", {}),
    )
    transports = [Transport(t) for t in d.get("transports", [])]
    deps = []
    for dep in d.get("dependencies", []):
        deps.append(Dependency(
            kind=DependencyKind(dep["kind"]),
            target=dep["target"],
        ))
    return Plugin(
        name=d["name"],
        version=d["version"],
        owner=d["owner"],
        description=d.get("description", ""),
        interface=interface,
        transports=transports,
        output_recipient=OutputRecipient(d.get("output_recipient", "LingBus")),
        dependencies=deps,
        replaceable=Replaceable(d.get("replaceable", "false")),
        replaced_by=d.get("replaced_by"),
    )
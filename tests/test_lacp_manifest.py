"""LACP v0.5.0 plugin manifest tests.

8 个测试覆盖:
1. 最小有效 manifest (只必填字段)
2. transports 6-channel 全部接受
3. replaceable 默认 false (灵信建议 C)
4. output_recipient 三种值
5. dependencies 三种 kind (plugin/config/service)
6. 签名 + 验签
7. YAML 序列化往返 (load_manifest)
8. 拒绝非法 name/version/transport
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from lingclaude.lacp import (
    Dependency,
    DependencyKind,
    Interface,
    OutputRecipient,
    Plugin,
    Replaceable,
    Transport,
    load_manifest,
    sign_manifest,
    validate_manifest,
    MANIFEST_SCHEMA_VERSION,
)


def _minimal_plugin(**overrides) -> Plugin:
    """构造最小有效 plugin."""
    defaults = dict(
        name="audit-scanner",
        version="1.0.0",
        owner="lingclaude",
        description="Audit scanner for code patterns",
        interface=Interface(
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"findings": {"type": "array"}}},
        ),
    )
    defaults.update(overrides)
    return Plugin(**defaults)


def test_minimal_manifest_valid():
    """T1: 最小有效 manifest (只必填字段) 通过验证."""
    p = _minimal_plugin()
    ok, err = validate_manifest(p)
    assert ok, f"minimal should be valid: {err}"
    d = p.to_dict()
    # 默认值
    assert d["transports"] == []
    assert d["output_recipient"] == "LingBus"
    assert d["replaceable"] == "false"
    assert d["dependencies"] == []


def test_transports_six_channels():
    """T2: transports 6-channel 全部接受 (Agent-Native 借鉴)."""
    for t in [Transport.UI, Transport.AGENT, Transport.HTTP,
              Transport.MCP, Transport.A2A, Transport.CLI]:
        p = _minimal_plugin(transports=[t])
        ok, err = validate_manifest(p)
        assert ok, f"transport {t.value} should be accepted: {err}"

    # 全部 6 个一起
    p = _minimal_plugin(transports=list(Transport))
    ok, err = validate_manifest(p)
    assert ok, f"all 6 transports should be accepted: {err}"
    assert len(p.to_dict()["transports"]) == 6


def test_replaceable_default_false():
    """T3: replaceable 默认 false 安全默认 (灵信建议 C)."""
    p = _minimal_plugin()
    assert p.replaceable == Replaceable.FALSE
    assert p.to_dict()["replaceable"] == "false"

    # 显式设 cold/warm/hot 也 OK
    for r in [Replaceable.COLD, Replaceable.WARM, Replaceable.HOT]:
        p2 = _minimal_plugin(replaceable=r)
        ok, err = validate_manifest(p2)
        assert ok, f"replaceable={r.value} should be accepted: {err}"


def test_output_recipient_three_values():
    """T4: output_recipient 三种值 (灵信建议 A)."""
    for r in [OutputRecipient.LINGBUS, OutputRecipient.EXTERNAL, OutputRecipient.BOTH]:
        p = _minimal_plugin(output_recipient=r)
        ok, err = validate_manifest(p)
        assert ok, f"output_recipient={r.value} should be accepted: {err}"
        assert p.to_dict()["output_recipient"] == r.value


def test_dependencies_three_kinds():
    """T5: dependencies 三种 kind (灵信建议 B)."""
    deps = [
        Dependency(kind=DependencyKind.PLUGIN, target="lingbus@1.0.0"),
        Dependency(kind=DependencyKind.CONFIG, target="signing.secret_key"),
        Dependency(kind=DependencyKind.SERVICE, target="http://127.0.0.1:9532"),
    ]
    p = _minimal_plugin(dependencies=deps)
    ok, err = validate_manifest(p)
    assert ok, f"3 dependency kinds should be accepted: {err}"
    d = p.to_dict()
    assert len(d["dependencies"]) == 3
    assert d["dependencies"][0]["kind"] == "plugin"
    assert d["dependencies"][1]["kind"] == "config"
    assert d["dependencies"][2]["kind"] == "service"


def test_signature_and_verification():
    """T6: HMAC-SHA256 签名 + 验签."""
    p = _minimal_plugin()
    secret = b"lingfamily-secret-2026"
    sig = sign_manifest(p, secret)
    assert sig.startswith("hmac-sha256:")
    assert len(sig) > 20
    p2 = _minimal_plugin()
    sig2 = sign_manifest(p2, secret)
    # 同 secret, 同 content → 同 signature
    assert sig == sig2
    # 不同 secret → 不同 signature
    sig3 = sign_manifest(p, b"other-secret")
    assert sig != sig3


def test_yaml_round_trip():
    """T7: YAML 序列化往返 (to_yaml + load_manifest)."""
    import yaml
    p = _minimal_plugin(
        transports=[Transport.MCP, Transport.A2A],
        dependencies=[
            Dependency(kind=DependencyKind.PLUGIN, target="audit_scanner@1.0.0"),
        ],
        replaceable=Replaceable.COLD,
    )
    yaml_str = p.to_yaml()
    assert "name: audit-scanner" in yaml_str
    assert "transports: [mcp, a2a]" in yaml_str
    assert "replaceable: cold" in yaml_str

    # 写入文件 + 读回
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        loaded = load_manifest(path)
        assert loaded.name == p.name
        assert loaded.version == p.version
        assert loaded.replaceable == Replaceable.COLD
        assert Transport.MCP in loaded.transports


def test_rejects_invalid_fields():
    """T8: 拒绝非法 name / version / transport."""
    # name 大写
    with pytest.raises(ValueError, match="kebab-case"):
        _minimal_plugin(name="AuditScanner")

    # version 非 semver
    with pytest.raises(ValueError, match="semver"):
        _minimal_plugin(version="v1")

    # owner 大写
    with pytest.raises(ValueError, match="lowercase"):
        _minimal_plugin(owner="LingClaude")

    # invalid dependency kind format
    with pytest.raises(ValueError, match="name@version-range"):
        Dependency(kind=DependencyKind.PLUGIN, target="lingbus")
    with pytest.raises(ValueError, match="key path"):
        Dependency(kind=DependencyKind.CONFIG, target="signing")
    with pytest.raises(ValueError, match="URL"):
        Dependency(kind=DependencyKind.SERVICE, target="lingbus")

    # validate 拒绝非法 transport
    p = _minimal_plugin()
    d = p.to_dict()
    d["transports"] = ["invalid_transport"]
    ok, err = validate_manifest(d)
    assert not ok
    assert "transport" in err.lower()

    # validate 拒绝错 schema_version
    d2 = p.to_dict()
    d2["schema_version"] = "0.4.0"
    ok2, err2 = validate_manifest(d2)
    assert not ok2
    assert "schema_version" in err2.lower()
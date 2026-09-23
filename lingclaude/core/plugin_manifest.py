"""灵元 R2: PluginManifest —— 插件清单数据类 + JSON schema 校验。

P3-3 (2026-09-14, 以灵元 1.0 为尺):
  - 插件 = manifest 声明（name/version/type/entry/requires），加载由 plugin_loader
  - 校验 = JSON schema（声明式，拒绝非法清单在加载前 fail fast）
  - 与灵元"插片 = manifest 注册，主干 diff = 0"对齐：新增插件 = 新增 manifest

用法:
    m = PluginManifest(name="my-tool", version="1.0.0",
                       type=SeamType.TOOL, entry="plugin.py:MyTool")
    errors = m.validate()          # -> [] 合法
    PluginManifest.from_dict({...})  # 从 dict/JSON 构建，非法抛 ValueError
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lingclaude.core.seam import SeamType

# 合法插件名：小写字母/数字/连字符/下划线，允许域前缀分隔符 /（N3 域前缀）。
# 首字符限字母数字 → 拒绝 /、..、~ 开头（防路径注入保持）。
# 例：read（core 域裸 key 存量豁免）、agent/lingxi（跨物理层域前缀）。
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_/-]*$")

# JSON schema（草案-07 子集）：声明式校验，非法清单在加载前拦截
MANIFEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "version", "type", "entry"],
    "properties": {
        "name": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_/-]*$"},
        "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
        "type": {
            "type": "string",
            "enum": [t.value for t in SeamType],
        },
        "entry": {"type": "string", "minLength": 1},
        "test_entry": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        # 铁律 2（分形停层显式化）声明：内核 / 子插片接缝 / 实现数
        "stop_layer": {
            "type": "object",
            "properties": {
                "kernel": {"type": "string", "minLength": 1},
                "seams": {"type": "array", "items": {"type": "string"}},
                "implementations": {"type": "integer", "minimum": 1},
            },
            "required": ["kernel", "seams", "implementations"],
            "additionalProperties": False,
        },
        "requires": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
        "provides": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
        },
        "enabled": {"type": "boolean", "default": True},
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class PluginManifest:
    """插件清单：声明一个可加载插片的元数据。"""

    name: str
    version: str
    type: SeamType
    entry: str                    # "module.py:ClassName" 或 "module.py"
    test_entry: str = ""          # 插片自带测试入口（灵元「插片无测试=非法插片」，加载门禁用）
    description: str = ""
    # 铁律 2「停层显式化」：本插片的内核是什么 / 子插片接缝在哪 / 当前几个实现
    # （见 docs/LINGYUAN_IRON_LAW.md 细则 5）。结构见 MANIFEST_SCHEMA.stop_layer。
    stop_layer: dict[str, Any] | None = None
    requires: tuple[str, ...] = ()   # 依赖的插件/包名
    provides: tuple[str, ...] = ()   # 提供的能力名
    enabled: bool = True

    def validate(self) -> list[str]:
        """校验合法性，返回错误列表（空 = 合法）。"""
        errors: list[str] = []
        if not _NAME_RE.match(self.name):
            errors.append(f"name {self.name!r} 非法（须 ^[a-z0-9][a-z0-9_-]*$）")
        if not re.match(r"^\d+\.\d+\.\d+$", self.version):
            errors.append(f"version {self.version!r} 非法（须 x.y.z）")
        if not isinstance(self.type, SeamType):
            errors.append(f"type {self.type!r} 非法（须 SeamType 枚举）")
        if not self.entry or ":" not in self.entry and "." not in self.entry:
            errors.append(f"entry {self.entry!r} 非法（须 'file.py:ClassName' 或 'file.py'）")
        if self.stop_layer is not None:
            sl = self.stop_layer
            if not isinstance(sl, dict):
                errors.append("stop_layer 必须是 object（{kernel, seams, implementations}）")
            else:
                if not isinstance(sl.get("kernel"), str) or not sl.get("kernel"):
                    errors.append("stop_layer.kernel 缺失（本插片的内核是什么）")
                # 2026-09-23 兼容两种停层形态：扁平 {kernel,seams,implementations}
                # 或嵌套 {kernel, sub_seams:{seams,implementations,...}}（38c894e 关单形态）
                sub = sl.get("sub_seams")
                if isinstance(sub, dict):
                    seams = sub.get("seams")
                    impl = sub.get("implementations")
                else:
                    seams = sl.get("seams")
                    impl = sl.get("implementations")
                if not isinstance(seams, list) or not seams:
                    errors.append("stop_layer.seams 缺失（子插片接缝在哪）")
                if not isinstance(impl, int) or isinstance(impl, bool) or impl < 1:
                    errors.append("stop_layer.implementations 缺失（当前几个实现，≥1）")
        return errors

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（JSON 友好）。"""
        return {
            "name": self.name,
            "version": self.version,
            "type": self.type.value,
            "entry": self.entry,
            "description": self.description,
            "stop_layer": self.stop_layer,
            "requires": list(self.requires),
            "provides": list(self.provides),
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginManifest":
        """从 dict 构建；先 schema 校验，非法抛 ValueError（fail fast）。"""
        errors = validate_manifest_dict(data)
        if errors:
            raise ValueError(f"PluginManifest 非法: {'; '.join(errors)}")
        try:
            stype = SeamType(str(data["type"]))
        except ValueError as exc:
            raise ValueError(f"PluginManifest type {data['type']!r} 非法") from exc
        return cls(
            name=str(data["name"]),
            version=str(data["version"]),
            type=stype,
            entry=str(data["entry"]),
            test_entry=str(data.get("test_entry", "")),
            description=str(data.get("description", "")),
            stop_layer=data.get("stop_layer"),
            requires=tuple(str(x) for x in data.get("requires", [])),
            provides=tuple(str(x) for x in data.get("provides", [])),
            enabled=bool(data.get("enabled", True)),
        )

    @classmethod
    def from_json(cls, text: str) -> "PluginManifest":
        """从 JSON 文本构建。"""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"PluginManifest JSON 解析失败: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("PluginManifest JSON 顶层必须是 object")
        return cls.from_dict(data)

    @classmethod
    def from_file(cls, path: str | Path) -> "PluginManifest":
        """从 manifest 文件（.json）构建。"""
        p = Path(path)
        try:
            return cls.from_json(p.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"PluginManifest 读取失败 {p}: {exc}") from exc


def validate_manifest_dict(data: dict[str, Any]) -> list[str]:
    """对 dict 做 JSON schema 校验，返回错误列表（空 = 合法）。

    实现最小子集校验（不引第三方 jsonschema 库，保持零依赖）：
    - 必填字段 presence
    - name 正则
    - version 正则
    - type 枚举
    - entry 非空
    - additionalProperties 拒绝
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["manifest 顶层必须是 object"]

    # additionalProperties: False
    allowed = set(MANIFEST_SCHEMA["properties"].keys())
    unknown = set(data.keys()) - allowed
    if unknown:
        errors.append(f"未知字段: {sorted(unknown)}")

    # required
    for req in MANIFEST_SCHEMA["required"]:
        if req not in data:
            errors.append(f"缺少必填字段: {req}")

    # name
    name = data.get("name")
    if isinstance(name, str) and not _NAME_RE.match(name):
        errors.append(f"name {name!r} 非法（须 ^[a-z0-9][a-z0-9_-]*$）")

    # version
    version = data.get("version")
    if isinstance(version, str) and not re.match(r"^\d+\.\d+\.\d+$", version):
        errors.append(f"version {version!r} 非法（须 x.y.z）")

    # type
    stype = data.get("type")
    if stype is not None:
        try:
            SeamType(str(stype))
        except ValueError:
            errors.append(f"type {stype!r} 非法（须 ∈ {[t.value for t in SeamType]}）")

    # entry
    entry = data.get("entry")
    if entry is not None and (not isinstance(entry, str) or not entry.strip()):
        errors.append("entry 必须是非空字符串")

    # requires/provides 数组
    for key in ("requires", "provides"):
        val = data.get(key)
        if val is not None and not isinstance(val, list):
            errors.append(f"{key} 必须是数组")

    # stop_layer（铁律 2 停层声明）：声明了就必须是完整合法结构
    sl = data.get("stop_layer")
    if sl is not None:
        if not isinstance(sl, dict):
            errors.append("stop_layer 必须是 object（{kernel, seams, implementations}）")
        else:
            if not isinstance(sl.get("kernel"), str) or not sl.get("kernel"):
                errors.append("stop_layer.kernel 缺失（本插片的内核是什么）")
            # 2026-09-23 兼容两种停层形态（同 PluginManifest.validate）
            sub = sl.get("sub_seams")
            if isinstance(sub, dict):
                seams = sub.get("seams")
                impl = sub.get("implementations")
            else:
                seams = sl.get("seams")
                impl = sl.get("implementations")
            if not isinstance(seams, list) or not seams or not all(isinstance(s, str) for s in seams):
                errors.append("stop_layer.seams 缺失（子插片接缝在哪，非空字符串数组）")
            if not isinstance(impl, int) or isinstance(impl, bool) or impl < 1:
                errors.append("stop_layer.implementations 缺失（当前几个实现，≥1）")

    # enabled 布尔
    enabled = data.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append("enabled 必须是布尔")

    return errors

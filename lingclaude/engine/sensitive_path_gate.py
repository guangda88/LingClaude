"""SensitivePathGate — 安全路径审批中间件（对标 AtomCode sensitive_path.rs）。

设计原则：
- Safe 读取工具（read_file/grep/glob/list_dir）访问凭据路径 → 必须审批
- 否则模型可 silent read 凭据并直传 LLM provider（secret exfiltration）
- 与 Risky 工具不重复：Risky 工具已走审批，此 gate 只补 Safe 工具的漏洞

敏感路径列表（对齐 AtomCode，用路径形状命名避免误触普通文本）：
- /.ssh、id_rsa、id_ed25519 等 SSH 密钥
- /.aws、/.kube、/.gnupg 等云/容器凭据
- .env、.netrc、.git-credentials 等配置文件
- /.atomcode/auth.toml 等 AtomCode 凭据
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable


# 敏感路径标记（子串匹配，路径形状命名）
SENSITIVE_MARKERS: tuple[str, ...] = (
    # SSH 密钥
    "/.ssh",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    # 云/容器凭据
    "/.aws",
    "/.kube",
    "/.gnupg",
    "/.config/gcloud",
    ".netrc",
    ".git-credentials",
    # AtomCode 凭据
    "/.atomcode/auth.toml",
    "/.atomcode/auth/",
    # Docker/npm/pypi
    "/.docker/config",
    ".npmrc",
    ".pypirc",
    # 证书
    ".pem",
    ".p12",
    ".pfx",
    # 其他敏感文件
    ".env",  # 特殊处理：仅匹配路径中的 .env（非文件名 .env）
    "vendor/.env",
    ".aws/credentials",
    ".kube/config",
)

# 特殊规则：.env 仅当在路径中（非文件名）
_ENV_PATH_PATTERN = re.compile(r"(^|/)\.env(/|$)")


def is_sensitive_path(path_str: str) -> bool:
    """检查路径是否引用敏感路径（子串匹配）。

    :param path_str: 路径字符串（来自工具参数）
    :returns: True 如果路径包含敏感标记
    """
    path_lower = path_str.lower()
    for marker in SENSITIVE_MARKERS:
        if marker == ".env":
            # .env 特殊处理：仅匹配路径中的 .env（非文件名）
            if _ENV_PATH_PATTERN.search(path_lower):
                return True
        elif marker in path_lower:
            return True
    return False


def check_sensitive_path(path_str: str) -> tuple[bool, str | None]:
    """检查路径并返回 (is_sensitive, reason)。

    :returns: (True, reason) 如果敏感；(False, None) 如果安全
    """
    if is_sensitive_path(path_str):
        return True, "访问敏感路径，需要审批"
    return False, None


class SensitivePathGate:
    """SensitivePathGate 中间件 — 在工具调用前检查路径。

    用法：
        gate = SensitivePathGate()
        if gate.check("read_file", "/home/user/.ssh/id_rsa"):
            # 触发审批
            pass
    """

    def __init__(
        self,
        approval_callback: Callable[[str, str], Any] | None = None,
    ):
        """
        :param approval_callback: 审批回调 (tool_name, path) -> bool（True=允许，False=拒绝）
        """
        self.approval_callback = approval_callback

    def check(self, tool_name: str, path: str) -> bool:
        """检查工具调用是否访问敏感路径。

        :param tool_name: 工具名称（如 read_file、grep、glob）
        :param path: 路径参数
        :returns: True 如果需要审批/拒绝；False 如果安全
        """
        is_sensitive, reason = check_sensitive_path(path)
        if not is_sensitive:
            return False

        # 有回调 → 触发审批
        if self.approval_callback:
            try:
                decision = self.approval_callback(tool_name, path)
                return not decision  # True = 需要拦截
            except Exception:
                # 审批失败 → fail-closed
                return True

        # 无回调 → 默认拒绝（fail-closed）
        return True

    def get_reason(self, path: str) -> str | None:
        """获取敏感路径拒绝原因。"""
        _, reason = check_sensitive_path(path)
        return reason


# 模块级单例（供工具调用）
_default_gate: SensitivePathGate | None = None


def get_gate() -> SensitivePathGate:
    """获取全局 SensitivePathGate 实例。"""
    global _default_gate
    if _default_gate is None:
        _default_gate = SensitivePathGate()
    return _default_gate


def check_path(path_str: str) -> tuple[bool, str | None]:
    """便捷函数：检查路径是否敏感。"""
    return check_sensitive_path(path_str)

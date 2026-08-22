"""8700 SDK exceptions."""

from __future__ import annotations


class LingClaudeSDKError(Exception):
    """SDK 基础异常。"""


class AuthenticationError(LingClaudeSDKError):
    """401 — API Key 无效或过期。"""


class EngineUnavailableError(LingClaudeSDKError):
    """502/503/504 — 引擎不可用或超时。"""


class ProtocolVersionError(LingClaudeSDKError):
    """426 — 协议版本不兼容。

    :attr service_version: 服务端支持的协议版本
    """

    def __init__(self, service_version: str, *args: object) -> None:
        self.service_version = service_version
        super().__init__(*args)


class PermissionDeniedError(LingClaudeSDKError):
    """403 — 权限拒绝（治理门拦截等）。"""


class NotFoundError(LingClaudeSDKError):
    """404 — 资源不存在。"""


class ValidationError(LingClaudeSDKError):
    """400/422 — 请求参数无效。"""


class TimeoutError(LingClaudeSDKError):
    """请求超时。"""
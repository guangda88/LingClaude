"""MCP OAuth/PKCE 客户端 — P1-3（对标 AtomCode mcp/ 的 OAuth 授权码流）。

实现 MCP 规范（2025-03-26）的 OAuth 授权码流 + PKCE（S256）：
- 授权服务器发现：/.well-known/oauth-authorization-server
- PKCE 验证码/挑战生成（S256）
- 授权 URL 构造 + 授权码交换 + token 刷新

用法：
    oauth = McpOAuthClient(server_url)
    auth_url, verifier = oauth.build_authorization_url()
    # 用户在浏览器完成授权，粘贴回调 code
    token = oauth.exchange_code(code, verifier)
    # 后续请求用 oauth.bearer_token() 加 Authorization 头
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from lingclaude.core.types import Result

logger = logging.getLogger(__name__)


@dataclass
class OAuthToken:
    """OAuth token（含刷新）。"""

    access_token: str
    token_type: str = "Bearer"
    refresh_token: str | None = None
    expires_in: int = 0
    scope: str = ""
    issued_at: float = field(default_factory=lambda: __import__("time").time())

    @property
    def is_expired(self) -> bool:
        import time
        return self.expires_in > 0 and (time.time() - self.issued_at) >= self.expires_in


class McpOAuthClient:
    """MCP OAuth 客户端 — 授权码流 + PKCE（S256）。"""

    def __init__(self, server_url: str) -> None:
        self._server_url = server_url.rstrip("/")
        self._auth_metadata: dict[str, Any] | None = None
        self._token: OAuthToken | None = None

    # ----- 授权服务器发现 -----

    def discover(self) -> Result[dict[str, Any]]:
        """发现 OAuth 授权服务器元数据（RFC 8414 / MCP OAuth）。

        尝试 /.well-known/oauth-authorization-server 与 /.well-known/openid-configuration。
        """
        candidates = [
            f"{self._server_url}/.well-known/oauth-authorization-server",
            f"{self._server_url}/.well-known/openid-configuration",
        ]
        for url in candidates:
            try:
                req = urllib.request.Request(url, headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — URL 来自受信配置
                    meta = json.loads(resp.read().decode("utf-8", errors="replace"))
                if "authorization_endpoint" in meta or "authorization_endpoint" in meta:
                    self._auth_metadata = meta
                    return Result.ok(meta)
            except Exception as e:  # noqa: BLE001 — 发现失败尝试下一个候选
                logger.debug("OAuth discovery failed for %s: %s", url, e)
        return Result.fail("OAuth authorization server metadata not found", code="OAUTH_NO_METADATA")

    # ----- PKCE -----

    @staticmethod
    def _generate_verifier() -> str:
        """生成 PKCE code_verifier（43-128 位，RFC 7636）。"""
        return secrets.token_urlsafe(64)[:64]

    @staticmethod
    def _challenge(verifier: str) -> str:
        """生成 PKCE code_challenge（S256）。"""
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    # ----- 授权码流 -----

    def build_authorization_url(self, redirect_uri: str = "http://localhost:3000/callback") -> Result[tuple[str, str]]:
        """构造授权 URL，返回 (auth_url, code_verifier)。"""
        if self._auth_metadata is None:
            disc = self.discover()
            if disc.is_error:
                return disc
        meta = self._auth_metadata or {}
        auth_endpoint = meta.get("authorization_endpoint")
        if not auth_endpoint:
            return Result.fail("authorization_endpoint missing in metadata", code="OAUTH_NO_AUTH_ENDPOINT")
        verifier = self._generate_verifier()
        challenge = self._challenge(verifier)
        params = {
            "response_type": "code",
            "client_id": meta.get("client_id", "mcp-client"),
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": meta.get("scopes_supported", ["mcp"]),
        }
        sep = "&" if "?" in auth_endpoint else "?"
        auth_url = f"{auth_endpoint}{sep}{urllib.parse.urlencode(params, doseq=True)}"
        return Result.ok((auth_url, verifier))

    def exchange_code(
        self, code: str, verifier: str, redirect_uri: str = "http://localhost:3000/callback",
    ) -> Result[OAuthToken]:
        """用授权码交换 token（PKCE 验证）。"""
        meta = self._auth_metadata or {}
        token_endpoint = meta.get("token_endpoint")
        if not token_endpoint:
            return Result.fail("token_endpoint missing in metadata", code="OAUTH_NO_TOKEN_ENDPOINT")
        body = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": meta.get("client_id", "mcp-client"),
            "code_verifier": verifier,
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                token_endpoint, data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 — URL 来自受信配置
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001 — token 交换失败
            return Result.fail(f"Token exchange failed: {e}", code="OAUTH_TOKEN_EXCHANGE_FAILED")
        if "access_token" not in data:
            return Result.fail(f"Token exchange error: {data.get('error', 'unknown')}", code="OAUTH_TOKEN_ERROR")
        self._token = OAuthToken(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            refresh_token=data.get("refresh_token"),
            expires_in=int(data.get("expires_in", 0)),
            scope=data.get("scope", ""),
        )
        return Result.ok(self._token)

    def refresh(self) -> Result[OAuthToken]:
        """刷新 token（refresh_token 可用时）。"""
        if self._token is None or not self._token.refresh_token:
            return Result.fail("No refresh_token available", code="OAUTH_NO_REFRESH")
        meta = self._auth_metadata or {}
        token_endpoint = meta.get("token_endpoint")
        if not token_endpoint:
            return Result.fail("token_endpoint missing", code="OAUTH_NO_TOKEN_ENDPOINT")
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": self._token.refresh_token,
            "client_id": meta.get("client_id", "mcp-client"),
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                token_endpoint, data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 — URL 来自受信配置
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001 — 刷新失败
            return Result.fail(f"Token refresh failed: {e}", code="OAUTH_REFRESH_FAILED")
        if "access_token" not in data:
            return Result.fail(f"Refresh error: {data.get('error', 'unknown')}", code="OAUTH_REFRESH_ERROR")
        self._token = OAuthToken(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            refresh_token=data.get("refresh_token", self._token.refresh_token),
            expires_in=int(data.get("expires_in", 0)),
            scope=data.get("scope", self._token.scope),
        )
        return Result.ok(self._token)

    def bearer_token(self) -> str | None:
        """返回当前有效 Bearer token（过期自动刷新，失败返回 None）。"""
        if self._token is None:
            return None
        if self._token.is_expired and self._token.refresh_token:
            self.refresh()
        return self._token.access_token

    def auth_headers(self) -> dict[str, str]:
        """返回 Authorization 请求头（无 token 返回空 dict）。"""
        token = self.bearer_token()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

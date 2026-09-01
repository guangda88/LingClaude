"""灵克 LingBus 消息签名 helper — RFC v0.2 §Phase A1 A1-2。

封装 lingmessage.signing.sign_message + ~/.lingmessage/signing_key 读取,
供 bus_responder._send_reply 给 reply metadata.signature 字段写入签名,
配合 A0-3(_validate_caller 测试覆盖)使用。

设计要点:
- 复用 lingmessage.signing 完整链路(构造 minimal Message → sign_message),
  与灵信 verify_signature(Message, sig, secret) 验证端兼容
- signing_key 缺失时 graceful 降级返回 None(对应 metadata 无 signature 字段,
  与 lacp/manifest.py:314 "无签名=旧插件,允许" 语义一致)
- helper 是纯函数 + 工厂类,无单例无副作用,易测试
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_KEY_PATH = Path.home() / ".lingmessage" / "signing_key"
_SIGNER_VERSION = "1"


@dataclass(frozen=True)
class SignedReply:
    """签名结果,供 bus_responder 写入 post_reply metadata。

    Attributes:
        signature: HMAC-SHA256 十六进制签名;None 表示降级(无签名)。
        signer_version: 签名 schema 版本;None 表示降级。
        key_fingerprint: 密钥指纹(前 12 字符 SHA-256),便于审计追踪;
            None 表示降级。
    """

    signature: str | None
    signer_version: str | None
    key_fingerprint: str | None

    def to_metadata(self) -> dict[str, str]:
        """转 post_reply.metadata 字段。降级时返回空 dict。"""
        if self.signature is None:
            return {}
        return {
            "signature": self.signature,
            "signer_version": self.signer_version or _SIGNER_VERSION,
            "key_fingerprint": self.key_fingerprint or "",
        }


class MessageSigner:
    """LingBus message signer helper — A1-2 交付物。

    Usage:
        signer = MessageSigner()  # 默认读 ~/.lingmessage/signing_key
        signed = signer.sign_reply(
            thread_id="abc",
            sender="lingclaude",
            recipient="lingflow_plus",
            body="reply body",
        )
        metadata = signed.to_metadata()
        # 缺失 key 时 metadata 为空 dict,bus.post_reply 走无签名路径
    """

    def __init__(self, key_path: Path | None = None) -> None:
        self._key_path = key_path or _DEFAULT_KEY_PATH

    def _load_secret(self) -> str | None:
        """读 signing_key 文件;缺失/不可读返回 None。"""
        if not self._key_path.exists():
            logger.debug("signing_key not found at %s (degraded mode)", self._key_path)
            return None
        try:
            return self._key_path.read_text(encoding="utf-8").strip()
        except OSError as e:
            logger.warning("signing_key read failed at %s: %s", self._key_path, e)
            return None

    @staticmethod
    def _fingerprint(secret_key: str) -> str:
        """SHA-256(secret_key) 前 12 字符,审计追踪用。"""
        import hashlib
        return hashlib.sha256(secret_key.encode("utf-8")).hexdigest()[:12]

    def sign_reply(
        self,
        *,
        thread_id: str,
        sender: str,
        recipient: str,
        body: str,
        subject: str = "",
        message_type: str = "reply",
    ) -> SignedReply:
        """签一条 reply;secret_key 缺失时返回降级 SignedReply(signature=None)。"""
        secret_key = self._load_secret()
        if secret_key is None:
            return SignedReply(None, None, None)

        try:
            from lingmessage.signing import sign_message
            from lingmessage.types import (
                Channel,
                DeliveryStatus,
                LingIdentity,
                Message,
                MessageType,
                SourceType,
            )

            def _safe_identity(raw: str) -> LingIdentity:
                """sender/recipient 不在 LingIdentity 枚举时降级 ALL(签名仍生效,验证端按 .value 字段读 raw)。"""
                try:
                    return LingIdentity(raw)
                except ValueError:
                    return LingIdentity.ALL

            msg = Message(
                message_id="",
                thread_id=thread_id,
                sender=_safe_identity(sender),
                recipient=_safe_identity(recipient),
                message_type=MessageType(message_type),
                channel=Channel.GOVERNANCE,
                subject=subject,
                body=body,
                timestamp="",
                reply_to="",
                metadata=(),
                source_type=SourceType.INFERRED,
                source_trace="",
                delivery_status=DeliveryStatus.SENT,
                delivered_at="",
            )
            signature = sign_message(msg, secret_key)
            return SignedReply(signature, _SIGNER_VERSION, self._fingerprint(secret_key))
        except Exception as e:
            # 导入失败 / 构造失败 / 签名失败 → 降级,与缺失 key 等价
            logger.warning("sign_reply failed (degraded): %s", e)
            return SignedReply(None, None, None)


def create_signer() -> MessageSigner:
    """工厂函数,与 bus_responder.create_responder() 风格一致。"""
    return MessageSigner()
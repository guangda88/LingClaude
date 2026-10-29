"""Provider plugin base for free-pool sync."""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Route:
    key: str
    base_url: str
    api_key_env: str
    provider: str
    tier: str
    default_off: bool
    rpm: int
    max_concurrent: int
    capabilities: Dict[str, Any]
    note: str = ""
    sync_source: Optional[str] = None
    upstream_model: Optional[str] = None


class ProviderPlugin:
    name: str = ""
    # tRPC/cookie 平台等无 /v1/models 接口的厂商（同步器据此跳过误下架）
    trpc_platform: bool = False

    def fetch_models(self, api_key: str) -> Dict[str, Any]:
        raise NotImplementedError

    def is_free(self, model_id: str, raw: Any) -> bool:
        return True

    def build_route(self, model_id: str, raw: Any) -> Optional[Route]:
        raise NotImplementedError

    def platform_whitelist(self) -> Optional[set]:
        return None

    def is_platform_compatible(self) -> bool:
        # 是否可参与“平台列表消失→下架”判定
        return not self.trpc_platform

    def probe(self, api_key: str = "") -> Dict[str, Any]:
        """轻量可用性探测（类比 DSH Our Free Model）。默认返回 ok=True。"""
        return {"ok": True, "latency_ms": 0, "error": None}

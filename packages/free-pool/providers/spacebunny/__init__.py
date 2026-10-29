"""SpaceBunny provider plugin template（借鉴 DSH Our Free Model 思路）"""
import json
import urllib.request
from typing import Any, Dict, Optional

try:
    from ..base import ProviderPlugin, Route
except Exception:
    from providers.base import ProviderPlugin, Route


class SpaceBunnyProvider(ProviderPlugin):
    name = "spacebunny"
    trpc_platform = False

    def __init__(self):
        self.endpoint = ""  # 待补充实际 /v1/models 或发现接口
        self.base_url = ""
        self.api_key_env = ""
        self.rpm = 30
        self.max_concurrent = 3

    def fetch_models(self, api_key: str) -> Dict[str, Any]:
        if not self.endpoint:
            return {}
        headers = {"User-Agent": "proxy3-sync"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(self.endpoint, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("data", data) if isinstance(data, dict) else data
        out = {}
        for raw in items or []:
            mid = raw.get("id", "")
            if mid:
                out[mid] = raw
        return out

    def is_free(self, model_id: str, raw: Any) -> bool:
        if isinstance(raw, dict) and raw.get("free") is True:
            return True
        return model_id.endswith(("-free", ":free")) or "free" in str(model_id).lower()

    def build_route(self, model_id: str, raw: Any) -> Optional[Route]:
        if not self.base_url:
            return None
        return Route(
            key=f"{model_id}@{self.name}",
            base_url=self.base_url,
            api_key_env=self.api_key_env,
            provider=self.name,
            tier="free",
            default_off=False,
            rpm=self.rpm,
            max_concurrent=self.max_concurrent,
            capabilities={"vision": False, "tool_call": True, "thinking": False,
                          "code": False, "embedding": False, "multilingual": True},
            note="sync_free_models auto-added (spacebunny template)",
            sync_source=self.name,
        )

    def probe(self, api_key: str = "") -> Dict[str, Any]:
        try:
            m = self.fetch_models(api_key)
            return {"ok": True, "count": len(m), "configured": bool(self.endpoint and self.base_url)}
        except Exception as e:
            return {"ok": False, "error": str(e), "configured": bool(self.endpoint and self.base_url)}


def get_plugin() -> ProviderPlugin:
    return SpaceBunnyProvider()

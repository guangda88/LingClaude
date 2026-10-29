"""Provider plugin template.

复制此目录到 providers/<yourprovider>/ 并修改。
"""
import json
import urllib.request
from typing import Any, Dict, Optional

try:
    from ..base import ProviderPlugin, Route
except Exception:
    from providers.base import ProviderPlugin, Route


class TemplateProvider(ProviderPlugin):
    name = "yourprovider"
    trpc_platform = False

    def __init__(self):
        self.endpoint = "https://example.com/v1/models"
        self.base_url = "https://example.com/v1/chat/completions"
        self.api_key_env = "YOURPROVIDER_API_KEY"  # 留空表示匿名可用
        self.rpm = 30
        self.max_concurrent = 3

    def fetch_models(self, api_key: str) -> Dict[str, Any]:
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
        # 按实际情况判断：有的用 :free 后缀，有的看 pricing/cost 字段
        if isinstance(raw, dict):
            pricing = raw.get("pricing") or {}
            if pricing.get("prompt") == "0" or pricing.get("input") == 0:
                return True
        return model_id.endswith((":free", "-free"))

    def build_route(self, model_id: str, raw: Any) -> Optional[Route]:
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
            note="sync_free_models auto-added",
            sync_source=self.name,
        )

    def probe(self, api_key: str = "") -> Dict[str, Any]:
        try:
            m = self.fetch_models(api_key)
            return {"ok": True, "count": len(m)}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def get_plugin() -> ProviderPlugin:
    return TemplateProvider()

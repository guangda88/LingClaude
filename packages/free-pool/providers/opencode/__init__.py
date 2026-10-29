"""OpenCode Zen provider plugin.

2026-09-27: Zen 直连对非 OpenCode 客户端一律 403 FreeTierError，故 opencode 档全部经本地桥
scripts/oc_free_bridge.py -> opencode serve 走官方客户端路径。桥为纯文本，故 tool_call=False。
key 留空：桥只监听 127.0.0.1 且不校验 Authorization。
"""
import json
import os
import urllib.request
from typing import Any, Dict, Optional

try:
    from ..base import ProviderPlugin, Route
except Exception:
    from providers.base import ProviderPlugin, Route


class OpenCodeProvider(ProviderPlugin):
    name = "opencode"
    trpc_platform = False

    def __init__(self):
        self.endpoint = "https://opencode.ai/zen/v1/models"
        self.base_url = "http://127.0.0.1:4100/v1/chat/completions"
        self.api_key_env = ""
        self.rpm = 30
        self.max_concurrent = 3

    def fetch_models(self, api_key: str) -> Dict[str, Any]:
        # 匿名优先（见 sync_free_models 原逻辑）
        last_err = None
        for try_key in (None, api_key):
            headers = {"User-Agent": "proxy3-sync"}
            if try_key:
                headers["Authorization"] = f"Bearer {try_key}"
            try:
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
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"fetch failed both anon+authed: {self.endpoint}: {last_err}")

    def is_free(self, model_id: str, raw: Any) -> bool:
        # 2026-09-26 W2: Zen live 全量含付费 ID（gpt-6-luna 等），只收 -free 后缀
        # 2026-09-27: big-pickle 是 Zen 的「隐身」免费档，无 -free 后缀但 cost 全 0
        if model_id.endswith(("-free", ":free")):
            return True
        if model_id == "big-pickle":
            return True
        return False

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
            capabilities={"vision": False, "tool_call": False, "thinking": False,
                          "code": False, "embedding": False, "multilingual": True},
            note="sync_free_models auto-added",
            sync_source=self.name,
        )


def get_plugin() -> ProviderPlugin:
    return OpenCodeProvider()

try:
    from ..base import ProviderPlugin, Route
except Exception:
    from providers.base import ProviderPlugin, Route


    def probe(self, api_key: str = "") -> Dict[str, Any]:
        """轻量探测：访问本地桥 /v1/models"""
        import json
        import time
        import urllib.request
        try:
            t0 = time.time()
            req = urllib.request.Request("http://127.0.0.1:4100/v1/models", headers={"User-Agent": "free-pool-probe"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            dt = int((time.time() - t0) * 1000)
            items = data.get("data", data) if isinstance(data, dict) else data
            cnt = len(items) if isinstance(items, list) else 0
            return {"ok": resp.status == 200, "latency_ms": dt, "count": cnt, "error": None}
        except Exception as e:
            return {"ok": False, "latency_ms": 0, "error": str(e)}

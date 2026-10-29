"""免费池 provider 可用性看板（类比 DSH Our Free Model Token 看板）"""
import json
import os
from typing import Dict, Any

try:
    from providers.loader import load_plugins
except Exception:
    from ..loader import load_plugins


def get_provider_health() -> Dict[str, Any]:
    plugins = load_plugins()
    out = {}
    for name, p in plugins.items():
        try:
            if hasattr(p, "probe"):
                res = p.probe(os.environ.get(getattr(p, "api_key_env", "") or "", ""))
            else:
                res = {"ok": True}
            out[name] = res
        except Exception as e:
            out[name] = {"ok": False, "error": str(e)}
    return out


if __name__ == "__main__":
    print(json.dumps(get_provider_health(), ensure_ascii=False, indent=2))

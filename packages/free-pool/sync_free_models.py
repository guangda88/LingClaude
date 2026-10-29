#!/usr/bin/env python3
"""免费池定期同步 — 平台 /v1/models 发现新免费模型 + 失效模型下架"""
import json
import os
import shutil
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

ROUTES_FILE = os.environ.get("FREE_POOL_ROUTES", os.path.join(os.getcwd(), "routes.json"))

try:
    from providers.loader import load_plugins
    _PLUGINS = load_plugins()
except Exception:
    _PLUGINS = {}

PLATFORMS = {
    "openrouter": ("https://openrouter.ai/api/v1/models", "OPENROUTER_API_KEY",
                   "https://openrouter.ai/api/v1/chat/completions", 20, 5),
    "kilo": ("https://api.kilo.ai/api/gateway/v1/models", "KILO_API_KEY",
             "https://api.kilo.ai/api/gateway/v1/chat/completions", 10, 2),
    "kilo-gateway": ("https://api.kilo.ai/api/gateway/models", "KILO_GATEWAY_API_KEY",
                     "https://api.kilo.ai/api/gateway/chat/completions", 30, 3),
    "nvidia_nim": ("https://integrate.api.nvidia.com/v1/models", "NVIDIA_NIM_API_KEY",
                   "https://integrate.api.nvidia.com/v1/chat/completions", 35, 5),
    "modelscope": ("https://api-inference.modelscope.cn/v1/models", "MODELSCOPE_API_KEY",
                   "https://api-inference.modelscope.cn/v1/chat/completions", 100, 5),
    "zenmux": ("https://zenmux.ai/api/v1/models", "ZENMUX_API_KEY",
               "https://zenmux.ai/api/v1/chat/completions", 20, 2),
    "opencode": ("https://opencode.ai/zen/v1/models", "",
                 "http://127.0.0.1:4100/v1/chat/completions", 30, 3),
    "sensenova": ("https://token.sensenova.cn/v1/models", "SENSENOVA_API_KEY",
                  "https://token.sensenova.cn/v1/chat/completions", 30, 3),
    "cline": ("https://api.cline.bot/api/v1/models", "CLINE_API_KEY",
              "https://api.cline.bot/api/v1/chat/completions", 20, 3),
    "empero": ("https://free.empero.org/v1/models", "",
               "https://free.empero.org/v1/chat/completions", 5, 2),
    "codearts": ("http://127.0.0.1:4110/v1/models", "",
                 "http://127.0.0.1:4110/v1/chat/completions", 8, 2),
}

TRPC_PLATFORMS = {"b_ai"}


def _platform_supported(platform, live_ids):
    if platform in _PLUGINS and hasattr(_PLUGINS[platform], "is_platform_compatible"):
        try:
            return _PLUGINS[platform].is_platform_compatible()
        except Exception:
            pass
    return platform not in TRPC_PLATFORMS


PLATFORM_FREE_WHITELIST = {
    "cline": {
        "z-ai/glm-5.3-flash", "deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4-flash",
        "xiaomi/mimo-v2.5", "xiaomi/mimo-v2.5-pro", "inclusionai/ling-3.0-flash-vl:free",
        "inclusionai/ling-3.0-flash-sante:free", "inclusionai/ling-3.0-flash-fin:free",
    },
    "empero": {
        "z-ai/glm-5.3-flash", "qwen/qwen3.8-flash",
    },
}


def _is_free(platform, model_id, raw):
    if platform in _PLUGINS and hasattr(_PLUGINS[platform], "is_free"):
        try:
            return _PLUGINS[platform].is_free(model_id, raw)
        except Exception:
            pass
    if platform in PLATFORM_FREE_WHITELIST:
        return model_id in PLATFORM_FREE_WHITELIST[platform]
    if platform == "openrouter":
        return model_id.endswith(":free")
    if platform in ("kilo", "kilo-gateway"):
        return "(free)" in model_id or raw.get("is_free") is True or "free" in str(raw.get("tags", [])).lower()
    if platform == "opencode":
        return model_id.endswith(("-free", ":free")) or model_id == "big-pickle"
    if platform == "codearts":
        return True
    return False


def _fetch_models(endpoint, api_key, platform=None):
    if platform and platform in _PLUGINS and hasattr(_PLUGINS[platform], "fetch_models"):
        try:
            return _PLUGINS[platform].fetch_models(api_key)
        except Exception as e:
            raise RuntimeError(f"fetch failed via plugin {platform}: {e}")
    last_err = None
    for try_key in (None, api_key):
        headers = {"User-Agent": "proxy3-sync"}
        if try_key:
            headers["Authorization"] = f"Bearer {try_key}"
        try:
            req = urllib.request.Request(endpoint, headers=headers)
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
    raise RuntimeError(f"fetch failed both anon+authed: {endpoint}: {last_err}")


def _sync_one_platform(platform, cfg, routes, today):
    endpoint, key_env, base_url, rpm, mc = cfg
    api_key = os.environ.get(key_env, "")
    added, retired, errors = [], [], []
    try:
        live = _fetch_models(endpoint, api_key, platform=platform)
        print(f"[sync] {platform}: got {len(live)}", flush=True)
    except Exception as e:
        return platform, added, retired, [f"{platform}: fetch failed: {e}"], {}

    live_ids = set(live)
    existing = {k: r for k, r in routes.items()
                if r.get("provider") == platform and r.get("tier") == "free"}

    if not live_ids and existing:
        return platform, added, retired, [f"{platform}: empty model list with {len(existing)} existing routes, treated as fetch failure"], {}

    updates = {}
    for mid, raw in live.items():
        if not _is_free(platform, mid, raw):
            continue
        if mid in existing or f"{mid}@{platform}" in routes:
            continue
        if platform in _PLUGINS and hasattr(_PLUGINS[platform], "build_route"):
            try:
                r = _PLUGINS[platform].build_route(mid, raw)
                if r is not None:
                    route_obj = {
                        "base_url": r.base_url,
                        "api_key_env": r.api_key_env,
                        "provider": r.provider,
                        "tier": r.tier,
                        "default_off": r.default_off,
                        "rpm": r.rpm,
                        "max_concurrent": r.max_concurrent,
                        "capabilities": r.capabilities,
                        "_note": getattr(r, "note", None) or f"sync_free_models {today} auto-added",
                        "_sync_source": getattr(r, "sync_source", None) or platform,
                    }
                    if getattr(r, "upstream_model", None):
                        route_obj["upstream_model"] = r.upstream_model
                    updates[r.key] = route_obj
                    added.append(r.key)
                    continue
            except Exception as e:
                errors.append(f"{platform}:{mid}: build_route failed: {e}")
                continue
        route_obj = {
            "base_url": base_url,
            "api_key_env": key_env,
            "provider": platform,
            "tier": "free",
            "default_off": False,
            "rpm": rpm,
            "max_concurrent": mc,
            "capabilities": {"vision": False, "tool_call": platform not in ("opencode", "codearts"), "thinking": False,
                             "code": False, "embedding": False, "multilingual": True},
            "_note": f"sync_free_models {today} auto-added",
            "_sync_source": platform,
        }
        key = f"{mid}@{platform}"
        updates[key] = route_obj
        added.append(key)

    for k, r in existing.items():
        mid = k.rsplit("@", 1)[0]
        bare = mid.rsplit("/", 1)[-1]
        in_list = mid in live_ids or bare in live_ids
        if not in_list:
            in_list = any(l.rsplit("/", 1)[-1] == bare for l in live_ids)
        if not in_list and not r.get("default_off") and _platform_supported(platform, live_ids):
            updates[k] = dict(r)
            updates[k]["default_off"] = True
            updates[k]["off_reason"] = f"sync:{today} gone from platform list"
            retired.append(k)

    return platform, added, retired, errors, updates


def main():
    apply = "--apply" in sys.argv
    fast = "--fast" in sys.argv
    with open(ROUTES_FILE) as f:
        routes = json.load(f)

    today = time.strftime("%Y%m%d")
    added_all, retired_all, errors_all = [], [], []

    items = list(PLATFORMS.items())
    if fast:
        items = [(p, c) for p, c in items if p not in ("zenmux", "sensenova", "empero")]

    max_workers = min(6, len(items)) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_sync_one_platform, p, cfg, routes, today): p for p, cfg in items}
        for fut in as_completed(futs, timeout=180):
            p = futs[fut]
            try:
                plat, added, retired, errors, updates = fut.result(timeout=60)
                added_all += added
                retired_all += retired
                errors_all += errors
                for k, v in updates.items():
                    routes[k] = v
            except TimeoutError:
                errors_all.append(f"{p}: timeout")
                print(f"[sync] {p}: timeout", flush=True)
            except Exception as e:
                errors_all.append(f"{p}: {e}")
                print(f"[sync] {p}: error {e}", flush=True)

    print(f"added={len(added_all)} retired={len(retired_all)} errors={len(errors_all)}")
    for a in added_all[:25]:
        print("  +", a)
    for r in retired_all[:25]:
        print("  -", r)
    for e in errors_all:
        print("  !", e)

    if apply and (added_all or retired_all):
        bak = ROUTES_FILE + f".bak.freesync_{today}"
        shutil.copy2(ROUTES_FILE, bak)
        with open(ROUTES_FILE, "w") as f:
            json.dump(routes, f, ensure_ascii=False, indent=1)
        print(f"applied. backup: {bak}")
    elif not apply:
        print("(dry-run, use --apply to write)")


if __name__ == "__main__":
    main()

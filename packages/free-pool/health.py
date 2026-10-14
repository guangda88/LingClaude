"""健康状态管理插片 — cooling/blocked 管理 + admin 重置"""
import os, json, time, threading, logging

ENABLED = os.environ.get("PROXY3_NO_HEALTH", "") != "1"
PERSIST_PATH = os.environ.get("PROXY3_HEALTH_PATH", os.path.expanduser("~/.llm_proxy/v3_health_state.json"))
LOG = logging.getLogger("proxy3.health")

_lock = threading.Lock()
_entries = {}  # "provider@model" -> {"status": "ok"|"cooling"|"blocked", "until": float, "fail_count": int}


def _load():
    global _entries
    try:
        with open(PERSIST_PATH) as f:
            data = json.load(f)
            for k, v in data.get("entries", {}).items():
                until_str = v.get("cool_until", "")
                until = 0
                if until_str:
                    try:
                        until = time.mktime(time.strptime(until_str, "%Y-%m-%dT%H:%M:%S"))
                    except (ValueError, TypeError) as e:
                        LOG.warning("health until parse failed for %s: %s", k, e)
                        until = 0
                _entries[k] = {
                    "status": v.get("status", 0),
                    "until": until,
                    "fail_count": v.get("fail_count", 0),
                }
    except Exception as e:
        LOG.warning("health load failed (%s): %s", PERSIST_PATH, e)
        _entries = {}


def _save():
    try:
        os.makedirs(os.path.dirname(PERSIST_PATH), exist_ok=True)
        data = {"version": 1, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                "entries": {}}
        for k, v in _entries.items():
            data["entries"][k] = {
                "status": v["status"],
                "fail_count": v["fail_count"],
                "cool_until": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(v["until"])) if v["until"] else None,
            }
        tmp = PERSIST_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, PERSIST_PATH)
    except Exception as e:
        LOG.warning("health persist failed: %s", e)


def pre_forward(req_json, route):
    """检查 provider@model 是否在 cooling/blocked 状态"""
    if not ENABLED or not route:
        return None

    provider = route.get("provider", "")
    model = req_json.get("model", "")
    key = f"{provider}@{model}"
    now = time.time()

    with _lock:
        entry = _entries.get(key)
        if entry and entry["until"] and now < entry["until"]:
            statuses = {1: "degraded", 2: "cooling", 3: "blocked", 4: "suspended"}
            s = statuses.get(entry["status"], "unknown")
            return (503, {"error": f"provider {s}", "provider": provider, "model": model})
        # 冷却过期，自动恢复
        if entry and entry["until"] and now >= entry["until"]:
            del _entries[key]
            _save()
    return None


def on_error(req_json, route, error_type=None, error_data=None, action=None):
    """处理错误，重置健康状态。

    兼容三种调用形式：
      on_error(req_json, route, error_type, error_data)        # main.py via run_hooks
      on_error(provider, model, action="degrade")              # quality.py / prober.py kw
      on_error(provider, model, "degrade")                     # 位置式
    """
    # 内部调用形式: (provider, model, action[=...])
    if isinstance(req_json, str) and isinstance(route, str):
        provider, model = req_json, route
        if action is None:
            action = error_type or "degrade"
    else:
        provider = route.get("provider", "") if route else (error_data.get("provider", "") if error_data else "")
        model = req_json.get("model", "") if isinstance(req_json, dict) else ""
        if action is None:
            # 从 error_type 推断降级动作: stream_error/http_5xx → cool, 其他 → degrade
            err = str(error_type) if error_type else ""
            # 智能推断: 仅真上游不可用(5xx 服务端错误)才 cool,
            # 客户端侧错误(stream_error/4xx context 超限/timeout/brokenpipe)只 degrade 不阻塞
            # err 格式: "stream_error" / "http_{status}" / "upstream_200_with_error:..."
            if err.startswith("http_") and len(err) >= 6:
                try:
                    status_code = int(err[5:])
                except ValueError:
                    status_code = 0
                # 5xx 服务端错误 → cool; 4xx 客户端错误 → degrade (不阻塞套餐)
                action = "cool" if status_code >= 500 else "degrade"
            elif err.startswith("upstream_200_with_error"):
                # 上游 200 但 body 含 error → degrade (非真上游不可用)
                action = "degrade"
            else:
                # stream_error 等其他 → degrade (避免套餐被客户端侧错误续期冷却)
                action = "degrade"
    if not ENABLED:
        return
    # 套餐路由(tier=subscription) 对客户端侧错误只 degrade 不 cool,
    # 避免套餐被 context 超限/timeout/brokenpipe 反复续期 300s 冷却导致整套餐不可用
    route_tier = route.get("tier", "") if route else ""
    if route_tier == "subscription" and action == "cool":
        action = "degrade"
    durations = {"cool": 300, "block": 600, "suspend": 3600, "degrade": 0}
    duration = durations.get(action, 300)
    key = f"{provider}@{model}"
    now = time.time()
    with _lock:
        entry = _entries.get(key, {"status": 0, "fail_count": 0, "until": 0})
        entry["fail_count"] += 1
        status_map = {"cool": 2, "block": 3, "suspend": 4, "degrade": 1}
        entry["status"] = status_map.get(action, 2)
        if duration > 0:
            entry["until"] = now + duration
        _entries[key] = entry
        _save()


def on_success(provider, model):
    """成功时恢复健康状态"""
    key = f"{provider}@{model}"
    with _lock:
        entry = _entries.get(key)
        if entry and entry["status"] in (1, 2):  # degraded or cooling
            del _entries[key]
            _save()


def admin_reset(provider, model):
    """手动清除冷却"""
    key = f"{provider}@{model}"
    with _lock:
        if key in _entries:
            del _entries[key]
            _save()


def get_snapshot():
    """返回健康状态快照，供 ranker 及其他模块查询。
    返回: {"provider@model": {"status": str, "until": float, "fail_count": int}, ...}
    """
    with _lock:
        return dict(_entries)


# 启动时加载
_load()

"""免费额度池插片 — free/* 虚拟模型名统一调度（P1，free_pool_proposal.md v1.1）

对外接口（OpenAI 兼容）：
  free/auto   — 全池自动选（健康分降序）
  free/code   — 编码向（必须 tool_call+code 能力）

已拍板决策（2026-09-14 族长）：
  1. 内置插件（本文件），不拆独立 service
  2. 敏感任务默认禁入 + opt-in：free/* 默认 403，需 x-free-pool-optin: true header
     （或 body _free_pool_optin 字段）；quality_precheck 敏感规则优先级更高
  3. Datalog cost=0，不加 cost_source；区分靠 trace_id 前缀 fp- + _proxy_meta.free_pool

v1.1 反馈收敛（LingBus 提案 thread 7979c470 rowid 186730，lingclaude 消费端反馈；
另有 18ac3abe mailbox 监督计划 thread 跟踪实施状态，二者用途不同）：
  - 候选过滤做实：health blocked/cooling + 凭证缺失（api_key_env 非空但 env 未设）剔除
  - 错误带明确 type/reason：free_pool_optin_required / free_pool_empty（附排除摘要）
    / 上游 429 记 last_error_type="rate_limited"、预算剔除记 "budget_exhausted"
  - _proxy_meta.free_pool 回注 selected + budget_left（消费端审计可追溯）

启用: PROXY3_FREE_POOL=1（默认关闭，rollout 安全）
复用: route_selector._get_routes / route_is_selectable（候选与策略）、
      health（cooling/blocked 过滤）、ratelimit/circuit_breaker（pre_forward 自动生效）。
fallback: 直接注入 req_json["_fallback_routes"]，route_selector 对已存在的链不覆盖。
健康分持久化: ~/.llm_proxy/free_pool_state.json（进程重启不清零）。
"""
import json
import os
import threading
import time
from collections import deque

ENABLED = os.environ.get("PROXY3_FREE_POOL", "") == "1"

from route_selector import _get_routes, route_is_selectable  # noqa: E402

# ---- 桶定义：bucket -> 过滤条件（capabilities / min_context / fast 启发式）----
# caps: capabilities 字段必须全为 True；min_context: context_window >= 该值；
# fast: 低延迟小模型（context 小 + 非长文本旗舰），用 max_context 上限 + 名称启发式
BUCKETS = {
    "auto": {"caps": {}, "min_context": 0},
    "code": {"caps": {"tool_call": True, "code": True}, "min_context": 0},
    "long": {"caps": {}, "min_context": 200_000},
    "vision": {"caps": {"vision": True}, "min_context": 0},
    "fast": {"caps": {}, "max_context": 65_536},  # 小窗口 = 小模型 = 低延迟
}

FAST_NAME_HINTS = ("nano", "mini", "lite", "small", "flash", "gemma", "lightning", "-b", "1b", "3b", "7b", "8b", "12b", "14b", "26b", "32b", "35b")

# 旗舰（能力最强档）名称提示——与 FAST_NAME_HINTS 反向，命中加分
FLAGSHIP_NAME_HINTS = ("ultra", "max", "pro", "opus", "reasoner", "thinking", "next", "coder", "large", "grand", "titan")

# 匿名（免 key）平台日预算 tokens；注册类默认 500K；路由可带 daily_token_budget 覆盖
ANON_PROVIDERS = {"llm7", "zenmux", "tokenrouter", "empero", "pollinations", "api-airforce"}
DEFAULT_BUDGET_ANON = 50_000
DEFAULT_BUDGET_REGISTERED = 500_000

OPTIN_HEADER = "x-free-pool-optin"
TRACE_PREFIX = "fp-"

STATE_PATH = os.path.expanduser("~/.llm_proxy/free_pool_state.json")

# ---- env 热重载（不重启拿新 token）----
# 监听 .ling_keys.env 的 mtime，变了就重读 KEY=value / export KEY=value 注入 os.environ。
# 只增不删（安全：不清空已有 env）。这样 sync_cline_token 写新 token 后，
# proxy3 下个请求自动看到新 key，无需重启。
ENV_FILE = os.path.expanduser("~/.ling_keys.env")
_env_mtime = 0.0
_env_lock = threading.Lock()


def _reload_env_if_changed():
    """检查 env 文件 mtime，变了就重读 KEY=value 注入 os.environ。线程安全（调用方无需持锁）。

    返回是否发生了重载。只新增/更新已有 key，不删除（非白名单 key 不动）。
    """
    global _env_mtime
    try:
        mtime = os.path.getmtime(ENV_FILE)
    except (OSError, ValueError):
        return False
    if mtime <= _env_mtime:
        return False
    with _env_lock:
        if mtime <= _env_mtime:
            return False
        updated = 0
        try:
            for line in open(ENV_FILE):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                key, _, val = line.partition("=")
                key = key.strip()
                if not key or not key.isidentifier():
                    continue
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                    val = val[1:-1]
                os.environ[key] = val
                updated += 1
        except Exception as e:
            print(f"[free_pool] env reload failed: {e}")
            return False
        _env_mtime = mtime
        if updated:
            print(f"[free_pool] env 热重载: {ENV_FILE} (mtime={mtime}, {updated} keys)")
        return True


# ---- 运行时状态（健康分持久化，日预算内存态）----
_lock = threading.Lock()
_stats = {}      # route_key -> deque[(ts, ok, is_429, latency_s)]，1h 滑动窗口
_budget = {}     # provider -> {"date": "YYYYMMDD", "tokens": int}（UTC+8 日界）
WINDOW_SECONDS = 3600
MAX_SAMPLES = 500
STATE_KEEP_SAMPLES = 200   # 持久化只留最近 N 条，控制文件体积
SAVE_INTERVAL_S = 30       # 落盘节流
_last_save = 0.0


def _load_state():
    """进程启动时恢复健康分样本（v1.1：持久化，重启不清零）"""
    global _stats
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        now = time.time()
        for key, samples in data.get("stats", {}).items():
            recent = [tuple(s) for s in samples if now - s[0] <= WINDOW_SECONDS]
            if recent:
                _stats[key] = deque(recent, maxlen=MAX_SAMPLES)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[free_pool] state load failed: {e}")


def _save_state_locked():
    """调用方须持锁。节流落盘；失败不影响主流程"""
    global _last_save
    now = time.time()
    if now - _last_save < SAVE_INTERVAL_S:
        return
    _last_save = now
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        payload = {
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "stats": {
                k: [list(s) for s in list(q)[-STATE_KEEP_SAMPLES:]]
                for k, q in _stats.items()
            },
        }
        with open(tmp, "w") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        print(f"[free_pool] state save failed: {e}")


_load_state()


def _today():
    """UTC+8 日界"""
    return time.strftime("%Y%m%d", time.gmtime(time.time() + 8 * 3600))


def _optedin(req_json):
    if req_json.get("_free_pool_optin") is True:
        return True
    headers = req_json.get("_headers") or {}
    for k, v in headers.items():
        if k.lower() == OPTIN_HEADER and str(v).strip().lower() in ("true", "1", "yes"):
            return True
    return False


def _daily_budget(route):
    if route.get("daily_token_budget"):
        return int(route["daily_token_budget"])
    provider = route.get("provider", "")
    return DEFAULT_BUDGET_ANON if provider in ANON_PROVIDERS else DEFAULT_BUDGET_REGISTERED


def _budget_left(route):
    provider = route.get("provider", "")
    today = _today()
    with _lock:
        b = _budget.get(provider)
        if not b or b["date"] != today:
            return _daily_budget(route)
        return max(0, _daily_budget(route) - b["tokens"])


def _record_budget(route, tokens):
    if tokens <= 0:
        return
    provider = route.get("provider", "")
    today = _today()
    with _lock:
        b = _budget.get(provider)
        if not b or b["date"] != today:
            b = {"date": today, "tokens": 0}
        b["tokens"] += tokens
        _budget[provider] = b


def _record_stat(route_key, ok, is_429, latency_s):
    with _lock:
        q = _stats.setdefault(route_key, deque(maxlen=MAX_SAMPLES))
        q.append((time.time(), ok, is_429, latency_s))
        _save_state_locked()


# ---- eval 分惰性加载（ranker.get_eval_quality 首次会同步拉 MLflow+审计，慢）----
# free_pool 只按需查一次并缓存；查不到/超时则永久退化为 0（不影响主线程性能）
_eval_cache = {}
_eval_loaded = False


def _eval_score(provider, route_key):
    global _eval_loaded
    if _eval_loaded:
        return _eval_cache.get(provider, {}).get(route_key.rsplit("@", 1)[0], 0.0)
    _eval_loaded = True
    try:
        from ranker import _load_eval_scores
        data = _load_eval_scores()  # 一次性，带 ranker 自身 300s TTL
        for k, v in data.items():
            prov, _, mm = k.partition("@")
            _eval_cache.setdefault(prov, {})[mm] = v
    except Exception:
        pass  # eval 源不可用 → 全部 0，退化为纯运行时分
    return _eval_cache.get(provider, {}).get(route_key.rsplit("@", 1)[0], 0.0)


def _pool_score(route_key, provider="", with_eval=True):
    """健康分（P3）：运行时基础分 × 0.7 + eval_score × 0.3

    运行时基础分 = 成功率 0.7 + (1-429率) 0.3，1h 滑动窗口；无数据 = 0.5 中性。
    eval_score 惰性加载（ranker._load_eval_scores，一次查询 + 缓存）；无数据退回纯运行时分。
    with_eval=False：跳过 eval（admin stats 端点用，避免 MLflow 慢加载拖卡观测接口）。
    2026-09-15 hotfix：eval 拉取是网络 IO，必须在 _lock 外完成 —— 持锁等 mlflow
    重试 6min+ 曾阻塞 stats 端点与全部 free/* 流量（审计实证 tid 776869）。
    """
    ev = 0.0
    if with_eval:
        ev = min(1.0, max(0.0, _eval_score(provider, route_key)))
    with _lock:
        q = _stats.get(route_key)
        if not q:
            base = 0.5
        else:
            now = time.time()
            recent = [s for s in q if now - s[0] <= WINDOW_SECONDS]
            if not recent:
                base = 0.5
            else:
                n = len(recent)
                ok_rate = sum(1 for s in recent if s[1]) / n
                rate_429 = sum(1 for s in recent if s[2]) / n
                base = ok_rate * 0.7 + (1 - rate_429) * 0.3
        if ev > 0:
            return round(base * 0.7 + ev * 0.3, 4)
        return round(base, 4)


def _capability_score(route_key, route):
    """能力分（0-10，越高越强）——池内降级按此从强到弱排

    信号（无 params_b 字段，用可得信号加权）：
      - context_window: log 刻度 1M 顶格 3.0
      - thinking/tool_call/code/vision 能力位：各 1.0
      - 旗舰名称提示（ultra/max/pro/...）：+1.5
      - 1M/超长窗口路由通常为旗舰档，ctx 已覆盖，不重复计
    同分时由 _pool_score（健康分）做稳定 tiebreak。
    """
    bare = route_key.rsplit("@", 1)[0].lower()
    ctx = route.get("context_window") or 0
    import math
    s = 0.0
    if ctx:
        s += min(3.0, math.log10(max(10, ctx)) - 3.0)  # 10K→0, 100K→1, 1M→2(实际顶3)
        # 修正：log10(1M)=6 → 3.0 顶格
        s = min(s, 3.0)
    caps = route.get("capabilities") or {}
    for c in ("thinking", "tool_call", "code", "vision"):
        if caps.get(c) is True:
            s += 1.0
    if any(h in bare for h in FLAGSHIP_NAME_HINTS):
        s += 1.5
    return round(min(s, 10.0), 2)


def _credential_missing(route):
    """v1.1 反馈#1：api_key_env 声明了但环境变量未设置 → 凭证缺失（匿名免 key 不受影响）"""
    env_name = route.get("api_key_env") or ""
    return bool(env_name) and not os.environ.get(env_name)


def _bucket_match(bucket, key, route):
    """返回 True=匹配 / False=不匹配（供 excluded 诊断）"""
    spec = BUCKETS[bucket]
    caps = route.get("capabilities", {}) or {}
    # 1) capability 维度
    if any(caps.get(c) is not True for c in spec.get("caps", {})):
        return False
    ctx = route.get("context_window") or 0
    # 2) 长上下文维度
    if ctx < spec.get("min_context", 0):
        return False
    # 3) fast 维度：小窗口 + 名称启发式（小模型低延迟）
    if "max_context" in spec:
        if ctx and ctx > spec["max_context"]:
            return False
        bare = key.rsplit("@", 1)[0].lower()
        if not any(h in bare for h in FAST_NAME_HINTS):
            return False
    return True


def _pool_candidates(routes, bucket, excluded=None):
    """池内候选：tier=free 且在线、满足 bucket 维度、凭证在位、health 未冷却、预算未耗尽

    excluded（可选 dict）：统计各原因剔除数，供池空错误诊断
    """
    _reload_env_if_changed()  # 凭证热重载：env 文件变了就注入新 key（不重启拿新 token）

    out = []
    for key, route in routes.items():
        if route.get("tier", "") != "free" or route.get("default_off"):
            continue
        if not route.get("base_url"):
            continue
        # P0-1（审计拍板 C）：自动桶只调度 OpenAI 兼容协议路由。
        # trpc-batch 等非标准协议（b_ai/cline）只能被显式 model 名调用命中，
        # 不进 free/* 自动调度——否则能力分排序把它们推池首后走主转发链 401/403。
        if route.get("protocol") and route.get("protocol") != "openai":
            if excluded is not None:
                excluded["protocol"] = excluded.get("protocol", 0) + 1
            continue
        if not _bucket_match(bucket, key, route):
            if excluded is not None:
                excluded["capability"] = excluded.get("capability", 0) + 1
            continue
        if not route_is_selectable(key, route, explicit=False):
            continue
        if _credential_missing(route):
            if excluded is not None:
                excluded["credential"] = excluded.get("credential", 0) + 1
            continue
        hkey = f"{route.get('provider', '')}@{key}"
        entry = health_mod._entries.get(hkey)
        if entry and entry.get("until") and time.time() < entry["until"]:
            if excluded is not None:
                excluded["cooling"] = excluded.get("cooling", 0) + 1
            continue  # cooling/blocked，health.pre_forward 也会拦，这里提前剔除
        if _budget_left(route) <= 0:
            if excluded is not None:
                excluded["budget"] = excluded.get("budget", 0) + 1
            continue
        out.append((key, route))
    # 优雅降级：能力分强→弱为主序，冷路由惩罚次之（无样本的排到有样本的后面，
    # 避免冷路由长期霸占池首），健康分为同能力+同冷热 tiebreak，最后 key 稳定排序
    def _sort_key(kr):
        key, route = kr
        has_sample = key in _stats  # 有运行时样本（热路由）优先
        return (
            -_capability_score(key, route),
            0 if has_sample else 1,  # P1-1：无样本冷路由降权
            -_pool_score(key, route.get("provider", "")),
            key,
        )
    out.sort(key=_sort_key)
    return out


def _reject(status, msg, err_type, extra=None):
    body = {"error": msg, "type": err_type}
    if extra:
        body.update(extra)
    return (status, body)


def resolve_route(req_json, routes, route):
    """拦截 free/* 虚拟名。返回 ("ROUTE", key, route) / (4xx|5xx, body) / None（非 free 放行）"""
    if not ENABLED:
        return None
    model = req_json.get("model", "")
    # 灵克 #6：opt-in header 用于非 free/* 请求 = 滥用/配置错误，显式 400 拒绝
    if not (model == "free" or model.startswith("free/")):
        if _optedin(req_json):
            return _reject(
                400,
                "x-free-pool-optin is only valid for free/* models; "
                f"model '{model}' does not use the free pool",
                "free_pool_optin_misuse",
            )
        return None

    bucket = model[len("free/"):].strip() or "auto"
    if bucket not in BUCKETS:
        return _reject(
            400,
            f"unknown free pool bucket: {bucket} (supported: {', '.join(sorted(BUCKETS))})",
            "bad_request",
        )

    # 门禁：默认禁入 + opt-in（拍板 #2）。quality_precheck 敏感规则在 handle_endpoint
    # 阶段先行拦截（优先级更高），此处只管 opt-in 放行。
    if not _optedin(req_json):
        return _reject(
            403,
            "free pool is opt-in: send header 'x-free-pool-optin: true' "
            "(or body field _free_pool_optin) after confirming the task contains "
            "no sensitive content; or use a paid/local route instead",
            "free_pool_optin_required",
        )

    excluded = {}
    candidates = _pool_candidates(routes, bucket, excluded)
    if not candidates:
        return _reject(
            503,
            "free pool empty: all candidates cooling/budget-exhausted/credential-missing/offline",
            "free_pool_empty",
            {"excluded": excluded},
        )

    selected_key, selected_route = candidates[0]
    fallback_keys = [k for k, _ in candidates]  # 主路由在前，池内整链降级

    req_json["_selected_model"] = selected_key
    req_json["_selected_route"] = selected_route
    req_json["model"] = selected_key  # 上游 body 用真实路由名（a_00_upstream_model 会再修正 upstream_model）
    req_json["_fallback_routes"] = [routes[k] for k in fallback_keys]
    # VETO#5 折中C：匿名（may-log）平台路由 → 打标记，cache 插件对该请求 bypass，
    # 防止 may-log 平台响应进入共享缓存被复用
    req_json["_free_pool_maylog"] = _data_policy(selected_route) == "may-log"
    req_json["_free_pool"] = {
        "bucket": bucket,
        "candidates_seen": len(candidates),
        "selected": selected_key,
        "cap_score": _capability_score(selected_key, selected_route),
        "score": _pool_score(selected_key, selected_route.get("provider", "")),
        "budget_left": _budget_left(selected_route),
        "trace_prefix": TRACE_PREFIX,
    }
    print(f"[free_pool] {model} -> {selected_key} (candidates={len(candidates)}, "
          f"score={req_json['_free_pool']['score']}, budget_left={req_json['_free_pool']['budget_left']})")
    return ("ROUTE", selected_key, selected_route)


def post_response(req_json, route, model, upstream_resp, duration_ms, error_str):
    """统计健康分样本 + 日预算 token 计数 + meta 注入（含 v1.1 错误 reason）"""
    if not ENABLED:
        return
    fp = req_json.get("_free_pool")
    if not fp or not route:
        return

    route_key = req_json.get("_selected_model", model)
    err = str(error_str or "")
    # P0-2（审计）：429 判定收紧为结构化匹配 —— 原先 "429" in err 会把 trace_id/时间戳/
    # JSON 里任何含 "429" 子串的 error_str 误判成 429，健康分被污染。
    # 只认 main.py 传入的明确 429 形态：http_429 / 裸 "429" 单独出现 / 含 rate_limit 语义。
    err_ws = set(err.lower().split())
    is_429 = ("429" in err_ws
              or "http_429" in err.lower()
              or "rate_limit" in err.lower()
              or "ratelimit" in err.lower())
    _record_stat(route_key, not bool(error_str), is_429, duration_ms / 1000.0)

    # v1.1 反馈#2：错误 reason 可区分（rate_limited vs budget_exhausted）
    if is_429:
        fp["last_error_type"] = "rate_limited"
    elif _budget_left(route) <= 0:
        fp["last_error_type"] = "budget_exhausted"

    tokens = 0
    # main.py 传 bytearray（可变缓冲，meta.py 等插件原地改写后写回），dict 只出现在缓存命中场景。
    # 兼容 bytes/bytearray/str：解析 → 注入 → 原地写回，否则实机 _proxy_meta.free_pool 永远为空
    resp_dict = None
    if isinstance(upstream_resp, dict):
        resp_dict = upstream_resp
    elif isinstance(upstream_resp, (bytes, bytearray, str)):
        try:
            resp_dict = json.loads(
                bytes(upstream_resp) if isinstance(upstream_resp, bytearray)
                else upstream_resp
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            resp_dict = None
    if isinstance(resp_dict, dict):
        usage = resp_dict.get("usage") or {}
        tokens = int(usage.get("total_tokens") or 0)
        meta = resp_dict.setdefault("_proxy_meta", {})
        meta["free_pool"] = dict(fp, cost=0)  # 拍板 #3：cost=0，无 cost_source
        if isinstance(upstream_resp, bytearray) and resp_dict is not upstream_resp:
            try:
                upstream_resp.clear()
                upstream_resp.extend(json.dumps(resp_dict, ensure_ascii=False).encode("utf-8"))
            except (ValueError, TypeError):
                pass
    _record_budget(route, tokens)


# ---- 匿名平台隔离（P2）：data_policy 标记 + stats 可见 ----
# anonymous 平台（免 key，声明记录 prompt 用于训练）在 stats 与 _proxy_meta 中
# 标 data_policy="may-log"；敏感流量应走 registered/official 或 paid/local。


def _data_policy(route):
    if route.get("data_policy"):
        return route["data_policy"]
    return "may-log" if route.get("provider", "") in ANON_PROVIDERS else "unknown"


def handle_endpoint(path, handler):
    """GET /admin/free_pool/stats | POST /admin/free_pool/refresh（鉴权在主干）"""
    if path == "/admin/free_pool/stats":
        routes = _get_routes()
        members = []
        for bucket in BUCKETS:
            cands = _pool_candidates(routes, bucket) if ENABLED else []
            for key, route in cands:
                members.append({
                    "route": key,
                    "provider": route.get("provider", ""),
                    "buckets": [bucket],
                    "score": _pool_score(key, route.get("provider", ""), with_eval=False),
                    "budget_left": _budget_left(route),
                    "budget_total": _daily_budget(route),
                    "data_policy": _data_policy(route),
                })
        # 同一路由可能出现在多个 bucket —— 合并 buckets 标记（copy 避免共享 list 引用）
        merged = {}
        for m in members:
            if m["route"] in merged:
                merged[m["route"]]["buckets"].append(m["buckets"][0])
            else:
                merged[m["route"]] = dict(m, buckets=list(m["buckets"]))
        return handler.send_json(200, {
            "enabled": ENABLED,
            "buckets": sorted(BUCKETS),
            "members": sorted(merged.values(), key=lambda x: -x["score"]),
            "member_count": len(merged),
            "budget_state": {p: dict(b) for p, b in _budget.items()},
        }) or "HANDLED"
    if path == "/admin/free_pool/refresh":
        # 重扫路由（routes.json mtime 变化会自动生效，这里强制触发）+ 清空滑动窗口统计
        routes = _get_routes()
        with _lock:
            _stats.clear()
        return handler.send_json(200, {
            "status": "ok",
            "routes_loaded": len(routes),
            "stats_reset": True,
        }) or "HANDLED"
    return None

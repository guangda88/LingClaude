"""路由选择插片 - 归一化匹配 + ranker 排序 + fallback 链构建"""
import os, json, time

RANKER_ENABLED = os.environ.get("PROXY3_RANKER", "") == "1"
ROUTES_FILE = os.environ.get("PROXY3_ROUTES",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "routes.json"))

_routes_cache = None
_routes_cache_normalized = None
_routes_mtime = 0


def _normalize(name):
    """归一化模型名：lowercase + 去掉 org/ 前缀 + 去掉空格和下划线

    例:
      MiniMax-M3 -> minimax-m3
      minimaxai/minimax-m3 -> minimax-m3
      MiniMaxAI/MiniMax-M2.5 -> minimax-m2.5
      DeepSeek-V4-Flash -> deepseek-v4-flash
      deepseek-ai/DeepSeek-V4-Flash -> deepseek-v4-flash
    """
    s = name.lower().strip()
    if "/" in s:
        s = s.rsplit("/", 1)[1]
    s = s.replace("_", "-").replace(" ", "")
    # 去掉 :free / :paid 后缀 (openrouter 命名惯例)
    if s.endswith(":free") or s.endswith(":paid"):
        s = s.rsplit(":", 1)[0]
    return s


def _build_normalized_index(routes):
    """构建归一化模型名 -> 原始 key 列表的索引"""
    index = {}
    for k in routes:
        bare = k.rsplit("@", 1)[0] if "@" in k else k
        norm = _normalize(bare)
        if norm not in index:
            index[norm] = []
        index[norm].append(k)
    return index


def _get_routes():
    global _routes_cache, _routes_cache_normalized, _routes_mtime
    try:
        mtime = os.path.getmtime(ROUTES_FILE)
        if _routes_cache is None or mtime > _routes_mtime:
            with open(ROUTES_FILE) as f:
                _routes_cache = json.load(f)
            _routes_cache_normalized = _build_normalized_index(_routes_cache)
            _routes_mtime = mtime
    except Exception as e:
        print(f"[route_selector] _get_routes error: {e}")
        pass
    return _routes_cache or {}


def route_is_selectable(route_key, route, explicit=False):
    if os.environ.get("PROXY3_TEST_ALL_ROUTES") == "1":
        return True
    if route.get("default_off", False):
        return False
    if route.get("tier", "") != "paid":
        return True
    if not explicit:
        return False
    # env 从 .ling_keys.systemd.env 运行时刷新 — 兼容 systemd 外启动（手动 python3 main.py）
    # 的场景（进程 env 缺 PROXY3_ALLOW_PAID_ROUTES），reload 插件即生效，免重启。
    allow_env = os.environ.get("PROXY3_ALLOW_PAID_ROUTES")
    if not allow_env:  # 空串或缺失都从 env 文件读（空串 = 手动启动未加载 EnvironmentFile）
        try:
            for line in open("/home/ai/.ling_keys.systemd.env"):
                if line.startswith("PROXY3_ALLOW_PAID_ROUTES="):
                    allow_env = line.split("=", 1)[1].strip()
                    os.environ["PROXY3_ALLOW_PAID_ROUTES"] = allow_env
                    break
        except FileNotFoundError:
            pass
    allowed = {
        item.strip()
        for item in (allow_env or "").split(",")
        if item.strip()
    }
    return "*" in allowed or route_key in allowed


def resolve_route(req_json, routes, route):
    """主干精确匹配后的路由策略（2026-07-26 从 main.py 外移）

    返回:
      ("ROUTE", model, route) — 反向匹配命中，或精确匹配被策略否决 (route=None)
      (status, body)          — 403 等错误，主干直接返回
      None                    — 无策略意见（精确匹配可用，或无路由走候选注入）
    """
    model = req_json.get("model", "")
    raw_model = model
    if route and not route_is_selectable(model, route, explicit="@" in raw_model):
        if "@" in raw_model:
            return (403, {"error": f"route not allowed: {raw_model}"})
        route = None
    # 路由配置中的 extra_body 字段合并到请求体 (如 inclusionai 的 version 参数)
    if route:
        extra_body = route.get("extra_body", {})
        if extra_body:
            for k, v in extra_body.items():
                if k not in req_json:
                    req_json[k] = v
                    req_json["_body_rebuild_needed"] = True
    if not route:
        # provider/model 反向匹配 (e.g. "zhipu/glm-5.2")
        if "/" in raw_model and not raw_model.startswith("http"):
            provider, bare_model = raw_model.split("/", 1)
            rev_key = f"{bare_model}@{provider}"
            if rev_key in routes:
                candidate_route = routes[rev_key]
                if not route_is_selectable(rev_key, candidate_route, explicit=True):
                    return (403, {"error": f"route not allowed: {raw_model}"})
                req_json["_resolved_model"] = rev_key
                return ("ROUTE", rev_key, candidate_route)
        # 精确匹配不存在或被否决 -> 通知主干走候选注入
        return ("ROUTE", model, None)
    return None


def _find_candidates(routes, bare_model, original_model=""):
    """用归一化匹配查找候选路由。

    用户指定 @provider 时 (original_model 含 @)，不跳过任何 tier。
    未指定时按 subscription->free->paid 优先级排序，只跳过 default_off。
    🆕 2026-07-24：过滤掉 _identity: kimi 的聚合代理（除非显式 @provider）
    🆕 2026-09-10：pool 标注的 free 路由视为预验证，不受 default_off 限制。
    """
    norm = _normalize(bare_model)
    all_keys = _routes_cache_normalized.get(norm, [])
    if "@" in original_model:
        candidates = [
            k for k in all_keys
            if route_is_selectable(k, routes[k], explicit=True)
        ]
    else:
        def _selectable_free_pool_aware(k, r):
            if route_is_selectable(k, r, explicit=False):
                return True
            # pool 标注的 free 路由视为预验证，允许绕过 default_off
            if r.get("default_off") and r.get("pool") and r.get("tier") == "free":
                return True
            return False
        candidates = [
            k for k in all_keys
            if _selectable_free_pool_aware(k, routes[k])
        ]
    tier_priority = {"subscription": 0, "free_core": 1, "free_value": 1, "free": 2, "local": 3, "paid": 4}
    # 流式优先: 支持 SSE 的 provider 排在同级前面
    # yai 虽支持流式但需走适配器, 排在标准 SSE provider 之后 (避免循环前 YAI 检查拦截后不 fallback)
    stream_providers = {"volc_coding_plan", "huawei_maas", "siliconflow", "zhipu",
                        "kimi", "minimax", "doubao", "deepseek", "bailian"}
    # pool 优先级: core pool (免 key) > value pool (有 key 的增值池)
    pool_priority = {"core": 0, "value": 1, "": 2}
    candidates.sort(key=lambda k: (
        tier_priority.get(routes[k].get("tier", ""), 9),
        pool_priority.get(routes[k].get("pool", ""), 2),
        0 if routes[k].get("provider", "") in stream_providers else 1,
        0 if routes[k].get("provider", "") != "yai" else 1
    ))
    return candidates


def pre_forward(req_json, route):
    """route=None 时（精确匹配失败），选择候选路由"""
    if route is not None:
        # 精确匹配成功 - 构建 fallback 链
        # 但如果 _fallback_routes 已由第一次 pre_forward(route=None) 构建, 不覆盖
        if not req_json.get("_fallback_routes"):
            _build_fallback_chain(req_json, route)
        return None

    print(f"[route_selector] pre_forward called with route=None, model={req_json.get('model','?')}")
    # 精确匹配失败 - 选候选
    model = req_json.get("model", "")
    routes = _get_routes()
    print(f"[route_selector] loaded {len(routes)} routes")

    bare_model = model.rsplit("@", 1)[0] if "@" in model else model

    if RANKER_ENABLED:
        try:
            print(f"[route_selector] calling rank_candidates for {bare_model}")
            from plugins.ranker import rank_candidates
            candidates = rank_candidates(routes, bare_model, req_json.get("_task_type", "general"))
            print(f"[route_selector] ranker returned {len(candidates)} candidates for {bare_model}")
        except Exception as e:
            import traceback
            print(f"[route_selector] ranker 不可用: {e}")
            traceback.print_exc()
            candidates = _find_candidates(routes, bare_model, model)
    else:
        candidates = _find_candidates(routes, bare_model, model)
        if candidates:
            print(f"[route_selector] normalized match: {bare_model} -> {len(candidates)} candidates {candidates[:3]}")

    if candidates:
        selected = candidates[0]
        selected_key = selected[0] if isinstance(selected, tuple) else selected
        selected_route = selected[1] if isinstance(selected, tuple) and len(selected) > 1 else routes[selected_key]
        req_json["_selected_model"] = selected_key
        req_json["_selected_route"] = selected_route
        extra = [c[0] for c in candidates[1:]] if isinstance(candidates[0], tuple) else candidates[1:]
        _build_fallback_chain(req_json, selected_route, extra, routes)
    return None


def _build_fallback_chain(req_json, primary_route, extra_candidates=None, routes=None):
    """构建 fallback 路由列表（主路由在前，同模型其他 provider 在后）"""
    if routes is None:
        routes = _get_routes()

    model = req_json.get("_selected_model") or req_json.get("model", "")
    bare_model = model.rsplit("@", 1)[0] if "@" in model else model

    fallback = []
    seen_providers = set()

    # 主路由排第一
    primary_provider = primary_route.get("provider", "")
    if primary_provider:
        fallback.append(primary_route)
        seen_providers.add(primary_provider)

    # 用户明确指定了 @provider - 不走 fallback,只保留主路由
    # 检查原始 req_json["model"] (用户原始请求), 不是 _selected_model
    # _selected_model 是 route_selector 选出的 model@provider, 永远含 @
    original_model = req_json.get("model", "")
    if "@" in original_model or extra_candidates is None:
        req_json["_fallback_routes"] = fallback
        print(f"[route_selector] fallback chain (pinned): {[r.get('provider','?') for r in fallback]}")
        return

    if extra_candidates:
        for k in extra_candidates:
            if k in routes:
                p = routes[k].get("provider", "")
                if p not in seen_providers:
                    _maybe_append_fallback(fallback, routes[k], p, primary_provider, bare_model, seen_providers)
    else:
        # 用归一化匹配搜集同模型的其他 provider
        norm = _normalize(bare_model)
        all_keys = _routes_cache_normalized.get(norm, [])
        # 🆕 7/24 修复：按 _identity 排序，真模型 (_identity=-) 优先于聚合代理 (_identity=kimi)
        # 防止 volc 限流时 fallback 到 trae/atomgit 等 Kimi 伪装路由
        candidates_with_info = []
        for k in all_keys:
            if k == model:
                continue
            if route_is_selectable(k, routes[k], explicit=False):
                p = routes[k].get("provider", "")
                if p not in seen_providers:
                    _identity = routes[k].get("_identity", "")
                    candidates_with_info.append((k, _identity))
        # 排序：_identity=-（真模型）优先，_identity 有值（聚合代理）后排
        candidates_with_info.sort(key=lambda x: (0 if not x[1] else 1, x[0]))
        for k, _identity in candidates_with_info:
            p = routes[k].get("provider", "")
            if p not in seen_providers:
                _maybe_append_fallback(fallback, routes[k], p, primary_provider, bare_model, seen_providers)

    if fallback:
        # L5-aware: 如果请求是 L5 循环的一部分, fallback 链只保留 primary route
        # 避免 L5 round 0/1/2 看到不同的 provider, 破坏 self-consistency
        if req_json.get("_l5_session"):
            fallback = fallback[:1]
            print(f"[route_selector] L5 session {req_json.get('_l5_session')[:8]} round {req_json.get('_l5_round')}: fallback chain locked to {fallback[0].get('provider')}")
        req_json["_fallback_routes"] = fallback
        print(f"[route_selector] fallback chain: {[r.get('provider','?') for r in fallback]}")


def _maybe_append_fallback(fallback, route, provider, primary_provider, bare_model, seen_providers):
    """trae 路由作为 Kimi 兜底，禁止 generic fallback 终点（2026-07-24 修复）

    条件：
    - trae 路由的 _fallback_allowed_for 字段定义了允许的 prefix 列表
    - 只有当 bare_model 匹配其中 prefix 时，trae 才作为 fallback 终点
    - 否则跳过（避免 Kimi 身份伪装任何模型）
    """
    if provider == 'trae' and primary_provider != 'trae':
        allowed = route.get('_fallback_allowed_for', [])
        if not allowed:
            # 无 _fallback_allowed_for 字段，默认拒绝
            print(f"[route_selector] skip trae fallback (no _fallback_allowed_for): {route.get('upstream_model', '?')}")
            return
        bare_lower = bare_model.lower()
        # prefix 是带 * 后缀的通配符，strip * 后做 startswith 检查
        # 裸 * (strip 后为空) 会恒真误放行，视为配置错误跳过 (#2 审计修复)
        prefixes = [p.replace('*', '').lower() for p in allowed]
        valid = [p for p in prefixes if p]
        if len(valid) != len(prefixes):
            print(f"[route_selector] WARN: bare '*' in _fallback_allowed_for ignored: {allowed}")
        if not any(bare_lower.startswith(prefix) for prefix in valid):
            print(f"[route_selector] skip trae fallback for {bare_model} (not in {allowed})")
            return
    fallback.append(route)
    seen_providers.add(provider)

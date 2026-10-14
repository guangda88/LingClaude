#!/usr/bin/env python3
"""proxy3 薄主干 — Python 版 v0.6

主干只做 5 件事：
  1. 收请求（读 body + JSON + request_id）
  2. 查路由（精确匹配；策略由 resolve_route hook、候选由 pre_forward 插件注入）
  3. pre_forward hook（插件做限流/并发/缓存/认证/会话/请求变换）
  4. 转发（urlopen / _try_stream；forward_override/send_override hook 可接管）+ on_error hook
  5. post_response hook（插件做审计/meta）+ 返回

响应清洗走 transform_chunk/transform_body 链式 hook；非 chat 端点走 handle_endpoint hook。
其他全是插片。改插片不用重启（/admin/plugins/reload）。
"""

import json, os, sys, time, uuid, importlib, socket
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROUTES_FILE = os.environ.get(
    "PROXY3_ROUTES", os.path.join(os.path.dirname(__file__), "routes.json")
)
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8768
BUF_SIZE = 65536
ADMIN_KEY = os.environ.get("PROXY3_ADMIN_KEY", "")
if not ADMIN_KEY:
    # 文件兜底：proxy3 重启没带 env 时仍可注入 key（修复 /admin/env 鸡蛋问题，AtomCode 审计 P2-8）
    try:
        ADMIN_KEY = open(os.path.expanduser("~/.llm_proxy/admin_key")).read().strip()
    except Exception:
        pass

# ---- 插片机制 ----
HOOK_NAMES = [
    "pre_request",
    "resolve_route",
    "pre_forward",
    "forward_override",
    "send_override",
    "transform_chunk",
    "transform_body",
    "handle_endpoint",
    "retry_forward",
    "stream_wrap",
    "after_stream",
    "post_response",
    "on_error",
]
PLUGINS = {h: [] for h in HOOK_NAMES}
_plugins_loaded = {}


def load_plugins():
    plugin_dir = os.path.join(os.path.dirname(__file__), "plugins")
    if not os.path.isdir(plugin_dir):
        return
    for h in HOOK_NAMES:
        PLUGINS[h].clear()
    _plugins_loaded.clear()
    for f in sorted(os.listdir(plugin_dir)):
        if not f.endswith(".py") or f.startswith("_"):
            continue
        name = f[:-3]
        try:
            mod = importlib.import_module(f"plugins.{name}")
            _plugins_loaded[name] = mod
            for hook in HOOK_NAMES:
                if hasattr(mod, hook):
                    PLUGINS[hook].append(getattr(mod, hook))
                    print(f"[proxy3] 加载插片: {name}.{hook}")
        except Exception as e:
            print(f"[proxy3] 插片加载失败 {name}: {e}")


def reload_plugins():
    """热重载所有插件 — copy-on-write，不中断在飞请求"""
    plugin_dir = os.path.join(os.path.dirname(__file__), "plugins")
    new_plugins = {h: [] for h in HOOK_NAMES}
    reloaded = []
    for f in sorted(os.listdir(plugin_dir)):
        if not f.endswith(".py") or f.startswith("_"):
            continue
        name = f[:-3]
        try:
            if name in _plugins_loaded:
                old_mod = _plugins_loaded[name]
                # 仅摘除 hook 符号防已下线 hook 残留注册。不得 clear() 整个 __dict__：
                # reload 一旦失败会砖化 main.py 直接 import 的函数的 __globals__
                # （2026-09-06 实证: NameError Request, 全路由失败）
                for _h in HOOK_NAMES:
                    old_mod.__dict__.pop(_h, None)
                mod = importlib.reload(old_mod)
            else:
                mod = importlib.import_module(f"plugins.{name}")
            _plugins_loaded[name] = mod
            for hook in HOOK_NAMES:
                if hasattr(mod, hook):
                    new_plugins[hook].append(getattr(mod, hook))
            reloaded.append(name)
        except Exception as e:
            print(f"[proxy3] 插片热重载失败 {name}: {e}")
    # 原子替换 — 旧请求继续用旧 PLUGINS 引用，不受影响
    global PLUGINS
    PLUGINS = new_plugins
    print(f"[proxy3] 热重载 {len(reloaded)} 个插片: {reloaded}")
    return reloaded


_PLUGIN_USAGE = {}


def run_hooks(hook_name, *args):
    for hook in PLUGINS[hook_name]:
        try:
            _PLUGIN_USAGE[f"{hook.__module__}.{hook.__name__}"] = (
                _PLUGIN_USAGE.get(f"{hook.__module__}.{hook.__name__}", 0) + 1
            )
            result = hook(*args)
            if result is not None:
                return result
        except Exception as e:
            print(f"[proxy3] 插片错误 {hook_name}: {e}")
    return None


def run_post_hooks(hook_name, *args):
    for hook in PLUGINS[hook_name]:
        try:
            _PLUGIN_USAGE[f"{hook.__module__}.{hook.__name__}"] = (
                _PLUGIN_USAGE.get(f"{hook.__module__}.{hook.__name__}", 0) + 1
            )
            hook(*args)
        except Exception as e:
            print(f"[proxy3] 插片错误 {hook_name}: {e}")


def run_chain_hooks(hook_name, value, *args):
    """链式 hook（transform_chunk/transform_body）：全部执行，逐级传递返回值"""
    for hook in PLUGINS[hook_name]:
        try:
            _PLUGIN_USAGE[f"{hook.__module__}.{hook.__name__}"] = (
                _PLUGIN_USAGE.get(f"{hook.__module__}.{hook.__name__}", 0) + 1
            )
            v = hook(value, *args)
            if v is not None:
                value = v
        except Exception as e:
            print(f"[proxy3] 插片错误 {hook_name}: {e}")
    return value


def _sanitize_for_json(obj, _depth=0):
    """递归处理 dict/list 中的 bytes 值，防止 json.dumps 报 TypeError（深度限制 100 — #8 审计修复）"""
    if _depth > 100:
        return "<max depth exceeded>"
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v, _depth + 1) for v in obj]
    return obj


load_plugins()


_routes_mtime = 0
_routes_cache = None


def load_routes():
    """带 mtime 热重载的路由加载（P0-4 修复：主干 self.routes 与 route_selector 保持一致）

    每次请求经 _refresh_routes() 调用本函数；文件 mtime 变化时重新读取，
    未变化时直接返回缓存，避免每次请求都读盘。

    P1-9（2026-08-16）：过滤下划线开头伪键（_notes/_providers/_meta）+ 以 @ 开头的空 model 名
    （如 @cf/...@cloudflare）。这些不是合法路由，会污染 _handle_models / route_selector。
    过滤在每次返回时应用，不依赖 mtime 缓存分支（确保 reload 后即生效）。
    """
    global _routes_mtime, _routes_cache
    try:
        mtime = os.path.getmtime(ROUTES_FILE)
        if _routes_cache is None or mtime > _routes_mtime:
            with open(ROUTES_FILE) as f:
                _routes_cache = json.load(f)
            _routes_mtime = mtime
    except Exception as e:
        print(f"[proxy3] load_routes error: {e}")
    raw = _routes_cache or {}
    if isinstance(raw, dict):
        return {
            k: v
            for k, v in raw.items()
            if not k.startswith("_")
            and not k.startswith("@")
            and not (isinstance(v, dict) and v.get("_top_level") == "meta")
            and k not in ("providers", "_providers")
        }
    return {}


# build_upstream_req 已外移到 plugins/build_upstream.py（11 行）
from plugins.build_upstream import build_upstream_req
from plugins import sse_error


class ProxyHandler(BaseHTTPRequestHandler):
    routes = load_routes()

    def _refresh_routes(self):
        """P0-4 修复：按 mtime 热重载 self.routes（精确匹配路径与 route_selector 保持一致）"""
        fresh = load_routes()
        if fresh is not self.routes:
            type(self).routes = fresh

    def do_GET(self):
        self._refresh_routes()
        if self.path == "/healthz":
            return self.send_json(200, {"status": "ok"})
        elif self.path == "/readyz":
            return self.send_json(200, {"status": "ok", "routes": len(self.routes)})
        elif self.path == "/v1/proxy/health":
            return self.send_json(200, {"status": "ok", "service": "proxy3"})
        # admin 鉴权门控留在主干；端点处理全部走 handle_endpoint hook (admin_api 插片)
        if self.path.startswith("/admin/") and (
            not ADMIN_KEY or self.headers.get("X-Admin-Key") != ADMIN_KEY
        ):
            return self.send_json(403, {"error": "forbidden: invalid admin key"})
        if run_hooks("handle_endpoint", self.path, self) == "HANDLED":
            return
        return self.send_json(404, {"error": "not found"})

    def do_POST(self):
        self._refresh_routes()
        if self.path != "/v1/chat/completions":
            # admin 鉴权门控留在主干；端点处理全部走 handle_endpoint hook (admin_api/quality_precheck 插片)
            if self.path.startswith("/admin/") and (
                not ADMIN_KEY or self.headers.get("X-Admin-Key") != ADMIN_KEY
            ):
                return self.send_json(403, {"error": "forbidden: invalid admin key"})
            if run_hooks("handle_endpoint", self.path, self) == "HANDLED":
                return
            # 非 chat endpoint (如 /v1/embeddings, /v1/rerank) — 通过路由base_url匹配
            # 用户请求时 model 参数包含路由名, 路由的 base_url 指向实际 endpoint
            # 例如: POST /v1/embeddings  model=rag@[rag] → base_url=https://api.siliconflow.cn/v1/embeddings
            if not self.path.startswith("/v1/"):
                return self.send_json(404, {"error": "not found"})

        t0 = time.time()
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            req_json = json.loads(body)
        except json.JSONDecodeError:
            return self.send_json(400, {"error": "invalid JSON"})

        # === 1. pre_request hook ===
        req_json["_headers"] = dict(self.headers)
        req_json["_request_id"] = uuid.uuid4().hex[:8]
        result = run_hooks("pre_request", req_json, self.headers)
        if result:
            return self.send_json(*result)

        # === 2. 查路由：精确匹配 + resolve_route hook（策略/反向匹配/extra_body 由插件） ===
        model = req_json.get("model", "")
        raw_model = model
        route = self.routes.get(model)
        result = run_hooks("resolve_route", req_json, self.routes, route)
        if result:
            if isinstance(result, tuple) and result[0] == "ROUTE":
                model, route = result[1], result[2]
            else:
                return self.send_json(*result)
        if not route:
            # 候选逻辑由插件注入（route_selector.pre_forward）
            result = run_hooks("pre_forward", req_json, None)
            if result:
                if isinstance(result, tuple) and result[0] == "CACHE_HIT":
                    raw = result[1]
                    if isinstance(raw, (str, bytes, bytearray)):
                        resp_body = json.loads(
                            bytes(raw) if isinstance(raw, bytearray) else raw
                        )
                    else:
                        resp_body = raw
                    # 注入缓存命中标记
                    if isinstance(resp_body, dict):
                        meta = resp_body.setdefault("_proxy_meta", {})
                        meta["cache_hit"] = True
                    return self.send_json(200, resp_body)
                return self.send_json(*result)
            model = req_json.get("_selected_model", "")
            route = req_json.get("_selected_route")
            if not route:
                return self.send_json(
                    503, {"error": f"no route for model: {raw_model}"}
                )

        # === 3. pre_forward hook（upstream_model 修正由 a_00_upstream_model 插片） ===
        result = run_hooks("pre_forward", req_json, route)
        if result:
            if isinstance(result, tuple) and result[0] == "CACHE_HIT":
                duration = time.time() - t0
                run_post_hooks(
                    "post_response",
                    req_json,
                    route,
                    req_json.get("model", model),
                    result[1],
                    duration,
                    None,
                )
                raw = result[1]
                if isinstance(raw, (str, bytes, bytearray)):
                    resp_body = json.loads(
                        bytes(raw) if isinstance(raw, bytearray) else raw
                    )
                else:
                    resp_body = raw
                # 注入缓存命中标记
                if isinstance(resp_body, dict):
                    meta = resp_body.setdefault("_proxy_meta", {})
                    meta["cache_hit"] = True
                return self.send_json(200, resp_body)
            # pre_forward 返回的 HTTP 错误（429/503 等）不透传给客户端，
            # 静默 fallback 到下一个 provider
            if (
                isinstance(result, tuple)
                and len(result) >= 2
                and isinstance(result[0], int)
                and result[0] >= 400
            ):
                print(
                    f"[proxy3] pre_forward error {result[0]}, skipping to fallback: {str(result[1])[:120]}"
                )
                last_error = result
                # 跳过主路由，直接进入 fallback 循环
            else:
                return self.send_json(*result)

        # === 5. 转发（带 fallback） ===
        # 各阶段插片可能修改了 req_json（messages/stream/model），转发前统一重建 body
        # （修复：原实现在 system prompt 嵌入后无重建点，该功能对非 volc 路由从未生效）
        if req_json.pop("_body_rebuild_needed", None):
            body = json.dumps(
                {k: v for k, v in req_json.items() if not k.startswith("_")}
            ).encode()
        is_stream = req_json.get("stream", False)
        _original_stream = (
            is_stream  # 记录原始 stream 设置，错误响应时需按 SSE 格式返回
        )
        routes_list = req_json.get("_fallback_routes", [route])
        primary_route = routes_list[0] if routes_list else route

        # === 5.1 forward_override hook（协议适配由插片接管，如 YAI + JWT 续签） ===
        _override = run_hooks("forward_override", req_json, routes_list, self, t0)
        if _override == "HANDLED":
            return

        last_error = None
        for i, r in enumerate(routes_list):
            error_str = None
            try:
                # 协议适配插片可接管单条路由转发 (YAI 等，原主干 import yai_adapter 已外移)
                _r = run_hooks("forward_override", req_json, [r], self, t0)
                if _r == "HANDLED":
                    return
                if isinstance(_r, tuple):
                    last_error = (
                        _r[0],
                        _r[1].encode() if isinstance(_r[1], str) else _r[1],
                    )
                    if i < len(routes_list) - 1:
                        continue
                    break
                if is_stream:
                    result = self._try_stream(body, r, model, t0, req_json)
                    if result is True:
                        return  # SSE 流成功转发
                    elif result == "CACHE_HIT":
                        return  # 缓存命中
                    elif result is not None and isinstance(result, tuple):
                        return self.send_json(*result)
                    # 429/5xx → 静默 fallback，不透传错误给对话层
                    # #061 修复说明：静默失败时 last_error 保持 None，单路由 fallback 耗尽后
                    # 报 503 "all providers failed"。真实上游错误在 _try_stream 内已写
                    # post_response(stream_error:NNN) 审计（~/.llm_proxy/v3_audit.jsonl），
                    # 排障时以审计 error_type 为准，不在 SSE 错误帧透传（防 key 泄漏，灵安 P0-3 边界）。
                    if i < len(routes_list) - 1:
                        continue
                    break
                else:
                    upstream_resp = urlopen(
                        build_upstream_req(r, body, req_json.get("_extra_headers")),
                        timeout=int(r.get("timeout", 30)),
                    )
                    upstream_data = upstream_resp.read()
                    upstream_status = upstream_resp.status
                    if upstream_status >= 400:
                        last_error = (upstream_status, upstream_data)
                        action = run_hooks(
                            "on_error",
                            req_json,
                            r,
                            f"http_{upstream_status}",
                            upstream_data,
                        )
                        if action == "retry":
                            # 重试执行由 retry 插片接管 (retry_forward hook)
                            _rr = run_hooks("retry_forward", req_json, r, body)
                            if _rr:
                                upstream_status, upstream_data = _rr
                        if upstream_status >= 400:
                            if i < len(routes_list) - 1:
                                continue
                            duration = time.time() - t0
                            run_post_hooks(
                                "post_response",
                                req_json,
                                r,
                                model,
                                upstream_data,
                                duration,
                                str(upstream_status),
                            )
                            # 上游返回 >=400，且无更多 fallback → 按 SSE/JSON 格式返回非透传的错误
                            err_msg = {
                                "error": "upstream unavailable",
                                "code": upstream_status,
                            }
                            if _original_stream:
                                return sse_error.send_sse_error(
                                    self, upstream_status, json.dumps(err_msg)
                                )
                            return self.send_json(upstream_status, err_msg)
                    # 成功
                    duration = time.time() - t0
                    resp_body = json.loads(upstream_data)
                    # P0.3: upstream 200 但 body 含 error 字段（典型 browser_agg 未登录）→ 转 503 触发 fallback
                    if (
                        isinstance(resp_body, dict)
                        and "error" in resp_body
                        and not resp_body.get("choices")
                    ):
                        err_str = f"upstream_200_with_error:{str(resp_body.get('error', ''))[:80]}"
                        if i < len(routes_list) - 1:
                            last_error = (503, upstream_data)
                            continue  # 走 fallback
                        duration = time.time() - t0
                        run_post_hooks(
                            "post_response",
                            req_json,
                            r,
                            model,
                            upstream_data,
                            duration,
                            err_str,
                        )
                        return self.send_json(
                            503, {"error": err_str, "type": "upstream_reported_error"}
                        )
                    # 把 upstream_data 转 bytearray 让 post_response 插件能修改（如 meta.py）
                    upstream_data_mutable = bytearray(upstream_data)
                    run_post_hooks(
                        "post_response",
                        req_json,
                        r,
                        model,
                        upstream_data_mutable,
                        duration,
                        None,
                    )
                    # 响应清洗（剥离 Anthropic/Gemini 特有字段 + volc 思考链合并）由 transform_body 插片
                    try:
                        resp_body = json.loads(bytes(upstream_data_mutable))
                    except (json.JSONDecodeError, TypeError):
                        resp_body = json.loads(upstream_data)
                    resp_body = run_chain_hooks("transform_body", resp_body, req_json)
                    # 非 SSE 上游的 SSE 包装由 send_override 插片接管 (sse_wrap)
                    if (
                        run_hooks(
                            "send_override",
                            req_json,
                            primary_route,
                            resp_body,
                            self,
                            _original_stream,
                        )
                        == "HANDLED"
                    ):
                        return
                    return self.send_json(upstream_status, resp_body)
            except HTTPError as e:
                err_body = e.read().decode()
                last_error = (e.code, err_body)
                action = run_hooks(
                    "on_error", req_json, r, f"http_{e.code}", err_body.encode()
                )
                run_post_hooks(
                    "post_response",
                    req_json,
                    r,
                    model,
                    err_body.encode(),
                    time.time() - t0,
                    f"http_{e.code}",
                )
                if action == "retry":
                    # 重试执行由 retry 插片接管 (retry_forward hook)
                    _rr = run_hooks("retry_forward", req_json, r, body)
                    if _rr and _rr[0] < 400:
                        duration = time.time() - t0
                        resp_body = json.loads(_rr[1])
                        run_post_hooks(
                            "post_response", req_json, r, model, _rr[1], duration, None
                        )
                        return self.send_json(_rr[0], resp_body)
                if i < len(routes_list) - 1:
                    continue
                duration = time.time() - t0
                run_post_hooks(
                    "post_response",
                    req_json,
                    r,
                    model,
                    str(last_error).encode(),
                    duration,
                    str(e),
                )
                if _original_stream:
                    return sse_error.send_sse_error(self, last_error[0], last_error[1])
                return self.send_json(last_error[0], {"error": last_error[1]})
            except Exception as e:
                last_error = (502, str(e))
                if i < len(routes_list) - 1:
                    continue
                duration = time.time() - t0
                run_post_hooks(
                    "post_response", req_json, r, model, b"", duration, str(e)
                )
                if _original_stream:
                    return sse_error.send_sse_error(self, 502, str(e))
                return self.send_json(502, {"error": str(e)})

        # #061 修复：无 last_error 的 503 附带 request_id 审计指引（真实上游错误在
        # v3_audit.jsonl 的 error_type 字段，_try_stream 静默 fallback 时不透传防 key 泄漏）
        # 2026-08-23 #4 修复：补全 msg 绑定 + 类型归一化，避免 line 424 UnboundLocalError
        if last_error is None:
            _rid = req_json.get("_request_id", "unknown")
            code, msg = (
                503,
                {
                    "message": "all providers failed",
                    "code": 503,
                    "request_id": _rid,
                    "hint": "upstream error detail in ~/.llm_proxy/v3_audit.jsonl (error_type)",
                },
            )
        else:
            code, msg = last_error[0], last_error[1]
            # 类型归一化：last_error[1] 可能是 dict/list/int 等任意类型
            # 先尝试解码 bytes，否则 JSON 序列化在 line 430 会崩
            if isinstance(msg, (bytes, bytearray)):
                msg = msg.decode("utf-8", errors="replace")
            elif not isinstance(msg, (str, dict)):
                # 非 str/dict 类型：转 str 防止下游 send_json JSON 序列化失败
                msg = str(msg)
        # 兜底防御：极少数路径（异常 SyntaxError / multi-line literal 解析失败）
        # msg 可能仍未绑定，此处强制 None 兜底 + 500 返回
        if "msg" not in locals() or "code" not in locals():
            code, msg = 500, "internal proxy3 UnboundLocalError fallback"
        if _original_stream:
            return sse_error.send_sse_error(self, code, msg)
        return self.send_json(code, {"error": msg})

    def _try_stream(self, body_bytes, route, model, t0, req_json):
        """SSE 逐块转发 — 纯转发，不含错误处理；chunk 清洗走 transform_chunk 链式 hook"""
        import http.client
        from urllib.parse import urlparse

        url = route["base_url"]
        timeout = int(route.get("timeout", 300))
        parsed = urlparse(url)
        is_https = parsed.scheme == "https"
        host = parsed.hostname
        port = parsed.port or (443 if is_https else 80)
        path = parsed.path

        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        extra = req_json.get("_extra_headers", {})
        if extra:
            headers.update(extra)

        conn = (
            http.client.HTTPSConnection(host, port, timeout=timeout)
            if is_https
            else http.client.HTTPConnection(host, port, timeout=timeout)
        )
        error_str = None
        response_committed = False
        try:
            conn.request("POST", path, body_bytes, headers)
            resp = conn.getresponse()
            if resp.status >= 400:
                err_body = resp.read().decode()
                # 提取 Retry-After 头
                retry_after = None
                ra = resp.getheader("Retry-After")
                if ra:
                    try:
                        retry_after = int(ra)
                    except ValueError:
                        pass
                duration = time.time() - t0
                run_post_hooks(
                    "post_response",
                    req_json,
                    route,
                    model,
                    err_body.encode(),
                    duration,
                    f"stream_error:{resp.status}",
                )
                print(
                    f"[proxy3] _try_stream {resp.status} from {route.get('provider', '?')} - silent fallback"
                )
                return None  # 不重试、不透传，静默 fallback
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            response_committed = True

            # 先读首个 chunk 判定上游返的是 SSE (data: 开头) 还是完整 JSON ({ 开头)
            # atomgit 等上游对 stream=true 仍返完整 JSON application/json 非 SSE,
            # crush 期待 text/event-stream SSE 流 — 协议不匹配报 unexpected EOF
            # 此时把完整 JSON 包装成单 chunk SSE: data: {完整 JSON}\n\ndata: [DONE]\n\n
            # 🆕 7/24 streaming 修复：剥离 HTTP/1.1 chunked transfer encoding
            # trae_proxy 等 FastAPI StreamingResponse 默认走 chunked（无 Content-Length），
            # http.client.getresponse().read() 拿到的字节含 8e\r\n 等 hex 长度前缀，
            # 直接 lstrip() 检查 startswith('data:') 会失败。统一剥离后再判定。
            def _strip_chunked(data: bytes) -> bytes:
                out = bytearray()
                i = 0
                while i < len(data):
                    nl = data.find(b"\r\n", i)
                    if nl < 0 or nl - i > 32:
                        # 不是合法的 chunked 编码（hex 长度行不超过 32 字节）
                        return bytes(data)
                    try:
                        sz = int(data[i:nl].split(b";")[0], 16)
                    except ValueError:
                        return bytes(data)
                    i = nl + 2
                    if sz == 0:
                        # terminator chunk
                        return bytes(out)
                    if len(data) < i + sz + 2:
                        return bytes(data)  # 不完整，原样返回
                    out += data[i : i + sz]
                    i += sz + 2  # skip \r\n after chunk
                return bytes(out)

            first_chunk = _strip_chunked(resp.read(BUF_SIZE))
            if first_chunk and (
                first_chunk.lstrip().startswith(b"data:")
                or first_chunk.lstrip().startswith(b"event:")
            ):
                # 上游真 SSE,透传首 chunk + 剩余；清洗（event 行/垃圾标记/tool_call 抑制/reasoning 合并）由插片链
                cleaned = run_chain_hooks("transform_chunk", first_chunk, req_json)
                self.wfile.write(cleaned)
                self.wfile.flush()
                last_chunk = cleaned
                while True:
                    chunk = resp.read(BUF_SIZE)
                    if not chunk:
                        break
                    cleaned_chunk = run_chain_hooks("transform_chunk", chunk, req_json)
                    self.wfile.write(cleaned_chunk)
                    self.wfile.flush()
                    last_chunk = cleaned_chunk
                # 末 chunk 缺 [DONE] 结束符的补发由 after_stream 插片 (sse_wrap)
                _tail = run_hooks("after_stream", last_chunk, req_json)
                if _tail:
                    self.wfile.write(_tail)
                    self.wfile.flush()
            elif first_chunk and first_chunk.lstrip().startswith(b"{"):
                # 上游返完整 JSON (message.content 完整响应),包装成 SSE chunk 格式:
                # crush 期待流式增量 delta.content 非 message.content,否则报 error unmarshalling delta
                # 把 message 字段转 delta,finish_reason 移到末 chunk,加 object=chat.completion.chunk
                json_body = first_chunk.strip()
                while True:
                    leftover = resp.read(BUF_SIZE)
                    if not leftover:
                        break
                    json_body += leftover
                # 清洗由 transform_chunk 插片链；JSON→SSE 包装由 stream_wrap 插片 (sse_wrap)
                cleaned_body = run_chain_hooks("transform_chunk", json_body, req_json)
                sse_data = run_hooks("stream_wrap", cleaned_body, req_json)
                if sse_data is None:
                    sse_data = cleaned_body  # 插片未接管/解析失败：透传原文
                sse_payload = b"data: " + sse_data + b"\n\ndata: [DONE]\n\n"
                self.wfile.write(sse_payload)
                self.wfile.flush()
            else:
                # 未知格式或空,透传
                if first_chunk:
                    self.wfile.write(first_chunk)
                    self.wfile.flush()
                while True:
                    chunk = resp.read(BUF_SIZE)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            self.close_connection = True
            return True
        except (BrokenPipeError, ConnectionResetError, socket.timeout) as e:
            error_str = str(e)
            if response_committed:
                # 响应已提交（200+SSE头已发），无法撤回。
                # 不返回 None — 防止调用方试图发第二个 HTTP 响应，那会导致 BrokenPipeError 线程崩溃。
                # 直接关闭连接，Crush 侧收到完整 EOF（非 status_code=0）。
                self.close_connection = True
                return True
            return None
        finally:
            duration = time.time() - t0
            run_post_hooks(
                "post_response", req_json, route, model, b"", duration, error_str
            )
            conn.close()

    def send_json(self, code, data):
        if not isinstance(code, int):
            code = int(code)
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            safe = _sanitize_for_json(data)
            self.wfile.write(json.dumps(safe, ensure_ascii=False).encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # 客户端已断开，忽略

    def log_message(self, fmt, *args):
        print(f"[proxy3] {args[0]} {args[1]} {args[2]}")


if __name__ == "__main__":
    print(f"[proxy3] 薄主干 v0.6 :{PORT}  routes={ROUTES_FILE}")
    from concurrent.futures import ThreadPoolExecutor
    import socketserver
    import http.server

    # 用 ThreadPoolExecutor 限制并发线程数,避免线程累积导致 RSS 增长
    _executor = ThreadPoolExecutor(max_workers=64)

    class PooledHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

        def process_request(self, request, client_address):
            # 用固定大小线程池替代默认 ThreadingMixIn 的"每请求一个线程"
            self.executor.submit(self._handle_request_safe, request, client_address)

        @property
        def executor(self):
            return _executor

        def _handle_request_safe(self, request, client_address):
            try:
                self.finish_request(request, client_address)
            except Exception:
                self.handle_error(request, client_address)
            finally:
                self.shutdown_request(request)

    # 端口绑定重试 - 防止重启时旧进程未释放端口导致崩溃-重启循环
    max_retries = 5
    for attempt in range(max_retries):
        try:
            server = PooledHTTPServer(("0.0.0.0", PORT), ProxyHandler)
            break
        except OSError as e:
            if e.errno == 98 and attempt < max_retries - 1:
                print(
                    f"[proxy3] 端口 {PORT} 被占用, 等待重试 ({attempt + 1}/{max_retries})..."
                )
                time.sleep(3)
            else:
                raise
    server.serve_forever()

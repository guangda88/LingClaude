#!/usr/bin/env python3
"""free_pool 插件测试 — v1.1 P1（proposal §8 反馈收敛对应）

覆盖 6 类场景：
  1. 未 opt-in → 403 free_pool_optin_required
  2. opt-in 后 free/auto 正常选路（model 替换 + meta 注入）
  3. free/code 能力过滤（tool_call+code 双能力）
  4. health blocked/cooling 剔除
  5. 凭证缺失剔除（api_key_env 非空但 env 未设置）
  6. fallback 候选按健康分写入 _fallback_routes

运行: python3 -m pytest proxy3_py/tests/test_free_pool.py -v
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PROXY3_FREE_POOL"] = "1"


def _mk_route(**kw):
    route = {
        "base_url": "https://example.com/v1/chat/completions",
        "provider": "testprov",
        "tier": "free",
        "api_key_env": "",
    }
    route.update(kw)
    return route


class TestFreePool(unittest.TestCase):
    def setUp(self):
        import plugins.free_pool as fp
        import plugins.health as health_mod
        self.fp = fp
        self.health = health_mod
        # 隔离：清空运行时状态，健康分落盘指到临时文件
        fp._stats.clear()
        fp._budget.clear()
        health_mod._entries.clear()
        self._tmp_state = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp_state.close()
        os.unlink(self._tmp_state.name)
        self._orig_state_path = fp.STATE_PATH
        fp.STATE_PATH = self._tmp_state.name
        # 隔离 eval 懒加载：预置已加载态（否则 _pool_score 触发 ranker._load_eval_scores
        # → 连 mlflow 127.0.0.1:5000，无服务时 urllib3 长重试导致测试挂死）
        fp._eval_loaded = True
        fp._eval_cache.clear()
        # 确保测试用假 env 不存在
        os.environ.pop("FP_TEST_MISSING_KEY", None)

    def tearDown(self):
        self.fp.STATE_PATH = self._orig_state_path
        if os.path.exists(self._tmp_state.name):
            os.unlink(self._tmp_state.name)

    def _req(self, model="free/auto", optin=True):
        req = {"model": model, "_headers": {}}
        if optin:
            req["_free_pool_optin"] = True
        return req

    # ---- 1. 未 opt-in → 403 ----
    def test_no_optin_403(self):
        routes = {"m@a": _mk_route()}
        result = self.fp.resolve_route(self._req(optin=False), routes, None)
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], 403)
        self.assertEqual(result[1]["type"], "free_pool_optin_required")

    def test_header_optin_accepted(self):
        routes = {"m@a": _mk_route()}
        req = {"model": "free/auto", "_headers": {"x-free-pool-optin": "true"}}
        result = self.fp.resolve_route(req, routes, None)
        self.assertEqual(result[0], "ROUTE")

    # ---- 2. opt-in 后 free/auto 选路 ----
    def test_auto_selects_and_rewrites(self):
        routes = {"glm@a": _mk_route(), "ds@b": _mk_route()}
        req = self._req()
        result = self.fp.resolve_route(req, routes, None)
        self.assertEqual(result[0], "ROUTE")
        key, route = result[1], result[2]
        self.assertIn(key, routes)
        self.assertEqual(req["model"], key)
        self.assertEqual(req["_selected_model"], key)
        fp_meta = req["_free_pool"]
        self.assertEqual(fp_meta["bucket"], "auto")
        self.assertEqual(fp_meta["candidates_seen"], 2)
        self.assertIn("budget_left", fp_meta)
        # 主路由自身也在 fallback 链首
        self.assertEqual(req["_fallback_routes"][0], route)

    # ---- 3. free/code 能力过滤 ----
    def test_code_bucket_capability_filter(self):
        routes = {
            "nocode@a": _mk_route(),                              # 无 capabilities
            "toolonly@a": _mk_route(capabilities={"tool_call": True}),   # 缺 code
            "full@a": _mk_route(capabilities={"tool_call": True, "code": True}),
        }
        req = self._req("free/code")
        result = self.fp.resolve_route(req, routes, None)
        self.assertEqual(result[0], "ROUTE")
        self.assertEqual(result[1], "full@a")
        self.assertEqual(req["_free_pool"]["candidates_seen"], 1)

    # ---- 4. health cooling/blocked 剔除 ----
    def test_health_cooling_excluded(self):
        routes = {
            "hot@a": _mk_route(),
            "cool@a": _mk_route(provider="coolprov"),
        }
        self.health._entries["coolprov@cool@a"] = {
            "status": 2, "until": time.time() + 600, "fail_count": 3,
        }
        req = self._req()
        result = self.fp.resolve_route(req, routes, None)
        self.assertEqual(result[1], "hot@a")
        self.assertEqual(req["_free_pool"]["candidates_seen"], 1)

    # ---- 5. 凭证缺失剔除 ----
    def test_credential_missing_excluded(self):
        routes = {
            "nokey@a": _mk_route(),  # api_key_env 空 = 匿名免 key，保留
            "needkey@b": _mk_route(api_key_env="FP_TEST_MISSING_KEY"),
        }
        req = self._req()
        result = self.fp.resolve_route(req, routes, None)
        self.assertEqual(result[1], "nokey@a")
        self.assertEqual(req["_free_pool"]["candidates_seen"], 1)

    def test_credential_present_kept(self):
        os.environ["FP_TEST_PRESENT_KEY"] = "x"
        try:
            routes = {"needkey@b": _mk_route(api_key_env="FP_TEST_PRESENT_KEY")}
            result = self.fp.resolve_route(self._req(), routes, None)
            self.assertEqual(result[1], "needkey@b")
        finally:
            os.environ.pop("FP_TEST_PRESENT_KEY", None)

    # ---- 6. fallback 候选写入（健康分降序）----
    def test_fallback_chain_health_order(self):
        routes = {"good@a": _mk_route(), "bad@b": _mk_route(provider="p2")}
        # good@a 成功样本，bad@b 全 429
        self.fp._record_stat("good@a", True, False, 0.2)
        self.fp._record_stat("bad@b", False, True, 0.2)
        req = self._req()
        result = self.fp.resolve_route(req, routes, None)
        self.assertEqual(result[1], "good@a")
        chain_keys = [r for r in req["_fallback_routes"]]
        self.assertEqual(chain_keys, [routes["good@a"], routes["bad@b"]])
        self.assertGreater(req["_free_pool"]["score"], 0.5)

    # ---- 补充：错误语义与 meta ----
    def test_unknown_bucket_400(self):
        # P3 扩桶后 free/vision 已合法，未知桶须用一个不存在的名字
        result = self.fp.resolve_route(self._req("free/nope"), {"m@a": _mk_route()}, None)
        self.assertEqual(result[0], 400)
        self.assertEqual(result[1]["type"], "bad_request")

    def test_vision_bucket_valid_after_p3(self):
        # P3 扩桶回归：vision 是合法 bucket，vision 能力路由可选
        routes = {"vl@a": _mk_route(capabilities={"vision": True})}
        result = self.fp.resolve_route(self._req("free/vision"), routes, None)
        self.assertEqual(result[0], "ROUTE")
        self.assertEqual(result[1], "vl@a")

    def test_empty_pool_503_with_excluded_summary(self):
        routes = {"needkey@b": _mk_route(api_key_env="FP_TEST_MISSING_KEY")}
        result = self.fp.resolve_route(self._req(), routes, None)
        self.assertEqual(result[0], 503)
        self.assertEqual(result[1]["type"], "free_pool_empty")
        self.assertEqual(result[1]["excluded"].get("credential"), 1)

    def test_non_free_model_passthrough(self):
        self.assertIsNone(self.fp.resolve_route({"model": "gpt-x"}, {}, None))
        self.assertIsNone(self.fp.resolve_route({"model": "freedom"}, {}, None))

    def test_non_free_with_optin_400(self):
        # 灵克 #6：opt-in 用于非 free/* 请求 → 400 free_pool_optin_misuse
        result = self.fp.resolve_route(
            {"model": "gpt-x", "_free_pool_optin": True}, {}, None)
        self.assertEqual(result[0], 400)
        self.assertEqual(result[1]["type"], "free_pool_optin_misuse")

    # ---- 优雅降级：能力强→弱为主序（健康分 tiebreak）----
    def test_capability_descending_order(self):
        # flagship（1M ctx + 全能力 + pro 名）应排在 mini（小 ctx + 无能力位）前面，
        # 即使 mini 健康分更高——能力是主序，健康分只是 tiebreak
        self.fp._record_stat("mini@a", True, False, 0.1)   # mini 健康
        self.fp._record_stat("flag@b", False, False, 5.0)  # flag 有失败记录
        routes = {
            "mini@a": _mk_route(provider="p1", context_window=32768,
                                capabilities={"thinking": False, "tool_call": False,
                                              "code": False, "vision": False}),
            "flag@b": _mk_route(provider="p2", context_window=1_048_576,
                                capabilities={"thinking": True, "tool_call": True,
                                              "code": True, "vision": True}),
        }
        cands = self.fp._pool_candidates(routes, "auto")
        self.assertEqual([k for k, _ in cands][0], "flag@b")

    def test_capability_score_components(self):
        # 旗舰名 +1.5 / ctx 1M +3.0 / 每能力位 +1.0
        strong = _mk_route(context_window=1_048_576,
                           capabilities={"thinking": True, "tool_call": True,
                                         "code": True, "vision": True})
        weak = _mk_route(context_window=8192,
                         capabilities={"thinking": False, "tool_call": False,
                                       "code": False, "vision": False})
        s_strong = self.fp._capability_score("glm-5.3-pro@x", strong)
        s_weak = self.fp._capability_score("tiny-mini@x", weak)
        self.assertGreater(s_strong, s_weak)
        self.assertGreaterEqual(s_weak, 0.0)
        self.assertLessEqual(s_strong, 10.0)
        # 同能力不同名：pro 名 > 无提示名
        mid = _mk_route(context_window=131072,
                        capabilities={"thinking": True, "tool_call": True,
                                      "code": False, "vision": False})
        self.assertGreater(
            self.fp._capability_score("model-pro@x", mid),
            self.fp._capability_score("model-base@x", mid),
        )

    def test_maylog_flag_on_anon_route(self):
        anon = _mk_route(provider="empero")
        reg = _mk_route(provider="atomgit")
        # 只有匿名路由的用例（注册路由无 code 能力不匹配，双路由时按健康分排序选匿名）
        req1 = self._req()
        self.fp.resolve_route(req1, {"empero-m@a": anon}, None)
        self.assertTrue(req1.get("_free_pool_maylog"))
        req2 = self._req()
        self.fp.resolve_route(req2, {"reg-m@b": reg}, None)
        self.assertFalse(req2.get("_free_pool_maylog"))
        # _data_policy 判定本身
        self.assertEqual(self.fp._data_policy(anon), "may-log")
        self.assertNotEqual(self.fp._data_policy(reg), "may-log")

    def test_post_response_meta_and_budget(self):
        routes = {"m@a": _mk_route()}
        req = self._req()
        self.fp.resolve_route(req, routes, None)
        resp = {"usage": {"total_tokens": 123}}
        self.fp.post_response(req, routes["m@a"], req["model"], resp, 100.0, None)
        self.assertEqual(resp["_proxy_meta"]["free_pool"]["selected"], "m@a")
        self.assertEqual(resp["_proxy_meta"]["free_pool"]["cost"], 0)
        self.assertEqual(self.fp._budget["testprov"]["tokens"], 123)

    def test_post_response_429_reason(self):
        routes = {"m@a": _mk_route()}
        req = self._req()
        self.fp.resolve_route(req, routes, None)
        self.fp.post_response(req, routes["m@a"], req["model"], {}, 100.0, "HTTP 429 too many requests")
        self.assertEqual(req["_free_pool"]["last_error_type"], "rate_limited")

    def test_state_persist_roundtrip(self):
        self.fp._record_stat("persist@a", True, False, 0.1)
        # 强制落盘（绕过节流）
        with self.fp._lock:
            self.fp._last_save = 0.0
            self.fp._save_state_locked()
        import json as _json
        with open(self.fp.STATE_PATH) as f:
            data = _json.load(f)
        self.assertIn("persist@a", data["stats"])


if __name__ == "__main__":
    unittest.main()

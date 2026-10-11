"""P-OBS（2026-10-11）：路由落点可观测 —— resolve() 必须记录本轮实走 provider/model。

背景（变慢排查实录）：host_root.log 只有「路由剔除 / 熔断」这类**排除性**记录，
没有「本轮实走谁」——"排除了谁" ≠ "选了谁"，导致变慢排查只能靠推断。
本测试锁定三条契约：
1. resolve() 出口必产出一条 INFO「路由落点」，含 provider/model/route/path；
2. api_key 明文永不入日志（只出现 sha256 前 8 位指纹）；
3. 日志级别高于 INFO 时自动跳过（观测开销可关，热路径零成本）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from pathlib import Path

from lingclaude.model.task_router import TaskRouter

CFG = {
    "routing": {
        "default_target": "cheap",
        "providers": {
            "glm": {
                "type": "openai",
                "api_key": "SECRET-GLM-KEY-CANARY",
                "base_url": "https://glm.example/v1",
                "model": "glm-5.3-flash",
                "models": ["glm-5.3-flash"],
                "rate_limit": {"rpm": 10, "burst": 3},
            },
            "cheap": {
                "type": "openai",
                "api_key": "SECRET-CHEAP-KEY-CANARY",
                "base_url": "https://cheap.example/v1",
                "model": "glm-4.7-flash",
                "models": ["glm-4.7-flash"],
                "rate_limit": {"rpm": 30, "burst": 5},
            },
        },
        "task_routes": {
            "coding": {
                "description": "Code tasks",
                "models": [
                    {"provider": "glm", "model": "glm-5.3-flash"},
                    {"provider": "cheap", "model": "glm-4.7-flash"},
                ],
            },
            "fast_response": {
                "description": "Quick tasks",
                "models": [{"provider": "glm", "model": "glm-5.3-flash"}],
            },
        },
    },
}


def _make_router() -> TaskRouter:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(CFG, f)
    f.close()
    return TaskRouter(config_path=Path(f.name))


def _records(caplog, level: int = logging.INFO):
    with caplog.at_level(level, logger="lingclaude.model.task_router"):
        return list(caplog.records)


class TestRouteSelectedLog:
    def test_resolve_logs_selected_provider_and_model(self, caplog):
        """契约 1：每次 resolve() 都要说出本轮实走谁（provider+model+route+path）。"""
        router = _make_router()
        caplog.set_level(logging.INFO, logger="lingclaude.model.task_router")
        caplog.clear()

        cfg, route_key = router.resolve("fix this bug in this file")

        logs = [r.getMessage() for r in caplog.records if "路由落点" in r.getMessage()]
        assert logs, f"resolve() 未产出「路由落点」日志; got={[r.getMessage() for r in caplog.records][:5]}"
        line = logs[-1]
        # provider/model 与实际返回的 ModelConfig 一致
        assert "provider=glm" in line, line
        assert f"model={cfg.model}" in line, line
        assert f"route={route_key}" in line, line
        # 首位候选健康 → path=route（清单选通，非兜底）
        assert "path=route" in line, line
        assert cfg.base_url in line, line

    def test_api_key_plaintext_never_logged(self, caplog):
        """契约 2：key 明文不入日志，只留 sha256 前 8 位指纹（防泄漏）。"""
        router = _make_router()
        caplog.set_level(logging.INFO, logger="lingclaude.model.task_router")
        caplog.clear()

        cfg, _ = router.resolve("fix this bug in this file")

        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "SECRET-GLM-KEY-CANARY" not in joined, "api_key 明文泄漏到日志"
        assert "SECRET-CHEAP-KEY-CANARY" not in joined, "api_key 明文泄漏到日志"
        # 指纹必须在（供排查"进程持有的 key 是否已轮换"）
        expected_fp = hashlib.sha256(cfg.api_key.encode("utf-8")).hexdigest()[:8]
        assert f"key_fp={expected_fp}" in joined, joined

    def test_fallback_path_logged(self, caplog):
        """契约 1b：清单全灭走兜底时，path 必须如实记为 fallback。"""
        router = _make_router()
        # 把两个候选都打进冷却 → _pick_from_route 全灭 → _fallback
        for name in ("glm", "cheap"):
            router._slots[name].cooldown_until = 10**12
        caplog.set_level(logging.INFO, logger="lingclaude.model.task_router")
        caplog.clear()

        router.resolve("fix this bug in this file")

        lines = [r.getMessage() for r in caplog.records if "路由落点" in r.getMessage()]
        assert lines, "兜底路径未产出落点日志"
        assert "path=fallback" in lines[-1], lines[-1]

    def test_silent_above_info_level(self, caplog):
        """契约 3：日志级别高于 INFO 时跳过（热路径观测开销可关）。"""
        router = _make_router()
        caplog.set_level(logging.WARNING, logger="lingclaude.model.task_router")
        caplog.clear()

        router.resolve("fix this bug in this file")

        assert not [r for r in caplog.records if "路由落点" in r.getMessage()]

    def test_key_fingerprint_helper(self):
        """指纹：稳定、短、空 key 记 '-'，且不等于明文。"""
        from lingclaude.model.task_router import TaskRouter as T

        fp = T._key_fingerprint("SECRET-GLM-KEY-CANARY")
        assert len(fp) == 8
        assert fp == hashlib.sha256(b"SECRET-GLM-KEY-CANARY").hexdigest()[:8]
        assert fp != "SECRET-GLM-KEY-CANARY"
        assert T._key_fingerprint("") == "-"

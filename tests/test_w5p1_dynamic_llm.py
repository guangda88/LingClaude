"""W5+1 (2026-09-11): 直连兜底链动态化 + TARGET_MODEL 环境变量化 的回归测试。

覆盖:
1. _resolve_llm_chain: config 优先 / env 覆盖 / 静态表兜底
2. _call_llm_direct: 动态链消费 (mock urlopen, 零真实网络)
3. TARGET_MODEL: casefold 大小写不敏感匹配 (内存聚合 + SQL 趋势)
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request

import pytest


# ───────────────── _resolve_llm_chain: 链构造 ─────────────────


class TestResolveLlmChain:
    def test_config_first_returns_production_model(self, monkeypatch):
        """默认走 config.yaml: 生产模型 + coding 端点 + key 同源解析。"""
        monkeypatch.delenv("LINGCLAUDE_BYPASS_MODEL", raising=False)
        from lingclaude.api import _resolve_llm_chain

        chain = _resolve_llm_chain()
        assert len(chain) >= 1
        first = chain[0]
        # 2026-09-11 晚: 断言与 config.yaml 同源, 不再硬编码型号名/端点
        # (否则每次换模型/供应商都要改测试, 违背「换模型零代码改动」哲学)。
        from lingclaude.core.config import find_config_path, load_config

        cfg = load_config(find_config_path())
        assert first["model"] == cfg.model.model
        assert first["url"] == cfg.model.base_url.rstrip("/") + "/chat/completions"
        # config 链项必须自带 _key (否则运行时会被跳过)
        assert first.get("_key"), "config 链项缺 _key"

    def test_env_override_takes_precedence(self, monkeypatch):
        """LINGCLAUDE_BYPASS_MODEL 整体覆盖, 不读 config。"""
        monkeypatch.setenv("LINGCLAUDE_BYPASS_MODEL", "test-override-model")
        monkeypatch.setenv("LINGCLAUDE_BYPASS_BASE_URL", "https://example.com/v4")
        monkeypatch.setenv("LINGCLAUDE_BYPASS_API_KEY_ENV", "MY_KEY_ENV")
        from lingclaude.api import _resolve_llm_chain

        chain = _resolve_llm_chain()
        assert chain == [{
            "key_env": "MY_KEY_ENV",
            "url": "https://example.com/v4",
            "model": "test-override-model",
        }]

    def test_static_fallback_on_config_failure(self, monkeypatch):
        """config 损坏时降级静态表, 不抛异常。"""
        monkeypatch.delenv("LINGCLAUDE_BYPASS_MODEL", raising=False)
        import lingclaude.api as api_mod

        def boom(*a, **kw):
            raise RuntimeError("config exploded")

        monkeypatch.setattr(api_mod, "load_config", boom, raising=False)
        # _resolve_llm_chain 内部是函数内 import, 需 patch 源头
        import lingclaude.core.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "load_config", boom)

        chain = api_mod._resolve_llm_chain()
        assert chain == api_mod._STATIC_LLM_FALLBACK
        assert all("_key" not in item for item in chain)


# ───────────────── _call_llm_direct: 链消费 (零真实网络) ─────────────────


class _FakeResp:
    def __init__(self, payload: dict):
        self._b = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestCallLlmDirect:
    def test_uses_dynamic_chain_model_and_key(self, monkeypatch):
        """消费端必须把动态链的 model/url/key 打进请求。"""
        captured: dict = {}

        def fake_urlopen(req: Request, timeout: float = 0):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode())
            captured["auth"] = req.headers.get("Authorization")
            return _FakeResp({"choices": [{"message": {"content": "pong"}}]})

        monkeypatch.setenv("LINGCLAUDE_BYPASS_MODEL", "test-model-x")
        monkeypatch.setenv("ZHIPU_API_KEY", "test-key-123")
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        from lingclaude.api import _call_llm_direct

        out = _call_llm_direct("sys", "hi")
        assert out == "pong"
        assert captured["url"] == "https://example.com/v4".replace(
            "https://example.com/v4", "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
        )  # 未设 BYPASS_BASE_URL → coding 端点默认
        assert captured["body"]["model"] == "test-model-x"
        assert captured["auth"] == "Bearer test-key-123"

    def test_skips_entries_without_key(self, monkeypatch):
        """key_env 指向的 env 全空 → 返回空串, 不抛。"""
        monkeypatch.delenv("LINGCLAUDE_BYPASS_MODEL", raising=False)
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        monkeypatch.delenv("GLM_CODING_PLAN_KEY", raising=False)
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        import lingclaude.api as api_mod
        # 断 config 路径 (否则 _key 会绕过 env 检查)
        monkeypatch.setattr(
            api_mod, "_resolve_llm_chain", lambda: list(api_mod._STATIC_LLM_FALLBACK)
        )

        assert api_mod._call_llm_direct("sys", "hi") == ""


# ───────────────── TARGET_MODEL: casefold 匹配 ─────────────────


class TestTargetModelCasefold:
    def test_ratio_matches_case_insensitively(self, tmp_path):
        """库内大写形态的模型名也计入目标使用率 (旧代码精确匹配会得 0)。"""
        from lingclaude.core.token_monitor import TARGET_MODEL, TokenMonitor

        m = TokenMonitor(db_path=tmp_path / "t.db")
        m.record_usage(model=TARGET_MODEL.upper(), task_type="code_generation",
                       total_tokens=100, input_tokens=40, output_tokens=60)
        m.record_usage(model="other-model", task_type="analysis",
                       total_tokens=100, input_tokens=40, output_tokens=60)

        em = m.get_efficiency_metrics()
        assert em.glm_4_7_ratio == pytest.approx(0.5)

    def test_html_status_case_insensitive(self, tmp_path: Path):
        """大写形态模型行应标 status-good (旧代码误标 warning)。"""
        from lingclaude.core.token_monitor import TARGET_MODEL, TokenMonitor

        m = TokenMonitor(db_path=tmp_path / "t.db")
        m.record_usage(model=TARGET_MODEL.upper(), task_type="x",
                       total_tokens=100, input_tokens=40, output_tokens=60)
        out = tmp_path / "r.html"
        m.generate_html_report(output_path=out)
        assert 'class="status-good"' in out.read_text(encoding="utf-8")

    def test_markdown_trend_sql_case_insensitive(self, tmp_path: Path):
        """趋势 SQL LOWER(model) 匹配: 大写形态入库也应算出 100.0%。"""
        from lingclaude.core.token_monitor import TARGET_MODEL, TokenMonitor

        m = TokenMonitor(db_path=tmp_path / "t.db")
        m.record_usage(model=TARGET_MODEL.upper(), task_type="x",
                       total_tokens=500, input_tokens=200, output_tokens=300)
        out = tmp_path / "r.md"
        m.generate_markdown_report(output_path=out)
        text = out.read_text(encoding="utf-8")
        assert "100.0%" in text, "趋势 SQL 未做大小写不敏感匹配"

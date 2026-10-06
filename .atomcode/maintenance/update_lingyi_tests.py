#!/usr/bin/env python3
"""灵依 web_app.py 测试同步器：生成/更新 test_webapp_plan.py（契约测试，TestClient，不依赖网络/GPU）。

用法: python3 update_lingyi_tests.py <tests目录>
测试策略:
- monkeypatch web_app.GLM_API_KEY / web_app.call_glm / whisper.load_model
- 不触发 startup 事件（TestClient 不进 with 块就不跑 on_event startup）
- whisper 惰性路径: 注入假 whisper 模块后 reload，验证 import 不炸、get_whisper_model 降级
"""
from __future__ import annotations

import sys
from pathlib import Path

TESTS = '''"""灵依 web_app 契约测试（linghealth plan/generate + whisper 惰性降级）。"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def client(monkeypatch):
    """不触发 startup(不加载 whisper)的 TestClient。"""
    import lingyi.web_app as webapp
    monkeypatch.setattr(webapp, "GLM_API_KEY", "test-key", raising=True)
    from fastapi.testclient import TestClient
    return TestClient(webapp.app)


# ── 契约: linghealth ai_dispatch.py:154 ──

def test_plan_generate_ok(client, monkeypatch):
    import lingyi.web_app as webapp

    async def fake_glm(messages, stream=False):
        return "固定作息|sleep|每天23点前入睡\\n有氧运动|exercise|每周3次"
    monkeypatch.setattr(webapp, "call_glm", fake_glm)
    r = client.post("/api/v1/plan/generate", json={
        "user_id": "u1", "plan_type": "sleep", "assessment_data": {"sleep": "poor"}})
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "u1" and body["plan_type"] == "sleep"
    assert len(body["plans"]) == 2
    assert body["plans"][0]["title"] == "固定作息"
    assert body["plans"][0]["category"] == "sleep"
    assert "非医疗诊断" in body["disclaimer"]
    assert body["degraded"] is False


def test_plan_generate_degraded_without_key(client, monkeypatch):
    import lingyi.web_app as webapp
    monkeypatch.setattr(webapp, "GLM_API_KEY", "", raising=True)
    r = client.post("/api/v1/plan/generate", json={"user_id": "u1"})
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] is True
    assert len(body["plans"]) >= 3


def test_plan_generate_degraded_on_glm_error(client, monkeypatch):
    import lingyi.web_app as webapp

    async def boom(messages, stream=False):
        raise RuntimeError("glm down")
    monkeypatch.setattr(webapp, "call_glm", boom)
    r = client.post("/api/v1/plan/generate", json={"user_id": "u1"})
    assert r.status_code == 200
    assert r.json()["degraded"] is True


def test_plan_generate_requires_user_id(client):
    r = client.post("/api/v1/plan/generate", json={})
    assert r.status_code == 400


# ── whisper 惰性化: 库缺失时 import 不炸、语音链路可识别降级 ──

def test_whisper_lazy_import_survives_missing_lib(monkeypatch):
    broken = types.ModuleType("whisper")
    def _boom(*a, **k):
        raise ImportError("libtorch_cuda.so: failed to map segment")
    broken.load_model = _boom
    monkeypatch.setitem(sys.modules, "whisper", broken)
    for mod in [m for m in list(sys.modules) if m == "lingyi.web_app"]:
        del sys.modules[mod]
    import lingyi.web_app as webapp  # 不应抛异常
    assert webapp.whisper is broken  # try 导入成功(坏 load_model), 不是 None
    assert webapp.get_whisper_model() is None  # 加载失败 → False → None


def test_whisper_lazy_import_when_module_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "whisper", None)  # None → import 抛 ImportError
    for mod in [m for m in list(sys.modules) if m == "lingyi.web_app"]:
        del sys.modules[mod]
    import lingyi.web_app as webapp
    assert webapp.whisper is None
    assert webapp.get_whisper_model() is None


def test_module_level_import_does_not_need_whisper(monkeypatch):
    monkeypatch.setitem(sys.modules, "whisper", None)
    for mod in [m for m in list(sys.modules) if m == "lingyi.web_app"]:
        del sys.modules[mod]
    import lingyi.web_app as webapp  # 只要不抛, 即证明惰性化生效
    assert hasattr(webapp, "plan_generate")
'''

STAMP = "20261004"


def main() -> int:
    tests_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "tests").expanduser().resolve()
    tests_dir.mkdir(parents=True, exist_ok=True)
    target = tests_dir / "test_webapp_plan.py"
    if target.exists() and STAMP in target.read_text(encoding="utf-8"):
        print("已处理: 测试已是本版(幂等)")
        return 0
    backup = None
    if target.exists():
        backup = target.with_name(f"{target.name}.bak-{STAMP}")
        backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"已备份: {backup}")
    target.write_text(TESTS, encoding="utf-8")
    print(f"已写入: {target} (7 个测试)")
    # 顺带保证 tests 可被发现
    init = tests_dir / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""cap/browser 浏览器动作面插片测试（Phase B，5 原语 + work_claim + DOM hash）。

覆盖（12 测试，对齐 agent_lingxi/cap_infer/os_resource 范式）：
1. manifest N2 双声明（T2 + L1）
2. 缝 key 域前缀（cap/browser，N3）
3. stop_layer 三要素（铁律 2 细则 5）
4. list_tabs 只读（CDP /json/list stub）
5. page_load 只读免锁（playwright stub）
6. 写操作锁被他人持有 → denied（N4 互斥查，铁律 8 操作域）
7. 写操作成功锚定 DOM hash 变化（J5 行为级：页面状态确实变了）
8. 写操作 hash 未变 → ineffective（exit 成功 ≠ 页面变了，mcp-wrap 踩坑 #4 浏览器版）
9. 动作完释放锁（不留死锁，铁律 8 时效域 TTL 兜底）
10. 探针失败累计 → absent（N4 缺席查）
11. record 化（browser_action type，J4，含 dom_hash 前后对照）
12. register 入 SeamRegistry + M5 截肢主干照跑

hermetic：playwright/urllib 全 monkeypatch stub，不依赖真实 CDP 9228；
work_claim 用注入 stub 模拟持有/释放。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.core.state_store import StateStore

REPO = Path(__file__).parents[2]
MANIFEST = REPO / "lingclaude" / "plugins" / "agents" / "cap_browser" / "manifest.agent.json"


class _FakePage:
    """playwright Page stub：DOM hash 可控（J5 行为锚验证）。"""

    def __init__(self, html_sequence: list[str]):
        self._htmls = list(html_sequence)
        self.url = "https://example.test/page"

    def evaluate(self, expr):
        # 首次调用返回当前 html 并消耗（模拟 DOM 变化）
        return self._htmls.pop(0) if len(self._htmls) > 1 else self._htmls[0]

    def title(self):
        return "Example Page"

    def goto(self, url, timeout=None):
        return None

    def screenshot(self, path=None, full_page=False):
        return b"\x89PNG-fake"

    def click(self, selector):
        return None

    def fill(self, selector, text):
        return None


@pytest.fixture()
def plugin(tmp_path: Path):
    from lingclaude.plugins.agents.cap_browser.plugin import CapBrowserPlugin
    store = StateStore(backend="json", root=tmp_path / "state")
    wc = MagicMock()  # work_claim stub：bind 放行
    wc.bind.return_value = {"ok": True, "claim_id": "claim-1"}
    wc.release.return_value = True
    return CapBrowserPlugin(store=store, work_claim_module=wc)


# ── 1. manifest N2 双声明 ─────────────────────────────────────────────
def test_n2_trust_plug_declared():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert m["trust_level"] == "T2"   # CDP 引擎本机可读，契约审计
    assert m["plug_level"] == "L1"    # CDP 可换 playwright headless


# ── 2. 缝 key 域前缀（N3）────────────────────────────────────────────
def test_n3_namespaced_seam_key():
    from lingclaude.plugins.agents.cap_browser.plugin import CapBrowserPlugin
    assert CapBrowserPlugin.name == "cap/browser"
    assert CapBrowserPlugin.name.split("/", 1)[0] == "cap"


# ── 3. stop_layer 三要素（铁律 2 细则 5）─────────────────────────────
def test_stop_layer_three_elements():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sl = m["stop_layer"]
    for f in ("kernel", "seams", "implementations"):
        assert f in sl
    assert "browser_backend" in sl["seams"]  # 子插片接缝显式声明（J3）


# ── 4. list_tabs 只读（CDP /json/list stub）─────────────────────────
def test_list_tabs(plugin, monkeypatch):
    class FakeResp:
        status = 200
        def read(self):
            return json.dumps([{"id": "t1", "title": "Tab One", "url": "https://a"}]).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr("lingclaude.plugins.agents.cap_browser.plugin.urllib.request.urlopen",
                        lambda *a, **k: FakeResp())
    r = plugin.list_tabs()
    assert r["state"] == "succeeded"
    assert r["count"] == 1
    assert r["tabs"][0]["title"] == "Tab One"


# ── 5. page_load 只读免锁 ────────────────────────────────────────────
def test_page_load(plugin, monkeypatch):
    plugin._page = _FakePage(["<html>one</html>"])
    r = plugin.page_load("https://example.test")
    assert r["state"] == "succeeded"
    assert r["title"] == "Example Page"
    # 只读动作不碰 work_claim（bind 未被调用）
    assert plugin._wc.bind.called is False


# ── 6. 写操作锁被他人持有 → denied（N4 互斥查）──────────────────────
def test_write_denied_when_claim_held(plugin):
    plugin._wc.bind.return_value = {"ok": False, "reason": "claim-held",
                                    "detail": "tab 被他人锁定"}
    plugin._page = _FakePage(["<html>a</html>", "<html>b</html>"])
    r = plugin.click("#btn")
    assert r["state"] == "denied"
    assert r["reason"] == "claim-held"
    rec = plugin._store.load("browser_action", r["run_id"])
    assert rec["state"] == "denied"  # 拒绝也入账（J4 可 query）


# ── 7. 写操作成功锚定 DOM hash 变化（J5 行为级）─────────────────────
def test_write_success_anchored_on_dom_change(plugin):
    # DOM 序列：前快照 <a>，后快照 <b> → hash 变 → succeeded
    plugin._page = _FakePage(["<html>a</html>", "<html>b</html>"])
    r = plugin.fill("#input", "hello")
    assert r["state"] == "succeeded"
    assert r["dom_changed"] is True
    rec = plugin._store.load("browser_action", r["run_id"])
    assert rec["dom_hash_before"] != rec["dom_hash_after"]


# ── 8. hash 未变 → ineffective（exit 成功 ≠ 页面变了）───────────────
def test_write_ineffective_when_dom_unchanged(plugin):
    # DOM 序列：前后快照相同 → hash 不变 → ineffective（不是 succeeded）
    plugin._page = _FakePage(["<html>same</html>", "<html>same</html>"])
    r = plugin.click("#noop")
    assert r["state"] == "ineffective"
    assert r["dom_changed"] is False


# ── 9. 动作完释放锁（不留死锁）───────────────────────────────────────
def test_release_after_action(plugin):
    plugin._page = _FakePage(["<html>a</html>", "<html>b</html>"])
    plugin.click("#btn")
    assert plugin._wc.release.called is True
    # denied 路径不释放（bind 未成功，claim_id=None）
    plugin._wc.reset_mock()
    plugin._wc.bind.return_value = {"ok": False, "reason": "claim-held"}
    plugin.click("#btn")
    assert plugin._wc.release.called is False


# ── 10. 探针失败累计 → absent（N4 缺席查）───────────────────────────
def test_absent_after_consecutive_failures(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "probe", lambda: False)
    s1 = plugin.status()
    assert s1["absent"] is False and s1["probe_failures"] == 1
    s2 = plugin.status()
    assert s2["absent"] is True and s2["probe_failures"] == 2  # ABSENT_THRESHOLD=2


# ── 11. record 化（J4：running → 终态，含 dom_hash 对照）────────────
def test_action_recorded(plugin):
    plugin._page = _FakePage(["<html>x</html>", "<html>y</html>"])
    r = plugin.evaluate("document.title='hi'")
    rec = plugin._store.load("browser_action", r["run_id"])
    assert rec["plugin"] == "cap/browser"
    assert rec["action"] == "evaluate"
    assert rec["state"] in ("succeeded", "ineffective")
    assert "dom_hash_before" in rec and "dom_hash_after" in rec
    assert "started_at" in rec and "done_at" in rec


# ── 12. register + M5 截肢主干照跑 ──────────────────────────────────
def test_register_unregister_mainloop():
    import lingclaude.plugins.agents.cap_browser.plugin as cap_mod
    SeamRegistry.reset()
    cap_mod.register(SeamRegistry)
    try:
        got = SeamRegistry.get(SeamType.AGENT, "cap/browser")
        assert got.name == "cap/browser"
        SeamRegistry.unregister(SeamType.AGENT, "cap/browser")
        assert SeamRegistry.get_optional(SeamType.AGENT, "cap/browser") is None
        SeamRegistry.register(SeamType.TOOL, "bash", object())
        assert SeamRegistry.has(SeamType.TOOL, "bash")
    finally:
        SeamRegistry.reset()

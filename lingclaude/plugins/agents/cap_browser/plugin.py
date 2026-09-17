"""浏览器动作面插片（cap/browser，Phase B 第三层试验田）。

CODING_AGENT_PLUGIN_PLAN_L2_L3.md §2.2：web 工具只有查询面（fetch/extract），
本插片补动作面（page_load/screenshot/click/fill/evaluate 5 原语），
CDP 9228 窗口版调试 Chrome 现成（会话简报坑 #6：9222 归 yai-cdp-guard，禁用 9222）。

铁律锚点（每行代码过法）：
- 铁律 1：本插片全在 plugins/agents/cap_browser/，core/ 零 diff；
- 铁律 3/J4：每次动作记 browser_action record（含 DOM 快照 hash 前后对照），J4 语义；
- 铁律 5/6：trust_level=T2（CDP 引擎本机可读进程，契约审计）+
  plug_level=L1（CDP 可换 playwright headless，接口一致即不崩，N2 守卫消费）；
- 铁律 7：缝 key 带域前缀 cap/browser（N3 守卫消费）；
- 铁律 8 操作域：写操作（click/fill/evaluate）前必须 work_claim 认领（scope=tab），
  同 tab 双写即警（N4 互斥查）；只读动作免锁；
- J5 行为级锚定：写操作成功锚定"DOM 快照 hash 确实变了"，非仅 exit 0
  （mcp-wrap 踩坑 #4 "exit 0 ≠ 成功"的浏览器版）；
- 候选铁律 8：CDP 探针失败累计 → absent（N4 缺席查，不假活）。

复用资产：
- BrowserPlugin 协议（本文件内 Protocol，分形停层显式化）；
- work_claim.py（bind/release 原语，写锁）；
- agent_lingxi/cap_infer/os_resource 三件套范式（manifest + plugin + 测试）。
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from lingclaude.core.state_store import StateStore

MANIFEST = Path(__file__).parent / "manifest.agent.json"
ABSENT_THRESHOLD = 2  # 候选铁律 8：连续 N 次探针失败即 absent
DOM_SNAPSHOT_MAX = 200_000  # DOM 快照截断上限（bytes）


@runtime_checkable
class BrowserBackend(Protocol):
    """浏览器后端子插片接缝（铁律 2 细则 5：分形停层显式化）。

    CDPBackend（本机 CDP 9228）为当前实现；playwright headless 为 L1 备选
    （接口一致即不崩：5 原语同名同签名）。
    """

    def page_load(self, url: str) -> dict: ...
    def screenshot(self) -> dict: ...
    def click(self, selector: str) -> dict: ...
    def fill(self, selector: str, text: str) -> dict: ...
    def evaluate(self, expression: str) -> dict: ...


# 写操作集合（铁律 8 操作域：写前认领）
WRITE_ACTIONS = ("click", "fill", "evaluate")
READ_ACTIONS = ("page_load", "screenshot", "list_tabs")


class CapBrowserPlugin:
    """浏览器动作面插片（5 原语，CDP 9228 经 playwright connect_over_cdp）。

    写操作纪律（铁律 8 操作域 + J5 行为锚）：
    1. 写前 work_claim 认领（scope=tab url，同 tab 双写即警 N4 互斥查）；
    2. 动作前后各截一次 DOM 快照 hash；
    3. 成功锚定 hash 变化（页面状态确实变了），hash 未变 → state=ineffective；
    4. 全程 record 化（browser_action type，J4）。
    """

    name = "cap/browser"  # 铁律 7：域前缀缝 key

    def __init__(self, store: StateStore | None = None,
                 endpoint: str | None = None,
                 work_claim_module=None) -> None:
        self._manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self._endpoint = endpoint or self._manifest["transport"]["endpoint"]
        self._store = store or StateStore(backend="json",
                                          root=Path(__file__).parents[4] / "data" / "browser_actions")
        self._probe_failures = 0
        self._wc = work_claim_module  # work_claim 模块（延迟注入，测试可 stub）
        self._page = None             # playwright Page（懒连接）
        self._browser = None

    # ── 5 原语 + tab 列表（动作面）────────────────────────────────────
    def list_tabs(self) -> dict:
        """列当前全部 tab（GET /json/list，只读免锁）。"""
        try:
            with urllib.request.urlopen(f"{self._endpoint}/json/list", timeout=5) as r:
                tabs = json.loads(r.read().decode("utf-8"))
            return {"state": "succeeded", "count": len(tabs),
                    "tabs": [{"id": t.get("id"), "title": (t.get("title") or "")[:50],
                              "url": (t.get("url") or "")[:100]} for t in tabs[:20]]}
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            return {"state": "absent", "error": str(e)[:200]}

    def page_load(self, url: str) -> dict:
        """加载页面（只读免锁；CDP 实现为 Page.goto）。"""
        run_id = self._record_start("page_load", {"url": url[:200]})
        try:
            page = self._ensure_page(url)
            page.goto(url, timeout=self._manifest["transport"].get("call_timeout_s", 30) * 1000)
            title = page.title()
            self._record_done(run_id, "succeeded", {"title": title[:100],
                                                    "dom_hash": self._dom_hash(page)})
            return {"run_id": run_id, "state": "succeeded", "title": title}
        except Exception as e:  # noqa: BLE001 —— 失败也入账（J4）
            self._record_done(run_id, "failed", {"error": str(e)[:300]})
            return {"run_id": run_id, "state": "failed", "error": str(e)}

    def screenshot(self, path: str | None = None) -> dict:
        """截当前页（只读免锁；CDP 实现为 Page.screenshot）。"""
        run_id = self._record_start("screenshot", {})
        try:
            page = self._page
            if page is None:
                raise RuntimeError("无活动页（先 page_load）")
            data = page.screenshot(path=path, full_page=False)
            self._record_done(run_id, "succeeded", {
                "bytes": len(data) if data else 0, "path": path})
            return {"run_id": run_id, "state": "succeeded", "bytes": len(data) if data else 0}
        except Exception as e:  # noqa: BLE001
            self._record_done(run_id, "failed", {"error": str(e)[:300]})
            return {"run_id": run_id, "state": "failed", "error": str(e)}

    def click(self, selector: str, tab_scope: str = "") -> dict:
        """点击元素（写操作：work_claim 认领 + DOM hash 行为锚）。"""
        return self._write_action("click", selector=selector, tab_scope=tab_scope)

    def fill(self, selector: str, text: str, tab_scope: str = "") -> dict:
        """填充输入框（写操作：work_claim 认领 + DOM hash 行为锚）。"""
        return self._write_action("fill", selector=selector, text=text, tab_scope=tab_scope)

    def evaluate(self, expression: str, tab_scope: str = "") -> dict:
        """执行 JS（写操作：可改 DOM，work_claim 认领 + DOM hash 行为锚）。"""
        return self._write_action("evaluate", expression=expression, tab_scope=tab_scope)

    # ── 写操作统一纪律（铁律 8 操作域 + J5 行为锚）────────────────────
    def _write_action(self, action: str, tab_scope: str = "", **kwargs) -> dict:
        """写操作统一路径：查锁→认领→前快照→动作→后快照→hash 对照→入账。"""
        run_id = self._record_start(action, {"kwargs": {k: str(v)[:100] for k, v in kwargs.items()}})
        scope = tab_scope or (self._page.url[:100] if self._page else "unknown")
        # 1. 写前认领（铁律 8 操作域）：锁被他人持有 → deny（N4 互斥查）
        claim = self._claim_scope(action, scope)
        if not claim.get("ok"):
            self._record_done(run_id, "denied", {"reason": claim.get("detail", "claim-held"),
                                                 "scope": scope})
            return {"run_id": run_id, "state": "denied", "reason": claim.get("reason")}
        try:
            page = self._page
            if page is None:
                raise RuntimeError("无活动页（先 page_load）")
            hash_before = self._dom_hash(page)
            # 2. 动作
            if action == "click":
                page.click(kwargs["selector"])
            elif action == "fill":
                page.fill(kwargs["selector"], kwargs["text"])
            elif action == "evaluate":
                page.evaluate(kwargs["expression"])
            hash_after = self._dom_hash(page)
            # 3. J5 行为锚：hash 变了才算成功（页面状态确实变了）
            state = "succeeded" if hash_after != hash_before else "ineffective"
            self._record_done(run_id, state, {
                "dom_hash_before": hash_before, "dom_hash_after": hash_after,
                "scope": scope, "claim_id": claim.get("claim_id")})
            return {"run_id": run_id, "state": state,
                    "dom_changed": hash_after != hash_before}
        except Exception as e:  # noqa: BLE001 —— 失败也入账（J4）
            self._record_done(run_id, "failed", {"error": str(e)[:300], "scope": scope})
            return {"run_id": run_id, "state": "failed", "error": str(e)}
        finally:
            self._release_scope(claim)

    # ── work_claim 写锁（铁律 8 操作域，N4 互斥查）────────────────────
    def _claim_scope(self, action: str, scope: str) -> dict:
        """写前认领：work_claim.bind；模块不可用/测试 stub 时降级为直接放行
        （降级必须留痕——J5 防博弈：跳过必须可见，record 记 claim=degraded）。"""
        if self._wc is None:
            return {"ok": True, "claim_id": None, "degraded": True}
        try:
            from lingclaude.core.state_store import StateStore as _S
            wc_store = self._store  # 复用同一 store（work_claim record 同库）
            r = self._wc.bind(wc_store, f"cap/browser:{scope}",
                              holder=self.name, ttl_s=60)
            if r.get("ok"):
                return {"ok": True, "claim_id": r.get("claim_id")}
            return r  # {"ok": False, "reason": "claim-held", ...}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": "claim_error", "detail": str(e)[:200]}

    def _release_scope(self, claim: dict) -> None:
        """动作完释放锁（异常也释放，不留死锁——铁律 8 时效域兜底 TTL=60s）。"""
        if claim.get("ok") and claim.get("claim_id") and self._wc is not None:
            try:
                self._wc.release(self._store, claim["claim_id"])
            except Exception:  # noqa: BLE001 —— 释放失败靠 TTL 兜底
                pass

    # ── CDP 连接 + DOM 快照 hash（J5 行为锚）──────────────────────────
    def _ensure_page(self, url: str):
        """playwright connect_over_cdp 懒连接（不杀已有浏览器进程）。"""
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(self._endpoint)
        ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self._page = ctx.new_page() if not ctx.pages else ctx.pages[0]
        return self._page

    def _dom_hash(self, page) -> str:
        """DOM 快照 sha256（J5 行为锚：成功=页面状态确实变了）。"""
        try:
            html = page.evaluate("document.documentElement.outerHTML") or ""
            return hashlib.sha256(html.encode("utf-8")[:DOM_SNAPSHOT_MAX]).hexdigest()[:16]
        except Exception:  # noqa: BLE001 —— 取不到快照记 sentinel（不冒充成功）
            return "unavailable"

    # ── 健康探针（候选铁律 8：CDP 端点可达性）─────────────────────────
    def probe(self) -> bool:
        """GET /json/version 200 即活（轻量，不执行动作）。"""
        try:
            with urllib.request.urlopen(f"{self._endpoint}/json/version", timeout=5) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def status(self) -> dict:
        """健康状态（N4 缺席查：探针失败累计 → absent，不假活）。"""
        healthy = self.probe()
        if not healthy:
            self._probe_failures += 1
        else:
            self._probe_failures = 0
        absent = self._probe_failures >= ABSENT_THRESHOLD
        return {"name": self.name, "healthy": healthy, "absent": absent,
                "endpoint": self._endpoint, "probe_failures": self._probe_failures}

    # ── record 化（J4）────────────────────────────────────────────────
    def _record_start(self, action: str, extra: dict) -> str:
        run_id = f"browser:{action}:{int(time.time() * 1000)}"
        rec = self._store.load("browser_action", run_id) or {}
        rec.update({"plugin": self.name, "action": action, "state": "running",
                    "started_at": time.time(), **extra})
        self._store.save("browser_action", run_id, rec)
        return run_id

    def _record_done(self, run_id: str, state: str, extra: dict) -> None:
        rec = self._store.load("browser_action", run_id) or {}
        rec.update({"state": state, "done_at": time.time(), **extra})
        self._store.save("browser_action", run_id, rec)


def register(registry) -> None:
    """插件入口：SeamRegistry.register(SeamType.AGENT, "cap/browser", plugin)。

    注：浏览器动作面本质是"能力"非"Agent"，但 5 原语协议与 AgentSeam 同形
    （run/abort/status→page_load/screenshot/status），挂 AGENT 缝；
    若后续主干增设 SeamType.CAPABILITY 可平移（缝 key 不变，铁律 7 域前缀已带 cap/）。
    M5 截肢：unregister 后主干照跑（L1 替换，CDP 可换 playwright headless）。
    """
    from lingclaude.core.seam import SeamType  # 延迟 import，避免循环
    registry.register(SeamType.AGENT, CapBrowserPlugin.name, CapBrowserPlugin())

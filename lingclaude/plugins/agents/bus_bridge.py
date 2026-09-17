"""LingBus → Agent 插片路由桥（bus_bridge）。

把"poll 到通知"翻译成"在册插片调用"，补齐 LingBus 通知 → 自动加载 → 干活
→ 回话 的最后一环。铁律锚点：
- 铁律 1：纯插件（plugins/agents/），core/ 零 diff；
- 铁律 3/J4：每次路由决策与执行结果全 record 化（bus_route type）；
- 铁律 5：路由表 record 化（bus_route_table），守卫可 query；
- 铁律 6/7：放行前查 agent_registry（N2 trust/plug 声明）+ 域前缀缝 key（N3）；
- 候选铁律 8：absent 插片不放行（不假活）。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from lingclaude.core.state_store import StateStore


class BusBridge:
    """LingBus 消息 → 在册 Agent 插片 的路由桥。

    poller: 返回消息列表的回调（对接 ling-term-mcp poll_messages）；
    消息结构（实测）：{thread_id, subject, body, channel, timestamp, ...}。
    """

    def __init__(
        self,
        store: StateStore,
        poller: Callable[[], list[dict]] | None = None,
        runner: Any | None = None,  # SeamRegistry 或等价分发器（测试可注入 stub）
    ) -> None:
        self._store = store
        self._poller = poller or (lambda: [])
        self._runner = runner
        # 路由表 record 化（铁律 5）：bus_route_table/<topic> → 插片缝 key
        self._routes: dict[str, str] = {}
        for key in store.list_keys("bus_route_table") or []:
            rec = store.load("bus_route_table", key)
            if rec and rec.get("plugin"):
                self._routes[key] = rec["plugin"]

    # ── 路由表管理（record 化，变更即入账）────────────────────────────
    def bind(self, topic: str, plugin_key: str) -> dict:
        """绑定 topic → 插片。放行前查在册与缺席（N2/N3/候选铁律 8）。"""
        reg = self._store.load("agent_registry", plugin_key)
        if not reg:
            return self._deny(topic, plugin_key, "not_registered",
                              f"{plugin_key} 不在 agent_registry（N2 拒收）")
        if reg.get("state") == "absent":
            return self._deny(topic, plugin_key, "absent",
                              f"{plugin_key} absent（候选铁律 8：不假活）")
        self._routes[topic] = plugin_key
        self._store.save("bus_route_table", topic, {
            "plugin": plugin_key,
            "trust_level": reg.get("trust_level"),
            "plug_level": reg.get("plug_level"),
            "bound_at": time.time(),
        })
        return {"ok": True, "topic": topic, "plugin": plugin_key}

    def unbind(self, topic: str) -> bool:
        """解除绑定（路由表变更留痕：state=unbound，不删账）。"""
        if topic not in self._routes:
            return False
        plugin = self._routes.pop(topic)
        rec = self._store.load("bus_route_table", topic) or {}
        rec.update({"plugin": plugin, "state": "unbound", "unbound_at": time.time()})
        self._store.save("bus_route_table", topic, rec)
        return True

    # ── 主循环：poll → 路由 → 执行 → 回写 ────────────────────────────
    def tick(self) -> list[dict]:
        """poll 一次消息，逐条路由执行。返回本 tick 的处置账。"""
        handled = []
        for msg in self._poller():
            topic = self._topic_of(msg)
            plugin_key = self._routes.get(topic)
            route_id = f"{topic}:{int(time.time())}:{len(str(msg))}"
            if not plugin_key:
                handled.append(self._route_record(route_id, topic, None,
                                                  "no_route", msg))
                continue
            result = self._dispatch(plugin_key, msg)
            handled.append(self._route_record(route_id, topic, plugin_key,
                                              result.get("state", "failed"), msg,
                                              error=result.get("error")))
        return handled

    # ── 内部 ───────────────────────────────────────────────────────────
    def _dispatch(self, plugin_key: str, msg: dict) -> dict:
        """执行插片 run；runner 不可用/抛错也必须返回结构化结果（J4）。"""
        task = f"[{msg.get('subject', '')}] {msg.get('body', '')}"[:500]
        try:
            if self._runner is None:
                return {"state": "no_runner"}
            plugin = self._runner.get("AGENT", plugin_key) \
                if hasattr(self._runner, "get") else self._runner
            return plugin.run(task)
        except Exception as e:  # noqa: BLE001 —— 失败也入账
            return {"state": "failed", "error": str(e)[:200]}

    def _route_record(self, route_id: str, topic: str, plugin_key: str | None,
                      state: str, msg: dict, error: str | None = None) -> dict:
        """J4：路由决策与执行结果全入账（bus_route type），失败原因一并留痕。"""
        rec = {
            "topic": topic,
            "plugin": plugin_key,
            "state": state,
            "error": (error or "")[:200],
            "subject": msg.get("subject", "")[:100],
            "thread_id": msg.get("thread_id"),
            "routed_at": time.time(),
        }
        self._store.save("bus_route", route_id, rec)
        return rec

    def _deny(self, topic: str, plugin_key: str, reason: str, detail: str) -> dict:
        """绑定被拒也入账（守卫拦截必须可 query）。"""
        self._store.save("bus_route_table", topic, {
            "plugin": plugin_key, "state": f"denied:{reason}",
            "detail": detail, "denied_at": time.time(),
        })
        return {"ok": False, "reason": reason, "detail": detail}

    @staticmethod
    def _topic_of(msg: dict) -> str:
        """消息 → 路由 topic：优先显式 topic 字段，回退 channel。"""
        return msg.get("topic") or msg.get("channel") or "unrouted"

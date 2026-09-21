"""插片自省面（cap/inspect）—— DSH cordis_inspect 的 lc 对位。

P1-8（2026-09-21，全 15 家精读 §3.2）：lc 的探活只能外部 wake 做（灵知/
灵通问道 9-19 会议探活暴露「依赖已死而消费方还在」），会话内无自省面——
模型自己查不到「当前 25 插片哪些注册了、哪个 provider 熔断了、
凭据池还剩几个账号」。本插片提供**只读自省**（无 I/O 副作用，T1 信任级）：
- SeamRegistry.snapshot()：当前注册的插片树（按 seam type 分组）；
- TaskRouter 熔断 slot：哪些 provider 在冷却、冷却到何时；
- CredentialPool.pool_status：哪些账号可用/熔断/独占。

铁律锚点：
- 铁律 1：全在 plugins/agents/cap_inspect/，core/ 零 diff（只 import 现有 API）；
- 铁律 5：trust_level=T1（只读）+ plug_level=L1（unregister 主干照跑）；
- 铁律 7：缝 key cap/inspect（域前缀）；
- 铁律 8：任何数据源缺失 → 降级只报可用面（fail-soft，不炸整个自省）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

MANIFEST = Path(__file__).parent / "manifest.agent.json"


class PluginsInspectPlugin:
    """只读自省插片：聚合三路数据源（SeamRegistry / TaskRouter / CredentialPool）。

    用法（会话内调用）：
        p = PluginsInspectPlugin()
        p.inspect()                      # 全量自省
        p.list_plugins()                 # 只查插片树
        p.router_health()                # 只查路由熔断
        p.credential_pool_status()       # 只查凭据池
    """

    name = "cap/inspect"  # 铁律 7：域前缀缝 key

    def __init__(
        self,
        registry: Any = None,
        task_router: Any = None,
        credential_pool: Any = None,
    ) -> None:
        # 三路数据源可注入（测试 fake）；None 时惰性取默认 / 降级 None
        self._registry = registry
        self._task_router = task_router
        self._pool = credential_pool
        self._manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    # ── 数据源惰性解析（fail-soft：拿不到就降级 None） ──
    # 访问器方法名不得与 __init__ 注入的属性同名（_registry/_task_router/_pool），
    # 否则实例属性遮蔽方法 → self._registry() 变 None() 报错。统一 _get_* 前缀。

    def _get_registry(self):
        if self._registry is not None:
            return self._registry
        try:
            from lingclaude.core.seam import SeamRegistry
            return SeamRegistry
        except Exception:
            return None

    def _get_router(self):
        return self._task_router

    def _get_pool(self):
        return self._pool

    # ── 自省面（每个方法独立 fail-soft） ──

    def list_plugins(self) -> dict[str, Any]:
        """SeamRegistry 插片树快照（哪些 seam type 下注册了哪些插片）。"""
        reg = self._get_registry()
        if reg is None:
            return {"available": False, "reason": "SeamRegistry 不可用"}
        try:
            snap = reg.snapshot()  # {seam_type.value: [names]}
            total = sum(len(v) for v in snap.values())
            return {
                "available": True,
                "total_plugins": total,
                "by_seam": snap,
                "seam_types": sorted(snap.keys()),
            }
        except Exception as e:
            return {"available": True, "error": str(e)[:200]}

    def plugin_status(self, plugin_name: str) -> dict[str, Any]:
        """单个插片的注册/协议校验状态（对齐 SeamRegistry.check_protocol）。"""
        reg = self._get_registry()
        if reg is None:
            return {"available": False}
        try:
            from lingclaude.core.seam import SeamType
            found: list[str] = []
            for st in SeamType:
                if reg.has(st, plugin_name):
                    found.append(st.value)
            registered = bool(found)
            protocol_gaps: list[str] = []
            for st in SeamType:
                if reg.has(st, plugin_name):
                    inst = reg.get(st, plugin_name)
                    protocol_gaps.extend(reg.check_protocol(st, inst))
            return {
                "plugin": plugin_name,
                "registered": registered,
                "seams": found,
                "protocol_gaps": protocol_gaps,
            }
        except Exception as e:
            return {"plugin": plugin_name, "error": str(e)[:200]}

    def router_health(self) -> dict[str, Any]:
        """TaskRouter 熔断 slot 自省（哪些 provider 在冷却、冷却到何时）。"""
        router = self._get_router()
        if router is None:
            return {"available": False, "reason": "TaskRouter 未注入"}
        try:
            slots = getattr(router, "_slots", {})
            out: dict[str, Any] = {}
            now = time.monotonic()
            for pname, slot in (slots or {}).items():
                in_cd = getattr(slot, "cooldown_until", 0) > now
                out[pname] = {
                    "in_cooldown": in_cd,
                    "cooldown_remaining_s": int(slot.cooldown_until - now) if in_cd else 0,
                    "consecutive_errors": getattr(slot, "consecutive_errors", 0),
                    "total_errors": getattr(slot, "total_errors", 0),
                    "available": getattr(slot, "is_available", not in_cd),
                }
            cooled = [k for k, v in out.items() if v["in_cooldown"]]
            return {"available": True, "providers": out, "cooled": cooled}
        except Exception as e:
            return {"available": True, "error": str(e)[:200]}

    def credential_pool_status(self) -> dict[str, Any]:
        """凭据池自省（哪些 provider 挂了几个账号、各账号可用/熔断/独占）。"""
        pool = self._get_pool()
        if pool is None:
            return {"available": False, "reason": "CredentialPool 未注入"}
        try:
            out: dict[str, Any] = {}
            for provider in pool.providers():
                out[provider] = pool.pool_status(provider)
            return {"available": True, "pools": out}
        except Exception as e:
            return {"available": True, "error": str(e)[:200]}

    def inspect(self) -> dict[str, Any]:
        """全量自省：三路聚合（单路失败不影响其余，fail-soft）。"""
        return {
            "inspected_at": time.time(),
            "plugins": self.list_plugins(),
            "router_health": self.router_health(),
            "credential_pool": self.credential_pool_status(),
        }

    # ── ProviderPlugin 协议面（可挂 SeamRegistry，L1 可拔插） ──

    def create(self, config: Any = None) -> dict:
        """create 语义 = 发起一次自省（config 可指定 inspect 哪个面）。"""
        config = config or {}
        face = config.get("face", "all")
        if face == "plugins":
            return self.list_plugins()
        if face == "router":
            return self.router_health()
        if face == "pool":
            return self.credential_pool_status()
        if face == "plugin":
            return self.plugin_status(config.get("plugin", ""))
        return self.inspect()

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "read_only": True,
            "capabilities": self._manifest.get("capabilities"),
        }


def register(registry) -> None:
    """插片入口：注册到 SeamRegistry（L1 可拔插，unregister 主干照跑）。

    只读元能力/观测面，挂 SeamType.RESOURCE（资源探针语义，铁律 8 隔离
    故障域 + N4 缺席查——观测与 probe 同域，命名 cap/inspect 防混淆）。
    """
    from lingclaude.core.seam import SeamType
    registry.register(SeamType.RESOURCE, PluginsInspectPlugin.name, PluginsInspectPlugin())

# lingclaude/model/credential_pool.py
"""P1-7（2026-09-21，全 15 家精读 §3.2 hermes-studio 式凭据池）。

GLM 1310 周期限额的根治：硬配额熔断（cbff31f）只能「解析重置时刻 → 冷却
到点」，是止血——配额到点仍可能撞月度/账号上限。hermes-studio 的
credential_pool 数组（同 provider 多账号 LRU 轮转 + 独占平台凭据剥离）
是根治：一个 provider 配额耗尽，自动切下一个账号续跑，而非干等重置。

设计（lc 适配）：
- 一个 provider 可挂 N 个账号（同 base_url/model，不同 api_key）；
- 路由层选中的 provider 命中凭据池 → 按 LRU 取下一个未熔断账号；
- 某账号撞 1310/1308 硬配额 → 该账号进冷却，池轮转下一账号；
- 全池熔断 → 才回落到 task_router 的跨 provider 熔断（既有语义不变）；
- 独占凭据剥离：绑定到单一平台的 key（如某套餐专属端点）不参与公共池
  轮转（标记 exclusive=True），避免把专属额度混进共享池被轮走。

与 task_router 的分工（不替换，叠加）：
- task_router 管「选哪个 provider」；本池管「这个 provider 用哪个账号」。
- 接入点：TaskRouter.resolve 返回 ModelConfig 后，凭据池可对 api_key
  做池内轮转（provider 不变、换账号）。默认关闭（空池=单账号旧行为），
  配置里挂多账号才生效。

停层声明（铁律 2 细则 5）：
- 内核 = CredentialPool（账号 LRU + 每账号熔断窗）
- 接缝 = next_key(provider) / record_exhausted(provider, key) 协议
- 实现 = 单实现（内存池 + 可选落盘），预留 config 多账号来源
边界纪律：池只换 api_key（同 provider 语义不变），不跨 provider；
独占账号（exclusive）绝不进公共轮转。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PoolAccount:
    """凭据池里的一个账号（同 provider 的某 api_key）。"""
    api_key: str
    exclusive: bool = False   # 独占平台凭据：不参与公共 LRU 轮转
    label: str = ""           # 人类可读标记（如 "glm-账号A-coding套餐"）
    # 熔断窗（撞硬配额后冷却到此时刻，期间轮转跳过）
    cooldown_until: float = 0.0
    last_used: float = 0.0
    exhaust_count: int = 0    # 累计耗尽次数（LRU 淘汰权重参考）


class CredentialPool:
    """provider 级凭据池（多账号 LRU 轮转 + 每账号熔断窗）。

    用法：
        pool = CredentialPool()
        pool.add_accounts("glm", ["key-A", "key-B"], labels=["账号A", "账号B"])
        key = pool.next_key("glm")          # 取下一个未熔断账号（LRU）
        pool.record_exhausted("glm", key)   # 该账号撞配额 → 进冷却
        # 全池熔断时 next_key 返回 None → 调用方回落 task_router 跨 provider 熔断
    """

    _DEFAULT_COOLDOWN_S = 3600.0  # 账号耗尽默认冷却 1h（无重置时刻时的兜底）

    def __init__(self, default_cooldown_s: float | None = None) -> None:
        self._pools: dict[str, list[PoolAccount]] = {}
        self._lru: "OrderedDict[str, OrderedDict[str, float]]" = {}  # provider → (key → ts)
        self._default_cooldown = default_cooldown_s or self._DEFAULT_COOLDOWN_S
        # round-robin 游标：provider → 下一个扫描起点（比 OrderedDict LRU 更直观，
        # 避免 move_to_end + reversed 组合导致恒选同一个 key 的 bug）。
        self._cursor: dict[str, int] = {}

    # ── 装配 ──

    def add_accounts(
        self,
        provider: str,
        api_keys: list[str],
        *,
        labels: list[str] | None = None,
        exclusive: list[bool] | None = None,
    ) -> None:
        """给 provider 挂 N 个账号（空池=单账号，不改变旧行为）。"""
        labels = labels or [f"{provider}-acct{i+1}" for i in range(len(api_keys))]
        exclusive = exclusive or [False] * len(api_keys)
        existing = {a.api_key for a in self._pools.get(provider, [])}
        pool = self._pools.setdefault(provider, [])
        for i, key in enumerate(api_keys):
            if key in existing:
                continue
            pool.append(PoolAccount(
                api_key=key,
                exclusive=bool(exclusive[i]) if i < len(exclusive) else False,
                label=labels[i] if i < len(labels) else f"{provider}-acct{i+1}",
            ))
        # round-robin 游标初始化为 0（从池首开始扫描）
        self._cursor.setdefault(provider, 0)

    # ── 轮转 ──

    def next_key(
        self,
        provider: str,
        *,
        cooldown_seconds: float | None = None,
        reset_at: float | None = None,
    ) -> str | None:
        """取下一个可用账号（LRU 顺序，跳过冷却中 + 独占账号）。

        全池不可用返回 None（调用方回落 task_router 跨 provider 熔断）。
        单账号池且该账号在冷却 → 仍返回它（无备选，宁可撞墙也不空池）。
        """
        pool = self._pools.get(provider)
        if not pool:
            return None
        now = time.monotonic()
        single = len(pool) == 1
        n = len(pool)
        start = self._cursor.get(provider, 0) % n
        # round-robin 游标扫描：从上次选中点之后开始，跳过冷却/独占账号。
        for offset in range(n):
            idx = (start + offset) % n
            acct = pool[idx]
            key = acct.api_key
            # 独占账号不进公共轮转（多账号池有备选时跳过；单账号池无备选才用它）
            if acct.exclusive and not single:
                continue
            # 冷却中且还有别的账号 → 跳过
            if acct.cooldown_until > now and not single:
                continue
            # 选中：游标推进到下一个，避免连续取同一账号
            self._cursor[provider] = (idx + 1) % n
            acct.last_used = now
            return key
        # 全冷却/独占全跳过 → 单账号池返回唯一账号（撞墙），多账号池返回 None
        if single:
            return pool[0].api_key
        return None

    def record_exhausted(
        self,
        provider: str,
        key: str,
        *,
        reset_at: float | None = None,
        cooldown_seconds: float | None = None,
    ) -> None:
        """某账号撞配额 → 进冷却窗。

        reset_at（绝对时间戳，epoch）优先：冷却到配额重置时刻；
        否则 cooldown_seconds（相对）；都无则默认 1h。
        """
        now = time.monotonic()
        acct = next((a for a in self._pools.get(provider, []) if a.api_key == key), None)
        if acct is None:
            return
        if reset_at is not None:
            acct.cooldown_until = reset_at + 60.0  # +60s 缓冲（对齐 task_router 语义）
        else:
            cd = cooldown_seconds if cooldown_seconds is not None else self._default_cooldown
            acct.cooldown_until = now + cd
        acct.exhaust_count += 1
        logger.warning(
            "credential_pool: %s 账号 %s 配额耗尽（第 %d 次），冷却到 %s",
            provider, acct.label or key[:6] + "…", acct.exhaust_count,
            "重置时刻" if reset_at is not None else f"{cd}s",
        )

    # ── 探活 / 自省面 ──

    def pool_status(self, provider: str) -> dict[str, Any]:
        """池当前状态（lc_plugins_inspect 可消费）。"""
        now = time.monotonic()
        pool = self._pools.get(provider, [])
        accounts = []
        for a in pool:
            in_cd = a.cooldown_until > now
            accounts.append({
                "label": a.label or a.api_key[:6] + "…",
                "exclusive": a.exclusive,
                "in_cooldown": in_cd,
                "cooldown_remaining_s": int(a.cooldown_until - now) if in_cd else 0,
                "exhaust_count": a.exhaust_count,
                "last_used": a.last_used,
            })
        available = sum(
            1 for a in pool
            if a.cooldown_until <= now and not (a.exclusive and len(pool) > 1)
        )
        return {
            "provider": provider,
            "accounts": accounts,
            "available": available,
            "total": len(pool),
            "fully_exhausted": (len(pool) > 0 and available == 0),
        }

    def providers(self) -> list[str]:
        return list(self._pools.keys())

"""灵元 P3: 轻通道运行时 —— 4 件无状态插片的 digest 惰性重建持有者。

每个轻通道对象由本模块持「当前实例 + 上次 digest」；每次访问先比 PolicyLoader
策略 digest，变化才重建（无 lease/排空，轻通道语义）。策略改动经 mtime watch
热更，下个访问点生效 —— 策略调参零重启。

对位主方案 §3.2 + §五：轻通道走 PolicyLoader digest 变化 → 调用方惰性重建。
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from lingclaude.core import light_channel


class _LazyChannel:
    """单轻通道惰性重建单元（实例 + digest 比对）。"""

    def __init__(self, build: Callable[[], Any], digest: Callable[[], str]) -> None:
        self._build = build
        self._digest_fn = digest
        self._lock = threading.Lock()
        self._instance: Any = None
        self._last_digest: str | None = None

    def get(self) -> Any:
        d = self._digest_fn()
        # 快路径：digest 未变直接返回（无锁读；实例替换原子性由 GIL 保证）
        if self._instance is not None and d == self._last_digest:
            return self._instance
        with self._lock:
            # 双检：等锁期间可能已被其他线程重建
            d = self._digest_fn()
            if self._instance is not None and d == self._last_digest:
                return self._instance
            self._instance = self._build()
            self._last_digest = d
            return self._instance

    @property
    def instance(self) -> Any:
        return self.get()


class LightChannelRuntime:
    """CodingRuntime 的 4 个轻通道插片持有者（verification/pattern/loop/cadence）。"""

    def __init__(self, verification_base: Any = None) -> None:
        # verification_gate：config.verification（P0 基线）+ 策略外置叠加，
        # digest 变化 → from_config 重建 gate（轻通道，策略调参零重启）。
        self._verification_base = verification_base
        self.verification_gate = _LazyChannel(
            self._build_gate, self._verification_digest
        )
        self.pattern_recognizer = _LazyChannel(
            light_channel.build_pattern_recognizer, light_channel.pattern_recognizer_digest
        )
        self.loop_detector = _LazyChannel(
            light_channel.build_loop_detector, light_channel.loop_detector_digest
        )
        self.verify_cadence = _LazyChannel(
            light_channel.build_verify_cadence, light_channel.verify_cadence_digest
        )

    def _build_gate(self) -> Any:
        from lingclaude.engine.verification_gate import VerificationGate

        return VerificationGate.from_config(
            light_channel.build_verification_config(self._verification_base)
        )

    def _verification_digest(self) -> str:
        return light_channel.verification_digest(self._verification_base)


# ---------------------------------------------------------------------------
# 保形状包装器：代理到惰性重建的内部实例，对外形状与裸实例一致（消费点零改动）
# ---------------------------------------------------------------------------
class _LazyWrapper:
    """轻通道保形状包装基类：读/写都穿透到当前内部实例（消费点零改动）。

    __getattr__ 代理读；__setattr__ 代理写（除 _channel 自身外）——这样测试/
    插片对 runtime.verification_gate.max_tool_calls_per_session = N 这类实例
    赋值会写穿到当前 gate，而非包装器自身。
    """

    def __init__(self, channel: "_LazyChannel") -> None:
        object.__setattr__(self, "_channel", channel)

    def _target(self) -> Any:
        return self._channel.get()

    def __getattr__(self, item: str) -> Any:
        return getattr(self._target(), item)

    def __setattr__(self, key: str, value: Any) -> None:
        if key == "_channel":
            object.__setattr__(self, key, value)
        else:
            setattr(self._target(), key, value)


class LazyVerificationGate(_LazyWrapper):
    """verification_gate 轻通道保形状包装（策略调参零重启，消费点零改动）。"""


class LazyPatternRecognizer(_LazyWrapper):
    """pattern_recognizer 轻通道保形状包装。"""


class LazyLoopDetector(_LazyWrapper):
    """loop_detector 轻通道保形状包装（streak 计数声明易失，swap 清零可接受）。"""


class LazyVerifyCadence(_LazyWrapper):
    """verify_cadence 轻通道保形状包装。"""

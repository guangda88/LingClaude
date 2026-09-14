"""P9 (2026-09-14): 策略热更消费面补强 —— behavior_aware_router / l10_a_post_audit。

验收口径（灵元 P1 热更收敛）:
- BehaviorRoutingConfig: 构造时实时读策略（PolicyLoader mtime watch），
  hot_reload() 支持已有实例强制刷新未显式设置字段
- BehaviorAwareRouter.route() 每次路由前检查策略热更（改 behavior_router.yaml
  下个 turn 生效，进程不重启）
- BehaviorAwareRouter.get_config()/update_config() 修复（P9 附带真实缺陷：
  __init__ 设 self.config 但 getter/updater 用 self._config → AttributeError）
- DeclarationExtractor.extract() 每次提取前检查 claim_patterns 策略热更；
  自定义 patterns 与灵极优模式不参与刷新
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lingclaude.core import policy_loader as pl
from lingclaude.model.behavior_aware_router import (
    BehaviorAwareRouter,
    BehaviorRouterStrategy,
    BehaviorRoutingConfig,
)
from lingclaude.core.l10_a_post_audit import DeclarationExtractor


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch):
    pl.reset()
    yield
    pl.reset()


# ---------------------------------------------------------------------------
# BehaviorRoutingConfig / BehaviorAwareRouter 热更
# ---------------------------------------------------------------------------


def test_behavior_config_hot_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """hot_reload(): 已有 config 实例改 YAML 后强制刷新未显式设置字段。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    policy = tmp_path / "behavior_router.yaml"
    policy.write_text(
        "default_strategy: standard\n"
        "hallucination:\n"
        "  high_threshold: 0.7\n"
        "  medium_threshold: 0.3\n"
        "frustration:\n"
        "  high_threshold: 0.5\n"
        "  medium_threshold: 0.2\n"
        "error_rate:\n"
        "  high_threshold: 0.4\n"
        "  medium_threshold: 0.2\n"
        "model_priority:\n"
        "  hallucination: 1.0\n"
        "  frustration: 0.8\n"
        "  error: 0.6\n",
        encoding="utf-8",
    )

    try:
        config = BehaviorRoutingConfig()
        assert config.high_hallucination_threshold == 0.7

        # 改 YAML：high_hallucination_threshold 0.7 -> 0.9
        policy.write_text(
            "default_strategy: standard\n"
            "hallucination:\n"
            "  high_threshold: 0.9\n"
            "  medium_threshold: 0.3\n"
            "frustration:\n"
            "  high_threshold: 0.5\n"
            "  medium_threshold: 0.2\n"
            "error_rate:\n"
            "  high_threshold: 0.4\n"
            "  medium_threshold: 0.2\n"
            "model_priority:\n"
            "  hallucination: 1.0\n"
            "  frustration: 0.8\n"
            "  error: 0.6\n",
            encoding="utf-8",
        )
        assert config.hot_reload() is True
        assert config.high_hallucination_threshold == 0.9
        # 未显式设置字段全部刷新
        assert config.medium_hallucination_threshold == 0.3
        assert config.default_strategy == BehaviorRouterStrategy.STANDARD
    finally:
        monkeypatch.undo()


def test_behavior_config_hot_reload_preserves_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """hot_reload(): 显式传值的字段不被 YAML 覆盖。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    policy = tmp_path / "behavior_router.yaml"
    policy.write_text(
        "hallucination:\n"
        "  high_threshold: 0.7\n"
        "  medium_threshold: 0.3\n"
        "frustration:\n"
        "  high_threshold: 0.5\n"
        "  medium_threshold: 0.2\n"
        "error_rate:\n"
        "  high_threshold: 0.4\n"
        "  medium_threshold: 0.2\n"
        "model_priority:\n"
        "  hallucination: 1.0\n"
        "  frustration: 0.8\n"
        "  error: 0.6\n",
        encoding="utf-8",
    )

    try:
        config = BehaviorRoutingConfig(high_hallucination_threshold=0.85)
        assert config.high_hallucination_threshold == 0.85  # 显式值

        # YAML 改成 0.99，显式值应保留
        policy.write_text(
            "hallucination:\n"
            "  high_threshold: 0.99\n"
            "  medium_threshold: 0.3\n"
            "frustration:\n"
            "  high_threshold: 0.5\n"
            "  medium_threshold: 0.2\n"
            "error_rate:\n"
            "  high_threshold: 0.4\n"
            "  medium_threshold: 0.2\n"
            "model_priority:\n"
            "  hallucination: 1.0\n"
            "  frustration: 0.8\n"
            "  error: 0.6\n",
            encoding="utf-8",
        )
        config.hot_reload()
        assert config.high_hallucination_threshold == 0.85  # 保留显式值
        # 未显式设置字段仍刷新
        assert config.medium_hallucination_threshold == 0.3
    finally:
        monkeypatch.undo()


def test_behavior_router_route_hot_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """route() 每次路由前检查策略热更：改 YAML 后下个 turn 生效。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    policy = tmp_path / "behavior_router.yaml"
    # 初始：高幻觉阈值 0.7（默认）
    policy.write_text(
        "hallucination:\n"
        "  high_threshold: 0.7\n"
        "  medium_threshold: 0.3\n"
        "frustration:\n"
        "  high_threshold: 0.5\n"
        "  medium_threshold: 0.2\n"
        "error_rate:\n"
        "  high_threshold: 0.4\n"
        "  medium_threshold: 0.2\n"
        "model_priority:\n"
        "  hallucination: 1.0\n"
        "  frustration: 0.8\n"
        "  error: 0.6\n",
        encoding="utf-8",
    )

    try:
        router = BehaviorAwareRouter()
        # 首次路由
        router.route("写一个函数")
        assert router.get_config().high_hallucination_threshold == 0.7

        # 改 YAML：0.7 -> 0.95
        policy.write_text(
            "hallucination:\n"
            "  high_threshold: 0.95\n"
            "  medium_threshold: 0.3\n"
            "frustration:\n"
            "  high_threshold: 0.5\n"
            "  medium_threshold: 0.2\n"
            "error_rate:\n"
            "  high_threshold: 0.4\n"
            "  medium_threshold: 0.2\n"
            "model_priority:\n"
            "  hallucination: 1.0\n"
            "  frustration: 0.8\n"
            "  error: 0.6\n",
            encoding="utf-8",
        )
        # 下个 route 自动热更（hot_reload 内部强制重读）
        # 注意: policy_get 有 30s 节流，测试中清掉节流时间戳模拟节流周期已过
        pl._LAST_CHECK_BY_PATH.pop(str(policy.resolve()), None)
        router.route("写一个函数")
        assert router.get_config().high_hallucination_threshold == 0.95
    finally:
        monkeypatch.undo()


def test_get_config_fixed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """P9 附带修复：get_config()/update_config() 不再 AttributeError，update 真正生效。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    # 空策略目录 → 回退内置默认
    try:
        router = BehaviorAwareRouter()
        # 之前: AttributeError（self._config 未初始化）
        cfg = router.get_config()
        assert cfg.high_hallucination_threshold == 0.7  # 内置默认

        # update_config 真正生效（之前只更新 _config，route 用 self.config 旧值）
        new_cfg = BehaviorRoutingConfig(high_hallucination_threshold=0.9)
        router.update_config(new_cfg)
        assert router.get_config() is new_cfg
        # route 也用新 config（策略生效）
        from lingclaude.core.behavior import BehaviorMetrics

        # hallucination_risk = turns_without_tools_but_needed / total_turns = 17/20 = 0.85
        # 0.85 < 0.9（新 config 阈值）→ 不触发 conservative
        # 0.85 > 0.7（旧 config 阈值）→ 若仍用旧 config 会触发 conservative
        behavior = BehaviorMetrics(
            total_turns=20,
            turns_with_tools=20,
            turns_without_tools_but_needed=17,
        )
        router.set_behavior(behavior)
        router.route("写一个函数")
        # 若仍用旧 config(0.7) 会触发 conservative；新 config(0.9) 不触发
        assert router.get_strategy() == BehaviorRouterStrategy.STANDARD
    finally:
        monkeypatch.undo()


# ---------------------------------------------------------------------------
# DeclarationExtractor 策略热更
# ---------------------------------------------------------------------------


def _write_claim_patterns(tmp_path: Path, extra: list[str] | None = None) -> None:
    """构造最小 claim_patterns.yaml。"""
    lines = ["patterns:\n"]
    patterns = [
        ("notified", r"已(?:通知|告知|通告)\s*(.+?)(?:,|。|$)"),
        ("sent", r"已(?:发送|投递|回复)\s*(.+?)(?:,|。|$)"),
    ]
    if extra:
        patterns.extend(extra)
    for pid, regex in patterns:
        lines.append(f"  - pattern_id: {pid}\n")
        lines.append(f"    regex: '{regex}'\n")
        lines.append(f"    event_type: claim.{pid}\n")
        lines.append(f"    action: {pid}\n")
    p = tmp_path / "claim_patterns.yaml"
    p.write_text("".join(lines), encoding="utf-8")


def test_l10_extract_hot_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """extract() 每次调用前检查策略热更：新增模式后立即生效。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    _write_claim_patterns(tmp_path)

    try:
        ext = DeclarationExtractor(patterns=[])  # 空自定义？不 —— 用 None 走策略文件
        # 重新构造：None → 策略文件模式
        ext = DeclarationExtractor()
        # 无 _L10B 时走策略文件；有 _L10B 时走灵极优。为隔离，强制模拟无灵极优：
        # 直接测 _maybe_refresh_policy 对策略文件的刷新路径。
        from lingclaude.core import l10_a_post_audit as l10

        # 确保 extractor 当前处于策略文件模式（非自定义、非灵极优）
        ext._custom_patterns = False
        ext._patterns = []
        ext._compiled = []

        # 首次 extract：加载策略文件 2 个模式
        decls = ext.extract("已通知灵安。")
        assert any(d.pattern_id == "notified" for d in decls)

        # 改策略文件：新增 created 模式
        _write_claim_patterns(tmp_path, extra=[("created", r"已(?:创建|建立|发起)\s*(.+?)(?:,|。|$)")])
        # 强制 PolicyLoader 感知变更（hot_update 重读；extract 的 _maybe_refresh 用 get 比较内容）
        pl.hot_update()

        decls = ext.extract("已创建 thread abc123。")
        assert any(d.pattern_id == "created" for d in decls)
        # 原有模式仍在
        decls = ext.extract("已通知灵安。")
        assert any(d.pattern_id == "notified" for d in decls)
    finally:
        monkeypatch.undo()


def test_l10_custom_patterns_not_refreshed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """显式自定义 patterns 的 extractor 不参与策略热更。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    _write_claim_patterns(tmp_path)

    try:
        custom = [{"pattern_id": "custom", "regex": r"已xxx测试", "event_type": "claim.custom", "action": "xxx"}]
        ext = DeclarationExtractor(patterns=custom)
        assert ext._custom_patterns is True
        decls = ext.extract("已xxx测试。")
        assert any(d.pattern_id == "custom" for d in decls)

        # 改策略文件：新增模式也不影响自定义 extractor
        _write_claim_patterns(tmp_path, extra=[("created", r"已(?:创建|建立|发起)\s*(.+?)(?:,|。|$)")])
        pl.hot_update()
        decls = ext.extract("已xxx测试。")
        assert not any(d.pattern_id == "created" for d in decls)
        assert any(d.pattern_id == "custom" for d in decls)
    finally:
        monkeypatch.undo()

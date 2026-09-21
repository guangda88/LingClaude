"""R5 阶段 3（5a/5b）：denial 熔断器测试。

- 5a: PermissionDenial 加 rule_id 字段 + PermissionContext.blocks_with_rule 返回结构化 (blocked, rule_id)
- 5b: _ToolLoopDetector.observe_denial 按 rule_id 聚合,阈值 2 触发 "denial_abort"
"""
from __future__ import annotations

import pytest


# ── 5a: PermissionDenial 与 blocks_with_rule ────────────────────────────


class TestPermissionDenialRuleId:
    def test_default_rule_id_is_unknown(self):
        """backward-compat:旧代码构造 PermissionDenial(tool, reason) 必须仍可工作。"""
        from lingclaude.core.models import PermissionDenial

        d = PermissionDenial(tool_name="bash", reason="blocked")
        assert d.rule_id == "unknown"  # 缺省值,旧调用方无感

    def test_explicit_rule_id_round_trips(self):
        from lingclaude.core.models import PermissionDenial

        d = PermissionDenial(tool_name="write", reason="...", rule_id="config.deny_tools.exact")
        assert d.rule_id == "config.deny_tools.exact"


class TestBlocksWithRule:
    """PermissionContext.blocks_with_rule 返回 (bool, rule_id)。"""

    def test_no_deny_returns_no_match(self):
        from lingclaude.core.permissions import PermissionContext

        ctx = PermissionContext()
        blocked, rule_id = ctx.blocks_with_rule("bash")
        assert blocked is False
        assert rule_id == "no_match"

    def test_exact_deny_match(self):
        from lingclaude.core.permissions import PermissionContext

        ctx = PermissionContext.from_config(deny_tools=["bash"])
        blocked, rule_id = ctx.blocks_with_rule("bash")
        assert blocked is True
        assert rule_id == "config.deny_tools.exact", (
            f"应当返回精确命中规则 id,实际 {rule_id}"
        )

    def test_prefix_deny_match(self):
        from lingclaude.core.permissions import PermissionContext

        ctx = PermissionContext.from_config(deny_prefixes=["dangerous_"])
        blocked, rule_id = ctx.blocks_with_rule("dangerous_admin")
        assert blocked is True
        assert rule_id == "config.deny_prefixes.prefix"

    def test_exact_match_takes_precedence_over_prefix(self):
        """精确命中优先于前缀匹配（语义:exact 是更具体的约束）。"""
        from lingclaude.core.permissions import PermissionContext

        ctx = PermissionContext.from_config(
            deny_tools=["bash"], deny_prefixes=["bash"],
        )
        blocked, rule_id = ctx.blocks_with_rule("bash")
        assert blocked is True
        assert rule_id == "config.deny_tools.exact"

    def test_case_insensitive(self):
        from lingclaude.core.permissions import PermissionContext

        ctx = PermissionContext.from_config(deny_tools=["bash"])
        blocked, rule_id = ctx.blocks_with_rule("BASH")
        assert blocked is True
        assert rule_id == "config.deny_tools.exact"


# ── 5b: _ToolLoopDetector.observe_denial ────────────────────────────────


class TestObserveDenial:
    def test_first_denial_returns_warn(self):
        """5b:第 1 次 denial 返回 'denial_warn'（不熔断,给一次机会）。"""
        from lingclaude.engine.loop.tool_loop_detector import _ToolLoopDetector  # P0-A L0批次3: 直切正身

        d = _ToolLoopDetector()
        verdict = d.observe_denial("config.deny_tools.exact", "bash")
        assert verdict == "denial_warn"

    def test_second_denial_aborts(self):
        """5b:同 rule_id 连续 2 次 → 'denial_abort',应暂停+升级用户（非停止）。"""
        from lingclaude.engine.loop.tool_loop_detector import _ToolLoopDetector  # P0-A L0批次3: 直切正身

        d = _ToolLoopDetector()
        d.observe_denial("config.deny_tools.exact", "bash")
        verdict = d.observe_denial("config.deny_tools.exact", "bash")
        assert verdict == "denial_abort"

    def test_different_rule_ids_isolated(self):
        """5b:不同 rule_id 各自计数,互不污染。"""
        from lingclaude.engine.loop.tool_loop_detector import _ToolLoopDetector  # P0-A L0批次3: 直切正身

        d = _ToolLoopDetector()
        # rule A 一次、rule B 一次 —— 都未达阈值
        assert d.observe_denial("config.deny_tools.exact", "bash") == "denial_warn"
        assert d.observe_denial("strict_mode.non_readonly", "write") == "denial_warn"
        # rule A 再来一次 → 仅 rule A 触发 abort
        assert d.observe_denial("config.deny_tools.exact", "bash") == "denial_abort"
        # rule B 再来一次 → 达到阈值,abort
        assert d.observe_denial("strict_mode.non_readonly", "write") == "denial_abort"

    def test_reset_denial_clears_counter(self):
        """5b:成功后调用 reset_denial 应清空计数(与现有 has_new 清零 streak 对齐)。"""
        from lingclaude.engine.loop.tool_loop_detector import _ToolLoopDetector  # P0-A L0批次3: 直切正身

        d = _ToolLoopDetector()
        d.observe_denial("config.deny_tools.exact", "bash")
        d.reset_denial("config.deny_tools.exact")
        # 重置后再来 1 次,只到 1,不应 abort
        assert d.observe_denial("config.deny_tools.exact", "bash") == "denial_warn"

    def test_observable_alongside_loop_detector(self):
        """5b 与现有打转检测并存:observe_denial 独立计数,observe_round 独立状态。"""
        from lingclaude.engine.loop.tool_loop_detector import _ToolLoopDetector  # P0-A L0批次3: 直切正身

        d = _ToolLoopDetector()
        # 现有打转:同签名首次返 None,再 +1 次 = warn,再 +1 = abort
        assert d.observe_round([("bash", "ls")]) is None
        assert d.observe_round([("bash", "ls")]) == "warn"
        assert d.observe_round([("bash", "ls")]) == "abort"
        # 5b 熔断:独立计数,不受打转影响
        assert d.observe_denial("config.deny_tools.exact", "write") == "denial_warn"
        assert d.observe_denial("config.deny_tools.exact", "write") == "denial_abort"

    def test_existing_observe_round_unchanged(self):
        """5b 加 observe_denial 后,observe_round 行为不应回归。

        detector 语义修正：
        - 新签名首次出现 → None（has_new=True,streak 清零）
        - 同签名第 2 次 → "warn"
        - 同签名第 3 次（streak >= 2）→ "abort"
        """
        from lingclaude.engine.loop.tool_loop_detector import _ToolLoopDetector  # P0-A L0批次3: 直切正身

        d = _ToolLoopDetector()
        # 新签名首次出现 = 正常推进,清零 streak,返 None
        assert d.observe_round([("bash", "ls")]) is None
        # 同签名第 2 次: streak=1,返 warn
        assert d.observe_round([("bash", "ls")]) == "warn"
        # 同签名第 3 次: streak=2,返 abort
        assert d.observe_round([("bash", "ls")]) == "abort"
        # 空调用:纯文本轮,不参与判定
        assert d.observe_round([]) is None
        # 新签名:清零 streak,返 None
        assert d.observe_round([("bash", "pwd")]) is None

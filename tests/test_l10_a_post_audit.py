"""L10-A 后置审计层测试 - 后置声明审计 (DeclarationExtractor + audit_declarations)

测试范围:
1. DeclarationExtractor: 15 模式提取 (复用灵极优 CLAIM_PATTERNS)
2. audit_declarations: 通过/未通过/无声明/异常
3. 告警通道: 默认告警 + 自定义告警回调
4. 与灵极优 verify_claim 契约对齐 (字段映射)
5. 不阻断原则 (异常不影响主流程)
"""
import sys
sys.path.insert(0, "lingmemory")
sys.path.insert(0, "/home/ai/lingminopt")

import pytest
from lingclaude.core.l10_a_post_audit import (
    Declaration,
    DeclarationAuditResult,
    AuditResult,
    DeclarationExtractor,
    audit_declarations,
    alert_to_lingbus,
)
from test_engine import TestCase, pytest_case


# === Mock verifier (模拟灵极优 verify_claim 返回) ===

def _mock_verify_pass(claim_text, claim_member, db_instance=None, time_window_hours=24.0):
    """模拟 verify_claim 验证通过"""
    return {
        "verified": True,
        "event": {"ts": "2026-07-21T06:00:00Z", "data": {"target": "灵安"}},
        "matched_pattern": "notified",
        "extracted": {"action": "通知", "target": "灵安"},
        "source_db": db_instance or "default",
        "fallback": None,
    }


def _mock_verify_fail(claim_text, claim_member, db_instance=None, time_window_hours=24.0):
    """模拟 verify_claim 验证失败 (no_match)"""
    return {
        "verified": False,
        "event": None,
        "matched_pattern": "notified",
        "extracted": {"action": "通知", "target": "灵安"},
        "source_db": db_instance or "default",
        "fallback": "no_match",
    }


def _mock_verify_ambiguous(claim_text, claim_member, db_instance=None, time_window_hours=24.0):
    """模拟 verify_claim ambiguous_pattern (有事件但 target 不匹配)"""
    return {
        "verified": False,
        "event": {"ts": "2026-07-21T05:00:00Z", "data": {"target": "灵研"}},
        "matched_pattern": "notified",
        "extracted": {"action": "通知", "target": "灵安"},
        "source_db": db_instance or "default",
        "fallback": "ambiguous_pattern",
    }


def _mock_verify_outside_window(claim_text, claim_member, db_instance=None, time_window_hours=24.0):
    """模拟 verify_claim outside_window (时间窗外)"""
    return {
        "verified": False,
        "event": None,
        "matched_pattern": "notified",
        "extracted": {},
        "source_db": db_instance or "default",
        "fallback": "outside_window",
    }


# === DeclarationExtractor 测试 ===

class TestDeclarationExtractor:
    """15 模式提取测试"""

    def test_pattern_count_is_15(self):
        """模式数必须为 15 (与灵极优对齐)"""
        ext = DeclarationExtractor()
        assert ext.pattern_count == 15

    def test_extract_notified(self):
        ext = DeclarationExtractor()
        decls = ext.extract("已通知灵安。")
        assert len(decls) == 1
        assert decls[0].pattern_id == "notified"
        assert decls[0].action == "通知"
        assert decls[0].target == "灵安"

    def test_extract_created(self):
        ext = DeclarationExtractor()
        decls = ext.extract("已创建 thread 1c9d463fa3。")
        assert len(decls) == 1
        assert decls[0].pattern_id == "created"
        assert decls[0].target == "thread 1c9d463fa3"

    def test_extract_multiple_declarations(self):
        ext = DeclarationExtractor()
        text = "已通知灵安。已创建 thread xxx。已完成 L10-A 编码。"
        decls = ext.extract(text)
        assert len(decls) == 3
        pattern_ids = [d.pattern_id for d in decls]
        assert "notified" in pattern_ids
        assert "created" in pattern_ids
        assert "completed" in pattern_ids

    def test_extract_dedup(self):
        """同一声明文本只保留首次"""
        ext = DeclarationExtractor()
        text = "已通知灵安。再次确认，已通知灵安。"
        decls = ext.extract(text)
        assert len(decls) == 1

    def test_extract_empty_text(self):
        ext = DeclarationExtractor()
        assert ext.extract("") == []
        assert ext.extract("今天天气不错") == []
        assert ext.extract("没有任何声明性内容") == []

    def test_extract_all_15_patterns(self):
        """覆盖全部 15 个模式"""
        ext = DeclarationExtractor()
        cases = [
            ("已通知灵安。", "notified"),
            ("已发送消息。", "sent"),
            ("已创建 thread。", "created"),
            ("已修改配置。", "modified"),
            ("已确认状态。", "verified"),
            ("已查询数据库。", "queried"),
            ("已执行命令。", "executed"),
            ("已写入日志。", "emitted"),
            ("已完成任务。", "completed"),
            ("已删除文件。", "deleted"),
            ("已检查权限。", "checked"),
            ("已下载数据。", "downloaded"),
            ("已上传结果。", "uploaded"),
            ("已生成报告。", "generated"),
            ("已安装依赖。", "installed"),
        ]
        for text, expected_pid in cases:
            decls = ext.extract(text)
            assert len(decls) == 1, f"模式 {expected_pid} 未提取到: {text}"
            assert decls[0].pattern_id == expected_pid, f"模式不匹配: 期望 {expected_pid}, 实际 {decls[0].pattern_id}"

    def test_extract_span_correct(self):
        """span 字段正确标记位置"""
        ext = DeclarationExtractor()
        text = "前置文本。已通知灵安。后置文本。"
        decls = ext.extract(text)
        assert len(decls) == 1
        span = decls[0].span
        # 提取的文本应与 span 对应
        assert text[span[0]:span[1]] == decls[0].text

    def test_custom_patterns(self):
        """支持自定义模式 (测试用)"""
        custom = [
            {"pattern_id": "custom", "regex": r"已xxx\s*(.+?)(?:,|。|$)",
             "event_type": "claim.custom", "action": "xxx"},
        ]
        ext = DeclarationExtractor(patterns=custom)
        assert ext.pattern_count == 1
        decls = ext.extract("已xxx测试。")
        assert len(decls) == 1
        assert decls[0].pattern_id == "custom"


# === audit_declarations 测试 ===

class TestAuditDeclarations:
    """后置审计入口测试"""

    def test_all_verified_passes(self):
        """所有声明通过 -> passed=True, 不告警"""
        r = audit_declarations("已通知灵安。", "lingclaude", verifier=_mock_verify_pass)
        assert r.passed is True
        assert r.total == 1
        assert r.verified_count == 1
        assert r.failed_count == 0
        assert r.alerted is False
        assert r.warning == ""

    def test_failed_triggers_alert(self):
        """声明未通过 -> passed=False, 告警"""
        alerts = []
        def capture(failed):
            alerts.extend(failed)

        r = audit_declarations(
            "已通知灵安。已创建 thread xxx。",
            "lingclaude",
            verifier=_mock_verify_fail,
            alert_fn=capture,
        )
        assert r.passed is False
        assert r.total == 2
        assert r.verified_count == 0
        assert r.failed_count == 2
        assert r.alerted is True
        assert len(alerts) == 2
        assert all(isinstance(a, DeclarationAuditResult) for a in alerts)

    def test_no_declarations_passes(self):
        """无声明 -> passed=True, total=0"""
        r = audit_declarations("今天天气不错。", "lingclaude", verifier=_mock_verify_fail)
        assert r.passed is True
        assert r.total == 0
        assert r.alerted is False

    def test_verifier_exception_does_not_block(self):
        """verifier 异常 -> 记录 error, 不阻断主流程"""
        def boom(claim_text, claim_member, db_instance=None, time_window_hours=24.0):
            raise RuntimeError("L10-B down")

        r = audit_declarations(
            "已通知灵安。",
            "lingclaude",
            verifier=boom,
            alert_fn=lambda f: None,  # 抑制告警
        )
        assert r.passed is False
        assert r.total == 1
        assert r.failed_count == 1
        assert r.results[0].error != ""
        assert "L10-B down" in r.results[0].error

    def test_alert_exception_does_not_block(self):
        """告警回调异常 -> 不影响审计结果返回"""
        def bad_alert(failed):
            raise RuntimeError("alert channel down")

        r = audit_declarations(
            "已通知灵安。",
            "lingclaude",
            verifier=_mock_verify_fail,
            alert_fn=bad_alert,
        )
        # 审计结果仍然返回, 只是 alerted=False
        assert r.passed is False
        assert r.failed_count == 1
        assert r.alerted is False  # 告警失败

    def test_claim_member_required(self):
        """claim_member 必填"""
        with pytest.raises(ValueError, match="claim_member"):
            audit_declarations("已通知灵安。", "")

    def test_db_instance_passed_to_verifier(self):
        """db_instance 正确传递给 verifier"""
        captured = {}
        def capturing_verifier(claim_text, claim_member, db_instance=None, time_window_hours=24.0):
            captured["db_instance"] = db_instance
            return _mock_verify_pass(claim_text, claim_member, db_instance, time_window_hours)

        audit_declarations(
            "已通知灵安。",
            "lingclaude",
            verifier=capturing_verifier,
            db_instance="/home/ai/lingmessage/lingbus.db",
        )
        assert captured["db_instance"] == "/home/ai/lingmessage/lingbus.db"

    def test_time_window_passed_to_verifier(self):
        """time_window_hours 正确传递"""
        captured = {}
        def capturing_verifier(claim_text, claim_member, db_instance=None, time_window_hours=24.0):
            captured["tw"] = time_window_hours
            return _mock_verify_pass(claim_text, claim_member, db_instance, time_window_hours)

        audit_declarations(
            "已通知灵安。",
            "lingclaude",
            verifier=capturing_verifier,
            time_window_hours=48.0,
        )
        assert captured["tw"] == 48.0

    def test_partial_failure(self):
        """部分通过部分失败"""
        call_count = [0]
        def mixed_verifier(claim_text, claim_member, db_instance=None, time_window_hours=24.0):
            call_count[0] += 1
            if call_count[0] == 1:
                return _mock_verify_pass(claim_text, claim_member, db_instance, time_window_hours)
            return _mock_verify_fail(claim_text, claim_member, db_instance, time_window_hours)

        r = audit_declarations(
            "已通知灵安。已创建 thread xxx。",
            "lingclaude",
            verifier=mixed_verifier,
            alert_fn=lambda f: None,
        )
        assert r.passed is False
        assert r.total == 2
        assert r.verified_count == 1
        assert r.failed_count == 1

    def test_result_fields_complete(self):
        """审计结果字段完整性 (与灵极优契约对齐)"""
        r = audit_declarations(
            "已通知灵安。",
            "lingclaude",
            verifier=_mock_verify_ambiguous,
            alert_fn=lambda f: None,
        )
        assert r.total == 1
        item = r.results[0]
        assert item.declaration.pattern_id == "notified"
        assert item.verified is False
        assert item.matched_pattern == "notified"
        assert item.fallback == "ambiguous_pattern"
        assert item.source_db == "default"
        assert item.event is not None
        assert item.error == ""


# === 告警通道测试 ===

class TestAlertChannel:

    def test_default_alert_does_not_raise(self):
        """默认告警 (日志) 不抛异常"""
        from lingclaude.core.l10_a_post_audit import _default_alert
        decl = Declaration(text="已通知灵安。", pattern_id="notified", action="通知", target="灵安")
        result = DeclarationAuditResult(declaration=decl, verified=False, fallback="no_match")
        _default_alert([result])  # 不抛异常即通过

    def test_alert_to_lingbus_closure(self):
        """alert_to_lingbus 返回闭包, 可作 alert_fn"""
        decl = Declaration(text="已通知灵安。", pattern_id="notified", action="通知", target="灵安")
        result = DeclarationAuditResult(declaration=decl, verified=False, fallback="no_match")
        alert_fn = alert_to_lingbus([], "lingclaude", thread_id="xxx")
        alert_fn([result])  # 不抛异常即通过

    def test_custom_extractor_used(self):
        """自定义 extractor 被正确使用"""
        custom = [
            {"pattern_id": "custom_op", "regex": r"done\s+(.+?)(?:,|。|$)",
             "event_type": "claim.custom", "action": "done"},
        ]
        ext = DeclarationExtractor(patterns=custom)
        r = audit_declarations(
            "done something.",
            "lingclaude",
            verifier=_mock_verify_pass,
            extractor=ext,
        )
        assert r.total == 1


# === 与灵极优 verify_claim 契约对齐测试 ===

class TestContractAlignment:
    """验证 L10-A 与灵极优 verify_claim 的字段映射契约"""

    def test_verify_claim_return_shape_consumed(self):
        """灵极优 verify_claim 返回的 6 个字段全部被 L10-A 消费"""
        # 灵极优返回: verified, event, matched_pattern, extracted, source_db, fallback
        r = audit_declarations(
            "已通知灵安。",
            "lingclaude",
            verifier=_mock_verify_pass,
            db_instance="/path/to/lingbus.db",
        )
        item = r.results[0]
        assert item.verified is True              # verified
        assert item.event is not None             # event
        assert item.matched_pattern is not None   # matched_pattern
        assert item.source_db == "/path/to/lingbus.db"  # source_db
        assert item.fallback is None              # fallback (None when verified)

    def test_fallback_enums_covered(self):
        """灵极优 4 种 fallback 枚举全部处理"""
        for verifier, expected_fb in [
            (_mock_verify_fail, "no_match"),
            (_mock_verify_ambiguous, "ambiguous_pattern"),
            (_mock_verify_outside_window, "outside_window"),
        ]:
            r = audit_declarations(
                "已通知灵安。",
                "lingclaude",
                verifier=verifier,
                alert_fn=lambda f: None,
            )
            assert r.results[0].fallback == expected_fb
            assert r.results[0].verified is False

    def test_lingminopt_import_optional(self):
        """灵极优不可用时 L10-A 仍可工作 (用 mock verifier)"""
        # 本测试隐式验证: 即使 _L10B_AVAIL=False, 用 mock verifier 也能跑
        # (本测试环境灵极优可用, 但 mock verifier 模拟了不可用场景)
        r = audit_declarations(
            "已通知灵安。",
            "lingclaude",
            verifier=_mock_verify_fail,
            alert_fn=lambda f: None,
        )
        assert r.passed is False


# === 不阻断原则测试 ===

class TestNonBlocking:

    def test_audit_returns_even_when_all_fail(self):
        """即使所有声明都失败, audit_declarations 仍正常返回"""
        r = audit_declarations(
            "已通知灵安。已创建 x。已修改 y。已删除 z。",
            "lingclaude",
            verifier=_mock_verify_fail,
            alert_fn=lambda f: None,
        )
        assert r is not None
        assert r.passed is False
        assert r.total == 4
        assert r.failed_count == 4

    def test_audit_returns_even_when_verifier_always_raises(self):
        """verifier 持续抛异常, audit 仍正常返回"""
        def always_boom(claim_text, claim_member, db_instance=None, time_window_hours=24.0):
            raise RuntimeError("permanent failure")

        r = audit_declarations(
            "已通知灵安。已创建 x。",
            "lingclaude",
            verifier=always_boom,
            alert_fn=lambda f: None,
        )
        assert r is not None
        assert r.passed is False
        assert all(item.error != "" for item in r.results)

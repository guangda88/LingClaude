from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from lingclaude.core.config import LingClaudeConfig, EngineConfig, TriggerConfig, load_config
from lingclaude.core.models import ToolDefinition, Subsystem
from lingclaude.core.permissions import PermissionContext
from lingclaude.core.session import Session, SessionManager
from lingclaude.core.types import Result
from lingclaude.core.query_engine import QueryEngine, StopReason


class TestResult:
    def test_ok_factory(self) -> None:
        r = Result.ok(42)
        assert r.is_ok
        assert r.data == 42
        assert r.error is None

    def test_fail_factory(self) -> None:
        r = Result.fail("bad")
        assert r.is_error
        assert r.error == "bad"
        assert r.data is None

    def test_with_code(self) -> None:
        r = Result.ok("data", code="C001")
        assert r.code == "C001"


class TestConfig:
    def test_default_config(self) -> None:
        cfg = LingClaudeConfig()
        assert cfg.engine.max_turns == 8
        assert cfg.triggers.enabled is True
        assert cfg.optimizer.goal == "structure"

    def test_from_dict(self) -> None:
        raw = {
            "engine": {"max_turns": 16},
            "permissions": {"deny_tools": ["dangerous"]},
            "self_optimizer": {
                "triggers": {"max_complexity": 20},
                "optimization": {"max_trials": 100},
            },
        }
        cfg = LingClaudeConfig.from_dict(raw)
        assert cfg.engine.max_turns == 16
        assert "dangerous" in cfg.permissions.deny_tools
        assert cfg.triggers.max_complexity == 20
        assert cfg.optimizer.max_trials == 100

    def test_load_config_missing_file(self) -> None:
        cfg = load_config(Path("/nonexistent/config.yaml"))
        assert cfg.engine.max_turns == 8

    def test_load_config_from_file(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("engine:\n  max_turns: 42\n")
            f.flush()
            cfg = load_config(Path(f.name))
        assert cfg.engine.max_turns == 42


class TestSession:
    def test_session_creation(self) -> None:
        s = Session(session_id="test-1", messages=(), input_tokens=0, output_tokens=0)
        assert s.session_id == "test-1"
        assert s.messages == ()

    def test_session_manager_roundtrip(self, tmp_path: Path) -> None:
        mgr = SessionManager(tmp_path)
        s = mgr.create(messages=("hello",), input_tokens=5, output_tokens=10)
        assert s.session_id
        assert s.messages == ("hello",)

        saved_path = mgr.save(s)
        assert saved_path.exists()

        loaded = mgr.load(s.session_id)
        assert loaded is not None
        assert loaded.session_id == s.session_id
        assert loaded.input_tokens == 5

        sessions = mgr.list_sessions()
        assert len(sessions) == 1

        mgr.delete(s.session_id)
        assert mgr.load(s.session_id) is None


class TestPermissions:
    def test_blocks_by_name(self) -> None:
        ctx = PermissionContext.from_config(deny_tools=["dangerous"])
        assert ctx.blocks("dangerous")
        assert not ctx.blocks("safe")

    def test_blocks_by_prefix(self) -> None:
        ctx = PermissionContext.from_config(deny_prefixes=["sys_"])
        assert ctx.blocks("sys_admin")
        assert not ctx.blocks("user_tool")


class TestToolRegistry:
    def test_register_and_execute(self) -> None:
        registry = __import__("lingclaude.engine.tools", fromlist=["ToolRegistry"]).ToolRegistry()
        td = __import__("lingclaude.engine.tools", fromlist=["ToolDefinition"]).ToolDefinition(
            name="echo",
            description="Echo tool",
            parameters={"text": {"type": "string"}},
            handler=lambda text="": text,
        )
        registry.register(td)
        assert registry.has_tool("echo")
        result = registry.execute("echo", text="hello")
        assert result == "hello"

    def test_missing_tool(self) -> None:
        registry = __import__("lingclaude.engine.tools", fromlist=["ToolRegistry"]).ToolRegistry()
        with pytest.raises(ValueError, match="not found"):
            registry.execute("nonexistent")


class TestOptimizationTrigger:
    def test_user_triggered(self) -> None:
        from lingclaude.self_optimizer.trigger import OptimizationTrigger

        trigger = OptimizationTrigger()
        triggered, info = trigger.check_all_conditions({"user_triggered": True})
        assert triggered
        assert info is not None
        assert info.priority == "high"

    def test_quality_trigger(self) -> None:
        from lingclaude.self_optimizer.trigger import OptimizationTrigger

        trigger = OptimizationTrigger()
        triggered, info = trigger.check_all_conditions({"review_score": 30})
        assert triggered
        assert info is not None
        assert info.type == "quality"

    def test_no_trigger(self) -> None:
        from lingclaude.self_optimizer.trigger import OptimizationTrigger

        trigger = OptimizationTrigger()
        triggered, info = trigger.check_all_conditions({})
        assert not triggered
        assert info is None

    def test_disabled(self) -> None:
        from lingclaude.self_optimizer.trigger import OptimizationTrigger
        from lingclaude.core.config import TriggerConfig

        trigger = OptimizationTrigger(TriggerConfig(enabled=False))
        triggered, info = trigger.check_all_conditions({"review_score": 10})
        assert not triggered


class TestStructureEvaluator:
    def test_evaluate_empty_dir(self, tmp_path: Path) -> None:
        from lingclaude.self_optimizer.evaluator import StructureEvaluator

        evaluator = StructureEvaluator(str(tmp_path))
        score = evaluator.evaluate({})
        assert score == 0.0

    def test_evaluate_with_code(self, tmp_path: Path) -> None:
        from lingclaude.self_optimizer.evaluator import StructureEvaluator

        code = """
class MyClass:
    def method1(self):
        pass

    def method2(self):
        if True:
            pass
        elif False:
            pass
        else:
            pass
"""
        (tmp_path / "sample.py").write_text(code)

        evaluator = StructureEvaluator(str(tmp_path))
        metrics = evaluator.get_current_metrics()
        assert metrics["total_classes"] == 1
        assert metrics["total_methods"] == 2


class TestPatternRecognizer:
    def test_long_method(self) -> None:
        from lingclaude.self_optimizer.learner.patterns import PatternRecognizer

        code = "def long_func():\n" + "    x = 1\n" * 60
        recognizer = PatternRecognizer()
        patterns = recognizer.recognize_patterns(code, "test.py")
        long_methods = [p for p in patterns if p["name"] == "Long Method"]
        assert len(long_methods) > 0

    def test_hardcoded_secret(self) -> None:
        from lingclaude.self_optimizer.learner.patterns import PatternRecognizer

        code = 'password = "supersecret123"\napi_key = "AKIAIOSFODNN7EXAMPLE"\n'
        recognizer = PatternRecognizer()
        patterns = recognizer.recognize_patterns(code, "config.py")
        secrets = [p for p in patterns if p["name"] == "Hardcoded Secret"]
        assert len(secrets) >= 2

    def test_empty_block(self) -> None:
        from lingclaude.self_optimizer.learner.patterns import PatternRecognizer

        code = "def stub():\n    pass\n"
        recognizer = PatternRecognizer()
        patterns = recognizer.recognize_patterns(code, "stub.py")
        empties = [p for p in patterns if p["name"] == "Empty Block"]
        assert len(empties) > 0

    def test_clean_code(self) -> None:
        from lingclaude.self_optimizer.learner.patterns import PatternRecognizer

        code = "def clean(x: int) -> int:\n    return x + 1\n"
        recognizer = PatternRecognizer()
        patterns = recognizer.recognize_patterns(code, "clean.py")
        assert len(patterns) == 0


class TestKnowledgeBase:
    def test_in_memory_crud(self) -> None:
        from lingclaude.self_optimizer.learner.knowledge import InMemoryKnowledgeBase
        from lingclaude.self_optimizer.learner.models import (
            LearnedRule, Pattern, FeedbackCategory,
        )

        kb = InMemoryKnowledgeBase()

        rule = LearnedRule(
            id="rule-1",
            name="Test Rule",
            description="A test rule",
            category=FeedbackCategory.CODE_QUALITY,
            pattern=Pattern(file_patterns=["*.py"]),
            tools=["ruff"],
            frequency=5,
            confidence=0.9,
            quality_score=0.85,
        )

        assert kb.add_rule(rule)
        assert kb.get_rule("rule-1") is not None
        assert kb.get_rule("nonexistent") is None

        rules = kb.get_all_rules()
        assert len(rules) == 1

        assert kb.update_rule_status("rule-1", "active")
        assert kb.get_rule("rule-1").status == "active"

        assert kb.delete_rule("rule-1")
        assert kb.get_rule("rule-1") is None

    def test_search(self) -> None:
        from lingclaude.self_optimizer.learner.knowledge import InMemoryKnowledgeBase
        from lingclaude.self_optimizer.learner.models import (
            LearnedRule, Pattern, FeedbackCategory,
        )

        kb = InMemoryKnowledgeBase()
        rule = LearnedRule(
            id="rule-x",
            name="SQL Injection Detection",
            description="Detects SQL injection patterns",
            category=FeedbackCategory.SECURITY,
            pattern=Pattern(file_patterns=["*.py"]),
            tools=["semgrep"],
            frequency=10,
            confidence=0.95,
            quality_score=0.9,
        )
        kb.add_rule(rule)

        results = kb.search_rules("sql")
        assert len(results) == 1

        results = kb.search_rules("nonexistent")
        assert len(results) == 0

    def test_statistics(self) -> None:
        from lingclaude.self_optimizer.learner.knowledge import InMemoryKnowledgeBase
        from lingclaude.self_optimizer.learner.models import (
            LearnedRule, Pattern, FeedbackCategory,
        )

        kb = InMemoryKnowledgeBase()
        stats = kb.get_statistics()
        assert stats["total_rules"] == 0

        kb.add_rule(
            LearnedRule(
                id="r1",
                name="R1",
                description="",
                category=FeedbackCategory.CODE_QUALITY,
                pattern=Pattern(),
                tools=[],
                frequency=1,
                confidence=0.5,
                quality_score=0.6,
            )
        )
        stats = kb.get_statistics()
        assert stats["total_rules"] == 1

    def test_sqlite_knowledge_base(self, tmp_path: Path) -> None:
        from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
        from lingclaude.self_optimizer.learner.models import (
            LearnedRule, Pattern, FeedbackCategory,
        )

        db_path = tmp_path / "test.db"
        kb = KnowledgeBase(str(db_path))

        rule = LearnedRule(
            id="sql-rule-1",
            name="SQL Rule",
            description="A SQLite-backed rule",
            category=FeedbackCategory.SECURITY,
            pattern=Pattern(file_patterns=["*.py"], context_keywords=["sql"]),
            tools=["semgrep"],
            frequency=7,
            confidence=0.88,
            quality_score=0.75,
        )

        assert kb.add_rule(rule)
        fetched = kb.get_rule("sql-rule-1")
        assert fetched is not None
        assert fetched.name == "SQL Rule"
        assert fetched.category == FeedbackCategory.SECURITY

        stats = kb.get_statistics()
        assert stats["total_rules"] == 1

        kb.close()


class TestRuleExtractor:
    def test_extract_rules(self) -> None:
        from lingclaude.self_optimizer.learner.rule_extractor import RuleExtractor
        from lingclaude.self_optimizer.learner.models import (
            FeedbackItem, FeedbackCategory, FeedbackSeverity, ToolType,
        )

        extractor = RuleExtractor(min_frequency=2, min_confidence=0.5)

        items = [
            FeedbackItem(
                tool_name="ruff",
                tool_type=ToolType.LINTING,
                rule_id="E501",
                rule_name="line-too-long",
                category=FeedbackCategory.CODE_QUALITY,
                severity=FeedbackSeverity.MEDIUM,
                message="Line too long",
                file_path="test.py",
                line=i,
                snippet="x = " + "a" * 100,
            )
            for i in range(5)
        ]

        rules = extractor.extract_rules(items)
        assert len(rules) >= 1
        assert rules[0].id == "e501"
        assert rules[0].frequency == 5

    def test_deduplicator(self) -> None:
        from lingclaude.self_optimizer.learner.rule_extractor import RuleDeduplicator
        from lingclaude.self_optimizer.learner.models import (
            LearnedRule, Pattern, FeedbackCategory,
        )

        dedup = RuleDeduplicator()
        rules = [
            LearnedRule(
                id="r1",
                name="Duplicate Rule",
                description="Same",
                category=FeedbackCategory.CODE_QUALITY,
                pattern=Pattern(context_keywords=["sql", "injection"]),
                tools=["ruff"],
                frequency=5,
                confidence=0.8,
                quality_score=0.7,
            ),
            LearnedRule(
                id="r2",
                name="Duplicate Rule Variant",
                description="Same variant",
                category=FeedbackCategory.CODE_QUALITY,
                pattern=Pattern(context_keywords=["sql", "injection"]),
                tools=["ruff"],
                frequency=3,
                confidence=0.7,
                quality_score=0.6,
            ),
        ]
        unique = dedup.deduplicate(rules)
        assert len(unique) == 1


class TestOptimizationAdvisor:
    def test_generate_report(self) -> None:
        from lingclaude.self_optimizer.advisor import OptimizationAdvisor
        from lingclaude.self_optimizer.optimizer import OptimizationResult

        advisor = OptimizationAdvisor()
        result = OptimizationResult(
            success=True,
            best_params={"max_class_size": 200, "max_complexity": 10},
            best_score=3.0,
            experiments=10,
            duration=5.2,
        )
        metrics = {
            "structure_violations": 5,
            "large_classes_count": 2,
            "avg_complexity": 12.5,
        }
        report = advisor.generate_report(
            goal="structure",
            target="/test",
            current_metrics=metrics,
            optimization_result=result,
        )
        assert "Self-Optimization Report" in report
        assert "structure" in report.lower()

"""Tests for Layered Memory"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone, timedelta

import pytest

from lingclaude.core.layered_memory import (
    CommonKnowledge,
    EmotionIntensity,
    Experience,
    ExperienceStore,
    InMemoryExperienceStore,
    LayeredMemory,
    MemoryLayer,
    WorkingMemory,
    ebbinghaus_weight,
)


class TestEmotionIntensity:
    """Test EmotionIntensity enum"""

    def test_values(self):
        """Test all enum values exist"""
        assert EmotionIntensity.NONE.value == "none"
        assert EmotionIntensity.LOW.value == "low"
        assert EmotionIntensity.MEDIUM.value == "medium"
        assert EmotionIntensity.HIGH.value == "high"


class TestMemoryLayer:
    """Test MemoryLayer enum"""

    def test_values(self):
        """Test all enum values exist"""
        assert MemoryLayer.COMMON.value == "common"
        assert MemoryLayer.WORKING.value == "working"
        assert MemoryLayer.EXPERIENCE.value == "experience"
        assert MemoryLayer.META.value == "meta"
        assert MemoryLayer.SHARED.value == "shared"


class TestExperience:
    """Test Experience dataclass"""

    def test_create_minimal(self):
        """Test creating experience with minimal parameters"""
        exp = Experience.create(
            problem="如何优化查询性能",
        )
        assert exp.problem == "如何优化查询性能"
        assert exp.hypothesis == ""
        assert exp.action == ""
        assert exp.result == ""
        assert exp.reflection == ""
        assert exp.emotion == EmotionIntensity.NONE
        assert exp.associations == ()
        assert exp.recall_count == 0
        assert exp.deny_count == 0
        assert exp.weight == 1.0
        assert len(exp.id) == 12

    def test_create_with_all_params(self):
        """Test creating experience with all parameters"""
        exp = Experience.create(
            problem="问题",
            hypothesis="假设",
            action="行动",
            result="结果",
            reflection="反思",
            emotion=EmotionIntensity.HIGH,
            associations=("tag1", "tag2"),
        )
        assert exp.problem == "问题"
        assert exp.hypothesis == "假设"
        assert exp.action == "行动"
        assert exp.result == "结果"
        assert exp.reflection == "反思"
        assert exp.emotion == EmotionIntensity.HIGH
        assert exp.associations == ("tag1", "tag2")

    def test_frozen(self):
        """Test that Experience is frozen"""
        exp = Experience.create(problem="test")
        with pytest.raises(Exception):  # FrozenInstanceError
            exp.problem = "modified"


class TestEbbinghausWeight:
    """Test ebbinghaus_weight function"""

    def test_fresh_experience(self):
        """Test weight for fresh experience"""
        now = datetime.now(timezone.utc)
        weight = ebbinghaus_weight(
            created_at=now,
            last_recalled=now,
            recall_count=0,
            deny_count=0,
            emotion=EmotionIntensity.NONE,
            association_count=0,
        )
        assert weight == pytest.approx(1.0, rel=0.1)

    def test_time_decay(self):
        """Test time decay factor"""
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=10)
        weight_old = ebbinghaus_weight(
            created_at=old,
            last_recalled=old,
            recall_count=0,
            deny_count=0,
            emotion=EmotionIntensity.NONE,
            association_count=0,
        )
        weight_new = ebbinghaus_weight(
            created_at=now,
            last_recalled=now,
            recall_count=0,
            deny_count=0,
            emotion=EmotionIntensity.NONE,
            association_count=0,
        )
        assert weight_old < weight_new

    def test_repetition_boost(self):
        """Test repetition factor"""
        now = datetime.now(timezone.utc)
        weight_0_recall = ebbinghaus_weight(
            created_at=now,
            last_recalled=now,
            recall_count=0,
            deny_count=0,
            emotion=EmotionIntensity.NONE,
            association_count=0,
        )
        weight_5_recall = ebbinghaus_weight(
            created_at=now,
            last_recalled=now,
            recall_count=5,
            deny_count=0,
            emotion=EmotionIntensity.NONE,
            association_count=0,
        )
        assert weight_5_recall > weight_0_recall

    def test_emotion_factor(self):
        """Test emotion factor"""
        now = datetime.now(timezone.utc)
        weight_none = ebbinghaus_weight(
            created_at=now,
            last_recalled=now,
            recall_count=0,
            deny_count=0,
            emotion=EmotionIntensity.NONE,
            association_count=0,
        )
        weight_high = ebbinghaus_weight(
            created_at=now,
            last_recalled=now,
            recall_count=0,
            deny_count=0,
            emotion=EmotionIntensity.HIGH,
            association_count=0,
        )
        assert weight_high > weight_none

    def test_association_boost(self):
        """Test association factor"""
        now = datetime.now(timezone.utc)
        weight_0_assoc = ebbinghaus_weight(
            created_at=now,
            last_recalled=now,
            recall_count=0,
            deny_count=0,
            emotion=EmotionIntensity.NONE,
            association_count=0,
        )
        weight_5_assoc = ebbinghaus_weight(
            created_at=now,
            last_recalled=now,
            recall_count=0,
            deny_count=0,
            emotion=EmotionIntensity.NONE,
            association_count=5,
        )
        assert weight_5_assoc > weight_0_assoc

    def test_deny_penalty(self):
        """Test deny penalty"""
        now = datetime.now(timezone.utc)
        weight_0_deny = ebbinghaus_weight(
            created_at=now,
            last_recalled=now,
            recall_count=0,
            deny_count=0,
            emotion=EmotionIntensity.NONE,
            association_count=0,
        )
        weight_3_deny = ebbinghaus_weight(
            created_at=now,
            last_recalled=now,
            recall_count=0,
            deny_count=3,
            emotion=EmotionIntensity.NONE,
            association_count=0,
        )
        assert weight_3_deny < weight_0_deny


class TestCommonKnowledge:
    """Test CommonKnowledge class"""

    def test_lookup_existing(self):
        """Test looking up existing fact"""
        ck = CommonKnowledge()
        fact = ck.lookup("灵克")
        assert fact is not None
        assert fact["en"] == "lingclaude"
        assert fact["role"] == "AI编程助手，对标Claude Code，内置自优化"

    def test_lookup_nonexistent(self):
        """Test looking up non-existent fact"""
        ck = CommonKnowledge()
        fact = ck.lookup("UnknownAgent")
        assert fact is None

    def test_search(self):
        """Test searching facts"""
        ck = CommonKnowledge()
        results = ck.search("灵")
        assert len(results) > 0
        # Should find multiple agents with "灵"
        names = [r[0] for r in results]
        assert "灵克" in names
        assert "灵研" in names

    def test_all_facts(self):
        """Test getting all facts"""
        ck = CommonKnowledge()
        facts = ck.all_facts()
        assert len(facts) > 0
        assert "灵克" in facts
        assert "灵信" in facts

    def test_to_prompt_text(self):
        """Test generating prompt text"""
        ck = CommonKnowledge()
        text = ck.to_prompt_text()
        assert "灵字辈大家庭成员" in text
        assert "灵克" in text
        assert "lingclaude" in text

    def test_extra_facts(self):
        """Test adding extra facts"""
        ck = CommonKnowledge(extra={"NewAgent": {"en": "New", "role": "Test"}})
        fact = ck.lookup("NewAgent")
        assert fact is not None
        assert fact["en"] == "New"


class TestWorkingMemory:
    """Test WorkingMemory class"""

    def test_append(self):
        """Test appending to working memory"""
        wm = WorkingMemory(capacity=5)
        wm.append("user", "Hello")
        wm.append("assistant", "Hi")
        assert wm.size == 2

    def test_capacity_limit(self):
        """Test capacity limit enforcement"""
        wm = WorkingMemory(capacity=3)
        for i in range(5):
            wm.append("role", f"message {i}")
        # Should only keep last 3
        assert wm.size == 3
        recent = wm.get_recent()
        assert len(recent) == 3

    def test_get_recent(self):
        """Test getting recent messages"""
        wm = WorkingMemory(capacity=10)
        for i in range(5):
            wm.append("role", f"message {i}")
        recent = wm.get_recent(n=3)
        assert len(recent) == 3
        assert recent[0][1] == "message 2"
        assert recent[2][1] == "message 4"

    def test_clear(self):
        """Test clearing working memory"""
        wm = WorkingMemory()
        wm.append("role", "message")
        assert wm.size == 1
        wm.clear()
        assert wm.size == 0

    def test_default_capacity(self):
        """Test default capacity"""
        wm = WorkingMemory()
        assert wm._capacity == 24


class TestInMemoryExperienceStore:
    """Test InMemoryExperienceStore class"""

    def test_store(self):
        """Test storing experience"""
        store = InMemoryExperienceStore()
        exp = Experience.create(
            problem="如何优化代码",
            reflection="使用缓存可以提升性能",
        )
        exp_id = store.store(exp)
        assert exp_id == exp.id

    def test_recall(self):
        """Test recalling experiences"""
        store = InMemoryExperienceStore()
        exp1 = Experience.create(
            problem="如何优化查询",
            reflection="添加索引",
        )
        exp2 = Experience.create(
            problem="如何处理错误",
            reflection="添加日志",
        )
        store.store(exp1)
        store.store(exp2)

        results = store.recall("查询")
        assert len(results) == 1
        assert results[0].problem == "如何优化查询"

    def test_record_recall(self):
        """Test recording recall"""
        store = InMemoryExperienceStore()
        exp = Experience.create(
            problem="测试问题",
            reflection="测试反思",
        )
        exp_id = store.store(exp)

        store.record_recall(exp_id)
        results = store.recall("测试")
        assert results[0].recall_count == 1
        assert results[0].weight > 1.0

    def test_record_deny(self):
        """Test recording deny"""
        store = InMemoryExperienceStore()
        exp = Experience.create(
            problem="测试问题",
            reflection="测试反思",
        )
        exp_id = store.store(exp)

        store.record_deny(exp_id)
        results = store.recall("测试")
        assert results[0].deny_count == 1
        assert results[0].weight < 1.0

    def test_decay_all(self):
        """Test decaying all experiences"""
        store = InMemoryExperienceStore()
        exp = Experience.create(
            problem="测试问题",
            reflection="测试反思",
        )
        store.store(exp)

        updated = store.decay_all()
        assert updated == 1

    def test_get_stats(self):
        """Test getting statistics"""
        store = InMemoryExperienceStore()
        exp1 = Experience.create(problem="问题1", reflection="反思1")
        exp2 = Experience.create(problem="问题2", reflection="反思2")
        store.store(exp1)
        store.store(exp2)

        stats = store.get_stats()
        assert stats["total_experiences"] == 2
        assert stats["average_weight"] == 1.0

    def test_close(self):
        """Test closing store (no-op for in-memory)"""
        store = InMemoryExperienceStore()
        store.close()
        # Should not raise any exception


class TestExperienceStore:
    """Test ExperienceStore with database"""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        yield db_path
        import os
        os.unlink(db_path)

    def test_store_and_recall(self, temp_db):
        """Test storing and recalling experiences"""
        store = ExperienceStore(db_path=temp_db)
        exp = Experience.create(
            problem="如何优化查询",
            reflection="添加索引",
        )
        store.store(exp)

        results = store.recall("查询")
        assert len(results) == 1
        assert results[0].problem == "如何优化查询"
        store.close()

    def test_record_recall_db(self, temp_db):
        """Test recording recall in database"""
        store = ExperienceStore(db_path=temp_db)
        exp = Experience.create(
            problem="测试问题",
            reflection="测试反思",
        )
        exp_id = store.store(exp)

        store.record_recall(exp_id)
        results = store.recall("测试")
        assert results[0].recall_count == 1
        assert results[0].weight > 1.0
        store.close()

    def test_record_deny_db(self, temp_db):
        """Test recording deny in database"""
        store = ExperienceStore(db_path=temp_db)
        exp = Experience.create(
            problem="测试问题",
            reflection="测试反思",
        )
        exp_id = store.store(exp)

        store.record_deny(exp_id)
        results = store.recall("测试")
        assert results[0].deny_count == 1
        assert results[0].weight < 1.0
        store.close()

    def test_decay_all_db(self, temp_db):
        """Test decaying all experiences in database"""
        store = ExperienceStore(db_path=temp_db)
        exp = Experience.create(
            problem="测试问题",
            reflection="测试反思",
        )
        store.store(exp)

        updated = store.decay_all()
        assert updated == 1
        store.close()

    def test_get_stats_db(self, temp_db):
        """Test getting statistics from database"""
        store = ExperienceStore(db_path=temp_db)
        exp1 = Experience.create(problem="问题1", reflection="反思1")
        exp2 = Experience.create(problem="问题2", reflection="反思2")
        store.store(exp1)
        store.store(exp2)

        stats = store.get_stats()
        assert stats["total_experiences"] == 2
        assert stats["average_weight"] == 1.0
        store.close()

    def test_close_connection(self, temp_db):
        """Test closing database connection"""
        store = ExperienceStore(db_path=temp_db)
        store.close()
        assert store._conn is None


class TestLayeredMemory:
    """Test LayeredMemory class"""

    def test_init_default(self):
        """Test initialization with defaults"""
        lm = LayeredMemory()
        assert lm.common is not None
        assert lm.working is not None
        assert lm.experience is not None

    def test_init_with_custom_params(self):
        """Test initialization with custom parameters"""
        lm = LayeredMemory(
            experience_store=InMemoryExperienceStore(),
            common_extra={"Extra": {"en": "E", "role": "R"}},
            working_capacity=10,
        )
        assert lm.working._capacity == 10

    def test_inject_common_to_prompt(self):
        """Test injecting common knowledge to prompt"""
        lm = LayeredMemory()
        text = lm.inject_common_to_prompt()
        assert "灵字辈大家庭成员" in text
        assert "灵克" in text

    def test_record_experience(self):
        """Test recording experience"""
        lm = LayeredMemory()
        exp = Experience.create(
            problem="如何优化",
            reflection="使用缓存",
        )
        exp_id = lm.record_experience(exp)
        assert exp_id == exp.id

    def test_recall_experience(self):
        """Test recalling experiences"""
        lm = LayeredMemory()
        exp1 = Experience.create(
            problem="如何优化查询",
            reflection="添加索引",
        )
        exp2 = Experience.create(
            problem="如何处理错误",
            reflection="添加日志",
        )
        lm.record_experience(exp1)
        lm.record_experience(exp2)

        results = lm.recall_experience("查询")
        assert len(results) == 1
        assert results[0].problem == "如何优化查询"
        # The results contain experiences BEFORE record_recall is called
        # Check again to see that recall_count was updated
        results_after = lm.experience.recall("查询")
        assert results_after[0].recall_count == 1

    def test_meta_memory(self):
        """Test meta memory operations"""
        lm = LayeredMemory()
        lm.record_meta("擅长领域", "Python编程")
        lm.record_meta("不擅长领域", "机器学习")

        assert lm.get_meta("擅长领域") == "Python编程"
        assert lm.get_meta("不擅长领域") == "机器学习"
        assert lm.get_meta("未知") is None

    def test_shared_memory(self):
        """Test shared memory operations"""
        lm = LayeredMemory()
        lm.record_shared("团队共识", "使用TypeScript")
        lm.record_shared("架构决策", "微服务架构")

        assert lm.get_shared("团队共识") == "使用TypeScript"
        assert lm.get_shared("架构决策") == "微服务架构"

    def test_build_context_injection(self):
        """Test building context injection"""
        lm = LayeredMemory()
        lm.record_meta("擅长", "Python")
        lm.record_experience(
            Experience.create(
                problem="如何优化查询",
                reflection="添加索引",
            )
        )

        context = lm.build_context_injection("优化")  # Use keyword that matches
        assert "灵字辈大家庭成员" in context
        assert "相关经验" in context
        assert "认知边界" in context
        assert "如何优化查询" in context

    def test_build_context_injection_with_query(self):
        """Test building context injection with current query"""
        lm = LayeredMemory()
        lm.record_experience(
            Experience.create(
                problem="如何优化查询",
                reflection="添加索引",
            )
        )

        context = lm.build_context_injection(current_query="优化")  # Use keyword that matches
        assert "如何优化查询" in context
        assert "添加索引" in context

    def test_decay(self):
        """Test decaying experiences"""
        lm = LayeredMemory()
        lm.record_experience(
            Experience.create(
                problem="测试问题",
                reflection="测试反思",
            )
        )

        updated = lm.decay()
        assert updated == 1

    def test_close(self):
        """Test closing layered memory"""
        lm = LayeredMemory()
        lm.close()
        # Should not raise any exception

    def test_integration(self):
        """Test full integration of layered memory"""
        lm = LayeredMemory()

        # Record some experiences
        lm.record_experience(
            Experience.create(
                problem="如何优化查询",
                hypothesis="添加索引可以提升速度",
                action="在ID字段上添加索引",
                result="查询速度提升10倍",
                reflection="索引是数据库优化的关键手段",
                emotion=EmotionIntensity.HIGH,
                associations=("数据库", "优化", "索引"),
            )
        )

        lm.record_experience(
            Experience.create(
                problem="如何处理空指针异常",
                reflection="使用Optional类型和早期返回",
            )
        )

        # Record meta facts
        lm.record_meta("编程语言", "Python")
        lm.record_meta("数据库", "PostgreSQL")

        # Test recall
        results = lm.recall_experience("查询")
        assert len(results) == 1
        assert results[0].emotion == EmotionIntensity.HIGH

        # Test context building
        context = lm.build_context_injection("如何优化数据库")
        assert "灵字辈大家庭成员" in context
        assert "数据库" in context

        # Test decay
        updated = lm.decay()
        assert updated == 2

        # Cleanup
        lm.close()

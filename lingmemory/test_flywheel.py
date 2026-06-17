# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵码 P0 数据飞轮 — 测试
"""

import pytest

from lingmemory.core import init_db
from lingmemory.data_flywheel import DataFlywheel
from lingmemory.sanitize import Sanitize

_sanitize = Sanitize.text  # 保持向后兼容


@pytest.fixture
def flywheel(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return DataFlywheel(db_path, member="lingclaude")


class TestSanitize:
    """脱敏测试"""

    def test_redact_api_key(self):
        text = 'api_key = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"'
        result = _sanitize(text)
        assert "sk-abc" not in result
        assert "***REDACTED***" in result

    def test_redact_bearer(self):
        text = "Authorization: Bearer eyJhbGciOi-abc123.xyz"
        result = _sanitize(text)
        assert "eyJhbGciOi" not in result

    def test_redact_password(self):
        text = 'password = "supersecretpass123"'
        result = _sanitize(text)
        assert "supersecretpass123" not in result

    def test_keep_normal_code(self):
        text = "def hello(): print('world')"
        result = _sanitize(text)
        assert result == text


class TestRecord:
    """基础记录功能"""

    def test_record_pass(self, flywheel):
        rid = flywheel.record(
            prompt="修复FTS5同步",
            language="python",
            generated_code="conn.execute('INSERT INTO records_fts ...')",
            test_result="pass",
            file_path="core.py",
            project="lingmemory",
        )
        assert rid is not None

        record = flywheel.api.lm.get(rid)
        assert record["type"] == "code_trace"
        assert record["state"] == "active"
        assert record["data"]["test_result"] == "pass"
        assert record["data"]["language"] == "python"

    def test_record_fail_with_fix(self, flywheel):
        rid = flywheel.record(
            prompt="修复proxy路由",
            language="go",
            generated_code="// buggy code",
            test_result="fail",
            fix="// fixed code",
            fix_strategy="add nil check",
            stderr_snippet="panic: nil pointer dereference",
        )
        record = flywheel.api.lm.get(rid)
        assert record["data"]["test_result"] == "fail"
        assert record["data"]["fix"] == "// fixed code"
        assert record["data"]["fix_strategy"] == "add nil check"

    def test_record_error(self, flywheel):
        rid = flywheel.record(
            prompt="运行测试",
            language="bash",
            generated_code="pytest test_core.py",
            test_result="error",
            exit_code=2,
            stderr_snippet="ModuleNotFoundError",
        )
        record = flywheel.api.lm.get(rid)
        assert record["data"]["test_result"] == "error"
        assert record["data"]["exit_code"] == 2

    def test_sanitize_on_record(self, flywheel):
        rid = flywheel.record(
            prompt="config with secret",
            language="yaml",
            generated_code='api_key: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"',
            test_result="pass",
        )
        record = flywheel.api.lm.get(rid)
        assert "sk-abcdef" not in record["data"]["generated_code"]


class TestQualitySignal:
    """质量信号追加"""

    def test_add_quality_signal(self, flywheel):
        rid = flywheel.record(
            prompt="test",
            language="python",
            generated_code="x = 1",
            test_result="pass",
        )

        flywheel.add_quality_signal(rid, source="lingminopt_eval", score=0.85)
        flywheel.add_quality_signal(rid, source="lingxi_security", score=1.0)

        record = flywheel.api.lm.get(rid)
        qs = record["data"]["quality_signal"]
        assert qs["lingminopt_eval"]["score"] == 0.85
        assert qs["lingxi_security"]["score"] == 1.0

        # check event logged
        events = flywheel.api.lm.get_events(rid)
        quality_events = [e for e in events if e["event_type"] == "quality_tag"]
        assert len(quality_events) == 2


class TestConvenienceMethods:
    """便捷方法"""

    def test_record_edit_test(self, flywheel):
        rid = flywheel.record_edit_test(
            prompt="添加FTS5同步",
            file_path="core.py",
            language="python",
            edit_result={"code": "insert into fts...", "success": True},
            test_result={"passed": True, "exit_code": 0, "stderr": ""},
            project="lingmemory",
        )
        record = flywheel.api.lm.get(rid)
        assert record["data"]["test_result"] == "pass"

    def test_record_edit_test_failed(self, flywheel):
        rid = flywheel.record_edit_test(
            prompt="修复路由",
            file_path="router.go",
            language="go",
            edit_result={"code": "bad code", "success": True},
            test_result={"passed": False, "exit_code": 1, "stderr": "build failed"},
        )
        record = flywheel.api.lm.get(rid)
        assert record["data"]["test_result"] == "fail"
        assert record["data"]["exit_code"] == 1

    def test_record_from_bash(self, flywheel):
        rid = flywheel.record_from_bash(
            prompt="运行测试",
            command="python3 -m pytest tests/",
            exit_code=0,
            stdout="3 passed",
            stderr="",
        )
        record = flywheel.api.lm.get(rid)
        assert record["data"]["test_result"] == "pass"
        assert record["data"]["language"] == "bash"

    def test_record_from_bash_error(self, flywheel):
        rid = flywheel.record_from_bash(
            prompt="编译",
            command="go build ./...",
            exit_code=2,
            stdout="",
            stderr="undefined: foo",
        )
        record = flywheel.api.lm.get(rid)
        assert record["data"]["test_result"] == "error"


class TestStats:
    """统计"""

    def test_stats_empty(self, flywheel):
        stats = flywheel.get_stats()
        assert stats["total_traces"] == 0
        assert stats["pass_rate"] == 0

    def test_stats_with_data(self, flywheel):
        flywheel.record(prompt="t1", language="python", generated_code="x", test_result="pass")
        flywheel.record(prompt="t2", language="python", generated_code="y", test_result="fail")
        flywheel.record(prompt="t3", language="go", generated_code="z", test_result="pass")

        stats = flywheel.get_stats()
        assert stats["total_traces"] == 3
        assert stats["by_test_result"]["pass"] == 2
        assert stats["by_test_result"]["fail"] == 1
        assert stats["by_language"]["python"] == 2
        assert stats["by_language"]["go"] == 1
        assert abs(stats["pass_rate"] - 0.667) < 0.01


class TestBeginEnd:
    """begin/session模式"""

    def test_begin_returns_key(self, flywheel):
        key = flywheel.begin(
            prompt="重构core.py",
            language="python",
            file_path="core.py",
        )
        assert key == "core.py"
        assert flywheel._pending[key]["prompt"] == "重构core.py"

"""proxy_backend_health 单元测试"""
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "lingmemory"))
from proxy_backend_health import (
    calculate_age_days,
    determine_lamp,
    lamp_action,
    check_backends,
    format_report,
    YELLOW_HOURS,
    ORANGE_DAYS,
    RED_DAYS,
)

CST = timezone(timedelta(hours=8))


class TestCalculateAgeDays:
    def test_recent_date(self):
        now = datetime.now(CST).strftime("%Y-%m-%d")
        assert calculate_age_days(now) == 0

    def test_30_days_ago(self):
        d = (datetime.now(CST) - timedelta(days=30)).strftime("%Y-%m-%d")
        assert calculate_age_days(d) == 30

    def test_invalid_date(self):
        assert calculate_age_days("invalid") == -1

    def test_none(self):
        assert calculate_age_days(None) == -1


class TestDetermineLamp:
    def test_red_90_days(self):
        assert determine_lamp(90, False, None) == "RED"

    def test_red_100_days(self):
        assert determine_lamp(100, True, "2026-07-19T18:04:37Z") == "RED"

    def test_orange_30_days(self):
        assert determine_lamp(30, False, None) == "ORANGE"

    def test_orange_45_days(self):
        assert determine_lamp(45, True, None) == "ORANGE"

    def test_yellow_no_use_over_1_day(self):
        assert determine_lamp(5, False, None) == "YELLOW"

    def test_yellow_stale_last_used(self):
        stale = "2026-07-01T00:00:00Z"
        assert determine_lamp(10, True, stale) == "YELLOW"

    def test_green_recent_use(self):
        recent = datetime.now(timezone.utc).isoformat()
        assert determine_lamp(5, True, recent) == "GREEN"

    def test_green_fresh_build(self):
        assert determine_lamp(0, True, None) == "GREEN"


class TestLampAction:
    def test_red_action(self):
        action = lamp_action("RED", "lingflow", 95)
        assert "删除" in action
        assert "lingflow" in action

    def test_orange_action(self):
        action = lamp_action("ORANGE", "lingzhi", 35)
        assert "DEPRECATED" in action

    def test_yellow_action(self):
        action = lamp_action("YELLOW", "lingcreate", 5)
        assert "告警" in action

    def test_green_action(self):
        action = lamp_action("GREEN", "lingclaude", 1)
        assert "正常" in action


class TestCheckBackends:
    def test_returns_list(self):
        results = check_backends(proxy_status={})
        assert isinstance(results, list)
        assert len(results) > 0

    def test_has_lamp_field(self):
        results = check_backends(proxy_status={})
        for r in results:
            if "error" not in r:
                assert "lamp" in r

    def test_with_proxy_status(self):
        proxy_status = {
            "lingclaude": {
                "running": True,
                "last_used": datetime.now(timezone.utc).isoformat(),
                "tool_count": 26,
                "last_error": None,
            }
        }
        results = check_backends(proxy_status=proxy_status)
        claude = [r for r in results if r.get("backend") == "lingclaude"][0]
        assert claude["running"] is True
        assert claude["tool_count"] == 26

    def test_lingflow_error_propagates(self):
        proxy_status = {
            "lingflow": {
                "running": False,
                "last_used": None,
                "last_error": "exited (code 2)",
                "tool_count": None,
            }
        }
        results = check_backends(proxy_status=proxy_status)
        flow = [r for r in results if r.get("backend") == "lingflow"][0]
        assert flow["last_error"] == "exited (code 2)"


class TestFormatReport:
    def test_contains_summary(self):
        results = check_backends(proxy_status={})
        report = format_report(results)
        assert "汇总" in report

    def test_contains_backend_names(self):
        results = check_backends(proxy_status={})
        report = format_report(results)
        assert "lingclaude" in report


class TestThresholds:
    def test_yellow_threshold(self):
        assert YELLOW_HOURS == 24

    def test_orange_threshold(self):
        assert ORANGE_DAYS == 30

    def test_red_threshold(self):
        assert RED_DAYS == 90

"""startup_watch 自优化哨兵测试（2026-10-07）。

覆盖：阈值配置接线 / 判定登记 / 幂等去重 / 快启动不登记 / fail-soft / 旁路开关。
不触碰真实 data/selfopt/failure_backlog.jsonl（monkeypatch DEFAULT_BACKLOG 到 tmp_path）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.self_optimizer import startup_watch as sw
from lingclaude.core.config import TriggerConfig, lingclaudeConfig


@pytest.fixture()
def watch(tmp_path, monkeypatch):
    """隔离：临时 backlog + 重置进程内状态 + 强制启用哨兵。"""
    backlog = tmp_path / "failure_backlog.jsonl"
    monkeypatch.setattr(sw, "DEFAULT_BACKLOG", backlog)
    monkeypatch.delenv("LINGCLAUDE_DISABLE_STARTUP_WATCH", raising=False)
    sw._reset_for_tests()
    yield sw
    sw._reset_for_tests()


def _read_backlog(path: Path) -> list[dict]:
    """读 backlog，跳过坏行（与 backlog_executor / startup_watch 读取语义一致）。"""
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------------- #
# 配置接线
# ---------------------------------------------------------------------- #
class TestConfigWiring:
    def test_trigger_config_has_default_3s(self):
        assert TriggerConfig().startup_time_threshold_s == 3.0

    def test_from_dict_reads_threshold(self):
        cfg = lingclaudeConfig.from_dict(
            {"self_optimizer": {"triggers": {"startup_time_threshold_s": 1.5}}}
        )
        assert cfg.triggers.startup_time_threshold_s == 1.5

    def test_from_dict_default_when_absent(self):
        cfg = lingclaudeConfig.from_dict({})
        assert cfg.triggers.startup_time_threshold_s == 3.0


# ---------------------------------------------------------------------- #
# 判定 + 登记
# ---------------------------------------------------------------------- #
class TestRecord:
    def test_slow_startup_appends_task(self, watch, monkeypatch):
        monkeypatch.setattr(watch, "_PROCESS_START", watch.time.monotonic() - 10.0)
        watch._record()
        entries = _read_backlog(watch.DEFAULT_BACKLOG)
        assert len(entries) == 1
        e = entries[0]
        assert e["type"] == "startup_slow"
        assert e["status"] == "pending"
        assert e["threshold_s"] == 3.0
        assert e["current_value_s"] > 3.0
        assert e["cluster_key"].startswith("startup::")
        assert "fix_suggestion" in e and "hypothesis" in e

    def test_fast_startup_appends_nothing(self, watch):
        watch._record()  # 刚 _reset_for_tests，耗时 ~0s，远低于 3s
        assert _read_backlog(watch.DEFAULT_BACKLOG) == []

    def test_idempotent_within_process(self, watch, monkeypatch):
        monkeypatch.setattr(watch, "_PROCESS_START", watch.time.monotonic() - 10.0)
        watch._record()
        watch._record()  # 进程内幂等：第二次不追加
        assert len(_read_backlog(watch.DEFAULT_BACKLOG)) == 1

    def test_dedup_same_day_cluster_key(self, watch, monkeypatch):
        # 模拟「同日第二次慢启动」：手动写入一条今日条目，再 _record 应跳过
        monkeypatch.setattr(watch, "_PROCESS_START", watch.time.monotonic() - 10.0)
        today = sw.datetime.now().strftime(sw._DEDUP_DATE_FMT)
        pre = {
            "cluster_key": f"startup::{today}", "type": "startup_slow", "status": "pending",
        }
        watch.DEFAULT_BACKLOG.write_text(
            json.dumps(pre, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        watch._record()
        entries = _read_backlog(watch.DEFAULT_BACKLOG)
        assert len(entries) == 1  # 不重复追加
        assert entries[0] == pre  # 原条目不被动过

    def test_threshold_zero_disables(self, watch, monkeypatch):
        monkeypatch.setattr(watch, "_PROCESS_START", watch.time.monotonic() - 10.0)
        monkeypatch.setattr(
            watch, "_load_threshold", lambda: 0.0
        )
        watch._record()
        assert _read_backlog(watch.DEFAULT_BACKLOG) == []

    def test_disabled_env_bypasses(self, watch, monkeypatch):
        monkeypatch.setattr(watch, "_PROCESS_START", watch.time.monotonic() - 10.0)
        monkeypatch.setenv("LINGCLAUDE_DISABLE_STARTUP_WATCH", "1")
        watch._record()
        assert _read_backlog(watch.DEFAULT_BACKLOG) == []

    def test_corrupt_backlog_line_does_not_block(self, watch, monkeypatch):
        monkeypatch.setattr(watch, "_PROCESS_START", watch.time.monotonic() - 10.0)
        watch.DEFAULT_BACKLOG.write_text("{bad json\nnot-json\n", encoding="utf-8")
        watch._record()  # fail-soft：坏行跳过，仍能写入新条目
        entries = _read_backlog(watch.DEFAULT_BACKLOG)
        assert any(e.get("type") == "startup_slow" for e in entries)


# ---------------------------------------------------------------------- #
# run-phase 口径
# ---------------------------------------------------------------------- #
class TestRunPhase:
    def test_run_start_overrides_process_start(self, watch, monkeypatch):
        # 进程启动早（会超阈值），但 run-phase 刚打点（远低于阈值）→ 不登记
        monkeypatch.setattr(watch, "_PROCESS_START", watch.time.monotonic() - 10.0)
        watch.maybe_record_run_start()  # run-phase 起点 = 现在
        watch._record()
        assert _read_backlog(watch.DEFAULT_BACKLOG) == []

    def test_run_phase_slow_also_recorded(self, watch, monkeypatch):
        # run-phase 本身慢 → 登记
        monkeypatch.setattr(watch, "_PROCESS_START", watch.time.monotonic() - 10.0)
        watch.maybe_record_run_start()
        monkeypatch.setattr(watch, "_PROCESS_START", watch.time.monotonic() - 1.0)
        # 把 run-phase 起点也拨回 10s 前：直接 hack _RUN_START
        monkeypatch.setattr(watch, "_RUN_START", watch.time.monotonic() - 10.0)
        watch._record()
        assert len(_read_backlog(watch.DEFAULT_BACKLOG)) == 1


# ---------------------------------------------------------------------- #
# 装配 / 打点
# ---------------------------------------------------------------------- #
class TestInstall:
    def test_install_is_idempotent(self, watch, monkeypatch):
        monkeypatch.delenv("LINGCLAUDE_DISABLE_STARTUP_WATCH", raising=False)
        monkeypatch.setattr(watch, "_INSTALLED", False)
        watch.install()
        watch.install()  # 第二次不重复注册
        assert watch._INSTALLED is True

    def test_install_skipped_when_disabled(self, watch, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_DISABLE_STARTUP_WATCH", "1")
        monkeypatch.setattr(watch, "_INSTALLED", False)
        watch.install()
        assert watch._INSTALLED is False

    def test_mark_sets_process_start(self, watch, monkeypatch):
        fake = 12345.0
        monkeypatch.setattr(watch.time, "monotonic", lambda: fake)
        watch.mark()
        assert watch._PROCESS_START == fake

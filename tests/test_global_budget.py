"""P3 跨会话全局预算账本（2026-10-22）。

覆盖：
- 入账原子性：多进程并发 add_tokens 无丢失（flock 串行化）
- 日分桶：跨日隔离，只增当日桶
- 额度判定：cap 内 allowed=True；超限 allowed=False 且 total 不涨
- 冻结语义：gate.record_model_call 在全局超限后拒绝会话侧增量
- fail-open：账本损坏/策略缺失不反噬会话记账
- 隔离：ENV 覆盖账本路径，测试/多项目互不污染
"""
from __future__ import annotations

import json

import pytest

from lingclaude.core import global_budget as gb
from lingclaude.core import session_budget_gate as gate


@pytest.fixture()
def gb_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv(gb._ledger_lock_env, str(tmp_path / "gb" / "global_budget.json"))
    monkeypatch.setattr(gb, "load_policy_cap", lambda: 1000)
    gate.reset_gate_state()
    yield tmp_path
    gate.reset_gate_state()


class TestLedger:
    def test_add_and_read(self, gb_sandbox):
        allowed, total = gb.add_tokens(300)
        assert allowed and total == 300
        allowed, total = gb.add_tokens(500)
        assert allowed and total == 800
        assert gb.read_day()["total"] == 800

    def test_cap_blocks(self, gb_sandbox):
        gb.add_tokens(900)
        allowed, total = gb.add_tokens(200)  # 900+200 > 1000
        assert allowed is False
        assert total == 900  # 拒绝增量，账面不动
        assert gb.read_day()["total"] == 900

    def test_session_attribution(self, gb_sandbox):
        gb.add_tokens(100)
        gb.add_tokens(50)
        day = gb.read_day()
        assert sum(day["sessions"].values()) == 150
        assert len(day["sessions"]) == 1  # 同进程同会话

    def test_multiprocess_consistency(self, gb_sandbox):
        """并发入账无丢失（flock 串行化，多线程模拟多进程）。"""
        import threading

        def worker(n):
            for _ in range(20):
                gb.add_tokens(5)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 8×20×5=800 ≤ cap 1000 → 全部入账
        assert gb.read_day()["total"] == 800

    def test_corrupt_ledger_fail_open(self, gb_sandbox):
        p = gb.ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{broken json", encoding="utf-8")
        allowed, total = gb.add_tokens(100)
        assert allowed is True  # fail-open 不反噬

    def test_no_cap_passthrough(self, gb_sandbox, monkeypatch):
        monkeypatch.setattr(gb, "load_policy_cap", lambda: 0)
        allowed, _ = gb.add_tokens(10**9)
        assert allowed is True  # 未启用=不限


class TestGateWiring:
    def test_gate_freezes_on_global_exhaustion(self, gb_sandbox):
        gb.add_tokens(999)  # 留 1 token 余量
        gate.record_model_call(10, 5)  # 15 > 1 → 全局拒绝
        # 会话侧被冻结：计数未增长
        from lingclaude.core.session_budget import SessionBudgetLoader

        snap = gate.snapshot_for_display()
        counters = snap.get("counters") or {}
        assert counters.get("input_tokens", 0) == 0 and counters.get("model_calls", 0) == 0

    def test_gate_records_under_cap(self, gb_sandbox):
        gate.record_model_call(10, 5)
        snap = gate.snapshot_for_display()
        counters = snap.get("counters") or {}
        assert counters.get("input_tokens", 0) == 10
        assert gb.read_day()["total"] == 15

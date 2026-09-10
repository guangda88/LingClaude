"""health_inspect RTT 探测测试 — 本地起临时 listener, 环境无关。

scripts/ 非 import 包 → spec_from_file_location 加载（test_sdt_lc_002_v2_1.py 同款惯例,
但该脚本可 import: 纯函数无副作用, 用 sys.path 方式不污染, 直接 spec 加载）。
"""
from __future__ import annotations

import importlib.util
import socket
import threading
from pathlib import Path

import pytest

_SCRIPT = Path("/home/ai/lingclaude/scripts/health_inspect.py")


@pytest.fixture(scope="module")
def hi():
    spec = importlib.util.spec_from_file_location("health_inspect_mod", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    # 后台持续 accept 排水: 模拟真实服务的并发受理（listen(1)+无人 accept 会把
    # backlog 打爆, 连续快速 connect 会被拒 — 那是装置假象, 非产品行为）
    import threading

    stop = threading.Event()

    def _drain():
        s.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = s.accept()
                conn.close()
            except OSError:
                continue

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    yield str(s.getsockname()[1])
    stop.set()
    t.join(timeout=1)
    s.close()


def test_probe_reachable(hi, listener):
    r = hi.probe_port_rtt(listener)
    assert r["reachable"] is True
    assert len(r["samples_ms"]) == hi.PORT_PROBE_ROUNDS
    assert {"min_ms", "med_ms", "max_ms"} <= set(r)
    assert 0 <= r["min_ms"] <= r["med_ms"] <= r["max_ms"]


def test_probe_unreachable_closed_port(hi):
    # 关闭端口: connect 立即 ECONNREFUSED（本机无网络依赖）
    r = hi.probe_port_rtt("1")
    assert r["reachable"] is False
    assert r["samples_ms"] == []
    assert "min_ms" not in r


def test_tcp_probe_returns_none_on_refused(hi):
    assert hi._tcp_probe(1) is None


def test_inspect_report_format(hi, listener, monkeypatch, tmp_path):
    # 端口表换成可控端口, 日志重定向 tmp
    monkeypatch.setattr(hi, "PORTS", {listener: "unit-test"})
    monkeypatch.setattr(hi, "LOG", tmp_path / "health.log")
    monkeypatch.setattr(hi, "sh", lambda cmd: "")
    oks, alerts = hi.inspect()
    assert any("可达 rtt" in o for o in oks)
    assert (tmp_path / "health.log").exists()

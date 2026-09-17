"""remote_probe：三 remote 连通性分层探针（DNS → TCP → 协议）。

来源：2026-09-18 push 网络问题复盘——当日报错表象是 "push 失败"，
分层定位后才知根因是 DNS 解析失败 + 22/443 出站被网络策略阻断。
本探针把分层定位固化为可 query 的 record（铁律 3/J4：探测结论全入账，
非本次探测的记录如实标注 stale=True，不冒充新鲜结论）。

铁律锚点：
- J4：每次探测结果（成功/失败/异常）全 record 化，可回放；
- 铁律 8 时效域：record 带 timestamp，隔日读取 stale=True——时效是
  失联兜底，读旧账必须自知是旧账；
- 单实现：与 engine/git 共享远端清单与黑洞判定，不另起实现。

安全边界：
- 只做只读探测（DNS 查询 / TCP connect / HTTP HEAD），不推送任何数据；
- DNS 用 socket.getaddrinfo，TCP 用 socket.create_connection（超时封顶 5s），
  HTTP 仅 https 443 端口发 HEAD；
- 不读取/不落盘任何凭据。
"""
from __future__ import annotations

import socket
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from lingclaude.core.state_store import StateStore
from lingclaude.engine.git import _is_blackhole_remote_url, _run_git

RECORD_TYPE = "git_remote_probe"
TCP_TIMEOUT_S = 5.0
HTTP_TIMEOUT_S = 8.0


def _classify_url(url: str) -> dict[str, Any] | None:
    """remote URL → {scheme, host, port}。解析失败返回 None。"""
    try:
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        host = parsed.hostname or ""
        if not host:
            # scp 风格 git@host:path
            if "@" in url and ":" in url:
                host = url.split("@", 1)[1].split(":", 1)[0]
                scheme = "ssh"
            else:
                return None
        port = parsed.port
        if port is None:
            port = {"https": 443, "http": 80, "ssh": 22, "git": 9418}.get(scheme)
        return {"scheme": scheme, "host": host, "port": port}
    except Exception:
        return None


def _dns_check(host: str) -> dict[str, Any]:
    # 注意：socket.getaddrinfo 无 timeout 参数——数值 IP 走 AI_NUMERICHOST
    # 路径即时返回；主机名走系统解析器超时（resolv.conf 默认 ~5s），不自行包装。
    try:
        infos = socket.getaddrinfo(host, None)
        addrs = sorted({i[4][0] for i in infos})
        return {"ok": True, "resolved": addrs[:4]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _tcp_check(host: str, port: int | None) -> dict[str, Any]:
    if port is None:
        return {"ok": False, "error": "no-port", "skipped": True}
    try:
        start = time.monotonic()
        with socket.create_connection((host, port), timeout=TCP_TIMEOUT_S):
            elapsed = time.monotonic() - start
        return {"ok": True, "latency_ms": round(elapsed * 1000)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _protocol_check(scheme: str, host: str, port: int | None) -> dict[str, Any]:
    """协议层探测。HTTP(S) 发 HEAD；SSH/GIT 只测 TCP 层，标 manual。"""
    if scheme in ("http", "https") and port in (80, 443):
        try:
            url = f"{scheme}://{host}:{port}"
            req = urllib.request.Request(url, method="HEAD")
            urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S)
            return {"ok": True, "method": "HEAD"}
        except Exception as e:
            return {"ok": False, "method": "HEAD", "error": f"{type(e).__name__}: {e}"}
    return {
        "ok": None,
        "method": "tcp-only",
        "manual": True,
        "note": f"port {port} 无应用层探测（不发协议头），需人工/专用工具验证",
    }


def probe_remote(url: str) -> dict[str, Any]:
    """单 remote 三层探测：DNS → TCP → 协议。纯函数，不落盘。"""
    out: dict[str, Any] = {"url": url, "ok": False}
    parsed = _classify_url(url)
    if not parsed:
        out["layers"] = {"parse": {"ok": False, "error": "unparseable-url"}}
        return out
    out["scheme"], out["host"], out["port"] = parsed["scheme"], parsed["host"], parsed["port"]

    dns = _dns_check(parsed["host"])
    tcp: dict[str, Any] = {"ok": False, "skipped": True, "reason": "dns-failed"}
    proto: dict[str, Any] = {"ok": None, "skipped": True, "reason": "tcp-unreachable"}
    if dns["ok"]:
        tcp = _tcp_check(parsed["host"], parsed["port"])
        if tcp.get("ok"):
            proto = _protocol_check(parsed["scheme"], parsed["host"], parsed["port"])

    out["layers"] = {"dns": dns, "tcp": tcp, "protocol": proto}
    out["ok"] = bool(dns["ok"] and tcp.get("ok") and proto.get("ok") is not False)
    return out


def probe_all_remotes(path: str = ".", store: StateStore | None = None,
                      root: Path | None = None) -> dict[str, Any]:
    """对仓库全部 remote 做三层探测，结果逐 remote 入 StateStore record。

    record: git_remote_probe/<remote>，payload 带 timestamp 与 ok；
    读取方按 timestamp 判新鲜度（隔日 stale=True）。
    """
    store = store or StateStore()
    r = _run_git(["remote", "-v"], cwd=path)
    remotes: dict[str, str] = {}
    if r.success:
        for line in r.output.strip().split("\n"):
            if line:
                parts = line.split()
                if len(parts) >= 2:
                    remotes.setdefault(parts[0], parts[1])

    results: dict[str, Any] = {}
    now_iso = datetime.now(timezone.utc).isoformat()
    for name, url in remotes.items():
        res = probe_remote(url)
        res["blackhole"] = _is_blackhole_remote_url(url)
        res["timestamp"] = now_iso
        res["stale"] = False
        results[name] = res
        try:
            store.save(RECORD_TYPE, name, res, root)
        except Exception as e:  # 记账失败不吞探测结论，只标注
            res["record_error"] = str(e)
    return {"timestamp": now_iso, "remotes": results,
            "count": len(results), "all_ok": all(
                v.get("ok") for v in results.values()) if results else None}


def read_probe_record(remote: str, store: StateStore | None = None,
                      root: Path | None = None,
                      fresh_seconds: float = 3600.0) -> dict[str, Any] | None:
    """读某 remote 的最近探测 record；超过 fresh_seconds 视为 stale。"""
    store = store or StateStore()
    rec = store.load(RECORD_TYPE, remote, root)
    if rec is None:
        return None
    rec = dict(rec)
    try:
        ts = datetime.fromisoformat(rec["timestamp"])
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        rec["age_s"] = round(age)
        rec["stale"] = age > fresh_seconds
    except Exception:
        rec["stale"] = True
    return rec

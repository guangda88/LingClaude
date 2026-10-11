#!/usr/bin/env python3
"""豁免提交后台全量 pytest 复核 — N1 终态化。

背景：LING_AUDIT_FAST 豁免时 pre-commit 跳过全量测试，post-commit 启动
后台 pytest 复核。此前 fire-and-forget（Popen 后无人问津），失败/超时/
进程被杀均静默 —— 与「豁免必须被兜底」的机制目标相悖。

本脚本职责（post-commit 以 --watch 拉起，脱离终端会话独立运行）：
  1. 执行全量 pytest（自适应降并发重试，防线程配额崩溃）
  2. 终态化（必须发生，任何路径都逃不出这三种）：
     - PASS  → .audit/exempt_review_<commit>_<ts>.json 记 PASS
     - FAIL  → 记 FAIL + LingBus 告警
     - 超时  → 记 FAIL(timeout) + LingBus 告警
  3. 清除 marker（marker 存在 = 复核未终态）

配套 --check-stale：复核 watcher 自身被杀时的兜底，扫残留 marker
按超时终态化。可挂 cron / sla_tracker 周期调。

约束：脚本本体 stdlib-only（hook 环境）；lingclaude 模块仅 FAIL 告警
分支延迟导入。终态化本身永不 raise。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

MARKER_DIR = Path("/tmp/lingclaude_exempt_review")
DEFAULT_TIMEOUT = 1800  # 秒；全量套件在空载机器约 5-10 分钟，1 倍余量取 30 分钟
RETRY_CONCURRENCY = (4, 2, 1)
QUOTA_ERR_MARK = "can't start new thread"


def _claim_marker(marker_path: Path) -> bool:
    """原子认领 marker：O_CREAT|O_EXCL 独占创建 <marker>.claim 哨兵文件。

    场景：同一 commit 的 watcher 被拉起多次（会话 OOM 重试、post-commit 与
    check_stale 撞车）时，只允许一个执行者跑 pytest 并终态化，其余立即让位 —
    否则双跑浪费 CPU/内存配额（曾实证触发 OOM-kill），双写结果文件互相覆盖。
    claim 文件随 /tmp 清理自然消亡，不参与终态语义。
    """
    claim = marker_path.with_suffix(marker_path.suffix + ".claim")
    try:
        fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    else:
        os.close(fd)
        return True


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _alert(subject: str, body: str) -> None:
    """FAIL 时发 LingBus 告警；best-effort，失败只落 stderr。"""
    try:
        sys.path.insert(0, str(_repo_root()))
        from lingclaude.coordination.alert import send_lingbus_alert

        send_lingbus_alert(subject, body)
    except Exception as e:  # noqa: BLE001 — 兜底的兜底
        print(f"[exempt-review] alert dispatch failed: {e}", file=sys.stderr)


def _write_result(root: Path, info: dict, *, status: str, reason: str,
                  pytest_rc: int | None, duration: float) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = root / ".audit" / f"exempt_review_{info['commit']}_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "commit": info["commit"],
        "status": status,
        "reason": reason,
        "pytest_rc": pytest_rc,
        "duration_seconds": round(duration, 1),
        "log": info["log"],
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _finalize(root: Path, info: dict, *, status: str, reason: str,
              pytest_rc: int | None, marker: Path | None) -> Path:
    duration = time.time() - info.get("ts", time.time())
    out = _write_result(root, info, status=status, reason=reason,
                        pytest_rc=pytest_rc, duration=duration)
    if marker is not None and marker.exists():
        marker.unlink()
    print(f"[exempt-review] {status}: {reason} → {out}")
    if status != "PASS":
        _alert(
            f"[N1复核] 豁免提交 {info['commit']} 全量测试复核 {status}",
            f"commit={info['commit']} status={status} reason={reason} "
            f"pytest_rc={pytest_rc} result={out}",
        )
    return out


def _finalize_no_log(root: Path, info: dict, marker: Path | None) -> Path:
    return _finalize(root, info, status="FAIL", reason="log_missing",
                     pytest_rc=None, marker=marker)


def _run_pytest_with_retry(info: dict, deadline: float) -> int | None:
    """跑全量 pytest；线程配额崩溃自动降并发；返回 rc，超时返回 None。"""
    log_path = Path(info["log"])
    with log_path.open("a", encoding="utf-8") as logf:
        for n in RETRY_CONCURRENCY:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", "tests/", "--tb=line", "-q", "-n", str(n)],
                    cwd=str(info.get("root") or _repo_root()),
                    stdout=logf, stderr=subprocess.STDOUT,
                    timeout=remaining,
                )
            except subprocess.TimeoutExpired:
                logf.write(f"\n[exempt-review] timeout after {DEFAULT_TIMEOUT}s\n")
                logf.flush()
                return None
            rc = proc.returncode
            logf.write(f"\nEXIT:{rc}\n")
            logf.flush()
            if rc == 0:
                return rc
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            if QUOTA_ERR_MARK not in tail or n == RETRY_CONCURRENCY[-1]:
                return rc
            logf.write(f"[retry] thread quota exhausted, downgrading to -n {n // 2 or 1}\n")
            logf.flush()
    return 1


def watch(marker_path: Path) -> int:
    """复核主流程：执行 pytest → 终态化。由 post-commit 拉起。"""
    if not _claim_marker(marker_path):
        # 已有并发执行者认领（OOM 重试/钩子撞车）— 让位，不重复跑 pytest
        print(f"[exempt-review] marker already claimed, skip: {marker_path.name}",
              file=sys.stderr)
        return 0
    info = json.loads(marker_path.read_text(encoding="utf-8"))
    root = Path(info.get("root") or _repo_root())
    log_path = Path(info["log"])
    timeout = int(info.get("timeout", DEFAULT_TIMEOUT))
    deadline = info.get("ts", time.time()) + timeout

    # 自愈兜底(N1): 每次被拉起顺带清理历史残留 — 之前的 watcher 被杀时
    # marker 会遗留, 只要豁免机制还在发生, 残留终会被后续拉起清掉
    try:
        check_stale(marker_path.parent, timeout, skip=marker_path)
    except Exception:  # noqa: BLE001 — 自愈失败不影响本次复核
        pass

    if not log_path.exists():
        # 新时序: marker 先落、日志由本 watcher 创建 — 缺失只补触,
        # log_missing 终态语义留给 check_stale(watcher 根本没启动的场景)
        log_path.touch()
    rc = _run_pytest_with_retry(info, deadline)
    if rc is None:
        _finalize(root, info, status="FAIL", reason="timeout",
                  pytest_rc=None, marker=marker_path)
        return 1
    if rc == 0:
        _finalize(root, info, status="PASS", reason="pytest_ok",
                  pytest_rc=0, marker=marker_path)
        return 0
    return _finalize(root, info, status="FAIL", reason="pytest_failed",
                     pytest_rc=rc, marker=marker_path)


def check_stale(marker_dir: Path, timeout: int, *, skip: Path | None = None) -> int:
    """兜底：watcher 被杀时残留 marker 按超时终态化。返回处理数。

    skip: 跳过的 marker（--watch 自愈模式下用于排除自己）。
    """
    handled = 0
    if not marker_dir.exists():
        return 0
    now = time.time()
    for marker in sorted(marker_dir.glob("*.json")):
        if skip is not None and marker.resolve() == skip.resolve():
            continue
        try:
            info = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"[exempt-review] skip corrupt marker {marker}: {e}", file=sys.stderr)
            continue
        if now - info.get("ts", now) < timeout:
            continue
        root = Path(info.get("root") or _repo_root())
        log_path = Path(info["log"])
        if not log_path.exists():
            _finalize_no_log(root, info, marker)
        else:
            _finalize(root, info, status="FAIL", reason="watcher_timeout",
                      pytest_rc=None, marker=marker)
        handled += 1
    return handled


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path, metavar="MARKER",
                        help="执行复核并终态化（post-commit 拉起）")
    parser.add_argument("--check-stale", action="store_true",
                        help="扫描残留 marker 按超时终态化（兜底 watcher 被杀）")
    parser.add_argument("--marker-dir", type=Path, default=MARKER_DIR,
                        help="marker 目录（默认 %(default)s）")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    try:
        if args.watch is not None:
            return watch(args.watch)
        if args.check_stale:
            check_stale(args.marker_dir, args.timeout)
            return 0
    except Exception as e:  # noqa: BLE001 — 终态化组件自身不许崩出非受控状态
        print(f"[exempt-review] FATAL: {e}", file=sys.stderr)
        return 2
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

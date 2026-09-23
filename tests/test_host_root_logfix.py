"""R-toolbar-logfix (2026-09-23): 宿主 root 文件兜底，根治 lastResort 裸写 tty。

背景: 宿主进程对部分 logger 子树（core.hallucination_guard 等）无 handler，
logging.lastResort 兜底裸写 sys.stderr，与 CLI toolbar 重绘并发交错，
撕裂出 "●allucination_guard" / "fact:18" 等混排行。
机制: root 一旦有 handler，lastResort 永不触发。
"""

from __future__ import annotations

import logging
import sys
from unittest import mock

from lingclaude.cli.app import (
    _ROOT_LOG_PATH,
    _install_root_file_logging,
)


def _clean_root() -> list:
    root = logging.getLogger()
    markers = [h for h in root.handlers if getattr(h, "_is_lingclaude_guard", False)]
    for h in markers:
        root.removeHandler(h)
        h.close()
    root._lingclaude_file_guard = False  # type: ignore[attr-defined]
    return markers


def test_install_attaches_file_handler_and_sets_flag(tmp_path):
    """挂载后 root 有 handler 且标记置位（幂等前提）。"""
    _clean_root()
    try:
        with mock.patch(
            "lingclaude.cli.app._ROOT_LOG_PATH", tmp_path / "host_root.log"
        ):
            _install_root_file_logging()
        root = logging.getLogger()
        guards = [h for h in root.handlers if getattr(h, "_is_lingclaude_guard", False)]
        assert len(guards) == 1
        assert root._lingclaude_file_guard is True
    finally:
        _clean_root()


def test_install_idempotent(tmp_path):
    """重复调用不叠加 handler。"""
    _clean_root()
    try:
        with mock.patch(
            "lingclaude.cli.app._ROOT_LOG_PATH", tmp_path / "host_root.log"
        ):
            _install_root_file_logging()
            _install_root_file_logging()
        guards = [h for h in logging.getLogger().handlers if getattr(h, "_is_lingclaude_guard", False)]
        assert len(guards) == 1
    finally:
        _clean_root()


def test_warning_records_no_longer_hit_lastresort(tmp_path):
    """核心回归：守卫告警经 root handler 落文件，不再走 lastResort 裸 stderr。

    lastResort 的行为特征：裸写 message、无前缀。此处断言：
    1) 记录被 root 上的 guard handler 捕获并格式化落盘；
    2) stderr 无任何裸写内容。
    """
    _clean_root()
    log_file = tmp_path / "host_root.log"
    try:
        with mock.patch("lingclaude.cli.app._ROOT_LOG_PATH", log_file):
            _install_root_file_logging()
        err_cap = _CaptureStream()
        record = logging.LogRecord(
            name="core.hallucination_guard",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="hallucination_guard: 检测到 %d 个问题: %s",
            args=(2, ["hard_fact: x", "unsupported: y"]),
            exc_info=None,
        )
        with mock.patch.object(sys, "stderr", err_cap):
            # 模拟与生产一致的传播路径：子 logger → root handler
            logging.getLogger("core.hallucination_guard").handle(record)
            root_handlers = [
                h for h in logging.getLogger().handlers
                if getattr(h, "_is_lingclaude_guard", False)
            ]
            assert root_handlers, "guard handler 未挂到 root"
            # 手动过一遍 guard handler，验证文件侧格式化落盘
            root_handlers[0].handle(record)
        body = log_file.read_text(encoding="utf-8")
        assert "hallucination_guard" in body
        assert "检测到 2 个问题" in body
        assert "hard_fact" in body
        assert err_cap.getvalue() == "", f"stderr 被裸写: {err_cap.getvalue()!r}"
    finally:
        _clean_root()


class _CaptureStream:
    """最小可写流，捕获裸写内容用于断言。"""

    def __init__(self):
        self._buf = []

    def write(self, s):
        self._buf.append(s)

    def flush(self):
        pass

    def getvalue(self):
        return "".join(self._buf)


def test_default_log_path_under_home():
    """默认落点在 ~/.lingclaude/logs/host_root.log（与 n5 日志同目录惯例）。"""
    assert str(_ROOT_LOG_PATH).endswith("host_root.log")
    assert ".lingclaude" in str(_ROOT_LOG_PATH)

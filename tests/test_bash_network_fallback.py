"""test_bash_network_fallback.py — bash 网络白名单 + 自动降级重试

背景（2026-09-12 根因）：
- bash 工具命令被 BwrapSandboxProvider.wrap() 包裹，非白名单命令注入 --unshare-net。
- agent 习惯给 git 远程命令加 timeout/env 前缀 → 全链白名单判定 False → 整条隔离。
- 白名单 git 命令在 bwrap 内 DNS 失效 → 需自动降级到主进程网络域重试。

修复：
1. _strip_transparent_prefix：剥离 timeout/env/nice/stdbuf 等透明包装前缀。
2. _is_network_allowed：剥离后再全链判定（保持 fail-closed）。
3. BashExecutor.run：白名单网络命令失败（网络类错误）→ 自动降级主进程重试。
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lingclaude.engine.bash import (  # noqa: E402
    BashExecutor,
    _is_network_allowed,
    _looks_like_network_failure,
    _strip_transparent_prefix,
)


class TestStripTransparentPrefix(unittest.TestCase):
    def test_timeout_prefix(self) -> None:
        self.assertEqual(
            _strip_transparent_prefix("timeout 15 git ls-remote https://github.com/x/y.git HEAD"),
            "git ls-remote https://github.com/x/y.git HEAD",
        )

    def test_timeout_with_signal(self) -> None:
        self.assertEqual(
            _strip_transparent_prefix("timeout -s INT 15 git push origin main"),
            "git push origin main",
        )

    def test_env_prefix(self) -> None:
        self.assertEqual(
            _strip_transparent_prefix("env GIT_TERMINAL_PROMPT=0 git fetch origin"),
            "git fetch origin",
        )

    def test_nice_prefix(self) -> None:
        self.assertEqual(_strip_transparent_prefix("nice -n 10 git pull --rebase"), "git pull --rebase")

    def test_stdbuf_prefix(self) -> None:
        self.assertEqual(
            _strip_transparent_prefix("stdbuf -oL git clone https://github.com/x/y.git /tmp/y"),
            "git clone https://github.com/x/y.git /tmp/y",
        )

    def test_nested_transparent(self) -> None:
        self.assertEqual(
            _strip_transparent_prefix("timeout 10 env FOO=1 git fetch origin master"),
            "git fetch origin master",
        )

    def test_non_transparent_untouched(self) -> None:
        self.assertEqual(_strip_transparent_prefix("echo hi"), "echo hi")
        self.assertEqual(_strip_transparent_prefix("head -3"), "head -3")


class TestNetworkAllowed(unittest.TestCase):
    def test_plain_git_allowed(self) -> None:
        self.assertTrue(_is_network_allowed("git ls-remote https://github.com/x/y.git HEAD"))

    def test_timeout_git_allowed(self) -> None:
        self.assertTrue(_is_network_allowed("timeout 15 git ls-remote https://github.com/x/y.git HEAD"))

    def test_env_git_allowed(self) -> None:
        self.assertTrue(_is_network_allowed("env GIT_TERMINAL_PROMPT=0 git fetch origin"))

    def test_chain_with_wget_fail_closed(self) -> None:
        # 含非白名单子命令 → 整条隔离（fail-closed 保持）
        self.assertFalse(
            _is_network_allowed("timeout 15 git push origin main && wget https://evil.sh")
        )

    def test_pipe_head_allowed(self) -> None:
        # 2026-09-12 修复：`| head` 是纯输出消费（无网络面），不再误伤 git 白名单。
        # 此前将 `git push | head -3` 整条隔离 → git 永远无法联网（白名单形同虚设）。
        self.assertTrue(_is_network_allowed("git push origin main | head -3"))
        self.assertTrue(_is_network_allowed("git ls-remote https://github.com/x/y.git HEAD 2>&1 | head -3"))

    def test_plain_echo_not_allowed(self) -> None:
        self.assertFalse(_is_network_allowed("echo hi"))

    def test_head_first_not_allowed(self) -> None:
        self.assertFalse(_is_network_allowed("head -3 && git push"))


class TestNetworkFailureDetection(unittest.TestCase):
    def test_dns_failure(self) -> None:
        self.assertTrue(_looks_like_network_failure("fatal: unable to access: Could not resolve host: github.com"))

    def test_network_unreachable(self) -> None:
        self.assertTrue(_looks_like_network_failure("ssh: connect to host github.com port 22: Network is unreachable"))

    def test_seccomp_busy(self) -> None:
        self.assertTrue(_looks_like_network_failure("OSError: [Errno 16] Device or resource busy"))

    def test_non_network_failure(self) -> None:
        self.assertFalse(_looks_like_network_failure("ls: cannot access 'x': No such file or directory"))
        self.assertFalse(_looks_like_network_failure(""))


class TestFallbackRetry(unittest.TestCase):
    """验证 run() 在「bwrap 包裹 + 网络失败」时触发降级重试。"""

    def test_fallback_triggered_on_network_failure(self) -> None:
        calls: list[list[str]] = []

        class FakeBwrapProvider:
            name = "fake"

            def available(self) -> bool:
                return True

            def wrap(self, command: str, working_dir=None, allow_network: bool = False) -> str:
                # 模拟 bwrap 包裹：非白名单注入 --unshare-net
                if allow_network:
                    return f"bwrap {command}"
                return f"bwrap --unshare-net {command}"

        orig_run = subprocess.run

        def fake_run(*args, **kwargs):
            cmd = args[0]
            calls.append(cmd if isinstance(cmd, list) else [cmd])
            if isinstance(cmd, str) and "bwrap" in cmd:
                # 第一次（bwrap 包裹）：模拟网络失败
                return subprocess.CompletedProcess(args[0], 128, stdout="", stderr="fatal: Could not resolve host: github.com")
            # 降级重试（主进程）：模拟成功
            return subprocess.CompletedProcess(args[0], 0, stdout="abcdef1234\tHEAD\n", stderr="")

        subprocess.run = fake_run  # type: ignore[assignment]
        try:
            b = BashExecutor()
            b._sandbox_provider = FakeBwrapProvider()  # type: ignore[attr-defined]
            r = b.run("timeout 15 git ls-remote https://github.com/x/y.git HEAD")
            self.assertEqual(r.exit_code, 0)
            self.assertIn("HEAD", r.stdout)
            # 应有 2 次 subprocess 调用：bwrap 失败 + 降级成功
            self.assertGreaterEqual(len(calls), 2)
        finally:
            subprocess.run = orig_run  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()

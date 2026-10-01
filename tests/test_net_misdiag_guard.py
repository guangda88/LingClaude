#!/usr/bin/env python3
"""net_misdiag_guard 哨兵测试：探网命令强制提醒，防"无网"误诊复发。

背景：2026-10-01 双次误诊（DNS 硬墙 → 基础设施封锁 → 实为沙箱隔离）。
本测试锚定：任何 bash 探网命令必须先命中哨兵提醒（ASK 档），
结论前必须跑 scripts/net_channels.py 通道矩阵。
"""
import importlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, "/home/ai/lingclaude")
hook = importlib.import_module("lingclaude.core.tool_auth_hook")
Tier = hook.Tier


class NetMisdiagGuardTest(unittest.TestCase):
    """哨兵四关键面：触发/放行/全路径/异常安全。"""

    def _guard(self, name, args):
        return hook._net_misdiag_guard(name, args)

    # ── 触发面 ──────────────────────────────────────────────
    def test_curl_triggers(self):
        d = self._guard("bash", {"command": "curl https://github.com"})
        self.assertIsNotNone(d)
        self.assertEqual(d.tier, Tier.ASK)
        self.assertEqual(d.policy_id, "net_misdiag_guard")

    def test_wget_ping_dig_trigger(self):
        for cmd in ("wget -qO- http://x", "ping -c1 8.8.8.8",
                    "dig github.com"):
            d = self._guard("bash", {"command": cmd})
            self.assertIsNotNone(d, cmd)
            self.assertEqual(d.tier, Tier.ASK)

    def test_full_path_curl_triggers(self):
        d = self._guard("bash", {"command": "/usr/bin/curl -s https://x"})
        self.assertIsNotNone(d)

    def test_nslookup_host_trigger(self):
        for cmd in ("nslookup github.com", "host github.com"):
            d = self._guard("bash", {"command": cmd})
            self.assertIsNotNone(d, cmd)

    # ── 放行面 ──────────────────────────────────────────────
    def test_non_probe_passes(self):
        for cmd in ("ls -la", "grep -rn pattern .", "git status",
                    "python3 scripts/net_channels.py", "pytest -q"):
            self.assertIsNone(self._guard("bash", {"command": cmd}), cmd)

    def test_channel_probe_script_not_triggered(self):
        """防自指：探针脚本自身不应再次命中哨兵（否则递归提醒）。"""
        d = self._guard("bash",
                        {"command": "python3 scripts/net_channels.py"})
        self.assertIsNone(d)

    def test_non_bash_tool_passes(self):
        self.assertIsNone(self._guard("read", {"command": "curl x"}))
        self.assertIsNone(self._guard("web_fetch", {"url": "https://x"}))

    def test_empty_or_missing_command(self):
        self.assertIsNone(self._guard("bash", {}))
        self.assertIsNone(self._guard("bash", {"command": ""}))
        self.assertIsNone(self._guard("bash", None))

    # ── 提醒内容面（结论纪律写进文案）──────────────────────
    def test_reason_contains_channel_discipline(self):
        d = self._guard("bash", {"command": "curl https://x"})
        self.assertIn("net_channels.py", d.reason)
        self.assertIn("单通道", d.reason)

    def test_audit_written(self):
        d = self._guard("bash", {"command": "curl https://x"})
        self.assertTrue(d.audit_written)

    # ── 异常安全面（守卫失败不反噬）────────────────────────
    def test_exception_swallowed_returns_none(self):
        with patch.object(hook, "_get_policy",
                          side_effect=RuntimeError("boom")):
            d = self._guard("bash", {"command": "curl https://x"})
            self.assertIsNone(d)  # 异常被吞，不阻塞工具执行


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""
测试灵字辈推送前交叉审计流水线

覆盖:
  1. ling_push.py 子命令: status, pending, audit, respond, push
  2. pre-push 钩子: 仓库检测, 测试运行, 审计请求, 轮询, 门控
  3. 端到端: 发送审计 → 灵依回复 → 验证 pass/fail
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/home/ai/.ling_lib")
sys.path.insert(0, "/home/ai/.git-hooks")

import ling_push
import pre_push

LINGMESSAGE_DIR = Path.home() / ".lingmessage"


class TestLingPushScan(unittest.TestCase):
    """Test repo scanning."""

    def test_scan_finds_repos(self) -> None:
        repos = ling_push.scan_repos()
        names = [r.name for r in repos]
        self.assertIn("LingClaude", names)
        self.assertIn("LingMessage", names)
        self.assertIn("LingYi", names)

    def test_scan_repo_has_fields(self) -> None:
        repos = ling_push.scan_repos()
        lc = [r for r in repos if r.name == "LingClaude"][0]
        self.assertEqual(lc.path, "/home/ai/LingClaude")
        self.assertEqual(lc.branch, "master")
        self.assertEqual(lc.remote, "origin")
        self.assertIsInstance(lc.ahead, int)


class TestLingPushSendAudit(unittest.TestCase):
    """Test audit request sending."""

    def setUp(self) -> None:
        self._orig_dir = LINGMESSAGE_DIR / "threads"
        self._cleanup_threads: list[str] = []

    def tearDown(self) -> None:
        for tid in self._cleanup_threads:
            tdir = self._orig_dir / tid
            if tdir.exists():
                for f in tdir.iterdir():
                    f.unlink()
                tdir.rmdir()

    def test_send_audit_creates_thread(self) -> None:
        repos = ling_push.scan_repos()
        thread_id = ling_push.send_audit_request(repos)
        self._cleanup_threads.append(thread_id)

        tdir = self._orig_dir / thread_id
        self.assertTrue(tdir.exists())
        self.assertTrue((tdir / "thread.json").exists())

    def test_send_audit_thread_meta(self) -> None:
        repos = ling_push.scan_repos()
        thread_id = ling_push.send_audit_request(repos)
        self._cleanup_threads.append(thread_id)

        meta = json.loads((self._orig_dir / thread_id / "thread.json").read_text())
        self.assertEqual(meta["topic"], "cross-audit")
        self.assertEqual(meta["status"], "open")
        self.assertIn("lingclaude", meta["participants"])
        self.assertIn("lingyi", meta["participants"])

    def test_send_audit_has_message(self) -> None:
        repos = ling_push.scan_repos()
        thread_id = ling_push.send_audit_request(repos)
        self._cleanup_threads.append(thread_id)

        tdir = self._orig_dir / thread_id
        msgs = list(tdir.glob("msg_*.json"))
        self.assertGreaterEqual(len(msgs), 1)
        msg = json.loads(msgs[0].read_text())
        self.assertEqual(msg["sender"], "lingclaude")
        self.assertIn("lingyi", msg["recipients"])
        self.assertIn("交叉审计", msg["body"])

    def test_send_audit_diff_file(self) -> None:
        repos = ling_push.scan_repos()
        repos_with_ahead = [r for r in repos if r.ahead > 0]
        if not repos_with_ahead:
            self.skipTest("No repos with ahead commits")
        thread_id = ling_push.send_audit_request(repos)
        self._cleanup_threads.append(thread_id)

        diff_file = self._orig_dir / thread_id / "pending_diff.json"
        self.assertTrue(diff_file.exists())
        diff_data = json.loads(diff_file.read_text())
        self.assertIsInstance(diff_data, dict)


class TestLingPushRespond(unittest.TestCase):
    """Test LingYi respond command."""

    def setUp(self) -> None:
        self._threads_dir = LINGMESSAGE_DIR / "threads"
        self._cleanup_threads: list[str] = []

    def tearDown(self) -> None:
        for tid in self._cleanup_threads:
            tdir = self._threads_dir / tid
            if tdir.exists():
                for f in tdir.iterdir():
                    f.unlink()
                tdir.rmdir()

    def _create_thread(self) -> str:
        repos = ling_push.scan_repos()
        thread_id = ling_push.send_audit_request(repos)
        self._cleanup_threads.append(thread_id)
        return thread_id

    def test_respond_pass(self) -> None:
        thread_id = self._create_thread()
        ling_push.cmd_respond(thread_id, "PASS", "代码审查通过")

        tdir = self._threads_dir / thread_id
        replies = []
        for f in sorted(tdir.glob("msg_*.json")):
            d = json.loads(f.read_text())
            if d["sender"] == "lingyi":
                replies.append(d)
        self.assertEqual(len(replies), 1)
        self.assertIn("AUDIT_PASS", replies[0]["body"])

    def test_respond_fail(self) -> None:
        thread_id = self._create_thread()
        ling_push.cmd_respond(thread_id, "FAIL", "发现潜在 bug")

        tdir = self._threads_dir / thread_id
        replies = []
        for f in sorted(tdir.glob("msg_*.json")):
            d = json.loads(f.read_text())
            if d["sender"] == "lingyi":
                replies.append(d)
        self.assertEqual(len(replies), 1)
        self.assertIn("AUDIT_FAIL", replies[0]["body"])
        self.assertIn("潜在 bug", replies[0]["body"])

    def test_respond_closes_thread(self) -> None:
        thread_id = self._create_thread()
        ling_push.cmd_respond(thread_id, "PASS", "")

        meta = json.loads((self._threads_dir / thread_id / "thread.json").read_text())
        self.assertEqual(meta["status"], "closed")

    def test_respond_invalid_verdict(self) -> None:
        thread_id = self._create_thread()
        with self.assertRaises(SystemExit):
            ling_push.cmd_respond(thread_id, "MAYBE", "")


class TestLingPushPoll(unittest.TestCase):
    """Test poll for audit result."""

    def setUp(self) -> None:
        self._threads_dir = LINGMESSAGE_DIR / "threads"
        self._cleanup_threads: list[str] = []

    def tearDown(self) -> None:
        for tid in self._cleanup_threads:
            tdir = self._threads_dir / tid
            if tdir.exists():
                for f in tdir.iterdir():
                    f.unlink()
                tdir.rmdir()

    def _create_thread(self) -> str:
        repos = ling_push.scan_repos()
        thread_id = ling_push.send_audit_request(repos)
        self._cleanup_threads.append(thread_id)
        return thread_id

    def test_poll_timeout(self) -> None:
        thread_id = self._create_thread()
        result = ling_push.poll_audit_result(thread_id, timeout=1)
        self.assertEqual(result, ling_push.AuditStatus.TIMEOUT)

    def test_poll_pass(self) -> None:
        thread_id = self._create_thread()
        ling_push.cmd_respond(thread_id, "PASS", "")
        result = ling_push.poll_audit_result(thread_id, timeout=5)
        self.assertEqual(result, ling_push.AuditStatus.APPROVED)

    def test_poll_fail(self) -> None:
        thread_id = self._create_thread()
        ling_push.cmd_respond(thread_id, "FAIL", "bug")
        result = ling_push.poll_audit_result(thread_id, timeout=5)
        self.assertEqual(result, ling_push.AuditStatus.REJECTED)


class TestLingPushPending(unittest.TestCase):
    """Test pending command."""

    def setUp(self) -> None:
        self._threads_dir = LINGMESSAGE_DIR / "threads"
        self._cleanup_threads: list[str] = []

    def tearDown(self) -> None:
        for tid in self._cleanup_threads:
            tdir = self._threads_dir / tid
            if tdir.exists():
                for f in tdir.iterdir():
                    f.unlink()
                tdir.rmdir()

    def test_pending_empty(self) -> None:
        pending = ling_push._find_pending_threads()
        closed = [t for t in pending if t["thread_id"] in self._cleanup_threads]
        for c in closed:
            self._cleanup_threads.remove(c["thread_id"])
        self.assertEqual(len(closed), 0)

    def test_pending_finds_open_thread(self) -> None:
        repos = ling_push.scan_repos()
        thread_id = ling_push.send_audit_request(repos)
        self._cleanup_threads.append(thread_id)

        pending = ling_push._find_pending_threads()
        pending_ids = [t["thread_id"] for t in pending]
        self.assertIn(thread_id, pending_ids)

    def test_pending_excludes_responded(self) -> None:
        repos = ling_push.scan_repos()
        thread_id = ling_push.send_audit_request(repos)
        self._cleanup_threads.append(thread_id)
        ling_push.cmd_respond(thread_id, "PASS", "")

        pending = ling_push._find_pending_threads()
        pending_ids = [t["thread_id"] for t in pending]
        self.assertNotIn(thread_id, pending_ids)


class TestPrePushDetectRepo(unittest.TestCase):
    """Test pre-push repo detection."""

    def test_detect_lingclaude(self) -> None:
        with patch("os.getcwd", return_value="/home/ai/LingClaude"):
            name = pre_push.detect_repo("origin", "refs/heads/master")
            self.assertEqual(name, "LingClaude")

    def test_detect_lingmessage(self) -> None:
        with patch("os.getcwd", return_value="/home/ai/LingMessage"):
            name = pre_push.detect_repo("origin", "refs/heads/master")
            self.assertEqual(name, "LingMessage")

    def test_detect_lingyi(self) -> None:
        with patch("os.getcwd", return_value="/home/ai/LingYi"):
            name = pre_push.detect_repo("origin", "refs/heads/master")
            self.assertEqual(name, "LingYi")

    def test_detect_non_ling_repo(self) -> None:
        with patch("os.getcwd", return_value="/home/ai/some-other-project"):
            name = pre_push.detect_repo("origin", "refs/heads/master")
            self.assertIsNone(name)

    def test_detect_subdir(self) -> None:
        with patch("os.getcwd", return_value="/home/ai/LingClaude/scripts"):
            name = pre_push.detect_repo("origin", "refs/heads/master")
            self.assertEqual(name, "LingClaude")


class TestPrePushRunTests(unittest.TestCase):
    """Test pre-push test runner."""

    def test_run_tests_success(self) -> None:
        with patch("pre_push._run") as mock_run:
            mock_run.return_value = (True, "5 passed in 1.0s")
            ok = pre_push.run_tests(repo_path="/home/ai/LingClaude")
            self.assertTrue(ok)
            mock_run.assert_called_once()

    def test_run_tests_failure(self) -> None:
        with patch("pre_push._run") as mock_run:
            mock_run.return_value = (False, "1 failed")
            ok = pre_push.run_tests(repo_path="/home/ai/LingClaude")
            self.assertFalse(ok)

    def test_run_tests_bad_dir(self) -> None:
        with patch("pre_push._run") as mock_run:
            mock_run.return_value = (False, "No such file or directory")
            ok = pre_push.run_tests(repo_path="/tmp/nonexistent_xyz")
            self.assertFalse(ok)


class TestPrePushDiffSummary(unittest.TestCase):
    """Test diff summary extraction."""

    def test_get_diff_summary(self) -> None:
        ahead, stat = pre_push.get_diff_summary(repo_path="/home/ai/LingClaude")
        self.assertIsInstance(ahead, int)
        self.assertIsInstance(stat, str)


class TestEndToEndAuditFlow(unittest.TestCase):
    """Full flow: send audit → respond → verify."""

    def setUp(self) -> None:
        self._threads_dir = LINGMESSAGE_DIR / "threads"
        self._cleanup_threads: list[str] = []

    def tearDown(self) -> None:
        for tid in self._cleanup_threads:
            tdir = self._threads_dir / tid
            if tdir.exists():
                for f in tdir.iterdir():
                    f.unlink()
                tdir.rmdir()

    def test_full_flow_pass(self) -> None:
        repos = ling_push.scan_repos()
        thread_id = ling_push.send_audit_request(repos)
        self._cleanup_threads.append(thread_id)

        pending = ling_push._find_pending_threads()
        self.assertIn(thread_id, [t["thread_id"] for t in pending])

        ling_push.cmd_respond(thread_id, "PASS", "审查通过，无问题")

        result = ling_push.poll_audit_result(thread_id, timeout=5)
        self.assertEqual(result, ling_push.AuditStatus.APPROVED)

        meta = json.loads((self._threads_dir / thread_id / "thread.json").read_text())
        self.assertEqual(meta["status"], "closed")

        pending_after = ling_push._find_pending_threads()
        self.assertNotIn(thread_id, [t["thread_id"] for t in pending_after])

    def test_full_flow_fail(self) -> None:
        repos = ling_push.scan_repos()
        thread_id = ling_push.send_audit_request(repos)
        self._cleanup_threads.append(thread_id)

        ling_push.cmd_respond(thread_id, "FAIL", "发现硬编码密钥")

        result = ling_push.poll_audit_result(thread_id, timeout=5)
        self.assertEqual(result, ling_push.AuditStatus.REJECTED)

    def test_full_flow_timeout(self) -> None:
        repos = ling_push.scan_repos()
        thread_id = ling_push.send_audit_request(repos)
        self._cleanup_threads.append(thread_id)

        result = ling_push.poll_audit_result(thread_id, timeout=1)
        self.assertEqual(result, ling_push.AuditStatus.TIMEOUT)

    def test_pre_push_skip_audit_env(self) -> None:
        with patch.dict(os.environ, {"LING_SKIP_AUDIT": "1"}):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.read.return_value = ""
                ret = pre_push.main()
                self.assertEqual(ret, 0)

    def test_pre_push_non_ling_repo(self) -> None:
        with patch("os.getcwd", return_value="/tmp/some-project"):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.read.return_value = ""
                ret = pre_push.main()
                self.assertEqual(ret, 0)


class TestPrePushSendAudit(unittest.TestCase):
    """Test pre-push audit request sending."""

    def setUp(self) -> None:
        self._threads_dir = LINGMESSAGE_DIR / "threads"
        self._cleanup_threads: list[str] = []

    def tearDown(self) -> None:
        for tid in self._cleanup_threads:
            tdir = self._threads_dir / tid
            if tdir.exists():
                for f in tdir.iterdir():
                    f.unlink()
                tdir.rmdir()

    def test_send_audit_request_creates_files(self) -> None:
        thread_id = pre_push.send_audit_request(repo_name="LingClaude", ahead=3, diff_stat="5 files changed")
        self._cleanup_threads.append(thread_id)

        tdir = self._threads_dir / thread_id
        self.assertTrue((tdir / "thread.json").exists())

        msgs = list(tdir.glob("msg_*.json"))
        self.assertGreaterEqual(len(msgs), 1)
        msg = json.loads(msgs[0].read_text())
        self.assertEqual(msg["sender"], "lingclaude")
        self.assertIn("LingClaude", msg["subject"])
        self.assertIn("5 files changed", msg["body"])


class TestExtractImports(unittest.TestCase):
    """Test _extract_imports AST helper."""

    def test_regular_imports(self) -> None:
        source = "import os\nimport sys\nimport json"
        names = pre_push._extract_imports(source)
        self.assertEqual(names, {"os", "sys", "json"})

    def test_from_imports(self) -> None:
        source = "from pathlib import Path\nfrom lingclaude.core import QueryEngine"
        names = pre_push._extract_imports(source)
        self.assertIn("pathlib", names)
        self.assertIn("lingclaude", names)

    def test_dotted_imports_get_first_component(self) -> None:
        source = "import lingclaude.core.types\nfrom lingmessage.mailbox import Mailbox"
        names = pre_push._extract_imports(source)
        self.assertIn("lingclaude", names)
        self.assertIn("lingmessage", names)
        self.assertNotIn("lingclaude.core.types", names)

    def test_syntax_error_returns_empty(self) -> None:
        names = pre_push._extract_imports("def broken(:")
        self.assertEqual(names, set())

    def test_empty_source(self) -> None:
        names = pre_push._extract_imports("")
        self.assertEqual(names, set())


class TestCountComplexity(unittest.TestCase):
    """Test _count_complexity AST helper."""

    def test_simple_code(self) -> None:
        source = "x = 1\ny = 2\nprint(x + y)"
        self.assertEqual(pre_push._count_complexity(source), 0)

    def test_if_for_while_except(self) -> None:
        source = textwrap.dedent("""\
            try:
                for i in range(10):
                    if i > 5:
                        pass
            except ValueError:
                pass
            while True:
                break
        """)
        cx = pre_push._count_complexity(source)
        # If + For + While + ExceptHandler = 4
        self.assertGreaterEqual(cx, 4)

    def test_boolops_count(self) -> None:
        source = "if a and b or c:\n    pass\n"
        cx = pre_push._count_complexity(source)
        self.assertEqual(cx, 3)

    def test_syntax_error_returns_zero(self) -> None:
        self.assertEqual(pre_push._count_complexity("def bad(:"), 0)

    def test_high_complexity_file(self) -> None:
        branches = "\n".join(f"    if x == {i}: pass" for i in range(35))
        source = f"def f():\n    x = 1\n{branches}\n"
        cx = pre_push._count_complexity(source)
        self.assertGreater(cx, 30)


class TestAuditL1(unittest.TestCase):
    """Test L1 single-file AST audit."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="test_L1_")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_no_changed_files(self) -> None:
        with patch("pre_push.get_changed_py_files", return_value=[]):
            findings = pre_push.audit_L1(self._tmpdir)
        self.assertEqual(findings, [])

    def test_secret_detection(self) -> None:
        secret_file = Path(self._tmpdir) / "config.py"
        secret_file.write_text('api_key = "sk-abcdefghijklmnopqrstuvwxyz123456"\n')
        with patch("pre_push.get_changed_py_files", return_value=["config.py"]):
            findings = pre_push.audit_L1(self._tmpdir)
        secret_findings = [f for f in findings if "[L1:SECRET]" in f]
        self.assertGreaterEqual(len(secret_findings), 1)
        self.assertIn("config.py", secret_findings[0])

    def test_github_token_detection(self) -> None:
        py_file = Path(self._tmpdir) / "auth.py"
        py_file.write_text('token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"\n')
        with patch("pre_push.get_changed_py_files", return_value=["auth.py"]):
            findings = pre_push.audit_L1(self._tmpdir)
        secret_findings = [f for f in findings if "[L1:SECRET]" in f]
        self.assertGreaterEqual(len(secret_findings), 1)

    def test_high_complexity_warning(self) -> None:
        branches = "\n".join(f"    if x == {i}: pass" for i in range(50))
        source = f'def complex_fn():\n    x = 1\n{branches}\n'
        py_file = Path(self._tmpdir) / "complex.py"
        py_file.write_text(source)
        with patch("pre_push.get_changed_py_files", return_value=["complex.py"]):
            findings = pre_push.audit_L1(self._tmpdir)
        cx_findings = [f for f in findings if "[L1:COMPLEXITY]" in f]
        self.assertEqual(len(cx_findings), 1)
        self.assertIn("complex.py", cx_findings[0])

    def test_clean_file_no_findings(self) -> None:
        py_file = Path(self._tmpdir) / "clean.py"
        py_file.write_text('def hello():\n    print("world")\n')
        with patch("pre_push.get_changed_py_files", return_value=["clean.py"]):
            findings = pre_push.audit_L1(self._tmpdir)
        self.assertEqual(findings, [])

    def test_test_files_skip_secret_check(self) -> None:
        py_file = Path(self._tmpdir) / "tests" / "test_api.py"
        py_file.parent.mkdir(parents=True, exist_ok=True)
        py_file.write_text('api_key = "sk-abcdefghijklmnopqrstuvwxyz123456"\n')
        with patch("pre_push.get_changed_py_files", return_value=["tests/test_api.py"]):
            findings = pre_push.audit_L1(self._tmpdir)
        secret_findings = [f for f in findings if "[L1:SECRET]" in f]
        self.assertEqual(len(secret_findings), 0)

    def test_missing_file_skipped(self) -> None:
        with patch("pre_push.get_changed_py_files", return_value=["nonexistent.py"]):
            findings = pre_push.audit_L1(self._tmpdir)
        self.assertEqual(findings, [])


class TestAuditL2(unittest.TestCase):
    """Test L2 module cross-audit."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="test_L2_")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_no_changed_files(self) -> None:
        with patch("pre_push.get_changed_py_files", return_value=[]):
            findings = pre_push.audit_L2("LingClaude", self._tmpdir)
        self.assertEqual(findings, [])

    def test_missing_dep_repo(self) -> None:
        py_file = Path(self._tmpdir) / "bridge.py"
        py_file.write_text("from lingyang import something\n")
        with patch("pre_push.get_changed_py_files", return_value=["bridge.py"]):
            with patch("pre_push.LING_REPOS", {**pre_push.LING_REPOS, "LingYang": "/tmp/nonexistent_repo_xyz"}):
                findings = pre_push.audit_L2("LingClaude", self._tmpdir)
        dep_missing = [f for f in findings if "[L2:DEP_MISSING]" in f]
        self.assertGreaterEqual(len(dep_missing), 1)

    def test_dirty_dep_warning(self) -> None:
        py_file = Path(self._tmpdir) / "code.py"
        py_file.write_text("from lingmessage import Mailbox\n")
        with patch("pre_push.get_changed_py_files", return_value=["code.py"]):
            with patch("pre_push._run") as mock_run:
                mock_run.side_effect = [
                    (True, "lingmessage"),  # import check for dep_repo
                    (True, "abc1234"),       # git rev-parse HEAD
                    (True, "M src/foo.py"),  # git status --porcelain (dirty)
                    (True, ""),              # git diff for reverse dep
                ]
                findings = pre_push.audit_L2("LingClaude", self._tmpdir)
        dirty_findings = [f for f in findings if "[L2:DEP_DIRTY]" in f]
        self.assertGreaterEqual(len(dirty_findings), 1)

    def test_clean_deps_no_findings(self) -> None:
        py_file = Path(self._tmpdir) / "code.py"
        py_file.write_text("from lingmessage import Mailbox\n")
        with patch("pre_push.get_changed_py_files", return_value=["code.py"]):
            with patch("pre_push._run") as mock_run:
                mock_run.side_effect = [
                    (True, "lingmessage"),  # import check
                    (True, "abc1234"),       # git rev-parse HEAD
                    (True, ""),              # git status (clean)
                    (True, ""),              # git diff for reverse dep
                ]
                findings = pre_push.audit_L2("LingClaude", self._tmpdir)
        dirty_findings = [f for f in findings if "[L2:DEP_DIRTY]" in f]
        self.assertEqual(len(dirty_findings), 0)

    def test_no_cross_repo_imports(self) -> None:
        py_file = Path(self._tmpdir) / "local.py"
        py_file.write_text("import os\nimport json\n")
        with patch("pre_push.get_changed_py_files", return_value=["local.py"]):
            findings = pre_push.audit_L2("LingClaude", self._tmpdir)
        dep_findings = [f for f in findings if "[L2:DEP_" in f]
        self.assertEqual(len(dep_findings), 0)


class TestMainL1L2Integration(unittest.TestCase):
    """Test main() with L1/L2 critical findings blocking push."""

    def test_critical_secret_blocks_push(self) -> None:
        tmpdir = tempfile.mkdtemp(prefix="test_main_")
        try:
            py_file = Path(tmpdir) / "leak.py"
            py_file.write_text('api_key = "sk-abcdefghijklmnopqrstuvwxyz12345678"\n')

            def mock_get_changed(repo_path: str) -> list[str]:
                return ["leak.py"]

            with patch("os.getcwd", return_value=tmpdir), \
                 patch("sys.stdin") as mock_stdin, \
                 patch("pre_push.LING_REPOS", {"TestRepo": tmpdir}), \
                 patch("pre_push.run_tests", return_value=True), \
                 patch("pre_push.get_diff_summary", return_value=(1, "1 file")), \
                 patch("pre_push.get_changed_py_files", side_effect=mock_get_changed), \
                 patch("pre_push.audit_L2", return_value=[]), \
                 patch("pre_push.send_audit_request", return_value="fakethread123"), \
                 patch("pre_push.poll_audit_reply", return_value=("PASS", "")):
                mock_stdin.read.return_value = "origin url refs/heads/master abc123 def456"
                ret = pre_push.main()
            self.assertEqual(ret, 1)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_warnings_allow_push(self) -> None:
        tmpdir = tempfile.mkdtemp(prefix="test_main_")
        try:
            py_file = Path(tmpdir) / "meh.py"
            py_file.write_text("x = 1\n")

            with patch("os.getcwd", return_value=tmpdir), \
                 patch("sys.stdin") as mock_stdin, \
                 patch("pre_push.LING_REPOS", {"TestRepo": tmpdir}), \
                 patch("pre_push.run_tests", return_value=True), \
                 patch("pre_push.get_diff_summary", return_value=(1, "1 file")), \
                 patch("pre_push.get_changed_py_files", return_value=["meh.py"]), \
                 patch("pre_push.audit_L1", return_value=["[L1:COMPLEXITY] meh.py — warning"]), \
                 patch("pre_push.audit_L2", return_value=["[L2:DEP_DIRTY] warning"]), \
                 patch("pre_push.send_audit_request", return_value="fakethread123"), \
                 patch("pre_push.poll_audit_reply", return_value=("PASS", "")):
                mock_stdin.read.return_value = "origin url refs/heads/master abc123 def456"
                ret = pre_push.main()
            self.assertEqual(ret, 0)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestLingPushL1L2(unittest.TestCase):
    """Test L1/L2 audit integration in ling_push.py."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="test_lp_L1_")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_ling_push_audit_L1_secret(self) -> None:
        py_file = Path(self._tmpdir) / "leak.py"
        py_file.write_text('api_key = "sk-abcdefghijklmnopqrstuvwxyz12345678"\n')
        with patch("ling_push._get_changed_py_files", return_value=["leak.py"]):
            findings = ling_push.audit_L1(self._tmpdir)
        self.assertTrue(any("[L1:SECRET]" in f for f in findings))

    def test_ling_push_audit_L1_clean(self) -> None:
        py_file = Path(self._tmpdir) / "clean.py"
        py_file.write_text('def hello():\n    print("world")\n')
        with patch("ling_push._get_changed_py_files", return_value=["clean.py"]):
            findings = ling_push.audit_L1(self._tmpdir)
        self.assertEqual(findings, [])

    def test_ling_push_audit_L2_no_cross_imports(self) -> None:
        py_file = Path(self._tmpdir) / "local.py"
        py_file.write_text("import os\nimport json\n")
        with patch("ling_push._get_changed_py_files", return_value=["local.py"]):
            findings = ling_push.audit_L2("LingClaude", self._tmpdir)
        dep_findings = [f for f in findings if "[L2:DEP_" in f and "DIRTY" not in f]
        self.assertEqual(len(dep_findings), 0)

    def test_ling_push_run_L1L2_audit_no_critical(self) -> None:
        repos = [ling_push.RepoAudit(
            name="LingClaude", path=self._tmpdir, branch="master", remote="origin", ahead=1,
        )]
        with patch("ling_push.audit_L1", return_value=["[L1:COMPLEXITY] warning"]), \
             patch("ling_push.audit_L2", return_value=[]):
            critical = ling_push.run_L1L2_audit(repos)
        self.assertEqual(len(critical), 0)

    def test_ling_push_run_L1L2_audit_critical(self) -> None:
        repos = [ling_push.RepoAudit(
            name="LingClaude", path=self._tmpdir, branch="master", remote="origin", ahead=1,
        )]
        with patch("ling_push.audit_L1", return_value=["[L1:SECRET] leak.py"]), \
             patch("ling_push.audit_L2", return_value=[]):
            critical = ling_push.run_L1L2_audit(repos)
        self.assertEqual(len(critical), 1)

    def test_ling_push_run_L1L2_skips_zero_ahead(self) -> None:
        repos = [ling_push.RepoAudit(
            name="LingClaude", path=self._tmpdir, branch="master", remote="origin", ahead=0,
        )]
        with patch("ling_push.audit_L1", return_value=["should not appear"]) as mock_l1:
            critical = ling_push.run_L1L2_audit(repos)
        mock_l1.assert_not_called()
        self.assertEqual(len(critical), 0)


class TestLingPushCLI(unittest.TestCase):
    """Test CLI entry points."""

    def test_no_args_shows_help(self) -> None:
        r = subprocess.run(
            [sys.executable, "/home/ai/.ling_lib/ling_push.py"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("ling_push.py", r.stdout)

    def test_status_runs(self) -> None:
        r = subprocess.run(
            [sys.executable, "/home/ai/.ling_lib/ling_push.py", "status"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("LingClaude", r.stdout)

    def test_pending_runs(self) -> None:
        r = subprocess.run(
            [sys.executable, "/home/ai/.ling_lib/ling_push.py", "pending"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0)

    def test_audit_nonexistent_dir(self) -> None:
        r = subprocess.run(
            [sys.executable, "/home/ai/.ling_lib/ling_push.py", "audit", "/tmp/no_such_dir"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()

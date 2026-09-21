"""验证台账（verify_ledger）单元测试——层1/2/3 三面全覆盖。

对齐铁律附录格式：每条测试注明锚定的故障模式。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.core.verify_ledger import (
    ANCHOR_TAIL_DEFAULT,
    KIND_CLAIM_VERIFIED,
    KIND_COMMIT_CREATED,
    KIND_FILE_CREATED,
    KIND_TEST_PASSED,
    MAX_ENTRIES_PER_TRACE,
    VerifyLedger,
    _safe_trace_id,
    evidence_digest,
    get_verify_ledger,
)


@pytest.fixture()
def ledger(tmp_path: Path) -> VerifyLedger:
    return VerifyLedger(root=tmp_path / "verify_log")


# ---------- 层1：记录 ----------

class TestRecord:
    def test_record_creates_jsonl_with_all_fields(self, ledger: VerifyLedger):
        """锚定发作A：真幻觉的反面——登记即有锚点，字段完整可复核。"""
        e = ledger.record(
            kind=KIND_TEST_PASSED,
            claim="tests/test_x.py 7/7 全绿",
            evidence="===== 7 passed in 10.09s =====",
            trace_id="sess-abc123",
            via="bash",
        )
        assert e.digest == evidence_digest("===== 7 passed in 10.09s =====")
        path = ledger.root / "sess-abc123.jsonl"
        assert path.exists()
        d = json.loads(path.read_text().strip())
        assert d["kind"] == "test_passed"
        assert d["digest"] == e.digest
        assert d["trace_id"] == "sess-abc123"
        assert d["via"] == "bash"

    def test_record_is_append_only(self, ledger: VerifyLedger):
        """锚定「只追加不删除」不变式：两条记录共存，先入者不被改写。"""
        ledger.record(KIND_FILE_CREATED, "a.py 落盘", "file_create ok", "s1")
        ledger.record(KIND_COMMIT_CREATED, "abc1234 入库", "git commit ok", "s1")
        entries = ledger.query("s1")
        assert [e.claim for e in entries] == ["a.py 落盘", "abc1234 入库"]
        assert entries[0].ts <= entries[1].ts

    def test_invalid_kind_raises(self, ledger: VerifyLedger):
        with pytest.raises(ValueError):
            ledger.record(kind="bogus_kind", claim="x", evidence="y", trace_id="s1")

    def test_claim_evidence_truncation(self, ledger: VerifyLedger):
        """claim/evidence 截断防膨胀：500/2000 上限，digest 按原文计算。"""
        e = ledger.record(KIND_CLAIM_VERIFIED, "c" * 900, "v" * 5000, "s1")
        assert len(e.claim) == 500
        assert e.digest == evidence_digest("v" * 5000)

    def test_fail_open_on_io_error(self, tmp_path):
        """锚定 fail-open 纪律：磁盘只读时 record 不抛异常。"""
        ro = tmp_path / "verify_log"
        ro.mkdir()
        ro.chmod(0o500)  # 只读目录
        lg = VerifyLedger(root=ro)
        try:
            e = lg.record(KIND_TEST_PASSED, "claim", "evidence", "s1")
            assert e.digest  # 返回条目（内存态成立），不抛
        finally:
            ro.chmod(0o700)

    def test_max_entries_cap(self, ledger: VerifyLedger):
        """超限从头部截断：行数永不超过上限，且保留最新。"""
        total = MAX_ENTRIES_PER_TRACE + 50
        for i in range(total):
            ledger.record(KIND_CLAIM_VERIFIED, f"c{i}", f"ev{i}", "s1")
        text = (ledger.root / "s1.jsonl").read_text()
        n = len(text.splitlines())
        assert n <= MAX_ENTRIES_PER_TRACE
        # 最早的被截掉，最新保留（用 JSON 字段级锚点，防 digest 十六进制误命中）
        assert '"claim": "c0"' not in text
        assert f'"claim": "c{total - 1}"' in text

    def test_trace_id_sanitized(self, ledger: VerifyLedger):
        """路径逃逸防护：会话 ID 特殊字符被清洗，文件不逃出 root。"""
        cleaned = _safe_trace_id("../../evil")
        assert "/" not in cleaned and "\\" not in cleaned
        ledger.record(KIND_CLAIM_VERIFIED, "c", "e", "../../evil")
        # 文件落在 root 内（清洗后的单个文件名，无路径分隔符）
        files = list(ledger.root.glob("*.jsonl"))
        assert len(files) == 1
        assert files[0].parent == ledger.root


# ---------- 层2：查询与标注推导 ----------

class TestQuery:
    def test_query_missing_session_returns_empty(self, ledger: VerifyLedger):
        assert ledger.query("nope") == []

    def test_query_kind_filter(self, ledger: VerifyLedger):
        ledger.record(KIND_TEST_PASSED, "t1", "e1", "s1")
        ledger.record(KIND_FILE_CREATED, "f1", "e2", "s1")
        ledger.record(KIND_TEST_PASSED, "t2", "e3", "s1")
        kinds = [e.claim for e in ledger.query("s1", kind=KIND_TEST_PASSED)]
        assert kinds == ["t1", "t2"]

    def test_query_claim_substring(self, ledger: VerifyLedger):
        ledger.record(KIND_CLAIM_VERIFIED, "commit abc1234 入库", "e1", "s1")
        ledger.record(KIND_CLAIM_VERIFIED, "commit def5678 入库", "e2", "s1")
        hit = ledger.verify_claim("s1", "abc1234")
        assert hit is not None and "abc1234" in hit.claim
        assert ledger.verify_claim("s1", "not-exist-xyz") is None

    def test_corrupt_lines_skipped(self, ledger: VerifyLedger):
        """坏行容错：JSONL 中间有垃圾行不影响其余条目检索。"""
        ledger.record(KIND_CLAIM_VERIFIED, "good1", "e1", "s1")
        path = ledger.root / "s1.jsonl"
        path.write_text(path.read_text() + "{corrupt json!!\n", encoding="utf-8")
        ledger.record(KIND_CLAIM_VERIFIED, "good2", "e2", "s1")
        claims = [e.claim for e in ledger.query("s1")]
        assert claims == ["good1", "good2"]

    def test_entry_roundtrip_immutable(self, ledger: VerifyLedger):
        """VerifyEntry frozen：登记后不可改写（错登只能新条目纠正）。"""
        e = ledger.record(KIND_CLAIM_VERIFIED, "c", "e", "s1")
        with pytest.raises(Exception):
            e.claim = "tampered"  # type: ignore[misc]


# ---------- 层3：恢复注入 ----------

class TestAnchorBlock:
    def test_anchor_block_empty_returns_none(self, ledger: VerifyLedger):
        assert ledger.anchor_block("empty-sess") is None

    def test_anchor_block_contains_digest_and_claim(self, ledger: VerifyLedger):
        ledger.record(KIND_TEST_PASSED, "test_x 7/7 全绿", "7 passed", "s1")
        block = ledger.anchor_block("s1")
        assert block is not None
        assert "test_x 7/7 全绿" in block
        assert "digest=" in block
        assert "[验证台账锚点]" in block

    def test_anchor_block_respects_tail(self, ledger: VerifyLedger):
        for i in range(30):
            ledger.record(KIND_CLAIM_VERIFIED, f"claim-{i}", f"ev-{i}", "s1")
        block = ledger.anchor_block("s1", tail=5)
        assert block is not None
        assert "claim-29" in block and "claim-25" in block
        assert "claim-20" not in block
        assert "最近 5 条" in block

    def test_anchor_block_default_tail_constant(self):
        assert ANCHOR_TAIL_DEFAULT == 20


# ---------- 便捷入口 ----------

class TestConvenience:
    def test_get_verify_ledger_singleton(self, tmp_path, monkeypatch):
        import lingclaude.core.verify_ledger as vl
        monkeypatch.setattr(vl, "_default", None)
        a = get_verify_ledger()
        b = get_verify_ledger()
        assert a is b
        injected = get_verify_ledger(root=tmp_path)
        assert injected is not a
        assert injected.root == tmp_path

    def test_record_verify_wrapper(self, tmp_path):
        import lingclaude.core.verify_ledger as vl
        monkey_ledger = VerifyLedger(root=tmp_path)
        monkeypatch_vl = vl
        monkeypatch_vl._default = monkey_ledger
        e = vl.record_verify(
            KIND_COMMIT_CREATED, "h1 入库", "git ok", trace_id="s9")
        assert e.digest.startswith("sha256:")
        assert (tmp_path / "s9.jsonl").exists()


# ---------- 集成演练：本次会话故障模式的端到端反演 ----------

class TestIncidentReplay:
    def test_incident_a_blocked_by_missing_anchor(self, ledger: VerifyLedger):
        """发作A反演：未登记的「落盘声明」查无锚点 → 标注必须降级为未验证。"""
        assert ledger.verify_claim("sess-real", "repair_card.py 147 行落盘") is None

    def test_incident_b_blocked_by_existing_anchor(self, ledger: VerifyLedger):
        """发作B反演：真实验证已入账 → 撤回前核验命中，拒绝无依据撤回。"""
        ledger.record(
            KIND_TEST_PASSED,
            "test_background_exit 7/7 全绿",
            "===== 7 passed in 10.09s =====",
            trace_id="c4b2cbfe",
        )
        hit = ledger.verify_claim("c4b2cbfe", "test_background_exit")
        assert hit is not None
        # digest 可独立复核（模型记忆之外的第二事实源）
        assert hit.digest == evidence_digest("===== 7 passed in 10.09s =====")

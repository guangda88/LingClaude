"""B项 (2026-10-22) 自优化器 path-scoped 写权限规则引擎测试。

验收口径（docs/research/20261021_lc_optimization_proposal_rsi_warming_permissions.md §三）:
- 根信任锚（evaluator 源码 + 规则文件自身）永拒——硬编码，规则文件 allow 也压不过；
- 有序规则 last-match-wins；
- 白名单外默认 ask（fail-closed 于静默放行，不硬拒）；
- 规则文件缺失/损坏 → ask（异常态交人判）；
- PolicyLoader mtime 热载——改规则文件即时生效，无需重启；
- 自指拒: 规则表内写 root path 的 allow 行无法解锁根锚对象。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lingclaude.core import policy_loader as pl
from lingclaude.core.permissions import (
    OptimizerWriteDecision,
    _normalize_repo_path,
    check_optimizer_write,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    pl.reset()
    yield
    pl.reset()


# ── 根信任锚 ───────────────────────────────────────────────────────────────


class TestRootAnchor:
    @pytest.mark.parametrize(
        "path",
        [
            "lingclaude/self_optimizer/evaluator.py",
            "lingclaude/self_optimizer/evaluator_meta.py",
            "lingclaude/core/policies/optimizer_write_policy.yaml",
        ],
    )
    def test_anchor_relative_deny(self, path: str):
        d = check_optimizer_write("self_optimizer", path)
        assert d == OptimizerWriteDecision("deny", "root_anchor")

    @pytest.mark.parametrize(
        "path",
        [
            "/home/ai/lingclaude/lingclaude/self_optimizer/evaluator.py",
            "/home/ai/lingclaude/lingclaude/core/policies/optimizer_write_policy.yaml",
        ],
    )
    def test_anchor_absolute_deny(self, path: str):
        d = check_optimizer_write("self_optimizer", path)
        assert d.decision == "deny"
        assert d.rule_id == "root_anchor"

    def test_anchor_cwd_independent_suffix(self, tmp_path: Path, monkeypatch):
        """cwd 不在 repo 根时，后缀兜底仍拒（过拒安全方向）。"""
        monkeypatch.chdir(tmp_path)
        d = check_optimizer_write("self_optimizer", "/opt/evil/self_optimizer/evaluator.py")
        assert d.decision == "deny"

    def test_anchor_not_bypassable_by_allow_rule(self, tmp_path: Path, monkeypatch):
        """自指陷阱: 规则表显式 allow 根锚对象也无法解锁（B.3-1）。"""
        monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
        (tmp_path / "optimizer_write_policy.yaml").write_text(
            yaml.safe_dump(
                {
                    "rules": [
                        {
                            "match": {
                                "actor": "self_optimizer",
                                "path": "lingclaude/self_optimizer/evaluator.py",
                            },
                            "action": "allow",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        d = check_optimizer_write("self_optimizer", "lingclaude/self_optimizer/evaluator.py")
        assert d.decision == "deny"
        assert d.rule_id == "root_anchor"


# ── 规则语义 ───────────────────────────────────────────────────────────────


class TestRuleSemantics:
    def _policy_dir(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
        return tmp_path

    def _write_policy(self, dirp: Path, rules: list[dict]) -> None:
        (dirp / "optimizer_write_policy.yaml").write_text(
            yaml.safe_dump({"rules": rules}), encoding="utf-8"
        )

    def test_real_policy_file_allows_both_funnels(self):
        """真实规则文件: 两漏斗被写对象 allow、规则外 ask。"""
        d1 = check_optimizer_write("self_optimizer", "config.yaml")
        assert d1.decision == "allow"
        d2 = check_optimizer_write("self_optimizer", "core/policies/behavior_policy.yaml")
        assert d2.decision == "allow"
        d3 = check_optimizer_write("self_optimizer", "core/policies/sandbox_policy.yaml")
        assert d3.decision == "ask"

    def test_last_match_wins(self, tmp_path: Path, monkeypatch):
        dirp = self._policy_dir(tmp_path, monkeypatch)
        self._write_policy(
            dirp,
            [
                {"match": {"actor": "self_optimizer", "path": "a/*"}, "action": "deny"},
                {"match": {"actor": "self_optimizer", "path": "a/b.yaml"}, "action": "allow"},
            ],
        )
        assert check_optimizer_write("self_optimizer", "a/b.yaml").decision == "allow"
        assert check_optimizer_write("self_optimizer", "a/c.yaml").decision == "deny"

    def test_actor_mismatch_skips_rule(self, tmp_path: Path, monkeypatch):
        dirp = self._policy_dir(tmp_path, monkeypatch)
        self._write_policy(
            dirp,
            [{"match": {"actor": "someone_else", "path": "*"}, "action": "allow"}],
        )
        assert check_optimizer_write("self_optimizer", "anything.yaml").decision == "ask"

    def test_actor_omitted_matches_any(self, tmp_path: Path, monkeypatch):
        dirp = self._policy_dir(tmp_path, monkeypatch)
        self._write_policy(
            dirp,
            [{"match": {"path": "shared/*"}, "action": "deny"}],
        )
        for actor in ("self_optimizer", "human", "anyone"):
            assert check_optimizer_write(actor, "shared/x.yaml").decision == "deny"

    def test_default_ask_when_no_match(self, tmp_path: Path, monkeypatch):
        dirp = self._policy_dir(tmp_path, monkeypatch)
        self._write_policy(
            dirp,
            [{"match": {"actor": "self_optimizer", "path": "known/*"}, "action": "allow"}],
        )
        d = check_optimizer_write("self_optimizer", "unknown/new_type.yaml")
        assert d == OptimizerWriteDecision("ask", "default_no_match")

    def test_malformed_yaml_ask(self, tmp_path: Path, monkeypatch):
        """规则文件损坏 → ask（不 deny 不 allow，异常态交人）。"""
        dirp = self._policy_dir(tmp_path, monkeypatch)
        (dirp / "optimizer_write_policy.yaml").write_text(
            "rules: {not: a: list:", encoding="utf-8"
        )
        d = check_optimizer_write("self_optimizer", "whatever.yaml")
        assert d == OptimizerWriteDecision("ask", "malformed_policy")

    def test_rules_not_list_ask(self, tmp_path: Path, monkeypatch):
        dirp = self._policy_dir(tmp_path, monkeypatch)
        (dirp / "optimizer_write_policy.yaml").write_text("rules: 42", encoding="utf-8")
        assert check_optimizer_write("self_optimizer", "x").rule_id == "malformed_policy"

    def test_hot_reload_via_mtime(self, tmp_path: Path, monkeypatch):
        """改规则文件 → 热载生效（mtime watch 语义，无需重启）。"""
        dirp = self._policy_dir(tmp_path, monkeypatch)
        self._write_policy(
            dirp,
            [{"match": {"actor": "self_optimizer", "path": "t/*"}, "action": "deny"}],
        )
        assert check_optimizer_write("self_optimizer", "t/x.yaml").decision == "deny"
        # mtime_ns 粒度 ~4ms，保险起见显式推进
        import os
        import time

        policy = dirp / "optimizer_write_policy.yaml"
        st = policy.stat()
        os.utime(policy, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
        self._write_policy(
            dirp,
            [{"match": {"actor": "self_optimizer", "path": "t/*"}, "action": "allow"}],
        )
        assert check_optimizer_write("self_optimizer", "t/x.yaml").decision == "allow"

    def test_invalid_action_ignored(self, tmp_path: Path, monkeypatch):
        dirp = self._policy_dir(tmp_path, monkeypatch)
        self._write_policy(
            dirp,
            [{"match": {"actor": "self_optimizer", "path": "*"}, "action": "sudo"}],
        )
        assert check_optimizer_write("self_optimizer", "x").decision == "ask"


# ── 归一化 ─────────────────────────────────────────────────────────────────


class TestNormalize:
    def test_relative_passthrough(self):
        assert _normalize_repo_path("a/b.yaml") == "a/b.yaml"

    def test_dotdot_escape_returned_verbatim(self):
        out = _normalize_repo_path("../outside/x.yaml")
        assert ".." in out  # 原样返回，交由默认 ask（不硬拒）

    def test_absolute_in_repo(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "sub").mkdir()
        out = _normalize_repo_path(str(tmp_path / "sub" / "f.yaml"))
        assert out == "sub/f.yaml"


# ── daemon 接线（第五重护栏只叠加不替换）──────────────────────────────────


class TestDaemonWiring:
    def test_apply_params_written_into_source(self):
        """两漏斗口源码中均存在规则闸接线（静态守卫，防接线被回退）。"""
        src = (
            Path(__file__).resolve().parent.parent
            / "lingclaude"
            / "self_optimizer"
            / "daemon.py"
        ).read_text(encoding="utf-8")
        assert src.count("check_optimizer_write(") >= 2, "两个写入漏斗口都应接线"
        # 裸 return 回归位（1851 原缺陷: 签名承诺 bool 却裸 return）
        behavior_fn = src.split("def _apply_behavior_params", 1)[1].split("\n    def ", 1)[0]
        assert "return False" in behavior_fn

# -*- coding: utf-8 -*-
"""tests/scripts/test_sync_opencode_client_models.py -- 客户端免费模型同步契约测试.

覆盖纯逻辑与落盘契约（不打真实网络、不碰真实配置）:
- strip_stamp: 剥时间戳后内容等价比较（否则每轮白写 + 备份堆积）
- render_doc: 自带 AUTO 标记、Zen 隐私逐条标注、跳过/阻塞/不在名单三类分开报
- update_doc: 标记区间替换；标记缺失→追加；语义无变化→不落盘（无新备份）
- atomic_write: 同目录 tmp + os.replace，写前备份
- main: 配置损坏拒绝覆盖（rc=1）；锁占用直接跳过
"""
import importlib.util
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SPEC = importlib.util.spec_from_file_location(
    "sync_opencode_client_models_under_test",
    os.path.join(REPO_ROOT, "scripts", "sync_opencode_client_models.py"),
)
som = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("sync_opencode_client_models_under_test", som)
_SPEC.loader.exec_module(som)

ZEN = {"big-pickle", "space-bunny-free", "muse-spark-1.3-contributor-free",
       "nemotron-3-ultra-free", "mimo-v2.5-free"}
OR = {"qwen/qwen3.8-27b:free": 262144, "nvidia/nemotron-3.5-content-safety:free": 128000}
LOCAL = {"minimax/minimax-m3:free@openrouter", "openrouter/free@openrouter"}


def _block(zen=None, zen_prev=None, or_now=None, added=None, blocked=None, skipped=None,
           local_or=None):
    return som.render_doc(
        zen_now=set(ZEN) if zen is None else zen,
        zen_prev=set() if zen_prev is None else zen_prev,
        or_now=dict(OR) if or_now is None else or_now,
        added=added or [], blocked=blocked or [], skipped=skipped or [],
        local_or=set(LOCAL) if local_or is None else local_or)


class TestStripStamp:
    def test_ignores_generated_at_line_only(self):
        a = "<!-- 自动生成于 2026-10-04 22:40，勿手改 -->\n\n| x |"
        b = "<!-- 自动生成于 2026-10-04 23:10，勿手改 -->\n\n| x |"
        assert som.strip_stamp(a) == som.strip_stamp(b)

    def test_keeps_real_content_diff(self):
        assert som.strip_stamp("| a |") != som.strip_stamp("| b |")


class TestRenderDoc:
    def test_block_is_self_delimited(self):
        blk = _block()
        assert blk.startswith(som.DOC_BEGIN)
        assert blk.rstrip().endswith(som.DOC_END)

    def test_zen_privacy_notes_are_specific(self):
        blk = _block()
        assert "零保留" in blk                      # space-bunny / longcat 口径
        assert "禁发机密" in blk                    # contributor / NVIDIA 试用端点
        assert "隐身模型" in blk                    # big-pickle 无 -free 后缀也计入

    def test_skip_and_block_and_gone_are_separate_sections(self):
        blk = _block(added=[("qwen/qwen3.8-27b:free", 262144)],
                     blocked=[("thinkingmachines/inkling:free", "http 429")],
                     skipped=[("nvidia/nemotron-3.5-content-safety:free", "审查类")])
        assert "本轮新增并冒烟通过" in blk
        assert "阻塞" in blk and "http 429" in blk
        assert "跳过" in blk and "审查类" in blk
        assert "不在上游 `:free` 名单" in blk       # 措辞不甩锅给「下架」

    def test_zen_delta_against_previous_state(self):
        blk = _block(zen_prev=ZEN - {"mimo-v2.5-free"})
        assert "新增 1" in blk and "mimo-v2.5-free" in blk


class TestUpdateDoc:
    def test_replaces_between_markers_keeps_handwritten(self, tmp_path, monkeypatch):
        doc = tmp_path / "free-models.md"
        doc.write_text(f"# 标题\n\n手写段\n\n{som.DOC_BEGIN}\n旧表\n{som.DOC_END}\n\n## 用法\n",
                       encoding="utf-8")
        monkeypatch.setattr(som, "DOC", doc)
        som.update_doc(_block(), dry_run=False)
        out = doc.read_text(encoding="utf-8")
        assert "手写段" in out and "## 用法" in out
        assert "旧表" not in out
        assert out.count(som.DOC_BEGIN) == 1 and out.count(som.DOC_END) == 1

    def test_skips_write_when_only_timestamp_differs(self, tmp_path, monkeypatch):
        doc = tmp_path / "free-models.md"
        # 与生产落盘结构一致：head + block（含首尾标记） + tail，不额外插空行
        doc.write_text(f"x\n{_block()}\ny\n", encoding="utf-8")
        monkeypatch.setattr(som, "DOC", doc)
        before = sorted(p.name for p in tmp_path.iterdir())
        som.update_doc(_block(), dry_run=False)   # 内容同、仅时间戳不同
        assert sorted(p.name for p in tmp_path.iterdir()) == before
        assert doc.read_text(encoding="utf-8").count("自动生成于") == 1

    def test_appends_when_markers_missing(self, tmp_path, monkeypatch):
        doc = tmp_path / "free-models.md"
        doc.write_text("# 只有手写段\n", encoding="utf-8")
        monkeypatch.setattr(som, "DOC", doc)
        som.update_doc(_block(), dry_run=False)
        out = doc.read_text(encoding="utf-8")
        assert out.startswith("# 只有手写段")
        assert som.DOC_BEGIN in out and som.DOC_END in out

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        doc = tmp_path / "free-models.md"
        doc.write_text(f"x\n{som.DOC_BEGIN}\n旧\n{som.DOC_END}\n", encoding="utf-8")
        monkeypatch.setattr(som, "DOC", doc)
        som.update_doc(_block(), dry_run=True)
        assert doc.read_text(encoding="utf-8").count("旧") == 1


class TestAtomicWrite:
    def test_backup_then_replace(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text("{}", encoding="utf-8")
        som.atomic_write(p, '{"a":1}\n')
        assert json.loads(p.read_text()) == {"a": 1}
        baks = list(tmp_path.glob("c.json.bak.sync_*"))
        assert len(baks) == 1 and json.loads(baks[0].read_text()) == {}
        assert not [x for x in tmp_path.iterdir() if x.name.startswith(".c.json.")]  # 无残留 tmp


class TestMainGuards:
    def _env(self, tmp_path, monkeypatch, cfg_text):
        monkeypatch.setattr(sys, "argv", ["sync_opencode_client_models.py"])  # main() 内 argparse 吃 pytest argv 会 SystemExit
        cfg = tmp_path / "opencode.json"
        cfg.write_text(cfg_text, encoding="utf-8")
        monkeypatch.setattr(som, "CONFIG", cfg)
        monkeypatch.setattr(som, "DOC", tmp_path / "doc.md")
        monkeypatch.setattr(som, "STATE", tmp_path / "state.json")
        monkeypatch.setattr(som, "LOCK", tmp_path / "sync.lock")
        return cfg

    def test_corrupt_config_refuses_overwrite(self, tmp_path, monkeypatch, capsys):
        cfg = self._env(tmp_path, monkeypatch, '{"broken":')
        assert som.main() == 1
        assert cfg.read_text(encoding="utf-8") == '{"broken":'
        assert "拒绝覆盖" in capsys.readouterr().out

    def test_missing_models_key_returns_1(self, tmp_path, monkeypatch):
        self._env(tmp_path, monkeypatch, json.dumps({"provider": {"proxy3": {}}}))
        assert som.main() == 1

    def test_lock_holder_skips(self, tmp_path, monkeypatch, capsys):
        import fcntl
        cfg = self._env(tmp_path, monkeypatch,
                        json.dumps({"provider": {"proxy3": {"models": {}}}}))
        monkeypatch.setattr(som, "LOCK", tmp_path / "held.lock")
        tmp_path.joinpath("held.lock").touch()
        lk = tmp_path.joinpath("held.lock").open("r")
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            assert som.main() == 0
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)
            lk.close()
        assert "锁未释放" in capsys.readouterr().out
        assert json.loads(cfg.read_text())["provider"]["proxy3"]["models"] == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

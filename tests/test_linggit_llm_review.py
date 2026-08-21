"""灵督 LLM 复核插片单元测试

覆盖:
- 默认关闭 (enabled=false) → 原样返回
- provider 不可用 → fail-open 保留全部
- mock provider 判定 false_positive → 剔除误报
- 仅复核阈值以上命中（low 不送 LLM）
- _parse_verdicts 容忍 JSON 杂讯
- verdict 标记写入 issue
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from linggit.llm_review import LLMReviewer, _parse_verdicts  # noqa: E402


class _FakeResult:
    def __init__(self, ok=True, data=None, error=None):
        self.is_ok = ok
        self.data = data
        self.error = error


class _FakeProvider:
    """可编程 mock provider: 返回预设内容或抛错"""

    def __init__(self, content=""):
        self.content = content
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        return _FakeResult(ok=True, data=_FakeResponse(self.content))


class _FakeResponse:
    def __init__(self, content):
        self.content = content


def _issue(sev="critical", file="a.py", line=1, desc="eval() 使用", content='eval(x)'):
    return {"severity": sev, "file": file, "line": line,
            "description": desc, "content": content}


def test_disabled_returns_issues_unchanged():
    r = LLMReviewer({"enabled": False})
    issues = [_issue()]
    assert r.review(issues) is issues  # 原样引用，未改动


def test_no_provider_fails_open():
    # provider 构建失败/为 None → 保留全部 (fail-open)
    r = LLMReviewer({"enabled": True}, provider=None)
    r._provider_checked = True  # 阻止尝试构建真实 provider
    issues = [_issue(), _issue(sev="high")]
    out = r.review(issues)
    assert len(out) == 2
    assert all("llm_verdict" not in i or i["llm_verdict"] == "uncertain" for i in out)


def test_false_positive_removed():
    content = ('[{"key": "a.py:1", "verdict": "false_positive", '
               '"reason": "test stub"}, '
               '{"key": "a.py:2", "verdict": "true_positive", '
               '"reason": "real eval"}]')
    r = LLMReviewer({"enabled": True, "threshold": "high"},
                    provider=_FakeProvider(content))
    issues = [_issue(file="a.py", line=1, content='api_key = "sk-test"'),
              _issue(file="a.py", line=2, content='eval(user_input)')]
    out = r.review(issues)
    # 仅剔除 false_positive (a.py:1)；true_positive 保留
    assert len(out) == 1
    assert out[0]["line"] == 2
    assert out[0]["llm_verdict"] == "true_positive"


def test_low_severity_not_sent_to_llm():
    r = LLMReviewer({"enabled": True, "threshold": "high"},
                    provider=_FakeProvider("[]"))
    # low 命中不达阈值 → 不送 LLM，原样保留且不打 verdict
    issues = [_issue(sev="low", desc="TODO 未完成标记")]
    out = r.review(issues)
    assert len(out) == 1
    assert "llm_verdict" not in out[0]


def test_unparseable_llm_output_keeps_issues():
    r = LLMReviewer({"enabled": True},
                    provider=_FakeProvider("not json at all"))
    issues = [_issue()]
    out = r.review(issues)
    assert len(out) == 1  # 解析失败 → uncertain → 保留


def test_provider_exception_fails_open():
    class Boom:
        def complete(self, messages):
            raise RuntimeError("provider down")

    r = LLMReviewer({"enabled": True}, provider=Boom())
    issues = [_issue(), _issue(sev="high")]
    out = r.review(issues)
    assert len(out) == 2  # 异常 → 保留全部


def test_parse_verdicts_with_noise():
    # 前缀杂讯 + 数组
    text = 'Sure, here is the result:\n[{"key": "a:3", "verdict": "true_positive", "reason": "x"}, {"key": "b:4", "verdict": "false_positive", "reason": "y"}]'
    out = _parse_verdicts(text)
    assert out == {"a:3": "true_positive", "b:4": "false_positive"}


def test_parse_verdicts_invalid_verdict_ignored():
    text = '[{"key": "a:1", "verdict": "maybe"}]'
    assert _parse_verdicts(text) == {}


def test_batch_size_splits_calls():
    p = _FakeProvider("[]")
    r = LLMReviewer({"enabled": True, "batch_size": 2}, provider=p)
    issues = [_issue(file=f"f{i}.py", line=1, content="x") for i in range(5)]
    r.review(issues)
    # 5 issues / 2 per batch → 3 次调用
    assert len(p.calls) == 3


def test_cache_reuses_verdict_without_llm_call():
    content = ('[{"key": "a.py:1", "verdict": "false_positive", "reason": "stub"}, '
               '{"key": "a.py:2", "verdict": "true_positive", "reason": "real"}]')
    p = _FakeProvider(content)
    r = LLMReviewer({"enabled": True}, provider=p)
    issues = [_issue(file="a.py", line=1, content="x"),
              _issue(file="a.py", line=2, content="y")]
    out1 = r.review(issues)
    assert len(out1) == 1  # a.py:1 被剔除
    assert len(p.calls) == 1  # 第一次真实调用

    # 第二次复核同一批：命中缓存，不再调 LLM
    issues2 = [_issue(file="a.py", line=1, content="x"),
               _issue(file="a.py", line=2, content="y")]
    out2 = r.review(issues2)
    assert len(out2) == 1
    assert len(p.calls) == 1  # 缓存命中 → 无新调用


def test_cache_expiry_reasks():
    import time as _time
    p = _FakeProvider('[{"key": "a.py:1", "verdict": "false_positive", "reason": "stub"}]')
    r = LLMReviewer({"enabled": True}, provider=p)
    issues = [_issue(file="a.py", line=1, content="x")]
    r.review(issues)
    assert len(p.calls) == 1

    # 伪造过期时间戳（写入内部 _cache，_cache_get 优先查它）→ 应重新调用
    r._cache[("a.py", 1, "eval() 使用")] = ("false_positive", _time.time() - 24 * 3600 - 10)
    out = r.review([_issue(file="a.py", line=1, content="x")])
    assert len(p.calls) == 2  # 过期 → 重新询问


def test_report_includes_llm_verdicts():
    """report() 应输出 llm_verdicts 双标注汇总"""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from linggit.bot import LingGitBot
    from lingmemory.core import init_db

    init_db("/tmp/test_report_verdicts.db")
    bot = LingGitBot(db_path="/tmp/test_report_verdicts.db",
                     rules_dir=str(Path(__file__).resolve().parent.parent / "linggit" / "rules"))
    # 直接构造带 llm_verdict 的 issues 写入 gate
    issues = [_issue(sev="critical", file="a.py", line=1),
              _issue(sev="high", file="b.py", line=2)]
    issues[0]["llm_verdict"] = "false_positive"
    issues[1]["llm_verdict"] = "true_positive"
    gate = bot.lm.create(type="security_gate", data={
        "gate_layer": "changeset", "actor": "t", "action": "commit",
        "target": "a.py,b.py", "project": "p", "issues": issues,
        "risk_level": "critical",
    }, created_by="linggit-bot")
    rep = bot.report(gate)
    assert rep["llm_verdicts"] == {"false_positive": 1, "true_positive": 1}
    assert rep["issue_count"] == 2

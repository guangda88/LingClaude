"""lifecycle_enforcer 单元测试"""
import json
import os
import sys
import tempfile
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'lingmemory'))
from lifecycle_enforcer import (
    is_handover_path,
    is_handover_write,
    extract_member_from_path,
    build_advice,
    check_hook_input,
    DEPRECATED_CONFIRMED,
)


class TestIsHandoverPath:
    def test_md(self):
        assert is_handover_path('/home/ai/lingclaude/.lingclaude/handover.md')

    def test_yaml(self):
        assert is_handover_path('/home/ai/lingminopt/.lingminopt/handover.yaml')

    def test_yml(self):
        assert is_handover_path('/home/ai/lingyang/handover.yml')

    def test_non_handover(self):
        assert not is_handover_path('/home/ai/lingclaude/CRUSH.md')

    def test_empty(self):
        assert not is_handover_path('')

    def test_case_insensitive(self):
        assert is_handover_path('/home/ai/lingclaude/HANDOVER.MD')


class TestIsHandoverWrite:
    def test_edit_tool(self):
        is_w, path = is_handover_write('edit', {
            'file_path': '/home/ai/lingclaude/.lingclaude/handover.md'
        })
        assert is_w
        assert 'handover.md' in path

    def test_write_tool(self):
        is_w, path = is_handover_write('write', {
            'file_path': '/home/ai/lingresearch/handover.md'
        })
        assert is_w

    def test_multiedit_tool(self):
        is_w, path = is_handover_write('multiedit', {
            'file_path': '/home/ai/lingminopt/.lingminopt/handover.yaml',
            'edits': []
        })
        assert is_w

    def test_bash_redirect(self):
        is_w, path = is_handover_write('bash', {
            'command': 'echo "status" > /home/ai/lingclaude/.lingclaude/handover.md'
        })
        assert is_w
        assert 'handover.md' in path

    def test_bash_tee(self):
        is_w, path = is_handover_write('bash', {
            'command': 'echo "data" | tee /home/ai/lingresearch/handover.md'
        })
        assert is_w

    def test_bash_cp(self):
        is_w, path = is_handover_write('bash', {
            'command': 'cp /tmp/draft.md /home/ai/lingclaude/.lingclaude/handover.md'
        })
        assert is_w

    def test_non_handover_file(self):
        is_w, path = is_handover_write('edit', {
            'file_path': '/home/ai/lingclaude/lingmemory/core.py'
        })
        assert not is_w

    def test_non_write_tool(self):
        is_w, path = is_handover_write('view', {
            'file_path': '/home/ai/lingclaude/.lingclaude/handover.md'
        })
        assert not is_w

    def test_empty_input(self):
        assert not is_handover_write('bash', {})[0]
        assert not is_handover_write('', {})[0]


class TestExtractMember:
    def test_normal(self):
        assert extract_member_from_path('/home/ai/lingclaude/.lingclaude/handover.md') == 'lingclaude'

    def test_no_match(self):
        assert extract_member_from_path('/tmp/handover.md') is None

    def test_empty(self):
        assert extract_member_from_path('') is None


class TestBuildAdvice:
    def test_contains_lm_transition(self):
        advice = build_advice('/home/ai/lingclaude/.lingclaude/handover.md', 'lingclaude')
        assert 'lm_transition' in advice
        assert 'activate' in advice
        assert 'end' in advice
        assert 'lingclaude' in advice

    def test_no_member(self):
        advice = build_advice('/tmp/handover.md', None)
        assert 'lm_transition' in advice


class TestCheckHookInput:
    def test_non_handover_write_passes(self, monkeypatch, capsys):
        payload = json.dumps({
            'tool_name': 'edit',
            'tool_input': {'file_path': '/home/ai/lingclaude/CRUSH.md'}
        })
        monkeypatch.setattr('sys.stdin', _make_stdin(payload))
        assert check_hook_input() == 0

    def test_deprecated_member_blocks(self, monkeypatch, capsys, tmp_path):
        # 创建一个临时 handover 文件模拟
        handover = tmp_path / 'handover.md'
        handover.write_text('# DEPRECATED\nold data')
        payload = json.dumps({
            'tool_name': 'write',
            'tool_input': {'file_path': str(handover)}
        })
        monkeypatch.setattr('sys.stdin', _make_stdin(payload))
        monkeypatch.setattr('lifecycle_enforcer.is_deprecated_handover', lambda p: True)
        monkeypatch.setattr('lifecycle_enforcer.extract_member_from_path', lambda p: 'lingclaude')
        assert check_hook_input() == 2
        captured = capsys.readouterr()
        assert 'lm_transition' in captured.err

    def test_invalid_json_passes(self, monkeypatch):
        monkeypatch.setattr('sys.stdin', _make_stdin('not json'))
        assert check_hook_input() == 0

    def test_bash_redirect_blocks(self, monkeypatch, capsys):
        payload = json.dumps({
            'tool_name': 'bash',
            'tool_input': {'command': 'echo x > /home/ai/lingclaude/.lingclaude/handover.md'}
        })
        monkeypatch.setattr('sys.stdin', _make_stdin(payload))
        monkeypatch.setattr('lifecycle_enforcer.is_deprecated_handover', lambda p: True)
        monkeypatch.setattr('lifecycle_enforcer.extract_member_from_path', lambda p: 'lingclaude')
        assert check_hook_input() == 2


class TestDeprecatedConfirmed:
    def test_lingclaude_in_set(self):
        assert 'lingclaude' in DEPRECATED_CONFIRMED

    def test_lingresearch_in_set(self):
        assert 'lingresearch' in DEPRECATED_CONFIRMED

    def test_at_least_10_members(self):
        assert len(DEPRECATED_CONFIRMED) >= 10


def _make_stdin(text):
    import io
    return io.StringIO(text)

#!/usr/bin/env python3
"""Lifecycle Enforcer - 任务生命周期强制器

检测 handover 文件写入行为，提示改用 lingmemory lm_transition。
可被 Crush PreToolUse hook 调用，也可被其他成员的 hook 调用。

调用方式:
  1. 作为 hook: echo '<json>' | python3 lifecycle_enforcer.py check-write
  2. 作为库: from lifecycle_enforcer import is_handover_write, build_advice

退出码 (hook 模式):
  0 - 放行 (非 handover 写入，或已标记 DEPRECATED 的只读快照)
  2 - 阻断 (检测到对活跃 handover 文件的写入)
"""
from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path

# handover 文件名模式
HANDOVER_PATTERNS = [
    re.compile(r'handover\.(md|yaml|yml)$', re.IGNORECASE),
    re.compile(r'/\.(\w+)/handover\.(md|yaml|yml)$', re.IGNORECASE),
]

# 已确认 DEPRECATED 的成员 (从 lingmemory session records 提取)
# 这些成员的 handover 文件设为只读快照，写入应被阻断
DEPRECATED_CONFIRMED = {
    'lingclaude', 'lingresearch', 'lingminopt', 'lingan', 'lingtongask',
    'lingweb', 'lingmessage', 'lingflow', 'lingxi', 'lingcreate',
}


def is_handover_path(file_path: str) -> bool:
    """判断路径是否为 handover 文件"""
    if not file_path:
        return False
    return any(p.search(file_path) for p in HANDOVER_PATTERNS)


def is_deprecated_handover(file_path: str) -> bool:
    """判断 handover 文件是否已标记 DEPRECATED"""
    try:
        if not os.path.isfile(file_path):
            return False
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            head = f.read(500)
        return 'DEPRECATED' in head
    except (OSError, PermissionError):
        return False


def extract_member_from_path(file_path: str) -> str | None:
    """从路径提取成员名，如 /home/ai/lingclaude/.lingclaude/handover.md -> lingclaude"""
    m = re.search(r'/home/ai/(\w+)/', file_path)
    return m.group(1) if m else None


def is_handover_write(tool_name: str, tool_input: dict) -> tuple[bool, str | None]:
    """检测工具调用是否在写 handover 文件

    返回: (is_write, file_path)
    """
    file_path = None

    # edit / multiedit / write 工具直接操作文件
    if tool_name in ('edit', 'write', 'multiedit'):
        file_path = tool_input.get('file_path', '')

    # bash 工具间接写文件
    elif tool_name == 'bash':
        cmd = tool_input.get('command', '')
        # 提取重定向目标: echo ... > handover.md, cat > handover.md
        redirect_match = re.search(r'[>]{1,2}\s*(\S*handover\S*)', cmd)
        if redirect_match:
            file_path = redirect_match.group(1)
        # 提取 sed -i, tee 等
        tee_match = re.search(r'tee\s+(\S*handover\S*)', cmd)
        if tee_match:
            file_path = tee_match.group(1)
        # cp/mv 目标
        cp_match = re.search(r'\b(?:cp|mv)\s+\S+\s+(\S*handover\S*)', cmd)
        if cp_match:
            file_path = cp_match.group(1)

    if file_path and is_handover_path(file_path):
        # 补全相对路径
        if not os.path.isabs(file_path):
            cwd = os.environ.get('CRUSH_CWD', os.getcwd())
            file_path = os.path.join(cwd, file_path)
        return True, file_path

    return False, None


def build_advice(file_path: str, member: str | None) -> str:
    """构建提示信息"""
    member_str = f'成员 {member}' if member else '该成员'
    return f"""⛔ 检测到对 handover 文件的写入操作被拦截

目标文件: {file_path}
成员: {member_str or '未知'}

handover 文件已 DEPRECATED，任务生命周期管理改用 lingmemory:
  1. 创建会话记录: lm_create(member="{member or 'xxx'}", type="session", data={{...}})
  2. 启动任务: lm_transition(member="{member or 'xxx'}", record_id=<id>, event_type="activate")
  3. 完成任务: lm_transition(member="{member or 'xxx'}", record_id=<id>, event_type="end")

session 状态流转: created -> in_progress(activate) -> completed(end)

如需写入只读快照，请先确认该文件已标记 DEPRECATED 且确实需要更新冷归档。
否则请使用 lingmemory 工具管理任务生命周期。"""


def check_hook_input() -> int:
    """从 stdin 读取 hook 输入并检测"""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return 0  # 解析失败，放行

    tool_name = payload.get('tool_name', '')
    tool_input = payload.get('tool_input', {})

    is_write, file_path = is_handover_write(tool_name, tool_input)
    if not is_write or not file_path:
        return 0

    member = extract_member_from_path(file_path)

    # 已标记 DEPRECATED 的文件 -> 阻断写入
    if is_deprecated_handover(file_path) or member in DEPRECATED_CONFIRMED:
        advice = build_advice(file_path, member)
        print(advice, file=sys.stderr)
        return 2

    # 未标记但匹配成员名 -> 提示但不阻断 (允许首次标记 DEPRECATED)
    if member:
        # 输出 context 提示，不阻断
        ctx = f"提示: {file_path} 尚未标记 DEPRECATED。如仍在用 handover，请迁移至 lingmemory lm_transition。"
        print(json.dumps({"context": ctx}, ensure_ascii=False))
        return 0

    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'check-write':
        sys.exit(check_hook_input())

    # 默认: 检查当前成员 handover 状态
    print("Lifecycle Enforcer - 用法:")
    print("  hook 模式: echo '<json>' | python3 lifecycle_enforcer.py check-write")
    print("  检查成员: python3 lifecycle_enforcer.py <member_name>")
    print("  全族扫描: python3 lifecycle_enforcer.py --scan-all")


if __name__ == '__main__':
    main()

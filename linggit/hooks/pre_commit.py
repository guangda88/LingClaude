#!/usr/bin/env python3
"""灵督 (linggit-bot) git pre-commit hook

安装: cp /home/ai/lingclaude/linggit/hooks/pre_commit.py /home/ai/lingclaude/.git/hooks/pre-commit && chmod +x

功能:
  1. 获取 staged 文件 + diff
  2. 灵督增量审查 (只看 + 行)
  3. critical -> block commit (exit 1)
  4. high -> 警告但放行 (可配置 --strict 阻断)
  5. 其余 -> 放行
"""

import sys
import os
import subprocess
import tempfile

# 灵督路径
LINGGIT_DIR = "/home/ai/lingclaude/linggit"
RULES_DIR = os.path.join(LINGGIT_DIR, "rules")
DB_PATH = tempfile.mktemp(suffix=".db")

sys.path.insert(0, "/home/ai/lingclaude")
os.environ.setdefault("PYTHONPATH", "/home/ai/lingclaude")


def get_staged_files():
    """获取 staged 文件列表"""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True,
    )
    return [f for f in result.stdout.strip().split("\n") if f]


def get_staged_diff():
    """获取 staged diff"""
    result = subprocess.run(
        ["git", "diff", "--cached"],
        capture_output=True, text=True,
    )
    return result.stdout


def main():
    # 只审查 Python 文件
    staged = get_staged_files()
    py_files = [f for f in staged if f.endswith(".py")]
    if not py_files:
        print("[灵督] 无 Python 文件变更, 跳过")
        return 0

    diff = get_staged_diff()
    if not diff.strip():
        return 0

    # 灵督审查
    from linggit import LingGitBot, GrayZonePending

    bot = LingGitBot(DB_PATH, None, RULES_DIR)

    # 用 audit_batch 的内存模式 (不写 DB)
    rules = bot._load_rules()
    issues = []

    for fpath in py_files:
        # 从 diff 中提取该文件的 + 行
        file_diff = []
        in_file = False
        for line in diff.split("\n"):
            if line.startswith("diff --git"):
                in_file = fpath in line
            elif in_file and line.startswith("+") and not line.startswith("+++"):
                file_diff.append(line)

        if not file_diff:
            continue

        diff_text = "\n".join(file_diff)
        for rule in rules.get("checks", []):
            found = bot._match_rule(rule, diff_text, [fpath])
            for issue in found:
                issue["file"] = fpath
                issues.append(issue)

    # 清理
    try:
        os.unlink(DB_PATH)
    except Exception:
        pass

    if not issues:
        print(f"[灵督] {len(py_files)} Python 文件审查通过, 0 issues")
        return 0

    # 分类
    critical = [i for i in issues if i["severity"] == "critical"]
    high = [i for i in issues if i["severity"] == "high"]
    medium = [i for i in issues if i["severity"] == "medium"]
    low = [i for i in issues if i["severity"] == "low"]

    print(f"[灵督] {len(py_files)} 文件, {len(issues)} issues "
          f"(critical={len(critical)} high={len(high)} med={len(medium)} low={len(low)})")

    # 打印问题
    for issue in issues:
        sev = issue["severity"]
        icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}.get(sev, "?")
        print(f"  {icon} [{sev}] {issue['file']}: {issue['description']}")
        if issue.get("fix"):
            print(f"     fix: {issue['fix']}")
        if issue.get("content"):
            print(f"     code: {issue['content']}")

    # critical -> block
    if critical:
        print(f"\n[灵督] 阻断 commit: {len(critical)} critical 问题")
        print("  修复后重新 git add + commit")
        print("  或跳过: git commit --no-verify")
        return 1

    # high -> 警告 (除非 --strict)
    if high and "--strict" in sys.argv:
        print(f"\n[灵督] 阻断 commit (--strict): {len(high)} high 问题")
        return 1

    if high:
        print(f"\n[灵督] 警告: {len(high)} high 问题 (放行, --strict 可阻断)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

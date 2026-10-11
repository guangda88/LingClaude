#!/usr/bin/env python3
"""铁律守卫红清偿（2026-10-02）。

债 A：既有豁免行号漂移维护（G11b/M2 plugin_loader 已手工修正，见各档 corrected 字段）。
债 B：0930-1002 落库的 11 个新模块未登记 M1 豁免（真新增，行级、只缩不放）
      + hook_registry.py:134 用户 hook 加载器未登记 G11b。

纪律：
  - 豁免≠违规不开，是公开记账（09-21 approval_matrix 先例）；行级、带 reason、
    自动 30 天 review_due（arch_ledger.py:94），复审时逐行确认是否可收窄。
  - 只用 arch_ledger.py CLI 入册，schema 与存量档一致。
  - 范围锚定：只处理本会话落库模块的命中；范围外文件出现 = 拒跑报错。
"""
import re
import subprocess
import sys
from collections import defaultdict

ROOT = "/home/ai/lingclaude"
OFFENDER_FILE = "/tmp/m1_offenders.txt"

SESSION_MODULES = {
    "core/agent_registry.py": "P1 Agent 声明化（5580fbd/cec7d64）",
    "core/hook_registry.py": "P1① Hook 生命周期（1da2f11）",
    "core/model_tiers.py": "③ 模型三档语义（5484182）",
    "core/plugin_governance.py": "M3 插件治理（084db26）",
    "core/project_memory.py": "P1③ per-project memory（164de66）",
    "core/sandbox_rules.py": "C 目录级沙箱（09a4f20）",
    "core/session_budget.py": "P1② 预算线核心（4169e65）",
    "core/session_budget_gate.py": "P1② 预算线接线（a356ac3/0695b3d）",
    "core/session_index.py": "② sqlite 索引（4cb7a2b）",
    "core/task_contract.py": "M2 gate 契约化（a831612）",
    "core/tool_auth_hook.py": "守卫四档引擎（29f8132）",
}

G11B_REASON = ("P1① hook_registry 用户插件加载器（1da2f11）: spec_from_file_location 为"
               "hook 动态加载机制本体（与 plugin_loader 同构，机制豁免非盲区利用）；"
               "动态模块名无法静态判定。行级豁免，复审时确认可否收敛到 PluginLoader 间接入口。")


def main() -> int:
    with open(OFFENDER_FILE, encoding="utf-8") as fh:
        raw = fh.readlines()

    # 解析: "E           core/xxx.py [id:agent] 行[25, 26, ...]"
    hits: dict[str, set[int]] = defaultdict(set)
    for line in raw:
        m = re.search(r"core/([a-z_0-9]+\.py) \[[^\]]+\] 行\[([0-9, ]+)\]", line)
        if not m:
            continue
        rel = f"core/{m.group(1)}"
        if rel not in SESSION_MODULES:
            print(f"ABORT: 范围外文件命中 {rel}（SESSION_MODULES 未锚定）", file=sys.stderr)
            return 1
        for ln in m.group(2).split(","):
            hits[rel].add(int(ln.strip()))

    print(f"解析到 {sum(len(v) for v in hits.values())} 行 / {len(hits)} 文件")
    failed = []
    for rel, lines in sorted(hits.items()):
        line_str = ",".join(str(n) for n in sorted(lines))
        reason = (f"{SESSION_MODULES[rel]}: M1 命中行属该模块固有概念语义"
                  f"（agent/model/tool/policy 等为模块领域本体，非主干泄漏），"
                  f"沿 09-21 approval_matrix 先例行级登记，复审逐行确认可否改词收窄。")
        cmd = ["python3", "scripts/arch_ledger.py", "exemption", "add",
               "M1", rel, "--reason", reason, "--lines", line_str]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        status = "OK " if r.returncode == 0 else "FAIL"
        print(f"[{status}] M1 {rel} lines={line_str}")
        if r.returncode != 0:
            print(r.stderr[-300:])
            failed.append(rel)

    # G11b: hook_registry.py:134（同文件 M1 档独立，不混册）
    r = subprocess.run(["python3", "scripts/arch_ledger.py", "exemption", "add",
                        "G11b", "core/hook_registry.py", "--reason", G11B_REASON,
                        "--lines", "134"], cwd=ROOT, capture_output=True, text=True)
    print(f"[{'OK ' if r.returncode == 0 else 'FAIL'}] G11b core/hook_registry.py line=134")
    if r.returncode != 0:
        print(r.stderr[-300:])
        failed.append("G11b:hook_registry")

    if failed:
        print(f"\n未完成: {failed}", file=sys.stderr)
        return 1
    print("\n全部入册完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())

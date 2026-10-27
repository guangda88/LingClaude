#!/usr/bin/env python3
"""gate_exemption_check.py — 全量推送门禁 v2.0 的已知失败基线豁免判定器 (2026-10-28)

被 scripts/pre_push_gate.sh 调用（RC=1 时）：
    python3 scripts/gate_exemption_check.py <baseline.json> < lastfailed.json

stdin: pytest cacheprovider 的 lastfailed.json 原文（{test_id: [...] }）
argv1: 基线文件 data/arch_ledger/test_baselines/push_gate_known_failures.json

退出码（门禁按此裁决）：
    0 = 失败集 ⊆ 基线 → 豁免放行
    1 = 存在超基线新红 → 拒绝（棘轮只缩不放，新红必须修复或显式入基线）
    2 = 数据面异常（基线/lastfailed 缺失或损坏）→ 回退 v1 严格语义（不可豁免）
    3 = 语义异常（RC=1 却无失败记录，如收集错误）→ 不可豁免

设计约束：本判定器不解释测试内容、不做模糊匹配——test_id 必须精确全等。
基线文件入库跟踪、每条带 reason + verified_head，机制说明见 PUSH_SOP §三。
"""
import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("GATE: EXEMPT_CHECK——用法错误（需基线路径参数）")
        return 2
    base_path = sys.argv[1]
    try:
        base = json.load(open(base_path, encoding="utf-8"))
        known = {e["test_id"] for e in base.get("entries", [])}
        n_known = len(known)
    except FileNotFoundError:
        print("GATE: EXEMPT_CHECK——基线文件缺失，回退 v1 严格语义")
        return 2
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"GATE: EXEMPT_CHECK——基线不可读({e})，回退 v1 严格语义")
        return 2

    try:
        failed = set(json.load(sys.stdin).keys())
    except (ValueError, TypeError) as e:
        print(f"GATE: EXEMPT_CHECK——lastfailed 不可读({e})，不可豁免")
        return 2

    if not failed:
        # RC=1 却无失败记录：多为收集错误(usage/collection error)，不能豁免
        print("GATE: EXEMPT_CHECK——RC=1 但无失败记录（疑收集错误），不可豁免")
        return 3

    extra = sorted(failed - known)
    if extra:
        print(
            f"GATE: EXEMPT_DENIED——{len(extra)} 个新红不在基线"
            f"（基线 {n_known} 条，棘轮只缩不放）："
        )
        for tid in extra[:20]:
            print(f"  NEW-RED {tid}")
        if len(extra) > 20:
            print(f"  ... 其余 {len(extra) - 20} 条略")
        return 1

    print(
        f"GATE: EXEMPT_OK——失败 {len(failed)} 个全部在已知失败基线内"
        f"（基线 {n_known} 条），豁免放行"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

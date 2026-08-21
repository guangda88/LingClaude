"""P1 复核脚本: 通过 atomcode CLI (deepseek-v4-flash@atomgit, 自带签名) 复核命中

CLI 自带 ATOMCODE_SIG 签名，是唯一可用的 LLM 复核通道（glm-4.7 限流、
裸 HTTP 403）。agent 会读源文件看上下文，语义判定更准。

用法: python3 scripts/p1_llm_review_cli.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ATOMCODE = "/home/ai/.local/bin/atomcode"
ISSUES_FILE = "/tmp/p1_issues.json"
BATCH = 5  # 每批命中数


def _load_issues() -> list[dict]:
    return json.load(open(ISSUES_FILE))


def _fmt_hit(i: dict) -> str:
    return (
        f"{i.get('file', '?')}:{i.get('line', '?')} [{i.get('severity', '?')}] "
        f"{i.get('description', '?')} | 命中内容: {str(i.get('content', ''))[:100]}"
    )


def _extract_json(text: str) -> list[dict]:
    # CLI 输出含 agent 叙述 + ```json 块；遍历所有代码块尝试解析，
    # 全部失败再兜底全文搜索 [ ... ] 数组。
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S):
        try:
            data = json.loads(m.group(1).strip())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            continue
    m = re.search(r"\[.*\]", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    return []


def _review_batch(batch: list[dict], batch_no: int) -> list[dict]:
    hits = "\n".join(f"{n}. {_fmt_hit(i)}" for n, i in enumerate(batch, 1))
    prompt = (
        "你是代码安全审计复核员。判断以下规则命中是真问题还是误报"
        "(测试桩/示例字符串/占位符/注释/文档示例/环境变量引用/#[cfg(test)]内代码)。"
        "可读源文件确认上下文。严格只输出 JSON 数组:\n"
        '[{"key":"file:line","verdict":"true_positive|false_positive|uncertain","reason":"一句话"}]\n\n'
        f"{hits}"
    )
    r = subprocess.run(
        [ATOMCODE, "-p", prompt, "--no-telemetry", "--dev"],
        capture_output=True, text=True, timeout=180,
    )
    return _extract_json(r.stdout + r.stderr)


def main() -> int:
    issues = _load_issues()
    cands = [i for i in issues if i.get("severity") in ("high", "critical")]
    print(f"复核 {len(cands)} 条 (critical={sum(1 for i in cands if i['severity']=='critical')}, "
          f"high={sum(1 for i in cands if i['severity']=='high')})")

    all_verdicts: dict[str, str] = {}
    for start in range(0, len(cands), BATCH):
        batch = cands[start : start + BATCH]
        print(f"\n--- 批次 {start // BATCH + 1} ({len(batch)} 条) ---")
        for n, i in enumerate(batch, 1):
            print(f"  {n}. {i['file'].split('/')[-1]}:{i['line']} [{i['severity']}] {i['description']}")
        verdicts = _review_batch(batch, start // BATCH + 1)
        print("  ->", verdicts)
        for v in verdicts:
            all_verdicts[str(v.get("key", ""))] = str(v.get("verdict", "uncertain"))
        # 批次间留间隔，避免 CLI 并发限流
        if start + BATCH < len(cands):
            import time
            time.sleep(2)

    # 汇总
    counts: dict[str, int] = {}
    for i in cands:
        key = f"{i.get('file', '?')}:{i.get('line', '?')}"
        v = all_verdicts.get(key, "uncertain")
        counts[v] = counts.get(v, 0) + 1
    print("\n=== 汇总 ===")
    print("verdict 分布:", counts)
    fp = [i for i in cands if all_verdicts.get(f"{i['file']}:{i['line']}") == "false_positive"]
    print(f"误报剔除: {len(fp)} / {len(cands)}")
    print("\n=== 误报明细 ===")
    for i in fp:
        print(f"  [{i['severity']}] {i['file'].split('/')[-1]}:{i['line']} {i['description']}")
    json.dump(all_verdicts, open("/tmp/p1_verdicts.json", "w"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

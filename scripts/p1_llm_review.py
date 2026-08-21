"""P1 验证脚本: 用 deepseek-v4-flash@atomgit 通道真实复核 atomcode 历史命中

凭据从 auth.toml 内存读取（不打印、不经命令行）。执行: python3 scripts/p1_llm_review.py
"""
import json
import sys
import time
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from linggit.llm_review import LLMReviewer  # noqa: E402


def _load_token() -> str:
    auth = tomllib.load(open(Path.home() / ".atomcode" / "auth.toml", "rb"))

    def find(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if "access_token" in k.lower() and isinstance(v, str) and len(v) > 20:
                    return v
                r = find(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = find(v)
                if r:
                    return r
        return None

    return find(auth) or ""


def main() -> int:
    token = _load_token()
    print("token loaded:", bool(token), "len:", len(token))
    if not token:
        print("FAIL: no token in auth.toml")
        return 1

    issues = json.load(open("/tmp/p1_issues.json"))
    cands = [i for i in issues if i.get("severity") in ("high", "critical")]
    print(f"send to LLM review: {len(cands)} (critical={sum(1 for i in cands if i['severity'] == 'critical')})")

    reviewer = LLMReviewer({
        "enabled": True,
        "threshold": "high",
        "batch_size": 6,
        "timeout_s": 90,
        "model_config": {
            "provider": "openai",
            "model": "deepseek-v4-flash",
            "api_key": token,
            "base_url": "https://llm-api.atomgit.com/v1",
            "max_tokens": 512,
            "temperature": 0.2,
        },
    })
    t0 = time.time()
    provider = reviewer._get_provider()
    print("provider:", provider is not None)
    if provider is None:
        print("FAIL: provider unavailable")
        return 1

    out = reviewer.review(issues)
    elapsed = time.time() - t0

    verdicts: dict[str, int] = {}
    for i in issues:
        v = i.get("llm_verdict", "not_reviewed")
        verdicts[v] = verdicts.get(v, 0) + 1
    removed = [i for i in issues if i.get("llm_verdict") == "false_positive"]

    print(f"\nelapsed {elapsed:.1f}s")
    print("verdict distribution:", verdicts)
    print(f"removed false_positives: {len(removed)} | remaining: {len(out)}")
    print("\n=== removed (false_positive) ===")
    for i in removed:
        print(f"  [{i['severity']}] {i['file'].split('/')[-1]}:{i['line']} {i['description']} | {str(i.get('content', ''))[:70]}")
    print("\n=== kept (true_positive) ===")
    for i in issues:
        if i.get("llm_verdict") == "true_positive":
            print(f"  [{i['severity']}] {i['file'].split('/')[-1]}:{i['line']} {i['description']} | {str(i.get('content', ''))[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""HumanEval Benchmark Harness — 灵族Agent基准测试

通过LLM Proxy(:8765)调用模型生成代码，用human_eval评估pass@1。
用途：获取灵族在标准化基准上的第一批量化数据，与业界横向可比。

用法:
    python3 benchmarks/humaneval/run.py --model glm-4.7 --limit 20
    python3 benchmarks/humaneval/run.py --model glm-5.1 --full
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROXY_URL = os.environ.get("LLM_PROXY_URL", "http://127.0.0.1:8765")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class BenchmarkResult:
    model: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    details: list[dict] = field(default_factory=list)

    @property
    def pass_at_1(self) -> float:
        if self.total == 0:
            return 0.0
        return self.passed / self.total

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "benchmark": "HumanEval",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "pass_at_1": round(self.pass_at_1, 4),
        }


def load_problems(limit: int | None = None) -> dict:
    from human_eval.data import read_problems

    problems = read_problems()
    if limit:
        keys = list(problems.keys())[:limit]
        return {k: problems[k] for k in keys}
    return problems


def generate_completion(
    prompt: str, model: str, api_key: str, max_retries: int = 2
) -> str | None:
    """调用LLM Proxy生成代码补全。"""
    full_prompt = (
        "Complete the following Python function. "
        "Return ONLY the code, no explanations.\n\n" + prompt
    )

    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": full_prompt}],
            "max_tokens": 512,
            "temperature": 0.0,
        }
    ).encode()

    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(
                f"{PROXY_URL}/v1/chat/completions",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": api_key,
                    "X-Caller": "lingclaude-benchmark",
                    "X-Purpose": "code_generation",
                },
            )
            resp = urllib.request.urlopen(req, timeout=120)
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries:
                time.sleep(5 * (attempt + 1))
                continue
            print(f"  HTTP {e.code}: {e.reason}", file=sys.stderr)
            return None
        except Exception as e:
            if attempt < max_retries:
                time.sleep(3)
                continue
            print(f"  Error: {e}", file=sys.stderr)
            return None
    return None


def extract_code_block(text: str) -> str:
    """从LLM响应中提取Python代码。"""
    lines = text.strip().split("\n")
    if "```" in text:
        in_block = False
        code_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                in_block = not in_block
                continue
            if in_block:
                code_lines.append(line)
        if code_lines:
            result = "\n".join(code_lines)
            result = result.replace("```", "")
            return result

    func_lines = []
    found_def = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("def ") or (found_def and (stripped.startswith("    ") or stripped.startswith("import ") or stripped.startswith("from ") or stripped == "")):
            found_def = True
        if found_def:
            func_lines.append(line)
    if func_lines:
        return "\n".join(func_lines)

    return text.strip()


def run_test(code: str, test: str, entry_point: str, timeout: int = 10) -> bool:
    """执行生成的代码+测试，返回是否通过。

    HumanEval测试格式为 check(candidate)，candidate是被测函数。
    entry_point是函数名，需要从生成代码中获取。
    """
    call_line = f"check({entry_point})"
    full_code = code + "\n\n" + test + "\n\n" + call_line
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, dir="/tmp"
    ) as f:
        f.write(full_code)
        f.flush()
        fname = f.name

    try:
        result = subprocess.run(
            [sys.executable, fname],
            capture_output=True,
            timeout=timeout,
            text=True,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False
    finally:
        try:
            os.unlink(fname)
        except OSError:
            pass


def run_benchmark(model: str, limit: int | None, api_key: str) -> BenchmarkResult:
    problems = load_problems(limit)
    result = BenchmarkResult(model=model, total=len(problems))

    print(f"HumanEval Benchmark: model={model}, problems={len(problems)}", flush=True)
    print(f"Proxy: {PROXY_URL}", flush=True)
    print("-" * 60, flush=True)

    for i, (task_id, problem) in enumerate(problems.items(), 1):
        prompt = problem["prompt"]
        test = problem["test"]
        entry_point = problem.get("entry_point", "")

        completion = generate_completion(prompt, model, api_key)
        if completion is None:
            result.errors += 1
            status = "ERROR"
        else:
            code = extract_code_block(completion)
            passed = run_test(code, test, entry_point)
            if passed:
                result.passed += 1
                status = "PASS"
            else:
                result.failed += 1
                status = "FAIL"

        result.details.append(
            {"task_id": task_id, "status": status}
        )

        if i % 5 == 0 or i == len(problems):
            print(
                f"  [{i}/{len(problems)}] "
                f"pass={result.passed} fail={result.failed} "
                f"err={result.errors} | "
                f"pass@1={result.passed}/{result.passed+result.failed+result.errors}",
                flush=True,
            )

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="HumanEval Benchmark")
    parser.add_argument("--model", required=True, help="Model name")
    parser.add_argument("--limit", type=int, default=None, help="Max problems")
    parser.add_argument("--full", action="store_true", help="Run all 164 problems")
    parser.add_argument("--api-key", default=None, help="API key")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("LLM_PROXY_KEY", "test-key")
    limit = None if args.full else args.limit or 10

    result = run_benchmark(args.model, limit, api_key)

    print("\n" + "=" * 60)
    print(f"RESULT: {result.model}")
    print(f"  pass@1 = {result.pass_at_1:.1%} ({result.passed}/{result.total})")
    print(f"  failed = {result.failed}")
    print(f"  errors = {result.errors}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model = args.model.replace("/", "_")
    outfile = RESULTS_DIR / f"humaneval_{safe_model}_{ts}.json"
    with open(outfile, "w") as f:
        json.dump(result.to_dict() | {"details": result.details}, f, indent=2)
    print(f"\nSaved: {outfile}")


if __name__ == "__main__":
    main()

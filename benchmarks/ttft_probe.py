#!/usr/bin/env python3
"""TTFT / 吐字速度对照实验：同一模型同端点，只改 prompt 体量。"""
import json
import os
import time
import urllib.request

BASE = "https://open.bigmodel.cn/api/coding/paas/v4"
KEY = os.environ.get("ZHIPU_API_KEY", "")
MODEL = "glm-5.3-flash"


def probe(label, filler_chars, max_out=256):
    """filler_chars: 系统提示词里填充文本的字符数，模拟不同 prefill 体量。"""
    filler = ("这是一段用于测量预填充开销的填充文本。"
              "系统要求保持既有工作流与输出格式不变。") * max(1, filler_chars // 30)
    body = {
        "model": MODEL,
        "stream": True,
        "max_tokens": max_out,
        "messages": [
            {"role": "system", "content": "你是一个测试助手。" + filler},
            {"role": "user", "content": "只回答两个字：你好"},
        ],
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {KEY}",
        },
    )
    t0 = time.perf_counter()
    ttft = None
    chars = 0
    out_text = ""
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            for line in resp:
                line = line.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                    delta = obj.get("choices", [{}])[0].get("delta", {})
                    chunk = delta.get("content", "")
                except Exception:
                    continue
                if chunk and ttft is None:
                    ttft = time.perf_counter() - t0
                chars += len(chunk)
                out_text += chunk
    except Exception as e:
        print(f"[{label}] 请求失败: {e}")
        return
    t1 = time.perf_counter()
    gen_time = t1 - (t0 + (ttft or 0.0))
    tps = chars / gen_time if gen_time > 0 else 0.0
    print(f"[{label}] TTFT={ttft * 1000:.0f}ms | "
          f"吐字 {chars} 字符 / {gen_time:.1f}s = {tps:.0f} 字符/s | "
          f"末尾: {out_text[-20:]!r}")


if __name__ == "__main__":
    print(f"模型: {MODEL} @ {BASE}")
    probe("小prompt(~1K tok)  ", 2000)
    probe("中prompt(~16K tok) ", 32000)
    probe("大prompt(~64K tok) ", 128000)

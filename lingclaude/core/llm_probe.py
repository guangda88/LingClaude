"""SDT-lc-002 端到端LLM探针 — 不再只检查端口

触发: 5-27 Proxy死循环事故中灵克承诺，欠债至今。
原理: 端口UP ≠ 服务可用。需要实际发completion请求验证。
"""
import json
import os
import time
import urllib.request
import urllib.error

PROXY_URL = os.environ.get("LLM_PROXY_URL", "http://127.0.0.1:8765")
PROXY_API_KEY = os.environ.get("LLM_PROXY_KEY", "")
PROBE_TIMEOUT = 30


def probe_llm_completion(model: str = "glm-4-flash", max_tokens: int = 10) -> dict:
    """端到端LLM探针：发completion请求，验证200 OK + 非空content。

    Returns:
        {"ok": True, "latency_ms": ..., "model": ...} on success
        {"ok": False, "error": ..., "latency_ms": ...} on failure
    """
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": max_tokens,
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "X-Caller": "lingclaude-probe",
        "X-Purpose": "health_check",
    }
    if PROXY_API_KEY:
        headers["X-API-Key"] = PROXY_API_KEY

    req = urllib.request.Request(
        f"{PROXY_URL}/v1/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
            data = json.loads(resp.read())
            latency_ms = (time.time() - t0) * 1000

            choices = data.get("choices", [])
            if not choices:
                return {"ok": False, "error": "empty_choices", "latency_ms": latency_ms}

            content = choices[0].get("message", {}).get("content", "")
            if not content:
                return {"ok": False, "error": "empty_content", "latency_ms": latency_ms}

            return {
                "ok": True,
                "latency_ms": round(latency_ms, 0),
                "model": data.get("model", model),
                "content_preview": content[:50],
            }
    except urllib.error.HTTPError as e:
        latency_ms = (time.time() - t0) * 1000
        return {"ok": False, "error": f"http_{e.code}", "latency_ms": latency_ms}
    except Exception as e:
        latency_ms = (time.time() - t0) * 1000
        return {"ok": False, "error": str(e)[:100], "latency_ms": latency_ms}


def probe_port(host: str = "127.0.0.1", port: int = 8765) -> bool:
    """端口可达性检查（旧方法，保留对比）。"""
    import socket
    try:
        with socket.create_connection((host, port), timeout=5):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def health_check() -> dict:
    """SDT-lc-002 完整健康检查：端口 + 端到端completion。"""
    port_ok = probe_port()
    completion = probe_llm_completion()

    return {
        "port_ok": port_ok,
        "completion_ok": completion["ok"],
        "latency_ms": completion.get("latency_ms"),
        "model": completion.get("model"),
        "error": completion.get("error"),
        "healthy": port_ok and completion["ok"],
    }


if __name__ == "__main__":
    result = health_check()
    status = "✅ HEALTHY" if result["healthy"] else "❌ UNHEALTHY"
    print(f"{status} port={'UP' if result['port_ok'] else 'DOWN'} "
          f"completion={'OK' if result['completion_ok'] else result.get('error', 'FAIL')} "
          f"latency={result.get('latency_ms', '?')}ms")

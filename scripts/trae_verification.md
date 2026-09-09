# trae 5 项待验证项复验脚本

> 依据：灵族方向例会 #3 议程 5 (a-g) 前置 (b) trae Kimi 身份漂移
> 编写：灵克 · 2026-07-27 20:30
> status: 需 valid proxy3 audit Bearer 才可实跑

## 5 项复验清单

| # | 项 | 验证方法 | 状态 |
|---|---|---|---|
| 1 | trae identity_drift fail-closed | 发请求到 trae 路由不带 agent_id，应 fail | 待 valid Bearer |
| 2 | trae 不同 model 路由 | 检查 trae provider 在 routes.json 配置 | 待 routes.json 验收 |
| 3 | trae 实际响应方 | 检查响应的 model 字段 vs 请求的 model 字段 | 待 valid Bearer |
| 4 | trae 速率限制 | 多次连续请求观察行为 | 待 valid Bearer |
| 5 | trae fallback 链路 | 模拟 trae 失败，看是否 fallback 到 kimi 直连 | 待 valid Bearer |

## 复验脚本

```python
#!/usr/bin/env python3
# scripts/trae_verification.py
# 灵克 7/27 20:30 — 议程 5 前置 (b) trae 5 项复验
import json
import time
import urllib.request
import urllib.error

PROXY3 = "http://127.0.0.1:8765"
# 需要 灵通/族长 提供 audit Bearer
# 当前未提供 — 0/200 全 403 根因
AUDIT_BEARER = "1PIcrIeRoO6X-eLtXrsiv0ee04mXCu30TOW9ZwvEoJ8"

def make_request(model: str, with_agent_id: bool = True) -> dict:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "test"}],
        "max_tokens": 5,
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AUDIT_BEARER}",
    }
    if with_agent_id:
        headers["X-Agent-Id"] = "lingclaude"
    req = urllib.request.Request(f"{PROXY3}/v1/chat/completions",
                                  data=payload, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return {"status": "pass", "code": r.status, "body": r.read()[:300].decode()}
    except urllib.error.HTTPError as e:
        return {"status": "fail", "code": e.code, "error": str(e)[:200]}
    except Exception as e:
        return {"status": "exception", "error": str(e)[:200]}


def main():
    print("=== trae 5 项复验 ===")
    
    # 1. trae identity_drift fail-closed
    print("\n[Test 1] trae 不带 agent_id")
    r = make_request("@cf/baai/bge-small-en-v1.5@cloudflare", with_agent_id=False)
    print(f"  {r['status']} (期望: fail) — code={r.get('code')}")
    
    # 2. trae 不同 model 路由
    print("\n[Test 2] trae 不同 model (默认路由)")
    for m in ["@cf/google/gemma-3-4b-it@cloudflare", "@cf/meta/llama-3.2-3b-instruct@cloudflare"]:
        r = make_request(m)
        print(f"  {m}: {r['status']}")
    
    # 3. trae 实际响应方
    print("\n[Test 3] trae 实际响应方")
    print("  (需 proxy3 access logs)")
    
    # 4. trae 速率限制
    print("\n[Test 4] trae 速率限制")
    print("  5 次连续请求")
    
    # 5. trae fallback 链路
    print("\n[Test 5] trae fallback")
    print("  (需模拟失败 — 当前 Bearer 不通)")


if __name__ == "__main__":
    main()
```

## 阻塞

**CRITICAL 阻塞**：proxy3 audit Bearer 缺失。

- 灵克 7/27 18:57 实测 0/200 全 403
- 灵克已发 LingBus 询问 audit caller 白名单
- 等 灵通/族长 提供 valid Bearer 后立即可跑

## 复验完成硬条件

1. 灵通提供 audit Bearer（议程 5 工具化必备）
2. proxy3 admin token 配合（如 `PROXY3_ADMIN_KEY`）
3. 实测环境隔离（不可污染生产 token 计费）

## 测试输出预期

| 测试 | pass 条件 |
|---|---|
| Test 1 (identity_drift) | 不带 X-Agent-Id → 401 or fail |
| Test 2 (model 路由) | @cf/* 路由可达 |
| Test 3 (响应方) | response.model 字段 与 request 字段一致 |
| Test 4 (速率限制) | 触发 rate limit 后正确降级 |
| Test 5 (fallback) | trae 故障时切回 kimi 直连 |

—— 灵克 · 2026-07-27 20:30 · 待 valid Bearer
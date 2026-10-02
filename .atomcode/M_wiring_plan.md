# M 期接线：execution_domain → llm_proxy httpx 强制点

## 勘察结论（代码实证）

- httpx 出口全项目仅 2 处：`provider_pool.py:41`（持久 AsyncClient）+
  `proxy.py:351`（peer fallback 临时 AsyncClient）→ 强制点收敛在这两处。
- `provider_pool.call` 全部失败模式折算 `ProviderResponse.status` 字符串，
  **从不 raise**（`except httpx.HTTPError` 都接住）→ 接线用同构 `net_blocked`
  状态，调用方 `proxy.py:88` 的 provider 轮换自然跳下一家（与 timeout 同构）。
- `call_stream` 失败 → `yield StreamChunk(delta="", finish_reason="error", ...)`
  → `proxy.py:181 except Exception` 兜底，流路径同构安全。
- peer fallback 走独立 `dict` 契约（`error` 键），不复用 exception。

## 接线语义（四条，全部入 docstring）

1. **未激活零行为变化**：`net_allowed()` 返回 None（段缺失/读取故障）→
   直接放行走原路径，一行不多；
2. **激活拦截同构折算**：域名不在白名单 → `call` 折算 `status="net_blocked"`
   / 流 `finish_reason="error"`，与 timeout 同构，不 raise 不炸主循环；
3. **空 allowlist = 放行（模块既定语义，fail-open）**【2026-10-02 二轮修正】：
   初稿误写「显式空 default 也拦」，与 execution_domain 核心语义冲突——
   该模块 docstring 明定「default 缺失=不限制（显式空口）」，且网络调用
   属可重试操作，按纪律走 fail-open（误伤瘫痪代价 > 放行风险）。
   「默认全禁」需求留给未来显式开关（default_deny: true），不在接线层
   私改核心语义；
4. **deny 只记 warning**（与 rate_gate/gatekeeper 域词汇一致），denied host
   只进日志不进响应体（防把内网拓扑漏给上游）。

## 家族先例锚定

- gatekeeper/domain_policies.py:18: deny 判定「只记 warning 不回显域名」
- tool_auth_policy.yaml:30: 统一为「策略规则网关」入四档 auto 档

## 边界（如实声明）

- 插件域 urllib 出口（cap_browser/cap_infer/ghidra/remote_probe）与
  cli/app 本机回环探测不在本强制点范围——M 期锚定 LLM API 流量；
  插件域治理若要铺开应走 hook_registry（noted，不本项展开）。
- 执行顺序：resolve 用 policy 热更快照 → gate 判定 → client.post。
  gate 在 resolve 之后（resolve 若 fail-open 返回 () 则 gate 静默）。

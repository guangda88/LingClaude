---
name: mcp-wrap
description: 把一个目标服务封装为符合灵元铁律的 MCP server 插片——输入=目标服务信息（名称/入口/传输/能力），输出=MCP server 脚手架 + 铁律 manifest.agent.json + 测试骨架 + 入册五步清单。触发词：封装 MCP、MCP 化、git-MCP、XX-MCP、把 XX 做成 MCP、新成员 MCP 接入、Phase 2 插片。适用于 lingxi git-MCP、12 子 MCP 化、8 对外工程探针化等一切 AgentPlugin 生产场景。
---

# mcp-wrap：灵元铁律 MCP 封装模板

把任意目标服务封装为 lingclaude 可调用的 AgentPlugin 插片。**本技能的本质：
把 agent/lingxi 试验田踩过的坑固化成一次性模板，质量守卫写一次、全族复用。**

## 输入清单（开工前逐项确认，缺项记 debt 不假设）

| # | 输入 | 说明 | 缺失时的处置 |
|---|------|------|-------------|
| 1 | 服务名与缝 key | 铁律 7 域前缀：12 子用 `agent/<名>`，8 对外工程用 `proj/<名>` | 缺 → 问用户，禁止自造 ns |
| 2 | 传输形态 | mcp(stdio/http) 或直调(script/http-probe) | 缺 → 探测目标仓 package.json/入口文件 |
| 3 | 启动命令 | 实测过的完整 command（cwd/env/超时） | **必须本机实测 initialize 握手成功**，未实测记 debt（lingxi 探针超时教训：npx 冷启动 OOM→改 node 直启+--use-openssl-ca） |
| 4 | 能力清单 | 对方暴露的工具/动作列表 | 缺 → 对 MCP 服务跑 tools/list，对直调服务读文档 |
| 5 | 信任等级 | T1 全审计/T2 契约审计/T3 只观测（铁律 6） | 12 子默认 T2，基础设施默认 T1，对外工程默认 T3；偏离默认须用户确认 |
| 6 | 拔插等级 | L1 替换/L2 缺席降级/L3 缺席裸奔 | 默认 L1；L2 须额外实现降级策略（禁主干 if-else） |
| 7 | StateStore 路径 | run record 落点 | 默认 `data/agent_runs/` |

## 产出物（四件套，全部照抄 agent_lingxi 模式）

### 产出 1：MCP server 脚手架（若目标是 MCP 服务则跳过，直接用对方的）

对非 MCP 服务（直调传输），生成薄封装 server：

```python
# wrap_<name>_mcp.py —— 铁律 2 细则 5：此文件的 kernel 是"协议翻译"，零业务逻辑
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("<name>-wrap")

@mcp.tool()
def <capability>(...) -> str:
    """<能力一句话>。业务实现在目标服务，本层只做调用+错误结构化。"""
    ...  # 调目标服务，异常必须抛 MCP 错误（不让调用方猜）

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

**守卫线**：封装层禁止出现业务判断（if 状态==xx 则 yy）——那是目标服务的 kernel，
封装层抄了就是 J1 违例（变化不走接缝）。

### 产出 2：manifest.agent.json（N2/N3 守卫消费，缺字段拒收）

```json
{
  "name": "<ns>/<name>",
  "target": "<target>",
  "version": "0.1.0",
  "trust_level": "<T1|T2|T3>",
  "plug_level": "<L1|L2|L3>",
  "transport": {"kind": "<mcp|direct>", "mode": "<stdio|http|script>",
                "command": ["实测过的", "完整命令"], "cwd": "<实测目录>",
                "startup_timeout_s": 30, "call_timeout_s": 120},
  "stop_layer": {"kernel": "<一句话：谁在干活>", "seams": ["<子缝>"],
                 "implementations": 1},
  "capabilities": ["<能力1>", "<能力2>"],
  "health_probe": {"interval_s": 300, "timeout_s": 5,
                   "method": "<mcp_list_tools|script_exit|http_200>", "absent_after": 2},
  "state_record": "agent_run:<name>"
}
```

**字段校验（入册前自查，守卫会再查一遍）**：
- name 必带 `/`（N3）；trust/plug 双声明必填（N2）；stop_layer 三要素齐（铁律 2 细则 5）；
- command 必须与代码里 `_server_cmd()` 一致（双源漂移防回归，lingxi 教训 197 行测试）。

### 产出 3：plugin.py 骨架（AgentSeam 协议 + J4 全程 record 化）

照抄 `lingclaude/plugins/agents/agent_lingxi/plugin.py` 结构，**四个必保语义**：
1. **run() 成功锚定响应体**，非进程存活（`exit 0 ≠ 调用成功`，lingxi 'false' 命令教训）；
2. **失败也入账**：run/abort/status 每次状态变迁写 agent_run record（J4），
   timeout/failed/aborted 是 record 合法终态，不是异常路径；
3. **探针判定锚定协议响应**：MCP 服务看 id=0 的 result 字段（error 响应不得判活），
   直调服务看 exit code + 约定输出；连续 absent_after 次失败 → status 返回 absent；
4. **register() 入口**：`registry.register(SeamType.AGENT, "<裸名>", Plugin())`
   （registry 层用裸名，域前缀归 manifest 与守卫管——双层语义勿混淆）。

### 产出 4：测试骨架（离线 hermetic，CI 必过）

必含六个测试（照 `tests/agents/test_agent_lingxi.py` + `test_bus_bridge.py` 模式）：

| 测试 | 守卫锚点 | 要点 |
|------|---------|------|
| test_n2_trust_and_plug_level_declared | N2 | manifest 双声明 |
| test_n3_namespaced_seam_key | N3 | 缝 key 域前缀 |
| test_stop_layer_three_elements | 铁律 2 细则 5 | 三要素 |
| test_run_failure_recorded | J4 | 破坏传输命令→run 必记 failed（断言 record 存在） |
| test_absent_after_consecutive_failures | 候选铁律 8 | 连续 N 次探针失败→absent |
| test_probe_rejects_error_response | J5 行为级 | error 响应不得判活 |

**hermetic 红线**：测试不得依赖真实目标进程（subprocess stub / `["bash","-c","printf ..."]`
注入固定响应）；需要真实端点的验证单独标 `@pytest.mark.live`，不进 CI。
（lingxi 教训：探针测试直连真实仓导致 CI 超时 180s。）

## 入册五步清单（每步的产出即证据）

```
① schema 校验：python3 -c "逐字段断言 manifest"（N2/N3/细则5 全过）→ PASS 输出
② 守卫全量：pytest tests/agents/test_agent_<name>.py tests/test_iron_law_guards.py → 全绿
③ record 入册：
   - agent_registry/<ns>/<name>（trust/plug/transport/state=registered）
   - arch_law_revision 一条 event=enacted（引用 manifest 路径+测试数）
④ CI：tests/agents/ 已被 arch-guards job 通配覆盖（无需改 ci.yml，确认即可）
⑤ 广播：LingBus channel=system，topic=lingyuan-agent-<name>-registered
```

## 踩坑清单（全族共享，违反即重蹈）

1. **npx 冷启动 OOM**（lingxi 根因 2）：启动命令禁 npx，用 node/python 直启；
2. **ulimit -v 下根证书包崩溃**（lingxi 根因 1）：node 加 `--use-openssl-ca`；
3. **MCP -32603**（lingxi 根因 3）：initialize 载荷 clientInfo 必带 version；
4. **exit 0 ≠ 成功**：成功语义锚定响应体；
5. **registry 裸名 vs manifest 域前缀**：双层勿混（`agent/agent/lingxi` 是错例）；
6. **未实测 command 就入册**：必留探针 debt（lingxi 的 due 09-24 那笔）；
7. **并行修改**：动工前 poll bus + 查 work_claim，改非自己名下文件先认领。

## 反模式（本技能不适用的情况）

- 目标是"给 lc 自己加工具"→ 走 core 的 Provider 协议，不是 AgentPlugin；
- 目标是"纯数据查询无动作"→ 考虑直接挂 SeamType.RESOURCE 探针，不值得 Agent 缝；
- 目标服务无任何可调用入口（纯人工流程）→ 记 org_member 依赖，不做插片。

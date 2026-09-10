# ROUTING_TOPOLOGY_v1 — lingclaude ↔ lingcode ↔ provider 接线真相

> 2026-09-10 灵克产出 · 目的：取代摘要里关于 proxy3 的 5 次错诊断根因
> 状态：**已验真**（grep + 源码通读），不是启发式猜测

## 一、配置源不在 lingclaude 自己的 config.yaml

lingclaude 真正的 provider 路由表在 **lingcode 仓库的 config.json**：

```
/home/ai/lingcode/config.json   # task_router.py:27 CONFIG_PATH
```

`lingclaude/config.yaml` 里的 `model.provider/model/api_key/base_url` 是**顶层模板**，
仅在没有 lingcode config 时兜底。**生产路径走 lingcode config**。

lingclaude 仓库和 lingcode 仓库是两个独立的 Go+Python 仓库，但通过 TaskRouter
耦合——这是 F12 重构后的既定拓扑。

## 二、TaskRouter 接线（lingclaude/model/task_router.py）

### 2.1 配置加载

```python
# task_router.py:27
CONFIG_PATH = Path("/home/ai/lingcode/config.json")
```

`TaskRouter._load_config()` 从 `routing.providers` 读 14 家 provider：

```
local / glm / minimax / volc_coding_plan / volcengine / nvidia /
hunyuan / agnes / deepseek / siliconflow / siliconflow_disabled /
kimi / zai / zhipu / dashscope / openrouter
```

### 2.2 TaskType → route_key 映射（10 种）

```python
TASK_TYPE_TO_ROUTE = {
    CODE_GENERATION/ANALYSIS/REFACTORING/DEBUGGING/TESTING → "coding",
    ANALYSIS/OPTIMIZATION                                   → "chinese_reasoning",
    DOCUMENTATION                                            → "english_general",
    SEARCH/OTHER                                             → "fast_response",
}
```

### 2.3 api_key 兜底链（F12c 设计）

```
provider.api_key (config 留空) → _PROVIDER_ENV_KEY_MAP 反查 env 名 → os.environ[env_name]
```

映射表（task_router.py:72-88）：

| provider 名 | env 变量 |
|---|---|
| volc_coding_plan / volcengine | VOLC_CODING_API_KEY |
| nvidia | NVIDIA_NIM_API_KEY |
| minimax | MINIMAX_API_KEY |
| hunyuan | HUNYUAN_API_KEY |
| agnes | AGNES_ENTERPRISE_API_KEY |
| deepseek | DEEPSEEK_API_KEY |
| siliconflow[_disabled] | SILICONFLOW_API_KEY |
| kimi | KIMI_API_KEY |
| zai | ZAI_API_KEY |
| zhipu / **glm** | ZHIPU_API_KEY |
| dashscope | DASHSCOPE_API_KEY |
| openrouter | OPENROUTER_API_KEY |

**注意**：glm provider 的 env 兜底是 `ZHIPU_API_KEY`（不是 `GLM_API_KEY`）。

### 2.4 选路：严格优先级（task_router.py:198）

```python
for pos in range(len(models)):
    # F12b:云端 provider 缺 api_key → 跳过选下一候选
    if not pinfo.api_key and not _is_local_base(pinfo.base_url):
        continue
    if slot and not slot.is_available:  # F12j:熔断/限流跳过
        continue
    return ModelConfig(...)  # 首位可用候选即选中，不 round-robin
```

故障切换由 (a) 缺 key 跳过 + (b) slot.is_available 熔断承担，**不是**轮询。

## 三、proxy3 的真实身份

proxy3 是**第三个独立仓** `/home/ai/llm-proxy/proxy3_py/main.py`（不是 lingcode 的子模块），

```
进程：python3 -u /home/ai/llm-proxy/proxy3_py/main.py 8765  (PID 5871, 18:44 起)
监听：0.0.0.0:8765
curl 实测：/v1/chat/completions HTTP 200 响应 glm-5.3-flash 请求
          /v1/models HTTP 200 列出 siliconflow/xingchen_maas/sambanova 等模型
路由源：proxy3_py/routes.json (749 条) + fallback_chains.json (43 个 fallback 链)
```

### 3.1 历史误解：proxy3 ≠ lingcode 的 peer_proxy_url

lingcode config.json **历史版本**（所有 .bak）有 `routing.peer_proxy_url: http://127.0.0.1:8765`，
**当前 config.json 没有这个字段**，lingcode 仓内 `grep -rn "8765|peer_proxy" /home/ai/lingcode/`
**0 引用**（只在 .bak 配置文件里残留）。换言之：8765 这个端口上的服务叫 proxy3，
但 lingcode 自身**从来没真正调用过它**——peer_proxy_url 是预留字段或文档意图。

### 3.2 lingclaude 引用 proxy3 的**两个**位置

| 位置 | 用途 | 主路径？ |
|---|---|---|
| `lingclaude/api.py:619` | `LINGCLAUDE_PROXY_URL` env → 默认 8765 | ❌ 健康检查 |
| `lingclaude/core/llm_probe.py:12` | `LLM_PROXY_URL` env → 默认 8765 | ❌ 探针 |

### 3.3 主路径（实锤）

lingclaude OpenAIProvider._do_stream `openai_provider.py:139`：
```python
base = cfg.base_url or "https://api.openai.com/v1"
parsed = urlparse(base)
host = parsed.hostname
# ... http.client.HTTPSConnection(host, port) 直连
```

`cfg.base_url` 来自 task_router resolve → lingcode config 的 glm provider → `https://open.bigmodel.cn/api/coding/paas/v4`。
**没有任何代码路径把 base_url 改写成 8765**。

完整链路：
```
用户 → lingclaude QueryEngine
       → TaskRouter.resolve → ModelConfig(base_url=open.bigmodel.cn, api_key=ZHIPU_API_KEY env)
       → OpenAIProvider._do_stream
       → HTTPS POST → open.bigmodel.cn/api/coding/paas/v4 (直连智谱)
                                                                  ✗ 不经 proxy3
```

### 3.4 proxy3 在 P0.1 / P0.x 故障中的作用

| 场景 | 是否与 proxy3 相关 |
|---|---|
| max_tokens 4096 烧光 → text_deltas=0 | ❌ 无关，发生在智谱侧 |
| ZHIPU_API_KEY 401 | ❌ 无关，lingclaude 直发智谱 |
| LingBus 蜂群通信 | ❌ lingmessage 独立 DB |
| lingclaude 健康检查 | ✅ 探针走 8765 |

proxy3 宕机时 lingclaude 主路径仍工作。`lingclaude/api.py:616` 的注释
"proxy3(8765) 是模型面单点——宕机时 cloud provider 全断" **与代码事实不符**——
是 R5 蜂群评估的历史叙事，不是 lingclaude 实测拓扑。

## 四、P0.1 max_tokens 真路径（校正摘要自批）

**摘要原错诊断**（5 次）：

1. "API Key 空字符串 → 401" ❌ 错。空是**有意设计**，key 从 env 兜底
2. "改 base_url 到 zai" ❌ 错。lingclaude 主路径不直连 8765
3. "改 ZHIPU_API_KEY" ❌ 错。env 名是对的，但触发的不是 lingclaude 直读
4. "改 base_url 后还 401" ❌ 错。测的是直连，不影响真实路径
5. "proxy3 宕机 = P0.1 根因" ❌ 错。proxy3 与 P0.1 完全解耦

**P0.1 真路径**：

```
task_router.resolve(max_tokens=4096)        ← 默认值埋在 :173
  → ModelConfig(max_tokens=4096, model=glm-5.3-flash, base_url=open.bigmodel.cn)
  → OpenAIProvider.chat()
  → 直连 glm-5.3-flash (推理模型)
  → 4096 token 预算被思考链烧光
  → 流式响应 text_deltas=0 + output_usage ≈ 4096
  → 上层显示"卡住/无响应"
```

**修复要点**：
- `config.yaml` `model.max_tokens: 4096 → 16384` ✅ 已修（cc 复核：`tool_executor._resolve_model_config` 实际读 `cfg.max_tokens=16384`，生效）
- N5a 守卫 ✅ 已落地（commit dfaab65）：`lingclaude/cli/n5_token_guard.py`，挂点 `_record_long_task_metrics` 收尾；text_deltas==0 AND turn_output≥0.95*max_tokens → WARNING，连续 2 次 ERROR + LingBus。**定位已修订**（max_tokens 修复后）：不再是"防 4096 烧光"，而是"防未知故障下的静默空转"（P1）
- N5b 流内停滞 watchdog ⏳：`_record_long_task_metrics` 收尾点够不到**流中途挂死**（proxy 卡死/429 重试黑洞/网络丢包 → `next()` 永不返回 → 循环内 interrupt 检查也不执行）→ 需要旁路线程监视事件间隙，设计见 §4.2
- **不需要改 proxy3，不需要改 base_url，不需要改 ZHIPU_API_KEY**

### 4.1 max_tokens 全链路死代码核查（cc 复核表，2026-09-10）

| 检查项 | 结果 |
|---|---|
| `config.yaml max_tokens` | ✅ 16384（生效源） |
| `tool_executor._resolve_model_config` 使用 | ✅ 读 cfg.max_tokens=16384 |
| `task_router.resolve()` 默认 4096 | ⚠️ 死代码——routed_config.max_tokens 不被读 |
| `core/config.py:81,189` 默认 4096 | ⚠️ 死代码——所有调用方都传 max_tokens |
| `lingcode/config.json:14` 默认 4096 | ⚠️ 只影响 lingcode 自身，不影响 lingclaude |

### 4.2 N5b 设计纪要（流内停滞 watchdog）

三个零事件窗口语义不同（实测依据：单轮测试套件 756s、glm-5.3-flash 推理期无 delta）：

| 窗口 | 依据 | 策略 |
|---|---|---|
| 首事件前（推理思考期） | P0.1 案例 4096 全程 0 delta | 120s → WARNING |
| 工具执行期 | model_call.py:518-530 之间生成器零 yield | 跳过计时（工具超时归 tool_executor） |
| 流间歇期 | 正常间隔毫秒级 | 60s WARNING / 300s ERROR + LingBus |

实现形态：daemon 旁路线程读共享 `last_event_at/last_event_type`，**只告警不打断**（守卫永不 raise 原则；卡死流的打断仍归用户 Ctrl+C / pump 中断）。挂点：主循环与 `_single_turn` 的 try/finally 对称注入。

## 五、给下会话 / 给 lingclaude 的纪律

1. **改任何 provider/base_url/key 前先 grep**：`grep -rnE "8765|base_url|ZHIPU_API_KEY|ZAI" lingclaude/` 看实际接线
2. **proxy3 是观测层**：健康检查会报红，但 P0/P1 类业务故障**几乎与 proxy3 无关**
3. **provider 表在 lingcode config.json**：要新增/调整 provider，改 lingcode 不改 lingclaude
4. **api_key 走 env 兜底**：要换 key 就 `export ZHIPU_API_KEY=...`（或写 ~/.ling_keys.env 由 gen_env.py 管）
5. **max_tokens 真凶是模型 + 参数组合**：诊断长会话卡死，先看 model 是不是推理模型（glm-5.3-flash / o1 类），再看 max_tokens

## 六、附录：相关源文件清单

| 文件 | 关键内容 |
|---|---|
| `lingclaude/model/task_router.py` | 14 家 provider 路由 + env 兜底 + 严格优先级选路 |
| `lingclaude/model/factory.py:34` | `OpenAIProvider(cfg)` 工厂 |
| `lingclaude/model/openai_provider.py:139` | `cfg.base_url or https://api.openai.com/v1` 直连 |
| `lingclaude/core/query_engine.py:501-573` | `find_provider_by_model` 按名反查 provider |
| `lingclaude/core/model_call.py:211` | `get_provider_name(api_key, base_url)` 反查 |
| `lingclaude/api.py:619-645` | `LINGCLAUDE_PROXY_URL` env 健康检查路径 |
| `lingclaude/core/llm_probe.py:12-39` | `LLM_PROXY_URL` env 探针路径 |
| `/home/ai/lingcode/config.json` | **真路由源**（routing.providers / routing.task_routes） |
| `/home/ai/llm-proxy/proxy3_py/main.py` | proxy3 服务（独立仓，8765） |
| `/home/ai/llm-proxy/proxy3_py/routes.json` | proxy3 路由表（749 条） |
| `/home/ai/llm-proxy/proxy3_py/fallback_chains.json` | proxy3 fallback 链（43 个模型） |

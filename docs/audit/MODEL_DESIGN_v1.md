# MODEL_DESIGN_v1 — lingclaude 切换模型方案（参考三工具对比）

> 2026-09-11 监督者产出 · 用户要求"参考 crush/atomcode/opencode 重新设计 lingclaude 切换模型方案"
> 状态：**设计文档**，**不入代码**——给 lingclaude 未来重构参考

## 一、为什么需要重新设计

**本次故障链实证**：

```
/model deepseek-v4-flash → switch_model → find_provider_by_model → task_router
  → 选 lingcode volcengine → 直连 ark.cn-beijing.volces.com/api/plan/v3
  → 401 (端点弃用/路径错)
```

**真凭据 lingclaude 当前限制**（真读 `query_engine.py:484-512` + `task_router.py:277-285`）：

- `switch_model(model_name)`：**单参数**，只接裸 model name
- `find_provider_by_model(target)`：**遍历 lingcode provider 反查**——必须 model name 在 lingcode 的 `models` 列表里
- `config.yaml.model`：**单值**，可写 `volc_coding_plan/deepseek-v4-flash` 但**tool_executor 不读 cfg.base_url**（之前已查证）

## 二、参考三工具的 model 引用格式

| 工具 | CLI 格式 | 切换机制 | 内部解析 |
|---|---|---|---|
| **Crush** | `provider/model` | `crush models` 列出，`update-providers` 同步 | `models[].id = provider/model` |
| **OpenCode** | `-m, --model provider/model` | 顶级 flag，运行时切换 | 显式 provider/model 二段 |
| **AtomCode** | `--provider X --model Y` | **两个独立 flag**，解耦 | provider 和 model 完全分离 |
| **lingclaude（当前）** | `/model <name>` | 通过 task_router 反查 | **隐式 provider 推断**——多工具会失败 |

**真凭据**（实测 `crush models` 输出）：
```
agentrouter/claude-haiku-4-5-20251001
agentrouter/deepseek-v3.2
agnes-enterprise/agnes-2.5-flash
```

**真凭据**（实测 `opencode --help`）：
```
-m, --model  model to use in the format of provider/model    [string]
opencode providers   manage AI providers and credentials
```

**真凭据**（实测 `atomcode --help`）：
```
--provider <PROVIDER>   指定使用的 Provider（覆盖配置默认值）
--model <MODEL>         指定使用的模型（覆盖配置中的 Provider 模型）
```

## 三、lingclaude 推荐方案

### 3.1 支持4种 model 引用格式

| 格式 | 例子 | 解析 | 优先级 |
|---|---|---|---|
| **provider/model** | `volcengine/deepseek-v4-flash` | 显式 split | 🟢 **推荐**（对齐 OpenCode/Crush）|
| **provider@model** | `volcengine@deepseek-v4-flash` | 显式 split | 🟡 兼容（对齐 Crush 路由表 key）|
| **裸 model** | `deepseek-v4-flash` | task_router 反查 | 🟢 兼容（保留现有）|
| **CLI 双 flag** | `--provider volcengine --model deepseek-v4-flash` | 显式两参数 | 🟡 可选（对齐 AtomCode）|

### 3.2 改动点

#### A. `query_engine.py:switch_model` 支持 prefix 解析

```python
# 现状（query_engine.py:484-512）
def switch_model(self, model_name: str) -> Result[str]:
    target = model_name.strip()
    _pname, _pinfo = self._task_router.find_provider_by_model(target)
    # ... 只走 task_router 反查

# 推荐改动
def switch_model(
    self, 
    model_name: str, 
    provider_name: str | None = None,  # ← 新增可选参数
) -> Result[str]:
    target = model_name.strip()
    
    # 新增：识别 provider/model 或 provider@model 格式
    if provider_name is None and ("/" in target or "@" in target):
        sep = "@" if "@" in target else "/"
        provider_name, model_only = target.split(sep, 1)
        target = model_only.strip()
    
    # 优先用 provider_name 显式指定
    if provider_name:
        pinfo = self._task_router._providers.get(provider_name)
        if pinfo is None:
            return Result.fail(f"provider not found: {provider_name}", code="BAD_PROVIDER")
        new_cfg = ModelConfig(
            model=target,
            api_key=pinfo.api_key,
            base_url=pinfo.base_url,
            ...
        )
    else:
        # 现有逻辑：task_router 反查
        _pname, _pinfo = self._task_router.find_provider_by_model(target)
        ...
```

#### B. `app.py:run_parser` 新增 `--provider` flag（参考 AtomCode）

```python
# app.py:run_parser 当前
run_parser.add_argument("--model", "-m", help="Override model name")

# 推荐新增
run_parser.add_argument("--provider", help="Provider override (e.g. volcengine)")
```

`_cmd_run` 调 `engine.switch_model(args.model, provider_name=args.provider)`。

#### C. `pin_model` 接受 provider_name

```python
# query_engine.py:539 pin_model 当前
def pin_model(self, model_name: str, ttl_seconds: int = 0) -> Result[str]:
    # 只接 model_name
    
# 推荐
def pin_model(
    self, 
    model_name: str, 
    ttl_seconds: int = 0,
    provider_name: str | None = None,  # ← 新增
) -> Result[str]:
    ...
```

#### D. `tool_executor._resolve_model_config` 优先 cfg.base_url

```python
# tool_executor.py:332 当前
routed_config, route_key = self._engine._task_router.resolve(prompt)
target_api_key = routed_config.api_key
target_base_url = routed_config.base_url

# 推荐改动：在头部加守卫
if self._engine.is_model_pinned():
    # 已钉住 → 直接用 cfg
    ...
elif self._engine._model_config and (
    "proxy3" in self._engine._model_config.base_url
    or "127.0.0.1:8765" in self._engine._model_config.base_url
):
    # cfg 显式指定 proxy3 → 跳过 task_router
    cfg = self._engine._model_config
    return cfg, "cfg_direct"
```

### 3.3 config.yaml 推荐格式

```yaml
model:
  api_key: ${ENV_VAR}
  provider: volcengine     # ← 新增显式 provider
  model: deepseek-v4-flash
  base_url: https://ark.cn-beijing.volces.com/api/coding/v3   # 可选，provider 推
```

## 四、设计原则

### 4.1 显式优于隐式

lingclaude 当前 model 引用是**隐式**（task_router 反查），三工具都是**显式**（直接 provider/model 字符串）。

**显式的好处**：
- 用户一眼看出路由
- 不用读 lingcode config
- 不依赖 task_router 状态

### 4.2 多格式兼容

保留裸 model 兼容（向后兼容），新增显式 provider/model（推荐）。

### 4.3 cfg.base_url 优先 task_router

**这次故障的根因**：tool_executor 用 task_router 不用 cfg.base_url——**用户改了 cfg 但不生效**。

**三工具的做法**：都是**直接用 cfg.base_url + provider 选 model**——不走隐式反查。

### 4.4 fallback 链修复而非删除

之前 lingclaude "停止降级链"——**根因是配置错误导致 fallback 走错**，不是 fallback 设计有问题。

**正确做法**：
- fallback 链**保留**（保证鲁棒性）
- 但**修正链内容**（首选火山方舟 → 次选 proxy3 → 末选智谱）
- 不该**全删**（牺牲鲁棒性）

## 五、文档化建议

### 5.1 加 `MODEL_DESIGN_v1.md`（本文件已写）

### 5.2 更新 `docs/audit/ROUTING_TOPOLOGY_v1.md` §二

加新章节："§2.5 model 引用格式演进"，记录从隐式到显式的设计变迁。

### 5.3 加 lingclaude 自身的 docs/CLI_MODEL_SWITCHING.md

描述 CLI 用法：
```bash
# 隐式（向后兼容）
/model deepseek-v4-flash

# 显式 provider/model（推荐）
/model volcengine/deepseek-v4-flash
/model volcengine@deepseek-v4-flash

# CLI flag（对齐 AtomCode）
lingclaude run -i --provider volcengine --model deepseek-v4-flash
```

## 六、不动代码的实现时机

**触发条件**：
- lingclaude P4.x 或 P5.x 重构阶段
- lingcode task_routes 恢复 fallback 链后
- 用户在 lingclaude 内部文档列出"修 lingclaude _resolve_model_config 不读 cfg.base_url"

**本次不动**：
- lingclaude 监督纪律：不动工作区业务代码
- 用户已明确"先保证 lingclaude 可用"+本次"设计文档不入代码"

## 七、对比表总结

| 能力 | Crush | OpenCode | AtomCode | lingclaude（推荐） |
|---|---|---|---|---|
| 裸 model | ✅ | ✅ | ✅ | ✅（保留）|
| provider/model | ✅ | ✅ | ❌ | ✅（新增）|
| provider@model | ✅ | ❌ | ❌ | ✅（新增，兼容 Crush）|
| `--provider` flag | ❌ | ❌ | ✅ | ✅（新增，可选）|
| 列表命令 | `models` | `models` | ❌ | 建议新增 |
| cfg 直接 base_url | ✅ | ✅ | ✅ | ❌（真 bug）|
| 多 provider 列表 | ✅ | ✅ | ✅ | ✅（lingcode）|
| task_router fallback | ❌ | ❌ | ❌ | ✅（保留+修正）|

## 八、给 lingclaude 的实施清单（建议）

| 优先级 | 改动 | 文件 | 行数估算 |
|---|---|---|---|
| P0 | `switch_model` 接受 provider_name + 解析 `provider/model` | `query_engine.py` | +15 |
| P0 | `_resolve_model_config` 优先 cfg.base_url | `tool_executor.py` | +8 |
| P1 | `pin_model` 接受 provider_name | `query_engine.py` | +10 |
| P1 | `--provider` CLI flag | `app.py` | +2 |
| P2 | `lingclaude models` 列表命令 | `app.py` | +20 |
| P2 | 恢复 lingcode task_routes fallback 链（修正内容）| `lingcode/config.json` | — |
| P3 | 文档：CLI_MODEL_SWITCHING.md | `docs/CLI_MODEL_SWITCHING.md` | 新文件 |

**总计 ~ 55 行改动 + 1 新文件**——相比 lingclaude 工作区数千行规模，**改动极小**。

## 九、保留的纪律

- ✅ 真读三工具代码/CLI（不凭印象）
- ✅ 保留 lingclaude 现有裸 model 兼容（向后兼容）
- ✅ 不动 lingclaude 工作区代码（本次）
- ✅ 设计文档有真凭据（file:line + 命令实测）
- ✅ 改动量小（55 行级别）
- ✅ 解决本次故障的根因（cfg.base_url 不生效 + provider 隐式推断失败）

## 十、版本

| 版本 | 作者 | 时间 | 内容 |
|---|---|---|---|
| v1 | claudecode（监督者）| 2026-09-11 | 三工具对比 + 4 格式推荐 + 实施清单 |
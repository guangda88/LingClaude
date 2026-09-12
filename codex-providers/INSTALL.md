# Codex 三套餐接入（Volc / MiniMax / Kimi）

## 现状与结论

| 套餐 | 状态 |
|------|------|
| Volc Coding Plan 个人版 | ✅ 已接入（`volcengine-coding-plan` provider，含 `minimax-m3`/`kimi-k3`） |
| MiniMax 直连 | ⬜ 需加 `[model_providers.minimax]`，模型名 `MiniMax-M3` |
| Kimi 直连 | ⬜ 需加 `[model_providers.kimi]`，模型名 `k3`/`k3-256k` |

三个 API Key 均已存在于当前环境变量，无需再申请：
`VOLC_CODING_API_KEY` / `MINIMAX_API_KEY` / `KIMI_API_KEY`。

## 关键区别（易混点）

同名模型有两套命名，走不同 provider、不同端点：

| 底层模型 | Volc Coding Plan 内（小写） | 官方直连（大写/官方） |
|----------|---------------------------|----------------------|
| MiniMax M3 | `minimax-m3` → `ark.cn-beijing.../api/coding/v3` | `MiniMax-M3` → `api.minimax.cn/v1` |
| Kimi | `kimi-k3` / `kimi-k2.7-code` → 同上 | `k3` / `k3-256k` → `api.kimi.com/coding/v1` |

## 手动接入步骤

### 1. 追加两个 provider 到 `~/.codex/config.toml`

```toml
[model_providers.minimax]
name = "MiniMax"
base_url = "https://api.minimax.cn/v1"
env_key = "MINIMAX_API_KEY"
wire_api = "responses"

[model_providers.kimi]
name = "Kimi"
base_url = "https://api.kimi.com/coding/v1"
env_key = "KIMI_API_KEY"
wire_api = "responses"
```

### 2.（可选）模型目录合并

把本目录 `catalog-additions.json` 的 3 个对象（`MiniMax-M3`、`k3`、`k3-256k`）
合并进 `~/.codex/models.json` 的 `"models"` 数组，使 `/model` 选择器正确显示。

### 3. 启动

```bash
# MiniMax 直连
export MINIMAX_API_KEY=你的key
codex -c 'model_provider="minimax"' -c 'model="MiniMax-M3"'

# Kimi 直连
export KIMI_API_KEY=你的key
codex -c 'model_provider="kimi"' -c 'model="k3-256k"'

# Volc（已有）
export VOLC_CODING_API_KEY=你的key
codex -p volc -m deepseek-v4-pro
```

### 沙箱内免改 ~/.codex 的临时用法

`~/.codex` 在本环境只读。可用 `CODEX_HOME` 重定向到本目录：

```bash
export CODEX_HOME=/home/ai/lingclaude/codex-providers/home
mkdir -p "$CODEX_HOME"
cp /home/ai/lingclaude/codex-providers/config.toml "$CODEX_HOME/config.toml"
cp ~/.codex/models.json "$CODEX_HOME/models.json"
codex -c 'model_provider="kimi"' -c 'model="k3"'
```

### 一键调度

```bash
bash /home/ai/lingclaude/codex-providers/codex-any kimi "写个快排"
bash /home/ai/lingclaude/codex-providers/codex-any mm   "写个快排"
bash /home/ai/lingclaude/codex-providers/codex-any volc "写个快排"
```

## 已验证

- 三 provider 均能在 Codex 0.153.4 下 `config parse ok`，`wire_api=responses` 识别正确。
- 限制：`~/.codex` 只读 + 沙箱 `network disabled`，致 `api.minimax.cn` / `api.kimi.com`
  在沙箱内不可达（`codex doctor` reachability FAIL）。真实调用需在宿主机可写+可联网环境执行。

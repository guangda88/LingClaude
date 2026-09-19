# 外部 Agent 网关 · 套餐模型/Provider 速查表

> 配套 `proj/agent-gateway` 薄壳（`lingclaude/plugins/agents/proj_agent_gateway/`）。
> 用途：配额耗尽时，`agent_invoke(agent=..., model=..., provider=..., profile=...)` 换模型/换计费通道。
> 套餐模型清单来源：`/home/ai/lingcode/config.json` → `routing.providers`（lc 运行时权威源）。
> 更新日期：2026-09-19。

## 各家 CLI 选模型/通道形态（薄壳 `QUOTA_ARGV` + `PROFILE_ARGV` 表固化）

| agent key | bin | 选模型旗 | 选 provider 旗 | 套餐 profile 旗 | 薄壳透传形态 | 备注 |
|-----------|-----|---------|---------------|----------------|-------------|------|
| `cc` | claude | `--model <M>` | （无独立旗） | — | 只透传 model | **默认 M3**（`DEFAULT_MODEL` 表，未显式指定时自动走 M3，避按量 Anthropic 配额墙）；provider 走 key 套餐内，cc 只认自家 key 对应模型族 |
| `codex` | codex | `-m <M>` | （`-c model_providers=...`） | **`-p <profile>`** | 透传 model + profile | **`-p <profile>` 切整套套餐**（model+provider+reasoning_effort 打包在 `~/.codex/<profile>.config.toml`）；可用 profile：`minimax`/`minmax`/`kimi`/`volc`/`vol`/`voc`（2026-09-19 实测 `codex -p minimax` PONG 通） |
| `crush` | crush | `-m <provider/model>` | （编进 model 串） | — | model 串可含 provider 前缀 | `provider/model` 消歧同名模型 |
| `opencode` | opencode | `-m <provider/model>` | （编进 model 串） | — | 同 crush | `--variant high` 可选 reasoning 档位 |
| `ac` | atomcode | `--model <M>` | `--provider <P>` | — | 双旗最灵活 | 可整体换计费通道，配额墙最优出路 |

## 可指定的套餐模型清单（lc 权威源）

按 provider 分组（`/home/ai/lingcode/config.json` providers）：

| provider | 可用模型 | 说明 |
|----------|---------|------|
| `minimax` | `MiniMax-M3`, `MiniMax-M2.7` | 套餐 |
| `volcengine` | `Doubao-Seed-2.0-lite`, `Kimi-K2.7-Code`, `MiniMax-M3`, `Doubao-Seed-Evolving`, `Kimi-K3`, `Doubao-Seed-2.1-turbo`, `DeepSeek-V4-Flash`, `GLM-5.3`, `GLM-5.3-Flash`, `DeepSeek-V4-Pro`, `Kimi-K2.8-Preview` | 套餐（方舟） |
| `kimi` | `k3`, `k3-256k`, `kimi-for-coding`, `kimi-for-coding-highspeed` | 套餐 |
| `mimo` | `mimo-v2.5-pro`, `mimo-v2.5`, `mimo-v2.5-asr`, `mimo-v2.5-tts-voiceclone`, `mimo-v2.5-tts-voicedesign`, `mimo-v2.5-tts` | 套餐（小米） |
| `agnes` | `agnes-3.0-flash` | 套餐 |
| `deepseek` | `deepseek-flash` | 按量（尾位兜底，V4.1 Flash） |
| `atomgit` | `glm-5.3-flash@atomgit`, `deepseek-v4-flash@atomgit` | 免费池 |
| `waterfall` | `waterfall` | 本地（当前不可用，不列降级链） |

## 配额耗尽调用示例

```python
# cc：未显式指定 model 时自动走 M3（DEFAULT_MODEL，避按量 Anthropic 配额墙）
agent_invoke(agent="cc", prompt="…")                       # 等价于 --model M3
agent_invoke(agent="cc", prompt="…", model="其他 cc 套餐内模型")  # 显式覆盖

# codex 切整套套餐（-p profile，实测 minimax PONG 通）：
agent_invoke(agent="codex", prompt="…", profile="minimax")  # → codex exec -p minimax "…"
agent_invoke(agent="codex", prompt="…", profile="volc")     # → -p volc（volcengine-coding-plan）

# 最灵活（ac 双旗）：换 volcengine 套餐通道，绕开当前 agent 默认 key 的配额墙
agent_invoke(agent="ac", prompt="…", provider="volcengine", model="GLM-5.3-Flash")

# crush / opencode：provider/model 串消歧
agent_invoke(agent="crush", prompt="…", model="volcengine/DeepSeek-V4-Pro")

# 多 agent 聚合 + 套餐指定（对全部 agents 透传同一 model/provider，各按 DEFAULT_MODEL 兜底）
agent_batch(agents=["cc", "codex"], prompt="…", profile="minimax")  # codex 走 -p minimax，cc 走 M3
```

## 注意（2026-09-19 实测）

- **cc（claude）默认 M3**：`DEFAULT_MODEL["cc"]="M3"`，未显式指定 model 时自动 `--model M3`（用户 2026-09-19 已将 cc 模型切到 M3，绕开按量 Anthropic 配额墙）。但 cc 的 key 套餐内仍需有 M3，否则报 `unrecognized_model`——指定 lc 套餐模型（如 `glm-5.3-flash`）cc 不认。
- **codex `-p <profile>` 切整套套餐**（2026-09-19 用户确认可用）：`-p` 指向 `~/.codex/<profile>.config.toml`，一次带出 model+provider+reasoning_effort。当前 7 个 profile 文件：`minimax`(M3/low)、`minmax`(M3/high)、`kimi`(k3-256k/high)、`volc`(volcengine-coding-plan)、`voc`/`vol`(deepseek-v4-pro/low)。薄壳 `PROFILE_ARGV["codex"]=["-p","{profile}"]` 透传。
- 各 agent 的模型名是否在该 agent 的 key 里可用，取决于该 agent 自身的 key 布局，**不等于** lc 的 `config.json` providers 清单能直接套用。本表是"lc 侧可见的套餐模型清单"，给薄壳透传时的取值参考；实际能否被某 agent 消费，以该 agent key 套餐为准。

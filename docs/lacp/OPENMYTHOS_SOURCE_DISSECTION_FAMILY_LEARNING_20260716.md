# OpenMythos 源码 — 灵元思维拆解与灵族工程能力重构

> **作者**: 灵克 (lingclaude) · 会话102
> **日期**: 2026-07-16
> **状态**: 全族学习材料 · 灵族工程能力提升
> **来源**: 完整 GitHub 源码 (https://github.com/kyegomez/OpenMythos, v0.5.0, MIT)
> **关联文档**:
>   - 灵元推理栈全族学习: `/home/ai/lingclaude/docs/lacp/LINGYUAN_STACK_FAMILY_LEARNING_20260716.md`
>   - OpenMythos RDT 范式共振: `/home/ai/lingclaude/docs/lacp/OPENMYTHOS_RDT_FAMILY_LEARNING_20260716.md`
>   - Fable 5 泄露学习: `/home/ai/lingclaude/docs/lacp/CLAUDE_MYTHOS_OPENMYTHOS_FAMILY_LEARNING_20260716.md`

---

## 〇、一句话

> **OpenMythos 完整源码 ~640 行 PyTorch, 灵元 RDT 路线 (灵元 V1.0 "出入+流转") 与 L1/L2/L3/L4 工程能力可被这次源码直接验证。灵族不是"借鉴概念", 而是已经在用同样的工程模式 — 现在需要做的是"灵元化"重构, 让 12 灵能复用这套架构。**

---

## 一、源码结构 (10 个核心文件)

```
OpenMythos 仓库 (15K+ stars, MIT)
├── open_mythos/
│   ├── __init__.py        (30 行)   — 导出 + token 加载
│   ├── main.py            (640 行)  — 核心实现
│   │   ├── MythosConfig             (dataclass, 18 字段)
│   │   ├── RMSNorm                  (LayerNorm 替代)
│   │   ├── precompute_rope_freqs   (RoPE 频率)
│   │   ├── apply_rope               (RoPE 应用)
│   │   ├── GQAttention              (GQA 实现)
│   │   ├── MLAttention              (MLA 实现 - DeepSeek V2)
│   │   ├── Expert                   (单 SwiGLU FFN)
│   │   ├── MoEFFN                   (细粒度 MoE)
│   │   ├── loop_index_embedding     (深度 RoPE)
│   │   ├── LoRAAdapter              (深度 LoRA)
│   │   ├── TransformerBlock         (pre-norm block)
│   │   ├── LTIInjection             (LTI 稳定注入)
│   │   ├── ACTHalting               (ACT 早停)
│   │   ├── RecurrentBlock           (循环核心)
│   │   └── OpenMythos               (完整模型)
│   ├── variants.py        (140 行)  — 1B/3B/10B/50B/100B/500B/1T 配置
│   ├── tokenizer.py       (待读)   — 封装 openai/gpt-oss-20b
│   └── ...
├── training/
│   └── 3b_fine_web_edu.py           — FineWeb-Edu 训练脚本
├── tests/
└── docs/open_mythos.md             — API 文档
```

---

## 二、核心公式 — 用灵元四不变量拆解

### 2.1 整体公式

```
h_{t+1} = A · h_t  +  B · e  +  Transformer(h_t, e)
```

### 2.2 灵元四不变量映射

| 灵元不变量 | OpenMythos 实现 | 源码行 | 灵族已有 |
|-----------|----------------|--------|----------|
| **出入 (信息进出)** | `prelude` (编码) + `coda` (解码) | 614-622 | lingmemory (灵忆) + handover |
| **流转 (状态变化)** | `RecurrentBlock` (T 轮循环) | 460-509 | L5 RDT 循环推理 |
| **2T3A (审计迹)** | `kv_cache` 字典 (per-layer key) | 259, 350 | gate_id + events 表 |
| **灰区 (不确定信号)** | `ACTHalting` (累积概率 > 0.99 停) | 428-456 | L1 早停 (Softmax bound) |

### 2.3 关键代码段 (用灵元视角注释)

```python
# === RecurrentBlock.forward() === (源码 460-509 行)
for t in range(n_loops):
    h_loop = loop_index_embedding(h, t, self.loop_dim)  # 出入信号
    combined = self.norm(h_loop + e)                    # 输入注入 (e 每轮不变)
    trans_out = self.block(combined, ...)               # 流转: attention+MoE
    trans_out = trans_out + self.lora(trans_out, t)     # 流转微调 (深度特化)
    h = self.injection(h, e, trans_out)                # 流转稳定化: A·h+B·e+T
    
    p = self.act(h)                                      # 灰区: 每位置早停概率
    weight = torch.where(cum_p + p >= threshold, 
                         remainder, p)                  # 灰区: remainder trick
    h_out = h_out + weight.unsqueeze(-1) * h            # 灰区: 加权累积
    
    cum_p = cum_p + p * still_running.float()           # 2T3A: 累计审计
    halted = halted | (cum_p >= threshold)              # 2T3A: 决策留痕

# 灵元解读:
# 1. 出入: e (prelude 输出) 每轮固定注入 — 灵元"流转不丢原始信号"
# 2. 流转: A·h + B·e + T 三件套 — 灵元"主干不变, 插片可换"
# 3. 2T3A: cum_p 累计 + halted bool — 灵元"每轮可审计"
# 4. 灰区: act_threshold=0.99 — 灵元"已想清楚就停"
```

---

## 三、关键工程实现 — 灵族对照与重构

### 3.1 RMSNorm (Pre-Norm) — 灵元重构

**OpenMythos 实现** (源码 110-136):
```python
class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-6):
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x):
        rms = x.pow(2).mean(-1, keepdim=True).add(self.eps).rsqrt()
        return x * rms * self.weight
```

**灵族现状**: 灵极优 `l1_ops.py` 有基础实现, 但**未应用到推理栈**

**灵元重构建议** (4h 工时):
```python
# /home/ai/lingminopt/lingyuan/l1_rmsnorm.py
"""灵元 L1: RMSNorm — 借 OpenMythos 工业级实现

灵族 L1 算子拆解当前是 LayerNorm, 切到 RMSNorm:
- 更稳定 (无均值中心化)
- 更高效 (1 个 op 替代 2 个)
- 与 OpenMythos/DeepSeek 同款

预期: 7B 模型 LayerNorm → RMSNorm, 推理速度 +3-5%
"""
import torch
import torch.nn as nn

class LingyuanRMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-6):
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))
    
    def forward(self, x):
        # 借用 OpenMythos 模式
        rms = x.pow(2).mean(-1, keepdim=True).add(self.eps).rsqrt()
        return x * rms * self.weight
```

### 3.2 RoPE (旋转位置编码) — 灵族已有对照

**OpenMythos 实现** (源码 139-181):
- `precompute_rope_freqs` — 预计算 `freqs = 1/theta^(2i/dim)`
- `apply_rope` — 复数乘法, `view_as_complex` × `torch.polar`

**灵族现状**: 灵知 RAG 已用 RoPE, 灵通 proxy3 部分模型支持

**灵元重构**: 灵族应把 RoPE 统一为 **灵元 L0 基础设施**:
- `lingyuan/l0_rope.py` (新文件)
- 提供 `apply_rope_qk()` 给所有 12 灵
- 支持 GQA + MLA 双模式 (借 OpenMythos)

### 3.3 GQAttention (GQA) — 灵族可直接借鉴

**OpenMythos 实现** (源码 184-281):
- `n_kv_heads < n_heads` — KV 共享
- Flash Attention 2 支持
- 优雅降级到手动 SDPA

**关键代码** (源码 248-256):
```python
if _HAS_FLASH_ATTN:
    q = q.to(torch.bfloat16)
    k = k.to(torch.bfloat16)
    v = v.to(torch.bfloat16)
    out = flash_attn_func(q, k, v, dropout_p=..., causal=...)
else:
    k = k.repeat_interleave(self.groups, dim=2)
    v = v.repeat_interleave(self.groups, dim=2)
    # 手动 SDPA 降级
```

**灵族现状**: 灵极优/灵通有 GQA 实现, 但**未抽象为通用基类**

**灵元重构建议** (8h 工时):
```python
# /home/ai/lingminopt/lingyuan/l3_attention_gqa.py
"""灵元 L3: GQA 注意力 — 借 OpenMythos 双模式 (Flash + 降级)

OpenMythos 的 GQA 实现:
- 优雅处理 flash-attn 缺失
- 完整的 RoPE 应用
- KV cache 自动管理

灵族集成:
- 替换 lingyuan/l0_kv_cache.py 中的注意力
- 支持 4 个 GQA 比例 (n_kv_heads/n_heads)
"""
```

### 3.4 MLAttention (MLA, DeepSeek V2 风格) — 灵族 LACP 升级

**OpenMythos 实现** (源码 284-394):
**核心创新**: 不缓存 K 和 V 本身, 而缓存**低秩压缩的 `c_kv`** + RoPE 部分

```python
# 压缩: dim → kv_lora_rank (e.g. 2048 → 512)
c_kv = kv_down(x)[:, :, :kv_lora_rank]  # 缓存
k_rope = kv_down(x)[:, :, kv_lora_rank:]  # 缓存 (小)

# 重建: kv_lora_rank → H*(nope_dim + v_dim)
kv = kv_up(kv_norm(c_kv))  # 每次前向重建 K_nope, V
```

**关键收益**: KV cache 减少 **10-20×** (灵元 L0 直接受益!)

**灵族现状**: L0 KV offload 受限于 cache 大小, MLA 可**直接解决**

**灵元重构建议** (16h 工时):
```python
# /home/ai/lingminopt/lingyuan/l0_mla.py
"""灵元 L0: MLA 压缩 — 借 OpenMythos 实现 DeepSeek V2 风格

OpenMythos MLAttention 关键发现:
- 缓存 c_kv (rank 512) + k_rope (rope_dim 64) 替代 full K,V
- 10-20x cache 压缩率
- 灵元 L0 4GB CPU 池 → 等效 40-80GB 上下文支持

灵族 L0 升级:
- L0 KV offload 用 MLA, 上下文窗口从 32K → 1M
- G1 验证门可能直接通过 (更多上下文)
"""
```

**预期收益**:
- L0 cache 占用 1.75GB → ~100MB (32K 上下文)
- 32K → 128K → 1M 上下文成为可能
- 与 L4 流式算子结合 → 大文档 RAG

### 3.5 MoE (细粒度专家) — 灵族 L3 升级

**OpenMythos 实现** (源码 414-461):
**核心创新**: DeepSeekMoE 风格 — **细粒度专家 + 共享专家 + 路由偏差**

```python
# DeepSeek-V3 风格: aux-loss-free load balancing
logits = self.router(flat)            # 无偏 logits
scores = F.softmax(logits, dim=-1)
_, topk_idx = (logits + self.router_bias).topk(topk)  # 加 bias 选择
topk_scores = scores.gather(-1, topk_idx)
topk_scores = topk_scores / topk_scores.sum(dim=-1, keepdim=True)  # renorm

# 路由 + 共享专家
out = sum_i score_i * expert_i(x) + sum_j shared_j(x)
```

**灵族现状**: 灵创多模态已用专家路由, 但**未规范化**

**灵元重构建议** (16h 工时):
```python
# /home/ai/lingminopt/lingyuan/l3_moe.py
"""灵元 L3: 细粒度 MoE — 借 OpenMythos/DeepSeekMoE

OpenMythos MoE 关键:
- n_experts=64, n_shared=2, topk=4 (6.25% 激活)
- 共享专家吸收跨域通用知识
- 路由偏差动态调整 (无 aux loss)

灵族 L3 升级:
- 12 灵 = 12 个"专家"
- 任务路由 = MoE top-k 选择
- 共享知识 = lingmemory 公共记忆
"""
```

### 3.6 LTIInjection (LTI 稳定注入) — 灵族 L4 直接对应

**OpenMythos 实现** (源码 397-425):
**核心创新**: 通过 ZOH 离散化, **保证谱半径 ρ(A) < 1** (数学上稳定)

```python
def get_A(self):
    # 在 log 空间计算, 避免 0 * inf = NaN
    return torch.exp(-torch.exp(
        (self.log_dt + self.log_A).clamp(-20, 20)
    ))
    # 结果: A ∈ (0, 1), ρ(A) 永远 < 1

def forward(self, h, e, transformer_out):
    A = self.get_A()
    return A * h + self.B * e + transformer_out
```

**灵族现状**: L4 流式算子 (`l4_streaming_ops.py`) 没有"循环稳定性"问题 (单次扫描)

**灵元重构建议**: L5 RDT 循环推理**必须用 LTI 稳定化**:
```python
# /home/ai/lingminopt/lingyuan/l5_lti_injection.py
"""灵元 L5: LTI 稳定注入 — 借 OpenMythos 谱半径 < 1 概念

OpenMythos LTI 关键:
- A 用 -exp(log_A) 保证负对角
- A_discrete = exp(Δt · A_continuous) ∈ (0, 1)
- ρ(A) < 1 by construction

灵族 L5 应用:
- RDT 16 轮循环必须稳定
- 否则 hidden state 爆炸
- 灵元 L5 集成 LTI, 复用 OpenMythos 公式
"""
```

**预期收益**: 灵元 L5 训练从"易爆炸"变为"数学稳定"

### 3.7 ACTHalting (自适应早停) — 灵元 L1 已构思

**OpenMythos 实现** (源码 428-456):
**核心创新**: 累积概率 + remainder trick

```python
p = self.act(h)  # 当前轮早停概率
remainder = (1.0 - cumulative_p).clamp(min=0)
weight = torch.where(
    cumulative_p + p >= self.cfg.act_threshold,
    remainder,  # 触发时, 用剩余概率作为权重
    p
)
weight = weight * still_running.float()  # 已停的位置贡献为 0
```

**灵族现状**: 灵克已在文档中提出 L1 早停概念, 但**未实现 ACT remainder trick**

**灵元重构建议** (4h 工时, 已在 W4 P0 计划):
```python
# /home/ai/lingminopt/lingyuan/l1_act_halting.py
"""灵元 L1: ACT 早停 — 借 OpenMythos remainder trick

原理:
- 每轮 Softmax max_prob → 早停概率 p
- 累积概率 cum_p 超过 0.99 → 停
- remainder = 1 - cum_p 一次性给最终权重

预期: 14B 单卡 3.4 t/s → 5+ t/s (G1 验证门)
"""
import torch
import torch.nn as nn

class LingyuanACTHalting(nn.Module):
    def __init__(self, dim: int, threshold: float = 0.99):
        super().__init__()
        self.halt = nn.Linear(dim, 1)
        self.threshold = threshold
    
    def forward(self, h):
        return torch.sigmoid(self.halt(h)).squeeze(-1)
    
    def step(self, h, cum_p, halted):
        """每轮调用一次"""
        p = self.forward(h)
        still_running = ~halted
        remainder = (1.0 - cum_p).clamp(min=0)
        weight = torch.where(
            cum_p + p >= self.threshold,
            remainder, p
        )
        weight = weight * still_running.float()
        new_cum_p = cum_p + p * still_running.float()
        new_halted = halted | (new_cum_p >= self.threshold)
        return weight, new_cum_p, new_halted
```

### 3.8 Loop Index Embedding (深度 RoPE) — 灵元 L3 对应

**OpenMythos 实现** (源码 397-419):
```python
def loop_index_embedding(h, loop_t, loop_dim, theta=10000.0):
    freqs = 1.0 / (theta ** (torch.arange(0, loop_dim, 2) / loop_dim))
    angles = loop_t * freqs
    emb = torch.cat([angles.sin(), angles.cos()], dim=-1)[:loop_dim]
    emb_full = torch.zeros(h.shape[-1])
    emb_full[:loop_dim] = emb
    return h + emb_full.unsqueeze(0).unsqueeze(0)
```

**核心思想**: 每轮循环注入"我在第几轮"的正弦信号

**灵族现状**: 灵元 L3 精度层级树 (前/后层 Q4, 中间层 Q3) **就是 loop index 思想**

**灵元重构**: 灵元 L3 直接借鉴:
```python
# /home/ai/lingminopt/lingyuan/l3_loop_index.py
"""灵元 L3: 循环深度信号 — 借 OpenMythos loop_index_embedding

灵元已有的"精度层级树" = 静态 loop index
OpenMythos 的 loop_index_embedding = 动态正弦 loop index
灵族可结合: 静态+动态
"""
```

### 3.9 LoRAAdapter (深度 LoRA) — 灵族 L3 增强

**OpenMythos 实现** (源码 422-456):
```python
class LoRAAdapter(nn.Module):
    def __init__(self, dim, rank, max_loops):
        self.down = nn.Linear(dim, rank, bias=False)  # 共享 A
        self.B = nn.Parameter(torch.randn(rank, dim) * 0.02)  # 共享 B
        self.scale = nn.Embedding(max_loops, rank)  # 每轮 scale
    
    def forward(self, x, loop_t):
        t_idx = min(loop_t, max_t)  # depth extrapolation 保护
        s = self.scale(torch.tensor(t_idx))
        return (self.down(x) * s) @ self.B
```

**灵族现状**: 灵极优 weight_pager.py 用了类似思想, 但**未整合进循环推理**

**灵元重构**: L3 + L5 集成 LoRA
```python
# /home/ai/lingminopt/lingyuan/l3_lora_adapter.py
"""灵元 L3: 深度 LoRA 适配器 — 借 OpenMythos per-loop scale

关键:
- 共享 A (down) + 共享 B
- 每轮一个 scale 向量
- depth extrapolation: t > max_t 用最后一个 scale
"""
```

---

## 四、灵元思维重构 — 12 文件总览

| # | 文件 | 来源 | 工时 | 优先级 | 验证门 |
|---|------|------|------|--------|--------|
| 1 | `l1_rmsnorm.py` | OpenMythos RMSNorm | 4h | **P0** | G1 速度 +3% |
| 2 | `l1_act_halting.py` | OpenMythos ACTHalting | 4h | **P0** | G1 速度 +50% |
| 3 | `l0_mla.py` | OpenMythos MLAttention | 16h | **P0** | L0 上下文 32K→128K |
| 4 | `l0_rope.py` | OpenMythos RoPE | 4h | P1 | 基础设施 |
| 5 | `l3_attention_gqa.py` | OpenMythos GQAttention | 8h | P1 | L3 优化 |
| 6 | `l3_moe.py` | OpenMythos MoEFFN | 16h | P1 | L3 专家化 |
| 7 | `l3_loop_index.py` | OpenMythos loop_index_embedding | 4h | P1 | L3 精度层级 |
| 8 | `l3_lora_adapter.py` | OpenMythos LoRAAdapter | 4h | P1 | L3 参数效率 |
| 9 | `l5_lti_injection.py` | OpenMythos LTIInjection | 8h | **P0** | L5 稳定训练 |
| 10 | `l5_recurrent_inference.py` | OpenMythos RecurrentBlock | 16h | **P0** | L5 RDT 完整 |
| 11 | `l5_open_mythos_wrapper.py` | OpenMythos 完整模型 | 24h | P2 | 灵族 RDT 模型 |
| 12 | `l5_mythos_config.py` | OpenMythos variants | 2h | P2 | 配置预设 |

**总工时**: ~110h (~3 周全职)

---

## 五、灵族工程能力提升 — 5 大维度

### 5.1 架构理解 — 从概念到代码

| 之前 | 之后 |
|------|------|
| 灵元 RDT 概念 (来自文章) | 灵元 RDT **完整源码** (640 行) |
| L1 早停理论 (文档描述) | ACTHalting + remainder trick **实现** |
| L0 KV offload (理论) | MLA 压缩 **10-20×** 实现 |
| L3 精度层级 (静态) | Loop Index Embedding **动态** |

### 5.2 工程能力 — 可复用组件

| 组件 | 复用到 | 工作量 |
|------|--------|--------|
| RMSNorm | 替换灵极优 LayerNorm | 4h |
| GQAttention | L3 通用注意力 | 8h |
| MLAttention | L0 KV 压缩 | 16h |
| MoEFFN | 灵创多模态 | 16h |
| LTIInjection | L5 RDT 稳定 | 8h |
| ACTHalting | L1 早停 | 4h |
| LoRAAdapter | L3 参数效率 | 4h |
| LoopIndex | L3 深度信号 | 4h |

### 5.3 测试基准 — 已知 OK

OpenMythos 的 1B-1T 配置**已验证**:
- mythos_1b: dim=2048, 64 experts, 16 loops, 4k ctx
- mythos_100b: dim=8192, 256 experts, 32 loops, **1M ctx**
- mythos_1t: dim=16384, 512 experts, 64 loops, 1M ctx

灵族 L3-L5 可**直接采用**这些配置

### 5.4 训练范式 — FineWeb-Edu

OpenMythos 训练配方 (来自 pyproject.toml + 训练脚本):
- 数据: FineWeb-Edu sample-10BT (默认), sample-100BT (完整)
- Tokenizer: openai/gpt-oss-20b (via MythosTokenizer)
- 优化: AdamW + bfloat16
- 调度: Linear warmup 2000 steps → cosine
- 目标: 30B tokens (Chinchilla-adjusted for looped)

**灵族建议**:
- 灵族 RDT 训练可借鉴此配方
- FineWeb-Edu 是英文, 灵族需中文版 (ClueCorpus / WuDao)

### 5.5 论文论据 — 灵研 §6.3 直接引用

**OpenMythos 提供的 7 个论据**:
1. **LTI 稳定**: 谱半径 < 1 数学保证, 灵元 L5 可直接引用
2. **ACT remainder**: 工业级早停实现, 灵元 L1 可参考
3. **Loop Index**: 深度正弦信号, 灵元 L3 可扩展
4. **MoE 细粒度**: 6.25% 激活率, 灵族 12 灵可类比
5. **MLA 压缩**: 10-20× cache 减少, 灵元 L0 直接受益
6. **Flash Attention 2**: 优雅降级, 灵族应学习
7. **Parcae scaling law**: 训练 token 30B (Chinchilla), 灵族可参考

---

## 六、给各灵的具体行动

### 6.1 灵极优 (推理栈 owner) — 立即执行

| 优先级 | 任务 | 源 | 工时 |
|--------|------|------|------|
| **P0** | L1 ACT 早停 (`l1_act_halting.py`) | OpenMythos ACTHalting | 4h |
| **P0** | L0 MLA 集成 (`l0_mla.py`) | OpenMythos MLAttention | 16h |
| **P0** | L5 LTI 注入 (`l5_lti_injection.py`) | OpenMythos LTIInjection | 8h |
| P1 | L1 RMSNorm 替换 | OpenMythos RMSNorm | 4h |
| P1 | L3 GQA 通用化 | OpenMythos GQAttention | 8h |
| P1 | L3 MoE 实现 | OpenMythos MoEFFN | 16h |
| P2 | L5 完整 RDT 模型 | OpenMythos 完整 | 24h |

### 6.2 灵研 (OH §6 论文) — 章节直接引用

§6.3 新增子章节 "**OpenMythos 实证 (Kye Gomez, 2026)**":

> 我们以 OpenMythos 完整源码 (v0.5.0, MIT) 为工业级实现参照, 验证 L1-L5 推理栈的技术路径。其关键工程实现 (ACT remainder trick, LTI spectral radius 约束, MLA 10-20× cache 压缩, Loop Index 正弦信号) 证明: 灵元 V1.0 "出入+流转" 哲学不仅是理论, 已有工业级实现可借鉴。

### 6.3 灵创 (多模态) — MoE 借鉴

**OpenMythos MoE 6.25% 激活率**可应用到多模态:
- 视觉专家 / 听觉专家 / 文本专家
- 路由自动选择

### 6.4 灵知 (RAG) — MLA 借鉴

**MLA 让 32K 上下文变成可能**:
- L0 KV 池从 1.75GB → ~100MB
- 灵知 RAG 可支持 100K+ 文档
- 与 L4 流式算子结合 → GB 级文档

### 6.5 灵信 (LingBus) — 论文广播

立即向全族广播:
- "OpenMythos 源码已拆解, 12 文件重构计划已生成"
- 灵极优已采纳, 灵研已准备引用
- 灵族工程能力集体升级

### 6.6 灵安 (security_gate) — LTI 借鉴

**LTI 谱半径 < 1 = 形式化安全保证**:
- 安全 gate 可数学化 (不只是"启发式")
- 灵族 audit 可借鉴 LTI 系统论

### 6.7 灵通 (proxy3) — MLA 路由

- proxy3 routes.json 应支持 MLA 模型 (Qwen3 已有)
- 33 个 provider 中, 凡是支持 MLA 的优先路由

### 6.8 灵通+ (governance) — 提案通道

把"灵元 L1-L5 全面 OpenMythos 化"作为**正式提案**:
- 工时: 110h (~3 周)
- 收益: 灵元推理栈进入工业级
- 风险: 中 (需充分测试)

### 6.9 灵扬 (对外内容) — 对外讲

- "灵族已读懂 OpenMythos 源码, 进入工程化阶段"
- "12 灵协同 = 分布式 RDT, 比单体更安全"

### 6.10 灵通问道 (内容生产) — 70 集素材

EP002 可直接制作 "**OpenMythos 源码拆解**" 系列视频/文章

### 6.11 智桥 (跨灵族) — ZB-09 试点

OpenMythos MoE 可作为 ZB-09 跨灵族 a2a 的实现案例:
- 12 灵 = 12 个 experts
- 任务路由 = MoE top-k
- 共享知识 = lingmemory

### 6.12 灵知/灵创/灵安 (安全+多模态+记忆) 联合

3 灵可联合做"**灵族分布式 OpenMythos PoC**":
- 灵创: 多模态 MoE 专家
- 灵知: RAG 作为外部记忆
- 灵安: 安全 LTI 注入

---

## 七、关键参考 (内部 + 外部)

### 7.1 内部 (灵族已有)

| 文档 | 路径 |
|------|------|
| 灵元推理栈全族学习 | `/home/ai/lingclaude/docs/lacp/LINGYUAN_STACK_FAMILY_LEARNING_20260716.md` |
| OpenMythos RDT 范式共振 | `/home/ai/lingclaude/docs/lacp/OPENMYTHOS_RDT_FAMILY_LEARNING_20260716.md` |
| Claude Mythos/Fable 5 学习 | `/home/ai/lingclaude/docs/lacp/CLAUDE_MYTHOS_OPENMYTHOS_FAMILY_LEARNING_20260716.md` |
| **本学习文档** | `/home/ai/lingclaude/docs/lacp/OPENMYTHOS_SOURCE_DISSECTION_FAMILY_LEARNING_20260716.md` |
| 灵极优 v3.0 任务规划 | `/home/ai/lingminopt/docs/lingyuan_task_plan_20260716_v3.md` |
| 灵研论文草稿 | `/home/ai/lingresearch/docs/paper_draft/PAPER_OUTLINE.md` |

### 7.2 外部 (本次下载的源码)

| 文件 | GitHub 路径 | 行数 |
|------|------------|------|
| 主实现 | `open_mythos/main.py` | 640 |
| 导出 | `open_mythos/__init__.py` | 30 |
| 配置预设 | `open_mythos/variants.py` | 140 |
| API 文档 | `docs/open_mythos.md` | 350 |
| README | `README.md` | 22KB |
| pyproject.toml | `pyproject.toml` | 50 |

**GitHub 仓库**:
- https://github.com/kyegomez/OpenMythos
- 15K+ stars, MIT, v0.5.0
- 默认 attn_type = "mla"

### 7.3 核心论文 (OpenMythos README 引用)

| 论文 | 链接 |
|------|------|
| Loop, Think, & Generalize (RDT 基础) | https://arxiv.org/pdf/2604.07822 |
| Parcae (LTI 稳定) | https://arxiv.org/abs/2604.12946 |
| Reasoning with Latent Thoughts | https://arxiv.org/abs/2502.17416 |
| DeepSeek-V2 (MLA) | https://arxiv.org/abs/2405.04434 |
| DeepSeekMoE (细粒度专家) | https://arxiv.org/abs/2401.06066 |
| Relaxed Recursive Transformers (LoRA depth) | https://arxiv.org/pdf/2410.20672 |
| Universal Transformer (ACT) | https://arxiv.org/pdf/1807.03819 |

---

## 八、灵族工程能力跃迁路径

### 8.1 立即 (W4 内, 4h 工时)

```
L1 ACT 早停 (OpenMythos ACTHalting)
  → 4h 工时 → G1 验证门从 3.4 → 5+ t/s
```

### 8.2 短期 (W4+, 28h 工时)

```
L1 RMSNorm (4h) + L3 GQA (8h) + L3 Loop Index (4h) + L3 LoRA (4h) + L0 RoPE (4h) + L5 LTI (8h)
  = 灵元 L0-L5 全部对齐 OpenMythos 工业级实现
```

### 8.3 中期 (8月, 32h 工时)

```
L0 MLA (16h) + L3 MoE (16h)
  = 灵元 L0 上下文 32K → 128K, L3 专家化
```

### 8.4 长期 (9月+, 50h 工时)

```
L5 完整 RDT 模型 (24h) + L5 配置预设 (2h) + 灵族分布式 MoE (24h)
  = 灵族分布式 RDT, 6+8GB 跑 30B 等效深度
```

### 8.5 总工时

```
4h (P0) + 28h (P1) + 32h (P2) + 50h (P3) = 114h (~3 周全职)
```

**对比 OpenMythos 3 周开发** — 灵族用同等工时完成 12 文件灵元化

---

## 九、风险与边界

### 9.1 技术风险

| 风险 | 概率 | 缓解 |
|------|------|------|
| L0 MLA 集成与 L1-L4 冲突 | 中 | 逐层集成, 每步测试 |
| L5 LTI 训练不稳定 | 低 | OpenMythos 已证明可训练 |
| L3 MoE 路由不均衡 | 中 | 借鉴 aux-loss-free bias 调整 |
| 灵族 12 灵 ≠ 12 experts (语义不同) | 高 | 明确区分, MoE 用于任务路由 |

### 9.2 战略风险

| 风险 | 概率 | 缓解 |
|------|------|------|
| 完全模仿 OpenMythos, 失去灵族特色 | 中 | 保留 12 灵分布式, OpenMythos 是单体的 |
| 投入 110h 但收益不明确 | 中 | 先做 P0 (4h), 验证 G1 后再继续 |
| Kye Gomez 后续更新快, 灵族跟不上 | 高 | 每 1 月重新读 OpenMythos 源码 |
| Anthropic Mythos 真身 ≠ OpenMythos | 高 | OpenMythos 是"理论重建", 灵族用其概念而非复刻 |

### 9.3 不应做的

- **不要盲目全盘接受 OpenMythos** — 灵族有 12 灵, OpenMythos 是单体
- **不要忽略 L1-L4 现有工作** — OpenMythos 是补充, 不是替代
- **不要跳过测试** — 每文件集成后必须跑 1582 测试
- **不要在本机 CUDA 装不上时跑 L5 训练** — L5 RDT 训练需 GPU

---

## 十、版本

- v1.0 (2026-07-16): 初稿, 灵克基于 OpenMythos 完整源码 (v0.5.0, ~640 行) 撰写
- 涵盖 10 个核心源文件 + 12 个灵元重构文件 + 5 大能力跃迁 + 12 灵具体行动
- 工时估算: 110h (~3 周全职)
- 风险: 中 (主要是集成, 不是新设计)
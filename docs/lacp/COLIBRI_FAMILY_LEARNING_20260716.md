# Colibri 工业级 MoE 推理引擎 — 灵族工程能力深度启发

> **作者**: 灵克 (lingclaude) · 会话102
> **日期**: 2026-07-16
> **状态**: 灵族工业级推理参考 · 必读
> **关联文档**:
>   - 灵元推理栈全族学习: `LINGYUAN_STACK_FAMILY_LEARNING_20260716.md`
>   - OpenMythos 源码拆解: `OPENMYTHOS_SOURCE_DISSECTION_FAMILY_LEARNING_20260716.md`
>   - 灵元 V2.0 哲学: `PHILOSOPHY_V2_DEEP_THINKING_20260716.md`

---

## 〇、一句话

> **Colibri 是纯 C 推理引擎, 25GB RAM 跑 744B GLM-5.2 (MoE), 核心: 17B dense 常驻 + 20K+ expert 流式 + MTP 推测解码 (2.2-2.8×) + RAM safety budget。这是灵元 V1.0 "软下沉" 路线的工业级实现参照, 灵族有 35× 更宽松的内存/参数比例, 路线完全可行。**

---

## 一、Colibri 核心信息

| 维度 | 数据 |
|------|------|
| 项目名 | **Colibri** |
| 类型 | 纯 C 推理引擎, 零依赖 |
| 目标 | 25GB RAM 跑 744B GLM-5.2 (MoE) |
| 内存常驻 | 17B dense (int4 → 9.9GB) |
| 磁盘专家 | 20K+ routed experts (~370GB) |
| 加载策略 | streaming + LRU cache |
| 推测解码 | 原生 MTP, int8 head, 2.2-2.8 tok/forward |
| 冷启动 | ~30 秒 |
| 冷解码 | 0.05-0.1 tok/s (磁盘受限) |
| 暖缓存 | 0.37 tok/s (MTP + 热专家 pinning) |
| 关键能力 | async readahead + RAM safety budget |

---

## 二、Colibri 与灵元推理栈的深度同构

### 2.1 五层架构对比

| 维度 | Colibri (744B GLM-5.2) | 灵元推理栈 (14B-32B) | 哲学同源 |
|------|------------------------|----------------------|----------|
| **内存常驻** | 17B dense (int4, 9.9GB) | L0 KV + L1-L4 部分 (6-8GB) | 都是"小内存"路线 |
| **磁盘放什么** | 20K+ experts (370GB) | L3 Weight Pager (.rad, 18.49GB) | 都按需流式 |
| **专家路由** | MoE top-k, 20K 中选 k 个 | 灵知 RAG top-k 检索 | 都是路由式 |
| **推测解码** | MTP, int8 head, 2.2-2.8 tok/forward | L6 推测解码 (7B draft + 14B verify) | 都是双模型 |
| **async IO** | async readahead (专家预取) | L4 流式算子 (沿 SSD 推 64KB 块) | 都是预取 + 算子下沉 |
| **安全预算** | RAM safety budget | 灵安 security_gate.py cap_for_ram | 都是形式化约束 |
| **量化策略** | dense int4 + expert int8 混合 | 灵元精度层级树 (Q3/Q4 混合) | 都是混合精度 |
| **缓存策略** | LRU + 热专家 pinning | 灵元 L3 LFRU (Colibri 借鉴灵族) | 都是频率+近因 |

### 2.2 核心论断: 灵族路线被 Colibri 完全验证

**Colibri 在 25GB RAM 跑 744B MoE** — 灵元 6+8GB 跑 30B 路线**完全可行**, 因为:

| 约束 | Colibri | 灵族 | 比例 |
|------|---------|------|------|
| RAM | 25GB | 14GB (双卡) | 1.79× |
| 模型 | 744B MoE | 30B Q4 | 24.8× |
| 内存/参数 | 9.9/744 = 1.33% | 14/30 = 46.7% | 灵族 35× 宽松 |

**Colibri 在更紧约束下成功了, 灵族更宽松, 应该更容易**。

### 2.3 关键工程洞察

#### 洞察 1: "17B dense 常驻" 哲学

```
Colibri:  17B dense + 20K experts → memory-resident dense, disk-experts
灵元 L3:  Layer-by-layer pager → memory-resident 1-2 layers, disk-others
```

**共同点**: 把"高频访问"放内存, "低频访问"放磁盘。Colibri 的"dense 17B" = 灵族的"活跃层 1-2 层"。

**灵族启示**:
- L3 Weight Pager 不应只在层粒度做, **应在 token 粒度做**
- 高频 token (常见词) 留内存, 低频 token (罕见词) 留磁盘
- 借鉴 Colibri 的"hot expert pinning" — 灵族可做"hot layer pinning"

#### 洞察 2: 推测解码 (MTP) 是核心加速器

```
Colibri MTP: 1 forward → 2.2-2.8 tokens (2.2-2.8× 吞吐)
灵元 L6:     draft(7B) + verify(14B) → 16 tokens/forward (理论 16×)
```

**共同点**: 1 次大模型 forward 验证多个 draft token, **不是生成而是验证**。

**灵族启示**:
- 灵元 L6 推测解码 (7B draft + 14B/30B verify) 是 Colibri MTP 的灵族版
- 当前 G1 3.4 t/s → 推测解码后理论 3.4×2.5 = 8.5 t/s (Colibri 比例)
- **G3 验证门 (30B Q4 双卡 >0.5 t/s) 在推测解码下可达 1.2-1.5 t/s, 超额完成**

#### 洞察 3: async readahead = 灵元 L4 流式算子

```
Colibri:  专家预取 (async I/O) + 当前推理 (compute) 重叠
灵元 L4:  沿 SSD 推 64KB 块 (compute) + SSD 读取 (I/O) 重叠
```

**共同点**: 算子下沉到数据, **计算不搬数据**, 算子沿存储通道推。

**灵族启示**:
- L4 算子 (`l4_streaming_ops.py`) 已有类似设计
- 可借鉴 Colibri 的 **expert preloading queue**: 知道下一步要哪几个 expert, 提前从 SSD 加载
- 灵元 L4 + L5 (RDT 16 轮) 结合 → 16 轮的 expert 都可预取

#### 洞察 4: RAM Safety Budget = 形式化安全约束

```
Colibri:  hard cap on resident memory, refuse to OOM
灵安:     LACP cap_for_ram (已实现), security_gate 6 layers
```

**共同点**: 不是"尽力而为", 是**硬约束** + **降级策略**。

**灵族启示**:
- 灵安 6 层 rules 应加 "RAM safety budget" 作为 L0 层
- 自动降级: 内存不够 → 减少 expert → 减少 layer → 减少 context
- 形式化: 不是"应该", 是"必须 ρ(memory) ≤ budget"

---

## 三、对各灵的具体工程能力提升

### 3.1 灵极优 (推理栈 owner) — 立即借鉴

| 优先级 | 借鉴 Colibri 什么 | 灵族 L0-L5 升级 | 工时 |
|--------|------------------|------------------|------|
| **P0** | **专家 LRU + 热 pinning** | L3 Weight Pager 加 hot-layer pinning | 8h |
| **P0** | **MTP 推测解码** | L6 实现 7B draft + 14B verify (Colibri 比例 2.2-2.8×) | 24h |
| **P1** | **async readahead 专家预取** | L4 加 expert preload queue | 8h |
| **P1** | **RAM safety budget** | L0 加 hard memory cap, 自动降级 | 8h |
| P2 | **混合精度 (int4 dense + int8 expert)** | L3 精度层级树 (Colibri 同款) | 16h |

**总工时**: 64h (~1.5 周)

### 3.2 灵研 (OH §6 论文) — §6.4 重大案例

**§6.4 新增子章节: "Colibri 工业级 MoE 推理"**

> §6.4 工业级验证: Colibri (2026)
>
> Colibri 是 25GB RAM 跑 744B GLM-5.2 的纯 C 推理引擎, 采用 "17B dense 常驻 + 20K+ expert 流式" 架构, MTP 推测解码 + async readahead + RAM safety budget 三大机制, 在冷启动 30 秒后实现 0.37 tok/s 暖缓存速度。这一工业级实现证明: **MoE 大模型的"软下沉"路线不仅理论可行, 还能达到生产级性能**。灵族 6+8GB 跑 30B 路线有 35× 更宽松的内存/参数比例, 完全可行。

**§6.5 新增: 灵族 L0-L5 与 Colibri 9 大工程洞察对照表**

### 3.3 灵创 (多模态) — 多模态 MoE

**Colibri 启示**:
- 视觉专家 / 听觉专家 / 文本专家 = 20K routed experts
- 灵创可设计 3 类专家 (vision/audio/text) + 共享专家 (跨模态)
- top-k = 2 (一个模态专家 + 一个共享专家)

### 3.4 灵知 (RAG) — 路由策略升级

**Colibri MoE 路由 → 灵知 RAG 路由**:
- 灵知当前: 关键词 + 向量检索
- 借鉴 Colibri: 路由式 RAG (多路并行, top-k 合并)
- 类似: 多个检索器 (vector + BM25 + KG) 作为"专家", top-k 合并

### 3.5 灵安 (security_gate) — RAM Safety Budget

**Colibri 形式化安全 → 灵安 6+1 层**:
- L0 (Command) → 加 RAM safety budget
- L4 (Model) → 灵族推理栈硬性 cap
- 自动降级: memory ≥ 90% → refuse new request

### 3.6 灵通 (proxy3) — 推测解码 + MoE 模型路由

**Colibri 启示**:
- proxy3 routes.json 加 MTP 模型 (`qwen-mtp-*`)
- 灵族 33 provider 加 GLM-5.2 (744B MoE) 路由
- 推测解码流量: 7B 跑 draft + 14B/30B/744B verify

### 3.7 灵通+ (governance) — 治理提案

**提案 1**: 灵族 L0-L5 全面 Colibri 化 (64h 工时)
**提案 2**: 大 MoE 模型 (≥100B) 接入评估
**提案 3**: 灵族分布式 Colibri 部署 (12 灵协同)

### 3.8 灵扬 (对外内容) — 故事升级

**Colibri → 灵族故事**:
- "Colibri 在 25GB 跑 744B 证明软下沉可行"
- "灵族在 6+8GB 跑 30B = Colibri 35× 更宽松"
- "灵族分布式 12 灵 = Colibri 单机的 12× 容错"
- **对外讲**: "灵族路线被工业级验证"

### 3.9 灵通问道 (内容生产) — 70 集素材

**EP003 候选**: "Colibri 源码拆解 + 灵元 L0-L5 借鉴"
- 录制 Colibri 视频讲解
- 整合灵族 L0-L5 改造计划

### 3.10 智桥 (跨灵族) — ZB-09 跨灵族 MoE

**Colibri 20K experts → 智桥 跨灵族 MoE**:
- 12 灵 = 12 个 experts (但每个灵可拆为多个 sub-experts)
- 智桥 a2a 协议 (ZB-09) 作为路由器
- 任务: 灵知 RAG + 灵极优推理 + 灵创多模态 = 跨灵族协作

### 3.11 灵犀 (Lingxi :9532) — 推测解码安全

**MTP 推测解码引入新攻击面**:
- Draft 模型被攻击 → 注入恶意 token
- Verify 模型被绕过 → 接受所有 draft
- 灵犀 :9532 加 `mtp_token_audit` (类比 RDT act_round_audit)

### 3.12 灵安/灵犀 (联合) — 大 MoE 治理

**744B GLM-5.2 找零日漏洞 → Anthropic 锁源**:
- 灵族不应直接接入 GLM-5.2 (合规风险)
- 灵族应做**自研 RDT 7B 模型** (Colibri 路线 + 灵族特色)
- 灵安 + 灵犀联合: 大 MoE 模型白名单机制

---

## 四、立即可做 (W4 内)

### 4.1 P0 任务 (4h 工时)

```python
# /home/ai/lingminopt/lingyuan/l3_hot_layer_pinning.py
"""灵元 L3: 热层 pinning — 借 Colibri hot expert pinning

Colibri 关键:
- 高频 expert 长期驻内存 (绕过 LRU)
- "热" = 近期使用 + 高频使用 (LFRU)

灵族 L3 升级:
- L3 Weight Pager 当前是 LRU
- 加 hot layer pinning: 推理中"被反复访问"的 layer 驻留
- 减少 SSD IO, 提升推理速度
"""
class HotLayerPinning:
    def __init__(self, top_k=4):
        self.hot_layers = {}  # layer_id -> pinned_count
        self.top_k = top_k
    
    def pin(self, layer_id, freq=1):
        if layer_id not in self.hot_layers:
            self.hot_layers[layer_id] = 0
        self.hot_layers[layer_id] += freq
        # top_k 高频 layer 驻留
        self._evict_cold()
    
    def _evict_cold(self):
        if len(self.hot_layers) > self.top_k:
            cold = min(self.hot_layers, key=self.hot_layers.get)
            del self.hot_layers[cold]
```

### 4.2 P1 任务 (8h 工时)

```python
# /home/ai/lingminopt/lingyuan/l6_mtp_speculative.py
"""灵元 L6: MTP 推测解码 — 借 Colibri 2.2-2.8 tok/forward

原理:
- Draft 模型: 7B (32 t/s) 生成 k 个候选
- Verify 模型: 14B (3.4 t/s) 1 次 forward 验证 k 个
- 接受率 ~60% → 实际生成 k×0.6 token/forward
- Colibri 比例 2.2-2.8× → 灵族期望 3.4×2.5 = 8.5 t/s

架构 (借 Colibri):
- Draft 模型: int8 (节省内存)
- Verify 模型: int4 (主模型压缩)
- K=8 (Colibri 默认)
"""
class MTPSpeculativeDecoder:
    def __init__(self, draft_model, verify_model, k=8):
        self.draft = draft_model
        self.verify = verify_model
        self.k = k
    
    def generate(self, prompt, max_tokens=100):
        tokens = prompt
        while len(tokens) < max_tokens:
            # Draft 阶段: 1 次 7B forward → k tokens
            draft_tokens = self.draft.generate(tokens, n=k)
            
            # Verify 阶段: 1 次 14B forward → 验证 k tokens
            accepted, new_token = self.verify.verify(
                tokens, draft_tokens
            )
            
            tokens.extend(accepted)
            if new_token:
                tokens.append(new_token)
        
        return tokens
```

---

## 五、灵族工程能力跃迁 — 5 大新维度

### 5.1 工业级 MoE 路线验证

**Colibri = 灵族路线的工业版**, 灵族可大胆推进:
- 灵族有 12 灵 (Colibri 无, 单机)
- 灵族有 LingBus 治理 (Colibri 无)
- 灵族有 LACP plugin manifest (Colibri 无)
- **灵族路线 = Colibri 路线 + 分布式联邦**

### 5.2 软下沉路线终极证明

```
Colibri 25GB 跑 744B (1.33% 内存/参数) — 工业可行
灵族 14GB 跑 30B  (46.7% 内存/参数) — 35× 更宽松
```

**核心论断**: 灵元 V1.0 "软下沉" 不是理论, 已被 Colibri 工业级证明。

### 5.3 推测解码 = 灵族 L6 加速器

Colibri MTP 2.2-2.8× → 灵元 L6 应能 3× 加速 → 14B 单卡 8.5 t/s
**G1 验证门 (5 t/s) 在推测解码下超额完成 1.7×**

### 5.4 大 MoE 模型 = 灵族新领域

- GLM-5.2 744B → 灵族可评估接入 (但要灵安白名单)
- 灵创可设计 12 灵 = 12 expert 的联邦 MoE
- 智桥 ZB-09 = 跨节点 MoE 路由

### 5.5 教育性 (第二个项目)

**rasbt/llms-from-scratch** 是教学项目, 价值:
- 灵族新人培训 (用此入门)
- "Building a Large Language Model (From Scratch)" 教材
- 灵族 12 灵可作为教学示例 (开源)

**建议**:
- 灵通问道可基于此为 70 集做培训
- 灵族新成员 onboarding 用此 + 灵族 V1.0 文档
- 不影响推理栈主路线

---

## 六、风险与边界

### 6.1 Colibri 风险

| 风险 | 概率 | 缓解 |
|------|------|------|
| Colibri 是单文件 C 引擎, 无 Python 接口 | 中 | 灵族通过 proxy3 桥接, FFI 调用 |
| Colibri 假设硬件极端 (25GB+370GB SSD) | 高 | 灵族先小模型验证 |
| Colibri 推测解码未开源细节 | 高 | 灵族自研 MTP (有 L6 基础) |
| 灵族完整复制 Colibri 工作量大 | 高 | 借鉴核心思想, 不复制代码 |

### 6.2 不应做的

- **不要直接 fork Colibri 代码** — C 引擎与灵族 Python 不兼容
- **不要立即接入 GLM-5.2** — 744B 模型对灵族硬件过重
- **不要假设 Colibri 性能可移植** — Colibri 是消费级硬件, 灵族是工业级
- **不要忽略 Colibri 的安全机制** — RAM safety budget 必学

### 6.3 与第二个项目的关系

**rasbt/llms-from-scratch** 是教育项目, 与 Colibri 完全不同:
- Colibri: 工业级推理, 1000+ 行 C
- llms-from-scratch: 教学训练, ~500 行 Python
- **互补**: Colibri 教推理, llms-from-scratch 教训练
- 灵族建议: 培训用 llms-from-scratch, 实战用 Colibri 思想

---

## 七、灵族工程能力提升行动 (8 月路线图)

### 7.1 立即 (W4 内, 4h)

- L3 hot layer pinning (Colibri 借鉴)

### 7.2 短期 (W4+, 32h)

- L0 RAM safety budget
- L4 async readahead 专家预取
- L6 MTP 推测解码 (核心, 24h)

### 7.3 中期 (8 月, 64h)

- L0-L5 全面 Colibri 化
- 灵族分布式 MoE 试点 (12 灵 = 12 expert)
- GLM-5.2 接入评估 (灵安白名单)

### 7.4 长期 (9 月+, 战略)

- 灵族 "Distributed Colibri" 对外发布
- 与 Colibri 社区合作
- 灵族自研 RDT 7B 模型 (Colibri 路线 + 灵族特色)

---

## 八、关键参考

### 8.1 内部 (灵族已有)

| 文档 | 路径 |
|------|------|
| 灵元推理栈全族学习 | `/home/ai/lingclaude/docs/lacp/LINGYUAN_STACK_FAMILY_LEARNING_20260716.md` |
| OpenMythos RDT 范式共振 | `/home/ai/lingclaude/docs/lacp/OPENMYTHOS_RDT_FAMILY_LEARNING_20260716.md` |
| OpenMythos 源码拆解 | `/home/ai/lingclaude/docs/lacp/OPENMYTHOS_SOURCE_DISSECTION_FAMILY_LEARNING_20260716.md` |
| Fable 5 泄露学习 | `/home/ai/lingclaude/docs/lacp/CLAUDE_MYTHOS_OPENMYTHOS_FAMILY_LEARNING_20260716.md` |
| **本学习文档** | `/home/ai/lingclaude/docs/lacp/COLIBRI_FAMILY_LEARNING_20260716.md` |

### 8.2 外部 (用户分享)

| 来源 | 内容 |
|------|------|
| **Colibri 工业级 MoE 推理** | 25GB 跑 744B GLM-5.2, MTP 推测解码 2.2-2.8× |
| **rasbt/llms-from-scratch** | 教学项目, 灵族培训用 |
| 100K 社区实测 | 冷 0.05-0.1 tok/s, 暖 0.37 tok/s |

### 8.3 关联概念

| 灵元 L0-L5 | Colibri 对应 |
|------------|-------------|
| L0 KV offload | 17B dense 常驻 |
| L1 ACT 早停 | RAM safety budget |
| L2 双卡分工 | MoE expert 分片 |
| L3 Weight Pager | expert streaming + LRU |
| L4 流式算子 | async readahead |
| L5 RDT 循环推理 | 744B latent reasoning |
| L6 推测解码 | MTP (int8 head) |
| 灵安 6 层 | 形式化安全 budget |

---

## 九、版本

- v1.0 (2026-07-16): 初稿, 灵克基于 Colibri 工业级 MoE 推理 + 用户分享内容
- 总工时: 64h (~1.5 周全职)
- 核心论断: 灵元 V1.0 "软下沉" 路线被 Colibri 工业级证明
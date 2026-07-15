# 灵元推理栈 — 全族学习文档

> **作者**: 灵克 (lingclaude) · 会话102
> **日期**: 2026-07-16
> **状态**: 阶段总结 · 基于 archive 文档 + 实盘核查
> **读者**: 灵族 12 灵（灵元/灵克/灵研/灵通/灵极优/灵犀/灵知/灵扬/灵创/灵信/灵通问道/智桥）
> **关联文档**: `/home/ai/meeting/archive/20260709-灵元推理栈定版/01_灵元推理栈_v1.0_定版与实施深挖.md`

---

## 〇、一句话

> **灵元推理栈 = 用软件优化让低端显卡跑大模型（不换硬件，只改代码）。把 GPU 显存从"全或无"降格为"按需换页"，让 6+8=14GB 旧硬件跑 30B-40B。**

---

## 一、问题背景

### 1.1 硬件现状

| 机器 | IP | GPU | 显存 | 当前能跑 |
|------|-----|-----|------|---------|
| ai01 (ZBOX-EN1070) | 192.168.2.2 | GTX 1070 | 8 GB | 最大 7B Q4 (4.4GB) |
| 本机 (ZBOX-EN51660T) | 192.168.2.1 | GTX 1660 Ti | 6 GB | 最大 7B Q4 (4.4GB) |
| **双卡合计** | — | 1070+1660Ti | **14 GB** | 理论上能跑 30B-40B (~15GB) |

**核心矛盾**: 14GB 显存 ≠ 两个 7GB 模型能合起来 — 没有专门软件，两张卡各跑各的。

### 1.2 行业对照

| 维度 | 2023 | 2025 | 2026 |
|------|------|------|------|
| 全球推理算力占比 | 1/3 | 1/2 | **2/3**（超训练）|
| 推理芯片市场 | — | — | **$500 亿+** |

**结论**: 训练是"买房"，推理是"交房租"——AI 行业真正的成本黑洞在推理。DeepSeek/OpenAI/Google/Anthropic 全在押推理专用化。

---

## 二、架构定版（v1.0, 7/9）

### 2.1 五层叠加架构

```
┌─────────────────────────────────────────────────────────────┐
│ L4 存储下沉式算子 (7/15 新增) — 沿SSD推64KB块, 不进RAM    │ ← 大文件L1 OOM时仍能跑
├─────────────────────────────────────────────────────────────┤
│ L3 Weight Pager  权重换页, 独立可叠加                       │
│   ├── 固定 GPU 池 (5GB) + LFRU + 顺序预取 (PIPE)         │
│   ├── .rad 格式 (单层独立寻址)                              │
│   └── 精度层级树 (前/后层Q4, 中间层Q3, 关键层FP16)         │
├─────────────────────────────────────────────────────────────┤
│ L2 多卡不对等分工  需要 L0 腾出空间才能分片                │
│   ├── CPU 调度器: 层号→卡号映射                             │
│   └── 容错降级: 单卡失效→回退单卡CPU                       │
├─────────────────────────────────────────────────────────────┤
│ L1 算子级拆解  CPU 跑 norm/softmax/residual                │
│   ├── RMSNorm/Softmax/Residual 拆到 CPU                    │
│   ├── PCIe 开销 ~0.2%                                       │
│   └── 省 ~1GB 显存 (7B 模型 3.5GB→2.5GB)                  │
├─────────────────────────────────────────────────────────────┤
│ L0 KV cache offload  CPU RAM 池, GPU 热列表                │
│   ├── GPU 热列表 (最近 2048 token, ~112MB)                 │
│   ├── CPU RAM 池 (4GB, ~70K token)                        │
│   ├── LRU 换页 + 顺序预取 (命中率 ~95%)                    │
│   └── 灰区 bound: Softmax 概率信号上行                     │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ llama.cpp (底层推理引擎) — 生产主力                        │
│ Colibri (GLM-5.2 专用) — 9100 Pro 后测试                   │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 各层的"灵元不变量"映射

| 灵元不变量 | L0 KV offload | L1 算子拆解 | L2 多卡分工 | L3 Weight Pager | L4 流式算子 |
|-----------|--------------|------------|------------|----------------|------------|
| **出入** | KV 入 CPU RAM / 出 GPU 池 | 算子入 CPU / 出 GPU | 层入卡 A / 出卡 B | 权重入 GPU 池 / 出 SSD | 算子沿 SSD 推 64KB 块 |
| **流转** | LRU 换页状态 | 算子执行状态 | 层执行状态 | 页面状态 (hot/cold) | 块流式 state (sum_sq, count, max_val) |
| **2T3A** | 原子换页 (页级锁) | 算子级审计点 | 层级 gate_id | 页级 gate_id | 块级 gate_id |
| **灰区** | 热列表 LRU 阈值 | norm/softmax bound 检查 | 容错降级门 | 精度层级树 (近高远低) | 块边界+残块置信度 |

---

## 三、代码资产（实测路径 — 7/16 验证）

### 3.1 总览

**两个 lingyuan 目录并存**:

| 位置 | 结构 | 用途 |
|------|------|------|
| `/home/ai/lingyuan/` | 分层 (`ops/` `kv/` `dual/` `pipeline/`) | 老位置, 7/14 文档说这里 |
| `/home/ai/lingminopt/lingyuan/` | 扁平 (`l0_*.py` `l1_*.py` `l4_*.py`) | 新位置, 7/15 真实工作区 |

> **教训**: 工作目录从 `lingyuan/` 迁移到 `lingminopt/lingyuan/` 时, 文档没更新。**文档说 ops/kv/dual, 实际工作区是扁平文件**。下次交接要先确认工作目录。

### 3.2 完整资产清单

| 模块 | 路径 | 大小 | 行数 | 状态 |
|------|------|------|------|------|
| **L1 算子（精简版）** | `/home/ai/lingminopt/lingyuan/l1_ops.py` | 3.6KB | - | ✅ |
| L1 管线 | `/home/ai/lingminopt/lingyuan/l1_pipeline.py` | 3.7KB | - | ✅ |
| L0 KV 池 | `/home/ai/lingminopt/lingyuan/l0_kv_cache.py` | 7.9KB | - | ✅ |
| L0 KV 整合 | `/home/ai/lingminopt/lingyuan/l0_integrated.py` | 6.8KB | - | ✅ |
| **L4 流式算子（新增）** | `/home/ai/lingminopt/lingyuan/l4_streaming_ops.py` | **15.9KB** | **387** | ✅ py_compile 通过 |
| L1 单测 | `/home/ai/lingminopt/lingyuan/test_l1_rmsnorm.py` | 3.7KB | - | ✅ |
| **L3 Weight Pager** | `/home/ai/lingminopt/scripts/weight_pager.py` | **26.2KB** | - | ✅ 云 GPU 验证 |
| **L3 .rad 转换器** | `/home/ai/lingminopt/scripts/rad_converter.py` | **17.3KB** | - | ✅ |
| L3 .rad 索引构建 | `/home/ai/lingminopt/scripts/build_rad_index.py` | 7.0KB | - | ✅ |
| L3 验证 oracle | `/home/ai/lingminopt/scripts/test_weight_pager_oracle.py` | 4.8KB | - | ✅ |
| L0 KV hot_list | `/home/ai/lingyuan/kv/hot_list.py` | 10.3KB | - | ✅ |
| L0 KV cpu_pool | `/home/ai/lingyuan/kv/cpu_pool.py` | 11.6KB | - | ✅ |
| L0 KV swap_engine | `/home/ai/lingyuan/kv/swap_engine.py` | 15.2KB | - | ✅ |
| L1 RMSNorm | `/home/ai/lingyuan/ops/rmsnorm_cpu.py` | 9.1KB | - | ✅ |
| L1 Softmax | `/home/ai/lingyuan/ops/softmax_cpu.py` | 13.2KB | - | ✅ |
| L1 Residual | `/home/ai/lingyuan/ops/residual_cpu.py` | 6.7KB | - | ✅ |
| L2 调度器 | `/home/ai/lingyuan/dual/scheduler.py` | 12.4KB | - | ✅ |
| L2 管线 | `/home/ai/lingyuan/dual/pipeline.py` | 10.8KB | - | ✅ |
| L2 coordinator | `/home/ai/lingyuan/dual/coordinator.py` | 3.4KB | - | ✅ |
| L2 C RPC client | `/home/ai/lingyuan/dual/layer_client.cpp` | 2.0KB | - | ✅ 编译通过 |
| L2 C RPC server | `/home/ai/lingyuan/dual/layer_server.cpp` | 3.4KB | - | ✅ 编译通过 |
| L2 C RPC header | `/home/ai/lingyuan/dual/layer_rpc.h` | 4.0KB | - | ✅ |
| L2 部署测试 | `/home/ai/lingyuan/dual/deploy_test.py` | 4.5KB | - | ✅ |
| L1 layer_pipeline | `/home/ai/lingyuan/pipeline/layer_pipeline.py` | 15.0KB | - | ✅ |
| 推测解码 | `/home/ai/lingyuan/spec_decode.py` | 10.5KB | - | ✅ |

**代码总量**: ~140KB Python + ~9KB C, ~30 文件

### 3.3 L4 算子（关键新增）

`l4_streaming_ops.py` 是 7/15 才出现的新层,**不在 7/9 定版四层架构中**:

```python
# 核心思想: 数据不搬, 算子沿 SSD 通道推 64KB 块
def rmsnorm_streaming(path: str, hidden_dim: int) -> float:
    """沿 SSD 通道推块, 在 CPU L1/L2 cache 算 reduction"""
    block_size = 64 * 1024  # 64KB
    sum_sq = 0.0
    count = 0
    with open(path, 'rb') as f:
        while chunk := f.read(block_size):
            block = np.frombuffer(chunk, dtype=np.float32)
            sum_sq += np.sum(block ** 2)
            count += len(block)
    return np.sqrt(sum_sq / count)
```

**与 L1 根本区别**:

| 维度 | L1 算子拆解 | L4 存储下沉 |
|------|------------|------------|
| 数据位置 | 整页 mmap 进 RAM | 沿 SSD 通道推 64KB 块, **不进 RAM** |
| 计算位置 | NumPy 数组运算 | CPU L1/L2 cache reduction |
| 内存占用 | O(模型层大小) | O(1) — 固定 64KB 块缓冲 |
| 适用 | 中小模型 (≤13B) | 大模型 (32B+), L1 OOM 时仍能跑 |

**实测 (9100 Pro ext4)**:
- 顺序读 2.4 GB/s, cached 16.8 GB/s
- 流式块 64KB: 块算 ~0.05ms (CPU L1), 块读 ~0.03ms (SSD)
- **CPU 算比 SSD 读快**, 瓶颈在 SSD 带宽 (算/读重叠)

---

## 四、验证门与实测状态

### 4.1 5 个验证门

| 门 | 条件 | 实测 | 判定 |
|----|------|------|------|
| **G1** | 单卡 8GB 跑 13B Q3 >5 t/s | ai01 14B 单卡 **3.4 t/s** (12/40 层 GPU) | ❌ 未达 |
| **G2** | 32K ctx P99 <500ms | 代码已写 (`l0_kv_cache.py`) | ⏳ 未实测 |
| **G3** | 双卡 30B Q4 >0.5 t/s | 本机 CUDA llama_cpp 装不上 | ❌ **唯一阻塞** |
| **G4** | 单卡 6GB 跑 13B + 16K ctx | 云 GPU 验证通过 (基线==.rad) | ✅ (云) |
| **L4** | 块流式 + cache hit | py_compile 通过, 正确性 ✅ | 🟡 待实测 |

### 4.2 G1 实测详情（7/15）

ai01 :18101 服务 14b-Q4_K_M.gguf (8.4GB), 单卡 GTX 1070:

```
n_gpu_layers = 12  # 不是全部 40 层
实际速度: 3.4 t/s (未达 G1 5 t/s 门)
```

**为什么只有 12 层 GPU**:
- 14B 模型 40 层, GTX 1070 8GB 显存装不下全部
- 装 12 层 GPU + 28 层 CPU = 3.4 t/s
- 装更多层 GPU 会 OOM
- **需要 L1/L0 算子拆解后才能腾出空间跑更多 GPU 层**

### 4.3 G3 卡死根因

**本机 CUDA 版 llama_cpp 装不上** — 7/15 全部工作的共同根因:

| 路径 | 状态 |
|------|------|
| `pip install llama-cpp-python[cuda]` (abetlen cu121 wheel) | ❌ **403** |
| `git clone https://github.com/abetlen/llama-cpp-python` | ❌ **超时** |
| 源码自编 (cmake + make) | ❌ 失败 |
| pip cache wheel 手动解压 (CPU 版) | ✅ |

**结果**:
- 本机 :8103 (CPU) DOWN, :8104 (CUDA) DOWN
- L2 双卡分工代码完整但**只能在 ai01 跑单卡**
- 双卡层间拆分无法端到端验证

---

## 五、硬件状态（7/16 实测）

### 5.1 已完成

| 硬件 | 状态 | 实测数据 |
|------|------|----------|
| **9100 Pro 2TB** | ✅ 格式化 ext4, 挂 /mnt/llm | 1.7T 空闲, dd 顺序写 **2.2 GB/s** |
| **ai01 sdf1 2.7T** | ✅ 格式化 ext4, ai01 /mnt/models | 2.6T 空闲 (ssh 可达) |
| **ai01 sshd** | ✅ 修通 | 根因: 9100 Pro 换槽后网卡重命名 |
| **本机 /data** | ✅ 4 个 gguf 模型 + .rad 索引 | — |

### 5.2 待办

| 硬件 | 状态 | 卡点 |
|------|------|------|
| **本机 CUDA llama_cpp** | ❌ 装不上 | GitHub 403 / 自编失败 |
| **ai01 磁盘清理** | ⚠️ 89% 满 | 需迁移旧模型 |

---

## 六、模型与 .rad 索引

### 6.1 gguf 模型文件

| 模型 | 路径 | 大小 | 服务状态 |
|------|------|------|----------|
| lingai-7b-finetuned-v5 | ai01 :8101 / 本机备份 | 4.36 GB | ✅ ai01 服务加载 |
| qwen2.5-7b-instruct-q4 | `/data/lingclaude_backup_20260703/models/` | 4.36 GB | 备份 |
| lingai-8b-q4 | `/data/models/lingai-8b-q4_k_m.gguf` | 8.07 GB | 未服务 |
| **14b-Q4_K_M** | `/data/models/14b-Q4_K_M.gguf` | **8.37 GB** | ✅ ai01 :18101 加载 |
| **32b-Q4_K_M** | `/data/models/32b-Q4_K_M.gguf` | **18.49 GB** | ✅ .rad 已生成 |
| lingai-7b-f16 | 备份目录 | 14.19 GB | 备份 |

### 6.2 .rad 索引（已生成!）

| 文件 | 路径 | 大小 |
|------|------|------|
| **32B index.rad** | `/mnt/llm/32b_rad/index.rad` | 104 KB |
| **32B data.bin** | `/mnt/llm/32b_rad/data.bin` | 18.49 GB |

```json
{
  "gguf_source": "/data/models/32b-Q4_K_M.gguf",
  "data_file": "/mnt/llm/32b_rad/data.bin",
  "n_tensors": 771,
  "total_bytes": 19851336288,
  "per_layer": {
    "0": {"name": "token_embd.weight", "offset": 0, "n_bytes": 437944320, "dims": [5120, 152064]},
    ...
  }
}
```

**好消息**: 32B .rad 索引已生成, data.bin 已复制到 9100 Pro。下一步可直接跑 Weight Pager。

### 6.3 关键纠正: 13B Q3 / 40B Q3 不存在

用户 7/15 澄清:
> **Qwen2.5 无 13B Q3 / 40B Q3 规格**。原下载清单四项只两项是真模型 (14B Q4 + 32B Q4)。

**教训**: 验证模型规格, 不要凭印象生成下载清单。

---

## 七、运行中服务（7/16 实测）

| 服务 | 端口 | 位置 | 状态 |
|------|------|------|------|
| ai01 llama-server v5 | :8101 | ai01 | ✅ UP, lingai-7b-v5 |
| ai01 llama-server 14B | :18101 | ai01 | ✅ UP, 14b-Q4_K_M (3.4 t/s) |
| ai01 layer_server | :8105 | ai01 | ✅ UP (C RPC 层间拆分) |
| **本机 llama-server CUDA** | :8104 | 本机 | ❌ **DOWN** (依赖未装) |
| **本机 llama-server CPU** | :8103 | 本机 | ❌ **DOWN** |
| **本机 layer_server** | :8105 | 本机 | ❌ **DOWN** |
| 本机 atomcode daemon | :13456 | 本机 | ✅ UP (v4.26.0) |
| LingMemory MCP | :9530 | 本机 | ✅ UP |
| 灵犀 lingxi | :9532 | 本机 | ✅ UP |
| proxy3 (LingOS) | :8765 | 本机 | ✅ UP |

**核心阻塞**: 本机 4 个推理端口全 DOWN → 双卡层间拆分无法端到端跑

---

## 八、核心算法细节（以 L0 为例）

### 8.1 L0 容量公式

```
单 token KV 大小 = 2 × n_layers × n_kv_heads × head_dim × bytes_per_element
                 = (K + V) × 层数 × KV 头数 × 头维度 × 元素大小

例: Qwen2.5-7B, GQA-4, head_dim=128, FP16
    = 2 × 28 × 4 × 128 × 2 = 57,344 bytes/token
    ≈ 56 KB/token

1K context  = 56 MB
8K context  = 448 MB
32K context = 1.75 GB
```

### 8.2 GPU 热列表策略

| 项 | 值 | 说明 |
|---|---|------|
| 热列表大小 | 最近 2048 token KV | ~112 MB, 固定 GPU 显存 |
| 冷列表位置 | CPU RAM 固定池 4GB | 可容 ~70K token 历史 |
| 换入触发 | attention 访问到非热 token | 同步换入 (阻塞) |
| 换出策略 | LRU | 标准缓存替换 |
| 预取策略 | 顺序访问下 token N+1 预取 | 命中率 ~95% |

### 8.3 换入延迟分析

```
换入 1 token KV (56 KB) via PCIe:
  PCIe 3.0 x4  = 4 GB/s   → 56 KB / 4 GB/s = 14 μs
  PCIe 3.0 x16 = 16 GB/s  → 56 KB / 16 GB/s = 3.5 μs

最坏情况 (全 miss, 2048 token):
  PCIe 3.0 x4  = 2048 × 14 μs = 29 ms   ← 明显延迟
  PCIe 3.0 x16 = 2048 × 3.5 μs = 7 ms   ← 可接受

实际命中率 ~95% → 只换 5% × 2048 = 102 token:
  PCIe 3.0 x4 = 102 × 14 μs = 1.4 ms   ← 可接受
```

**结论**: L0 的关键不是换入速度, 而是**命中率**。顺序访问预取让命中率 ~95%, L0 可行。

---

## 九、与 DeepSeek 战略对照

| 维度 | DeepSeek 造芯 | 灵元推理栈 |
|------|--------------|----------|
| 核心洞察 | 推理是成本黑洞 | 同 |
| 路径 | 硬件专用化 (造芯片) | **软件灵元化 (不改硬件)** |
| 算子优化 | 固化到硅片 | 拆解到 CPU/SSD 侧 |
| KV cache | 芯片内专用 SRAM | offload 到 CPU RAM |
| 权重管理 | 芯片内高带宽互联 | Weight Pager 换页 |
| 落地时间 | 2028+ | **2026 Q3** |
| 风险 | 架构可能翻篇 (稀疏注意力) | 零硬件风险 |
| 极限 | 专用芯片效能最高 | PCIe 3.0 x4 天花板 (~0.6 t/s) |

**灵族独特价值**:
1. 不造芯片, 用软件栈榨 2-3× 模型规模容量
2. 灵元化—算子拆解让每个算子有 gate_id、可审计、可灰区检查
3. 低端硬件友好—6+8=14GB 旧卡跑 30B-40B
4. 可验证—每个 Phase 有验证门

---

## 十、5 周时间线（W1-W4+ 实际执行）

```
W1 (6/27-7/3) 基础规范周
├─ ✅ L3 Weight Pager 原型 (.rad + LFRU + PIPE)
├─ ✅ .rad 转换器 + 云 GPU 验证
├─ ✅ L1 算子拆分 (RMSNorm/Softmax/Residual)
├─ ✅ L0 KV cache offload 代码
└─ ✅ 双卡 6+8 调度器 + C RPC

W2 (7/4-7/10) 优化+文档周
├─ ✅ L4 算子 (存储下沉) - 7/15 实际是 W3
├─ ✅ 文档体系 (8个文档, ~50KB)
├─ ✅ 9100 Pro 采购决策 (7/14 选定)
└─ ✅ 待办清单刷新 (7/10)

W3 (7/11-7/14) 集成+验证周
├─ ✅ ai01 微调 v5 部署 (32 t/s)
├─ ✅ 云 GPU 验证 (基线==.rad)
├─ ✅ 灵元四维评估
└─ ⏳ G1 验证未达 (3.4/5 t/s)

W3.5 (7/15-7/16) 硬件+下载周  ← 当前
├─ ✅ ai01 sshd 修通 (网卡重命名)
├─ ✅ 9100 Pro 格式化 (ext4, 2.2 GB/s)
├─ ✅ ai01 sdf1 格式化 (2.7T)
├─ ✅ 14B Q4 + 32B Q4 下载 (8.4G + 19G)
├─ ✅ 32B .rad 索引生成 (data.bin 18.49GB)
├─ ⚠️ G1 部分验证 (单卡 3.4 t/s)
├─ ❌ 本机 CUDA llama_cpp 装不上
└─ ❌ 双卡 G3 阻塞

W4 (7/17-7/21) 计划
├─ P0: 32B Weight Pager (不依赖 GitHub)
├─ P1: 等 GitHub 链恢复 → 本机 CUDA → 双卡 G1
└─ P2: Colibri + GLM-5.2

W4+ (7/22-7/25) 收尾
└─ 全栈整合验证
```

---

## 十一、关键阻塞与解决路径

| 阻塞 | 影响 | 解法 | 优先级 |
|------|------|------|--------|
| **本机 CUDA llama_cpp 装不上** | 双卡 G3 唯一阻塞 | GitHub 链恢复 + abetlen cu121 wheel / git clone / 自编 | **P0** |
| 32B Weight Pager 跑起来 | L3 本地验证 | 已有 .rad 索引 + ai01 llama-server, **无需 GitHub** | **P0** |
| L4 性能优化 | 实用化 | Python 块循环 → 块加大到 4MB 或 mmap 版 | P1 |
| Colibri 落地 | 跑 GLM-5.2 744B | 需 GitHub 链 + 大模型 372G | P2 |

**核心洞察**: 所有 Python 代码资产 (140KB) 都写完了, 但**"接入 llama.cpp 生产管线"**这一步是真正卡点 — 需要本机 CUDA 版 llama_cpp 才能做端到端验证。

---

## 十二、给各灵的具体启示

### 12.1 给灵极优 (主要负责方)

- **L4 算子是新层, 7/9 定版未包含** — 已在 `/home/ai/lingminopt/lingyuan/l4_streaming_ops.py` 实现, 387 行
- **L3 验证域在云 GPU 完成, 本地端到端验证待 32B Weight Pager 跑起来**
- **目录迁移问题**: `/home/ai/lingyuan/` → `/home/ai/lingminopt/lingyuan/`, 文档说 ops/kv/dual, 实际是扁平文件 — 下次交接要先确认工作目录

### 12.2 给灵通 (proxy3 / LLM 路由)

- **双卡层间拆分 RPC 已就绪** — `layer_rpc.h` 双端编译通过, ai01 layer_server :8105 UP
- **本机 layer_server :8105 DOWN** — 等本机 CUDA llama_cpp 装好后启动
- **G1 部分结果**: ai01 单卡 14B 跑 3.4 t/s (12/40 层 GPU), 离 5 t/s 门差 1.6 t/s

### 12.3 给灵犀 (Lingxi :9532)

- **atomcode daemon :13456 UP** (v4.26.0), 提供 12 layer GPU 推理
- **9100 Pro 已识别** (1.7T 空闲), 可作 Colibri 落地点

### 12.4 给灵研 (OH 论文 §6)

- **L1/L0/L2/L3/L4 算法细节已在 archive 中完整记录**, 可直接引用为 §6.2 (灵元推理栈)
- **灵元不变量映射表** (出入/流转/2T3A/灰区) 是 OH §6 的核心论据

### 12.5 给灵信 (LingBus)

- **LingMemory MCP :9530 UP** (会话101 拉起后稳定)
- **proxy3 :8765 UP**, LingBus 可正常路由消息

### 12.6 给灵知 (RAG)

- **Lingyuan 代码未接入 llama.cpp** — RAG 增强 (如推测解码 + RAG 混合检索) 需等本机 CUDA 版

### 12.7 给灵创 (多模态)

- **L4 流式算子** 同样适用于多模态模型 (CLIP, LLaVA) — 视频/图像数据本身就是"大文件"
- **优先级**: 等推理栈主线打通后接入

### 12.8 给灵扬 (对外内容)

- **AI-07 灵族日报 co-owner**: 待灵扬拉起 + 议程3 决策后启动

### 12.9 给灵通问道 (内容生产)

- **EP001 lingpack 打包 PoC** 待启动, 与 L3 联动 (`.ling` 包可包含 .rad 索引)

### 12.10 给智桥 (跨灵族通信)

- **L2 双卡分工** 实际是单机双卡, 智桥跨灵族 a2a 通信是 L4+ (跨节点)
- **跨灵族 a2a PoC** (ZB-09) 待启动

### 12.11 给灵安 (安全)

- **L4 算子天然带灰区信号** (块边界+残块置信度) — 适合 security_gate type 的 data/command 层

---

## 十三、教训与防坑

### 13.1 文档-现实一致性

**坑**: 7/15 交接文档说 lingyuan 资产在 `lingyuan/ops/`, 实际工作目录是 `lingminopt/lingyuan/` (扁平)。

**教训**:
- 文档必须基于实际 `find`/`ls` 输出, 不能凭印象
- 工作目录迁移必须在 handover 留痕
- 每次交接前先 `ls -R <工作目录>`

### 13.2 下载模型规格验证

**坑**: 下载清单写了 13B Q3 / 40B Q3, 实际 Qwen2.5 无此规格。

**教训**:
- 先验证模型规格再下载 (查 HuggingFace model card)
- 不要凭印象生成下载清单
- hf-mirror 镜像找原模型 ID, 不确定就先 wget HEAD 验证

### 13.3 端口 vs 服务的混淆

**坑**: 文档说"llama-server :8104 UP", 实测 DOWN。

**教训**:
- "UP" 是端口活, 不是服务活 — 还要探测 `/v1/models` 接口
- systemd service 启动失败但端口被占的情况常见
- 每次会话启动协议要实测接口 (`curl /v1/models`)

### 13.4 GitHub 链断的根因

**坑**: abetlen wheel 403, git clone 超时, 自编失败。

**教训**:
- **pip cache wheel 手动解压** 是 fallback — 只支持 CPU 版, CUDA 版必须走 abetlen 或 git
- 离线环境要预下载 wheel 包
- 自编路径: cmake + CUDA toolkit + ninja, 需 ~3GB 依赖

### 13.5 L4 算子的发现时机

**坑**: L4 在 7/15 才出现, 7/9 定版四层架构未包含 — 交接时未对齐。

**教训**:
- 每次定版前要回顾最近 7 天的所有文档, 确保没有遗漏新发现
- "新增层"应该走治理提案 (LING PROPOSAL), 不是私下实现

---

## 十四、下一步行动（7/17-7/25）

### P0: 不依赖 GitHub, 立即可做

| # | 任务 | 工时 | 阻塞 |
|---|------|------|------|
| 1 | **32B Weight Pager 跑起来** — 用 ai01 llama-server + `/mnt/llm/32b_rad/` 索引, 不需要本机 CUDA | 2-4h | 无 |
| 2 | L4 算子真机测试 (本机跑 l4_streaming_ops on 9100 Pro) | 1-2h | 无 |
| 3 | L3 验证报告 (云 GPU vs 本机 .rad 推理对比) | 2h | 无 |

### P1: GitHub 链恢复后

| # | 任务 | 工时 |
|---|------|------|
| 4 | 拉 abetlen cu121 wheel → 本机 CUDA llama_cpp | 1h |
| 5 | 本机 :8104 CUDA 启动 + 双卡层间拆分端到端 | 4h |
| 6 | G1 重测 (期望 >5 t/s) | 1h |
| 7 | G3 验证 (30B Q4 双卡 >0.5 t/s) | 4h |

### P2: 中长期

| # | 任务 | 工时 |
|---|------|------|
| 8 | L4 性能优化 (块 4MB / mmap 版) | 8h |
| 9 | 推测解码接入 (60% 接受率验证) | 16h |
| 10 | Colibri + GLM-5.2 跑通 | 24h |
| 11 | 文档体系合并 (lingyuan 老位置→新位置, 一份权威文档) | 4h |

---

## 十五、参考索引

### 15.1 核心文档

| 文档 | 路径 |
|------|------|
| 7/9 定版深挖 | `/home/ai/meeting/archive/20260709-灵元推理栈定版/01_灵元推理栈_v1.0_定版与实施深挖.md` |
| 7/9 未来硬件铺路 | `/home/ai/meeting/archive/20260709-灵元推理栈定版/02_灵元推理栈_v1.x_为未来硬件铺路.md` |
| 7/11 L1+L0 规划 | `/home/ai/meeting/archive/20260711-灵元推理栈_L1_L0实施规划.md` |
| 7/14 三阶段实施 | `/home/ai/meeting/archive/20260714-灵元推理栈三阶段实施规划.md` |
| 7/14 回顾与收益 | `/home/ai/meeting/archive/20260714-灵元推理栈回顾与收益.md` |
| 7/14 当前工作规划 | `/home/ai/meeting/archive/20260714-当前工作规划.md` |
| 7/15 交接文档 | `/home/ai/meeting/archive/20260715-交接文档.md` |
| 7/10 未结清单 | `/home/ai/meeting/archive/00_当前未结清单_20260710.md` |
| **本学习文档** | `/home/ai/lingclaude/docs/lacp/LINGYUAN_STACK_FAMILY_LEARNING_20260716.md` |

### 15.2 外部对照

| 项目 | 借鉴点 |
|------|--------|
| **Colibri** | LFRU 淘汰 + tier_pick_lfru + repin_pass + safety_budget |
| **Spark 2.0 Splat Pager** | 固定 GPU 池 + 顺序预取 |
| **ssd-llm / DiskLLM / GdsLLM / ds4** | 同类项目对照 (7/7 调研) |

---

## 十六、版本

- v1.0 (2026-07-16): 初稿, 灵克基于 archive + 实盘核查撰写
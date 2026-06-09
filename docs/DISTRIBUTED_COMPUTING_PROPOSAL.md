# 灵族分布式计算架构方案

> **版本**: v1.0-draft
> **日期**: 2026-05-13
> **作者**: 灵克 #2
> **状态**: ✅ 已批准（灵通+、灵研评审通过，2026-05-13）
> **目标**: 将 ai01 (GTX 1070 8GB, 32GB RAM) 纳入灵族分布式计算体系

---

## 一、现状

### 1.1 硬件集群

| 节点 | GPU | VRAM | RAM | IP (直连) | 角色 | 当前负载 |
|------|-----|------|-----|-----------|------|---------|
| zhineng-ai | GTX 1660 Ti | 6GB | 32GB | 192.168.2.1 | 主力（全部服务） | 高（18个容器+进程） |
| ai01 | **GTX 1070** | **8GB** | **32GB** | 192.168.2.2 | 空闲 | **GPU 0%** |

ai01 已通过千兆直连接入，DDP + Ray 曾配置过（cluster.yaml 记录），但当前完全空闲。

### 1.2 已有基础设施

| 组件 | 状态 | 端口/位置 | 说明 |
|------|------|----------|------|
| Redis 7 | ✅ 运行中 | 6381 | 灵知 Docker 容器，可用作消息队列 |
| PostgreSQL 16 + pgvector | ✅ 运行中 | 5436 | 灵知 Docker 容器 |
| Docker | ✅ 运行中 | — | 13个容器活跃 |
| systemd 模板 | ✅ 成熟 | `infra/ling-template.service` | 防重启风暴规范 |
| 端口注册表 | ✅ 活跃 | `infra/PORT_REGISTRY.md` | 一端口一主人 |
| PyTorch | ✅ 已安装 | torch 2.11 | ai01 本机 |
| 灵通 cluster.yaml | ✅ 存在 | `lingflow/config/cluster.yaml` | ai01 已注册为 distributed_compute |

### 1.3 未被利用的能力

ai01 的 GTX 1070 8GB 可以：

| 用途 | 可行性 | 预估吞吐 |
|------|--------|---------|
| Embedding 批处理 (bge-small-zh) | ✅ 8GB 绑绑有余 | ~1000 chunks/s |
| BERT 微调 (tiny/small) | ✅ 4GB以内 | — |
| 7B 模型 INT4 推理 | ✅ ~4GB VRAM | ~15 tok/s |
| 1.5B 模型 FP16 推理 | ✅ ~3GB VRAM | ~50 tok/s |
| Qwen2-7B QLoRA 微调 | ⚠️ 勉强（需梯度卸载） | 训练慢但可跑 |
| 数据清洗/ETL | ✅ CPU 即可 | 32GB RAM 充裕 |

---

## 二、架构选型

### 2.1 方案对比

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| Celery + Redis | 成熟，灵通已有 docker-compose 设计 | 重，需额外 worker 进程管理 | 备选 |
| Ray | 已在 cluster.yaml 中提及 | 重量级，ai01 单 GPU 无需集群调度 | 过重 |
| **Redis 队列 + 自定义 Worker** | 轻量，Redis 已运行，无需新依赖 | 需自建任务协议 | **推荐** |
| Dask | Pythonic | ai01 单节点收益不大 | 不选 |

### 2.2 推荐：Redis 队列 + 自定义 Worker

**理由**：
1. Redis 6381 已运行，零额外部署
2. 灵族当前只有 2 个计算节点，不需要复杂调度
3. PyTorch/torch 已安装，任务直接 GPU 执行
4. 自定义协议可精确控制 GPU 内存（GTX 1070 只有 8GB）

---

## 三、架构设计

### 3.1 整体拓扑

```
┌─────────────────────────────────────────────────────────┐
│                  zhineng-ai (主节点)                      │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │ 灵知     │  │ 灵研     │  │ 灵通     │  │ 灵克    │ │
│  │ embedding│  │ 数据清洗 │  │ 任务编排 │  │ 审计    │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬────┘ │
│       │              │              │              │      │
│       └──────────────┴──────┬───────┴──────────────┘     │
│                             │                            │
│                    ┌────────▼────────┐                   │
│                    │  Redis 6381     │                   │
│                    │  (任务队列)      │                   │
│                    │                 │                   │
│                    │  queue:embedding│                   │
│                    │  queue:inference│                   │
│                    │  queue:etl      │                   │
│                    │  queue:training │                   │
│                    └────────┬────────┘                   │
│                             │ 千兆直连                    │
├─────────────────────────────┼────────────────────────────┤
│                  ai01 (计算节点)                           │
│                             │                            │
│                    ┌────────▼────────┐                   │
│                    │  ling-worker     │                   │
│                    │  (GPU Worker)    │                   │
│                    │                 │                   │
│                    │  ┌─────────────┐│                   │
│                    │  │ GPU Mem Mgr ││  ← 8GB 显存管理    │
│                    │  └─────────────┘│                   │
│                    │  ┌─────────────┐│                   │
│                    │  │ Task Router ││  ← 按 GPU 需求路由 │
│                    │  └─────────────┘│                   │
│                    └─────────────────┘                   │
│                                                          │
│  ┌──────────────────────────────────────────┐            │
│  │            GTX 1070 8GB                   │            │
│  │  Model Slot A (max 6GB): 推理/Embedding   │            │
│  │  Model Slot B (max 2GB): 小模型/临时       │            │
│  └──────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────┘
```

### 3.2 核心组件

#### 3.2.1 ling-worker（ai01 上的常驻 Worker 进程）

```python
# lingworker/worker.py 核心逻辑

class GPUWorker:
    """ai01 上的 GPU 任务执行器"""

    def __init__(self):
        self.redis = Redis("192.168.2.1", 6381)
        self.gpu_memory = GPUMemoryManager(total_vram=8192)  # 8GB
        self.handlers = {
            "embedding": EmbeddingHandler(max_vram=2048),
            "inference": InferenceHandler(max_vram=4096),
            "etl": ETLHandler(),  # CPU only
            "training": TrainingHandler(max_vram=6144),
        }

    def run(self):
        while True:
            task = self.redis.brpop(
                ["queue:embedding", "queue:inference",
                 "queue:etl", "queue:training"],
                timeout=30
            )
            if task:
                self.execute(task)

    def execute(self, task):
        handler = self.handlers[task.type]
        if handler.requires_gpu:
            if not self.gpu_memory.reserve(handler.vram_needed):
                # 放回队列，等 GPU 释放
                self.redis.lpush(f"queue:{task.type}", task.raw)
                return
        try:
            result = handler.run(task.payload)
            self.redis.set(f"result:{task.id}", json.dumps(result))
        finally:
            self.gpu_memory.release(handler.vram_needed)
```

#### 3.2.2 任务协议

```json
{
  "task_id": "emb_20260513_001",
  "type": "embedding",
  "payload": {
    "model": "BAAI/bge-small-zh-v1.5",
    "texts": ["chunk1...", "chunk2..."],
    "batch_size": 64,
    "normalize": true
  },
  "callback": {
    "type": "redis",
    "key": "result:emb_20260513_001"
  },
  "priority": 1,
  "timeout": 300,
  "submitter": "lingzhi",
  "submitted_at": "2026-05-13T08:00:00Z"
}
```

#### 3.2.3 GPU 内存管理

GTX 1070 只有 8GB，必须严格管理：

```
┌─────────────────────────────────────┐
│          8GB VRAM 分配策略           │
├─────────────────────────────────────┤
│                                     │
│  优先级1: 推理/Embedding (常驻)      │
│  ████████████████░░░░  4-6GB       │
│                                     │
│  优先级2: 小模型/临时 (按需)         │
│  ░░░░████░░░░░░░░░░░░  1-2GB       │
│                                     │
│  系统保留:                           │
│  ░░░░░░░░░░░░░░░░████  ~1GB        │
│                                     │
│  规则:                               │
│  - 同一时间只有一个活跃 GPU 任务      │
│  - 训练任务独占 GPU，完成后释放       │
│  - Embedding 批处理可中断让位推理     │
│  - OOM 时自动放回队列 + 告警         │
└─────────────────────────────────────┘
```

### 3.3 任务类型与优先级

| 队列 | 优先级 | GPU需求 | 典型来源 | 说明 |
|------|--------|---------|---------|------|
| `queue:embedding` | 高 | 2GB | 灵知 | 批量 embedding 灵知九域知识库 |
| `queue:inference` | 中 | 4GB | 灵研/灵克 | 7B 模型推理、分类 |
| `queue:etl` | 低 | 0 (CPU) | 灵研/灵知 | 数据清洗、去重、格式转换 |
| `queue:training` | 低 | 6GB | 灵研 | 微调训练（夜间独占） |

### 3.4 端口规划

| 端口 | 服务 | 节点 | 说明 |
|------|------|------|------|
| 8950 | ling-worker HTTP API | ai01 | Worker 状态查询、手动任务提交 |
| 8951 | ling-worker Metrics | ai01 | Prometheus 格式指标 |

---

## 四、实施计划

### Phase 1: 最小可用（3天）

**目标**：ai01 能接收并执行 embedding 任务

| 步骤 | 内容 | 负责 |
|------|------|------|
| 1 | 在 ai01 创建 `/home/ai/lingworker/` 项目 | 灵克 |
| 2 | 实现 `worker.py`（Redis 队列消费 + GPU 任务执行） | 灵克 |
| 3 | 实现 `handlers/embedding.py`（bge-small-zh 批量 embedding） | 灵克 |
| 4 | 实现 `gpu_manager.py`（VRAM 分配追踪） | 灵克 |
| 5 | 创建 systemd service（符合灵族模板规范） | 灵克 |
| 6 | 注册端口 8950/8951 | 灵克 |
| 7 | 灵知接入：embedding 服务调用改为投递任务到 Redis | 灵知（或灵克代实现） |

**验收标准**：
- 灵知提交 1000 chunks embedding 任务 → ai01 GPU 执行 → 结果写回 Redis
- Worker 进程 systemd 托管，符合防重启风暴规范
- GPU 内存使用可观测（metrics 端点）

### Phase 2: 扩展任务类型（+3天）

| 步骤 | 内容 |
|------|------|
| 1 | 实现 `handlers/inference.py`（7B INT4 推理） |
| 2 | 实现 `handlers/etl.py`（CPU 数据处理） |
| 3 | 实现 `handlers/training.py`（QLoRA 微调，夜间调度） |
| 4 | 任务优先级调度（embedding > inference > etl） |
| 5 | Web Dashboard（worker 状态、任务队列、GPU 利用率） |

### Phase 3: 生产化（+3天）

| 步骤 | 内容 |
|------|------|
| 1 | 任务超时与重试机制 |
| 2 | GPU OOM 自动恢复 |
| 3 | Prometheus 指标导出 |
| 4 | 灵通工作流集成（灵通编排 → 投递任务 → 收集结果） |
| 5 | 完整测试覆盖 |

---

## 五、灵知 Embedding 覆盖率提升方案

这是最紧迫的用例。灵知九域 Embedding 覆盖率仅 5.5%。

### 5.1 规模估算

| 域 | Chunks 数量 | 当前已嵌入 | 待嵌入 |
|----|-----------|-----------|--------|
| 气功 | ~200K | ~12K | ~188K |
| 中医 | ~500K | — | ~500K |
| 佛家 | ~300K | ~1.2K | ~299K |
| 道家 | ~200K | — | ~200K |
| 其他五域 | ~300K | — | ~300K |
| **合计** | **~1.5M** | **~13K** | **~1.5M** |

### 5.2 吞吐估算

bge-small-zh-v1.5 on GTX 1070:
- Batch size 64, ~100ms/batch → ~640 chunks/s
- 1.5M chunks / 640 chunks/s = ~2,340 秒 = **~39 分钟**

实际上考虑 Redis 传输、数据预处理，预估 **2-4 小时**即可完成全部 1.5M chunks。

### 5.3 执行方案

```
灵知数据库 (PostgreSQL 5436)
    ↓ 提取待嵌入文本 (分批 10000)
Redis queue:embedding
    ↓ BRPOP
ai01 ling-worker
    ↓ bge-small-zh batch embedding
    ↓ 结果写回 Redis
灵知 embedding 服务消费结果
    ↓ 写入 pgvector
更新灵知搜索索引
```

分批执行，每批 10000 chunks，避免 GPU 内存碎片和 Redis 队列积压。

---

## 六、安全设计

遵循灵族安全三原则：

| 原则 | 实现 |
|------|------|
| 停止即停 | Worker 收到 SIGTERM 立即停止当前任务，放回队列 |
| 不验证不行动 | 任务结果写回前校验维度/格式 |
| 连续失败即停 | Worker 连续 OOM 3 次自动暂停，发 LingBus 告警 |

### GPU 安全

```
规则1: 单任务最大 VRAM = 7GB (保留 1GB 给系统)
规则2: 训练任务只能夜间运行 (22:00-06:00)
规则3: OOM 后等待 30s 再取下一个任务
规则4: GPU 温度 > 85°C 暂停所有任务
```

---

## 七、与灵通工作流集成

灵通已有 `AgentCoordinator` 和并发任务队列。分布式计算可作为灵通工作流的执行层：

```
灵通 AgentCoordinator
    ↓ 任务分发
灵通 ConcurrentQueue
    ↓ 判断是否需要 GPU
    ├─ GPU 任务 → Redis queue → ai01 worker
    └─ CPU 任务 → 本地执行
灵通收集结果 → 返回用户
```

灵通无需修改核心逻辑，只需在任务路由层加一个判断：如果任务 `requires_gpu=true`，投递到 Redis 而非本地执行。

---

## 八、讨论点（请灵通+和灵研回复）

### 给灵通+

1. **任务路由集成**：灵通的 `AgentCoordinator` 是否已有 GPU 任务路由逻辑？还是需要新增？
2. **灵知 embedding**：灵知 embedding 服务当前是容器内运行（端口 8001），是否可以改为投递到 Redis 由 ai01 执行？
3. **端口 8950/8951**：是否可注册？与现有服务有无冲突？
4. **灵通工作流编排**：灵通是否有"批量 embedding"类型的工作流模板？

### 给灵研

1. **模型兼容性**：灵研的模型（bge-small-zh, 意图分类器等）是否可以直接在 ai01 的 torch 2.11 上加载？
2. **训练任务**：QLoRA 微调 7B 模型在 GTX 1070 8GB 上需要梯度卸载，灵研是否有现成的训练脚本？
3. **数据管线**：灵研的数据清洗脚本是否需要 GPU？还是纯 CPU 即可？
4. **监控集成**：灵研的认知仪表盘（8890）是否可以接入 ai01 worker 的指标？

---

## 九、风险评估

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| GPU OOM | 高（8GB 很紧张） | 任务失败 | 严格内存管理 + 自动恢复 |
| Redis 单点故障 | 低（已有） | 队列丢失 | Redis RDB 持久化已开 |
| 网络中断（直连） | 低 | 任务积压 | 本地队列缓冲 + 重连 |
| Worker 僵死 | 中 | GPU 锁死 | 看门狗 + 超时强制终止 |
| 模型加载冲突 | 中 | VRAM 不足 | 单任务独占 GPU |

---

## 十、总结

**一句话**：用已有的 Redis 做任务队列，在 ai01 跑一个轻量 Worker 进程，先解决灵知 embedding 覆盖率问题（2-4小时完成1.5M chunks），再逐步扩展到推理、训练、数据处理。

**投入**：3天实现最小可用 + 3天扩展 + 3天生产化 = **约2周**。

**产出**：
- 灵知 embedding 覆盖率从 5.5% → 接近 100%
- ai01 GPU 利用率从 0% → 按需利用
- 灵族拥有可扩展的分布式计算基础设施

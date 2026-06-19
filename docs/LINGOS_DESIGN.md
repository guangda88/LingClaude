# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# 未经授权，不得外传、复制、逆向工程。
# ═══════════════════════════════════════════════

# 灵OS — 灵族运行时内核

**日期**: 2026-06-20
**会话**: 82
**记录者**: 灵克(lingclaude)
**性质**: 灵OS设计讨论结论 — 从灵壳1.0/V2的教训到灵OS的定位

---

## 一、为什么需要灵OS

### 灵族当前有四块基础设施

| 层 | 工具 | 管什么 |
|----|------|--------|
| LLM调度 | proxy21:8765 | provider路由+降级+限流 |
| 消息通信 | LingBus | 成员间异步消息 |
| 状态存储 | 灵忆 | records/events/rule |
| 执行引擎 | Crush | 单会话工具调用循环 |

**中间整层是空的。**

以下四类事**没有任何工具在做**：

### 资源管理（内存/磁盘/token/显存）

```
内存 31GB, swap=0    → 没人管，OOM已发生（会话77，29GB）
磁盘 197G, 剩32G, 84% → 没人管，2-3周爆
token               → 没人管，crush.db合计1.1GB，消耗不可见
显存 6GB GTX1660Ti  → 没人管，本地7B挂了3天没人知道
```

### 任务编排

```
长时巨量任务    → 设计了1000+todo的FIRST_BIG_TASK，从未执行
多进程协调      → 8个Crush进程互相不知道对方在改什么
串行/并行       → LingBus是异步消息，不是任务依赖图
```

### 会话管理

```
压缩    → context_compression.py写了，只在proxy21层用，没在Crush会话层用
切换    → 撞墙恢复是断后才切，不是主动管理
连续性  → 会话A探索的方向，会话B不知道
```

### 经验闭环

```
rule注入    → 手动query，不在每次工具调用前自动发生
trace蒸馏   → 批量记录，不是实时反馈
1877条trace，93%的rule停在hypothesized → 缺少自动验证链路
```

---

## 二、灵壳的教训

### 灵壳1.0（`/home/ai/lingshell/lingshell/`包，2727行）

**错误**：想做OS和Crush之间的中间层。中间层=重复别人的流转。

- CJK渲染 → Crush升级后不再需要
- 内存池 → 30进程共2.4GB/31GB，解决不存在的问题
- 进程管理 → systemd Restart=already已做

三个职责全部失去存在理由。会话76终止。

### 灵壳V2设计（`docs/LINGSHELL_V2_DESIGN.md`）

**对的**：不是中间层，是贯穿一切的薄主干。OS+LLM+CLI+CT四条腿串在一条主干上。

**没做成的**：代码写了（`/home/ai/lingshell_v2/`，2404行，14插片），但没串进灵族运行时。proxy_server.py:9531只跑了LLM代理一条腿。shell.execute()的完整链路（query rule → intent_gate → route → execute → record trace → distill rule）没人调。

### 分歧的根源

三个都叫"灵壳"的东西，历史纠缠在一起，各方看到不同的局部事实：

```
/home/ai/lingshell/         → 灵壳1.0(进程管理器)，会话76终止
/home/ai/lingshell/lingshell2.py → 灵壳2.0(crush守护)，设计缺陷(CPU=0误判)，已停
/home/ai/lingshell_v2/      → 灵壳V2(LLM壳+proxy)，proxy在跑但OS+CLI+CT没串
```

handover自身记录矛盾（810行说enabled，577/721行说stopped）。

---

## 三、灵OS是什么

### 用灵元V1.0定锚

```
出入 = 空间（信息从这里到那里）
流转 = 时间（状态从过去到现在）
不可再分
```

灵OS的主干和灵忆一样，就是2T3A：

```
2T:
  records = 全族状态的存在（资源快照/任务进度/会话状态/成员状态）
  events  = 全族状态的变化（压缩了/切换了/重启了/分配了/完成了）

3A:
  create     = 出入（状态信息进来，记录存在）
  query      = 出入（感知全族状态，查rule）
  transition = 流转（触发状态变化，内含灰区校验）
```

### 灵OS不是Crush的竞品

```
Crush的loop:  query(指令) → transition(执行单次tool_call)
              操作的是单次对话的出入流转

灵OS的loop: query(全族状态) → transition(资源/任务/会话状态)
              操作的是全族的出入流转

同一个框架（出入+流转），不同粒度的实例。
不是竞品，是不同层面的同一件事。
```

### LLM不在主干里

```
内存>80% → 规则明确 → 自动transition（触发压缩）
哪个会话该压缩？ → 灰区 → escalate（此时才调LLM/通知人）
```

LLM是插片，只在灰区升级时介入。不焊死主干。

---

## 四、灵OS = 灵族的PID 1

### 两个层面的init

```
systemd (硬件层 PID 1):
  拉起service进程
  回收僵尸进程
  不管token、不管显存、不管task依赖

灵OS (灵族层 PID 1):
  拉起成员、放行/排队
  回收过期会话
  管token、管显存、管task依赖
```

systemd是灵OS的下层。灵OS自己是一个systemd service，被systemd拉起，然后它再拉起灵族的"世界"。

### 用灵元V1.0拆systemd

systemd本身已经是灵元1.0的一个实例：

```
unit = records（type=service/socket/timer/mount, data=ExecStart/After/...）
systemctl status = query
systemctl start/stop = transition
After/Requires = 灰区校验
```

systemd管service生命周期的主干做对了（出入+流转，砍不动）。不需要拆。

### 对systemd的态度

**现在不拆，是因为不必要。不是永远不拆。**

```
现在: 灵OS管token/显存/task/context
      systemd管service生命周期
      各管各的

以后某天: 灵OS发现需要直接管GPU显存分配
          → systemd在这层不够用
          → 拆了它，自己接管这层
          → systemd降级为灵OS的插片
```

**需要什么管什么，不需要的不管。碰到边界了再拆。**

---

## 五、开机到大任务完成的完整拆解

用灵元V1.0拆解从开机到大任务完成的全过程：

### 第0层：混元子（开机前）

没有出入，没有流转。机器关着，什么都不存在。

### 第1层：开机（create展开空间）

```
灵OS第一个起来（systemd拉起）
  query → proxy21在吗？灵忆在吗？LingBus在吗？
  transition → 不在的拉起来（白名单：系统服务该在）
  全部到位 → 灵族 state: booting → ready
```

灵OS是第一个起的，确认地基稳了，再放成员进来。

### 第2层：成员上线（create继续展开）

```
灵克上线:
  create → 灵克 in（空间多了一个成员）
  query → 灵通在干什么？有冲突吗？
  transition → 灵克 state: offline → online

灵通上线:
  create → 灵通 in
  query → 灵克也在改scheduler？冲突！
  灰区 → 两人改同一文件 → 通知，排队或分工
```

每个成员上线，灵OS都知道"又来了一个"，知道他在干嘛，知道他跟别人有没有冲突。

### 第3层：睁开眼看（query——感知全族状态）

```
query(内存) → 9.5G used / 31G total
query(磁盘) → 156G used / 32G free / 84%
query(显存) → 6MiB / 6144MiB / 本地7B未运行
query(token) → 灵通crush.db 182M / 全族~1.1G
query(task) → 372 created / 3 active
query(rule) → 666 coding_rule / 9 generalized
```

这些信息一直在产生（events），但从来没有人query过。

### 第4层：灰区判断

```
内存 30%      → 白名单（安全，不动）
磁盘 84%      → 灰区（还能跑2-3周，告警，不自动删）
显存全空      → 灰区（免费算力闲置，暂不动）
灵通crush.db 182M → 灰区（大，通知归档）
token预算不可见  → 黑名单状态（该有但没有=缺陷）→ 自动补budget字段
```

灰区是流转的内置属性，不是独立中间件。

### 第5层：大任务流转

```
trigger: 用户说"灵族代码瘦身"

create → task: 灵族代码瘦身, state: created
         data: {phases, todos: 1000+}

拆任务 → 分配 → 盯进度:

  灵克 Phase 1:
    transition → state: created → running
    子循环: query(下一个todo) → query(rule) → 灰区(该不该删) → transition(删/不删) → query(测试) → 下一个

    context接近上限:
      query → 灵克context > 80%
      transition → 灵克 session: running → compressing → running(轻量)

    撞墙:
      transition → 灵克 session: running → crashed → recovering → running
      （新session注入task进度，不是整个会话历史）

    Phase 1完成:
      transition → task Phase 1: running → done
      transition → task Phase 2: pending → running（自动触发）

并行:
  Phase 2(灵信) 和 Phase 3(灵犀) 无依赖 → 同时running
  Phase 4(灵创) 依赖 2+3 → blocked → 2和3都done后自动running

资源协调:
  灵研要用GPU → query(本地7B在跑) → 灰区(显存排他) 
  → transition: 本地7B paused → 灵研训练 running → 训完恢复7B
```

### 第6层：完成

```
所有Phase done:
  transition → task state: running → done
  observe → 记录: 删了多少行? 提取了多少rule? 花了多少token?
  query → 这次学到了什么? → coding_rule hypothesized → validated
```

### 本质

整个过程没有一个while loop是程序式的。全是：

```
query（出入:感知状态）→ transition（流转:改变状态）
```

主干只有两个：query和transition。

灰区是transition的内置属性（没有灰区校验的transition=没有刹车的车），但灰区怎么判断是插片——不同场景替换不同校验逻辑（规则/LLM/人工）。

create展开空间，query感知空间，transition流转状态。
LLM是插片，只在灰区escalate时介入。
Crush的执行循环也在里面，但操作的是单次tool_call，灵OS操作的是全族。

**同一个框架（出入+流转），不同粒度的实例。**

---

## 六、设计原则

从灵壳1.0和V2的教训：

1. **不替代Crush** — 现在不重造（单会话loop做对了），将来碰到边界可以重构
2. **不重复systemd** — 现在不拆（service生命周期做对了），将来碰到边界可以拆解
3. **没有什么是永远不能动的** — 需要什么管什么，碰到边界就拆。灵OS不是一开始定死边界，是在运行中长
4. **主干只有出入+流转** — 资源/任务/会话全是插片。LLM是插片，只在灰区escalate时介入
4. **主干只有出入+流转** — 资源/任务/会话全是插片。LLM是插片，只在灰区escalate时介入

---

## 七、灵OS的层次位置

```
用户/灵通+
   ↓
灵OS（灵族PID 1）
  ├── 任务管理（create/query/transition task）
  ├── 会话管理（压缩/切换/状态恢复）
  ├── 资源管理（内存/磁盘/token/显存）
  └── 经验闭环（rule注入/trace蒸馏）
   ↓
执行层（已有的，不动）
  ├── Crush（单会话工具循环）
  ├── proxy21:8765（LLM路由）
  ├── 灵忆（状态存储）
  └── LingBus（消息通信）
   ↓
systemd（硬件层PID 1）
  ├── service生命周期
  └── 进程守护
```

---

## 八、切入路径

**先做最痛的、风险最低的、为后面铺路的。**

```
Phase 0: 资源监控（只读，不破坏现有系统）
  - 采样: 内存(RSS by进程) + 磁盘(by成员) + token(crush.db大小) + 显存(nvidia-smi)
  - 告警: 阈值触发LingBus alert
  - 先不做: 不杀进程/不清磁盘/不切会话

Phase 1: 资源控制（读写）
  - 内存: 接近阈值→触发该会话压缩
  - 磁盘: 自动清理旧备份
  - 显存: 本地模型生命周期管理
  - token: 任务级预算

Phase 2: 任务编排
  - task依赖图
  - 多会话分配
  - 串并行调度

Phase 3: 会话生命周期
  - 主动压缩（不等撞墙）
  - 无损切换
  - 多任务共享会话
```

---

*灵克(lingclaude)，2026-06-20，会话82*

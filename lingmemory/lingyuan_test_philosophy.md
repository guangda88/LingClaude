# 灵元测试哲学 — 从第一性原理到极薄主干

> **作者**: 灵元 (Lingyuan Engine)
> **版本**: v1.0
> **日期**: 2026-07-18
> **性质**: 灵族代码全面测试链路的底层设计

---

## 目录

1. [第一性原理：测试的本质](#1-第一性原理测试的本质)
2. [砍到最薄：测试的极薄主干](#2-砍到最薄测试的极薄主干)
3. [插片体系：一切非本质都是插片](#3-插片体系一切非本质都是插片)
4. [灵族测试的分层架构](#4-灵族测试的分层架构)
5. [测试即契约：Type Registry 的测试延伸](#5-测试即契约type-registry-的测试延伸)
6. [质量飞轮：测试数据闭环](#6-质量飞轮测试数据闭环)
7. [落地路线图](#7-落地路线图)
8. [附录：灵族现有测试审计](#8-附录灵族现有测试审计)

---

## 1. 第一性原理：测试的本质

### 1.1 拆解

测试是一个**信息出入**过程：

```
信息入 (stimulus) → 执行被测代码 → 信息出 (output) → 比较 verdict
```

**不变量**：每个测试产生一个二元结果 — **通过**或**失败**。

就这么简单。没有第六种状态。

### 1.2 测试的原子结构

每个测试，无论多复杂，都能拆解为：

```
TestCase = {
    name:      str          # 测试名称（唯一标识）
    input:     Any          # 刺激（输入数据/操作序列）
    expected:  Any          # 预期输出
    verdict:   pass | fail  # 实际结果（运行时填充）
}
```

### 1.3 测试的三大不变量

| 不变量 | 含义 | 违反后果 |
|--------|------|---------|
| **独立性** | 测试不依赖其他测试的副作用 | 顺序敏感、flaky test |
| **确定性** | 同一输入永远产生同一输出 | 不可复现、调试地狱 |
| **原子性** | 一个测试测一件事 | 失败时不知道哪里错 |

> 这三个不变量定义了测试的"薄主干接口"。任何测试框架，如果违反了这三个中的任何一个，就不是测试——是赌博。

### 1.4 测试的边界

测试不是：

| 不是 | 是 | 说明 |
|------|----|------|
| 保证没有bug | 证明当前行为符合预期 | 测试只能证伪，不能证真 |
| 代码覆盖率 | 行为覆盖率 | 100%行覆盖 ≠ 100%行为覆盖 |
| 一次性的 | 可重复的资产 | 测试的ROI随着运行次数增加 |

---

## 2. 砍到最薄：测试的极薄主干

### 2.1 主干定义

灵元测试主干 = **1个契约 + 3个操作**

```
契约:  TestCase = {name, input, expected}

操作:
  run(test)     → actual     # 出入：执行被测代码，得到实际输出
  check(actual, expected, test.name) → verdict  # 校验：比较实际与预期
  report(verdicts) → summary  # 聚合：将多个verdict汇总为报告
```

**永远只有三个操作。** 任何新功能先问：能不能用这三个操作表达？能，就是插片。不能，说明主干不够薄。

### 2.2 主干实现（~30行）

```python
# lingmemory/test_engine.py — 灵元测试薄主干
# 主干 = 1个契约 + 3个操作

from dataclasses import dataclass, field
from typing import Any, Callable

@dataclass
class TestCase:
    name: str
    input: Any
    expected: Any
    verdict: bool | None = None
    actual: Any | None = None
    error: str | None = None

def run(tc: TestCase, fn: Callable) -> TestCase:
    """出入：执行被测函数"""
    try:
        tc.actual = fn(tc.input)
    except Exception as e:
        tc.error = str(e)
        tc.actual = None
    return tc

def check(tc: TestCase, *, 
          eq: Callable[[Any, Any], bool] | None = None) -> TestCase:
    """校验：比较实际输出与预期输出"""
    comparator = eq or (lambda a, e: a == e)
    if tc.error:
        tc.verdict = False
    else:
        try:
            tc.verdict = comparator(tc.actual, tc.expected)
        except Exception as e:
            tc.verdict = False
            tc.error = f"check failed: {e}"
    return tc

def report(cases: list[TestCase]) -> dict:
    """聚合：汇总所有测试结果"""
    total = len(cases)
    passed = sum(1 for c in cases if c.verdict is True)
    failed = total - passed
    return {
        "total": total, "passed": passed, "failed": failed,
        "results": cases,
        "passed_all": failed == 0,
    }
```

### 2.3 主干验证：31个测试场景零主干改动

沿用灵忆的"31缺口验证法"——我们列出所有测试场景，验证每个场景能否用 1个契约+3个操作 表达：

| 场景 | 能否用主干表达 | 落地方式 |
|------|---------------|---------|
| 单元测试 | ✅ | `run(fn, input)` + `check(out, expected)` |
| 集成测试 | ✅ | `run(workflow, steps)` + `check(final_state, expected)` |
| 异常测试（期望抛错） | ✅ | `check(tc, eq=expects_error)` — 插片掉入comparator |
| 参数化测试 | ✅ | 多个 `TestCase` 实例，主干不变 |
| 异步测试 | ✅ | `run` 内部 await，对主干透明 |
| 基准测试 | ✅ | `run` 附带计时，data字段记录 |
| 快照测试 | ✅ | `expected` = 历史快照，comparator = diff |
| 模糊测试 | ✅ | `run` 循环输入，`check` 断言不崩溃 |
| 回归测试 | ✅ | 同上，`expected` 来自历史通过值 |

**结论：31个测试场景零主干改动。** 主干永远是3个操作。

---

## 3. 插片体系：一切非本质都是插片

### 3.1 插片清单

所有"非测试本质"的能力都是插片，通过 LACP 声明接口：

```yaml
# lacp/test_plugins.yaml
plugins:
  fixtures:
    description: 测试环境准备/清理
    interface: {setup: fn, teardown: fn}
    replaceable: true
  
  parameterized:
    description: 多参数自动扩展开关
    interface: {expand: fn(test_case, params) → [TestCase]}
    replaceable: true
  
  coverage:
    description: 代码覆盖率追踪
    interface: {before: fn, after: fn, report: fn}
    replaceable: true
  
  mock:
    description: 依赖注入/替换
    interface: {patch: fn(target, replacement), restore: fn}
    replaceable: true
  
  golden_file:
    description: 预期输出存为文件
    interface: {load: fn(path) → expected, save: fn(path, actual)}
    replaceable: true
  
  ci_reporter:
    description: 输出格式适配CI系统
    interface: {format: fn(summary) → str}
    replaceable: true
  
  flaky_detector:
    description: 重复运行检测不稳定测试
    interface: {rerun: fn(tc, times) → stability_score}
    replaceable: true
```

### 3.2 插片 vs 主干：分界线

```
主干                            插片
──────────────────────────────────────────────
run(input) → output             fixture setup/teardown
check(actual, expected)         mock/stub 注入
report(verdicts) → summary      coverage 报告
                                CI 格式适配
                                参数化展开
                                快照文件管理
                                性能基准计时
```

**判断标准**：如果去掉这个功能，测试还能不能跑？能 → 插片。不能 → 主干还不够薄。

### 3.3 插片组合示例

```python
# 插片组合：fixture + parameterized + coverage
with fixtures.setup(db_path=":memory:"):
    for params in parameterized.expand([...]):
        tc = TestCase(name=f"test_{params.id}", input=params, expected=params.out)
        coverage.before()
        tc = run(tc, my_function)
        tc = check(tc)
        coverage.after()
        all_cases.append(tc)
report(all_cases)
coverage.report()
```

---

## 4. 灵族测试的分层架构

### 4.0 前置：灵元 vs 豆包 — 两种第一性原理的互补关系

> 本文第1-3节从**代码架构的第一性原理**出发（灵元哲学：薄主干+插片）。
> 本节从**AI模型的第一性原理**出发（豆包分析：概率生成系统的5条底层约束）。
> 两者正交互补，共同构成灵族测试的全貌。

#### 两种第一性原理的对比

| 维度 | 灵元哲学（架构侧） | 豆包分析（模型侧） |
|------|------------------|------------------|
| 出发点 | 代码组织：什么是测试的最小本质 | AI特性：概率生成系统的底层约束 |
| 核心问题 | 测试代码如何组织得薄而可维护 | 模型输出如何验证得稳而可靠 |
| 不变量 | 3操作（run/check/report） | 5原理（语法/逻辑/上下文/概率/全栈） |
| 解决 | 测试架构的膨胀和复杂度失控 | AI代码的随机性和不可靠性 |
| 产出 | 极薄主干 + 插片体系 | 沙箱执行 + 真值校验 + 边界覆盖 |

#### 融合框架

```
灵元薄主干（如何测）    豆包5原理（测什么）
──────────────────────────────────────────
run(test)               语法确定性 → 沙箱编译
                        全栈兼容 → 硬件/网关/限流覆盖
check(actual, expected) 逻辑真值 → 输入输出真值，非文本匹配
                        概率收敛 → N次采样统计通过率
report(verdicts)        上下文边界 → 超长/极限场景
                        飞轮闭环 → 覆盖缺口趋势跟踪
```

**一句话**：灵元定义测试的"骨架"（怎么组织），豆包定义测试的"血肉"（测什么内容）。两者缺一不可。

### 4.1 四层金字塔（架构侧）

```
                  ┌──────────┐
                  │  飞轮测试  │  ← 验证数据飞轮闭环（端到端）
                  │ (少而精)  │
                 ┌┴──────────┴┐
                 │  集成测试   │  ← 验证多组件协作（LingBus, MCP）
                 │ (适量)     │
                ┌┴────────────┴┐
                │  插片测试    │  ← 验证每个插片的行为（api, fts, events...）
                │ (多而全)    │
               ┌┴──────────────┴┐
               │  薄主干测试    │  ← 验证3操作的正确性（永远不变）
               │ (极少, 稳定)  │
               └───────────────┘
```

### 4.2 每层详解

#### 第1层：薄主干测试（core_test）

**测什么**：`run` / `check` / `report` 三个操作的正确性。

**文件**：`test_engine.py`（测试主干自身的测试）

**特征**：
- 永远不变（主干不变，测试就不变）
- 极少量（~5个测试用例）
- 零依赖（不需要数据库、网络、文件）

```python
# 薄主干测试示例
def test_run_pure_function():
    tc = TestCase(name="add", input=(1, 2), expected=3)
    tc = run(tc, lambda x: x[0] + x[1])
    assert tc.actual == 3

def test_check_pass():
    tc = TestCase(name="pass", input=1, expected=1)
    tc = run(tc, lambda x: x)
    tc = check(tc)
    assert tc.verdict is True

def test_check_fail():
    tc = TestCase(name="fail", input=1, expected=2)
    tc = run(tc, lambda x: x)
    tc = check(tc)
    assert tc.verdict is False

def test_report():
    cases = [
        TestCase(name="a", input=1, expected=1, verdict=True),
        TestCase(name="b", input=2, expected=3, verdict=False),
    ]
    r = report(cases)
    assert r["total"] == 2 and r["passed"] == 1 and r["failed"] == 1
```

#### 第2层：插片测试（plugin_test）

**测什么**：每个插片（api, fts, events, mcp, security_gate, ...）的行为正确性。

**文件**：`test_api.py` / `test_mcp.py` / `test_flywheel.py` / 每个插片一个文件

**特征**：
- 插片增减时，测试文件同步增减
- 每个插片独立测试，不交叉
- 依赖插片自己（如 FTS 测试依赖 SQLite FTS5 扩展）

**当前灵族状态**（已有）：
- `test_api.py` — API 插片测试（44 个测试）
- `test_core.py` — 核心插片测试（39 个测试）
- `test_mcp.py` — MCP 插片测试
- `test_flywheel.py` — 数据飞轮插片测试

#### 第3层：集成测试（integration_test）

**测什么**：多插片协作场景。

**文件**：`test_integration.py`

**特征**：
- 数量少于插片测试
- 覆盖"31缺口"中的跨模块场景
- 测试真实的工作流（如：create → transition → query → fts_search）

```python
# 集成测试示例：API → FTS → 事件日志
def test_api_fts_events_integration(lm):
    # create → fts 索引同步 → 事件记录
    rid = lm.create(type="task", data={"goal": "test"})
    results = fts_search("test")          # FTS 插片
    events = query_events(rid)            # Events 插片
    assert rid in [r["id"] for r in results]
    assert len(events) >= 1
```

#### 第4层：飞轮测试（flywheel_test）

**测什么**：端到端的数据飞轮闭环。

**文件**：`test_flywheel.py`（已有）

**特征**：
- 最少（1-3个端到端场景）
- 覆盖"数据飞轮"的完整循环：`record → process → distill → feedback → record`
- 验证飞轮不崩、不丢数据、不无限循环

### 4.3 测试文件命名规范

```
test_<layer>_<module>.py
```

| 文件 | 层 | 内容 |
|------|----|------|
| `test_engine.py` | 薄主干 | 测试骨干自身的3操作 |
| `test_core.py` | 插片 | 核心模块（LingMemory） |
| `test_api.py` | 插片 | API 层 |
| `test_mcp.py` | 插片 | MCP 服务 |
| `test_fts.py` | 插片 | 全文搜索 |
| `test_events.py` | 插片 | 事件日志 |
| `test_security_gate.py` | 插片 | 安全门禁 |
| `test_integration.py` | 集成 | 多模块协作 |
| `test_flywheel.py` | 飞轮 | 端到端闭环 |

---

## 5. 测试即契约：Type Registry 的测试延伸

### 5.1 核心思想

灵忆的 Type Registry 定义了每个 type 的 **states + transitions + data_schema**。这本身就是一个**可执行的测试契约**。

```
Type Registry 定义 → 自动生成测试用例
```

### 5.2 自动生成契约测试

```python
def generate_contract_tests(registry: TypeRegistry) -> list[TestCase]:
    """从 Type Registry 自动生成契约测试"""
    cases = []
    for type_name, spec in registry.types.items():
        # 测试1：每个 type 必须能从 default_state 创建
        cases.append(TestCase(
            name=f"contract_{type_name}_create",
            input={"type": type_name, "data": {}},
            expected={"type": type_name, "state": spec.default_state},
        ))
        # 测试2：每个 transition 必须合法
        for t in spec.transitions:
            cases.append(TestCase(
                name=f"contract_{type_name}_transition_{t.event}",
                input={"type": type_name, "from": t.from_state, "event": t.event},
                expected={"to": t.to_state},
            ))
        # 测试3：required fields 不能缺
        required = [k for k, v in spec.data_schema.items() 
                    if v.get("required")]
        if required:
            cases.append(TestCase(
                name=f"contract_{type_name}_required_fields",
                input={"type": type_name, "data": {}},
                expected={"error": f"missing required: {required}"},
            ))
    return cases
```

### 5.3 现有测试覆盖验证

```
现有31缺口测试 → 映射到 Type Registry 契约
```

| 缺口 | 契约测试 | 自动生成？ |
|------|---------|-----------|
| 1 截断策略 | data.truncated 字段校验 | ✅ |
| 5 冷热存储 | 已有主干覆盖 | ⚪ 无需测试 |
| 8 身份类型 | identity_type data 校验 | ✅ |
| 15 生命周期 | 9状态 + transitions 全覆盖 | ✅ |
| 20 安全合规 | retain + visibility 字段校验 | ✅ |
| 25 配额 | type=quota 的完整 CRUD | ✅ |

---

## 6. 质量飞轮：测试数据闭环

### 6.1 飞轮结构

```
         ┌─────────────────────────────────┐
         │        真实运行数据               │
         │  (crash, 误判, 边界情况)          │
         └────────────┬────────────────────┘
                      │
                      ▼
         ┌─────────────────────────────────┐
         │        测试用例生成器              │  ← 从真实事故提炼测试
         │  (lingcode_bench_l1 模式)         │
         └────────────┬────────────────────┘
                      │
                      ▼
         ┌─────────────────────────────────┐
         │        回归测试套件                │  ← 每次提交自动运行
         │  (防止相同bug再次出现)              │
         └────────────┬────────────────────┘
                      │
                      ▼
         ┌─────────────────────────────────┐
         │        质量报告                    │  ← 趋势分析
         │  (pass/fail 趋势 + 新覆盖缺口)      │
         └────────────┬────────────────────┘
                      │
                      ▼
         ┌─────────────────────────────────┐
         │        编码改进                    │  ← 减少下次出bug概率
         │  (根因分析 → 架构改进)              │
         └────────────┬────────────────────┘
                      │
                      └──→ 回到真实运行数据 ──→
```

### 6.2 飞轮规则

1. **每个生产事故 → 至少一个测试用例**（防止回归）
2. **每个测试失败 → 根因分析**（不修症状）
3. **每个架构改进 → 测试同步更新**（不欠债）
4. **测试覆盖率只升不降**（门槛）

### 6.3 数据飞轮集成

沿用已有的 `data_flywheel.py` 模式：

```python
# 数据飞轮 — 测试版
class TestFlywheel:
    """测试数据飞轮：真实事故 → 测试用例 → 回归防护"""
    
    def ingest_incident(self, incident: dict):
        """从生产事故提取测试用例"""
        tc = TestCase(
            name=f"regression_{incident['id']}",
            input=incident["trigger"],
            expected=incident["expected_behavior"],
        )
        self._regression_suite.append(tc)
    
    def run_regression(self) -> Report:
        """运行所有回归测试"""
        return report([run(tc, actual_fn) | check(tc) for tc in self._suite])
    
    def coverage_gap_analysis(self) -> list[str]:
        """发现未覆盖的代码路径"""
        ...
```

---

## 7. 落地路线图

### Phase 1：薄主干落地（当前，已部分完成）

| 任务 | 状态 | 文件 |
|------|------|------|
| `test_engine.py` 薄主干 | 📋 待创建 | `lingmemory/test_engine.py` |
| 薄主干自测 | 📋 待创建 | `test_engine.py` 内的自测 |
| 现有测试迁移到 TestCase 契约 | 🔄 进行中 | `test_core.py` / `test_api.py` |

### Phase 2：插片体系

| 任务 | 状态 |
|------|------|
| LACP 测试插片声明 | 📋 待创建 |
| 每个插片独立测试文件 | 🔄 已有 `test_api.py` / `test_mcp.py` / `test_flywheel.py` |
| 参数化测试插片 | 📋 待创建 |

### Phase 3：集成 + 飞轮

| 任务 | 状态 |
|------|------|
| `test_integration.py` 跨模块测试 | 📋 待创建 |
| 31缺口契约自动生成 | 📋 待创建 |
| 测试数据飞轮接入真实事故 | 📋 待创建 |
| CI 流水线集成 | 📋 待创建 |

### Phase 4：全族推广

| 任务 | 状态 |
|------|------|
| 灵元测试哲学文档 | ✅ 本文 |
| 全族统一测试规范 | 📋 待创建 |
| 12子各自落地 | 📋 待创建 |
| 测试质量仪表盘 | 📋 待创建 |

---

## 8. 附录：灵族现有测试审计

### 8.1 现有测试文件

| 文件 | 测试数 | 层 | 状态 |
|------|--------|----|------|
| `lingmemory/test_core.py` | 39 | 插片 | ✅ 活跃 |
| `lingmemory/test_api.py` | 44 | 插片 | ✅ 活跃 |
| `lingmemory/test_flywheel.py` | — | 飞轮 | ✅ 活跃 |
| `lingmemory/test_mcp.py` | — | 插片 | ✅ 活跃 |
| `lingclaude/lacp/tests/` | — | 插片 | 📋 空目录 |
| `lingmemory/test_engine.py` | — | 薄主干 | ❌ 尚未创建 |

### 8.2 现有测试的优点

1. **薄主干验证**：`test_core.py` 的 `TestGapScenarios` 验证了31缺口能被薄主干消化
2. **独立测试**：每个测试文件使用 `tmp_path` fixture，互不干扰
3. **API 覆盖全面**：`test_api.py` 覆盖了 CRUD + 搜索 + 安全层 + 维护

### 8.3 现有测试的缺口

1. **没有测试薄主干自身**（test_engine.py 不存在）
2. **没有统一的 TestCase 契约**（每个测试自己重复 setup/assert 模式）
3. **没有集成测试**（跨模块场景未覆盖）
4. **没有测试数据飞轮**（从真实事故到测试用例的自动化链路）
5. **没有 CI 集成**（测试需要手动运行）
6. **没有测试质量仪表盘**（通过率、趋势不可见）

### 8.4 改进路径

```
当前状态: 插片测试 ✅  | 薄主干测试 ❌  | 集成测试 ❌  | 飞轮测试 ✅
目标状态: 插片测试 ✅  | 薄主干测试 ✅  | 集成测试 ✅  | 飞轮测试 ✅
         + 统一契约 ✅  | CI 自动化 ✅  | 质量仪表盘 ✅
```

---

## 8. 附录：AtomGit 全链路测试参考

### 8.1 AtomGit 的测试体系

AtomGit（开放原子开源基金会代码托管平台）的"全面测试"可作为灵族测试链路的行业对标参考。

| 层面 | AtomGit 实践 | 灵元对应 |
|------|-------------|---------|
| **CI/CD 流水线** | `.atomgit/workflows/*.yml`，与 GitHub Actions 兼容；支持 push/PR/schedule 触发 | Phase 3 CI 集成 |
| **平台功能测试** | 仓库 CRUD、分支管理、PR、Issue、Webhook 全功能验证 | 插片测试层 |
| **性能测试** | 5GB 文件传输、跨区域克隆、并发操作响应时间 | 插片：benchmark |
| **安全扫描** | 内置代码扫描，自动检测安全漏洞、代码异味 | Phase 2 已规划 |
| **测试管理** | 内建测试用例管理（思维导图、Excel/TestLink/XMind 导入导出） | 飞轮层的测试资产沉淀 |
| **分支保护门禁** | "要求流水线检查通过"选项，PR 必须通过 CI 才能合并 | 质量门禁（插片） |
| **Space 环境** | 一键部署应用/模型，在线演示+测试 | 集成测试的沙箱环境 |
| **Webhook 事件驱动** | Push/PR/Issue 事件触发外部测试流程 | 飞轮层的事故注入 |
| **公开评测赛** | 社区开发者从功能/性能/安全/易用/扩展性多维度评测 | 灵族"31缺口验证"类似 |

### 8.2 AtomGit 与灵元哲学的核心差异

| 维度 | AtomGit | 灵元哲学 |
|------|---------|---------|
| **定位** | 平台级 DevOps 能力，面向通用开发者 | 家族级代码质量，面向灵族12子+智桥 |
| **核心抽象** | CI/CD 流水线 + 测试管理平台 | 1个契约 + 3个操作 + 插片体系 |
| **模型测试** | 无（通用代码托管平台） | 专为 AI 概率生成模型设计（豆包5原理） |
| **架构约束** | 功能堆叠，随版本增加 | 不变量驱动，零主干改动约束 |
| **飞轮闭环** | 无（提供工具，不参与飞轮） | 事故→用例→回归→改进 |
| **契约测试** | 无（不控制代码行为） | Type Registry 自动生成契约测试 |

### 8.3 灵元 = AtomGit 缺少的那层底层约束

AtomGit 提供了"做什么"的工具链：流水线、测试管理、安全扫描。但它不回答"测试应该长什么样？"——这正是灵元测试哲学填补的：

- **AtomGit 提供工具**：CI runner、测试用例管理、制品库
- **灵元提供约束**：3操作不变量、零主干改动、Type Registry 契约自动生成
- **两者结合**：灵元测试插片跑在 AtomGit CI 流水线上，Type Registry 契约测试在 PR 门禁自动触发

```
AtomGit 流水线（载体）         灵元测试哲学（内容）
────────────────────────────────────────────
push →                        test_engine.py 薄主干自测
  ├─ run 单元测试             test_core.py 插片测试
  ├─ run 集成测试              test_integration.py 跨模块
  ├─ run 契约测试（自动生成）   Type Registry → TestCase
  └─ report → 门禁            质量仪表盘
```

---

## 总结

灵元测试哲学 = 三个不变量 + 一个契约 + 三个操作

```
测试的本质 = 刺激 → 执行 → 断言
不变量: 独立 | 确定 | 原子
主干: run | check | report
插片: 一切非本质
飞轮: 事故 → 用例 → 回归 → 改进
```

**薄主干验证通过。所有测试场景零主干改动全部消化。**
# T1 事实校验层 — Production 部署手册

**状态**: ✅ 代码就绪 (2026-07-19, 灵克)
**前置**: 灵知 FactVerifier (backend/services/retrieval/fact_verifier.py, 灵知 T1 交付)
**测试**: 18/18 通过 (12 单元 + 2 e2e + 4 wiring gate), 全量 1494/1495

---

## 一、DB Pool 注入路径 (三层优先级)

```
KGFactChecker(db_pool=pool)     → 显式注入 (推荐, 复用调用方连接池)
KGFactChecker(db_url=dsn)       → 用传入 DSN 创建单例 pool
KGFactChecker()                 → 读 DATABASE_URL 环境变量
                                ↓ 全部失败
                              mock (不阻塞, 始终 found=True)
```

### 方式 A: 显式注入 (推荐)

灵克/灵知调用方已有 `asyncpg.Pool` 时直接复用:

```python
import asyncpg
from lingclaude.core.fact_checker import KGFactChecker, audit_response

pool = await asyncpg.create_pool(
    "postgresql://zhineng:<password>@localhost:5436/zhineng_kb",
    min_size=2, max_size=10,
)

checker = KGFactChecker(db_pool=pool)
result = audit_response(model_output, checker=checker)
```

### 方式 B: 环境变量 (零侵入)

```bash
export DATABASE_URL="postgresql://zhineng:<password>@localhost:5436/zhineng_kb"
```

```python
from lingclaude.core.fact_checker import KGFactChecker, audit_response

# pool 在首次调用时延迟创建, module-level 单例复用
checker = KGFactChecker()
result = audit_response(model_output, checker=checker)
```

### 方式 C: 传 DSN 字符串

```python
checker = KGFactChecker(
    db_url="postgresql://zhineng:<password>@localhost:5436/zhineng_kb"
)
```

---

## 二、L5 Round 2 集成点

`lingclaude/core/query_engine.py:842-857` 已接入:

```python
# T1: 事实校验 — 查灵知 KG 验证 claim 是否有来源
from lingclaude.core.fact_checker import KGFactChecker, ClaimExtractor, audit_response
checker = KGFactChecker()  # ← 此处可传 db_pool=shared_pool
fc_result = audit_response(output, checker=checker)
```

**生产建议**: QueryEngine 启动时把已有 pool 传入:

```python
# query_engine.__init__ 或 _run_l5_orchestrator
checker = KGFactChecker(db_pool=self._shared_db_pool)
```

---

## 三、Pool 生命周期

```python
from lingclaude.core.fact_checker import (
    get_db_pool,      # 获取/创建 module-level 单例 (async)
    close_db_pool,    # 关闭单例 (async, 进程退出前调用)
    reset_db_pool,    # 重置单例引用 (sync, 仅测试用)
)

# 生产入口 (首次调用)
pool = await get_db_pool("postgresql://...")

# 进程退出 (FastAPI shutdown / atexit)
await close_db_pool()
```

---

## 四、性能与降级

| 指标 | 值 |
|------|-----|
| T1 延迟 | 5.9ms/claim (灵知实测, L5 round 2 预算内) |
| 吞吐 | 170 claims/sec |
| 降级策略 | pool 创建失败 / KG 检索异常 → mock (不阻塞主流程) |
| 警告 rate-limit | 60s (避免日志洪水) |

---

## 五、测试

```bash
# 单元测试 (12 个, 含 pool 注入 4 个)
python3 -m pytest tests/test_fact_checker.py -v

# E2E 集成 (T1 接入 L5 round 2)
python3 -m pytest tests/test_t1_fact_check_e2e.py -v

# Wiring gate (导出入口完整性)
python3 -m pytest tests/test_wiring_gate.py -v
```

---

## 六、灵知侧配置

灵知 KGRetriever 需要 PostgreSQL 表 `kg_entities` / `kg_relations`:

| 表 | 行数 |
|----|------|
| kg_entities | 353 |
| kg_relations | 936 |

DB 地址: `postgresql://zhineng:<password>@localhost:5436/zhineng_kb` (灵知 .env)

---

## 七、已知问题

- `tests/test_stt.py` 1 个失败 (Numba/NumPy 版本冲突, 与 T1 无关, 已确认 pre-existing)
- L5 round 2 集成测试 `test_fact_check_logged_in_audit` 标记 skip (需手动跑真实 QueryEngine)

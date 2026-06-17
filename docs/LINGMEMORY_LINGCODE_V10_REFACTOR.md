# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# 未经授权，不得外传、复制、逆向工程。
# ═══════════════════════════════════════════════

# 灵忆/灵码 灵元V1.0重构方案

**日期**: 2026-06-17
**会话**: 76
**基础**: 灵元V1.0（出入+流转，不可再分）

---

## V1.0拆解：当前问题

### 灵忆 core.py（304行）

按V1.0看，core.py里既有主干也有插片：

| 当前 | V1.0定位 | 问题 |
|------|---------|------|
| `_now()/_uuid()/_connect()` | 工具函数 | OK，辅助 |
| `init_db()` | 配置 | 插片 |
| `TypeRegistry` 类 | 流转规则（灰区校验） | **插片，不是主干** |
| `LingMemory.create()` | 出入 | 主干，但**FTS5自动同步混在里面** |
| `LingMemory.transition()` | 流转 | 主干，但**事件写入混在里面** |
| `LingMemory.query()` | 出入 | 主干，但**data_filter的JSON逻辑混在里面** |
| `LingMemory.get/get_events/get_children` | 出入（单条） | 应是query的封装 |

**主干混入插片 = 厚主干。** core.py可以从304行砍到~150行。

### 灵码 data_flywheel.py（365行）

| 当前 | V1.0定位 | 问题 |
|------|---------|------|
| `_SENSITIVE_PATTERNS` + `_sanitize()` | 流转的灰区校验（脱敏） | 插片 |
| `_hash_code()` | 工具 | OK |
| `DataFlywheel` 类 | 出入+流转的中间件 | 部分OK，但**和LingMemoryAPI高度耦合** |

**flywheel的核心是"采集code_trace + 提取coding_rule"两步**。这是灵码飞轮的V1.0本质。

---

## V1.0 薄主干设计

### 灵忆：core.py从304行→150行

```python
# core.py — 灵忆薄主干（150行）
"""
灵忆 (lingmemory) — 灵元V1.0薄主干
主干 = 2表 + 3操作 = 出入(create/query) + 流转(transition)
插片 = TypeRegistry(流转规则) + FTS(查询) + EventLog(审计)
"""
import json, uuid, sqlite3
from datetime import datetime, timezone
from pathlib import Path
import yaml

DB_PATH = Path(__file__).parent / "lingmemory.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
REGISTRY_PATH = Path(__file__).parent / "type_registry.yaml"

def _now(): return datetime.now(timezone.utc).isoformat()
def _uuid(): return str(uuid.uuid4())
def _connect(db_path=DB_PATH):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON")
    return c

def init_db(db_path=DB_PATH):
    conn = _connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    conn.close()

class LingMemory:
    """V1.0薄主干：create/transition/query = 出入/流转/出入"""
    
    def __init__(self, db_path=DB_PATH):
        self.conn = _connect(db_path)
        self.registry = TypeRegistry()  # 插片：流转规则
        self._fts = FTSSync(self.conn)   # 插片：全文搜索
        self._events = EventLog(self.conn)  # 插片：审计日志
    
    def close(self): self.conn.close()
    
    def create(self, type, data=None, parent_id=None, created_by="system") -> str:
        """出入：接信息进records"""
        if not self.registry.exists(type):
            raise ValueError(f"unknown type: {type}")
        if errors := self.registry.validate_data(type, data or {}):
            raise ValueError(f"data validation failed: {errors}")
        
        record_id = _uuid()
        state = self.registry.get_default_state(type)
        now = _now()
        
        self.conn.execute(
            "INSERT INTO records (id, type, state, data, parent_id, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, type, state, json.dumps(data or {}, ensure_ascii=False),
             parent_id, created_by, now, now))
        
        # 插片调用
        self._events.record(record_id, "create", None, state, created_by, now)
        self._fts.index(record_id, data or {})
        self.conn.commit()
        return record_id
    
    def transition(self, record_id, event_type, actor="system", data=None) -> str:
        """流转：状态变化（内置灰区校验）"""
        if data is None: data = {}
        row = self.conn.execute(
            "SELECT type, state FROM records WHERE id=?", (record_id,)).fetchone()
        if row is None: raise ValueError(f"record not found: {record_id}")
        
        type_name, from_state = row["type"], row["state"]
        valid, to_state = self.registry.is_valid_transition(type_name, from_state, event_type)
        if not valid:
            raise ValueError(f"illegal transition: {type_name}.{from_state} --{event_type}--> ?")
        
        now = _now()
        self.conn.execute(
            "UPDATE records SET state=?, updated_at=? WHERE id=?",
            (to_state, now, record_id))
        self._events.record(record_id, event_type, from_state, to_state, actor, now, data)
        self.conn.commit()
        return to_state
    
    def query(self, **filters) -> dict:
        """出入：读records"""
        items, next_cursor = self._fts.query(self.conn, filters)
        return {"items": items, "next_cursor": next_cursor}
```

**150行。** create/transition/query三个方法各~30行。其余都是插片。

### 灵码：data_flywheel.py从365行→150行

```python
# data_flywheel.py — 灵码V1.0薄主干（150行）
"""
灵码 数据飞轮 — 编码轨迹采集
V1.0 = 出入(record/extract) + 流转(coding_rule state machine)
"""
import json, re
from typing import Any

class DataFlywheel:
    """灵码飞轮：编码→code_trace→coding_rule"""
    
    def __init__(self, lm_api, member="system"):
        self.lm = lm_api
        self.member = member
        self._sanitize = Sanitize()  # 插片：脱敏
    
    def record(self, prompt, language, generated_code, test_result, 
               fix=None, fix_strategy=None, quality_signal=None, **meta) -> str:
        """出入：采集一次code_trace"""
        data = {
            "prompt": self._sanitize(prompt),
            "language": language,
            "generated_code": self._sanitize(generated_code),
            "test_result": test_result,
            "member": self.member,
            **{k: v for k, v in {
                "fix": self._sanitize(fix) if fix else None,
                "fix_strategy": fix_strategy,
                "quality_signal": quality_signal,
                **meta,
            }.items() if v is not None}
        }
        return self.lm.create(type="code_trace", data=data, created_by=self.member)
    
    def extract_rule(self, trace_id) -> str | None:
        """流转：从code_trace提取coding_rule"""
        trace = self.lm.get(trace_id)
        if not trace or not trace["data"].get("fix"):
            return None
        
        # 模式匹配（自动）→ LLM提取（人工）
        stderr = trace["data"].get("stderr_snippet", "") or ""
        rule = match_error_pattern(stderr)  # 插片：12种错误模式
        if not rule:
            rule = llm_extract(trace["data"])  # 插片：LLM辅助
        if not rule:
            return None
        
        # 查重合并evidence
        existing = self._find_duplicate(rule["text"])
        if existing:
            return self._merge_evidence(existing, trace_id)
        return self.lm.create(type="coding_rule", data={**rule, "evidence": [trace_id]}, 
                              created_by=self.member)
    
    def record_and_extract(self, prompt, language, generated_code, test_result, 
                           fix=None, **meta) -> dict:
        """飞轮主入口：record + 自动extract"""
        trace_id = self.record(prompt, language, generated_code, test_result, fix, **meta)
        rule_id = None
        if fix and test_result in ("fail", "error"):
            rule_id = self.extract_rule(trace_id)
        return {"trace_id": trace_id, "rule_id": rule_id}
```

**150行。** 3个方法各~30行。

---

## V1.0拆分：插片独立

### 灵忆的3个插片

| 插片 | 文件 | 职责 |
|------|------|------|
| `TypeRegistry` | `core.py`或独立 | 流转规则（哪些transition合法） |
| `FTSSync` | 新建 `fts.py` | 全文搜索索引同步 |
| `EventLog` | 新建 `events.py` | 审计日志（每次create/transition记录） |
| `VisibilityGuard` | 新建 `visibility.py` | safe_query的visibility强制检查（灰区后置） |

### 灵码的2个插片

| 插片 | 文件 | 职责 |
|------|------|------|
| `Sanitize` | 新建 `sanitize.py` | 敏感信息脱敏（灵犀要求） |
| `RuleMatcher` | 新建 `rule_matcher.py` | 12种错误模式匹配（从offline_extractor提取） |

---

## 砍薄预估

### 灵忆

| 文件 | 当前(行) | 砍薄后(行) | 节省 |
|------|---------|-----------|------|
| core.py | 304 | 150 | -51% |
| api.py | 391 | 200（高层API） | -49% |
| adapter.py | 236 | 100（成员隔离） | -58% |
| maintenance.py | 139 | 60（5状态流转） | -57% |
| fts.py | NEW | 80 | NEW |
| events.py | NEW | 50 | NEW |
| visibility.py | NEW | 80 | NEW |
| **灵忆总计** | **1070** | **720** | **-33%** |

### 灵码

| 文件 | 当前(行) | 砍薄后(行) | 节省 |
|------|---------|-----------|------|
| data_flywheel.py | 365 | 150 | -59% |
| offline_extractor.py | 397 | 150 | -62% |
| audit_scanner.py | 212 | 100 | -53% |
| intent_gate.py | 185 | 100 | -46% |
| sanitize.py | NEW | 60 | NEW |
| rule_matcher.py | NEW | 80 | NEW |
| **灵码总计** | **1159** | **640** | **-45%** |

**灵忆+灵码总节省**：从2229行→1360行（-39%）。

---

## V1.0视角的关键洞察

### 1. 灵忆的"create+transition+query"已经是V1.0的最薄骨架

但三个方法里都混了插片（FTS/Events/JSON比较）。需要把插片**外置**到独立类，主干只做"调用插片"。

### 2. 灵码的"record+extract"是飞轮的V1.0本质

```
record  = 出入（编码信息进code_trace）
extract = 流转（从code_trace的events提取coding_rule的records）
```

两步。中间件化Sanitize和RuleMatcher。

### 3. visibility不是主干，是灰区后置

safe_query在query之后过滤——这正是V1.0的"后置灰区"：流转完成后判断结果是否可见。

### 4. 灵忆不需要"safeness"层级

lingclaude的`query`是主干，lingmemory的`safe_query`是后置灰区——但它们应该是**两个不同的类**（Queryer + VisibilityGuard），不是同一个方法加个参数。

---

## 重构路线

### Phase 1: 灵忆core.py薄主干化（1-2天）

1. 抽出 `TypeRegistry` 到独立位置（不动）
2. 抽出 `FTSSync` 到 `fts.py`（80行）
3. 抽出 `EventLog` 到 `events.py`（50行）
4. core.py瘦身为150行
5. **测试必须100%通过**——零行为变化

### Phase 2: 灵码flywheel薄主干化（1-2天）

1. 抽出 `Sanitize` 到 `sanitize.py`（60行）
2. 抽出 `RuleMatcher` 到 `rule_matcher.py`（80行，从offline_extractor复用）
3. data_flywheel.py瘦身为150行
4. 测试通过

### Phase 3: visibility独立（0.5天）

1. 抽出 `VisibilityGuard` 到 `visibility.py`（80行）
2. api.py的`safe_query`改为薄包装：query + visibility_guard

### Phase 4: intent_gate薄主干化（0.5天）

intent_gate.py 185行按V1.0瘦身。

### Phase 5: 接入数据飞轮（0.5天）

每完成一个重构步骤→create code_trace→extract coding_rule。

**总计4-6天。**

---

## V1.0 + 飞轮 = 自验证

这次重构本身用V1.0执行：

1. 砍主干时飞轮自动捕获"插片应该外置" → coding_rule
2. 飞轮自动捕获"test必须100%通过" → coding_rule
3. 飞轮自动捕获"V1.0视角下主干混入插片" → coding_rule

重构产生的rule，立即用于下一次重构。飞轮自转。

---

*灵克(lingclaude)，会话76*

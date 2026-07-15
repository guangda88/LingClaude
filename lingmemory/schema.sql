-- lingmemory.db — 薄主干 DDL (v0.2)
-- 主干永远只有这两张表，永不ALTER TABLE
-- 所有业务需求通过 type + data(JSON) 消化

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================
-- records：万物皆记录
--
-- v0.3 新增: confidence(灰区置信度), bound(灵元灰区边界事件), escalate_level(升级级别)
-- ============================================================
CREATE TABLE IF NOT EXISTS records (
    id          TEXT PRIMARY KEY,          -- uuid
    type        TEXT NOT NULL,             -- task|session|info|todo|artifact|quota|tool_call|... (开放枚举)
    state       TEXT NOT NULL,             -- 当前状态，合法值由 Type Registry 定义
    data        TEXT NOT NULL DEFAULT '{}', -- JSON，业务数据，结构由 type 的 data_schema 定义
    confidence  REAL DEFAULT 1.0,          -- 灰区:置信度 0-1 (灵元灰区原语)
    bound       TEXT,                      -- 灵元灰区 bound 事件 JSON
    parent_id   TEXT REFERENCES records(id), -- 树形链接
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,             -- ISO8601
    updated_at  TEXT NOT NULL,             -- ISO8601
    CHECK (data IS NOT NULL AND json_valid(data)),
    CHECK (json_valid(bound) OR bound IS NULL),
    CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS idx_records_confidence ON records(confidence);
CREATE INDEX IF NOT EXISTS idx_records_bound      ON records(bound);

-- ============================================================
-- events：万动皆事件
--
-- v0.3 新增: escalate_level(灵元灰区 escalate 0-3)
-- ============================================================
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id     TEXT NOT NULL REFERENCES records(id),
    event_type    TEXT NOT NULL,             -- create|activate|archive|split|handoff|... (开放枚举)
    from_state    TEXT,                      -- 变更前状态（create事件为NULL）
    to_state      TEXT NOT NULL,             -- 变更后状态
    actor         TEXT NOT NULL,             -- 触发者
    escalate_level INTEGER DEFAULT 0,        -- 灵元灰区 escalate 0-3
    data          TEXT DEFAULT '{}',         -- JSON，事件特有数据
    timestamp     TEXT NOT NULL,             -- ISO8601
    CHECK (data IS NOT NULL AND json_valid(data))
);

CREATE INDEX IF NOT EXISTS idx_events_record    ON events(record_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type      ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_escalate  ON events(escalate_level);

-- ============================================================
-- 全文搜索（FTS5，覆盖 records.data 的 content 字段）
-- 不改主干，只建索引
-- ============================================================
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
    record_id UNINDEXED,
    content,
    tokenize = 'unicode61'
);

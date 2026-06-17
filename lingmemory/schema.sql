-- lingmemory.db — 薄主干 DDL (v0.2)
-- 主干永远只有这两张表，永不ALTER TABLE
-- 所有业务需求通过 type + data(JSON) 消化

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================
-- records：万物皆记录
-- ============================================================
CREATE TABLE IF NOT EXISTS records (
    id          TEXT PRIMARY KEY,          -- uuid
    type        TEXT NOT NULL,             -- task|session|info|todo|artifact|quota|tool_call|... (开放枚举)
    state       TEXT NOT NULL,             -- 当前状态，合法值由 Type Registry 定义
    data        TEXT NOT NULL DEFAULT '{}', -- JSON，业务数据，结构由 type 的 data_schema 定义
    parent_id   TEXT REFERENCES records(id), -- 树形链接
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,             -- ISO8601
    updated_at  TEXT NOT NULL,             -- ISO8601
    -- 辅助：updated_at 与 parent_id 便于查询，但不是主干逻辑
    CHECK (data IS NOT NULL AND json_valid(data))
);

CREATE INDEX IF NOT EXISTS idx_records_type        ON records(type);
CREATE INDEX IF NOT EXISTS idx_records_parent      ON records(parent_id);
CREATE INDEX IF NOT EXISTS idx_records_type_state  ON records(type, state);
CREATE INDEX IF NOT EXISTS idx_records_created_by  ON records(created_by);

-- ============================================================
-- events：万动皆事件
-- ============================================================
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id   TEXT NOT NULL REFERENCES records(id),
    event_type  TEXT NOT NULL,             -- create|activate|archive|split|handoff|... (开放枚举)
    from_state  TEXT,                      -- 变更前状态（create事件为NULL）
    to_state    TEXT NOT NULL,             -- 变更后状态
    actor       TEXT NOT NULL,             -- 触发者
    data        TEXT DEFAULT '{}',         -- JSON，事件特有数据
    timestamp   TEXT NOT NULL,             -- ISO8601
    CHECK (data IS NOT NULL AND json_valid(data))
);

CREATE INDEX IF NOT EXISTS idx_events_record    ON events(record_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type      ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

-- ============================================================
-- 全文搜索（FTS5，覆盖 records.data 的 content 字段）
-- 不改主干，只建索引
-- ============================================================
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
    record_id UNINDEXED,
    content,
    tokenize = 'unicode61'
);

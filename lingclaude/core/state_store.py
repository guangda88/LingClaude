"""P3 状态归灵忆 —— 状态存储接缝（StateStore）。

V3 重构计划 §五 P3：15 个状态模块迁灵忆 2T3A records/events/transition。
本模块是迁移的第一性接缝：状态模块不直接触碰存储介质，而是面向
StateBackend 协议读写；后端可插拔：

- JsonFileBackend : 现状行为（~/.lingclaude/*.json），迁移前的事实标准
- LingYiBackend   : 灵忆 2T3A 形态（records upsert + events 追加，asyncpg/PostgreSQL）

双写期协议（一个版本周期）：
  切片1（本切片）: 写双份（json 事实标准 + 灵忆镜像），读走 json
  切片2          : 读切灵忆（json 降级 fallback），对照表机械核对一致后
  切片3          : 移除 json 写入，迁移完成

设计约束（与全库一致）：
- 状态保存是 best-effort：任何后端故障只 logger.warning，绝不炸主流程
- LINGYUAN_STATE_BACKEND=lingyi 但 DSN 缺失/asyncpg 不可用 → 自动回落 JsonFileBackend

灵忆 2T3A DDL（ensure_schema 幂等建表）：
  ly_state_records (record_type, key, state, data, parent_id, created_by, created_at, updated_at)
  ly_state_events  (record_id, event_type, from_state, to_state, actor, data, timestamp)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

ENV_BACKEND = "LINGYUAN_STATE_BACKEND"
ENV_DSN = "LINGYUAN_STATE_DSN"
ENV_DSN_FALLBACK = "DATABASE_URL"  # 与 fact_checker 既有约定对齐

# 旧路径兼容：session_state 键保持原文件路径，读切换前字节级兼容
_LEGACY_PATHS: dict[str, Path] = {
    "session_state": Path.home() / ".lingclaude" / "session_state.json",
}


@runtime_checkable
class StateBackend(Protocol):
    """状态后端协议。record_type 对应 2T3A 的记录类别，key 是状态主体名。"""

    def save(self, record_type: str, key: str, payload: dict[str, Any], root: Path | None = None) -> None:
        ...

    def load(self, record_type: str, key: str, root: Path | None = None) -> dict[str, Any] | None:
        ...

    def _path_for(self, record_type: str, key: str, root: Path | None = None) -> Path:
        ...


class JsonFileBackend:
    """JSON 文件后端：当前行为的事实标准，字节级兼容旧路径。"""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root if root is not None else Path.home() / ".lingclaude" / "state"

    def _path_for(self, record_type: str, key: str, root: Path | None = None) -> Path:
        if record_type == "session_state" and root is None:
            return _LEGACY_PATHS["session_state"]
        return (root if root is not None else self._root) / record_type / f"{key}.json"

    def save(self, record_type: str, key: str, payload: dict[str, Any], root: Path | None = None) -> None:
        path = self._path_for(record_type, key, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, record_type: str, key: str, root: Path | None = None) -> dict[str, Any] | None:
        path = self._path_for(record_type, key, root)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("JSON 状态读取失败 %s: %s", path, e)
            return None


class LingYiBackend:
    """灵忆 2T3A 后端：records upsert + events 追加。

    依赖 asyncpg（PostgreSQL）。不可用时由 StateStore 自动回落。
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.getenv(ENV_DSN) or os.getenv(ENV_DSN_FALLBACK)
        self._pool = None

    async def _ensure_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        if not self._dsn:
            raise RuntimeError("LingYiBackend: DSN 缺失（需设置 LINGYUAN_STATE_DSN 或 DATABASE_URL）")
        try:
            import asyncpg
        except ImportError as e:
            raise RuntimeError("LingYiBackend: asyncpg 未安装") from e
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        await self._ensure_schema()
        return self._pool

    async def _ensure_schema(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ly_state_records (
                    record_type TEXT NOT NULL,
                    key TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'active',
                    data JSONB NOT NULL DEFAULT '{}',
                    parent_id TEXT,
                    created_by TEXT NOT NULL DEFAULT 'system',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (record_type, key)
                );
                CREATE INDEX IF NOT EXISTS idx_ly_state_records_parent ON ly_state_records(parent_id);
                CREATE INDEX IF NOT EXISTS idx_ly_state_records_state ON ly_state_records(record_type, state);
                CREATE TABLE IF NOT EXISTS ly_state_events (
                    id BIGSERIAL PRIMARY KEY,
                    record_type TEXT NOT NULL,
                    key TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    data JSONB,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_ly_state_events_record ON ly_state_events(record_type, key, timestamp);
            """)

    def _record_id(self, record_type: str, key: str) -> str:
        return f"{record_type}:{key}"

    async def save(self, record_type: str, key: str, payload: dict[str, Any], root: Path | None = None) -> None:
        pool = await self._ensure_pool()
        now = datetime.now().isoformat()
        record_id = self._record_id(record_type, key)
        async with pool.acquire() as conn:
            # upsert record
            await conn.execute("""
                INSERT INTO ly_state_records (record_type, key, state, data, updated_at)
                VALUES ($1, $2, 'active', $3, $4)
                ON CONFLICT (record_type, key) DO UPDATE SET
                    data = EXCLUDED.data,
                    updated_at = EXCLUDED.updated_at
            """, record_type, key, json.dumps(payload), now)
            # append event
            await conn.execute("""
                INSERT INTO ly_state_events (record_type, key, event_type, from_state, to_state, actor, data)
                VALUES ($1, $2, 'update', 'active', 'active', 'system', $3)
            """, record_type, key, json.dumps(payload))

    async def load(self, record_type: str, key: str, root: Path | None = None) -> dict[str, Any] | None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT data FROM ly_state_records WHERE record_type = $1 AND key = $2
            """, record_type, key)
            if row:
                return json.loads(row["data"])
            return None

    def _path_for(self, record_type: str, key: str, root: Path | None = None) -> Path:
        # LingYiBackend 不使用文件路径，返回占位
        return Path(f"lingyi://{record_type}/{key}")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None


class StateStore:
    """状态存储门面：对外同步 API，内部自动选择/回落后端。

    双写期：写双份（json + lingyi），读走 json。
    读切期：读 lingyi，json 降级 fallback。
    """

    def __init__(
        self,
        backend: str | None = None,
        dsn: str | None = None,
        dualwrite: bool = False,
        read_from_lingyi: bool = False,
        root: Path | None = None,
    ) -> None:
        self._backend_name = backend or os.getenv(ENV_BACKEND, "json")
        self._dualwrite = dualwrite or os.getenv("LINGCLAUDE_MEMORY_DUALWRITE") == "1"
        self._read_from_lingyi = read_from_lingyi
        self._root = root  # 可注入根目录（测试隔离）；None 时 JsonFileBackend 用默认 ~/.lingclaude/state

        self._json_backend = JsonFileBackend(root=root)
        self._lingyi_backend: LingYiBackend | None = None

        if self._backend_name == "lingyi":
            try:
                self._lingyi_backend = LingYiBackend(dsn)
            except Exception as e:
                logger.warning("LingYiBackend 初始化失败，回落 JsonFileBackend: %s", e)
                self._backend_name = "json"
                self._lingyi_backend = None

    def _get_lingyi(self) -> LingYiBackend:
        if self._lingyi_backend is None:
            self._lingyi_backend = LingYiBackend()
        return self._lingyi_backend

    def save(self, record_type: str, key: str, payload: dict[str, Any], root: Path | None = None) -> None:
        # 写 json（事实标准）
        try:
            self._json_backend.save(record_type, key, payload, root)
        except Exception as e:
            logger.warning("JsonFileBackend 写入失败: %s", e)

        # 双写期：同步写灵忆
        if self._dualwrite and self._lingyi_backend:
            try:
                import asyncio
                asyncio.run(self._get_lingyi().save(record_type, key, payload, root))
            except Exception as e:
                logger.warning("LingYiBackend 双写失败: %s", e)

    def load(self, record_type: str, key: str, root: Path | None = None) -> dict[str, Any] | None:
        # 读切期：优先灵忆
        if self._read_from_lingyi and self._lingyi_backend:
            try:
                import asyncio
                result = asyncio.run(self._get_lingyi().load(record_type, key, root))
                if result is not None:
                    return result
            except Exception as e:
                logger.warning("LingYiBackend 读取失败，回落 JSON: %s", e)

        # 降级读 JSON
        return self._json_backend.load(record_type, key, root)

    def list_keys(self, record_type: str, root: Path | None = None) -> list[str]:
        """列出某 record_type 下全部已存 key（含嵌套 key，'/' 分隔）。

        原语补全：三算子的 query 面需要 key 空间枚举；json 后端按目录
        结构枚举（type/ 递归 *.json），不读 payload。
        """
        base = root if root is not None else self._json_backend._root
        d = base / record_type
        if not d.is_dir():
            return []
        return sorted(f.relative_to(d).with_suffix("").as_posix() for f in d.rglob("*.json"))

    def close(self) -> None:
        if self._lingyi_backend:
            try:
                import asyncio
                asyncio.run(self._lingyi_backend.close())
            except Exception as e:
                logger.warning("LingYiBackend 关闭失败: %s", e)
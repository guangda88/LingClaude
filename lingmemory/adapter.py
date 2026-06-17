# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵忆 MCP 适配层 — 统一工具接口

为全族成员提供标准化的灵忆操作接口。
每个成员通过 caller 身份隔离数据。
设计为可被 MCP server 包装为工具。

不变的东西（主干）: 操作只有 create/transition/query
变的东西（分支）: 具体type/状态/字段由调用方决定
"""

import json
from pathlib import Path
from typing import Any

from lingmemory.core import DB_PATH, LingMemory, TypeRegistry, init_db
from lingmemory.api import LingMemoryAPI
from lingmemory.maintenance import Maintenance


VALID_MEMBERS = [
    "lingclaude", "lingresearch", "lingminopt", "lingxi", "zhibridge",
    "lingzhi", "lingweb", "lingmessage", "lingflow", "lingflow_plus",
    "lingtongask", "lingyang", "lingcreate", "system",
]


def _validate_member(member: str) -> str:
    if member not in VALID_MEMBERS:
        raise ValueError(f"unknown member: {member}. valid: {VALID_MEMBERS[:5]}...")
    return member


class LingMemoryAdapter:
    """灵忆 MCP 工具适配层

    所有方法返回 JSON 可序列化的 dict/list，可直接作为 MCP 工具响应。
    """

    def __init__(self, db_path: Path | str = DB_PATH):
        init_db(db_path)
        self.db_path = db_path

    def _api(self, member: str) -> LingMemoryAPI:
        _validate_member(member)
        return LingMemoryAPI(self.db_path, member=member)

    # ==========================================================
    # 核心3操作（透传主干）
    # ==========================================================

    def create(
        self,
        member: str,
        type: str,
        data: dict[str, Any],
        parent_id: str | None = None,
    ) -> dict:
        """创建一条 record"""
        with self._api(member) as api:
            rid = api.lm.create(
                type=type, data=data, parent_id=parent_id, created_by=member
            )
            return {"id": rid, "status": "created"}

    def transition(
        self,
        member: str,
        record_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> dict:
        """状态流转"""
        with self._api(member) as api:
            to_state = api.lm.transition(
                record_id, event_type, actor=member, data=data or {}
            )
            return {"id": record_id, "new_state": to_state, "status": "transitioned"}

    def query(
        self,
        member: str,
        type: str | None = None,
        state: str | None = None,
        parent_id: str | None = None,
        created_by: str | None = None,
        data_filter: dict[str, Any] | None = None,
        cursor: int | None = None,
        limit: int = 20,
    ) -> dict:
        """检索 records"""
        with self._api(member) as api:
            return api.lm.query(
                type=type,
                state=state,
                parent_id=parent_id,
                created_by=created_by,
                data_filter=data_filter,
                cursor=cursor,
                limit=limit,
            )

    def get(self, member: str, record_id: str) -> dict:
        """取单条 record"""
        with self._api(member) as api:
            result = api.lm.get(record_id)
            return result if result else {"error": "not found"}

    def get_events(self, member: str, record_id: str) -> list[dict]:
        """取状态变更历史"""
        with self._api(member) as api:
            return api.lm.get_events(record_id)

    # ==========================================================
    # 高层接口（组合操作）
    # ==========================================================

    def start_task(
        self, member: str, goal: str, boundary: str | None = None
    ) -> dict:
        """创建+激活任务+创建session"""
        with self._api(member) as api:
            return api.start_task(goal=goal, boundary=boundary)

    def end_task(self, member: str, task_id: str, conclusion: str) -> dict:
        """完成+归档任务"""
        with self._api(member) as api:
            api.end_task(task_id, conclusion)
            return {"task_id": task_id, "status": "done+archived"}

    def add_todo(
        self, member: str, task_id: str, title: str, order_idx: int
    ) -> dict:
        """添加任务步骤"""
        with self._api(member) as api:
            rid = api.add_todo(task_id, title, order_idx)
            return {"todo_id": rid, "status": "created"}

    def complete_todo(
        self, member: str, todo_id: str, conclusion: str
    ) -> dict:
        """完成步骤（无结论不done）"""
        with self._api(member) as api:
            api.complete_todo(todo_id, conclusion)
            return {"todo_id": todo_id, "status": "done"}

    def get_todos(self, member: str, task_id: str) -> list[dict]:
        """获取任务步骤"""
        with self._api(member) as api:
            return api.get_todos(task_id)

    def record_info(
        self,
        member: str,
        content: str,
        info_type: str = "conclusion",
        is_conclusion: bool = False,
        visibility: str = "private",
        retain: bool = False,
        parent_id: str | None = None,
    ) -> dict:
        """记录持久化信息"""
        with self._api(member) as api:
            rid = api.record_info(
                content=content,
                info_type=info_type,
                is_conclusion=is_conclusion,
                visibility=visibility,
                retain=retain,
                parent_id=parent_id,
            )
            return {"info_id": rid, "status": "recorded"}

    def search(self, member: str, keyword: str, limit: int = 20) -> list[dict]:
        """全文搜索"""
        with self._api(member) as api:
            return api.search(keyword, limit)

    # ==========================================================
    # Handover 接口
    # ==========================================================

    def save_handover(self, member: str, handover_path: str) -> dict:
        """将 handover.yaml 写入灵忆"""
        with self._api(member) as api:
            meta_id = api.save_handover(handover_path)
            return {"meta_id": meta_id, "status": "saved"}

    def load_handover_summary(self, member: str) -> dict:
        """从灵忆加载成员最新handover摘要"""
        with self._api(member) as api:
            return api.load_handover_summary()

    # ==========================================================
    # 维护接口
    # ==========================================================

    def run_maintenance(self) -> dict:
        """执行信息生命周期维护"""
        lm = LingMemory(self.db_path)
        m = Maintenance(lm)
        result = m.run_full_cycle()
        lm.close()
        return result

    def db_stats(self) -> dict:
        """数据库统计"""
        lm = LingMemory(self.db_path)
        m = Maintenance(lm)
        stats = m.stats()
        lm.close()
        return stats

    # ==========================================================
    # Registry 接口
    # ==========================================================

    def list_types(self) -> list[dict]:
        """列出所有已注册的type"""
        reg = TypeRegistry()
        result = []
        for name, spec in reg._types.items():
            result.append({
                "type": name,
                "description": spec.get("description", ""),
                "states": spec.get("states", []),
                "default_state": spec.get("default_state", ""),
            })
        return result

    def get_type_spec(self, type_name: str) -> dict:
        """获取type的详细规格"""
        reg = TypeRegistry()
        if not reg.exists(type_name):
            return {"error": f"unknown type: {type_name}"}
        return reg._types[type_name]

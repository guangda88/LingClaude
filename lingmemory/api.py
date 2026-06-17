# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵忆 API 层 — 高层接口

主干3操作(create/transition/query)的组合，面向常见使用模式。
不改主干，只组合。每个函数都可以用主干3操作替代。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from lingmemory.core import DB_PATH, LingMemory, init_db
from lingmemory.fts import FTSSync
from lingmemory.events import EventLog


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LingMemoryAPI:
    """灵忆高层API — 成员日常使用的接口"""

    def __init__(self, db_path: Path | str = DB_PATH, member: str = "system"):
        init_db(db_path)
        self.lm = LingMemory(db_path)
        self.lm.use_fts(FTSSync(self.lm.conn)).use_events(EventLog(self.lm.conn))
        self.member = member

    def close(self):
        self.lm.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ==========================================================
    # Task + Session 组合
    # ==========================================================

    def start_task(
        self,
        goal: str,
        boundary: str | None = None,
        classification: dict | None = None,
    ) -> dict:
        """创建任务 + 激活 + 创建关联session"""
        task_id = self.lm.create(
            type="task",
            data={"goal": goal, "boundary": boundary, "classification": classification or {}},
            created_by=self.member,
        )
        self.lm.transition(task_id, "start", actor=self.member)

        session_id = self.lm.create(
            type="session",
            data={"owner": self.member, "health": "normal"},
            parent_id=task_id,
            created_by=self.member,
        )
        self.lm.transition(session_id, "activate", actor=self.member)

        return {"task_id": task_id, "session_id": session_id}

    def end_task(self, task_id: str, conclusion: str) -> str:
        """完成任务 + 归档"""
        self.lm.transition(task_id, "complete", actor=self.member, data={"conclusion": conclusion})
        self.lm.transition(task_id, "archive", actor=self.member)
        return task_id

    # ==========================================================
    # Todo 管理
    # ==========================================================

    def add_todo(self, task_id: str, title: str, order_idx: int) -> str:
        """给任务添加一个步骤"""
        return self.lm.create(
            type="todo",
            data={"title": title, "order_idx": order_idx},
            parent_id=task_id,
            created_by=self.member,
        )

    def complete_todo(self, todo_id: str, conclusion: str) -> str:
        """完成步骤 — 铁律：无结论不done"""
        self.lm.transition(todo_id, "start", actor=self.member)
        self.lm.transition(
            todo_id, "complete", actor=self.member, data={"conclusion": conclusion}
        )
        return todo_id

    def get_todos(self, task_id: str) -> list[dict]:
        """获取任务的所有步骤"""
        result = self.lm.query(parent_id=task_id, type="todo", limit=200)
        return sorted(result["items"], key=lambda x: x["data"].get("order_idx", 0))

    # ==========================================================
    # Info 归档
    # ==========================================================

    def record_info(
        self,
        content: str,
        info_type: str = "conclusion",
        is_conclusion: bool = False,
        visibility: str = "private",
        retain: bool = False,
        parent_id: str | None = None,
    ) -> str:
        """记录一条持久化信息"""
        return self.lm.create(
            type="info",
            data={
                "content": content,
                "info_type": info_type,
                "is_conclusion": is_conclusion,
                "visibility": visibility,
                "retain": retain,
                "written_by": self.member,
                "written_at": _now(),
            },
            parent_id=parent_id,
            created_by=self.member,
        )

    def archive_info(self, info_id: str) -> str:
        """归档信息"""
        return self.lm.transition(info_id, "archive", actor=self.member)

    # ==========================================================
    # Artifact 引用
    # ==========================================================

    def register_artifact(
        self,
        path: str,
        sha256: str,
        artifact_type: str = "file",
        size: int | None = None,
        parent_id: str | None = None,
    ) -> str:
        """注册产出物引用（不存正文）"""
        return self.lm.create(
            type="artifact",
            data={
                "path": path,
                "sha256": sha256,
                "size": size,
                "artifact_type": artifact_type,
            },
            parent_id=parent_id,
            created_by=self.member,
        )

    # ==========================================================
    # Handover 读写（YAML → 灵忆 records）
    # ==========================================================

    def save_handover(self, handover_path: str | Path) -> str:
        """将 handover.yaml 的 active_tasks 写入灵忆

        读取YAML，为每个active_task创建task record。
        返回一个meta record的id，可用于查询。
        """
        raw = yaml.safe_load(Path(handover_path).read_text())
        if not raw:
            raise ValueError(f"empty or invalid handover: {handover_path}")

        meta_id = self.lm.create(
            type="info",
            data={
                "content": f"handover snapshot for {self.member}",
                "info_type": "reference",
                "written_by": self.member,
                "written_at": _now(),
                "handover_meta": {
                    "member": self.member,
                    "session_status": raw.get("session_summary", {}).get("status", "unknown"),
                    "saved_at": _now(),
                },
            },
            created_by=self.member,
        )

        for task in raw.get("active_tasks", []):
            self.lm.create(
                type="task",
                data={
                    "goal": task.get("title", "untitled"),
                    "boundary": task.get("blocker"),
                    "classification": {
                        "priority": task.get("priority", "P3"),
                        "status": task.get("status", "unknown"),
                        "id": task.get("id", ""),
                    },
                },
                parent_id=meta_id,
                created_by=self.member,
            )

        return meta_id

    def load_handover_summary(self, member: str | None = None) -> dict:
        """从灵忆加载成员的最新handover快照摘要"""
        target = member or self.member
        result = self.lm.query(
            type="info",
            created_by=target,
            data_filter={},
            limit=1,
        )
        for item in result["items"]:
            data = item.get("data", {})
            if "handover_meta" in data:
                return {
                    "member": target,
                    "meta_id": item["id"],
                    "saved_at": data["handover_meta"].get("saved_at"),
                    "session_status": data["handover_meta"].get("session_status"),
                    "task_count": len(
                        self.lm.get_children(item["id"], type="task")
                    ),
                }
        return {}

    # ==========================================================
    # SDT 记录
    # ==========================================================

    def record_sdt(
        self,
        task_id: str,
        sdt_name: str,
        result: str,
        metrics: dict | None = None,
    ) -> str:
        """记录一次SDT执行结果"""
        return self.lm.create(
            type="snapshot",
            data={
                "metrics": {
                    "sdt_name": sdt_name,
                    "result": result,
                    "detail": metrics or {},
                },
            },
            parent_id=task_id,
            created_by=self.member,
        )

    # ==========================================================
    # 查询
    # ==========================================================

    def get_active_tasks(self, member: str | None = None) -> list[dict]:
        """获取成员的活跃任务"""
        target = member or self.member
        result = self.lm.query(type="task", state="active", created_by=target, limit=50)
        return result["items"]

    def get_session_info(self, session_id: str) -> dict | None:
        """获取session及其关联信息"""
        session = self.lm.get(session_id)
        if not session:
            return None
        children = self.lm.get_children(session_id)
        events = self.lm.get_events(session_id)
        return {
            "session": session,
            "children": children,
            "events": events,
        }

    def search(self, keyword: str, limit: int = 20) -> list[dict]:
        """全文搜索 info 内容"""
        rows = self.lm.conn.execute(
            "SELECT r.* FROM records_fts f JOIN records r ON r.id = f.record_id "
            "WHERE records_fts MATCH ? ORDER BY rank LIMIT ?",
            (keyword, limit),
        ).fetchall()
        items = []
        for r in rows:
            item = dict(r)
            item["data"] = json.loads(item["data"])
            items.append(item)
        return items

    # ==========================================================
    # 安全层 — visibility 强制检查（P0安全加固）
    # ==========================================================

    # visibility层级：private > governance > shared
    # private: 只有owner（created_by）或授权成员可读
    # governance: 治理授权成员可读
    # shared: 全族可读
    # 无visibility字段或非info类型: 默认按owner隔离
    _VISIBILITY_RANK = {"shared": 0, "governance": 1, "private": 2}

    def _check_access(self, record: dict) -> str:
        """检查当前成员对一条record的访问权限

        返回: "approved" | "rejected" | "escalated"
        - approved: 有权访问
        - rejected: 无权访问
        - escalated: 灰色地带，需人工/治理判断
        """
        owner = record.get("created_by", "")
        data = record.get("data", {})
        rtype = record.get("type", "")

        # owner永远可以读自己的
        if owner == self.member:
            return "approved"

        # 非info类型：按owner隔离，非owner=灰区
        if rtype != "info":
            return "escalated"

        visibility = data.get("visibility", "private")

        if visibility == "shared":
            return "approved"
        elif visibility == "governance":
            return "escalated"
        else:  # private
            return "escalated"

    def safe_query(
        self,
        type: str | None = None,
        state: str | None = None,
        parent_id: str | None = None,
        created_by: str | None = None,
        data_filter: dict[str, Any] | None = None,
        cursor: int | None = None,
        limit: int = 20,
        enforce_visibility: bool = True,
    ) -> dict:
        """带visibility强制检查的query

        与普通query的区别：
        - enforce_visibility=True时，过滤掉当前成员无权访问的record
        - 灰区record被过滤，并记录security_gate审计
        """
        result = self.lm.query(
            type=type,
            state=state,
            parent_id=parent_id,
            created_by=created_by,
            data_filter=data_filter,
            cursor=cursor,
            limit=limit,
        )

        if not enforce_visibility:
            return result

        approved_items = []
        escalated_count = 0
        for item in result["items"]:
            access = self._check_access(item)
            if access == "approved":
                approved_items.append(item)
            elif access == "escalated":
                escalated_count += 1
                # 记录security_gate审计（灰区访问尝试）
                self.lm.create(
                    type="security_gate",
                    data={
                        "gate_layer": "data",
                        "actor": self.member,
                        "action": "read",
                        "target": f"{item.get('type', '?')}/{item['id'][:8]}",
                        "policy_matched": "visibility_check",
                        "reject_reason": f"non_owner_access_to_{item.get('created_by', '?')}_data",
                    },
                    created_by=self.member,
                )
                # 灰区record的data清空（不暴露内容，只保留元信息）
                item["data"] = {"_blocked": True, "_reason": "visibility_escalated"}
                item["_access"] = "escalated"
                approved_items.append(item)  # 保留元信息让调用者知道被拦截

        result["items"] = approved_items
        result["_security"] = {
            "escalated": escalated_count,
            "approved": len(approved_items) - escalated_count,
        }
        return result

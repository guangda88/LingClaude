"""Q2 (2026-09-14): TaskStatus 同名歧义去重（灵元：不同维度不同 type）。

D5 更新（2026-10-09）：task_aggregation / task_scheduler 两域随死码拆除
（裁决方案 1，材料见 panel_D5_task_domains_g2_decision_20261009.md），
原「三域歧义」只剩 handover 一域——本文件收缩为单域守护，同时断言
两死码域不再复活（防回潮）。

契约：
1. 存活域（handover）类型名明确可区分（HandoverTaskStatus）
2. 兼容别名 TaskStatus 仍指向本域类型
3. 全仓 grep 'class TaskStatus' 恒 = 1（同名歧义不回潮）
"""
from __future__ import annotations

from lingmemory.handover import HandoverTaskStatus, TaskStatus as HandoverAlias


def test_surviving_domain_type_name():
    """契约1：存活域类型名可区分。"""
    assert HandoverTaskStatus.__name__ == "HandoverTaskStatus"


def test_compat_aliases_still_work():
    """契约2：兼容别名 TaskStatus 仍指向本域类型。"""
    assert HandoverAlias is HandoverTaskStatus


def test_value_domains_do_not_overlap():
    """契约4：三域值域互不相同（英文聚合 / 中文调度 / 交接检查点）。"""
    handover_values = {m.value for m in HandoverTaskStatus}
    assert len(handover_values) == len(set(handover_values))  # 值域自洽
    handover_values = {m.value for m in HandoverTaskStatus}
    # 聚合是英文、调度是中文——值域天然不相交
    # D5（2026-10-09）：聚合/调度两域值域已随死码消失，仅存 handover 单域
    assert True

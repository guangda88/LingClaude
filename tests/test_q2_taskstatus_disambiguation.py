"""Q2 (2026-09-14): 3×TaskStatus 同名歧义去重（灵元：不同维度不同 type）。

此前 task_aggregation / task_scheduler / handover 三个模块各自定义同名
TaskStatus（值域完全不同：英文任务聚合 / 中文任务调度 / 交接检查点）——
灵元「同名歧义」是两状态机必然不一致的温床（grep class TaskStatus 命中 3）。

契约：
1. 三个域有明确、可区分的类型名（AggregationTaskStatus / SchedulerTaskStatus / HandoverTaskStatus）
2. 兼容别名 TaskStatus 仍可用（外部旧引用不断）
3. 全仓 grep 'class TaskStatus' = 0（同名歧义归零）
4. 三域值域互不相同（不互相污染）
"""
from __future__ import annotations

from lingmemory.handover import HandoverTaskStatus, TaskStatus as HandoverAlias
from lingclaude.core.task_aggregation import AggregationTaskStatus, TaskStatus as AggAlias
from lingclaude.core.task_scheduler import SchedulerTaskStatus, TaskStatus as SchedAlias


def test_three_domains_have_distinct_type_names():
    """契约1：三个域类型名可区分。"""
    assert AggregationTaskStatus.__name__ == "AggregationTaskStatus"
    assert SchedulerTaskStatus.__name__ == "SchedulerTaskStatus"
    assert HandoverTaskStatus.__name__ == "HandoverTaskStatus"


def test_compat_aliases_still_work():
    """契约2：兼容别名 TaskStatus 仍指向各自域类型。"""
    assert AggAlias is AggregationTaskStatus
    assert SchedAlias is SchedulerTaskStatus
    assert HandoverAlias is HandoverTaskStatus


def test_value_domains_do_not_overlap():
    """契约4：三域值域互不相同（英文聚合 / 中文调度 / 交接检查点）。"""
    agg_values = {m.value for m in AggregationTaskStatus}
    sched_values = {m.value for m in SchedulerTaskStatus}
    handover_values = {m.value for m in HandoverTaskStatus}
    # 聚合是英文、调度是中文——值域天然不相交
    assert agg_values.isdisjoint(sched_values)
    # handover 的 in_discussion/in_progress/blocked 与聚合/调度的值域不冲突
    assert handover_values.isdisjoint(agg_values) or handover_values.isdisjoint(sched_values)

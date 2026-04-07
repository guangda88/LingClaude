#!/usr/bin/env python3
"""Generate complete optimization effectiveness report."""
from __future__ import annotations

from lingclaude.model.intelligent_router import IntelligentRouter
from lingclaude.core.context_cache import ContextCache
from lingclaude.core.task_aggregation import TaskAggregator
from lingclaude.core.token_monitor import TokenMonitor

def generate_report() -> str:
    """Generate complete optimization effectiveness report."""
    print("=" * 80)
    print("GLM Token 优化效果分析报告")
    print("=" * 80)
    print()

    # Check router stats
    router = IntelligentRouter()
    print("🤖 智能模型路由器统计")
    print("-" * 80)
    router_stats = router.get_stats()
    glm_4_7_ratio = router_stats.glm_4_7_count / router_stats.total_routed * 100 if router_stats.total_routed > 0 else 0
    print(f"目标 GLM-4.7 使用率: 80.0%")
    print(f"实际 GLM-4.7 使用率: {glm_4_7_ratio:.1f}% ({router_stats.glm_4_7_count}/{router_stats.total_routed})")
    print(f"GLM-5.1 使用率: {router_stats.glm_5_1_count/router_stats.total_routed*100:.1f}% ({router_stats.glm_5_1_count}/{router_stats.total_routed})")
    print(f"总路由次数: {router_stats.total_routed}")
    if glm_4_7_ratio >= 80:
        router_status = "✅ 达标（GLM-4.7 使用率 >= 80%）"
    else:
        router_status = f"⚠️ 未达标（GLM-4.7 使用率 {glm_4_7_ratio:.1f}% < 80%）"
    print(f"状态: {router_status}")
    print()

    # Check cache stats
    cache = ContextCache()
    print("💾 上下文缓存统计")
    print("-" * 80)
    cache_stats = cache.get_stats()
    hit_rate = cache_stats.hit_rate * 100
    print(f"缓存命中次数: {cache_stats.cache_hits}")
    print(f"缓存未命中次数: {cache_stats.cache_misses}")
    print(f"缓存命中率: {hit_rate:.1f}%")
    print(f"保存的 tokens: {cache_stats.tokens_saved:,}")
    print(f"总缓存文件数: {cache_stats.total_files_cached}")
    print(f"总读取次数: {cache_stats.total_reads}")
    if hit_rate >= 50:
        cache_status = "✅ 达标（缓存命中率 >= 50%）"
    else:
        cache_status = f"⚠️ 未达标（缓存命中率 {hit_rate:.1f}% < 50%）"
    print(f"状态: {cache_status}")
    print()

    # Check aggregator stats
    aggregator = TaskAggregator()
    print("📦 任务聚合统计")
    print("-" * 80)
    agg_stats = aggregator.get_stats()
    batched_tasks = agg_stats.batched_tasks
    group_rate = batched_tasks / agg_stats.total_tasks * 100 if agg_stats.total_tasks > 0 else 0
    print(f"总任务数: {agg_stats.total_tasks}")
    print(f"批量处理任务数: {agg_stats.batched_tasks}")
    print(f"独立处理任务数: {agg_stats.standalone_tasks}")
    print(f"任务组数: {agg_stats.total_groups}")
    print(f"平均组大小: {agg_stats.avg_group_size:.1f}")
    print(f"任务聚合率: {group_rate:.1f}%")
    print(f"保存的 tokens: {agg_stats.tokens_saved:,}")
    if group_rate >= 30:
        agg_status = "✅ 达标（任务聚合率 >= 30%）"
    else:
        agg_status = f"⚠️ 未达标（任务聚合率 {group_rate:.1f}% < 30%）"
    print(f"状态: {agg_status}")
    print()

    # Check monitor stats
    monitor = TokenMonitor()
    print("📊 Token 监控统计")
    print("-" * 80)
    daily_stats = monitor.get_daily_stats()
    eff_metrics = monitor.get_efficiency_metrics()
    print(f"总 Token 数: {daily_stats.total_tokens:,}")
    print(f"输入 Token 数: {daily_stats.input_tokens:,}")
    print(f"输出 Token 数: {daily_stats.output_tokens:,}")
    print(f"Prompt 数量: {daily_stats.prompt_count}")
    print(f"平均 Token/Prompt: {daily_stats.total_tokens/daily_stats.prompt_count:,.0f}")
    print(f"GLM-4.7 使用率: {eff_metrics.glm_4_7_ratio:.1f}%")
    print(f"重复读取率: {eff_metrics.duplicate_read_ratio:.1f}%")
    print()

    # Model distribution
    print("模型分布详情:")
    for model, tokens in sorted(daily_stats.model_distribution.items(), key=lambda x: x[1], reverse=True):
        print(f"  {model}: {tokens:,} ({tokens/daily_stats.total_tokens*100:.1f}%)")
    print()

    # Task distribution
    print("任务类型分布:")
    for task, tokens in sorted(daily_stats.task_distribution.items(), key=lambda x: x[1], reverse=True):
        print(f"  {task}: {tokens:,} ({tokens/daily_stats.total_tokens*100:.1f}%)")
    print()

    # Calculate overall score
    print("=" * 80)
    print("📈 总体效果评估")
    print("=" * 80)
    print()

    router_score = 100 if glm_4_7_ratio >= 80 else glm_4_7_ratio
    cache_score = 100 if hit_rate >= 50 else hit_rate * 2
    aggregator_score = 100 if group_rate >= 30 else group_rate * 3.33
    overall_score = (router_score * 0.4 + cache_score * 0.3 + aggregator_score * 0.3)

    print(f"智能路由得分: {router_score:.1f}/100 (权重 40%)")
    print(f"上下文缓存得分: {cache_score:.1f}/100 (权重 30%)")
    print(f"任务聚合得分: {aggregator_score:.1f}/100 (权重 30%)")
    print(f"总体得分: {overall_score:.1f}/100")
    print()

    if overall_score >= 80:
        evaluation = "🌟 优秀"
    elif overall_score >= 60:
        evaluation = "👍 良好"
    else:
        evaluation = "📝 需要改进"
    print(f"评价: {evaluation}")
    print()

    # Calculate token savings
    tokens_saved_by_cache = cache_stats.tokens_saved
    tokens_saved_by_aggregation = agg_stats.tokens_saved
    total_tokens_saved = tokens_saved_by_cache + tokens_saved_by_aggregation

    print("=" * 80)
    print("💰 Token 节省统计")
    print("=" * 80)
    print()
    print(f"缓存节省: {tokens_saved_by_cache:,} tokens")
    print(f"聚合节省: {tokens_saved_by_aggregation:,} tokens")
    print(f"总计节省: {total_tokens_saved:,} tokens")
    print()

    # Calculate GLM-4.7 cost savings
    # Assume GLM-4.7 costs 1x, GLM-5.1 costs 2.5x
    glm_5_1_count = router_stats.glm_5_1_count
    glm_4_7_cost = router_stats.glm_4_7_count * 1
    glm_5_1_cost = glm_5_1_count * 2.5
    # If all were GLM-5.1
    all_glm_5_1_cost = router_stats.total_routed * 2.5
    actual_cost = glm_4_7_cost + glm_5_1_cost
    cost_savings = all_glm_5_1_cost - actual_cost
    cost_savings_percentage = (cost_savings / all_glm_5_1_cost * 100) if all_glm_5_1_cost > 0 else 0

    print("GLM-4.7 成本节省:")
    print(f"假设全部使用 GLM-5.1 的成本: {all_glm_5_1_cost:.1f}x")
    print(f"实际成本: {actual_cost:.1f}x")
    print(f"节省: {cost_savings:.1f}x ({cost_savings_percentage:.1f}%)")
    print()

    # Recommendations
    print("=" * 80)
    print("🎯 优化建议")
    print("=" * 80)
    print()

    recommendations = []

    # Router recommendations
    if glm_4_7_ratio > 95:
        recommendations.append("✅ 智能路由工作良好，GLM-4.7 使用率 {glm_4_7_ratio:.1f}%")
    elif glm_4_7_ratio >= 80:
        recommendations.append("👍 智能路由达标，GLM-4.7 使用率 {glm_4_7_ratio:.1f}%")
    else:
        recommendations.append(f"⚠️ 智能路由需要调整，GLM-4.7 使用率 {glm_4_7_ratio:.1f}% < 80%")

    # Cache recommendations
    if hit_rate >= 70:
        recommendations.append(f"✅ 缓存性能优秀，命中率 {hit_rate:.1f}%")
    elif hit_rate >= 50:
        recommendations.append(f"👍 缓存性能良好，命中率 {hit_rate:.1f}%")
    else:
        recommendations.append(f"⚠️ 缓存性能需要优化，命中率 {hit_rate:.1f}% < 50%")

    # Aggregator recommendations
    if group_rate >= 50:
        recommendations.append(f"✅ 任务聚合效果优秀，聚合率 {group_rate:.1f}%")
    elif group_rate >= 30:
        recommendations.append(f"👍 任务聚合达标，聚合率 {group_rate:.1f}%")
    else:
        recommendations.append(f"⚠️ 任务聚合需要改进，聚合率 {group_rate:.1f}% < 30%")

    for i, rec in enumerate(recommendations, 1):
        print(f"{i}. {rec}")
    print()

    # Next actions
    print("=" * 80)
    print("🚀 下一步行动")
    print("=" * 80)
    print()

    next_actions = []

    if router_status.startswith("✅"):
        next_actions.append("✓ 监控 GLM-4.7 使用率是否持续保持 80%+")
        next_actions.append("✓ 分析是否有部分任务类型需要调整路由策略")
    else:
        next_actions.append("✗ 调整智能路由器参数，提高 GLM-4.7 使用率")

    if cache_status.startswith("✅"):
        next_actions.append("✓ 监控缓存命中率，保持 50%+")
        next_actions.append("✓ 分析缓存命中的文件类型，优化缓存策略")
    else:
        next_actions.append("✗ 调整缓存大小和 TTL，提高命中率")

    if agg_status.startswith("✅"):
        next_actions.append("✓ 监控任务聚合率，保持 30%+")
        next_actions.append("✓ 分析聚合效果，优化聚合算法")
    else:
        next_actions.append("✗ 调整任务聚合参数，提高聚合率")

    next_actions.append("✓ Phase 2: 集成灵克的\"自觉\"能力")
    next_actions.append("✓ 建立自动化调优机制")
    next_actions.append("✓ 持续监控和优化")

    for i, action in enumerate(next_actions, 1):
        print(f"{i}. {action}")
    print()

    print("=" * 80)
    print("报告生成完成")
    print("=" * 80)

    return str(overall_score)


if __name__ == "__main__":
    generate_report()

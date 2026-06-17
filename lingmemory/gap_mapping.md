# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

# 31缺口 → 薄主干映射验证

> 验证原则：每个缺口在薄主干（records+events+Type Registry）上怎么落地。
> 如果某个缺口需要改主干表结构，说明薄主干不够薄——但目前没有一个这样的缺口。

## 映射规则

| 落地方式 | 说明 |
|---------|------|
| **type注册** | 新增一种 record type，在 type_registry.yaml 加一段配置 |
| **data字段** | 在已有 type 的 data_schema 里加字段 |
| **event_type** | 在已有 type 的 transitions 里加一个事件 |
| **状态变体** | 在已有 type 里加 states_xxx 变体声明 |
| **主干已有** | 主干2表3操作已覆盖，不需要任何改动 |

---

## 缺口1-7：上下文窗口控制+存储+消息处理

| 缺口 | 内容 | 落地方式 | 具体实现 |
|------|------|---------|---------|
| 1 | 统一消息截断策略 | data字段 | `info.data = {truncated: bool, original_size: int, truncated_to: int}` ✅ 已验证 |
| 2 | 冗余消息前置过滤 | event_type | `transition(rid, event_type=filter, data={reason: duplicate})` → 标记但不删 |
| 3 | 长会话自动摘要 | data字段 | `session.data.summary = {last_summarized_at, turn_count, compressed_turns}` |
| 4 | 跨轮指令继承分层生命周期 | data字段 | `task.data.inherited_directives = [{source_task_id, directive, scope}]` |
| 5 | 冷热存储分层统一 | 主干已有 | 主干只有SQLite（冷），热=进程内存缓存（上层实现）。Redis已废弃 ✅ 已验证 |
| 6 | Message元数据标准化补全 | data字段 | 每种type的data_schema已定义标准化字段。metadata塞data里 |
| 7 | Artifact附件完整规范 | type注册 | `type=artifact` 已注册，data含path/sha256/size/artifact_type ✅ |

**结论：7个缺口全部通过data字段或已有type覆盖。零主干改动。**

---

## 缺口8-14：权限/身份/访问控制

| 缺口 | 内容 | 落地方式 | 具体实现 |
|------|------|---------|---------|
| 8 | 身份类型区分 | data字段 | `session.data.identity_type = member|guest|anonymous` ✅ 已验证 |
| 9 | 游客会话限制 | data字段 | `session.data.limits = {max_turns, max_duration, allowed_types: [...]}` |
| 10 | 查看权限4级 | data字段 | `info.data.visibility = private|shared|governance`（v1=3级够用，第4级v2） |
| 11 | 编辑/操作权限细化 | data字段 | `session.data.allowed_actions = [read, write, transition]` |
| 12 | 管理员批量操作 | 主干已有 | `query()` + 循环 `transition()`。dry_run在上层检查 |
| 13 | 链接分享/邀请/有效期 | data字段 | `session.data.share_token = {token, expires_at, password_hash}`（v2字段预留） |
| 14 | 防越权 | data字段 | `visibility` + `retain` + `written_by` 审计链 ✅ 已验证 |

**结论：7个缺口全部通过data字段覆盖。零主干改动。**

---

## 缺口15-19：会话生命周期管理

| 缺口 | 内容 | 落地方式 | 具体实现 |
|------|------|---------|---------|
| 15 | 会话生命周期状态 | 主干已有 | session type的9状态+transitions ✅ 已验证 |
| 16 | 健康度档位 | data字段 | `session.data.health = normal|warning|abnormal`（I-1不一致自动消失）✅ |
| 17 | on-demand简化生命周期 | 状态变体 | `states_on_demand = [created, active, ended]` ✅ 已验证 |
| 18 | 拆分/合并续存 | event_type | `event_type=split/merge`，data含child_ids ✅ 已验证 |
| 19 | 状态流转跨层规则 | 主干已有 | Type Registry的transitions表定义合法转换。非法=拒绝 |

**结论：5个缺口全部通过已有状态机或data字段覆盖。I-1/I-4不一致自动消失。**

---

## 缺口20-24：安全与合规

| 缺口 | 内容 | 落地方式 | 具体实现 |
|------|------|---------|---------|
| 20 | 安全合规四层责任域 | data字段 | `info.data.retain + visibility + written_by + written_at` 审计链 ✅ 已验证 |
| 21 | 敏感数据扫描标记 | data字段 | `info.data.sensitive = true, sanitized = true`（扫描由上层管线执行） |
| 22 | 红区信息状态 | data字段 | `info.data.security_level = normal|elevated|red_zone` |
| 23 | 数据生命周期自动清理 | event_type | `transition(rid, event_type=expire/purge)` → 过期→清理。retain=true的跳过 |
| 24 | 访问审计日志 | type注册 | `type=access_audit, data={actor, action, target_id, allowed, reason}` |

**结论：5个缺口全部通过data字段或新type注册覆盖。零主干改动。**

---

## 缺口25-28：资源管控与限流

| 缺口 | 内容 | 落地方式 | 具体实现 |
|------|------|---------|---------|
| 25 | 配额管理 | type注册 | `type=quota, data={limit, window, used, scope}` ✅ 已验证 |
| 26 | per-caller并发限制 | data字段 | `quota.data = {scope: session, concurrent_limit: 5}` |
| 27 | 上下文膨胀检测 | data字段 | `session.data.context_trend = [{ts, tokens_in, tokens_out}, ...]` |
| 28 | 降级而非失败 | event_type | `transition(session, event_type=model_downgrade, data={from, to, reason})` |

**结论：4个缺口全部通过type注册或data字段覆盖。零主干改动。**

---

## 缺口29-30：性能+体验

| 缺口 | 内容 | 落地方式 | 具体实现 |
|------|------|---------|---------|
| 29 | 性能鲁棒性 | 主干已有 | 游标分页 + WAL + 索引 ✅ 已验证。结构化存储+引用代替分片 |
| 30 | 体验优化 | data字段 | `session.data = {title, is_pinned, model_config, ui_prefs}` |

**结论：2个缺口全部通过主干已有或data字段覆盖。零主干改动。**

---

## 缺口31-32：运维与后台

| 缺口 | 内容 | 落地方式 | 具体实现 |
|------|------|---------|---------|
| 31 | 部署方案 | 主干已有 | `init_db()` 执行schema.sql，设600权限，daemon纳入备份 ✅ 已验证 |
| 32 | 数据迁移 | event_type | `type=snapshot, data={schema_version, migration_history}`。schema不变=不需要迁移 |

**结论：2个缺口通过主干已有覆盖。主干schema永远不变，所以迁移问题基本不存在。**

---

## 总结

| 指标 | 数值 |
|------|------|
| 总缺口 | 31 |
| 需要改主干表结构 | **0** |
| 通过 data 字段消化 | 18 |
| 通过 type 注册消化 | 5 |
| 通过 event_type 消化 | 5 |
| 通过状态变体消化 | 1 |
| 主干已有覆盖 | 5 |
| v1不加（缺口10） | 1（v1=3级够用） |
| v2字段预留 | 2（缺口13链接分享、缺口10第4级权限） |

**薄主干验证通过。31个缺口零主干改动全部消化。**

不一致消失：
- I-1（health 3档vs4档）→ data.health由Registry定义，3档
- I-2（状态数9≠10）→ 每种type的states独立定义，不存在全局统一
- I-3（daemon巡检频率）→ 运维配置，不在主干
- I-4（on-demand矛盾）→ states_on_demand变体，不是另一套表
- I-5（两处7天）→ 各自event.data声明

重复消失：
- D-1（状态机定义2次）→ Registry唯一定义源
- D-2（异常处理重叠）→ event_type=interrupt，Registry唯一定义

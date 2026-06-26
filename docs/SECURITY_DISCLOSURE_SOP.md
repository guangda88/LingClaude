# 提案 (b)：SDT 安全披露 SOP — DRAFT

| 字段 | 值 |
|------|------|
| 提案人 | 灵克 (lingclaude) |
| 提案时间 | 2026-06-26 |
| 状态 | **DRAFT — 待全体讨论** |
| 主持人 | 灵研 (lingresearch) |
| 关联提案 | (a) EXT_INQUIRY_FAQ / (c) EXTERNAL_INQUIRY_POLICY |
| 建议落地 | `/home/ai/lingclaude/docs/SECURITY_DISCLOSURE_SOP.md` |
| 触发事件 | LingBus 历史消息 `SDT-lc-014 安全泄露 — 请尽快修复 git 历史` |

---

## 一、目的

建立标准化的 **Security Disclosure Trust (SDT)** 流程，覆盖：
1. 接收 → 2. 分类 → 3. 处置 → 4. 修复 → 5. 致谢 → 6. 复盘

**核心原则**：
- ✅ 72h 内确认、评估、分类
- ✅ 修复时效与 CVSS 等级挂钩
- ✅ 默认致谢 + 公开披露
- ❌ 不私下绕过流程、不让披露者"石沉大海"

---

## 二、SDT 流程总览（七步）

```
[T+0h]   接收 → [T+72h] 确认 → [T+7d] 评估 → [T+30/90d] 修复 → [T+X] 致谢 → 复盘
   │          │              │              │                │
   ▼          ▼              ▼              ▼                ▼
公开邮箱    自动回执      严重性定级     修复+测试       公开致谢
归档去敏    编号          责任 Agent      回归测试       引用条目
```

---

## 三、详细步骤

### Step 1. 接收（Disclosure Intake）

**T+0h**

- **入口**：LingBus 公开频道 + 安全披露专用邮箱（建议：`security@lingfamily.org` 或暂用 `liuqingabc@163.com` 转发）
- **接收责任**：灵克 (lingclaude) 主备；备选灵研 (lingresearch)
- **接收动作**：
  1. 自动回执（`T+0h` 内）："已收到，编号 SDT-YYYY-NNN，预计 T+72h 给出确认评估"
  2. 创建内部 ticket：`lingclaude/sdt/SDT-YYYY-NNN.md`（最小化：编号 + 接收时间 + 一句话描述）
  3. **不要**在 ticket 中保存未脱敏的原始 payload（避免再泄露——SDT-lc-014 教训）

### Step 2. 确认（Acknowledge）

**T+72h 内**

- 回复披露者：确认收到 + 初步严重性（High/Medium/Low）+ 下一步计划
- 内部通知：LingBus `security-disclosure` 频道 + 灵族生态群 + 灵研 + 灵法

### Step 3. 评估（Triage）

| 维度 | 评估点 |
|------|------|
| **严重性** | CVSS v3.1 评分（如可达 RCE / 数据外泄 → Critical） |
| **影响范围** | 哪些 Agent / 哪些服务 / 哪些用户受影响 |
| **复现性** | 是否提供 PoC？是否需要环境细节？ |
| **责任 Agent** | 灵克（代码 / proxy）、灵研（数据 / 论文）、灵法（合规）、灵流（运维） |
| **披露者偏好** | 公开致谢 / 匿名 / 立即公开 / 修复后公开 |

输出：`SDT-YYYY-NNN_TRIAGE.md`（内部）

### Step 4. 修复（Fix）

| 严重性 | 目标修复时效 | 验证要求 |
|--------|------------|---------|
| **Critical**（RCE / 大规模数据泄露） | T+30d | 全量回归 + 灵研 + 灵流 双签 |
| **High**（限定的数据访问 / 鉴权绕过） | T+60d | 关键路径测试 + 灵克审核 |
| **Medium**（信息泄露 / DoS） | T+90d | 单点测试 |
| **Low**（最佳实践 / 文档） | 下次迭代 | 单元测试 |

**修复动作**：
1. 在隔离分支开发修复
2. 单元 + 集成测试
3. 安全回归（参考 GLM-5.2 报告的"成本过滤"教训——限流/路由类修复必须有 fail-safe 测试）
4. 灰度上线（如涉及 proxy21 / lingbus）
5. 修复 commit 引用 SDT 编号（如 `fix: proxy21 cost filter (SDT-2026-005)`）

### Step 5. 致谢（Credit）

**T+修复完成 + 7d 内**

- **默认**：在 `lingclaude/SECURITY.md`（如不存在则创建）的 `## Hall of Fame` 段添加披露者
- **匿名选项**：若披露者要求，仅写"一位匿名研究者"
- **公开披露**：撰写 `SDT-YYYY-NNN_PUBLIC.md` 摘要（不含具体利用细节），发布到 gh-pages
- **联动**：通知披露者致谢已发布 + 询问反馈

### Step 6. 公开披露（Public Disclosure）

| 严重性 | 披露时机 | 内容深度 |
|--------|---------|---------|
| Critical | 修复后立即 | 影响 + 修复 + 缓解措施（不含 exploit） |
| High | 修复后 7d | 影响 + 修复 + 时间线 |
| Medium | 修复后 30d | 简短公告 |
| Low | 季度汇总 | 简短条目 |

### Step 7. 复盘（Post-Mortem）

**T+修复完成 + 14d 内**

- 内部记录：`SDT-YYYY-NNN_POSTMORTEM.md`
- 内容：根因、修复过程、协作亮点、流程改进项
- 输出：流程改进提案 → 提交到灵研主持的治理讨论

---

## 四、披露者保护条款

- **善意披露免责**：灵族承诺不对遵循本流程的善意披露者采取法律行动
- **最小化原则**：内部 ticket 不保存未脱敏的 PoC；只在修复所需的隔离环境保留
- **双向保密**：在公开披露前不向第三方透露披露者身份（除法律强制要求）
- **拒绝私下交易**：不接受"先修复再公开"的私下安排——所有修复必须按本流程公开

---

## 五、组织角色

| 角色 | Agent | 职责 |
|------|------|------|
| SDT 接收负责人 | 灵克 (lingclaude) | 主接收 + 流程主导 |
| 严重性评估 | 灵克 + 灵研 (lingresearch) | CVSS 评分 + 影响面评估 |
| 法务审查 | 灵法 (linglaw) | 高风险披露必介入 |
| 运维配合 | 灵流 (lingflow) | 灰度 / 回滚 / 监控 |
| 致谢发布 | 灵扬 (lingyang) | SECURITY.md / gh-pages 维护 |
| 流程监督 | 灵研 (lingresearch) | 季度复盘 + 流程迭代 |

---

## 六、待讨论 / 待决策项

> 由灵研主持收敛

- [ ] **披露邮箱落地**：是申请 `security@lingfamily.org` 专属域名？还是先用 `liuqingabc@163.com` 转发过渡？
- [ ] **是否对 CVSS Critical / High 启用悬赏机制**（类似 GitHub Security Lab）？
- [ ] **致谢粒度**：是否只致谢披露者本人？还是可致谢披露者所在机构？
- [ ] **公开披露的渠道优先级**：gh-pages vs. AAAI-27 附录 vs. 单独 security advisory？
- [ ] **流程监督的 SLA**：灵研季度复盘是否需要形成固定报告？归档到 lingresearch/docs/audits/？
- [ ] **本 SOP 与现有 `LINGFAMILY_BOUNDARY_PROTOCOL.md` 的关系**：是新增独立文件，还是合并修订？
- [ ] **SDT-lc-014 是否回溯走本 SOP**（致谢 + 公开披露补做）？
- [ ] **披露语言**：中英双语 vs. 仅英文？（涉及国际研究者）

---

## 七、相关文件

- 历史教训：LingBus `SDT-lc-014 安全泄露 — 请尽快修复 git 历史`（@ 2026-06-25T22:23:18 by lingke via lingclaude）
- 关联提案：`(a) EXT_INQUIRY_FAQ_PROPOSAL.md` / `(c) EXTERNAL_INQUIRY_POLICY_PROPOSAL.md`
- 边界协议参考：`lingresearch/docs/LINGFAMILY_BOUNDARY_PROTOCOL.md`

---

## 八、强制红线（任何 Agent 不得破）

> 4 条硬红线 —— 与提案 (a) FAQ 第 4 节一致

1. **不暴露原始 session 日文**（含其他 Agent 内部对话、token、prompt 原文）
2. **不暴露 proxy21 完整配置**（含 provider key、限流阈值、路由表）—— GLM-5.2 生产事故报告已识别为弱点
3. **不暴露 Agent 身份凭证 / CRUSH.md 原文**（参考 SDT-lc-014 教训）
4. **不在生产环境做 live demo**——所有外部演示走隔离沙箱

---

## 九、修订历史

| 版本 | 日期 | 修订人 | 说明 |
|------|------|--------|------|
| v0.1.0-draft | 2026-06-26 | 灵克 (lingclaude) | 初稿，待全体讨论 |
| v0.1.0-rollback | 2026-06-27 | 灵极优 (lingclaude) | Session 92 治理回退：删除原第九节"组织角色"（与第五节"流程"重复），合并至第五节统一管理 |

---

💘 Generated with Crush  
Assisted-by: Crush:glm-5.2

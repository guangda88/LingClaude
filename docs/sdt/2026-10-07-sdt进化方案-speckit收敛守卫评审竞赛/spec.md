# SPEC: SDT进化方案-SpeckKit收敛守卫评审竞赛

> 本任务包是进化方案自身的三工件（首例实践, 自应用）。
> 来源: docs/EXTERNAL_PROJECTS_EVAL_20261007_SpeckKit_Caddy_GODMOD3.md 评估 → 学习借鉴 → 落地。

## 意图（一句话）

把 Spec Kit（意图工件链+收敛反查）、Caddy（不自研已圆轮子）、GODMOD3（多模型竞赛评审）三个项目的有效机制吸收进 lc 的守卫/SDT/脚本体系, 并以零依赖形式对外输出方法论。

## 背景与动机

2026-10-07 评估确认: SDT-lc-006 返审是变化驱动, 缺目标驱动（spec 反查）半边; lc 多模型基础设施（proxy3）已备但缺竞赛评审调度; 缺跨项目复用的「不自研已圆轮子」清单。

## 验收标准（可验证）

- [x] AC1: `python scripts/sdt_init.py "测试"` 能生成三工件任务包且 --list/--status 可用（2026-10-07 实测生成+status 7/8）
- [x] AC2: `python -m lingclaude.gov.guard.spec_converge_gate` 对未收敛包 exit=1 并报未勾选数, 对全勾选包 exit=0（三分支实测 1/0/1）
- [x] AC3: `scripts/review_contest.py --check`（不调真实模型）退出码 0, 端点/参数 env 可覆盖（实测 PASS exit=0）
- [x] AC4: `docs/INFRA_WHEELS.md` 存在且含决策清单表（ls 实测 2156B, 含决策/自查/准入三节）
- [x] AC5: `skills/lc-gov-playbook/` 存在且含 SKILL.md + guards/three-artifacts 两个方法论文档（ls 实测 3 文件）
- [x] AC6: `python -m py_compile` 全部新脚本/守卫通过（实测 all compile OK）
- [x] AC7: 全部交付物 core/ 零 diff（git status --porcelain -- lingclaude/core/ 实测 0 行）

## 边界（明确不做）

- 不做: 不引入 spec-kit CLI / caddy 二进制 / GODMOD3 任何代码（只吸收思想）
- 不做: 不代码化认知型守卫（H13 sure? / H14 双因追问保持文档形态）
- 不做: 不改 core/governance.py（守卫裁决仅结构对齐, 不动框架单轨）
- 不做: Parseltongue 扰动引擎不引入（红队工具, 生产红线）

## 关联

- 评估/来源: docs/EXTERNAL_PROJECTS_EVAL_20261007_SpeckKit_Caddy_GODMOD3.md
- 关联任务: SDT-lc-006 返审触发器（本守卫补其目标驱动半边）; H17 闭环申报（本守卫为其 SDT 形态）
- 创建日期: 2026-10-07

---
name: lc-gov-playbook
description: lc（灵克）治理方法论包——守卫/台账/SDT三工件/多模型评审的机制与纪律，零依赖纯markdown，任何agent框架可直接吸收
---

# lc-gov-playbook：lc 治理方法论包

> 输出给其他 agent / 灵字辈成员 / 外部框架的治理方法论。
> 形态纪律：纯 markdown 零依赖（对齐 Caddy 哲学——把可直接复用的东西做成零维护形态）。
> 来源：docs/EXTERNAL_PROJECTS_EVAL_20261007_SpeckKit_Caddy_GODMOD3.md + lc 自家实践。

## 包内容

| 文件 | 方法论 | 源头 |
|---|---|---|
| [guards.md](guards.md) | 守卫双性分类法（机制型代码化 / 认知型纪律化）+ 17 守卫速查 | lc H1-H17 实践 |
| [three-artifacts.md](three-artifacts.md) | 三工件工作流（spec→plan→tasks + converge 收敛反查） | github/spec-kit 思想 |
| SKILL.md（本文件） | 使用指南与铁律红线 | lc 铁律体系 |

## 快速使用

1. **新任务启动**：读 three-artifacts.md，按模板产出 spec/plan/tasks 三工件再动手。
2. **写基础设施前**：查 INFRA_WHEELS 式轮子清单（见 three-artifacts.md 附录），不自研已圆轮子。
3. **守卫设计时**：按 guards.md 双性分类法决定「代码化 or 纪律化」，不做假守卫。

## 铁律红线（吸收时不可违背）

1. **机制/认知分界**：代码能判定的（计数、比对、清单核验）才代码化；模型自律类（自问 sure?、双因追问）只能纪律化，伪装成代码守卫 = 假守卫。
2. **fail-open + 显式留痕**：守卫失效宁可放行、绝不静默——静默失效的守卫比没有守卫危险。
3. **薄主干**：方法论包零依赖零 CLI；借鉴思想不搬运代码（尤其 AGPL 项目，只读思想）。

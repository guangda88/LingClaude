# TASKS: SDT进化方案-SpeckKit收敛守卫评审竞赛

## 执行清单

- [x] T1 (D-): 探查现有范式（free_ram_gate 守卫接口 / gov 布局 / proxy3 端点 / sdt 目录）
- [x] T2 (D1): Spec Kit 篇 — docs/sdt/templates/ 三工件模板落地
- [x] T3 (D2): Spec Kit 篇 — scripts/sdt_init.py 脚手架 + REJECT/PASS 分支验证（exit 1/0）
- [x] T4 (D2): Spec Kit 篇 — gov/guard/spec_converge_gate.py 收敛反查守卫（含未勾选拒收分支验证）
- [x] T5 (D1): Caddy 篇 — docs/INFRA_WHEELS.md 不自研已圆轮子清单
- [x] T6 (D1): GODMOD3 篇 — scripts/review_contest.py 评审竞赛插片（proxy3 多模型+裁判, --check 干跑 PASS exit=0）
- [x] T7 (D1): 封装输出 — skills/lc-gov-playbook/（SKILL.md + guards + three-artifacts）
- [x] T8 (D7): 全量验证 py_compile + 收尾汇报（AC1-AC7 全勾, 见 spec.md 证据）

## 收敛判定

- 全部勾选 + spec.md AC1-AC7 逐条核对通过 → 可申报「已完成」
- 有未勾选项 → spec_converge_gate 拒收完成申报

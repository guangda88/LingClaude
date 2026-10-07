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

## 经验条目（真实翻车复盘，比规则更有说服力）

### E1. 沙箱 netns 假阴性（2026-10-07，incident: sandbox_netns_false_negative_20261007）

**现象**：在 bwrap 沙箱会话里探测宿主机端口（proxy3/8765、灵忆/9530、mihomo/7890），全部 connection
refused → 断言「三个核心服务全灭」。次日发现 mihomo 进程已存活三周且 journald 显示其 10 秒前还在转发流量。

**根因**：沙箱 bash 处在独立网络命名空间（`/proc/self/cgroup` 可查归属），`/proc/net/tcp` 是沙箱自己的空回环——
「服务未监听」是**负向判定**，负向结论的效力永远 ≤ 探测视角的可信度。

**吸取的纪律（任何 agent 通用）**：

1. 探测前先自证视角：`cat /proc/self/cgroup` 查 netns 归属；`/proc/net/tcp` 全空 = 沙箱信号本身。
2. 负向判定必须带视角标注：「沙箱内测得 refused」≠「宿主机服务死亡」，两句话不能混写。
3. 负向结论入账前至少找一个**宿主视角**证据源交叉验证（journald / 宿主进程表 / 应用层探活）。
4. 矛盾信号是礼物：一旦有证据与自己的负向判定冲突，立即重验而不是辩解——本例正是 journald
   的实时转发记录推翻了误判。
5. 与「TCP 可达 ≠ 应用健康」（health_inspect）互补成对：**视角对 ≠ 应用健康，应用健康 ≠ 视角对**，
   两个维度都要验。

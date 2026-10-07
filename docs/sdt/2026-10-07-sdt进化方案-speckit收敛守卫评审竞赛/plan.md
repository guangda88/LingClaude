# PLAN: SDT进化方案-SpeckKit收敛守卫评审竞赛

## 技术方案

三条借鉴线各自落为薄插片，共用 free_ram_gate 确立的守卫范式（check 入口 / fail-open+留痕 / env 可配 / 裁决结构对齐 GovernanceGate）：

1. **Spec Kit → 意图层**：`docs/sdt/templates/` 三工件模板 + `scripts/sdt_init.py` 脚手架（88 行零依赖）+ `lingclaude/gov/guard/spec_converge_gate.py` 收敛反查守卫（gov/guard/ 插片，铁律 7）。
2. **Caddy → 决策纪律层**：`docs/INFRA_WHEELS.md` 轮子清单（判断表 + 自查记录 + 准入三问），不引 caddy 二进制。
3. **GODMOD3 → 评审层**：`scripts/review_contest.py` 评审竞赛插片（urllib+ThreadPool 标准库，双盲去标识裁判，规模上限 env），零 GODMOD3 代码复制（AGPL）。
4. **封装输出**：`skills/lc-gov-playbook/` 三文档纯 markdown 包。

## 影响面

| 层 | 文件/目录 | 变更类型 | 铁律校验 |
|---|---|---|---|
| core/ | 无 | 零 diff | 铁律1 ✅ |
| gov/guard/ | spec_converge_gate.py | 新增（插片） | 铁律7 ✅ |
| scripts/ | sdt_init.py, review_contest.py | 新增 | 零第三方依赖 ✅ |
| docs/ | sdt/templates/×3, INFRA_WHEELS.md | 新增 | — |
| skills/ | lc-gov-playbook/×3 | 新增 | — |

## 依赖与顺序

- proxy3(8765) 仅 review_contest 真实模式需要；--check 干跑零外部依赖。
- spec_converge_gate 依赖 docs/sdt/templates 存在（sdt_init 前置）。

## 回滚方案

全部为纯新增文件：`git clean` 新增路径即可回滚，无状态修改、无 schema 变更、无数据迁移。

## 风险与盲区

- 自知盲区 security：本次不涉鉴权/加密代码，review_contest 仅为内网 proxy3 调用，key 走 env 不落盘。
- 中文路径 slug 在 shell 引号内使用（sdt_init 输出已带引号提示）。
- 认知型守卫（H13/H14）明确不代码化——防假守卫边界写入 skill 文档。

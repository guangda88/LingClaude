# 不自研已圆轮子清单（INFRA_WHEELS）

> 哲学源自 Caddy（2026-10-07 评估, docs/EXTERNAL_PROJECTS_EVAL_20261007_SpeckKit_Caddy_GODMOD3.md）:
> 机器能做的交给机器、别人圆好的轮子绝不重造。lc 的精力花在业务中间件, 不花在基础设施复刻。
> 用法: 动手写基础设施代码前先查此表; 命中即用现成方案, 并在此登记决策。

## 决策清单

| 轮子 | 判断 | 推荐用法 | 禁止 |
|---|---|---|---|
| TLS 证书 / ACME 续期 | Caddy 已圆（自动 HTTPS, 零维护） | 灵字辈对外服务统一走 Caddy 反代, 域名/路径分发 | Python 后端手写 ACME/证书逻辑 |
| 对外服务鉴权门 | Caddy `forward_auth` 已圆 | 鉴权统一放 Caddy 层对接自建鉴权, 内部服务只听内网 | 各服务各写一套鉴权 |
| spec→plan→tasks 工件链 | github/spec-kit 已圆（思想层, MIT） | `scripts/sdt_init.py` + `docs/sdt/templates/` | 引入 spec-kit CLI（薄主干） |
| 守卫/台账对外封装 | 自家 `lc_mcp_guard` MCP 先例已通 | 扩展 `lingclaude/plugins/agents/lc_mcp_guard/` | 为每个消费者重写守卫逻辑 |
| 多模型竞赛调度 | GODMOD3 已证范式（思想层, AGPL 只读思想） | `scripts/review_contest.py`（proxy3 复用） | 抄 GODMOD3 代码 / 规模超 5 模型 |

## 本项目自查记录

| 日期 | 场景 | 决策 | 依据 |
|---|---|---|---|
| 2026-10-07 | 评审竞赛调度 | 自研薄插片 scripts/review_contest.py（复用 proxy3, 不抄 GODMOD3） | AGPL 传染性 + 12-60 模型规模过度设计, 3-5 模型足够 |
| 2026-10-07 | SDT 工件链 | 借 spec-kit 思想自研 88 行零依赖脚本 | 引 CLI 违反薄主干; 价值在流程不在工具 |
| 2026-10-07 | TLS/鉴权 | （待触发）灵字辈服务对外开放时统一走 Caddy | 官网文档确认自动 HTTPS + 热更新 |

## 新轮子准入

写基础设施代码前回答三问，任一「是」即停:
1. 这个问题业界是否已有成熟方案（且 license 可用）?
2. lc 现有体系（proxy3 / Caddy / 灵犀 MCP / scripts）是否已覆盖?
3. 自研部分是否只是薄粘合层（<200 行, 零业务判断）?

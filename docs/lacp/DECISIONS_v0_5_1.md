# 族长 4 项决策 v0.5.1 (7/29 18:42)

## D-族长-1: proxy3 恢复生产 (8/8 #4 验收) — **A 三方联署通过**

- 联署人: **灵克 + 灵安 + 族长**
- 时效: 8/8 #4 会议
- 7 项前置 (a-g) 全部关闭才通过

## D-族长-2: proxy3 维护 owner — **C 灵克+灵通 联管**

- 联管制: 重大决策 灵克 + 灵通 双 owner 签
- 日常运维 灵通 sole (proxy3 owner)
- 重大变更 (路由表, 5-2 401 等) 灵克 + 灵通 双签

## D-族长-3: audit Bearer 风险 — **A 信任灵通配置 (灵克 own audit fallback)**

- 主路径: 灵通 owner 配 audit Bearer
- fallback: 灵克 own audit 工具 (scripts/proxy3_route_audit.py)
- 不开放 admin 临时白名单 (避免 attack surface)

## D-族长-4: 灵商跨 project — **A 灵通改 2 行**

- 责任: 灵通 owner 改 /home/ai/yitang/lingshang/server/lingshang_server.py
- 改动: 2 行代码 (加 X-Agent-Id header)
- 不依赖 yitang owner 响应

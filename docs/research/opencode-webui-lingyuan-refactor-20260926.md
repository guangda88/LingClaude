# 灵元思维重构 lc WebUI × opencode 逆向借鉴研究

**日期**: 2026-09-26
**作者**: 灵克
**性质**: 调研 + 架构方案（P0 设计稿，非实施）
**关联**: `docs/SYSTEMS_THEORY_SYNTHESIS.md`（灵元体系源文档）、`docs/LINGCLAUDE_REFACTOR.md`（灵元插片对照表）、`.audit/webui_audit_e2e_20260926.md`（lc webUI 审计基线）
**证据等级说明**: opencode 侧所有结构断言均引自 opencode.ai 官方文档（2026-09-26 实时抓取）；GitHub 源码目录细节因 API 限流**未逐文件验证**，标注 🔶；lc 侧全部来自本地源码实读。

---

## 〇、任务界定

用户命题：「用灵元思维重构此功能，作成插片，逆向 opencode webUI，看 lc 有哪些可借鉴」。

三个子问题：
1. **什么是灵元思维** —— 以体系文档为准，不由我发挥
2. **opencode webUI 是什么形态** —— 实时文档验证，不凭训练记忆
3. **lc 怎么借鉴** —— 落到插片（模块边界 + 接缝协议）方案

---

## 一、灵元思维方法论口径（摘自体系文档）

### 1.1 灵元的架构主张

`docs/LINGCLAUDE_REFACTOR.md` 的核心论点：lingclaude 大量 Python 模块在重复实现「灵元」（lingyuán）底座已提供的能力（records / events / query+transition / 记忆分层），正确路线是 **core 薄壳化、灵元化**——把 22+ 个模块（session/task_aggregation/governance/handover/context_cache/layered_memory 等，合计约 6000 行）换成对灵元 records+events 的调用，只保留不可替代的核心（query_engine/model providers/coding engine，约 5000 行）。

**插片定义**：一个插片 = 一条以「接缝协议」为边界的自治模块，依赖只能指向公共接缝（参考 N7 收敛案例：`plugins/memory/memory_common.py` 作为五桥公共接缝），禁止点对点网状依赖。

### 1.2 控制论三书框架（`docs/SYSTEMS_THEORY_SYNTHESIS.md`）

| 书 | 回答的问题 | 在 webUI 重构中的用法 |
|---|---|---|
| 钱学森《工程控制论》 | 单回路怎么稳定收敛 | 每个插片声明自己的纠错回路（传感器→决策→执行器）、增益上限、限幅、停机条件 |
| Meadows《系统之美》 | 该动哪里（杠杆点） | 用杠杆点排序决定重构顺序：信息结构 > 规则 > 调参 |
| Kelly《失控》 | 去中心化与涌现 | webUI/TUI/IDE/CLI 都是 core 的对等 client（多客户端涌现），不建中央 UI 状态 |

### 1.3 六条已验证的设计纪律（从本次调研中提炼，全部有实证）

- **D1 时钟件 vs 群件分离**（Kelly）：安全边界（鉴权/host 白名单/命令黑名单）是钟表件，必须窄域确定；对话渲染是群件，可以多客户端自由演化
- **D2 反馈回路必带产物**（钱学森 F12c 反例）：每个「声明完成」的位置必须同位置放验证证据（H17 闭环申报）
- **D3 事件总线优先于点对点**（Kelly「世界即通信总线」）：会话/消息/权限变更经 bus 广播，client 各自渲染，core 不感知 client 数量
- **D4 协议先行**（Meadows 信息结构杠杆）：先固化 OpenAPI/接缝协议，再谈实现；协议是最便宜的高杠杆
- **D5 fail-closed 默认**（已有 lc 纪律）：一切桥接失败显式报错，禁止静默降级
- **D6 可逆切换**（Meadows 韧性）：新旧实现双跑可回滚，禁止大爆炸重写

---

## 二、opencode webUI 逆向结论（实时文档验证）

### 2.1 最重要的发现：opencode 的"webUI"根本不是前端工程

文档原话（opencode.ai/docs/server，2026-09-26 抓取）：

> When you run opencode it starts a TUI and a server. **Where the TUI is the client that talks to the server.** The server exposes an **OpenAPI 3.1 spec** endpoint. This endpoint is also used to generate an SDK.

> This architecture lets opencode support **multiple clients** and allows you to interact with opencode programmatically.

**架构真相**：
- `opencode serve`（默认 port 4096）暴露的是**无头核心 + OpenAPI 3.1 规格 HTTP 服务**
- TUI 只是这个 server 的**一个 client**（启动 TUI = 随机端口起一个 server，TUI 连接它）
- Web UI 位于其托管服务 **OpenCode Zen**（opencode.ai 导航栏 Web / Zen 项，🔶 源码在 `packages/web`，未逐文件验证）；自建 server 场景下，web 端可走官方 JS SDK（`@opencode-ai/sdk`，npm）自建
- SDK 的 type 定义**全部由服务端 OpenAPI spec 生成**——服务端是类型的唯一事实源

这对 lc 的含义：**opencode 的"webUI 借鉴"首先不是抄界面，而是抄"server-first + 多 client"的拓扑**。

### 2.2 已验证的 opencode server 能力清单

来源：opencode.ai/docs/server 的 API 表（2026-09-26 抓取）。

**会话域（差距最大）**：
| 端点 | 说明 |
|---|---|
| `GET /session` | 列出全部会话，返回 `Session[]` |
| `POST /session` | 新建会话 `{parentID?, title?}`——**parentID 支持分叉/子会话** |
| `GET /session/:id/message` | 分页拉消息 `{info: Message, parts: Part[]}[]`——**消息与部件分离建模** |
| `POST /session/:id/message` | 发消息（流式） |
| `GET /session/:id/parts` | 会话的 parts 流 |

**事件域**：
| 端点 | 说明 |
|---|---|
| `GET /event` | **SSE 总线**：首个事件 `server.connected`，之后是全部 bus 事件 |
| `GET /global/event` | 全局事件流 |
| `POST /tui/append-prompt` / `/tui/open-help` 等 | **TUI 也走 server API 被远程驱动**（IDE 插件就是这样接入的） |

**项目/环境域**：`GET /project`（项目列表）、`GET /project/current`、`GET /path`、`GET /vcs`（VCS 信息）、`GET /find?pattern=`（服务端文件模糊搜索）

**外围能力域**：`GET /command`（自定义命令表）、`GET /agent`（可用 agent 列表）、`GET /lsp`、`GET /formatter`、`GET /mcp`、`GET /config` + `PATCH /config`（**运行时改配置**）、`GET /provider` + `PUT /auth/:id`（provider 授权管理）、`POST /log`

**运维面**：`GET /global/health` 返回 `{healthy, version}`；`GET /doc` 返回 OpenAPI 3.1 规格页。

**鉴权面**：`OPENCODE_SERVER_PASSWORD` + `OPENCODE_SERVER_USERNAME` 环境变量启用 HTTP Basic（默认 127.0.0.1 监听 + 可选 `--cors` 白名单 + `--mdns` 局域网发现）。**注意：lc 的 Bearer token 方案在此之上，API key 比 Basic 更适合作 machine-to-machine；但 opencode 的 env-var 零配置起步更友好**。

### 2.3 opencode 前端工程形态（🔶 部分验证）

- GitHub repo 存在 `packages/web/` 与 `packages/console/` 目录（GitHub 页面抓取确认，各出现 2/6 次），前端技术栈未验证
- Zen 是托管多租户形态（账号体系、项目列表等），lc 是单用户本地工具，**不抄 Zen 的产品层，只抄它的 client-server 拓扑**

---

## 三、lc webui-server 现状基线（本地实读）

修复 P1 后（commit `04194d0`）的架构：

| 文件 | 行数 | 职责 |
|---|---|---|
| `main.rs` | 403 | AppState、路由表、TcpListener、超时/keepalive 配置 |
| `auth.rs` | 360 | Bearer 鉴权 + **Host 白名单防 DNS rebinding**（loopback 恒放行 + `LINGCLAUDE_WEBUI_ALLOWED_HOSTS`） |
| `chat_api.rs` | 258 | `/chat` SSE，**透传**引擎 `/ask/stream` |
| `live_api.rs` | 181 | `/live` snapshot，对齐引擎 `_list_projects()` 对象数组（P1 已修） |
| `audit.rs` | 71 | 审计日志（**已知 P3：open 失败静默落 /dev/null**，`audit.rs:23-26`） |
| `webui.rs` | 105 | rust-embed 静态资产 + `/mint` handoff |

**关键架构事实**：
- webui-server 是**纯透传桥**：无会话存储（`chat_api.rs:24-28` 注释明示 session_id "接受但不消费"）、无消息持久化、无多轮上下文
- 引擎侧 `lingclaude/api.py`（8700）只有 `/ask` + `/ask/stream`（question+context 二字段），**没有会话注册表**——这是差距的根源，不是 webui-server 能独立补的
- 前端是 rust-embed 内嵌 SPA（单仓库分发 ✅）
- 安全面已对齐 opencode 默认姿态：loopback 默认 + 显式白名单 + Bearer（lc 比 opencode 多一层 token 鉴权）

---

## 四、差距矩阵（opencode × lc）

按 Meadows 杠杆点排序（信息结构 > 规则 > 结构 > 参数）：

| # | 差距 | opencode | lc 现状 | 杠杆级 | 借鉴动作 |
|---|---|---|---|---|---|
| G1 | **会话是一等资源** | `Session` 有 CRUD、parentID 树、title、磁盘持久化；`Message`/`Part` 分离 | 无（前端自造 requestId，死后即焚） | 结构 | 引擎 api.py 加 `/session*` 域（见 §五 R2） |
| G2 | **事件总线 `/event`** | 单一 SSE 总线，首事件 `server.connected`，全 bus 事件外放 | webui 每请求自建 SSE（chat 一条流） | 信息结构 | 引擎挂 bus，webui 订阅转发 |
| G3 | **OpenAPI 规格即 SDK** | spec → 自动生成 `@opencode-ai/sdk`，TS 类型唯一事实源 | 无规格文档；前后端契约靠口头+回归测试钉 | 信息结构（最廉价） | axum-utoipa 生成 OpenAPI 3.1 + 前端类型生成 |
| G4 | **多 client 对等** | TUI/IDE/web/SDK 全是同一 server 的 client | CLI(REPL) 与 webUI 是**两条独立链路**（webUI→api.py→query_engine；REPL→query_engine 直连） | 结构 | 统一为「core serve + 多 client」拓扑 |
| G5 | **服务端文件搜索** `GET /find` | 服务端索引+模糊搜索 | 无（前端无法搜文件） | 参数级 | 低优先，可用时再补 |
| G6 | **TUI 可被远程驱动** `/tui/*` | IDE 插件经 server 注入 prompt | lc 无对应（TUI=终端独占） | 结构 | 远期：等 G4 统一后自然获得 |
| G7 | **运行时配置** `PATCH /config` | 可热改 config | 无 | 参数级 | 低优先 |
| G8 | 鉴权零配置起步 | env-var Basic | env-var Bearer（已对齐 ✅） | — | 保持 |
| G9 | mDNS 局域网发现 | `--mdns` | 无 | 参数级 | 可选 |
| G10 | 消息 Part 化模型 | text/tool-call/reasoning/attachment 都是 Part | 引擎流式输出是原始 token 行 | 结构 | 随 G1 一起建模 |

**判定**：G1+G2+G3 是同一个杠杆（信息结构）的三刀，一次切中；G4 是范式级（Kelly 多 client），收益最大但动 core；G5-G9 都是参数级，明确**降级为最后手段**（Meadows 纪律）。

---

## 五、灵元思维插片重构方案

### 5.1 插片拓扑（目标态）

```
┌─────────────────────────────────────────────────────┐
│  client 插片群（群件/swarmware，可对等涌现多个）        │
│  ┌────────┐  ┌────────┐  ┌────────┐  ┌──────────┐  │
│  │ REPL   │  │ webUI  │  │ IDE    │  │ SDK/API  │  │
│  │ (现有) │  │ (现有) │  │ (未来) │  │ 脚本     │  │
│  └───┬────┘  └───┬────┘  └───┬────┘  └────┬─────┘  │
│      └───────────┴─────┬─────┴────────────┘          │
│                   seam: OpenAPI 3.1 (G3)              │
└──────────────────────────────────────────────────────┘
                      │ HTTP + SSE (事件订阅)
┌─────────────────────────────────────────────────────┐
│  core serve 插片（钟表件/clockware，窄域确定）          │
│  ┌────────────┐ ┌────────────┐ ┌─────────────────┐  │
│  │ session域   │ │ event bus  │ │ tool/permission │  │
│  │ /session*  │ │ /event SSE │ │ 域(已有 H8/权限) │  │
│  │ (R2 新建)  │ │ (R3 挂bus) │ │                 │  │
│  └────────────┘ └────────────┘ └─────────────────┘  │
│  ┌──────────────────────────────────────────────┐   │
│  │ 灵元接缝（records/events/query+transition）    │   │
│  │ 会话=records(type=session)；消息=events链；     │   │
│  │ 权限决策=transition 校验（对齐 task_aggregation→│   │
│  │ 灵元 query+transition 的先例）                 │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
                      │
        ┌─────────────┴──────────────┐
        │  query_engine（不可替代核心，│
        │  保留薄壳）                 │
        └────────────────────────────┘
```

**依赖规则**（D1/D3 落地）：
1. client 插片只允许依赖 OpenAPI seam，禁止 client-to-client 直接依赖
2. core serve 插片只允许依赖灵元接缝 + query_engine，禁止依赖任何具体 client
3. 安全件（auth/host 白名单/危险命令门）留在钟表件层，不进事件总线（H8 纪律）

### 5.2 分期路线（D6 可逆切换，每期都可独立交付回滚）

**R1（本周可做，纯 webui-server，无引擎改动）**
- 用 `utoipa` 从 axum handler 注解生成 OpenAPI 3.1，挂 `/doc`（对齐 opencode 的 `/doc`）
- 收益：前后端契约从此有产物；D2 落实（声明完成的位置=spec 文件本身）
- 风险：零（只加不改）

**R2（本期核心，引擎 api.py + 灵元接缝）**
- 引擎加会话域：`GET/POST /session`、`GET /session/:id/message`、`POST /session/:id/message`，**存储落灵元 records(type=session) + events 链**（不重造 sqlite——这正是 LINGCLAUDE_REFACTOR.md 反对的"重复实现灵元"）
- webui-server 把 `chat_api.rs` 的"接受但不消费"的 session_id 接上真语义
- 双跑：新旧 `/ask` 并存一个版本周期，前端切新路由，回归不过即切回
- 这一步顺带解决 `/chat/stop` 空转（有会话注册表后才能真正取消）

**R3（事件总线）**
- 引擎挂全局 bus（lingclaude 已有 bus 概念可复用），暴露 `GET /event` SSE，首事件 `server.connected`（对齐 opencode）
- webui live 页从轮询 snapshot 改为订阅事件流；`/live` snapshot 保留给健康检查（存量不破坏）

**R4（多 client 对齐，范式级）**
- 评估 REPL 是否改连 core serve（收益：TUI/webUI/IDE 行为完全一致；成本：REPL 直连 query_engine 的低延迟路径要绕 HTTP）
- 决策标准：如果 R2/R3 落地后 REPL 与 webUI 出现行为漂移（必然发生），则做；否则缓

**明确不做**（本次调研的否定结论）：
- 不抄 Zen 托管形态（账号/多租户/云项目）——lc 是单用户本地工具
- 不抄 opencode 前端 UI 细节——lc 前端已可用，且 UI 是群件层可以自由演化
- G5/G7/G9（文件搜索/热配置/mDNS）参数级，进遗留队列最后处理

### 5.3 每条回路的控制论自检（钱学森七问）

| 回路 | 传感器 | 决策 | 执行器 | 限幅/停机条件 |
|---|---|---|---|---|
| 会话持久化 (R2) | 灵元 records 写入返回 | serde 校验 | 落 records | 写失败 fail-closed 报错（禁静默，对齐 chat_api 现有纪律） |
| 事件总线 (R3) | 订阅者心跳 | 断线检测 | 重连退避 | 3 次重连失败降级轮询（H15 三连败模式） |
| API key 鉴权（已有） | 请求头 | 常时比较 | 401 | 失败不区分"无 key/错 key"（现有，保持） |
| audit 日志（已知 P3） | open 结果 | —— | /dev/null 降级 | **当前无传感器=开环（F12c 病理）** → R1 顺手加错误计数器 |

---

## 六、遗留与交接

1. **本文件为设计稿，未动任何代码**。R1 可即刻开工（预计 1 提交）。
2. R2 需要动 `lingclaude/api.py` 与灵元接缝，**必须先与灵元/灵忆侧确认 records(type=session) 的 schema 归属**（对齐 memory_common.py 公共接缝的收敛模式，防五桥式点对点）。
3. audit 静默降级（P3）建议并入 R1 顺手修（加计数器+`/live` 暴露），不开新任务。
4. opencode 前端技术栈与 `packages/web` 内部结构本次未逐文件验证（API 限流），如后续要抄 UI 细节需补一轮源码克隆调研——但按 §5.2 结论，UI 细节不在借鉴范围内。
5. 参考来源：
   - https://opencode.ai/docs/server/ （server API 全表，已验证）
   - https://opencode.ai/docs/sdk/ （SDK/类型生成链，已验证）
   - https://github.com/sst/opencode （packages/web、packages/console 存在性，🔶）
   - 本地：`webui-server/src/*.rs`、`lingclaude/api.py`（8700 契约）

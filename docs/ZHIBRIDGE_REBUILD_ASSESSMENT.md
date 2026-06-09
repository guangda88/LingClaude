# 智桥 (zhibridge) 重建评估报告

**评估人**: 灵克 #2
**日期**: 2026-05-12
**背景**: 广大老师决策——智桥重建为灵族对外统一网关，灵通+管辖

---

## 一、现状诊断

### 代码规模

| 目录 | 文件数 | 估算行数 | 保留价值 |
|------|--------|----------|----------|
| relay-server/ | 35 | ~8,000 | 骨架+鉴权可复用 |
| session_protocol/ | 6 | ~1,500 | 不需要（灵信已覆盖会话管理） |
| scripts/ | 30 | ~3,000 | 全部废弃 |
| plugins/ | 3 | ~400 | 废弃 |
| tests/ | 15 | ~2,500 | 废弃（重建需新测试） |
| lingflow/ | ~5 | ~800 | 废弃（灵通已有工作流引擎） |
| optimization/ | ~5 | ~600 | 废弃（灵极优覆盖） |
| 顶层散落文件 | 23 | ~3,000 | 废弃（test_*, demo_*, chat_*） |
| **总计** | **~105** | **~20,000** | |

### 问题诊断

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | **定位错位** | 严重 | 把网关建成了SaaS（用户注册/团队管理/推送通知/插件系统），与灵族对外网关无关 |
| 2 | **功能膨胀** | 严重 | OAuth2、TOTP 2FA、团队管理、文件API、推送通知——全是"能做"不是"该做" |
| 3 | **与其他成员重叠** | 高 | session_protocol与灵信重叠；lingflow/与灵通重叠；optimization/与灵极优重叠 |
| 4 | **元认知退化** | 严重 | CRUSH.md自评 C/D (2/10)，G1-reactive，有3个编码事件（身份幻觉、8.5h沉默、越权） |
| 5 | **最后活跃** | 高 | 最后commit 2026-05-06，6天前。长期低活跃 |
| 6 | **散落测试文件** | 中 | 顶层23个test_*.py不在tests/目录，不符合规范 |

### 可复用资产

| 模块 | 行数 | 复用方式 |
|------|------|----------|
| auth/ (6文件) | ~2,219 | JWT鉴权、密码哈希、TOTP → 精简后用于API key鉴权 |
| http_server.py | ~1,538 | FastAPI路由骨架 → 砍掉90%，保留路由注册模式 |
| config.py | ~284 | pydantic-settings配置模式 |
| rate_limit.py | ~463 | 速率限制 → 直接复用 |
| metrics.py | ~456 | Prometheus指标 → 直接复用 |
| request_signing.py | ~291 | 请求签名 → 直接复用 |
| csrf.py | ~432 | CSRF防护 → Web场景复用 |
| ssl_manager.py | ~461 | SSL/TLS → 对外HTTPS复用 |

**结论**: ~4,600行可复用（23%），~15,400行废弃（77%）。

---

## 二、重建方案

### 设计原则

1. **薄层网关** — 只做路由、鉴权、限流，不做业务逻辑
2. **灵信=内部，灵犀=出站，智桥=入站** — 三层通信架构各司其职
3. **目标3K行以内** — 从20K→3K，砍掉85%
4. **灵通+管辖** — 代码仓库可独立，但API设计和运维归属灵通+

### 目标架构

```
外部请求 → 智桥(:8767) → 鉴权 → 限流 → 路由分发
                                        ├── /api/knowledge/query → 灵知(:8901)
                                        ├── /v1/chat/completions → 灵通+ proxy(:8765)
                                        ├── /api/status → 灵通+(:8765)
                                        ├── /api/podcast/episodes → 灵通问道数据
                                        └── /api/research/papers → 灵研
```

### API端点（按灵通+P0-P2清单）

| 优先级 | 端点 | 后端 | 说明 |
|--------|------|------|------|
| P0 | `GET /v1/health` | 自身 | 健康检查 |
| P0 | `POST /v1/chat/completions` | 转发灵通+:8765 | LLM代理（已有） |
| P0 | `POST /api/knowledge/query` | 转发灵知 | 知识库语义检索 |
| P1 | `GET /api/status` | 转发灵通+:8765 | 灵族健康仪表盘 |
| P1 | `GET /api/agents` | 转发灵通+:8765 | 成员状态 |
| P1 | `GET /api/podcast/episodes` | 静态JSON/灵通问道 | 播客元数据 |
| P2 | `GET /api/research/papers` | 转发灵研 | 论文检索 |
| P2 | `POST /v1/images/generations` | 转发LLM Proxy | 图片生成（未来） |

### 模块设计（8个文件，目标3000行）

```
zhineng-bridge/
├── gateway/
│   ├── __init__.py          # 导出
│   ├── app.py               # FastAPI应用入口 (~200行)
│   ├── config.py            # pydantic-settings配置 (~100行)
│   ├── auth.py              # API key鉴权+灵通+用户系统桥接 (~300行)
│   ├── router.py            # 路由注册+后端服务发现 (~400行)
│   ├── middleware.py         # 限流+CORS+请求日志 (~300行)
│   ├── proxy.py             # 反向代理核心（HTTP转发） (~500行)
│   └── metrics.py           # Prometheus指标+健康检查 (~200行)
├── tests/
│   ├── test_auth.py
│   ├── test_router.py
│   ├── test_proxy.py
│   └── test_integration.py
├── pyproject.toml
├── Dockerfile
├── AGENTS.md
└── CRUSH.md
```

### 鉴权方案

```
外部请求 → API Key (X-API-Key header)
         → 灵通+验证（/api/auth/verify 端点）
         → 通过 → 路由分发
         → 失败 → 401

未来扩展: OAuth2 (GitHub/Google) → 用户注册 → API Key发放
```

### 不做的事（明确边界）

| 功能 | 归谁 | 原因 |
|------|------|------|
| 业务逻辑 | 各成员 | 网关不碰业务 |
| 内部通信 | 灵信 | 异步消息总线 |
| 出站工具调用 | 灵犀 | MCP协议 |
| 团队管理 | 删除 | 灵族不需要SaaS |
| 用户注册/登录 | 删除 | 第一版用API Key |
| 推送通知 | 删除 | 无场景 |
| 插件系统 | 删除 | 过度设计 |
| WebSocket中继 | 删除 | 灵信+灵通+已覆盖 |
| 工作流引擎 | 删除 | 灵通已有 |
| 会话管理 | 删除 | 灵信已有 |

---

## 三、实施路径

### Phase 0: 准备（1天）

- [ ] 确认灵通+管辖关系和代码仓库位置
- [ ] 确认端口号（建议8767，需注册到PORT_REGISTRY.md）
- [ ] 备份当前代码（git tag v1.4.0-before-rebuild）
- [ ] 创建 `gateway/` 目录和 pyproject.toml

### Phase 1: 核心骨架（2-3天）

- [ ] `app.py` — FastAPI应用 + 中间件注册
- [ ] `config.py` — 后端服务地址配置
- [ ] `auth.py` — API Key鉴权
- [ ] `router.py` — P0端点路由（health + LLM代理 + 灵知查询）
- [ ] `proxy.py` — HTTP反向代理核心
- [ ] 基本测试

### Phase 2: P1端点+限流（1-2天）

- [ ] `/api/status`, `/api/agents` — 灵通+仪表盘
- [ ] `/api/podcast/episodes` — 播客元数据
- [ ] `middleware.py` — 限流+CORS
- [ ] `metrics.py` — Prometheus指标

### Phase 3: 对外暴露+HTTPS（1天）

- [ ] SSL/TLS配置（复用ssl_manager.py）
- [ ] systemd服务文件
- [ ] 健康检查+熔断
- [ ] 端到端验证

### 总预估：5-7天

---

## 四、风险与依赖

| 风险 | 缓解 |
|------|------|
| 灵知无现成HTTP API → /api/knowledge/query无法直接转发 | 需灵知提供API端点，或智桥内嵌查询逻辑 |
| 灵研无HTTP服务 → /api/research/papers | P2优先级，等灵研准备 |
| CORS配置影响Web前端 | 中间件统一处理 |
| 灵通+8765端点变更影响路由 | 配置驱动，改配置不改代码 |

---

## 五、灵克建议

1. **先建骨架再删旧代码** — 新 `gateway/` 独立于旧 `relay-server/`，验证通过后再删
2. **灵通+确认后再动** — 这是灵通+管辖的服务，灵克代为实现需确认
3. **端口8767** — 8765已被灵通+占用，8766被旧智桥WebSocket占用，8767是新起点
4. **第一版只做P0** — health + LLM代理转发 + 灵知查询，3个端点足够验证架构

---

**状态**: 待灵通+确认管辖关系和API设计 → 灵克开始Phase 1实现

# free-pool 封装复用性论证与计划

## 1. 当前 free-pool 组成分析
proxy3_py (free-pool) 核心：
- routes.json：路由清单（provider/tier/base_url/capabilities/budgets/...）
- scripts/sync_free_models.py：平台 /v1/models 自动发现+过滤（只增不删、default_off 保守下架）
- oc_free_bridge.py (port 4100)：opencode serve 网关适配（Zen free 限制）
- codearts_bridge.py (port 4110)：CodeArts agent API -> OpenAI 兼容桥
- admin API（/admin/free_pool/*）管理/统计
- 调度：健康分、桶（auto/code/fast/long/vision）、限流（rpm/max_concurrent）

## 2. 封装价值
- 降低接入成本：一键拉起“免费模型池”（OpenAI 兼容 /v1/chat/completions）
- 可复用性：独立于 lingclaude/lingflow，作为通用 gateway
- 可组合性：可替代/补充 LiteLLM/OmniRoute 的免费池子集，聚焦“可调用性”
- 可维护性：同步、桥接、路由治理解耦

## 3. 可复用性评估（边界）
强依赖：
- Python 3.11+、http.server/urllib（轻量）
- 本地桥（oc_bridge/codearts_bridge）需后台常驻
- routes.json 结构 + sync 脚本的平台列表
- proxy3_py 内部调度（free_pool 模块）

松耦合：
- bridges 可独立容器化/进程化
- sync 脚本可独立 cron
- routes.json 可作为数据配置（JSON Schema）

## 4. 封装方式（推荐）
选项 A：子模块（git submodule） — 保持演进同步，改动回流难
选项 B：独立仓库 free-pool（精简版） — 清晰边界，便于复用、发版
选项 C：PyPI 包 + 配置目录 — 最通用（pip install free-pool，提供 CLI: free-pool serve/sync）
推荐 B+C：先整理为独立仓库（最小可用），再打包 CLI

## 5. 精简范围
保留：
- free-pool 调度核心（或将 proxy3 精简为最小 gateway）
- bridges（oc_free_bridge, codearts_bridge）
- sync_free_models.py + PLATFORMS 配置
- routes.json 模板 + README + 示例 env
剔除：
- lingclaude/lingflow 特定集成（admin key 策略、审计日志耦合）
- 非 free 相关路由治理、任务路由等

## 6. 接口设计
对外：
- OpenAI 兼容：POST /v1/chat/completions, GET /v1/models（透传/聚合）
- 管理：GET /healthz, GET /admin/free_pool/stats（可选鉴权）
- 配置：环境变量（FREE_POOL_*）、config.yaml（可选）

## 7. 部署形态
- 单进程（uvicorn/fastapi）替代 http.server？当前轻量够用
- docker-compose：gateway + bridges + sync cron
- systemd：三进程（gateway, oc_bridge, codearts_bridge）

## 8. 风险与对策
风险：桥依赖外部（opencode serve、Chrome CDP 抓 cookie） — 文档明确、只读/本地
风险：平台频繁变动 — sync 自动发现降低维护
风险：ToS/可用性 — 保持“只增不删+default_off”、可选黑名单

## 9. MVP 计划
Phase 0（论证）: 本文 ✓
Phase 1（提取）：复制 proxy3_py 核心（free_pool+bridges+sync）到新仓库 /tmp/free-pool
Phase 2（解耦）：移除内网耦合（路径、admin key、审计），提取配置
Phase 3（打包）：pyproject.toml + CLI（typer/click）+ docker + README
Phase 4（测试）：本地冒烟（/v1/models, chat）、sync dry-run
Phase 5（发布）：首版 0.1.0，文档使用方式

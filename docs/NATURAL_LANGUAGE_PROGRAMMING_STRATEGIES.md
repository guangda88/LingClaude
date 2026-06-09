# 自然语言编程固有局限的应对策略

**作者**: 灵克 (lingclaude)  
**日期**: 2026-05-31  
**状态**: 讨论稿  
**前提**: 基于灵族3个月、13成员、14仓库、7172测试的完整调查

---

## 一、策略5：审计闭环的最后一公里

### 问题本质

灵族Git Hooks审计能**发现**问题，但不能**强制修复**。审计报告→代码修复之间有一个"成员决定是否修"的决策点，这个决策点靠自觉。

**数据**：
- 灵极优exec() RCE：审计3轮，承诺3次，零代码改动
- 灵通SSH密码硬编码：审计2轮，密码仍在`hengyuan_pack_download.py:10`
- 已修复P0平均3.4天，未修复P0最长8+天
- 13+个P0未修复，修复率64%

### 为什么不能简单加CI红线

直觉方案：加一条CI规则"P0超过N天未修→拒绝merge"。

**但这有三个副作用**：
1. **阻断开发节奏** — 灵极优如果不修exec()，所有其他功能开发都被阻塞。这等于惩罚了整个项目而不是责任人
2. **P0定义争议** — 谁判定一个问题是P0？灵克审计报告中的P0，被审计方可能有不同看法。灵极优认为"AST沙箱已经够了"
3. **绕过激励** — 有了红线，成员可能倾向于降低审计严重度（把P0报成P1），而不是修复问题

### 分层策略

```
Layer 1: 发现（已有）
    灵克审计 + Git Hooks L0/L1/L2
    ↓
Layer 2: 告警（已有）
    LingBus告警 + governance讨论
    ↓
Layer 3: 确认（需加强）  ← 新增
    P0审计结果需要被审计方48h内确认或争议
    争议→governance投票仲裁
    ↓
Layer 4: 时限（需新增）  ← 核心
    确认后的P0：
    - P0（可利用）: 72h修复期限
    - P1（有风险）: 7天修复期限
    - P2（最佳实践）: 无强制期限
    ↓
Layer 5: 升级（需新增）  ← 强制执行
    超时未修复：
    - P0超72h → 灵克获得该问题的修复权限（跨权例外）
    - governance投票授权
    - 修复后原成员review
    ↓
Layer 6: 预防（中期）
    P0未修超过72h的新提交 → pre-commit提示提醒
    不阻断，但每次commit都可见
```

### Layer 3-5 的实施细节

**Layer 3（确认机制）**：
- 灵克发审计报告到LingBus（已有）
- 被审计方48h内回复：确认P0 / 争议（附理由）
- 争议→governance 24h快速投票（quorum=3，多数决）
- 确认后计时开始

**Layer 4（时限）**：
- 记录在灵克`self_driven_tasks.json`或独立`p0_tracker.json`
- 每次灵克健康巡检时检查时限
- 接近时限（剩12h）发LingBus提醒

**Layer 5（升级/接管）**：
- governance提案：`GOV-P0-ESCALATION`
- 规则：P0确认72h未修→灵克提交修复→原成员48h review→无异议则merge
- 安全护栏：灵克只改涉事代码，不动其他逻辑
- 原成员可以"正在修复中+进度证据"申请延期48h（仅一次）

### 与现有机制的兼容

| 现有 | 策略5影响 |
|------|----------|
| Git Hooks pre-commit | 不改。Layer 3-5在Hooks之外 |
| governance投票 | 新增快速投票通道（P0争议仲裁） |
| 灵克审计权限 | 扩展：P0超时获得修复权限（需governance授权） |
| 审计报告 | 新增：P0确认状态+时限+倒计时 |

### 预期效果

- 灵极优exec()：3轮零修复→72h内必须确认→确认后72h必须修或被接管
- 灵通SSH密码：从"承诺未交付"→有时限压力→governance可见
- 整体P0修复率：从64%→目标>90%

---

## 二、策略4：共享安全库 lingsec

### 问题本质

灵族13个成员共享协议（LingBus/MCP），但不共享实现。同一类安全问题（输入验证、认证、路径校验），每个项目独立实现，质量参差不齐。

**数据**：
- 输入验证：灵通用regex、灵极优用AST白名单、灵创用`_SAFE_PATH_RE`、灵网没有
- 认证：灵信用HMAC、灵网用动态token、灵知用`public_path_prefixes`、灵研用白名单
- Git Hooks：6个仓库用v3.0集中式，4个仓库用简化版，1个用Git默认sample

### 为什么不直接用PyPI上的安全库

可以用。但灵族有几个特殊需求：

1. **MCP协议层** — 标准安全库不覆盖MCP工具参数校验
2. **LingBus集成** — 签名验证需要跟灵信`signing.py`配合
3. **轻量** — 灵信是0依赖设计，lingsec也应该0依赖
4. **灵族约定** — SEC-*编号体系、安全基线框架、端口注册表等灵族特有的约定

### lingsec 设计

```python
# lingsec/ — 灵族共享安全库
# 0依赖，只用Python标准库

lingsec/
├── __init__.py
├── path.py          # 路径安全校验（统一 SAFE_PATH_RE + 路径穿越检测）
├── command.py       # 命令执行安全（subprocess wrapper + shell=False强制）
├── input.py         # 输入验证（类型+长度+范围+白名单）
├── auth.py          # 认证工具（HMAC签名、token校验，复用灵信signing.py）
├── network.py       # 网络安全（CORS配置、绑定地址校验、端口冲突检测）
├── secret.py        # 密钥管理（环境变量读取+chmod校验+泄露检测）
├── audit.py         # 审计辅助（安全自检清单、SEC-*编号映射）
└── types.py         # 共享类型（SecurityLevel、AuditResult、P0Record）
```

### 关键模块设计

**path.py — 统一路径校验**（灵创/灵极优/灵通各有各的，统一到这一个）：

```python
"""统一路径安全校验。所有文件操作必须经过此模块。"""
import os
from pathlib import Path

SAFE_BASE_DIRS = {
    "lingclaude": "/home/ai/lingclaude",
    "lingmessage": "/home/ai/lingmessage",
    # ... 按端口注册表
}

def validate_path(filepath: str, project: str, allow_create: bool = False) -> Path:
    """校验文件路径是否安全。
    
    规则：
    1. 必须在项目SAFE_BASE_DIR下
    2. 解析后不能越界（../检测）
    3. 符号链接不能指向项目外
    4. 不允许/dev/null, /proc, /sys等特殊路径
    """
    base = Path(SAFE_BASE_DIRS[project]).resolve()
    target = (base / filepath).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError(f"Path traversal: {filepath} escapes {base}")
    if target.is_symlink() and not str(target.resolve()).startswith(str(base)):
        raise ValueError(f"Symlink escape: {target} -> {target.resolve()}")
    if not allow_create and not target.exists():
        raise FileNotFoundError(target)
    return target
```

**command.py — 统一命令执行**（解决shell=True/ exec()/ 路径穿越）：

```python
"""统一命令执行安全。禁止shell=True，禁止exec()用户输入。"""
import subprocess
from typing import list

BLOCKED_COMMANDS = {"rm", "dd", "mkfs", "format", "shutdown", "reboot"}

def safe_run(args: list[str], *, cwd: str = None, timeout: int = 30) -> subprocess.CompletedProcess:
    """安全执行命令。
    
    规则：
    1. shell=False（强制）
    2. 命令必须在PATH中或绝对路径
    3. 阻止危险命令
    4. timeout强制
    """
    if not isinstance(args, list) or len(args) == 0:
        raise ValueError("args must be non-empty list")
    cmd = args[0]
    cmd_name = cmd.rsplit("/", 1)[-1] if "/" in cmd else cmd
    if cmd_name in BLOCKED_COMMANDS:
        raise ValueError(f"Blocked command: {cmd_name}")
    return subprocess.run(args, shell=False, cwd=cwd, timeout=timeout, 
                          capture_output=True, text=True)
```

### 实施路径

| 阶段 | 内容 | 负责 | 工作量 |
|------|------|------|--------|
| Phase 0 | governance提案 | 灵克 | 1天 |
| Phase 1 | lingsec骨架（path.py + command.py） | 灵克 | 3天 |
| Phase 2 | 各项目迁移（灵创/灵极优/灵通优先，有P0未修） | 各成员 | ~50行/项目 |
| Phase 3 | Git Hooks L1新增：检测到项目未用lingsec → WARN | 灵克 | 1天 |
| Phase 4 | auth.py + network.py + secret.py | 灵克+灵信 | 5天 |

### 风险

| 风险 | 缓解 |
|------|------|
| 成员不愿迁移 | Phase 3的hooks WARN是温和推动，不阻断 |
| lingsec成为新的单点 | 0依赖+小体量（<500行），烂了也好修 |
| 统一方案不如各自定制 | 安全不是定制化领域——路径穿越就是路径穿越，没有"我的项目需要不同风格的穿越检测" |

---

## 三、策略2：非功能需求的可见性提升

### 问题本质

自然语言天然只说"做什么"，不说"认证/限流/注入防护"。灵族Git Hooks能拦截部分（shell=True、SQL拼接），但只能匹配**已知模式**。

**数据**：
- L1拦截了69次密钥泄露、46次SQL注入、4次shell=True
- 但灵极优的`exec()`不在L1黑名单中——因为用的是`exec()`不是`subprocess(shell=True)`
- 22个P0中没有一个是"功能写错了"，全部是安全缺失

### 为什么扩展hooks不是根治

扩展L1黑名单（加`exec()`/`eval()`/`__import__`等）能堵更多，但：
1. **总有下一个不在名单中的写法** — `type().__subclasses__()`等元编程技巧
2. **语义理解超出静态分析** — `open(user_input)`是漏洞还是正常操作，取决于`user_input`从哪来

### 分层策略

```
Layer 1: Hooks黑名单扩展（立即可做）
    + exec() / eval() / __import__ 到L1
    + open(变量) 标记为WARN
    + os.system() / os.popen() 到L1
    
Layer 2: 安全模板（中期）
    新功能请求的"安全检查清单"
    不是自然语言对话中的自然产物，
    而是AGENTS.md中的条件触发器：
    "每次新增MCP工具 → 自动检查路径校验/认证/限流"
    
Layer 3: 语义审计（长期）
    用LLM理解代码语义（不只是模式匹配）
    灵克的LLM proxy已经有此能力
    但成本高、误报率高
```

### Layer 1 的具体扩展

在 `ling_audit_lib.py` 的L1检查中新增：

```python
# 新增到 L1_SHELL_INJECT 检查
DANGEROUS_BUILTINS = {
    "exec": "L1:DANGEROUS_EXEC",
    "eval": "L1:DANGEROUS_EVAL",
    "__import__": "L1:DANGEROUS_IMPORT",
    "compile": "L1:DANGEROUS_COMPILE",
}

# 新增到 L1
L1_OS_COMMAND = {
    "os.system": "L1:OS_SYSTEM",
    "os.popen": "L1:OS_POPEN",
    "subprocess.call": "L1:SUBPROCESS_CALL",  # 没有shell=参数也要检查
}

# 新增：文件操作审计
L1_FILE_INJECTION = {
    "open(": "L1:FILE_INJECTION",  # 如果参数不是字符串字面量 → WARN
}
```

### Layer 2 的安全检查清单

不是对话中自然产生的，而是**强制插入**到特定场景中：

```markdown
## AGENTS.md 条件触发器：新功能安全自检

触发条件：新增MCP工具 / 新增API端点 / 新增文件操作
执行流程：
1. 路径校验：所有文件路径参数是否经过白名单校验？
2. 认证：端点是否有认证？认证方式是否与SEC-AUTH-001一致？
3. 限流：是否有速率限制？是否与SEC-CMD-001一致？
4. 输入验证：所有外部输入是否经过类型+长度+范围校验？
5. 输出编码：返回用户的数据是否经过编码/转义？
6. 日志：是否有审计日志？是否记录操作者身份？

未通过以上检查 → 不提交。提交时pre-commit验证。
```

### 预期效果

- Layer 1：exec()/eval()/__import__从"不在黑名单"→"L1 FAIL拦截"
- Layer 2：新功能开发时强制思考安全问题（不是靠自觉，是靠触发器）
- 组合效果：灵极优类型的P0在开发阶段就被拦截，不需要等灵克审计发现

---

## 四、策略6：跨项目复杂度的管理

### 问题本质

灵族13个项目互相依赖。灵扬三层token提案需要灵知/灵律/智桥/灵信4个项目的协调——用自然语言讨论了5轮LingBus才理清依赖关系。

**当前灵族的跨项目依赖**：
```python
CROSS_REPO_DEPS = {
    "lingflowplus": ["lingflow", "lingmessage"],
    "lingyi": ["lingclaude"],
    "lingclaude": ["lingmessage"],
    "lingtongask": ["lingflow"],
}
```

这个依赖声明是**静态的、声明式的**，不能回答：
- "灵扬提案如果改了灵知的认证，智桥要不要改？"
- "灵创MCP工具改了参数格式，灵通能不能调？"

### 为什么不用现成的API契约工具

OpenAPI/protobuf是标准方案。但灵族的接口不是HTTP API为主——是MCP工具调用。MCP协议本身就是灵族的"API契约"（有schema定义、有类型标注），但当前没有**自动化的跨项目兼容性检查**。

### 分层策略

```
Layer 1: 依赖图可视化（立即可做）
    从CROSS_REPO_DEPS + MCP注册表生成依赖图
    每次修改接口时自动标注影响范围
    
Layer 2: MCP Schema版本化（中期）
    每个MCP工具的参数格式加版本号
    破坏性变更需要bump版本+通知依赖方
    
Layer 3: 自动兼容性检查（长期）
    pre-push时检查：我的MCP工具参数是否改了？
    如果改了，依赖方是否已更新？
```

### Layer 1 的具体实现

**依赖图**（从已有数据自动生成）：

```python
# 从 mcp_registry + CROSS_REPO_DEPS + LingBus 消息流自动推导
# 产出：docs/DEPENDENCY_GRAPH.md

# 示例输出：
# 灵扬三层token提案影响分析：
#   灵律(linglaw:8002) → CORS修改
#     ← 灵创(lingcreate:9528) 调用 /projects/linglaw/*
#     ← 智桥(zhibridge:8767) 代理 /projects/linglaw/*
#   智桥(zhibridge:8767) → 常驻化+认证层
#     ← 灵信(lingmessage) token颁发(signing.py复用)
#     ← 灵通+(daemon) 通过灵壳管理进程
```

**实现方式**：灵克自驱任务，每次全族审计时更新。不需要新工具，只需要一个Python脚本从LingBus消息流+MCP注册表+端口注册表提取调用关系。

### Layer 2 的 MCP Schema 版本化

当前灵族的MCP工具定义在`mcp_registry.py`中，但没有版本号。加一个字段：

```python
# 当前
MCP_SERVERS = {
    "lingminopt": {
        "tools": ["optimize", "load_results", "compare_results", ...]
    }
}

# 改为
MCP_SERVERS = {
    "lingminopt": {
        "tools": {
            "optimize": {"version": "1.0", "params": {...}},
            "load_results": {"version": "1.0", "params": {...}},
        },
        "schema_version": "1.0"
    }
}
```

破坏性变更（参数删除/类型改变）→bump版本→LingBus通知依赖方。

---

## 五、策略3：上下文管理的优化

### 问题本质

transformer的上下文窗口有限。灵克身份退化事件（104条消息~51K tokens后CRUSH.md被忽略）证明：**信息在窗口中≠信息被注意**。

**灵族已有的缓解措施**：
- CRUSH.md瘦身实验：6904B→1371B，命名率+30.5pp
- handover机制：跨会话上下文传递
- 条件触发器：唤醒时强制读CRUSH.md
- 灵壳（设计中）：proxy中断后身份重锚定

### 为什么不能根治

transformer的注意力机制是概率性的——CRUSH.md在位置1被读到，但在位置100的决策中权重可能很低。这不是提示工程能解决的，是架构约束。

### 可继续优化的方向

```
方向1: 信息密度提升（持续优化）
    - CRUSH.md已经从6904B瘦身到1371B
    - 下一步：handover.md也瘦身（当前~2KB）
    - 原则：每条规则必须被最近一次实证验证有效
    
方向2: 关键信息重注入
    - 不是放在头部等注意力衰减
    - 而是在关键决策点主动注入
    - 实现：灵壳的proxy中断后自动注入身份声明
    
方向3: 长期记忆外置
    - 128K上下文装不下的信息→外部存储
    - 灵克已有：knowledge.db / memory.db / metrics.db
    - 下一步：结构化查询（不是塞进上下文，是按需检索）
    
方向4: 灵壳层的注意力辅助
    - 灵壳检测到"用户问你是谁"→自动注入CRUSH.md身份声明
    - 灵壳检测到"proxy_fallback"→自动重锚定
    - 不改Crush，在壳层拦截并补充关键信息
```

### 方向2的具体设计（灵壳身份重锚定）

这是灵壳设计中已包含的功能，细化一下触发条件：

```
触发条件（灵壳检测到以下任一）：
1. Crush输出包含"你是谁" / "你是" / "who are you"
2. Crush输出包含"编程助手"（灵克退化后的典型回答）
3. proxy_fallback事件
4. 会话恢复（Crush重启后第一条消息）
5. idle超过30分钟后首次输出

灵壳动作：
1. 写入临时文件 /tmp/lingshell_reanchor_{pid}.md
   内容：CRUSH.md的前3行身份声明
2. 通过PTY注入到Crush输入
   （不修改Crush代码，通过终端输入模拟）
3. 记录日志
```

**注意**：这个方案依赖灵壳能拦截Crush的输出并判断是否需要重锚定。这是灵壳"渲染替代"功能的副产品——灵壳已经在读Crush的输出。

---

## 六、总结：策略优先级和依赖关系

```
策略5（审计闭环）     ← 立即可做，governance层面
    ↓ 提供修复压力
策略4（lingsec）      ← 1-2周可落地，工程层面
    ↓ 统一安全实现
策略2（hooks扩展）    ← 立即可做，防御层面
    ↓ 拦截更多已知模式
策略6（依赖管理）     ← 中期，协调层面
    ↓ 管理跨项目复杂度
策略3（上下文优化）   ← 持续优化 + 灵壳Phase 2
    ↓ 缓解注意力衰减
```

**不依赖灵壳的策略**（可立即开始）：5、4、2  
**依赖灵壳的策略**：3（方向2和4需要灵壳的输出拦截能力）  
**中期策略**：6（需要依赖图工具+schema版本化）

灵克 | lingclaude

# linggit-bot 设计规格

> 灵元1.0 拆解 atomgit-bot 的产物
> 作者: 灵克 (lingclaude) 2026-07-03
> 用途: 灵克代码审计时的 diff 级安全审查工具

---

## 一、灵元拆解 atomgit-bot

### 1.1 atomgit-bot 做了什么

AtomGit 平台的官方 AI bot，在合并请求创建后:
- AI 自动分析代码 diff
- 生成检视摘要 + 发现问题 + 提供可应用的代码建议
- 检测安全漏洞、代码异味、最佳实践偏离
- 在评论中 @atomgit-bot 提问，AI 回复

### 1.2 灵元三步拆解

**第一步: 找到什么不变**

atomgit-bot 的本质不变量:
- diff 进来 (信息出入: in)
- review 出去 (信息出入: out)
- 审查有状态: pending -> analyzing -> completed

每次审查都是一条信息出入导致状态流转。

**第二步: 砍到最薄**

atomgit-bot 砍到最薄:
- 出入: diff 进来 (create)，review 出去 (query)
- 流转: pending -> analyzing -> approved/rejected/escalated/audited

这就是 security_gate 状态机在 changeset 层的一个实例。

**第三步: 变化变成插片**

- 审查规则是插片 (安全规则、代码风格规则、最佳实践规则)
- 审查对象是插片 (diff、commit message、文件类型)
- 审查结果是插片 (摘要、风险等级、修复建议)

### 1.3 拆解结论

atomgit-bot = security_gate changeset 层的一个实例。

linggit-bot = 同一个状态机，不同的 rules 插片，面向灵克代码审计场景。

不需要新建独立系统。linggit-bot 就是 lingmemory 的 type=code_review + 规则引擎。

---

## 二、2T3A 映射

```
2T:
  records = 审查记录 (code_review type)
  events  = 审查事件 (每次状态变化)

3A:
  create     = submit(diff) — 提交代码变更
  query      = report(review_id) — 获取审查结果
  transition = analyze -> evaluate -> resolve -> audit
```

---

## 三、code_review type 注册

在 lingmemory/type_registry.yaml 新增:

```yaml
code_review:
  description: "代码变更安全审查 (linggit-bot)"
  default_state: pending
  states:
  - pending
  - analyzing
  - approved
  - rejected
  - escalated
  - audited
  transitions:
  - from: pending
    to: analyzing
    event: start_analyze
  - from: analyzing
    to: approved
    event: auto_pass
  - from: analyzing
    to: rejected
    event: auto_block
  - from: analyzing
    to: escalated
    event: gray_zone
  - from: escalated
    to: approved
    event: manual_approve
  - from: escalated
    to: rejected
    event: manual_reject
  - from: '*'
    to: audited
    event: auto
  data_schema:
    project:      {required: true, type: string}
    committer:    {required: true, type: string}
    files:        {required: true, type: array}
    diff_summary: {required: false, type: string}
    risk_level:   {required: false, enum: [low, medium, high, critical]}
    issues:       {required: false, type: array}
    suggestions:  {required: false, type: array}
    approver:     {required: false, type: string}
```

---

## 四、薄主干 — 4 函数

```python
from lingmemory import LingMemory
import fnmatch, re, yaml, subprocess, json

class LingGitBot:
    """linggit-bot — 灵族代码变更安全审查

    灵元拆解: atomgit-bot = security_gate changeset 层实例
    薄主干: 4 函数 (submit/analyze/evaluate/report)
    规则是插片: review_rules/*.yaml
    """

    def __init__(self, db_path, registry_path, rules_dir):
        self.lm = LingMemory(db_path, registry_path)
        self.rules_dir = rules_dir

    def submit(self, project, committer, files, diff_text=None):
        """1. 入口: 提交代码变更，创建 code_review record"""
        review_id = self.lm.create(
            type="code_review",
            data={
                "project": project,
                "committer": committer,
                "files": files,
                "diff_summary": diff_text[:500] if diff_text else "",
            },
            created_by="linggit-bot")
        return review_id

    def analyze(self, review_id):
        """2. 启动分析: pending -> analyzing，调用规则引擎"""
        self.lm.transition(review_id, "start_analyze", actor="linggit-bot")
        record = self.lm.get(review_id)
        data = record["data"]

        rules = self._load_rules()
        issues = []
        suggestions = []

        for rule in rules.get("checks", []):
            result = self._run_check(rule, data)
            if result:
                issues.extend(result.get("issues", []))
                suggestions.extend(result.get("suggestions", []))

        risk = self._assess_risk(issues)
        data["issues"] = issues
        data["suggestions"] = suggestions
        data["risk_level"] = risk

        return self._evaluate(review_id, data, rules)

    def _evaluate(self, review_id, data, rules):
        """3. 白/黑/灰判断 (同步阻塞)"""
        risk = data.get("risk_level", "low")
        if risk == "critical":
            self.lm.transition(review_id, "auto_block", actor="linggit-bot", data=data)
            return "auto_block"
        elif risk == "low" and not data.get("issues"):
            self.lm.transition(review_id, "auto_pass", actor="linggit-bot", data=data)
            return "auto_pass"
        else:
            self.lm.transition(review_id, "gray_zone", actor="linggit-bot", data=data)
            return "gray_zone"

    def report(self, review_id):
        """4. 生成审查报告: 摘要 + 风险等级 + 修复建议"""
        record = self.lm.get(review_id)
        d = record["data"]
        return {
            "review_id": review_id,
            "state": record["state"],
            "project": d.get("project"),
            "committer": d.get("committer"),
            "files": d.get("files", []),
            "risk_level": d.get("risk_level", "unknown"),
            "issues": d.get("issues", []),
            "suggestions": d.get("suggestions", []),
            "summary": self._generate_summary(d),
        }

    def _generate_summary(self, data):
        risk = data.get("risk_level", "unknown")
        files = data.get("files", [])
        issues = data.get("issues", [])
        return f"[{risk}] {len(files)} files, {len(issues)} issues"

    def _assess_risk(self, issues):
        if any(i.get("severity") == "critical" for i in issues):
            return "critical"
        if any(i.get("severity") == "high" for i in issues):
            return "high"
        if issues:
            return "medium"
        return "low"

    def _load_rules(self):
        import os
        rules = {"checks": []}
        rules_path = os.path.join(self.rules_dir, "review_rules.yaml")
        if os.path.exists(rules_path):
            loaded = yaml.safe_load(open(rules_path).read())
            if loaded:
                rules = loaded
        return rules

    def _run_check(self, rule, data):
        check_type = rule.get("type", "")
        if check_type == "pattern":
            return self._check_pattern(rule, data)
        elif check_type == "command":
            return self._check_command(rule, data)
        return None

    def _check_pattern(self, rule, data):
        files = data.get("files", [])
        pattern = rule.get("pattern", "")
        severity = rule.get("severity", "medium")
        desc = rule.get("description", pattern)
        fix = rule.get("fix", "")
        issues = []
        suggestions = []
        for f in files:
            if isinstance(f, str) and re.search(pattern, f):
                issues.append({"file": f, "severity": severity, "description": desc})
                if fix:
                    suggestions.append({"file": f, "fix": fix})
        return {"issues": issues, "suggestions": suggestions} if issues else None

    def _check_command(self, rule, data):
        cmd = rule.get("command", "")
        project = data.get("project", ".")
        try:
            result = subprocess.run(
                cmd.split(), cwd=project, capture_output=True, text=True, timeout=30)
            if result.returncode != 0 and rule.get("fail_on_nonzero", False):
                return {"issues": [{"file": "*", "severity": rule.get("severity", "medium"),
                                    "description": result.stderr[:200]}],
                        "suggestions": []}
        except Exception as e:
            return {"issues": [{"file": "*", "severity": "low",
                                "description": f"check failed: {e}"}],
                    "suggestions": []}
        return None
```

---

## 五、规则插片

```yaml
# review_rules.yaml
# linggit-bot 审查规则 — 灵克代码审计用

checks:
  # === 安全规则 ===
  - type: pattern
    pattern: "eval\("
    severity: critical
    description: "eval() 使用 — 代码注入风险"
    fix: "替换为 ast.literal_eval() 或 json.loads()"

  - type: pattern
    pattern: "shell=True"
    severity: high
    description: "subprocess shell=True — 命令注入风险"
    fix: "使用 shell=False + 参数列表"

  - type: pattern
    pattern: "os\.system\("
    severity: high
    description: "os.system() — 命令注入风险"
    fix: "使用 subprocess.run() + shell=False"

  - type: pattern
    pattern: "except\s*:"
    severity: high
    description: "裸 except — 吞异常"
    fix: "使用 except Exception as e: 并记录"

  - type: pattern
    pattern: "except.*pass"
    severity: high
    description: "silent except — 异常被静默吞掉"
    fix: "至少 log.warning(str(e))"

  - type: pattern
    pattern: "(password|secret|token|api_key)\s*=\s*['\"][^'\"]+['\"]"
    severity: critical
    description: "硬编码密钥"
    fix: "使用环境变量或配置文件"

  # === 灵族特有规则 ===
  - type: pattern
    pattern: "CRUSH\.md"
    severity: medium
    description: "修改身份锚定文件 — 需审查"
    fix: "确认是否为身份漂移修复"

  - type: pattern
    pattern: "handover\.ya?ml"
    severity: medium
    description: "修改交接文件 — 需确认状态变更"
    fix: "确认 handover 铁律: 读后必验 读完必写"

  - type: pattern
    pattern: "\.env"
    severity: high
    description: "触及 .env 文件 — 密钥泄露风险"
    fix: "确认 .env 在 .gitignore 中"

  # === 代码质量规则 ===
  - type: pattern
    pattern: "import.*#.*unused"
    severity: low
    description: "可能的 unused import"
    fix: "移除未使用的导入"

  # === 命令规则 (调用外部工具) ===
  - type: command
    command: "ruff check --select S"
    severity: medium
    fail_on_nonzero: true
    description: "ruff 安全检查"
```

---

## 六、使用方式

### 6.1 灵克代码审计时

```python
from linggit_bot import LingGitBot

bot = LingGitBot(
    db_path="/home/ai/lingclaude/crush.db",
    registry_path="/home/ai/lingclaude/lingmemory/type_registry.yaml",
    rules_dir="/home/ai/lingclaude/linggit/rules",
)

# 提交 diff
review_id = bot.submit(
    project="/home/ai/lingflow",
    committer="lingflow",
    files=["src/main.py", "src/utils.py"],
    diff_text="...",
)

# 分析
result = bot.analyze(review_id)

# 获取报告
report = bot.report(review_id)
print(report["summary"])
# [high] 2 files, 3 issues

# 灰区 -> LingBus 通知双签
if result == "gray_zone":
    # 通知灵安/灵克审查
    pass
```

### 6.2 与 SDT-lc-001 集成

灵克 SDT-lc-001 (全族代码审计) 当前用 audit_scanner.py 做全量扫描。
linggit-bot 可作为 SDT-lc-001 的增量补充:
- 全量扫描: audit_scanner.py (每周)
- 增量审查: linggit-bot (每次 push/commit)

### 6.3 与灵安 changeset 层的关系

linggit-bot 是灵安 changeset 层的先行实现。
灵安上线后，linggit-bot 的规则和状态机直接被 changeset 层复用。
linggit-bot 是灵安的 MVP (最小可行产品)。

---

## 七、与 atomgit-bot 的对比

| 维度 | atomgit-bot | linggit-bot |
|------|-------------|-------------|
| 平台 | GitCode 平级 bot | 灵族内部工具 |
| 触发 | PR 创建后命令触发 | git commit/push 或手动 |
| 审查对象 | PR diff | 任意代码变更 |
| 规则 | 平台内置 | YAML 插片热加载 |
| 状态机 | 无 (直接出结果) | 5态状态机 (lingmemory) |
| 灰区处理 | 无 | escalate + 双签 |
| 审计 | 无 | lingmemory events 表 |
| 底层架构 | 平台绑定 | 灵元 2T3A |
| 灵族特有规则 | 无 | CRUSH.md/handover/.env 保护 |

---

## 八、实施计划

| 阶段 | 内容 | 工作量 | 依赖 |
|------|------|--------|------|
| 1 | code_review type 注册到 type_registry.yaml | 20行 YAML | 无 |
| 2 | LingGitBot 类实现 (4函数) | ~150行 Python | 阶段1 |
| 3 | review_rules.yaml 规则编写 | ~50行 YAML | 阶段2 |
| 4 | 灵克 SDT-lc-001 集成测试 | 测试 | 阶段3 |
| 5 | git pre-commit hook (可选) | ~20行 shell | 阶段4 |

阶段1-3 可在本会话完成。阶段4-5 需测试验证。

---

起草: 灵克 (lingclaude) 2026-07-03

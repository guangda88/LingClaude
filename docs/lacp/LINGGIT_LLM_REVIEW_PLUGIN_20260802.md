# 灵督 LLM 复核插片设计方案（降误报专项）

> 作者: 灵克 (lingclaude) 2026-08-02
> 目标: 把 atomgit-bot 的 LLM 语义复核能力并入灵督 linggit-bot，用"规则扫描 + LLM 复核"
> 双引擎消除误报，复用灵族现有 model 栈，不引第三方依赖。

---

## 一、背景与问题

灵督是规则引擎（正则插片 + security_gate 状态机）。本次 atomcode 审计暴露核心短板：

| 指标 | 数值 | 根因 |
|------|------|------|
| 命中 109 项 | critical 49 + low 60 | 规则与目标语言强耦合 |
| **critical 误报 49/49** | 100% 误报 | 正则无法区分测试桩/示例字符串/真实密钥 |
| 漏报 | 8 处 `sh -c` 全漏 | 无 Rust 语法规则 |

atomgit-bot（gitcode-bot）用 LLM 语义审查能识别结构性/上下文问题（如 PR #859 的 HTML
标题层级错误），但不可定制、不可批量、不可离线。**灵督补 LLM 层 = 兼得两者**。

## 二、架构设计（双引擎）

```
                ┌──────────────────────────────────────┐
                │            LingGitBot.audit_batch    │
                └──────────────────────────────────────┘
                                    │
              ┌─────────────────────┴─────────────────────┐
              │  L0 规则引擎（现有，零成本）               │
              │  review_rules.yaml 正则命中               │
              └─────────────────────┬─────────────────────┘
                                    │ issues (含疑似误报)
              ┌─────────────────────┴─────────────────────┐
              │  L1 LLM 复核插片（新增）                  │
              │  verdict: true_positive / false_positive  │
              │  / uncertain                              │
              └─────────────────────┬─────────────────────┘
                                    │ 过滤后的 issues
              ┌─────────────────────┴─────────────────────┐
              │  _assess_risk / report                    │
              └───────────────────────────────────────────┘
```

**关键点**：L1 只在 L0 命中后触发，不改变规则引擎本身；LLM 不可用时**降级**为纯规则
结果（fail-open，不阻塞审计）——灵督保持"审计永不失败"的既有承诺。

## 三、插片设计

### 3.1 新增模块 `linggit/llm_review.py`

```python
"""灵督 LLM 复核插片 — 语义层二次确认，降低规则引擎误报"""

from lingclaude.model.factory import create_provider

class LLMReviewer:
    def __init__(self, config: dict):
        # config: {enabled, provider, model, threshold, batch_size, timeout_s}
        self.enabled = config.get("enabled", False)
        self.threshold = config.get("threshold", "high")  # 仅复核 high/critical
        self.batch_size = config.get("batch_size", 8)
        self.timeout_s = config.get("timeout_s", 20)
        self._provider = None  # 懒加载

    def _ensure_provider(self):
        if self._provider is None and self.enabled:
            res = create_provider(provider_name="openai")  # 复用 config.yaml 凭据
            if res.ok:
                self._provider = res.value
        return self._provider

    def review(self, issues: list[dict]) -> list[dict]:
        """对每个 issue 打 verdict 标记，误报直接剔除"""
        if not self.enabled:
            return issues
        provider = self._ensure_provider()
        if provider is None:
            return issues  # 降级：LLM 不可用 → 保留全部（fail-open）
        # 仅复核达阈值级别的命中（cost 控制）
        candidates = [i for i in issues
                      if sev_rank(i.get("severity")) >= sev_rank(self.threshold)]
        # 批量聚合：按 file 分组，一个请求复核一组，省 token
        for file_group in group_by_file(candidates):
            verdicts = self._ask_llm(file_group)   # -> {issue_key: verdict}
            for i in file_group:
                v = verdicts.get(i["key"], "uncertain")
                i["llm_verdict"] = v
        # 过滤：仅剔除明确 false_positive；uncertain 保留并标注
        return [i for i in issues if i.get("llm_verdict") != "false_positive"]
```

### 3.2 复核 Prompt（核心降误报逻辑）

```
你是代码安全审计复核员。以下是从源码扫描命中的规则告警，请判断每条是
真实问题还是误报（测试桩、示例字符串、占位符、注释、文档示例、环境变量
引用、`#[cfg(test)]` 内代码）。

规则命中格式: {file}:{line} [{severity}] {rule} | 命中内容: {snippet}

对每条输出 JSON: {"key": "<file>:<line>", "verdict": "true_positive" |
"false_positive" | "uncertain", "reason": "<一句话理由>"}

要求:
- 命中内容为 sk-test/sk-ant-test/test/valid/example 等明显测试值 → false_positive
- 命中行以 # / // / /// 开头（注释）→ false_positive
- 内容引用 $ENV_VAR / <placeholder> → false_positive
- 无法确定 → uncertain（宁可保留，不可误杀）
```

### 3.3 接入点 `bot.py`（2 处改动）

```python
def __init__(self, ..., llm_review: dict | None = None):
    ...
    self._llm = LLMReviewer(llm_review or {})

# audit_batch: 收集 all_issues 后、_assess_risk 前
all_issues = self._llm.review(all_issues)      # ← 插入点
risk = self._assess_risk(all_issues)

# review: issues = self._analyze_diff(...) 之后
issues = self._llm.review(issues)              # ← 插入点
```

## 四、降误报策略（三层）

| 层 | 机制 | 成本 | 覆盖 |
|----|------|------|------|
| L0a | 规则级：值长度约束 + `$`/`<` 排除（已实施） | 0 | 短测试桩 |
| L0b | 规则级：注释行/`#[cfg(test)]`/文件名白名单 | 0 | 结构误报 |
| **L1** | **LLM 语义复核（本次新增）** | token | 上下文误报（残留） |

L1 专门解决"L0 正则无论如何都分不清"的语义问题——这正是本次 49 个 critical
误报的根因（`scrub.rs` 测试夹具 vs 真实 token，肉眼可辨但正则不可辨）。

## 五、成本控制

| 手段 | 说明 |
|------|------|
| 阈值过滤 | 仅 high/critical 送 LLM；low（TODO 类）不送 |
| 批量聚合 | 按 file 分组，一组一个请求（平均 5-10 命中/请求） |
| 超时降级 | 20s 超时 → 整组标记 uncertain，不阻塞 |
| 结果缓存 | `(file, line, rule_hash)` → verdict 缓存 24h，重复审计 0 成本 |
| 开关默认关 | `llm_review.enabled=false`，显式开启 |
| 模型选择 | 复用 config.yaml 的 glm-4.7（大模型语义强）；可选 openai/anthropic |

## 六、风险与对策

| 风险 | 对策 |
|------|------|
| LLM 误杀真漏洞 | verdict 默认 uncertain；仅剔除明确 false_positive |
| LLM 不可用阻塞审计 | fail-open：异常/超时 → 保留全部 issues |
| 依赖 model 栈 | 复用 factory.create_provider，零新依赖 |
| token 成本 | 阈值过滤 + 批量 + 缓存 + 默认关闭 |

## 七、与 atomgit-bot 对比

| 能力 | 灵督+LLM插片 | atomgit-bot |
|------|------------|-------------|
| 批量审计 | ✅ 513 文件/数秒 + LLM 复核 | ❌ 按 PR |
| 可定制规则 | ✅ yaml 插片 | ❌ |
| 语义复核 | ✅ 新增（等效） | ✅ 原生 |
| 离线/内部 | ✅ 灵族 model 栈 | ❌ 平台依赖 |
| 治理闭环 | ✅ security_gate | ❌ |

**结论**：插片后灵督在语义层追平 atomgit-bot，且在批量、定制、离线、治理四维
全面占优——这正是用户"灵督更好用"体验的工程化落地。

## 八、实施路径

1. **P0 立即可做**：`LLMReviewer` 单文件实现 + bot.py 2 处接入 + 3 单测
2. **P1 数据验证**：用 atomcode 审计历史 109 项跑复核，量化误报率（目标 49→≤5）
3. **P2 完善**：verdict 写入 security_gate data、缓存、report 展示 llm_verdict
4. **P3 推广**：全族 SDT-lc-001 启用，审计报告带"规则命中 + LLM verdict"双标注

---

*本方案复用 lingclaude.model 现有 provider 栈（config.yaml: glm-4.7 / open.bigmodel.cn），
无第三方依赖；LLM 复核失败自动降级为纯规则结果，不改变灵督既有行为。*

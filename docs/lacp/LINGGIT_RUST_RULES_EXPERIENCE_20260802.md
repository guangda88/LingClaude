# linggit-bot Rust 规则插片经验总结

> 作者: 灵克 (lingclaude) 2026-08-02
> 场景: 用 linggit-bot 审计 atomcode 源码（Rust 引擎，513 文件）
> 结论: **linggit-bot 规则与目标语言强耦合**，跨语言审计必须先补规则插片，否则大面积误报/漏报。

---

## 一、问题背景

linggit-bot 的 `review_rules.yaml` 最初面向 Python 代码（eval / shell=True / os.system / pickle），
直接用它审计 Rust 仓库时出现两种失真：

| 失真类型 | 表现 | 根因 |
|---------|------|------|
| **误报** | 49 个 critical "硬编码密钥"，全部是测试桩/示例字符串 | 密钥规则 `['"]+` 匹配任意短值 |
| **漏报** | 8 处 `sh -c` 执行、48 处 unsafe、4 处 env unwrap 全部漏检 | 规则是 Python 语法，Rust 对应模式（`Command::new`）无规则 |

## 二、规则插片内容

### 2.1 收紧硬编码密钥规则（消除误报）

```
旧: (password|secret|token|api_key)\s*=\s*['"][^'"]+['"]      # 任意值都命中
新: (password|secret|token|api_key)\s*=\s*['"][^'"$<]{12,}['"] # ≥12字符，排除 $ENV / <占位符>
```

一次排除的误报样本：
- 测试桩: `sk-test`(7) / `sk-ant-test`(11) / `"valid"` / `"test"` / `"host-secret"`(11)
- 环境变量引用: `api_key = "$OPENROUTER_API_KEY"`（`$` 被排除）
- 占位符: `<your-codingplan-api-key>`（`<` 被排除）

### 2.2 新增 Rust 规则插片

```yaml
# === Rust 规则插片 ===
- pattern: 'Command::new\(\s*["''](sh|bash|/bin/(sh|bash))["'']\s*\)'
  severity: high      # shell 执行 — 命令注入面
- pattern: '(std::)?env::var\s*\([^)]*\)\s*\.(unwrap|expect)\s*\('
  severity: high      # 环境变量 unwrap — 缺配置即 panic
- pattern: 'unsafe\s*\{'
  severity: medium    # unsafe 块 — 需人工审查
- pattern: '\.join\(\s*["''][^"'']*\.\.[^"'']*["'']\s*\)'
  severity: medium    # 路径 join 含 .. — 路径穿越风险
- pattern: '(from_utf8_unchecked|transmute)'
  severity: high      # UB 风险
```

## 三、审计效果对比（513 文件 / 3.2s）

| 严重级 | 旧规则 | 新规则 | 变化 |
|--------|--------|--------|------|
| critical | 49（全误报） | 2（测试夹具） | ↓47 |
| high | 0 | 11（8 shell + 3 env unwrap） | ↑11 |
| medium | 0 | 51（48 unsafe + 3 join） | ↑51 |
| low | 60 | 60 | 不变 |
| 合计 | 109 | 124 | 信号纯度提升 |

## 四、经验沉淀（供 SDT-lc-001 全族审计复用）

1. **审计前先做语言适配**：对新语言的仓库，先用 `grep -c` 统计目标模式噪音量
   （如 `.unwrap()` 全库 5040 处 → 不能直接作为规则），再决定规则粒度。
2. **密钥规则必须带值约束**：`[^'"$<]{12,}` + 排除注释行/占位符/环境变量引用，
   否则测试桩淹没真实告警。
3. **filename 型规则天然语言无关**：CRUSH.md / .env / members.yaml 等跨语言复用。
4. **critical 告警必须人工复核**：机器判定 risk_level=critical 时，仍需逐条核查
   原文（本次 49 条 critical 全部为测试数据，靠人工复核确认）。
5. **测试夹具污染**：脱敏/加密功能的测试数据（如 `scrub.rs` 的 `ghp_Qq123...`）
   无法用长度规则排除，建议加文件名白名单或 `#[cfg(test)]` 识别。

## 五、后续建议

- [ ] 为 linggit-bot 增加语言感知规则集（`rules/rust.yaml` / `rules/python.yaml` 分片加载）
- [ ] 密钥规则增加 test-file 降级增强（现有 `_is_test_file` 仅按文件名判断）
- [ ] 把 hook/skill 的 `sh -c` 执行白名单修复建议提交 atomcode issue（见
      `LINGGIT_HOOK_WHITELIST_FIX_20260802.md`）

# D4 SDT-lc-001 v2 注册表 — 灵克起草 + 灵安增强

> **依据**: 灵安 7/27 会议 D4 议程 + 7/31 Codex Security 参考整合
> **版本**: v2.1.0
> **日期**: 2026-07-31
> **状态**: ✅ 已起草，待联署确认

---

## 一、注册表条目

| 字段 | v1 (当前) | v2 (新) |
|------|----------|---------|
| sdt_id | SDT-lc-001 | SDT-lc-001-v2 |
| name | 全族代码审计 | 全族代码审计 v2 |
| description | 全族代码安全审计+bandit扫描+漏洞检测 | 全族代码安全审计+bandit扫描+漏洞检测+依赖漏洞+license合规+AI上下文分析+覆盖率追踪+SARIF导出+增量扫描 |
| direction | 方向1 安全 | 方向1 安全 |
| priority | P1 | P1 |
| interval_minutes | 1440 | 1440 |
| risk_level | low | medium |
| type | maintenance | maintenance |
| exit_condition | 连续3次bandit=0+无新发现 | 连续3次bandit=0+无新发现+连续3次safety=0+覆盖率≥90% |
| external_verification | L1: bandit输出 | L1: bandit输出 + L1: safety输出 + L1: license输出 + L1: coverage.json + L1: SARIF |
| sdt_version | (空) | 2.1.0 |
| verifier | (空) | lingan |
| status | active | active |
| enabled | true | true |

---

## 二、v2 新增能力

### 1. 扩展扫描范围

| 扫描项 | v1 | v2 |
|--------|----|----|
| bandit 安全扫描 | ✅ | ✅ |
| safety 依赖漏洞 | ❌ | ✅ |
| license 合规检查 | ❌ | ✅ |
| secrets 检测 | ❌ | ✅ |
| AI 上下文分析 | ❌ | ✅ |
| 增量 diff 扫描 | ❌ | ✅ |

### 2. 覆盖率追踪 (coverage.json)

**参考**: Codex Security coverage.json 设计

```json
{
  "completeness": "complete|partial|unknown",
  "scanned_files": 453,
  "total_files": 480,
  "coverage_percent": 94.4,
  "excluded_patterns": ["node_modules/", "*.test.py"],
  "scan_duration_seconds": 120,
  "timestamp": "2026-07-31T12:00:00+08:00"
}
```

**关键规则**:
- `completeness=partial` 时，结论必须标记为"未完整扫描"
- `completeness=unknown` 时，必须人工确认覆盖范围
- 覆盖率 < 90% 时，exit_condition 不满足

### 3. SARIF 格式导出

**目的**: 对接 GitHub Code Scanning + 安全治理平台

```json
{
  "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
  "version": "2.1.0",
  "runs": [{
    "tool": {
      "driver": {
        "name": "lingan-sdt-lc-001",
        "version": "2.1.0",
        "informationUri": "https://github.com/lingan/sdt-lc-001"
      }
    },
    "results": []
  }]
}
```

**导出路径**: `.audit/sarif_$(date +%Y%m%d).json`

### 4. AI 上下文分析

**目的**: 降低误报率，提升修复建议质量

| 能力 | 说明 |
|------|------|
| 调用链分析 | 追踪函数调用路径，判断漏洞是否可达 |
| 数据流分析 | 追踪用户输入到敏感函数的数据流 |
| 架构感知 | 结合项目架构判断风险等级 |
| 证据生成 | 每个发现附带可复现的证据链 |
| 修复建议 | 基于代码上下文生成贴合的修复方案 |

**误报过滤规则**:
- `eval()` 在 test fixture 中 → 降级为 low
- `shell=True` 在 CLI 工具中 → 降级为 medium
- `pickle.loads()` 在可信数据源 → 降级为 medium

### 5. 增量 diff 扫描

**目的**: 日常开发只审查本次改动，替代全量扫描

```bash
# 标准 diff 扫描
bandit -r . --diff $(git merge-base HEAD origin/main) HEAD

# 工作区未提交改动
bandit -r . --diff HEAD

# 增量扫描 + AI 上下文分析
python3 -m lingan.audit.incremental_scan \
  --base HEAD \
  --head HEAD \
  --enable-ai-context
```

**触发时机**:
- git commit 前 (L6 changeset hook)
- git push 前 (门禁检查)
- PR 创建时 (CI 集成)

---

## 三、联署机制

- **联署方**: 灵安（安全审查）+ 灵克（执行）
- **签名验证**: 每次审计结果附带 LingBus 签名
- **审计日志**: 结果写入 `.audit/` 目录 + LingBus 通告
- **覆盖率要求**: 扫描完成必须附带 coverage.json
- **SARIF 导出**: 重大审计必须导出 SARIF 格式

---

## 四、增强验证门

| 门 | 目标 | 验证方式 |
|----|------|---------|
| L1-bandit | bandit 输出 0 HIGH/MEDIUM | grep -c HIGH/MEDIUM |
| L1-safety | safety 输出 0 vulnerabilities | grep -c "vulnerabilities found" |
| L1-license | 无 GPL/AGPL 依赖 | licensecheck 输出 |
| L1-coverage | 覆盖率 ≥ 90% | coverage.json coverage_percent |
| L1-sarif | SARIF 导出成功 | 文件存在且 valid |
| L2-签名 | 审计结果已签名 | LingBus verify_signature |
| L2-AI上下文 | 误报过滤生效 | AI 分析报告 |

---

## 五、执行协议

### 执行步骤

1. **增量扫描模式** (日常)
   - `git diff` 获取变更文件列表
   - bandit 扫描变更文件
   - AI 上下文分析变更代码
   - 生成 coverage.json (变更范围覆盖率)
   - 结果汇总 → `.audit/incremental_$(date +%Y%m%d_%H%M).json`

2. **全量扫描模式** (每周/每月)
   - bandit 扫描全族代码
   - safety 检查依赖
   - license 检查
   - secrets 检查
   - AI 上下文分析
   - 生成 coverage.json (全量覆盖率)
   - SARIF 导出
   - 结果汇总 → `.audit/full_scan_$(date +%Y%m%d).md`
   - LingBus 通告 → 灵安联署确认

### 频率

- **增量扫描**: 每次 commit/push (L6 changeset hook)
- **全量扫描**: 每周 P1 优先级
- **SARIF 导出**: 每次全量扫描

---

## 六、待联署确认

| 联署方 | 角色 | 状态 |
|--------|------|------|
| 灵安 | 安全审查 | 🟡 待确认 |
| 灵克 | 执行 | ✅ 已起草 |
| 灵通+ | 调度 | 🟡 待确认 |

---

## 七、注册命令（待执行）

```python
# 联署确认后执行以下注册
mcp_lingbus_sdt_registry(
    command="register",
    member="lingclaude",
    sdt_id="SDT-lc-001-v2",
    name="全族代码审计 v2",
    description="全族代码安全审计+bandit扫描+漏洞检测+依赖漏洞+license合规+AI上下文分析+覆盖率追踪+SARIF导出+增量扫描",
    direction="方向1 安全",
    priority="P1",
    interval_minutes=1440,
    risk_level="medium",
    type="maintenance",
    exit_condition="连续3次bandit=0+无新发现+连续3次safety=0+覆盖率≥90%",
    external_verification="L1: bandit输出 + L1: safety输出 + L1: license输出 + L1: coverage.json + L1: SARIF",
    sdt_version="2.1.0",
    verifier="lingan"
)
```

---

## 八、参考来源

| 来源 | 用途 |
|------|------|
| Codex Security coverage.json | 覆盖率追踪设计 |
| Codex Security SARIF | 格式导出标准 |
| Codex Security diff 扫描 | 增量审查模式 |
| Codex Security AI 上下文 | 误报过滤 + 修复建议 |

---

—— 灵克（lingclaude）+ 灵安（lingan）· D4 SDT-lc-001 v2 注册表 · 2026-07-31

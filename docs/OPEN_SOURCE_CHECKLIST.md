# 开源准备清单 v1.0

来源: 会话84全族战略讨论收敛 (thread 5142c5392fec45b7bfdb1716f6f981d9)
主持: 灵克(lingclaude)
定位: Layer1 代码开源 → 2026年7月（1月后）

## 定位

**灵族 = "开源AI组织" 不是 "开源项目"**

代码开源只是手段，真正壁垒是: 48起事故经验 + 灵元方法论 + 12子协作模式 + 六道防线。
开源先发代码(做诚意)，再发方法论(做差异化)，经验库最后发(做护城河)。

## 分层开源时间线

```
Layer1 (1月): proxy21 + probe.py + pipeline.py + ling-ui Base组件
  → GitHub public, Apache 2.0
Layer2 (2月): 灵元V1.0方法论 + 六道防线 + 薄主干审计
  → 技术博客 + 文档站
Layer3 (3月+): 48起事故(脱敏) + coding_rule + R5指标
  → 灵族差异化壁垒，最晚开源
```

## 开源前必须完成的6项

### ✅ 已完成
| 项 | 状态 | 备注 |
|----|------|------|
| proxy21认证/限速 | ✅ | 已有auth模块 |
| Git无key泄漏 | ✅ | 已验证 |

### ⚠️ 待完成

**1. 硬编码路径清理 — 各成员自查 3天**

| 文件 | 需清理 | 优先级 |
|------|--------|--------|
| proxy21/scheduler.py | 无硬编码路径 | ✅ |
| proxy21/proxy.py | 无硬编码路径 | ✅ |
| lingmemory/* | `/home/ai/lingclaude/`路径在code中存在 | P1 |
| 各成员scripts/* | 检查 `sys.path.insert` 和 `open(/home/ai/...)` | P1 |

**2. 事故案例脱敏 — 1周**

- 删除成员名(灵通→Agent-F, 灵克→Agent-A, 通用代号)
- 删除绝对路径(`/home/ai/xx` → `/opt/ling/xx`)
- 删除IP/hash/API key
- 保留: 错误模式、根因分析、修复方案、教训

**3. LICENSE决定 — 需用户确认**

建议: **Apache 2.0**
- 比MIT严格(专利保护)
- 比GPL宽松(允许商业使用)
- 主流AI项目标准(PyTorch/TensorFlow/Kubernetes)
- Layer1代码Apache 2.0, Layer3经验CC BY-NC(非商业)

**4. README定位 — 需用户确认**

建议结构:
```
# 灵族 — 开源AI组织

灵族是12个AI Agent长期共存的工程实验。
从3个月48起事故中演化出:
- 六道认知防线(创建→执行→发布→审计→可见→评估)
- 灵元V1.0方法论(薄主干+插片)
- 议事厅协议(40分钟12方决策收敛)
- proxy21 LLM网关(100x成本压缩, 30+ provider实测)

## 快速开始
...

## 架构
...

## 灵族成员
...

## 相关论文/实验
...
```

建议添加: `CRUSH.md` 根目录, 替代README作为项目介绍。
README用英文(国际化), 中文版在`README.zh.md`。

**5. 成员AGENTS.md脱敏**

当前AGENTS.md包含:
- 绝对路径信息 `/home/ai/...`
- 内部端口号 :8765/:9530/:5436
- 内部服务名
- 灵族成员分工详细

开源版需: 删除路径/IP/端口, 保留架构描述和能力说明。

**6. 灵忆import路径清理**

当前全族用 `sys.path.insert(0, '/home/ai/lingclaude')` 引用灵忆。
开源前需完成软独立Step 2(MCP独立systemd service)。

## 开源协作模型

### AI维护 + 用户最终确认

```
外部issue → LingBus alert → AI成员认领(按方向)
  ↓
外部PR → AI成员review → 用户最终确认 → merge
```

**关键约束**:
- AI不能自主merge外部代码
- 外部PR merge必须走用户确认(不能仅AI签名)
- 安全相关issue灵犀优先响应

### 对外身份

| 渠道 | 运营者 | 频率 |
|------|--------|------|
| GitHub Issues | AI成员+用户 | 按需 |
| 技术博客(知乎/掘金) | 灵扬 | 月1-2篇 |
| 邮件列表 | 灵扬(225联系人) | 周1推送 |
| 论文(arXiv) | 灵研 | 季1篇 |

## 开源前自检清单

```bash
# 1. 密钥检查
grep -r "sk-" --include="*.py" --include="*.json" --include="*.yaml" --include="*.sh" --include="*.md" .
grep -r "api_key" --include="*.py" --include="*.json" .
grep -r "ark-" --include="*.py" --include="*.json" .

# 2. 路径检查
grep -r "/home/ai/" --include="*.py" --include="*.md" --include="*.yaml" .

# 3. 端口检查
grep -rE ":(8765|9530|5436|8100|8900)" --include="*.py" --include="*.json" --include="*.yaml" .

# 4. 灵族成员名检查(保留方法论描述, 删除内部评价)
grep -r "灵通\|lingflow\|lingclaude\|lingxi" --include="*.md" | grep -v "CHANGELOG\|handover\|README"
```

## 开源收益预期

| 收益 | 量化 | 时间 |
|------|------|------|
| 社区贡献者 | 5-20人/月 | 3月后 |
| GitHub Star | 100-1000 | 6月 |
| 潜在付费客户转化 | 1-5个 | 12月 |
| 学术引用 | 3-10篇 | 12月 |
| 招聘(如果未来需要) | 社区→员工 | 18月 |

## 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 代码被盗用/竞品复制 | 高 | 中 | Apache 2.0专利保护 |
| API key泄漏 | 中 | 高 | 开源前清除所有key |
| 社区issue淹没AI | 低 | 高 | 只开放有限通道 |
| 失去研究自由 | 中 | 中 | 保持"研究型经营者"定位 |

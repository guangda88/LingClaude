# AI-08 lingpack 规范 v0.1 — 灵族 .ling 包打包工具

> **作者**: 灵克 (lingclaude) · AI-20260627-08 spec
> **目的**: 灵族成员产出可分发的 `.ling` 包 (包含 SOP + verify + manifest 三段)
> **状态**: W2 末 (7/7) 灵克 spec 完 + 灵通 impl 完
> **关联**: AI-05 trace_emitter (已交付, 14/14 tests) / AI-04 OH §6 实验 1 / EP001 EP-001 lingpack PoC

---

## 1. 设计目标

将灵族"知识包"封装成自包含的 `.ling` 文件, 可被:
- (a) 其他灵族成员加载 (skill apply)
- (b) 外部系统 (browser_agg, CI/CD) 调用
- (c) 灵族日报自动 import (AI-07 dashboard)

**核心理念**: schema v0.1 = manifest / sop / verify 三段式 (COMBO_SKILLS_v0.1.md 已定义)

---

## 2. .ling 包结构

```
my-skill.ling  (zip / tarball)
├── manifest.yaml      # 元数据 + 依赖 + 输入输出契约
├── sop.md             # 标准操作流程 (人/AI 都可读)
├── verify.py          # 自校验脚本 (返回 0/1)
├── skill.yaml         # skill schema (可选, 用于 AI apply)
├── assets/            # 静态资源 (可选)
│   ├── template.*
│   └── config.json
└── audit/             # 审计日志 (可选)
    └── history.jsonl
```

---

## 3. manifest.yaml schema

```yaml
# === 必备字段 ===
name: <string>                    # 包名, k8s-style (e.g. "audit-scanner")
version: <semver>                 # e.g. "0.1.0"
owner: <member>                   # 灵族成员 (lingclaude / lingflow / etc.)
description: <string>             # 一行描述
tags: [<string>]                  # 标签 (e.g. ["security", "audit"])

# === 依赖 ===
depends:
  python: ">=3.11"
  packages: ["requests>=2.31", "pyyaml>=6.0"]
  ling: ["trace_emitter>=0.1"]   # 依赖的其他 .ling 包

# === 输入输出契约 ===
inputs:
  - name: <string>
    type: <string>                 # file / string / json / env
    required: <bool>
    default: <any>
outputs:
  - name: <string>
    type: <string>

# === 元数据 (LACP v0.5.0 字段) ===
trace:
  actor: <灵族成员名>             # owner = 主执行者
  actor_role: <enum>              # producer / verifier / daemon
  executor: <进程名@semver>       # 实际执行进程
  cost_estimate: <string>         # e.g. "low / <1s / 1 model call"

# === 自校验 ===
verify:
  entry: verify.py
  timeout_seconds: 30
  expects:                        # verify.py 退出码 0 时的最低产出
    - logs/receipt.jsonl

# === 适用场景 ===
applies_to:
  - "<使用场景描述>"
excludes:
  - "<不适用的场景, 避免误用>"
```

---

## 4. sop.md 模板

```markdown
# SOP: <skill name>

## 目的
<一句话目标>

## 何时使用
- ✅ <触发条件 1>
- ✅ <触发条件 2>
- ❌ <反例>

## 操作步骤

### Step 1: <动词 + 目标>
<具体操作>

### Step 2: <动词 + 目标>
<具体操作>

## 失败模式
- <失败现象>: <排查思路>

## 关联
- LACP actor: <哪个成员执行>
- verify.py: <如何验证>
```

---

## 5. verify.py 模板

```python
#!/usr/bin/env python3
"""<skill name> 自校验脚本"""

import sys
import json
from pathlib import Path

def verify() -> int:
    """返回 0 = 通过, 1 = 失败"""
    receipts = []
    
    # Step 1: 验证 manifest 字段
    manifest_path = Path(__file__).parent / "manifest.yaml"
    if not manifest_path.exists():
        print(f"❌ manifest.yaml 缺失")
        return 1
    
    # Step 2: 验证 SOP 可读
    sop_path = Path(__file__).parent / "sop.md"
    if not sop_path.exists() or sop_path.stat().st_size < 100:
        print(f"❌ sop.md 缺失或过短")
        return 1
    
    # Step 3: 验证 trace emitter 可用 (依赖 AI-05)
    try:
        from trace_emitter import TraceEmitter
        emitter = TraceEmitter(actor="<member>", actor_role="verifier")
        emitter.emit({"event": "verify.start", "skill": "<name>"})
    except ImportError:
        print(f"⚠️  trace_emitter 未安装, 跳过 trace")
    
    # Step 4: 输出 receipt
    Path("logs").mkdir(exist_ok=True)
    receipts.append({"check": "manifest_ok", "ts": "..."})
    receipts.append({"check": "sop_ok", "ts": "..."})
    with open("logs/receipt.jsonl", "w") as f:
        for r in receipts:
            f.write(json.dumps(r) + "\n")
    
    print(f"✅ {len(receipts)} 项校验通过")
    return 0

if __name__ == "__main__":
    sys.exit(verify())
```

---

## 6. CLI 工具 (AI-08 impl 目标)

```bash
# 打包
lingpack pack ./my-skill/ -o my-skill.ling

# 校验
lingpack verify my-skill.ling

# 安装到灵忆
lingpack install my-skill.ling

# 列出已安装
lingpack list

# 移除
lingpack remove <skill-name>
```

---

## 7. W2 末交付清单 (7/7)

| owner | 任务 | 状态 |
|-------|------|------|
| 灵克 | spec v0.1 (本文档) | 🟡 draft |
| 灵通 | CLI impl (Python 3.11) | 🟡 待 |
| 灵克 | 集成 trace_emitter + LACP actor field | 🟡 待 |
| 灵通 | 与灵忆 lm_query 集成 | 🟡 待 |
| 灵极优 | 单元测试 (pack/verify/install 路径) | 🟡 待 |

---

## 8. 与 AI-05 trace_emitter 的集成

每个 .ling 包安装时, 自动:
1. 提取 manifest.actor → 写入灵忆 "skill_installed" record
2. trace_emitter 记录 install 事件 (actor_role=daemon)
3. 触发灵族日报 dashboard 增量 (AI-07)

每个 .ling 包执行时, 自动:
1. trace_emitter 记录 invoke 事件 (actor_role=producer)
2. cost_estimate 与实际 cost 对比, 超 2x 报警
3. verify.py 输出写入 audit/history.jsonl

---

## 9. 关联 EP001 灵通问道 EP-001 lingpack PoC

EP001 是 lingpack 在 灵通问道 13 SKILL 上的首次实际应用。
W2 末前 EP001 必须:
- 13 SKILL 全部打包为 .ling
- 灵族日报 dashboard 至少显示其中 3 个 SKILL 的调用统计
- verify.py 全部返回 0

---

## 元注

本 spec 是 AI-08 的上半段 (灵克 spec)。
impl 段 (灵通) 在 W2 末前完成。
EP001 是真实压力测试 — 如果 EP001 跑通, lingpack 即从"工具"升级为"灵族标准发布单元"。

— 灵克 · 2026-06-27 v0.1 draft
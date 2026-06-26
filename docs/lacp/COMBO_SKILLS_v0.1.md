# Ling Skills (Combo Skills 灵族版) — 三段式封装 v0.1

> **薄主干**: Combo Skill = `manifest.md` + `sop.md` + `verify.md` 三件。
> **插片**: 单个 skill 是 LACP plugin manifest 的实例化。

## 为什么三段式

灵族当前散落 SOP 形态：
- 文档（markdown）— 只描述
- 脚本（python/bash）— 只执行
- 审计报告 — 只回顾

三段式让 **可描述 + 可执行 + 可验证** 装进同一目录，可被飞轮 distill、可被 verifier hook 验证、可被 LACP trace 跟踪。

## 三件契约

### `manifest.md` — 契约（必填）

声明插片的接口、owner、依赖。

```yaml
---
schema_version: "0.1.0"
skill:
  name: <kebab-case>
  version: "0.x.y"
  owner: <member-name>
  description: <一句话>
interface:
  inputs:
    - name: <param>
      type: <type>
      required: true | false
  outputs:
    - name: <param>
      type: <type>
replaceable: cold | warm | hot
dependencies:
  - <plugin-name>@<version-range>
tags: [security, git, audit, ...]
---
```

### `sop.md` — 步骤（必填）

人类 + AI 都可读的执行步骤。Free-form，但建议结构：

```markdown
## 触发
什么情况下用这个 skill

## 前置
依赖 / 检查

## 步骤
1. ...
2. ...

## 失败处理
- 如果 X 失败 → 怎么办
```

### `verify.md` — 验证（必填）

如何确认 skill 执行成功。

```markdown
## E2E 验证
- [ ] 验证项 1
- [ ] 验证项 2

## 单元测试
- `tests/test_<skill-name>.py` — 5+ tests

## 回归检查
- 不破坏 X
```

## 目录结构

```
skills/
└── <skill-name>/
    ├── manifest.md      # 契约 (LACP plugin manifest 实例)
    ├── sop.md           # 步骤
    ├── verify.md        # 验证
    ├── impl.py          # 可选 - 实现代码
    └── tests/           # 可选 - skill 自身测试
        └── test_<skill-name>.py
```

## 命名规范

- skill 目录: `<verb>-<noun>` kebab-case (e.g. `apply-security-patch`, `commit-with-trace`)
- manifest.md skill.name: 同目录名
- version: semver, 0.x.y (0 = 还在迭代)

## LACP 对接

- skill = plugin manifest 实例 → 可被 LACP 插片契约校验
- skill 执行 → emit Trace(phase=EXECUTE, target_plugin=skill.name@version, ...)
- skill 验证 → emit Trace(phase=VERIFY, outcome=PASS/FAIL, ...)

## 已有 skill 迁移

- 检查 `lingclaude/skills/`、`lingclaude/docs/SOP_*.md` 等散落文档
- 按三段式重写为 `skills/<name>/{manifest,sop,verify}.md`
- v0.1 阶段不强行迁移，新 skill 用新格式 + 旧 skill 并行

## PoC 2 验证（灵通）

PoC 2 TaskRegistry verifier hook 应该**直接做成 Combo Skill**，不是散落代码：
- `skills/verify-task-registry/manifest.md` 声明接口
- `sop.md` 描述验证逻辑
- `verify.md` 自验证 E2E

这样 verifier 可被飞轮 distill、可被热替换、可被其他 skill 调用。
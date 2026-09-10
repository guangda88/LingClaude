# SELF-OPT: read 工具目录边界放宽（extra_read_roots）

> 状态: **pending**（todo `624faa95`，优先级 6）
> 提出: 2026-09-10 会话，用户指令"read 限制过严，请放宽"
> 施工期约束: **不早于 P2.a 落地**（wiring.py 是参数注入点），且不与 P1/P2 提交混栈

## 一、背景与实证

- 现状: `lingclaude/engine/file_read.py:218-232` `_resolve()` 是**单一 `base_dir` 硬边界**，
  绝对路径 resolve 后不在 base 内 → `拒绝访问路径 X（超出基础目录 Y）`
- 实测复现: read `/etc/hostname` 被拒（本会话）
- 构造函数实证: `FileReadTool.__init__(base_dir=".", max_file_size=5MB, ...)` —
  **无白名单机制，无 per-call 例外**（本会话已读源码确认）
- 痛点: 系统级排查（`/proc`、`/etc`、`/var/log`、兄弟项目目录）只能用 bash `cat` 绕行，
  绕过了 read 工具的 size guard / 行号处理 / 编码检测，审计负担反而更重

## 二、设计（最小权限原则）

```python
# file_read.py
def __init__(self, base_dir=".", ..., extra_read_roots: list[str] | None = None):
    self.base_dir = Path(base_dir).resolve()
    self.extra_read_roots = [Path(r).resolve() for r in (extra_read_roots or [])]

# _resolve 判定顺序:
#   1. resolved 在 base_dir 内        → ok（现状行为，含写入共用时不变）
#   2. resolved 在任一 extra root 内  → ok（新增，仅读）
#   3. 否则                           → 拒绝（错误消息附带当前白名单，便于诊断）
```

- **只放宽读**。`file_edit.py:257` 的同名边界**一行不动**（写入面不扩大）
- 默认 `None` → 空列表 = 行为向后兼容，现有测试基线不破（fail-safe default）
- 配置入口: P2.a `wiring.py` manifest 中 read 工具构建行注入；
  可选 config 键 `security.extra_read_roots`（显式配置，无"全放行"快捷值）

## 三、安全评估（低置信领域，必须留痕）

| 维度 | 评估 |
|---|---|
| 放宽面 | 只读；read contract `rollback="No rollback needed"` 无副作用 |
| 主要风险 | 白名单含 `/` 或 `$HOME` 时敏感文件泄露面扩大 |
| 缓解 | 白名单显式逐项配置；错误消息回显白名单；不做通配/环境变量展开 |
| symlink | 判定在 `resolve()` 后的目标上（与现状一致），extra root 内 symlink 指向界外仍拒——**不新增逃逸面** |
| 密钥泄露 | 审计钩子密钥模式检测不覆盖 read 内容路径——白名单默认空即现状 |

## 四、测试计划（红→绿，6 例）

1. 默认构造（无参数）: `/etc/hostname` **仍拒** ← 回归保护
2. `extra_read_roots=["/etc"]`: `/etc/hostname`、`/etc/passwd` 可读
3. 白名单外仍拒: `/usr/bin/python3`（未列入）拒绝
4. base_dir 内路径行为不变 ← 回归
5. symlink 逃逸: extra root 内建 symlink → `/etc/shadow` 仍拒
6. 写边界不变: `file_edit` 对 extra root 内路径**仍拒**

## 五、实施顺序

1. 前置: P1 提交落地 + P2.a（wiring.py）合入
2. `file_read.py` 改造（约 15 行）+ 测试 6 例
3. `wiring.py` manifest 注入（约 1 行）+ config 读取
4. 全量定向: `pytest tests/ -k "file_read or wiring" -q`
5. 收尾: 本文档状态改 done，todo `624faa95` complete

预估: ~40 行代码 + 6 测试 + 1 行接线。

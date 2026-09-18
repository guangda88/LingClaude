"""行编辑能力单源 helper —— 2026-09-18 方向键/历史修复。

症状：方向键被转成 ^[[A 字面字符、无法移光标、上键翻不出历史。

根因：
- 方向键发出的是 ANSI 转义序列（\\x1b[A 等）。只有行编辑器（GNU readline /
  prompt_toolkit）接管终端时才会被解释成「左移/右移/翻历史」；裸 input() 的
  行规程由内核 tty 驱动按普通字符回显，于是字面显示。
- 全仓库裸 input() 有 5 处，此前只有 repl.py fallback_read 分支挂了 readline
  （try-import 内联在分支里），其余 4 处（interface.py×2、coding.py、
  full_tui.py 降级）均裸读 —— 哪条降级路径被走到，哪条就没方向键没历史。
- 历史还有个隐性缺口：GNU readline 只是 import 即挂钩 input()，但 **不会自动
  把 input() 读到的行写进历史**。repl.py 分支此前调 push_to_history 落盘到
  FallbackSession 的历史文件，但从未写进 readline 内存历史 → 就算挂了
  readline，上键也翻不出上一条。

修法（单源化）：全部裸 input() 统一经本 helper：
- ensure_readline(): 幂等挂载 GNU readline（import 失败静默，如 Windows/精简构建）
- add_history_line(line): 非空行去重连续后写 readline 内存历史（HISTCONTROL=ignoredups
  语义，避免按上键来回抖在同一行之间）

PTY 探测受限未实测的行为按文档知识实现，上层调用失败一律静默 ——
helper 的职责是「尽力增强」，绝不让输入路径因增强失败而坏掉。
"""

from __future__ import annotations

_rl_attached = False


def ensure_readline() -> bool:
    """幂等挂载 GNU readline 钩住内建 input()；成功返回 True。

    import readline 的副作用即完成挂钩（CPython 直接挂钩 GNU readline，
    libedit 衍生版由 site 内的兼容 shim 处理），无需额外注册。
    重复调用无副作用（_rl_attached 幂等 + import 幂等）。
    """
    global _rl_attached
    if _rl_attached:
        return True
    try:
        import readline  # noqa: F401 — 导入即生效
        _rl_attached = True
        return True
    except ImportError:
        # Windows / 精简构建无 readline：静默放弃，退回内核 tty 行规程
        return False


def add_history_line(line: str) -> bool:
    """把已提交的一行写进 readline 内存历史；写入返回 True。

    规则：空行/纯空白不记；与上一条完全相同不记（ignoredups，避免上键在
    重复行之间打转）。调用前不必 ensure_readline —— 未挂载时调用本函数
    等价于顺手挂载。
    """
    line = (line or "").strip()
    if not line:
        return False
    if not ensure_readline():
        return False
    import readline

    n = readline.get_current_history_length()
    if n > 0 and readline.get_history_item(n) == line:
        return False
    readline.add_history(line)
    return True


def load_history_file(path: str) -> None:
    """把已落盘的历史文件喂进 readline 内存历史（跨进程延续上键体验）。

    FallbackSession 启动时调用（传其历史文件路径）；文件缺失/损坏静默
    跳过 —— 增强路径，绝不反噬输入。
    """
    if not ensure_readline():
        return
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                add_history_line(raw.rstrip("\n"))
    except OSError:
        pass

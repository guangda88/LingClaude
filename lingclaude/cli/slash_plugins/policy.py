"""斜杠命令插件：/policy —— 策略热更（2026-09-30 方案 A 自 commands.py 主干迁出）。

迁移背景：命令注册原是硬编码主干（SLASH_REGISTRY 旁的 _register 行），
新增/修改命令必须改 commands.py 并重启。本文件是插件化的首条试跑命令：
丢进本目录即注册，运行中新增文件由 /policy reload 免重启拾取。
"""
from __future__ import annotations


def policy_cmd(processor, arg: str = "") -> None:
    """P6: /policy reload —— 主动触发 PolicyLoader 热更检查。

    背景（2026-09-30）：自然 mtime watch 有 30s 节流窗（_WATCH_INTERVAL），
    运行中改 yaml（sgr_styles / table_render 等）最多等 30s 才被感知。
    本命令内部调 policy_loader.hot_update()（不依赖 mtime、内容比较兜底），
    改完立即生效；TUI 下刷新后追加 resync 全量重绘，所见即最新策略。

    2026-09-30 扩展：顺带重扫斜杠命令插件目录——运行中新增的插件 .py
    首次加载后立即可用（首次 import 不触碰 importlib 缓存，符合
    「reload data 不 reload module」红线；已加载插件的修改仍需重启）。
    """
    from lingclaude.core import policy_loader

    if arg.strip() not in ("", "reload"):
        print("[/policy] 用法：/policy reload（当前仅支持 reload）")
        return
    try:
        changed = policy_loader.hot_update()
    except Exception as e:  # noqa: BLE001 — 热更失败不阻塞会话
        print(f"[policy 热更失败] {e}")
        return
    if changed:
        print("[policy 已热更] 内容有变化，新策略即刻生效")
    else:
        print("[policy 检查完毕] 无变化（缓存已是最新）")

    # 插件重扫：运行中新增的插件 .py 首次加载即注册（幂等）
    try:
        from lingclaude.cli.slash_plugin_loader import load_slash_plugins

        new_cmds = load_slash_plugins()
    except Exception as e:  # noqa: BLE001 — 插件重扫失败不影响策略热更主结果
        print(f"[slash 插件重扫失败] {e}")
        new_cmds = []
    for cmd in new_cmds:
        print(f"[slash 插件新命令] {cmd} 已注册，立即可用")

    session = getattr(processor, "session", None)
    resync = getattr(session, "resync", None)
    if callable(resync):
        try:
            resync()
        except Exception:  # noqa: BLE001 — 重绘失败不阻塞会话
            pass


def register(add) -> None:
    """loader 契约：add(name, fn, desc, aliases=(), needs_args=, arg_hint=)。"""
    add(
        "/policy", policy_cmd,
        "策略热更：/policy reload 立即重载 policies/*.yaml 并拾取新斜杠插件（免重启）",
    )

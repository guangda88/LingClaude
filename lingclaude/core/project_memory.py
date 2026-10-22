"""per-project memory（P1③ 件 B，2026-10-02）。

定位：项目级长期记忆的**文件式**落点 + 系统提示自动注入。
与既有记忆设施的关系：
- LayeredMemory.experience（SQLite）：经验条目，agent 自主读写，无项目维度过滤；
- L7 CognitiveStore（SQLite）：认知记忆分层，supersede 链见 l7_cognitive（P1③ 件 A）；
- 本模块：**人类可手编**的 markdown 笔记，按项目目录天然隔离——
  豆包清单「Auto Memory 笔记」项的 lc 形态（不走向量库、不做 RAG，
  触发条件见 docs/agent_trend_2026-09_external_review.md 对账附录裁定）。

文件位置：``<项目根>/.lingclaude/project_memory.md``（项目根 = cwd）。
- 格式自由 markdown；建议每行一条、行首日期，append-only 演变史自然形成；
- 人类手编优先，agent 维护走 append_project_memory（时间戳+单行）；
- 注入点：build_dynamic_system_suffix 动态尾随块（P0-2/P0-3 前缀缓存纪律：
  前缀冻结，一切动态内容只进尾部）；
- fail-soft：文件缺失/读失败/超限 → 截断或空串，绝不反噬 prompt 组装。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_FILENAME = "project_memory.md"
#: 单文件注入上限（chars）。超限保头部截断——prompt 预算保护，非存储上限。
DEFAULT_MAX_CHARS = 4000
#: 环境变量：显式指定记忆文件路径；设为 "off"/"0" 禁用注入。
ENV_OVERRIDE = "LINGCLAUDE_PROJECT_MEMORY"
# ---------------------------------------------------------------------------
# 记忆分层（2026-10-22，P4）：根治「超限截断=尾部新条目丢失」。
# 主文件超阈值时，把注入不可见的溢出尾部（DEFAULT_MAX_CHARS 之后）搬入
# 归档文件 + 主文件留指针标记——注入语义完全不变（那部分本就不可见），
# 信息零丢失（静默截断 → 可查档案）。写路径自愈，读路径不产生副作用。
# ---------------------------------------------------------------------------
ARCHIVE_FILENAME = "project_memory_archive.md"
#: 触发分层的主文件字符阈值（= 注入上限+缓冲：阈值过高会留下
#: 「已截断注入却未归档」的不可见窗口，过低则每次追加都触发自愈）
AUTO_ARCHIVE_THRESHOLD = 4500
#: 指针标记+新条目的字符预算——保留量须给它们留位，否则标记被注入截断
_MARKER_BUDGET_CHARS = 600


def memory_path(project_dir: str | os.PathLike | None = None) -> Path:
    """解析记忆文件路径。env 显式路径 > <project_dir|cwd>/.lingclaude/project_memory.md"""
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override and override.lower() not in ("off", "0"):
        return Path(override)
    root = Path(project_dir) if project_dir else Path.cwd()
    return root / ".lingclaude" / MEMORY_FILENAME


def project_memory_enabled() -> bool:
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    return override.lower() not in ("off", "0")


def load_project_memory(
    project_dir: str | os.PathLike | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """读取项目记忆文本（已渲染为注入块；空/禁用/失败 → 空串）。

    截断策略：超 max_chars 保头部 + 尾部提示行（记忆按时间追加时，
    头部是最早笔记——若要"最新优先"，人类编辑时应把活跃条目挪到文件头部，
    本模块不擅自重排用户文件）。
    """
    if not project_memory_enabled():
        return ""
    try:
        path = memory_path(project_dir)
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return ""
        truncated = False
        if len(text) > max_chars:
            text = text[:max_chars].rstrip()
            truncated = True
        header = f"\n\n# 📌 项目记忆（{MEMORY_FILENAME}，可手编）\n"
        footer = "\n... (截断：文件超限，请手工精简)\n" if truncated else "\n"
        return header + text + footer
    except Exception:  # noqa: BLE001 — 注入组件不得破坏主 prompt
        logger.debug("project_memory 读取失败", exc_info=True)
        return ""


def append_project_memory(
    line: str,
    project_dir: str | os.PathLike | None = None,
) -> bool:
    """追加一条带时间戳的记忆行（agent 维护入口；人类可直接编辑文件）。

    append-only：单行一行，失败返回 False 不抛（学习笔记性质，丢一条不致命）。
    """
    try:
        path = memory_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d")
        clean = " ".join(str(line).split())  # 压成单行
        if not clean:
            return False
        with path.open("a", encoding="utf-8") as f:
            f.write(f"- [{stamp}] {clean[:300]}\n")
        _auto_archive_if_oversized(path)
        return True
    except Exception:  # noqa: BLE001
        logger.debug("project_memory 追加失败", exc_info=True)
        return False


def _auto_archive_if_oversized(
    path: Path,
    threshold: int = AUTO_ARCHIVE_THRESHOLD,
    keep_chars: int = DEFAULT_MAX_CHARS - _MARKER_BUDGET_CHARS,
) -> bool:
    """写路径自愈：主文件超阈值时把**最旧头部**搬入归档（fail-soft）。

    设计要点(2026-10-22 狗粮实测修正)：条目按尾部追加，**头部=最旧、尾部=最新**。
    注入保头部截断，若按"保头切尾"会把最新进展切进归档（下会话恰好看不见）——
    方向必须反过来：保尾(新)切头(旧)，旧条目归档可寻回，注入始终看到最新状态。
    归档统一锚定 `.lingclaude/`（与指针标记一致，无论记忆文件在哪层），
    追加式成时间线，主文件原子替换。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) <= threshold:
            return True
        cut = len(text) - keep_chars
        # 条目对齐：切口向后吸附到下一个条目起点（"\n- ["），避免把条目劈成两半
        nxt = text.find("\n- [", cut)
        if nxt != -1 and nxt - cut <= 2000:
            cut = nxt + 1
        overflow = text[:cut]  # 最旧头部 → 归档
        kept = text[cut:].lstrip()  # 最新尾部 → 留守注入
        if path.parent.name == ".lingclaude":
            archive = path.parent / ARCHIVE_FILENAME
        else:
            archive = path.parent / ".lingclaude" / ARCHIVE_FILENAME
        archive.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with archive.open("a", encoding="utf-8") as f:
            f.write(f"\n## 归档于 {stamp}（{len(overflow)} chars）\n{overflow}\n")
        marker = (
            f"\n\n> ⚠ 主文件曾超限（{threshold} chars），头部 {len(overflow)} chars 旧条目"
            f"已归档至 .lingclaude/{ARCHIVE_FILENAME}（注入不可见但可查）。"
            f"请精简合并活跃条目。\n"
        )
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(marker.strip() + "\n" + kept, encoding="utf-8")
        os.replace(tmp, path)
        logger.info(
            "project_memory 自动分层: 头部 %s chars 归档（主文件保尾部 %s）",
            len(overflow), len(kept),
        )
        return True
    except Exception:  # noqa: BLE001 — 自愈失败不影响记忆已追加的事实
        logger.debug("project_memory 分层失败", exc_info=True)
        return False

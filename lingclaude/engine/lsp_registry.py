"""LSP server 配置注册表 — lsp add 命令后端（对标 Crush `lsp add`）。

提供：
- register(lang, command, args): 注册语言 → LSP server 启动命令
- list / remove / get: 查询与删除
- 持久化到 ~/.lingclaude/lsp_servers.json（运行时 add 的配置）
- 默认内置映射（python→pylsp、rust→rust-analyzer、typescript→typescript-language-server）
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path.home() / ".lingclaude" / "lsp_servers.json"
_LSP_ENV_OVERRIDE = "LINGCLAUDE_LSP_CONFIG"


def _config_path() -> Path:
    """配置文件路径（可被 LINGCLAUDE_LSP_CONFIG 环境变量覆盖，单测用）。"""
    override = os.environ.get(_LSP_ENV_OVERRIDE)
    if override:
        return Path(override)
    return Path.home() / ".lingclaude" / "lsp_servers.json"

# 默认内置映射（不可被 remove 删除，add 可覆盖）
_DEFAULT_SERVERS: dict[str, dict[str, Any]] = {
    "python": {"command": "pylsp", "args": [], "default": True},
    "rust": {"command": "rust-analyzer", "args": [], "default": True},
    "typescript": {"command": "typescript-language-server", "args": ["--stdio"], "default": True},
    "go": {"command": "gopls", "args": [], "default": True},
}

# 语言 → 文件扩展名（dispatcher 自动路由用）
_LANG_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": (".py", ".pyi"),
    "rust": (".rs",),
    "typescript": (".ts", ".tsx", ".js", ".jsx"),
    "go": (".go",),
}


def _load_user_servers() -> dict[str, dict[str, Any]]:
    """读取用户自定义 LSP 配置（JSON）。"""
    try:
        config_path = _config_path()
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load LSP config: %s", e)
    return {}


def _save_user_servers(servers: dict[str, dict[str, Any]]) -> None:
    """持久化用户自定义 LSP 配置。"""
    try:
        config_path = _config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(servers, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except OSError as e:
        logger.warning("Failed to save LSP config: %s", e)


def get_server(lang: str) -> dict[str, Any] | None:
    """查询语言对应的 LSP server 配置（用户配置优先，回退内置）。"""
    lang = lang.lower()
    user = _load_user_servers().get(lang)
    if user is not None:
        return user
    return _DEFAULT_SERVERS.get(lang)


def register(lang: str, command: str, args: list[str] | None = None) -> dict[str, Any]:
    """注册语言 → LSP server 启动命令（持久化到用户配置）。"""
    lang = lang.lower()
    if not lang or not lang.strip():
        raise ValueError("lang is required")
    if not command or not command.strip():
        raise ValueError("command is required")
    user = _load_user_servers()
    user[lang] = {"command": command.strip(), "args": args or [], "default": False}
    _save_user_servers(user)
    return {"lang": lang, "command": command.strip(), "args": args or [], "default": False}


def remove(lang: str) -> bool:
    """删除用户自定义 LSP 配置（内置默认不可删）。"""
    lang = lang.lower()
    user = _load_user_servers()
    if lang in user:
        del user[lang]
        _save_user_servers(user)
        return True
    if lang in _DEFAULT_SERVERS:
        return False  # 内置默认不可删
    return False


def list_servers() -> list[dict[str, Any]]:
    """列出全部 LSP server（内置 + 用户自定义）。"""
    user = _load_user_servers()
    result: list[dict[str, Any]] = []
    for lang, cfg in _DEFAULT_SERVERS.items():
        entry = {"lang": lang, **cfg}
        if lang in user:
            entry = {"lang": lang, **user[lang], "default": False}
        result.append(entry)
    for lang, cfg in user.items():
        if lang not in _DEFAULT_SERVERS:
            result.append({"lang": lang, **cfg})
    return result


def check_server(lang: str, workspace_root: str | None = None) -> dict[str, Any]:
    """LSP server 最小握手验证（cc P0: /lsp 只注册不验证协议可用性）。

    对已注册 server 执行 initialize 握手，返回:
      {ok, lang, command, error?, capabilities?}
    - ok=False 且 error 含"未安装"时: 命令不存在
    - ok=False 且 error 含"超时"时: server 启动但未响应协议
    - 无 server 配置时 graceful 降级（ok=False, error="未注册"）
    """
    cfg = get_server(lang)
    if cfg is None:
        return {"ok": False, "lang": lang, "command": "", "error": f"未注册 LSP server: {lang!r}"}

    cmd = [cfg["command"], *cfg.get("args", [])]
    if not cmd:
        return {"ok": False, "lang": lang, "command": "", "error": "server 命令为空"}

    import asyncio
    import shutil

    if shutil.which(cmd[0]) is None:
        return {
            "ok": False,
            "lang": lang,
            "command": cmd[0],
            "error": f"命令未安装: {cmd[0]}（请先安装或 /lsp add 替换）",
        }

    from lingclaude.engine.lsp_provider import StdioLspProvider

    root = workspace_root or str(Path.cwd())

    async def _handshake() -> dict[str, Any]:
        provider = StdioLspProvider(cmd, workspace_root=Path(root))
        try:
            caps = await provider.initialize(Path(root))
            return {"ok": True, "lang": lang, "command": cmd[0], "capabilities": caps}
        except Exception as e:
            return {"ok": False, "lang": lang, "command": cmd[0], "error": str(e)[:300]}
        finally:
            try:
                await provider.shutdown()
            except Exception:
                pass

    try:
        # 总握手超时 8s：server 未响应视为失败，避免验证命令长时间卡住
        return asyncio.run(asyncio.wait_for(_handshake(), timeout=8))
    except (asyncio.TimeoutError, Exception) as e:  # 兜底：事件循环/启动/超时异常
        msg = str(e)[:300] if str(e) else "握手超时"
        return {"ok": False, "lang": lang, "command": cmd[0], "error": msg}


def detect_lang(file_path: str) -> str | None:
    """按文件扩展名检测语言（dispatcher 自动路由）。"""
    from pathlib import Path

    suffix = Path(file_path).suffix.lower()
    for lang, exts in _LANG_EXTENSIONS.items():
        if suffix in exts:
            return lang
    return None

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


def detect_lang(file_path: str) -> str | None:
    """按文件扩展名检测语言（dispatcher 自动路由）。"""
    from pathlib import Path

    suffix = Path(file_path).suffix.lower()
    for lang, exts in _LANG_EXTENSIONS.items():
        if suffix in exts:
            return lang
    return None

"""Patches for lingyuan integration (owner: lingclaude).

A — workspace autoinject for ``lingyuan.datalog.write_event``.

This module monkey-patches :func:`lingyuan.datalog.write_event` so every
call attaches ``data.workspace = os.getcwd()`` when the caller did not
pass an explicit value. It lets the Better Harness evidence adapter pick
up LingYuan 1.0 events without waiting for a coordinated
DATALOG_SCHEMA_V2 upgrade in lingminopt.

Usage (single-line integration)::

    from lingclaude.lingyuan_patches import enable_workspace_autoinject
    enable_workspace_autoinject()

Once enabled, the patch is idempotent: calling it twice is a no-op.
Disable with :func:`disable_workspace_autoinject` to restore the
original function (useful for tests).
"""

from __future__ import annotations

import os
from typing import Any, Optional


_PATCH_STATE: dict[str, Any] = {
    "enabled": False,
    "original": None,
    "module": None,
}


def _resolve_workspace(value: Optional[str]) -> Optional[str]:
    """Mirror ``lingyuan.datalog._resolve_workspace`` for callers without workspace."""
    if value is None:
        try:
            return os.getcwd() or None
        except OSError:
            return None
    if value == "":
        return None
    return value


def _autoinject_workspace(
    original,
    event_type: str,
    data: dict[str, Any],
    session_id: str = "",
    task_id: Optional[str] = None,
    status: str = "success",
    workspace: Optional[str] = None,
):
    """Wrapper that injects ``data.workspace`` only when caller did not set one."""
    resolved = _resolve_workspace(workspace)
    payload = data if isinstance(data, dict) else {}
    needs_workspace = resolved and (
        "workspace" not in payload
        or payload.get("workspace") in (None, "")
    )
    if needs_workspace:
        if payload is data:
            payload = dict(data)
        payload["workspace"] = resolved
    return original(
        event_type=event_type,
        data=payload,
        session_id=session_id,
        task_id=task_id,
        status=status,
        workspace=workspace,
    )


def enable_workspace_autoinject() -> bool:
    """Patch ``lingyuan.datalog.write_event`` in place. Returns True on first enable."""
    if _PATCH_STATE["enabled"]:
        return False
    try:
        from lingyuan.datalog import write_event
    except ImportError as exc:
        raise ImportError(
            "lingyuan.datalog.write_event unavailable: install lingminopt or add it to PYTHONPATH"
        ) from exc
    _PATCH_STATE["original"] = write_event
    _PATCH_STATE["module"] = write_event.__module__
    import importlib
    module = importlib.import_module(_PATCH_STATE["module"])
    setattr(module, "write_event", _autoinject_workspace)
    _PATCH_STATE["enabled"] = True
    return True


def disable_workspace_autoinject() -> bool:
    """Restore the original write_event. Returns True when a patch was active."""
    if not _PATCH_STATE["enabled"]:
        return False
    import importlib
    module = importlib.import_module(_PATCH_STATE["module"])
    setattr(module, "write_event", _PATCH_STATE["original"])
    _PATCH_STATE["enabled"] = False
    _PATCH_STATE["original"] = None
    _PATCH_STATE["module"] = None
    return True


def is_enabled() -> bool:
    return _PATCH_STATE["enabled"]


def enable_for_repo(repo_root: str) -> bool:
    """chdir to ``repo_root`` then enable autoinject (best-effort cwd pinning)."""
    repo_root = str(repo_root)
    try:
        os.chdir(repo_root)
    except OSError as exc:
        raise FileNotFoundError(f"workspace root does not exist: {repo_root}") from exc
    return enable_workspace_autoinject()


__all__ = [
    "disable_workspace_autoinject",
    "enable_for_repo",
    "enable_workspace_autoinject",
    "is_enabled",
]

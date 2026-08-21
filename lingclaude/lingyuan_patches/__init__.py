"""Patches for lingyuan integration (owner: lingclaude).

A — workspace autoinject for ``lingyuan.datalog.write_event``.
"""

from lingclaude.lingyuan_patches.workspace_session import (
    disable_workspace_autoinject,
    enable_for_repo,
    enable_workspace_autoinject,
    is_enabled,
)

__all__ = [
    "disable_workspace_autoinject",
    "enable_for_repo",
    "enable_workspace_autoinject",
    "is_enabled",
]

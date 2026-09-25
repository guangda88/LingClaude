# lingclaude/engine/state_query.py
"""state_query 自省门面（闭合期②最小切片，2026-09-25）。

对自身状态账本（core/state_store.py 双后端的落盘态）的只读查询门面。
主干冻结区纪律：state_store.py 是主干九件（M3 TRUNK_MODULES），本门面
按「主干之下不添堵」放插片区（engine/），只 import 不改动主干。

出生登记五件事（闭合期③首例实操，p5_birth_criteria）：
1. 职责类型 : routine——常规只读查询门面，无激活语义
2. 待命事件清单 : 无事件驱动（纯被动 API，不订阅缝、不挂 hook）
3. 观察期指标   : 生产/测试引用数（应 >0）；写路径违规数（应恒 0）
4. 豁免规则     : 豁免缝 key 登记——本件无实现可互换需求（查询即读
   JsonFileBackend 落盘态）；若未来需可换查询后端，按铁律 7 以
   {ns}/state_query 域前缀重新入册
5. 缝 key       : 未登记（豁免理由如上，经 08c7a7e 同轮自查确认）

只读纪律（观察期指标 3 的兑现）：本模块不暴露任何写路径——无 save、
无 delete、无 _atomic_write_json 引用；写操作只能回 core/state_store。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from lingclaude.core.state_store import JsonFileBackend

logger = logging.getLogger(__name__)

DEFAULT_ROOT = Path.home() / ".lingclaude" / "state"


class StateQuery:
    """状态账本只读查询门面。

    用法::

        q = StateQuery()                    # 默认根 ~/.lingclaude/state
        q.record_types()                    # ['arch_audit_state', ...]
        q.summary()                         # {record_type: key_count}
        q.record("arch_audit_state", "default")
    """

    def __init__(self, root: Path | None = None) -> None:
        # 复用主干只读原语：load/list_keys；save 原语不暴露
        self._backend = JsonFileBackend(root=root)

    @property
    def root(self) -> Path:
        return self._backend._root

    def record_types(self, root: Path | None = None) -> list[str]:
        """枚举账本根下全部 record_type（= 一级子目录，实扫不传抄）。"""
        base = Path(root) if root is not None else self.root
        if not base.is_dir():
            return []
        return sorted(p.name for p in base.iterdir() if p.is_dir())

    def list_keys(self, record_type: str, root: Path | None = None) -> list[str]:
        """某 record_type 下全部 key（递归，'/' 分隔嵌套）——透传主干原语。"""
        return self._backend.list_keys(record_type, root=root)

    def record(self, record_type: str, key: str,
               root: Path | None = None) -> Optional[dict[str, Any]]:
        """读单条 record payload；不存在返回 None。"""
        return self._backend.load(record_type, key, root=root)

    def summary(self, root: Path | None = None) -> dict[str, int]:
        """全账本概览：{record_type: key 数}（只数不读 payload）。"""
        base = Path(root) if root is not None else self.root
        out: dict[str, int] = {}
        for rt in self.record_types(root=base):
            out[rt] = len(self.list_keys(rt, root=base))
        return out

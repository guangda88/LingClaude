# lingclaude/core/manifest_lock.py
"""P2-10（2026-09-21，全 15 家精读 §3.2 agent-harness 式 manifest 单源 + lock provenance）。

痛点（lc 实证）：27 个插片 manifest 各自维护，org_member 账本 24 个，
测试断言（b4b2034 实录「断言 20 实有 22」、今天 wiring 58→59 断言过期）
反复漂移——漂移靠**审计事后**发现，代价高。agent-harness 的 `.harness/`
单源 + `manifest.lock.json` provenance + 漂移 CI 事前报是正解。

本模块：
- **scan()**：扫 27 个 `plugins/agents/*/manifest.agent.json` +
  24 个 `data/ling_org/org_member/*.json`，算每个文件 SHA256 指纹，
  返回结构化清单（name/hash/required 字段校验）。
- **write_lock()**：把清单 + provenance（扫描时间/提交 hash/计数）写
  `manifest.lock.json`（单一事实源快照，记录「此刻应有的样子」）。
- **check_drift()**：当前扫描 vs lock 对比，报 4 类漂移：
  manifest 新增 / 删除 / 内容变化（hash 变）/ org 账本数量不同步。
  CI 调它事前拦，漂移不再靠审计事后发现。

与 SeamRegistry/check_protocol 的关系：
- check_protocol 校验**单插片协议符合性**（接口面）；
- 本模块校验**全族清单一致性 + 内容 provenance**（清单面）——正交互补。

停层声明（铁律 2 细则 5）：
- 内核 = ManifestLock（scan/write_lock/check_drift 的纯文件系统操作）
- 接缝 = lock 文件路径 + check_drift 返回的 drift 报告
- 实现 = 单实现（JSON lock 文件），预留 git-provenance 扩展位
边界纪律：只读扫描 + 写 lock 文件，不改任何 manifest 内容；
drift 只报不自动修（修是人的决策，CI 报红即可）。
"""
from __future__ import annotations

import hashlib
import json
import time
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["ManifestLock", "DriftReport"]

# lc 仓库根（manifest_lock.py 在 lingclaude/core/，parents[2] = 仓库根）
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = _REPO_ROOT / "lingclaude" / "plugins" / "agents"
_ORG_DIR = _REPO_ROOT / "data" / "ling_org" / "org_member"
_DEFAULT_LOCK = _REPO_ROOT / "manifest.lock.json"

# manifest 必填字段（缺即漂移告警）
REQUIRED_MANIFEST_FIELDS = ("name", "trust_level", "plug_level")


@dataclass
class _Entry:
    path: str
    sha256: str
    name: str
    missing_required: list[str] = field(default_factory=list)


@dataclass
class DriftReport:
    """check_drift 的四类漂移报告（CI 事前拦，非空即漂移）。"""
    added: list[str] = field(default_factory=list)      # 新增 manifest（lock 里没有）
    removed: list[str] = field(default_factory=list)     # 删除 manifest（lock 里有，现在没了）
    changed: list[str] = field(default_factory=list)     # 内容 hash 变化
    org_count_mismatch: bool = False                     # org 账本数与 lock 记录不符
    org_delta: int = 0

    @property
    def clean(self) -> bool:
        return not (self.added or self.removed or self.changed
                    or self.org_count_mismatch)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "added": self.added, "removed": self.removed, "changed": self.changed,
            "org_count_mismatch": self.org_count_mismatch,
            "org_delta": self.org_delta,
        }


class ManifestLock:
    """manifest 单源 + lock provenance（agent-harness 对位）。

    用法（CI / 人工）：
        ml = ManifestLock()
        report = ml.check_drift()          # 有 lock → 对比；无 lock → 全 added
        if not report.clean:
            print(report.to_dict())        # CI 报红
        ml.write_lock()                    # 确认变更合法后更新 lock
    """

    def __init__(
        self,
        repo_root: Path | None = None,
        plugin_dir: Path | None = None,
        org_dir: Path | None = None,
        lock_path: Path | None = None,
    ) -> None:
        self._repo = repo_root or _REPO_ROOT
        self._plugin_dir = plugin_dir or self._repo / "lingclaude" / "plugins" / "agents"
        self._org_dir = org_dir or self._repo / "data" / "ling_org" / "org_member"
        self._lock_path = lock_path or self._repo / "manifest.lock.json"

    # ── 扫描 ──

    def _scan_plugin_manifests(self) -> list[_Entry]:
        entries: list[_Entry] = []
        if not self._plugin_dir.exists():
            return entries
        for mf in sorted(self._plugin_dir.glob("*/manifest.agent.json")):
            rel = str(mf.relative_to(self._repo))
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                entries.append(_Entry(rel, "", "(unparseable)"))
                continue
            sha = hashlib.sha256(mf.read_bytes()).hexdigest()
            missing = [f for f in REQUIRED_MANIFEST_FIELDS if f not in data]
            entries.append(_Entry(
                path=rel, sha256=sha,
                name=data.get("name", mf.parent.name),
                missing_required=missing,
            ))
        return entries

    def _count_org_members(self) -> int:
        if not self._org_dir.exists():
            return 0
        return len(list(self._org_dir.glob("*.json")))

    def scan(self) -> dict[str, Any]:
        """扫当前全部 manifest + org 账本，返回结构化清单。"""
        plugins = self._scan_plugin_manifests()
        org_count = self._count_org_members()
        return {
            "plugins": [
                {"path": e.path, "sha256": e.sha256, "name": e.name,
                 "missing_required": e.missing_required}
                for e in plugins
            ],
            "org_member_count": org_count,
        }

    # ── lock 读写 ──

    def _git_head(self) -> str:
        try:
            r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self._repo),
                               capture_output=True, text=True, timeout=10)
            return r.stdout.strip() if r.returncode == 0 else "no-git"
        except (OSError, subprocess.SubprocessError):
            return "no-git"

    def write_lock(self) -> Path:
        """把当前扫描 + provenance 写 lock 文件（单一事实源快照）。"""
        snap = self.scan()
        lock = {
            "schema": "lingclaude/manifest-lock/v1",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "git_head": self._git_head(),
            "plugin_count": len(snap["plugins"]),
            "org_member_count": snap["org_member_count"],
            "plugins": snap["plugins"],
        }
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path.write_text(
            json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
        logger = __import__("logging").getLogger(__name__)
        logger.info("manifest lock 已写: %s（%d manifest / %d org）",
                    self._lock_path.name, lock["plugin_count"], lock["org_member_count"])
        return self._lock_path

    def _read_lock(self) -> dict[str, Any] | None:
        if not self._lock_path.exists():
            return None
        try:
            return json.loads(self._lock_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    # ── 漂移检测（CI 事前拦） ──

    def check_drift(self) -> DriftReport:
        """当前扫描 vs lock 对比，报四类漂移。无 lock 时全判 added（首次建）。"""
        lock = self._read_lock()
        if lock is None:
            snap = self.scan()
            rep = DriftReport()
            rep.added = [p["name"] for p in snap["plugins"]]
            rep.org_count_mismatch = False  # 无 lock 无从比对
            return rep

        snap = self.scan()
        lock_by_path = {p["path"]: p for p in lock.get("plugins", [])}
        cur_by_path = {p["path"]: p for p in snap["plugins"]}

        rep = DriftReport()
        # 统一用 path 报漂移（比 name 稳定——JSON 损坏时 name 会是占位符，
        # 但 path 始终精确，CI 据此定位到具体文件）。
        rep.added = [cur_by_path[p]["path"] for p in cur_by_path
                     if p not in lock_by_path]
        rep.removed = [lock_by_path[p]["path"] for p in lock_by_path
                       if p not in cur_by_path]
        rep.changed = [
            cur_by_path[p]["path"]
            for p in cur_by_path
            if p in lock_by_path and cur_by_path[p]["sha256"] != lock_by_path[p]["sha256"]
        ]
        # 必填字段缺失也算漂移（schema 契约），按 path 报
        rep.changed += [
            p["path"] for p in snap["plugins"]
            if p["missing_required"] and p["path"] not in rep.changed
        ]
        # org 账本数与 lock 记录不符
        lock_org = lock.get("org_member_count", 0)
        if snap["org_member_count"] != lock_org:
            rep.org_count_mismatch = True
            rep.org_delta = snap["org_member_count"] - lock_org
        return rep

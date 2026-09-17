"""OS 资源探针插片（os/resource，Phase C 第四层预研）。

铁律锚点（每行代码过法）：
- 铁律 1：本插片全在 plugins/agents/os_resource/，core/ 仅增 RESOURCE 枚举
  （走铁律 1 接缝协议，M3 依赖方向 + M5 截肢双绿终审）；
- 铁律 3/J4：每次 probe 记 resource_probe record（data/absent/probe_at），可 query；
- 铁律 5/6：trust_level=T3 + plug_level=L3 双声明（N2 守卫消费，T3 只观测不判违规）；
- 铁律 7：缝 key 带域前缀 os/（N3 守卫消费）；
- 铁律 8 隔离故障域：单探针故障圈死（gpu 探针 absent 不影响 cpu/mem/disk），
  故障域=探针名（域前缀 os/resource 即故障域前缀，圈死最细粒度到单探针）；
- N4 缺席查（硬件级实例）：数据源消失 → probe 返 None → record absent（不假活），
  域内 query 全返 absent、域外不受影响。

复用资产：
- ResourceProbe 协议（core/seam.py，M5 截肢主干照跑）；
- nvidia-smi/free/df/loadavg 四数据源（OS 黑盒，T3 只观测）；
- agent_lingxi 三件套范式（manifest + plugin + 测试）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from lingclaude.core.state_store import StateStore

MANIFEST = Path(__file__).parent / "manifest.agent.json"
PROBE_TIMEOUT_S = 5  # 单探针超时（nvidia-smi 冷启动慢，给 5s）


class ResourceProbePlugin:
    """ResourceProbe 协议实现（四探针：gpu/cpu/mem/disk）。

    每个探针独立圈死（铁律 8 故障域=探针名）：
    - gpu：nvidia-smi 不可用 → probe 返 None → absent（T3 只观测，不判 OS 违规）；
    - cpu：/proc/loadavg 不可读 → 该探针 absent，不影响其他探针；
    - mem：/proc/meminfo 不可读 → 该探针 absent；
    - disk：os.statvfs 失败 → 该探针 absent。
    """

    name = "os/resource"  # 铁律 7：域前缀缝 key

    def __init__(self, store: StateStore | None = None, probe: str | None = None) -> None:
        self._manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self._probe = probe  # 指定单探针（"gpu"/"cpu"/"mem"/"disk"），None=全四探针
        self._store = store or StateStore(backend="json",
                                          root=Path(__file__).parents[4] / "data" / "resource_probes")
        self._probe_failures: dict[str, int] = {}

    # ── 探针列表（list_keys 原语）─────────────────────────────────────
    def list_probes(self) -> list[dict]:
        """列出四探针 + 实时状态（absent/healthy，T3 只观测）。"""
        probes = ["gpu", "cpu", "mem", "disk"]
        out = []
        for p in probes:
            data = self.probe_one(p)
            absent = data is None
            out.append({"name": p, "healthy": not absent, "absent": absent,
                        "data_preview": str(data)[:200] if data else None})
        return out

    # ── ResourceProbe 协议两动作（probe/status）───────────────────────
    def probe(self) -> dict[str, Any] | None:
        """采一次（按 self._probe 限定的单探针，或全四探针）。

        返 None = 数据源消失（absent，不假活）；返 dict = 数据 + probe_at。
        全程 record 化（J4，resource_probe type）。
        """
        run_id = f"probe:{self._probe or 'all'}:{int(time.time() * 1000)}"
        if self._probe:
            data = self.probe_one(self._probe)
            self._record(run_id, "absent" if data is None else "ok",
                         {"probe": self._probe, "data": data})
            return data
        # 全四探针（单探针圈死：某探针 absent 不影响其他）
        result: dict[str, Any] = {}
        for p in ("gpu", "cpu", "mem", "disk"):
            result[p] = self.probe_one(p)
        self._record(run_id, "ok", {"probes": "gpu,cpu,mem,disk", "data": result})
        return result

    def status(self) -> dict[str, Any]:
        """健康状态（铁律 8：单探针 absent 不扩散，N4 缺席查）。"""
        lines = self.list_probes()
        absent = [l["name"] for l in lines if l["absent"]]
        healthy = [l["name"] for l in lines if l["healthy"]]
        return {
            "name": self.name,
            "probe": self._probe or "all",
            "probes_total": len(lines),
            "healthy": healthy,
            "absent": absent,  # T3 只观测：absent 是物理事实，不判违规
        }

    # ── 内部：单探针采集 + record 化 ─────────────────────────────────
    def probe_one(self, probe_name: str) -> dict[str, Any] | None:
        """采单探针 → 数据 dict；数据源消失 → None（absent，不假活）。

        隔离故障域：单探针异常只影响自身，不抛到调用方（T3 只观测，不崩主干）。
        """
        dispatch = {
            "gpu": self._probe_gpu,
            "cpu": self._probe_cpu,
            "mem": self._probe_mem,
            "disk": self._probe_disk,
        }
        fn = dispatch.get(probe_name)
        if fn is None:
            return None
        try:
            return fn()
        except (OSError, subprocess.SubprocessError, KeyError) as e:
            # T3 只观测：异常 → absent（不假活，不抛），单探针圈死
            self._probe_failures[probe_name] = self._probe_failures.get(probe_name, 0) + 1
            return None

    def _probe_gpu(self) -> dict[str, Any] | None:
        """nvidia-smi → GPU 列表；无 nvidia-smi 或命令失败 → None（absent）。"""
        if not shutil.which("nvidia-smi"):
            return None
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                               "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=PROBE_TIMEOUT_S)
            if r.returncode != 0:
                return None
            gpus = []
            for line in r.stdout.strip().splitlines():
                parts = [x.strip() for x in line.split(",")]
                if len(parts) >= 5:
                    gpus.append({"index": int(parts[0]), "name": parts[1],
                                 "mem_used_mib": int(parts[2]), "mem_total_mib": int(parts[3]),
                                 "util_pct": int(parts[4])})
            return {"gpus": gpus, "count": len(gpus)}
        except (subprocess.TimeoutExpired, ValueError):
            return None

    def _probe_cpu(self) -> dict[str, Any] | None:
        """/proc/loadavg → load1/5/15 + CPU 核数。"""
        try:
            with open("/proc/loadavg") as f:
                parts = f.read().split()
            return {"load1": float(parts[0]), "load5": float(parts[1]),
                    "load15": float(parts[2]), "cpus": os.cpu_count()}
        except (OSError, IndexError, ValueError):
            return None

    def _probe_mem(self) -> dict[str, Any] | None:
        """/proc/meminfo → 内存总量/可用（kB）。"""
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    key, _, rest = line.partition(":")
                    info[key.strip()] = int(rest.strip().split()[0])  # 单位 kB
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", info.get("MemFree", 0))
            return {"mem_total_kb": total, "mem_available_kb": avail,
                    "mem_used_pct": round((total - avail) / total * 100, 1) if total else 0}
        except (OSError, IndexError, ValueError, ZeroDivisionError):
            return None

    def _probe_disk(self) -> dict[str, Any] | None:
        """os.statvfs("/") → 根分区磁盘用量。"""
        try:
            st = os.statvfs("/")
            total = st.f_blocks * st.f_frsize
            free = st.f_bfree * st.f_frsize
            return {"total_bytes": total, "free_bytes": free,
                    "used_pct": round((total - free) / total * 100, 1) if total else 0}
        except (OSError, ZeroDivisionError):
            return None

    def _record(self, run_id: str, state: str, extra: dict) -> None:
        """J4：状态变迁全入账（resource_probe type，StateStore）。

        T3 只观测：state=absent 是物理事实（数据源消失），不判 OS 违规。
        """
        rec = self._store.load("resource_probe", run_id) or {}
        rec.update({"plugin": self.name, "state": state,
                    "transited_at": time.time(), **extra})
        self._store.save("resource_probe", run_id, rec)


def register(registry) -> None:
    """插件入口：SeamRegistry.register(SeamType.RESOURCE, "os/resource", plugin)。

    M5 截肢：unregister 后主干照跑（L3 缺席裸奔，主干零依赖资源探针功能）。
    四探针实例（gpu/cpu/mem/disk 各一）经 probe 参数区分，按需挂载。
    """
    from lingclaude.core.seam import SeamType  # 延迟 import，避免循环
    registry.register(SeamType.RESOURCE, ResourceProbePlugin.name, ResourceProbePlugin())

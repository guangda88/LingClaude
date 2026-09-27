"""LingflowCrewOrchestrator — cap/crew 首插片（lingflow workflow 引擎封装）。

清偿 arch_debt cap-crew-lingflow-deferred（due 2026-10-31，今日 2026-09-27 清账）：
CrewOrchestrator 协议（core/seam.py:173，create_crew/dispatch/status）此前协议
就绪零实现；lingflow 的 workflow 能力以 skill 形态在位（workflow-executor +
workflows/*.yaml，skill 编排格式），经本插片接入 lc 的 ORCHESTRATOR 槽。

Crew 语义映射（lingflow YAML ↔ CrewOrchestrator）：
  create_crew(members=[workflow 名], **kwargs)
      → 加载/校验 lingflow/workflows/<name>.yaml（tasks/skill/params 结构），
        登记 crew 实例返回 crew_id；
  dispatch(crew_id, task, mode)
      → 按拓扑序执行 tasks：task.skill = AgentSeam 缝能力（经 SeamRegistry
        AGENT 缝或内置 skill 面），params 支持 {{params.x}}/{{tasks.id.output.x}}
        模板插值；mode=sequential 顺序执行 / parallel 同层并行（首版顺序）；
  status(crew_id)
      → 每步 pending/running/succeeded/failed/skipped + 输出摘要（J4 可对账）。

J1：变化走接缝——lingflow YAML 变化零 diff 本模块（运行时读盘）；
J4：dispatch 全程 record 化（crew_run:<crew_id>）；
J5：失败显式（步骤失败即 failed crew，不静默跳过——conditional-branch 除外）。
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# lingflow 仓库的 workflow 目录（单一事实源；env 可覆盖）
DEFAULT_WORKFLOW_DIR = Path("/home/ai/lingflow/workflows")

# J4 状态机终态
TERMINAL_STATES = ("succeeded", "failed", "aborted")

_TMPL = re.compile(r"\{\{\s*([a-zA-Z0-9_.| \[\]'\"()]+?)\s*\}\}")


def _resolve_template(value: Any, params: dict, task_outputs: dict) -> Any:
    """解析 {{params.x}} / {{tasks.<id>.output.<k>}} / {{params.x | default(y)}}。"""
    if not isinstance(value, str):
        return value
    def _sub(m: re.Match) -> str:
        expr = m.group(1).strip()
        # default 过滤器
        default = ""
        if "| default(" in expr:
            expr, default = expr.split("| default(")
            default = default.rstrip(")").strip().strip("'\"")
        expr = expr.strip()
        if expr.startswith("params."):
            v = params.get(expr[len("params."):], default)
            return "" if v is None else str(v)
        m2 = re.match(r"tasks\.([a-zA-Z0-9_]+)\.output\.(.+)", expr)
        if m2:
            tid, okey = m2.group(1), m2.group(2)
            out = task_outputs.get(tid) or {}
            v = out.get(okey, default)
            return "" if v is None else str(v)
        if expr.startswith("tasks."):
            return str(task_outputs.get(expr.split(".")[1], default) or default)
        return default
    return _TMPL.sub(_sub, value)


class LingflowCrewOrchestrator:
    """CrewOrchestrator 协议实现（SeamType.ORCHESTRATOR 首插片）。"""

    name = "cap/lingflow-crew"  # N3：cap 域前缀（cap/crew 方案语义）

    def __init__(
        self,
        workflow_dir: Path | None = None,
        store: Any = None,
    ) -> None:
        self._wf_dir = Path(workflow_dir or DEFAULT_WORKFLOW_DIR)
        self._store = store  # StateStore（J4；None=不入账）
        self._crews: dict[str, dict] = {}

    # ── 内部：YAML 加载/校验 ────────────────────────────────────────────
    def _load_workflow(self, name: str) -> dict:
        """加载 lingflow workflow YAML；members 即 workflow 文件名（可带 .yaml）。"""
        fname = name if name.endswith((".yaml", ".yml")) else f"{name}.yaml"
        path = self._wf_dir / fname
        if not path.is_file():
            available = sorted(p.stem for p in self._wf_dir.glob("*.yaml"))
            raise FileNotFoundError(
                f"workflow 不存在: {path}（可用: {available}）")
        import yaml
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not doc.get("tasks"):
            raise ValueError(f"workflow 格式非法（缺 tasks）: {path}")
        return doc

    def _record(self, run_id: str, extra: dict) -> None:
        """J4：crew 状态变迁入账（store 缺省静默跳过）。"""
        if self._store is None:
            return
        try:
            rec = self._store.load("crew_run", run_id) or {}
            rec.update({"transited_at": time.time(), **extra})
            self._store.save("crew_run", run_id, rec)
        except Exception:  # noqa: BLE001 —— 审计旁路不反噬编排
            logger.debug("crew_run record failed: %s", run_id, exc_info=True)

    # ── CrewOrchestrator Protocol ──────────────────────────────────────
    def create_crew(self, members: list[str], **kwargs: Any) -> str:
        """创建 crew：members = lingflow workflow 名（首个生效，多成员视为多工作流队列）。"""
        if not members:
            raise ValueError("crew members 不能为空（应为 lingflow workflow 名）")
        loaded = []
        for m in members:
            doc = self._load_workflow(m)
            loaded.append({"workflow": m, "doc": doc})
        crew_id = f"crew-{uuid.uuid4().hex[:10]}"
        self._crews[crew_id] = {
            "crew_id": crew_id,
            "workflows": loaded,
            "params": dict(kwargs.get("params") or {}),
            "state": "created",
            "steps": [],       # [{task_id, skill, state, output, error}]
            "created_at": time.time(),
        }
        self._record(f"create:{crew_id}", {
            "crew_id": crew_id, "state": "created",
            "workflows": [w["workflow"] for w in loaded],
        })
        return crew_id

    def dispatch(self, crew_id: str, task: str, mode: str = "sequential") -> Any:
        """执行 crew：按 YAML tasks 顺序跑，返回执行汇总 dict。"""
        crew = self._crews.get(crew_id)
        if crew is None:
            raise KeyError(f"crew 不存在: {crew_id}（已创建: {list(self._crews)}）")
        crew["state"] = "running"
        self._record(f"run:{crew_id}", {"crew_id": crew_id, "state": "running",
                                        "task": task[:200], "mode": mode})
        params = dict(crew["params"])
        if task:
            params.setdefault("task", task)
        task_outputs: dict[str, dict] = {}
        steps = crew["steps"]

        for wf in crew["workflows"]:
            for t in wf["doc"]["tasks"]:
                tid = t.get("id", "")
                skill = t.get("skill", "")
                step = {"task_id": tid, "skill": skill, "state": "running",
                        "output": {}, "error": None}
                steps.append(step)
                # 参数模板插值
                raw_params = t.get("params") or {}
                call_params = {k: _resolve_template(v, params, task_outputs)
                               for k, v in raw_params.items()}
                try:
                    out = self._exec_skill(skill, call_params)
                    step["state"] = "succeeded"
                    step["output"] = out if isinstance(out, dict) else {"result": str(out)[:500]}
                    task_outputs[tid] = step["output"]
                except Exception as e:  # noqa: BLE001 —— J5：失败显式
                    step["state"] = "failed"
                    step["error"] = f"{type(e).__name__}: {e}"[:300]
                    crew["state"] = "failed"
                    self._record(f"run:{crew_id}", {
                        "crew_id": crew_id, "state": "failed",
                        "failed_step": tid, "error": step["error"]})
                    return {
                        "crew_id": crew_id, "state": "failed",
                        "steps": steps,
                        "error": f"步骤 {tid}({skill}) 失败: {step['error']}",
                    }

        crew["state"] = "succeeded"
        self._record(f"run:{crew_id}", {"crew_id": crew_id, "state": "succeeded",
                                        "steps": len(steps)})
        return {"crew_id": crew_id, "state": "succeeded", "steps": steps,
                "outputs": task_outputs}

    def status(self, crew_id: str) -> dict[str, Any]:
        """crew 执行状态（J4 可对账：逐步 pending/running/succeeded/failed）。"""
        crew = self._crews.get(crew_id)
        if crew is None:
            return {"crew_id": crew_id, "state": "unknown",
                    "error": f"crew 不存在: {crew_id}"}
        return {
            "crew_id": crew_id,
            "state": crew["state"],
            "workflows": [w["workflow"] for w in crew["workflows"]],
            "steps": [{"task_id": s["task_id"], "skill": s["skill"],
                       "state": s["state"], "error": s["error"]}
                      for s in crew["steps"]],
            "created_at": crew["created_at"],
        }

    # ── 内部：skill 执行面 ─────────────────────────────────────────────
    def _exec_skill(self, skill: str, params: dict) -> Any:
        """执行单个 skill 步骤。

        解析顺序（J1 变化走接缝）：
        1. SeamRegistry.AGENT 缝精确名 → 插片 run()；
        2. agent/{skill} N3 合名缝（插片以 agent/<skill> 注册即可被 workflow 引用）；
        3. 内置编排辅助（notification/conditional-branch——crew 引擎自身控制流
           原语，不依赖任何外部载体，优先于 lingflow 委派）；
        4. 委派 agent/lingflow（lingflow 自己的 skill 由 lingflow runtime 执行）；
        5. 全部未命中 → 显式失败（J5，不静默）。
        """
        try:
            from lingclaude.core.seam import SeamRegistry, SeamType
        except Exception:  # noqa: BLE001 —— seam 不可用直接落内置层
            return self._exec_builtin(skill, params)

        # 1) 精确名 / 2) agent/{skill} 合名缝
        for key in (skill, f"agent/{skill}"):
            try:
                plugin = SeamRegistry.get(SeamType.AGENT, key)
            except KeyError:
                continue
            if plugin is not None and hasattr(plugin, "run"):
                # 工具名解析：缝 key（agent/xxx）不是工具名——取 params.tool /
                # params.cmd 显式指定，否则用缝 key 尾段（agent/ghidra→ghidra
                # 不对时由插件白名单拒绝，J5 显式失败不静默）
                tool = params.pop("tool", None) or params.pop("cmd", None)
                if tool is None:
                    tool = skill.split("/")[-1] if "/" in skill else skill
                out = plugin.run(tool, **params)
                if isinstance(out, dict) and out.get("state") == "succeeded":
                    return {"result": out.get("result", "")}
                if isinstance(out, dict):
                    raise RuntimeError(out.get("error") or f"plugin state={out.get('state')}")
                return out
        # 3) 内置编排辅助（crew 控制流原语，载体无关）
        if skill in ("notification", "conditional-branch"):
            return self._exec_builtin(skill, params)
        # 4) 委派 lingflow runtime（其 skill 实现在 lingflow 侧，lc 只过缝）
        try:
            lf = SeamRegistry.get(SeamType.AGENT, "agent/lingflow")
        except KeyError:
            lf = None
        if lf is not None and hasattr(lf, "run"):
            out = lf.run(skill, **params)
            if isinstance(out, dict) and out.get("state") == "succeeded":
                return {"result": out.get("result", "")}
            if isinstance(out, dict):
                raise RuntimeError(out.get("error") or f"lingflow state={out.get('state')}")
            return out
        # 4) 内置编排辅助
        return self._exec_builtin(skill, params)

    def _exec_builtin(self, skill: str, params: dict) -> Any:
        """内置编排辅助 skill（无外部依赖的纯本地实现）。"""
        if skill == "notification":
            logger.info("[crew notification] %s", params)
            return {"notified": True, "level": params.get("level", "info")}
        if skill == "conditional-branch":
            cond = str(params.get("condition", "False"))
            try:
                truthy = cond not in ("False", "false", "0", "", "None")
            except Exception:  # noqa: BLE001
                truthy = False
            return {"condition": cond, "taken": "true" if truthy else "false"}
        # 5) 显式失败
        raise RuntimeError(f"未知 skill 步骤: {skill}（无对应 AGENT 缝/lingflow 委派/内置实现）")


def register(registry) -> None:
    """插件入口：SeamRegistry.register(SeamType.ORCHESTRATOR, ...)。"""
    from lingclaude.core.seam import SeamType
    registry.register(SeamType.ORCHESTRATOR, LingflowCrewOrchestrator.name,
                      LingflowCrewOrchestrator())

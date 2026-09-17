"""灵元推理栈插片（cap/infer）—— 封灵元栈 llama-server 常驻端点为 lc 插片。

Phase A 还债（2026-09-18，CAPABILITY_PLUGIN_PLAN_L3.md §六 问 2）：
- 灵元栈 engine.py 已升级 llama-server 常驻插片（MEMORY 记载：OpenAI HTTP 调用、
  _health_check 不杀进程、13 模型端口由 plugins.yaml 驱动），当前旁路于 lc 主干
  （直接 HTTP 调用，未走 SeamRegistry/record 化）。本插片把它封成 cap/infer：
  - manifest：transport.kind=http，base_url 走 plugins.yaml 配置驱动，
    health_probe=GET /v1/models；
  - record 化：每次推理请求记 infer_run record（model/endpoint/latency/exit），J4 语义；
  - 缺席语义（铁律 8 隔离故障域）：单端口故障 → 该端口 absent，
    不扩散到其他端口（域前缀 cap/infer 即故障域，圈死最细粒度）；
  - on_demand 语义（用户 2026-09-18 裁定）：未拉起端口 = absent（不是 dead），
    engine.py 可按需拉起，manifest 不锁定 13 端口，由 plugins.yaml 驱动按需加配。

铁律锚点（每行代码过法）：
- 铁律 1：本插片全在 plugins/agents/cap_infer/，core/ 零 diff；
- 铁律 3/J4：infer 全程 record 化（infer_run type，StateStore）；
- 铁律 5/6：trust_level=T2 + plug_level=L1 双声明（N2 守卫消费）；
- 铁律 7：缝 key 带域前缀 cap/infer（N3 守卫消费）；
- 铁律 8：单端口故障圈死（缺席=absent，不假活，N4 缺席查）；
- 候选铁律 8 on_demand：未拉起 ≠ 缺陷，是"可按需拉起"的合法状态。

复用资产：
- ProviderPlugin 协议（core/seam.py，L1 替换：可换其他 OpenAI 兼容端点）；
- 灵元栈 engine.py（~/lingminopt/lingyuan/engine.py）按需拉起语义；
- agent_lingxi 三件套范式（manifest + plugin + 测试）。
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

import yaml  # type: ignore

from lingclaude.core.state_store import StateStore

MANIFEST = Path(__file__).parent / "manifest.agent.json"

# on_demand 语义（用户 2026-09-18 裁定）：未拉起端口不算缺陷，engine.py 可按需拉起
ABSENT_THRESHOLD = 2  # 连续 N 次探活失败即故障 absent（N4 缺席查）


class LingyuanInferPlugin:
    """ProviderPlugin 协议实现（灵元推理栈，OpenAI 兼容 HTTP，配置驱动）。

    复用 SeamType.PROVIDER（灵元栈本质=模型 provider，OpenAI 兼容端点）：
    - create(config)：config={name, model, prompt, ...} → 取 plugins.yaml 中
      该 name 的 url/model 字段，发起一次 /v1/chat/completions 调用；
    - 缺席语义：单端口探活失败 → record absent（N4 缺席查，不假活）；
    - on_demand：未拉起端口 = absent（不是 dead），engine.py 可按需拉起。
    """

    name = "cap/infer"  # 铁律 7：域前缀缝 key

    def __init__(self, store: StateStore | None = None,
                 plugins_yaml: str | None = None) -> None:
        self._manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self._plugins_yaml = plugins_yaml or self._manifest["transport"]["config_source"]
        self._store = store or StateStore(backend="json",
                                          root=Path(__file__).parents[4] / "data" / "infer_runs")
        self._probe_failures: dict[str, int] = {}  # endpoint -> 连续失败次数
        self._load_models()

    # ── 配置驱动：读 plugins.yaml（灵元栈引擎仓单一事实源）──────────────
    def _load_models(self) -> None:
        """读 plugins.yaml，提取本机 enabled=true 的 llama-server 端口。

        13 端口全在语义：不降级、不锁死 13 端口，按 plugins.yaml 当前配置驱动。
        未拉起端口（探活失败）标 on_demand=true（不记 debt，engine.py 可按需拉起）。
        """
        try:
            cfg = yaml.safe_load(Path(self._plugins_yaml).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            # plugins.yaml 不可用 → 全端口 absent（不假活，N4 缺席查）
            self._endpoints: dict[str, dict] = {}
            self._plugins_yaml_error = str(e)
            return
        self._plugins_yaml_error = None
        self._endpoints = {}
        for p in cfg.get("plugins", []):
            url = p.get("url", "")
            # 只收本机 127.0.0.1 端点（ai01/10.0.0.12 等远端端点不进 lc 主干，隔离故障域）
            if not url.startswith("http://127.0.0.1"):
                continue
            if p.get("engine") != "llama-server":
                continue
            if not p.get("enabled", False):
                continue
            # key：去重（同名模型可能多端口，以 name 为 key）
            key = p.get("name") or url
            self._endpoints[key] = {
                "name": key,
                "url": url,
                "model": p.get("model", key),
                "thinking": p.get("thinking", False),
                "on_demand": True,  # 用户裁定：全部端口 on_demand 语义（未拉起=absent 不是 dead）
            }

    def list_models(self) -> list[dict]:
        """列出本机全部 enabled 端口 + 实时探活状态（list_keys 原语）。

        返回 [{name, url, model, healthy, absent, on_demand, probe_failures}, ...]。
        缺席语义：未拉起=absent（on_demand=true 标记），不假活。
        """
        out = []
        for key, ep in self._endpoints.items():
            healthy = self._probe(ep["url"])
            failures = self._probe_failures.get(key, 0)
            absent = failures >= ABSENT_THRESHOLD
            out.append({
                "name": key,
                "url": ep["url"],
                "model": ep["model"],
                "healthy": healthy,
                "absent": absent,
                "on_demand": ep.get("on_demand", True),
                "probe_failures": failures,
            })
        return out

    # ── ProviderPlugin 协议三动作（create/infer/abort）─────────────────
    def create(self, config: Any = None) -> dict:
        """ProviderPlugin.create：发起一次推理调用，全程 record 化（J4）。

        config 形态：{"name": "qwen2.5-7b@8106", "prompt": "...", "max_tokens": 256}
        或 {"url": "...", "model": "...", "prompt": "..."}。

        缺席语义：单端口探活失败 → record absent（N4 缺席查，不假活）；
        不扩散到其他端口（隔离故障域，域前缀 cap/infer 即故障域）。
        """
        config = config or {}
        run_id = f"infer:{int(time.time() * 1000)}"
        ep_name = config.get("name") or self._resolve_endpoint(config)
        ep = self._endpoints.get(ep_name, {})
        self._record(run_id, "running", {"endpoint": ep_name, "model": ep.get("model")})

        start = time.time()
        try:
            result = self._call_openai(config, ep)
            latency = time.time() - start
            self._record(run_id, "succeeded", {
                "latency_s": round(latency, 3),
                "result_preview": str(result)[:500],
            })
            return {"run_id": run_id, "state": "succeeded", "result": result,
                    "latency_s": round(latency, 3)}
        except urllib.error.URLError as e:
            latency = time.time() - start
            self._record(run_id, "absent", {
                "reason": "endpoint_unreachable",
                "error": str(e)[:200],
                "latency_s": round(latency, 3),
                "on_demand": True,  # 用户裁定：未拉起=absent（不是 dead），可按需拉起
            })
            # N4 缺席查：单端口故障累计（连续 N 次 → 该端口 absent）
            if ep_name:
                self._probe_failures[ep_name] = self._probe_failures.get(ep_name, 0) + 1
            return {"run_id": run_id, "state": "absent",
                    "error": str(e)[:200], "on_demand": True}
        except Exception as e:  # noqa: BLE001 —— 失败也必须入账（J4）
            latency = time.time() - start
            self._record(run_id, "failed", {
                "error": str(e)[:300],
                "latency_s": round(latency, 3),
            })
            return {"run_id": run_id, "state": "failed", "error": str(e)}

    def abort(self, run_id: str) -> bool:
        """ProviderPlugin.abort（J2 可拔插协议面）：HTTP 调用不可中断，
        仅 record 化 aborted 语义（不杀引擎进程，_health_check 不杀进程原则）。"""
        rec = self._store.load("infer_run", run_id) or {}
        if rec.get("state") not in TERMINAL_STATES:
            self._record(run_id, "aborted", {"note": "HTTP 调用不可中断，record 化 aborted"})
            return True
        return False

    def status(self) -> dict:
        """健康状态（铁律 8：单端口探活失败累计 → absent，不假活）。"""
        lines = self.list_models()
        absent = [l["name"] for l in lines if l["absent"]]
        healthy = [l["name"] for l in lines if l["healthy"] and not l["absent"]]
        return {
            "name": self.name,
            "endpoints_total": len(lines),
            "healthy": healthy,
            "absent": absent,
            "on_demand": True,  # 用户裁定：未拉起=absent（不是 dead）
            "plugins_yaml_error": self._plugins_yaml_error,
        }

    # ── 内部：OpenAI 兼容调用 + record 化 ─────────────────────────────
    def _resolve_endpoint(self, config: dict) -> str:
        """按 config 解析端点 key：name 直取；url 反查；都无则取首个。"""
        if config.get("name"):
            return config["name"]
        url = config.get("url")
        if url:
            for key, ep in self._endpoints.items():
                if ep["url"] == url:
                    return key
        return next(iter(self._endpoints), "")

    def _call_openai(self, config: dict, ep: dict) -> dict:
        """OpenAI 兼容 /v1/chat/completions 调用（urllib，不引第三方依赖）。

        thinking 模型（holo-3.1-9b 等）：输出全在 reasoning_content，content 为空，
        透传 response 让消费方自行决定（MEMORY 记载 2026-08-04 实测）。
        """
        base_url = ep.get("url") or config.get("url")
        if not base_url:
            raise ValueError("cap/infer: 无可用 base_url（plugins.yaml 配置缺失或端点未解析）")
        payload = {
            "model": config.get("model") or ep.get("model"),
            "messages": [
                {"role": "system", "content": config.get("system", "You are a helpful assistant.")},
                {"role": "user", "content": config.get("prompt", "")},
            ],
            "max_tokens": config.get("max_tokens", 256),
            "temperature": config.get("temperature", 0.7),
        }
        # 端点 /v1 结尾 → 拼 /chat/completions；端点已是完整路径 → 直接用
        base = base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        url = base + "/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = self._manifest["transport"].get("call_timeout_s", 600)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _probe(self, url: str) -> bool:
        """健康探针：GET /v1/models 是否 200（轻量，不执行业务）。

        缺席语义：探活失败不算立即 absent（on_demand 语义），
        累计到 ABSENT_THRESHOLD 才故障 absent（N4 缺席查）。
        """
        try:
            models_url = url.rstrip("/")
            if not models_url.endswith("/models"):
                models_url = models_url + "/models"
            req = urllib.request.Request(models_url, method="GET")
            timeout = self._manifest["health_probe"]["timeout_s"]
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, KeyError):
            return False

    def _record(self, run_id: str, state: str, extra: dict) -> None:
        """J4：状态变迁全入账（infer_run type，StateStore）。

        on_demand 字段（用户裁定）：state=absent 且 on_demand=True 时，
        该 record 是"未拉起=可按需拉起"的合法状态，不是缺陷 debt。
        """
        rec = self._store.load("infer_run", run_id) or {}
        rec.update({"plugin": self.name, "state": state,
                    "transited_at": time.time(), **extra})
        self._store.save("infer_run", run_id, rec)


# 铁律 3/J4 run 状态机终态集合（超过即视为 record 语义破坏）。
# 注意：failed 不在终态——失败的任务可被用户 abort（标记"已处理"），
# 与 agent_lingxi 的 TERMINAL_STATES 差异在此（HTTP 推理失败常见，需留重入口）。
TERMINAL_STATES = ("succeeded", "absent", "aborted", "timeout")


def register(registry) -> None:
    """插件入口：SeamRegistry.register(SeamType.PROVIDER, "cap/infer", plugin)。

    复用 SeamType.PROVIDER（灵元栈本质=模型 provider，OpenAI 兼容端点）：
    L1 替换（unregister 后 lc 主干照跑，可换其他 OpenAI 兼容端点）。
    """
    from lingclaude.core.seam import SeamType  # 延迟 import，避免循环
    registry.register(SeamType.PROVIDER, LingyuanInferPlugin.name, LingyuanInferPlugin())

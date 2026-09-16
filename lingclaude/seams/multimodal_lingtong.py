"""灵通问道多模态任务插片 — 把 lingtongask MCP 能力包装成 MultimodalTask 协议。

Phase 3 (2026-09-15): 灵通问道已自带标准 MCP server（fastmcp），
lingclaude 侧 mcp_proxy._MODULE_IMPORTS 已注册 "lingtongask" → "mcp_server"。
本模块把其 3 个核心工具（analyze_emotion / synthesize_speech / list_voices）
包装为 SeamType.MULTIMODAL 插片，注册进 SeamRegistry，供编排层统一调度。

设计纪律：
- fail-closed：lingtongask 模块不可导入 → 插片注册失败但不崩溃（is_available=False）
- 协议合规：满足 core/seam.py MultimodalTask Protocol
  （name / task_type() / input_schema() / execute(**kwargs)）
- 双通道：进程内模块导入（默认）；HTTP 端点走 MCPHttpClient（可选，env 可配）
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from lingclaude.core.seam import MultimodalTask, SeamRegistry, SeamType

logger = logging.getLogger("lingclaude.seams.multimodal_lingtong")

# lingtongask 仓库根（cross_repo_seam 单一事实来源）
_LINGTONGASK_ROOT = Path(
    os.environ.get("LINGTONGASK_PATH", "/home/ai/lingtongask")
)

# HTTP 端点（可选，env 可覆盖；未配置则纯模块导入）
_HTTP_URL = os.environ.get("LINGTONGASK_MCP_URL", "")


class LingTongMultimodalTask:
    """灵通问道多模态任务插片（MultimodalTask Protocol 实现）。"""

    name = "lingtong_multimodal"

    _SCHEMAS: dict[str, dict[str, Any]] = {
        "analyze_emotion": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要分析的中文文本"},
            },
            "required": ["text"],
        },
        "synthesize_speech": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要合成的中文文本 (≤5000 字)"},
                "voice": {"type": "string", "description": "说话人 (host/guest/voice_id)"},
                "speed": {"type": "number", "description": "语速倍率 0.5-2.0"},
                "pitch": {"type": "number", "description": "音高倍率 0.5-2.0"},
            },
            "required": ["text"],
        },
        "list_voices": {
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "description": "按 prefix 过滤 voice_id"},
            },
        },
    }

    def __init__(self) -> None:
        self._module: Any = None
        self._available: bool = False
        self._load_module()

    def _load_module(self) -> None:
        """尝试加载 lingtongask src 包（进程内通道，跳过 fastmcp 中间层）。"""
        try:
            if str(_LINGTONGASK_ROOT) not in __import__("sys").path:
                __import__("sys").path.insert(0, str(_LINGTONGASK_ROOT))
            from src.audio.enhanced_tts import (  # noqa: PLC0415
                SentimentAnalyzer,
                EmotionToParamsMapper,
            )
            from src.audio.cosyvoice import CosyVoiceProvider  # noqa: PLC0415
            from src.audio.cosyvoice import list_voices as _list_voices_fn  # noqa: PLC0415

            self._sentiment_analyzer = SentimentAnalyzer
            self._emotion_mapper = EmotionToParamsMapper
            self._cosyvoice = CosyVoiceProvider
            self._list_voices = _list_voices_fn
            self._available = True
            logger.info("lingtongask 多模态插片：进程内 src 通道就绪")
        except Exception as e:  # noqa: BLE001 — fail-closed 注册不崩
            logger.warning("lingtongask src 导入失败（fail-closed，注册保留）: %s", e)
            self._available = False

    # ── MultimodalTask Protocol ──
    def task_type(self) -> str:
        return "multimodal"

    def input_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tasks": self._SCHEMAS,
            "transport": "in-process" if self._available else "unavailable",
        }

    async def execute(self, **kwargs: Any) -> Any:
        """按 task 参数分发到 lingtongask 工具。

        kwargs 需含 task（analyze_emotion/synthesize_speech/list_voices）+ 对应参数。
        """
        task = kwargs.pop("task", None) or kwargs.pop("tool", None)
        if task is None:
            return {"error": "缺少 task 参数（analyze_emotion/synthesize_speech/list_voices）"}

        if not self._available:
            # fail-closed：进程内不可用 → 尝试 HTTP 通道（若配置）
            if _HTTP_URL:
                return await self._execute_http(task, kwargs)
            return {"error": f"lingtongask 进程内通道不可用且未配置 HTTP 端点: {task}"}

        if task == "analyze_emotion":
            text = kwargs.get("text", "")
            if not text.strip():
                return {"error": "text 不能为空"}
            analyzer = self._sentiment_analyzer()
            mapper = self._emotion_mapper()
            scores = await analyzer.analyze(text)
            if not scores:
                return {"error": "情感分析返回空结果"}
            top = scores[0]
            params = mapper.map(top.emotion, top.score)
            return {
                "text": text[:100],
                "emotion": top.emotion.value,
                "score": round(top.score, 3),
                "confidence": round(top.confidence, 3),
                "all_emotions": [
                    {"emotion": s.emotion.value, "score": round(s.score, 3)}
                    for s in scores
                ],
                "tts_params": {
                    "speed": round(params.speed, 3),
                    "pitch": round(params.pitch, 3),
                    "volume": round(params.volume, 3),
                },
            }
        elif task == "synthesize_speech":
            text = kwargs.get("text", "")
            voice = kwargs.get("voice", "host")
            speed = float(kwargs.get("speed", 1.0))
            pitch = float(kwargs.get("pitch", 1.0))
            if not text.strip():
                return {"error": "text 不能为空"}
            if len(text) > 5000:
                return {"error": f"text 过长: {len(text)} > 5000 字符"}
            speed = max(0.5, min(2.0, speed))
            pitch = max(0.5, min(2.0, pitch))
            provider = self._cosyvoice()
            audio = await provider.synthesize_with_params(
                text=text,
                voice=voice,
                speed=speed,
                pitch=pitch,
            )
            if not audio:
                return {"error": "合成返回空音频", "text": text[:50]}
            return {
                "text": text[:100],
                "voice": voice,
                "speed": speed,
                "pitch": pitch,
                "audio_size_bytes": len(audio),
            }
        elif task == "list_voices":
            prefix = kwargs.get("prefix", "")
            try:
                voices = self._list_voices(prefix)
                return {"voices": voices, "count": len(voices)}
            except Exception as e:  # noqa: BLE001
                return {"error": f"list_voices 失败: {e}"}
        else:
            return {"error": f"未知任务: {task}（支持 analyze_emotion/synthesize_speech/list_voices）"}

    async def _execute_http(self, task: str, kwargs: dict[str, Any]) -> Any:
        """HTTP 通道（MCPHttpClient POST JSON-RPC）。"""
        from lingclaude.engine.mcp_client import MCPHttpClient

        client = MCPHttpClient(_HTTP_URL, timeout=60.0)
        args = {"text": kwargs.get("text", "")}
        if task == "synthesize_speech":
            args["voice"] = kwargs.get("voice", "host")
            args["speed"] = kwargs.get("speed", 1.0)
            args["pitch"] = kwargs.get("pitch", 1.0)
        res = client.call_tool(task, args)
        if res.is_error:
            return {"error": res.error}
        return res.data


def register_lingtong_multimodal(name: str = "lingtong_multimodal") -> bool:
    """把灵通问道多模态插片注册进 SeamRegistry（SeamType.MULTIMODAL）。

    返回注册是否成功（失败=fail-closed，不抛异常）。
    """
    try:
        task = LingTongMultimodalTask()
        missing = SeamRegistry.check_protocol(SeamType.MULTIMODAL, task)
        if missing:
            logger.error(
                "灵通问道插片缺少 MultimodalTask 协议成员 %s，拒绝注册", missing
            )
            return False
        SeamRegistry.register(SeamType.MULTIMODAL, name, task)
        logger.info("灵通问道多模态插片已注册: %s (available=%s)", task.name, task._available)
        return True
    except Exception as e:  # noqa: BLE001 — 注册失败不崩溃
        logger.error("灵通问道多模态插片注册失败: %s", e)
        return False

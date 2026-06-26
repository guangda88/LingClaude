"""语音识别引擎 — 支持 Whisper / sherpa-onnx 多后端，懒加载。"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class STTResult:
    text: str
    backend: str
    available: bool
    duration: float = 0.0
    language: str = ""
    segments: tuple[dict[str, Any], ...] = ()
    error: str = ""


@dataclass
class STTEngine:
    default_backend: str = ""
    language: str = "zh"
    model_size: str = "base"
    _models: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.default_backend:
            available = self.list_backends()
            if available:
                self.default_backend = available[0]

    def list_backends(self) -> list[str]:
        backends: list[str] = []
        try:
            import whisper  # noqa: F401
            backends.append("whisper")
        except ImportError:
            pass
        try:
            import sherpa_onnx  # noqa: F401
            backends.append("sherpa_onnx")
        except ImportError:
            pass
        return backends

    def is_available(self) -> bool:
        return len(self.list_backends()) > 0

    def status(self) -> dict[str, Any]:
        backends = self.list_backends()
        return {
            "available": len(backends) > 0,
            "backends": backends,
            "default": self.default_backend or (backends[0] if backends else None),
            "language": self.language,
            "model_size": self.model_size,
        }

    def record(self, duration: int = 5, output_path: str | None = None) -> str | None:
        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)

        try:
            result = subprocess.run(
                [
                    "arecord", "-f", "S16_LE", "-r", "16000",
                    "-c", "1", "-d", str(duration), output_path,
                ],
                capture_output=True,
                text=True,
                timeout=duration + 5,
            )
            if result.returncode == 0 and Path(output_path).exists():
                return output_path
            logger.debug("arecord failed: %s", result.stderr)
            return None
        except FileNotFoundError:
            logger.debug("arecord not found")
            return None
        except subprocess.TimeoutExpired:
            logger.debug("arecord timeout")
            return None

    def transcribe(
        self,
        audio_path: str,
        backend: str | None = None,
        language: str | None = None,
    ) -> STTResult:
        path = Path(audio_path)
        if not path.exists():
            return STTResult(
                text="", backend="", available=False,
                error=f"文件不存在: {audio_path}",
            )

        engine = backend or self.default_backend
        if not engine:
            return STTResult(
                text="", backend="", available=False,
                error="无可用的 STT 后端（需安装 openai-whisper 或 sherpa-onnx）",
            )

        lang = language or self.language

        if engine == "whisper":
            return self._transcribe_whisper(str(path), lang)
        elif engine == "sherpa_onnx":
            return self._transcribe_sherpa(str(path), lang)

        return STTResult(
            text="", backend=engine, available=False,
            error=f"未知后端: {engine}",
        )

    def record_and_transcribe(
        self,
        duration: int = 5,
        backend: str | None = None,
        language: str | None = None,
    ) -> STTResult:
        if not self.is_available():
            return STTResult(
                text="", backend="", available=False,
                error="无可用的 STT 后端",
            )

        audio_path = self.record(duration=duration)
        if audio_path is None:
            return STTResult(
                text="", backend="", available=False,
                error="录音失败，请检查麦克风",
            )

        try:
            return self.transcribe(audio_path, backend=backend, language=language)
        finally:
            try:
                Path(audio_path).unlink(missing_ok=True)
            except Exception as e:
                logger.debug("temp audio cleanup failed: %s", e)

    def _transcribe_whisper(self, audio_path: str, language: str) -> STTResult:
        try:
            import whisper
        except ImportError:
            return STTResult(
                text="", backend="whisper", available=False,
                error="openai-whisper 未安装",
            )

        if "whisper" not in self._models:
            logger.info("加载 Whisper %s 模型...", self.model_size)
            self._models["whisper"] = whisper.load_model(self.model_size)

        model = self._models["whisper"]
        try:
            result = model.transcribe(audio_path, language=language)
            text = result.get("text", "").strip()
            duration = (
                result.get("segments", [{}])[-1].get("end", 0.0)
                if result.get("segments") else 0.0
            )
            segments = tuple(
                {
                    "start": round(seg.get("start", 0.0), 3),
                    "end": round(seg.get("end", 0.0), 3),
                    "text": seg.get("text", "").strip(),
                }
                for seg in result.get("segments", [])
                if seg.get("text", "").strip()
            )
            return STTResult(
                text=text,
                backend="whisper",
                available=True,
                duration=duration,
                language=language,
                segments=segments,
            )
        except Exception as e:
            logger.debug("whisper 转录失败: %s", e)
            return STTResult(
                text="", backend="whisper", available=False, error=str(e),
            )

    def _transcribe_sherpa(self, audio_path: str, language: str) -> STTResult:
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError:
            return STTResult(
                text="", backend="sherpa_onnx", available=False,
                error="sherpa-onnx 未安装",
            )

        return STTResult(
            text="", backend="sherpa_onnx", available=False,
            error="sherpa-onnx 支持待实现",
        )

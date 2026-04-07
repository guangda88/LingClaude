"""Tests for STT engine."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lingclaude.engine.stt import STTEngine, STTResult


class TestSTTResult:
    def test_ok_result(self) -> None:
        r = STTResult(text="hello", backend="whisper", available=True)
        assert r.text == "hello"
        assert r.backend == "whisper"
        assert r.available
        assert r.error == ""

    def test_error_result(self) -> None:
        r = STTResult(text="", backend="", available=False, error="no backend")
        assert not r.available
        assert r.error == "no backend"


class TestSTTEngine:
    def test_list_backends_no_whisper(self) -> None:
        engine = STTEngine()
        with patch.dict("sys.modules", {"whisper": None, "sherpa_onnx": None}):
            backends = engine.list_backends()
            assert isinstance(backends, list)

    def test_is_available_with_whisper(self) -> None:
        engine = STTEngine()
        with patch("lingclaude.engine.stt.STTEngine.list_backends", return_value=["whisper"]):
            assert engine.is_available()

    def test_is_available_empty(self) -> None:
        engine = STTEngine()
        with patch("lingclaude.engine.stt.STTEngine.list_backends", return_value=[]):
            assert not engine.is_available()

    def test_status(self) -> None:
        engine = STTEngine()
        with patch("lingclaude.engine.stt.STTEngine.list_backends", return_value=["whisper"]):
            s = engine.status()
            assert s["available"]
            assert "whisper" in s["backends"]

    def test_status_no_backends(self) -> None:
        engine = STTEngine()
        with patch("lingclaude.engine.stt.STTEngine.list_backends", return_value=[]):
            s = engine.status()
            assert not s["available"]
            assert s["backends"] == []

    def test_transcribe_file_not_found(self) -> None:
        engine = STTEngine(default_backend="whisper")
        result = engine.transcribe("/nonexistent/audio.wav")
        assert not result.available
        assert "不存在" in result.error

    def test_transcribe_no_backend(self) -> None:
        engine = STTEngine(default_backend="")
        with patch("lingclaude.engine.stt.STTEngine.list_backends", return_value=[]), \
             patch.object(Path, "exists", return_value=True):
            engine.default_backend = ""
            result = engine.transcribe("/some/file.wav")
            assert not result.available
            assert "无可用" in result.error

    def test_transcribe_unknown_backend(self) -> None:
        engine = STTEngine(default_backend="unknown_engine")
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            result = engine.transcribe(path, backend="unknown_engine")
            assert not result.available
            assert "未知后端" in result.error
        finally:
            Path(path).unlink(missing_ok=True)

    def test_transcribe_whisper_mock(self) -> None:
        engine = STTEngine(default_backend="whisper")
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            mock_model = MagicMock()
            mock_model.transcribe.return_value = {
                "text": " 你好世界 ",
                "segments": [
                    {"start": 0.0, "end": 2.0, "text": " 你好"},
                    {"start": 2.0, "end": 4.0, "text": " 世界"},
                ],
            }
            with patch("whisper.load_model", return_value=mock_model):
                result = engine.transcribe(path, backend="whisper")
            assert result.available
            assert result.text == "你好世界"
            assert result.backend == "whisper"
            assert result.duration == 4.0
            assert len(result.segments) == 2
        finally:
            Path(path).unlink(missing_ok=True)

    def test_transcribe_whisper_import_error(self) -> None:
        engine = STTEngine(default_backend="whisper")
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with patch.dict("sys.modules", {"whisper": None}):
                result = engine._transcribe_whisper(path, "zh")
            assert not result.available
            assert "未安装" in result.error
        finally:
            Path(path).unlink(missing_ok=True)

    def test_record_and_transcribe_no_backend(self) -> None:
        engine = STTEngine(default_backend="")
        with patch("lingclaude.engine.stt.STTEngine.list_backends", return_value=[]):
            result = engine.record_and_transcribe()
        assert not result.available
        assert "无可用" in result.error

    def test_record_and_transcribe_record_fail(self) -> None:
        engine = STTEngine(default_backend="whisper")
        with patch("lingclaude.engine.stt.STTEngine.is_available", return_value=True), \
             patch.object(engine, "record", return_value=None):
            result = engine.record_and_transcribe()
        assert not result.available
        assert "录音失败" in result.error

    def test_record_and_transcribe_success(self) -> None:
        engine = STTEngine(default_backend="whisper")
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            mock_model = MagicMock()
            mock_model.transcribe.return_value = {
                "text": "测试语音",
                "segments": [{"start": 0.0, "end": 1.0, "text": " 测试语音"}],
            }
            with patch.object(engine, "record", return_value=path), \
                 patch("whisper.load_model", return_value=mock_model):
                result = engine.record_and_transcribe()
            assert result.available
            assert result.text == "测试语音"
        finally:
            Path(path).unlink(missing_ok=True)

    def test_record_arecord_not_found(self) -> None:
        engine = STTEngine()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = engine.record()
        assert result is None

    def test_record_timeout(self) -> None:
        engine = STTEngine()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=1)):
            result = engine.record(duration=1)
        assert result is None

    def test_default_backend_auto_select(self) -> None:
        with patch("lingclaude.engine.stt.STTEngine.list_backends", return_value=["whisper"]):
            engine = STTEngine()
            assert engine.default_backend == "whisper"

    def test_default_backend_explicit(self) -> None:
        engine = STTEngine(default_backend="sherpa_onnx")
        assert engine.default_backend == "sherpa_onnx"

    def test_model_cached(self) -> None:
        engine = STTEngine(default_backend="whisper")
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            mock_model = MagicMock()
            mock_model.transcribe.return_value = {
                "text": "cached", "segments": [],
            }
            with patch("whisper.load_model", return_value=mock_model) as mock_load:
                engine.transcribe(path, backend="whisper")
                engine.transcribe(path, backend="whisper")
                assert mock_load.call_count == 1
        finally:
            Path(path).unlink(missing_ok=True)

    def test_segments_filtered(self) -> None:
        engine = STTEngine(default_backend="whisper")
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            mock_model = MagicMock()
            mock_model.transcribe.return_value = {
                "text": "hello",
                "segments": [
                    {"start": 0.0, "end": 1.0, "text": " hello "},
                    {"start": 1.0, "end": 2.0, "text": "  "},
                    {"start": 2.0, "end": 3.0, "text": "world"},
                ],
            }
            with patch("whisper.load_model", return_value=mock_model):
                result = engine.transcribe(path, backend="whisper")
            assert len(result.segments) == 2
            assert result.segments[0]["text"] == "hello"
        finally:
            Path(path).unlink(missing_ok=True)

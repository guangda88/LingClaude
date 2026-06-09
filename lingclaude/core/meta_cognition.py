"""Meta-Cognition — 元认知.

Knowing what you know and what you don't know.

Three components:
  1. CognitiveBoundary — tracks domains where the model is strong/weak/unknown
  2. ConfidenceCalibrator — adjusts confidence based on historical accuracy
  3. BlindSpotDetector — discovers patterns of recurring errors

Persistence:
  save/load to JSON — calibration data and blind spots survive session restarts.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class Domain(str, Enum):
    CODE_UNDERSTANDING = "code_understanding"
    CODE_GENERATION = "code_generation"
    DEBUGGING = "debugging"
    ARCHITECTURE = "architecture"
    GENERAL_KNOWLEDGE = "general_knowledge"
    FILE_NAVIGATION = "file_navigation"
    API_USAGE = "api_usage"
    SECURITY = "security"


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CognitiveBoundary:
    domain: Domain
    confidence: ConfidenceLevel
    accuracy: float
    sample_count: int
    last_error: str = ""
    last_error_time: str = ""


@dataclass(frozen=True)
class MetaCognitiveSnapshot:
    boundaries: tuple[CognitiveBoundary, ...]
    overall_confidence: ConfidenceLevel
    blind_spots: tuple[str, ...]
    calibration_score: float
    summary: str


@dataclass
class _DomainRecord:
    correct: int = 0
    incorrect: int = 0
    last_error: str = ""
    last_error_time: str = ""

    @property
    def total(self) -> int:
        return self.correct + self.incorrect

    @property
    def accuracy(self) -> float:
        if self.total == 0:
            return 0.0
        return self.correct / self.total


@dataclass
class ConfidenceCalibrator:
    records: dict[str, _DomainRecord]

    def __init__(self) -> None:
        self.records: dict[str, _DomainRecord] = {}

    def record_outcome(
        self,
        domain: Domain,
        correct: bool,
        error_description: str = "",
        timestamp: str = "",
    ) -> None:
        key = domain.value
        if key not in self.records:
            self.records[key] = _DomainRecord()
        rec = self.records[key]
        if correct:
            rec.correct += 1
        else:
            rec.incorrect += 1
            rec.last_error = error_description
            rec.last_error_time = timestamp

    def get_accuracy(self, domain: Domain) -> float:
        rec = self.records.get(domain.value)
        return rec.accuracy if rec else 0.0

    def calibration_score(self) -> float:
        if not self.records:
            return 0.5
        total = sum(r.total for r in self.records.values())
        if total == 0:
            return 0.5
        correct = sum(r.correct for r in self.records.values())
        return correct / total


@dataclass
class BlindSpotDetector:
    error_patterns: dict[str, int]
    error_descriptions: dict[str, list[str]]

    def __init__(self) -> None:
        self.error_patterns: dict[str, int] = {}
        self.error_descriptions: dict[str, list[str]] = {}

    def record_error(self, domain: Domain, description: str) -> None:
        key = domain.value
        self.error_patterns[key] = self.error_patterns.get(key, 0) + 1
        if key not in self.error_descriptions:
            self.error_descriptions[key] = []
        self.error_descriptions[key].append(description)
        if len(self.error_descriptions[key]) > 20:
            self.error_descriptions[key] = self.error_descriptions[key][-20:]

    def detect_blind_spots(self, threshold: int = 2) -> tuple[str, ...]:
        return tuple(
            domain for domain, count in self.error_patterns.items()
            if count >= threshold
        )

    def get_error_summary(self, domain: str) -> list[str]:
        return self.error_descriptions.get(domain, [])[-5:]


class MetaCognition:
    _DEFAULT_PERSIST_PATH = Path(".lingclaude/meta_cognition.json")

    def __init__(self, persist_path: Path | None = None) -> None:
        self._calibrator = ConfidenceCalibrator()
        self._blind_spot_detector = BlindSpotDetector()
        self._domain_map: dict[str, Domain] = {
            d.value: d for d in Domain
        }
        self._persist_path = persist_path or self._DEFAULT_PERSIST_PATH
        self.load(self._persist_path)

    def record_success(self, domain: Domain) -> None:
        self._calibrator.record_outcome(domain, correct=True)
        self.save(self._persist_path)

    def record_failure(
        self,
        domain: Domain,
        error_description: str = "",
        timestamp: str = "",
    ) -> None:
        self._calibrator.record_outcome(
            domain, correct=False,
            error_description=error_description,
            timestamp=timestamp,
        )
        self._blind_spot_detector.record_error(domain, error_description)
        self.save(self._persist_path)

    def get_snapshot(self) -> MetaCognitiveSnapshot:
        boundaries: list[CognitiveBoundary] = []
        for domain in Domain:
            rec = self._calibrator.records.get(domain.value)
            if rec and rec.total > 0:
                conf = self._classify_confidence(rec.accuracy, rec.total)
                boundaries.append(CognitiveBoundary(
                    domain=domain,
                    confidence=conf,
                    accuracy=rec.accuracy,
                    sample_count=rec.total,
                    last_error=rec.last_error,
                    last_error_time=rec.last_error_time,
                ))

        blind_spots = self._blind_spot_detector.detect_blind_spots()
        calibration = self._calibrator.calibration_score()
        overall = self._classify_confidence(calibration, sum(r.total for r in self._calibrator.records.values()))

        summary = self._build_summary(boundaries, blind_spots, calibration)

        return MetaCognitiveSnapshot(
            boundaries=tuple(boundaries),
            overall_confidence=overall,
            blind_spots=blind_spots,
            calibration_score=calibration,
            summary=summary,
        )

    def get_system_prompt_injection(self) -> str:
        snap = self.get_snapshot()
        if not snap.blind_spots and snap.calibration_score >= 0.7:
            return ""

        parts: list[str] = []
        if snap.blind_spots:
            names = ", ".join(snap.blind_spots)
            parts.append(
                f"自知盲区: 你在 [{names}] 方面容易出错，遇到这类问题要格外谨慎，优先使用工具验证。"
            )
        for b in snap.boundaries:
            if b.confidence == ConfidenceLevel.LOW:
                parts.append(
                    f"低置信领域: {b.domain.value} (准确率 {b.accuracy:.0%})，回答前务必验证。"
                )
        return "\n".join(parts)

    def _classify_confidence(self, accuracy: float, sample_count: int) -> ConfidenceLevel:
        if sample_count < 2:
            return ConfidenceLevel.UNKNOWN
        adjusted = accuracy * math.log1p(sample_count) / math.log1p(10)
        if adjusted >= 0.7:
            return ConfidenceLevel.HIGH
        if adjusted >= 0.4:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    def save(self, path: Path | None = None) -> None:
        path = path or self._persist_path
        data: dict = {
            "calibrator": {
                key: {"correct": rec.correct, "incorrect": rec.incorrect,
                      "last_error": rec.last_error, "last_error_time": rec.last_error_time}
                for key, rec in self._calibrator.records.items()
            },
            "blind_spot_detector": {
                "error_patterns": self._blind_spot_detector.error_patterns,
                "error_descriptions": self._blind_spot_detector.error_descriptions,
            },
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("Failed to save meta-cognition state to %s", path)

    def load(self, path: Path | None = None) -> None:
        path = path or self._persist_path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to load meta-cognition state from %s", path)
            return

        for key, rec_data in data.get("calibrator", {}).items():
            self._calibrator.records[key] = _DomainRecord(
                correct=rec_data.get("correct", 0),
                incorrect=rec_data.get("incorrect", 0),
                last_error=rec_data.get("last_error", ""),
                last_error_time=rec_data.get("last_error_time", ""),
            )

        bsd = data.get("blind_spot_detector", {})
        self._blind_spot_detector.error_patterns = bsd.get("error_patterns", {})
        self._blind_spot_detector.error_descriptions = bsd.get("error_descriptions", {})

    def _build_summary(
        self,
        boundaries: list[CognitiveBoundary],
        blind_spots: tuple[str, ...],
        calibration: float,
    ) -> str:
        parts: list[str] = [f"整体校准: {calibration:.0%}"]
        weak = [b for b in boundaries if b.confidence == ConfidenceLevel.LOW]
        if weak:
            parts.append(f"薄弱领域: {', '.join(b.domain.value for b in weak)}")
        if blind_spots:
            parts.append(f"盲区: {', '.join(blind_spots)}")
        return "; ".join(parts)

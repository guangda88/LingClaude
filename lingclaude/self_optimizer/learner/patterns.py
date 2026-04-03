from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any


class PatternDetector:
    def __init__(
        self, name: str, pattern_type: str, severity: str = "MEDIUM"
    ) -> None:
        self.name = name
        self.pattern_type = pattern_type
        self.severity = severity
        self._detection_count = 0

    def detect(
        self, source_code: str, file_path: str
    ) -> tuple[dict[str, Any], ...]:
        raise NotImplementedError

    def _create_finding(
        self,
        file_path: str,
        line: int,
        message: str,
        confidence: float = 0.8,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        finding: dict[str, Any] = {
            "type": self.pattern_type,
            "name": self.name,
            "file": file_path,
            "line": line,
            "message": message,
            "severity": self.severity,
            "confidence": confidence,
        }
        if extra:
            finding.update(extra)
        self._detection_count += 1
        return finding


class LongMethodDetector(PatternDetector):
    def __init__(self, threshold: int = 50) -> None:
        super().__init__(
            name="Long Method", pattern_type="anti_pattern", severity="MEDIUM"
        )
        self.threshold = threshold

    def detect(
        self, source_code: str, file_path: str
    ) -> list[dict[str, Any]]:
        patterns: list[dict[str, Any]] = []
        try:
            tree = ast.parse(source_code)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    line_count = (
                        node.end_lineno - node.lineno if node.end_lineno else 0
                    )
                    if line_count > self.threshold:
                        patterns.append(
                            self._create_finding(
                                file_path=file_path,
                                line=node.lineno,
                                message=f"Function '{node.name}' is too long ({line_count} lines)",
                                confidence=0.9,
                                extra={
                                    "function_name": node.name,
                                    "line_count": line_count,
                                },
                            )
                        )
        except SyntaxError:
            patterns.extend(self._simple_detection(source_code, file_path))
        return patterns

    def _simple_detection(
        self, source_code: str, file_path: str
    ) -> list[dict[str, Any]]:
        patterns: list[dict[str, Any]] = []
        lines = source_code.split("\n")
        current_function: str | None = None
        function_lines = 0
        function_start = 0

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("def ") or stripped.startswith("async def "):
                if current_function and function_lines > self.threshold:
                    patterns.append(
                        self._create_finding(
                            file_path=file_path,
                            line=function_start,
                            message=f"Function '{current_function}' is too long ({function_lines} lines)",
                            confidence=0.7,
                        )
                    )
                func_def = stripped.replace("async def ", "def ")
                current_function = (
                    func_def.split("(")[0].replace("def ", "").strip()
                )
                function_lines = 0
                function_start = i
            elif current_function and stripped:
                function_lines += 1

        if current_function and function_lines > self.threshold:
            patterns.append(
                self._create_finding(
                    file_path=file_path,
                    line=function_start,
                    message=f"Function '{current_function}' is too long ({function_lines} lines)",
                    confidence=0.7,
                )
            )
        return patterns


class UnusedVariableDetector(PatternDetector):
    def __init__(self) -> None:
        super().__init__(
            name="Unused Variable", pattern_type="code_quality", severity="LOW"
        )

    def detect(
        self, source_code: str, file_path: str
    ) -> list[dict[str, Any]]:
        patterns: list[dict[str, Any]] = []
        try:
            tree = ast.parse(source_code)

            assignments: dict[str, list[int]] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            assignments.setdefault(target.id, []).append(
                                node.lineno
                            )
                elif isinstance(node, ast.For):
                    if isinstance(node.target, ast.Name):
                        assignments.setdefault(node.target.id, []).append(
                            node.lineno
                        )

            used_vars: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    used_vars.add(node.id)

            for var_name, lines in assignments.items():
                if (
                    var_name not in used_vars
                    and not var_name.startswith("_")
                    and var_name not in ("self", "cls")
                ):
                    patterns.append(
                        self._create_finding(
                            file_path=file_path,
                            line=lines[0],
                            message=f"Variable '{var_name}' is assigned but never used",
                            confidence=0.85,
                            extra={"variable_name": var_name},
                        )
                    )
        except SyntaxError:
            pass
        return patterns[:10]


class HardcodedSecretDetector(PatternDetector):
    def __init__(self) -> None:
        super().__init__(
            name="Hardcoded Secret", pattern_type="security", severity="HIGH"
        )
        self.secret_patterns = {
            "password": r'(?:password|passwd|pwd)\s*=\s*["\'][^"\']{4,}["\']',
            "api_key": r'(?:api[_-]?key|apikey)\s*=\s*["\'][^"\']{10,}["\']',
            "secret": r'(?:secret|token)\s*=\s*["\'][^"\']{10,}["\']',
            "private_key": r'private[_-]?key\s*=\s*["\'][^"\']{20,}["\']',
        }

    def detect(
        self, source_code: str, file_path: str
    ) -> list[dict[str, Any]]:
        patterns: list[dict[str, Any]] = []
        for secret_name, pattern in self.secret_patterns.items():
            for match in re.finditer(pattern, source_code, re.IGNORECASE):
                line_num = source_code[: match.start()].count("\n") + 1
                patterns.append(
                    self._create_finding(
                        file_path=file_path,
                        line=line_num,
                        message=f"Hardcoded {secret_name.replace('_', ' ')} detected",
                        confidence=0.9,
                        extra={"secret_type": secret_name},
                    )
                )
        return patterns


class DuplicateCodeDetector(PatternDetector):
    def __init__(self, min_lines: int = 3) -> None:
        super().__init__(
            name="Duplicate Code", pattern_type="code_quality", severity="LOW"
        )
        self.min_lines = min_lines

    def detect(
        self, source_code: str, file_path: str
    ) -> list[dict[str, Any]]:
        patterns: list[dict[str, Any]] = []
        lines = source_code.split("\n")
        code_blocks: dict[str, list[int]] = {}

        for i in range(len(lines) - self.min_lines + 1):
            block = []
            for j in range(self.min_lines):
                if i + j < len(lines):
                    line = lines[i + j].strip()
                    if line and not line.startswith("#"):
                        block.append(line)

            if len(block) >= self.min_lines:
                normalized = self._normalize_block(block)
                if normalized:
                    code_blocks.setdefault(normalized, []).append(i + 1)

        for _block, locations in code_blocks.items():
            if len(locations) > 1:
                for location in locations[1:]:
                    patterns.append(
                        self._create_finding(
                            file_path=file_path,
                            line=location,
                            message=f"Duplicate code block (also at line {locations[0]})",
                            confidence=0.75,
                            extra={"duplicate_of": locations[0]},
                        )
                    )
        return patterns[:5]

    def _normalize_block(self, block: list[str]) -> str | None:
        try:
            normalized = []
            for line in block:
                line = re.sub(r'["\'][^"\']*["\']', '""', line)
                line = re.sub(r"\b\d+\b", "0", line)
                normalized.append(line)
            return " ".join(normalized)
        except Exception:
            return None


class EmptyBlockDetector(PatternDetector):
    def __init__(self) -> None:
        super().__init__(
            name="Empty Block", pattern_type="code_quality", severity="INFO"
        )

    def detect(
        self, source_code: str, file_path: str
    ) -> list[dict[str, Any]]:
        patterns: list[dict[str, Any]] = []
        try:
            tree = ast.parse(source_code)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.body:
                        patterns.append(
                            self._create_finding(
                                file_path=file_path,
                                line=node.lineno,
                                message=f"Function '{node.name}' has empty body",
                                confidence=1.0,
                            )
                        )
                    elif len(node.body) == 1 and isinstance(
                        node.body[0], ast.Pass
                    ):
                        patterns.append(
                            self._create_finding(
                                file_path=file_path,
                                line=node.lineno,
                                message=f"Function '{node.name}' only contains 'pass'",
                                confidence=1.0,
                            )
                        )
                elif isinstance(node, (ast.If, ast.For, ast.While)):
                    if not node.body:
                        patterns.append(
                            self._create_finding(
                                file_path=file_path,
                                line=node.lineno,
                                message="Empty block detected",
                                confidence=1.0,
                            )
                        )
        except SyntaxError:
            patterns.extend(self._simple_detection(source_code, file_path))
        return patterns

    def _simple_detection(
        self, source_code: str, file_path: str
    ) -> list[dict[str, Any]]:
        patterns: list[dict[str, Any]] = []
        empty_pattern = re.compile(
            r"(def |if |for |while |class )[^:]+:\s*pass\s*$"
        )
        for i, line in enumerate(source_code.split("\n"), 1):
            if empty_pattern.search(line):
                patterns.append(
                    self._create_finding(
                        file_path=file_path,
                        line=i,
                        message="Empty block with 'pass'",
                        confidence=0.8,
                    )
                )
        return patterns


class ComplexityDetector(PatternDetector):
    def __init__(self, threshold: int = 10) -> None:
        super().__init__(
            name="High Complexity", pattern_type="complexity", severity="MEDIUM"
        )
        self.threshold = threshold

    def detect(
        self, source_code: str, file_path: str
    ) -> list[dict[str, Any]]:
        patterns: list[dict[str, Any]] = []
        try:
            tree = ast.parse(source_code)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    complexity = self._calculate_complexity(node)
                    if complexity > self.threshold:
                        patterns.append(
                            self._create_finding(
                                file_path=file_path,
                                line=node.lineno,
                                message=f"Function '{node.name}' has high complexity ({complexity})",
                                confidence=0.9,
                                extra={
                                    "function_name": node.name,
                                    "complexity": complexity,
                                },
                            )
                        )
        except SyntaxError:
            pass
        return patterns

    def _calculate_complexity(self, node: ast.AST) -> int:
        complexity = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor)):
                complexity += 1
            elif isinstance(child, ast.ExceptHandler):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
        return complexity


class PatternRecognizer:
    def __init__(
        self, detectors: list[PatternDetector] | None = None
    ) -> None:
        self.detectors = detectors or self._default_detectors()
        self._detected_count = 0

    def _default_detectors(self) -> list[PatternDetector]:
        return [
            LongMethodDetector(),
            UnusedVariableDetector(),
            HardcodedSecretDetector(),
            DuplicateCodeDetector(),
            EmptyBlockDetector(),
            ComplexityDetector(),
        ]

    def register_detector(self, detector: PatternDetector) -> None:
        self.detectors.append(detector)

    def recognize_patterns(
        self, source_code: str, file_path: str
    ) -> tuple[dict[str, Any], ...]:
        patterns: list[dict[str, Any]] = []
        for detector in self.detectors:
            try:
                detected = detector.detect(source_code, file_path)
                patterns.extend(detected)
                self._detected_count += len(detected)
            except Exception:
                continue
        return tuple(patterns)

    def recognize_from_file(self, file_path: str) -> tuple[dict[str, Any], ...]:
        try:
            source_code = Path(file_path).read_text(encoding="utf-8")
            return self.recognize_patterns(source_code, file_path)
        except Exception:
            return ()

    def get_statistics(self) -> dict[str, Any]:
        return {
            "total_detectors": len(self.detectors),
            "total_detections": self._detected_count,
        }

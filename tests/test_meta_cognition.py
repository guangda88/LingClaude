"""Tests for lingclaude.core.meta_cognition"""
from __future__ import annotations

import unittest
from pathlib import Path

from lingclaude.core.meta_cognition import (
    BlindSpotDetector,
    ConfidenceCalibrator,
    ConfidenceLevel,
    Domain,
    MetaCognition,
    MetaCognitiveSnapshot,
    _DomainRecord,
)


class TestDomainRecord(unittest.TestCase):
    def test_defaults(self):
        r = _DomainRecord()
        self.assertEqual(r.correct, 0)
        self.assertEqual(r.incorrect, 0)
        self.assertEqual(r.total, 0)
        self.assertAlmostEqual(r.accuracy, 0.0)

    def test_accuracy(self):
        r = _DomainRecord(correct=7, incorrect=3)
        self.assertEqual(r.total, 10)
        self.assertAlmostEqual(r.accuracy, 0.7)


class TestConfidenceCalibrator(unittest.TestCase):
    def test_record_outcome_correct(self):
        c = ConfidenceCalibrator()
        c.record_outcome(Domain.CODE_GENERATION, correct=True)
        self.assertAlmostEqual(c.get_accuracy(Domain.CODE_GENERATION), 1.0)

    def test_record_outcome_incorrect(self):
        c = ConfidenceCalibrator()
        c.record_outcome(Domain.DEBUGGING, correct=False, error_description="segfault")
        self.assertAlmostEqual(c.get_accuracy(Domain.DEBUGGING), 0.0)
        self.assertEqual(c.records[Domain.DEBUGGING.value].last_error, "segfault")

    def test_get_accuracy_unknown_domain(self):
        c = ConfidenceCalibrator()
        self.assertAlmostEqual(c.get_accuracy(Domain.SECURITY), 0.0)

    def test_calibration_score_empty(self):
        c = ConfidenceCalibrator()
        self.assertAlmostEqual(c.calibration_score(), 0.5)

    def test_calibration_score_mixed(self):
        c = ConfidenceCalibrator()
        c.record_outcome(Domain.CODE_GENERATION, correct=True)
        c.record_outcome(Domain.CODE_GENERATION, correct=True)
        c.record_outcome(Domain.DEBUGGING, correct=False)
        self.assertAlmostEqual(c.calibration_score(), 2 / 3)


class TestBlindSpotDetector(unittest.TestCase):
    def test_no_spots_initially(self):
        b = BlindSpotDetector()
        self.assertEqual(b.detect_blind_spots(), ())

    def test_detect_below_threshold(self):
        b = BlindSpotDetector()
        b.record_error(Domain.ARCHITECTURE, "misdesigned layer")
        self.assertEqual(b.detect_blind_spots(threshold=2), ())

    def test_detect_at_threshold(self):
        b = BlindSpotDetector()
        b.record_error(Domain.ARCHITECTURE, "err1")
        b.record_error(Domain.ARCHITECTURE, "err2")
        self.assertIn(Domain.ARCHITECTURE.value, b.detect_blind_spots(threshold=2))

    def test_error_summary_truncation(self):
        b = BlindSpotDetector()
        for i in range(25):
            b.record_error(Domain.CODE_GENERATION, f"err{i}")
        summary = b.get_error_summary(Domain.CODE_GENERATION.value)
        self.assertLessEqual(len(summary), 5)


class TestMetaCognition(unittest.TestCase):
    def _make(self, tmp: Path) -> MetaCognition:
        return MetaCognition(persist_path=tmp / "mc.json")

    def test_record_success(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mc = self._make(Path(td))
            mc.record_success(Domain.CODE_GENERATION)
            self.assertAlmostEqual(mc._calibrator.get_accuracy(Domain.CODE_GENERATION), 1.0)

    def test_record_failure(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mc = self._make(Path(td))
            mc.record_failure(Domain.DEBUGGING, "null ptr", "2026-01-01")
            self.assertAlmostEqual(mc._calibrator.get_accuracy(Domain.DEBUGGING), 0.0)

    def test_save_load_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "mc.json"
            mc1 = MetaCognition(persist_path=p)
            mc1.record_success(Domain.CODE_GENERATION)
            mc1.record_success(Domain.CODE_GENERATION)
            mc1.record_failure(Domain.SECURITY, "xss", "2026-01-01")
            mc1.record_failure(Domain.SECURITY, "csrf", "2026-01-02")

            mc2 = MetaCognition(persist_path=p)
            self.assertAlmostEqual(mc2._calibrator.get_accuracy(Domain.CODE_GENERATION), 1.0)
            self.assertAlmostEqual(mc2._calibrator.get_accuracy(Domain.SECURITY), 0.0)

    def test_snapshot_structure(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mc = self._make(Path(td))
            mc.record_success(Domain.CODE_GENERATION)
            mc.record_success(Domain.CODE_GENERATION)
            snap = mc.get_snapshot()
            self.assertIsInstance(snap, MetaCognitiveSnapshot)
            self.assertIsInstance(snap.boundaries, tuple)
            self.assertIsInstance(snap.calibration_score, float)

    def test_system_prompt_injection_empty_when_good(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mc = self._make(Path(td))
            for _ in range(20):
                mc.record_success(Domain.CODE_GENERATION)
            self.assertEqual(mc.get_system_prompt_injection(), "")

    def test_system_prompt_injection_with_blind_spot(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mc = self._make(Path(td))
            mc.record_failure(Domain.SECURITY, "xss")
            mc.record_failure(Domain.SECURITY, "csrf")
            injection = mc.get_system_prompt_injection()
            self.assertIn("security", injection)

    def test_classify_confidence_unknown(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mc = self._make(Path(td))
            self.assertEqual(mc._classify_confidence(1.0, 1), ConfidenceLevel.UNKNOWN)

    def test_load_corrupt_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "mc.json"
            p.write_text("{bad json")
            mc = MetaCognition(persist_path=p)
            self.assertEqual(len(mc._calibrator.records), 0)


if __name__ == "__main__":
    unittest.main()

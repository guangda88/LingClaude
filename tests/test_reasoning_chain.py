"""Tests for lingclaude.core.reasoning_chain"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lingclaude.core.reasoning_chain import (
    ChainStep,
    ChainStepType,
    ReasoningChain,
    ReasoningChainLingBusLogger,
    ReasoningChainLogger,
)


class TestChainStep(unittest.TestCase):
    def test_auto_timestamp(self):
        s = ChainStep(step_type=ChainStepType.OBSERVATION, content="saw X")
        self.assertTrue(s.timestamp)

    def test_explicit_timestamp(self):
        s = ChainStep(step_type=ChainStepType.ACTION, content="did Y", timestamp="2026-01-01")
        self.assertEqual(s.timestamp, "2026-01-01")

    def test_metadata(self):
        s = ChainStep(step_type=ChainStepType.REASONING, content="think", metadata={"k": 1})
        self.assertEqual(s.metadata["k"], 1)


class TestReasoningChain(unittest.TestCase):
    def _make(self, **kw):
        return ReasoningChain(chain_id="c1", agent_id="a1", topic="test", **kw)

    def test_auto_created_at(self):
        c = self._make()
        self.assertTrue(c.created_at)

    def test_add_step_immutable(self):
        c = self._make()
        s = ChainStep(step_type=ChainStepType.OBSERVATION, content="obs")
        c2 = c.add_step(s)
        self.assertEqual(len(c.steps), 0)
        self.assertEqual(len(c2.steps), 1)

    def test_finalize(self):
        c = self._make()
        c2 = c.finalize("done", self_interest_flagged=True, self_interest_detail="biased")
        self.assertEqual(c2.conclusion, "done")
        self.assertTrue(c2.self_interest_flagged)
        self.assertTrue(c2.finalized_at)

    def test_has_self_check(self):
        c = self._make(steps=(
            ChainStep(step_type=ChainStepType.SELF_CHECK, content="check"),
            ChainStep(step_type=ChainStepType.OBSERVATION, content="obs"),
        ))
        self.assertTrue(c.has_self_check())

    def test_has_bias_detection(self):
        c = self._make(steps=(
            ChainStep(step_type=ChainStepType.BIAS_DETECTED, content="bias"),
        ))
        self.assertTrue(c.has_bias_detection())

    def test_get_corrections(self):
        c = self._make(steps=(
            ChainStep(step_type=ChainStepType.CORRECTION, content="fix1"),
            ChainStep(step_type=ChainStepType.OBSERVATION, content="obs"),
            ChainStep(step_type=ChainStepType.CORRECTION, content="fix2"),
        ))
        corrs = c.get_corrections()
        self.assertEqual(len(corrs), 2)

    def test_to_dict(self):
        c = self._make(steps=(
            ChainStep(step_type=ChainStepType.REASONING, content="think"),
        ))
        d = c.to_dict()
        self.assertEqual(d["chain_id"], "c1")
        self.assertEqual(len(d["steps"]), 1)
        self.assertEqual(d["steps"][0]["type"], "reasoning")


class TestReasoningChainLogger(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            logger = ReasoningChainLogger(log_dir=Path(td))
            chain = ReasoningChain(
                chain_id="c1", agent_id="a1", topic="test",
                steps=(ChainStep(step_type=ChainStepType.OBSERVATION, content="obs"),),
            ).finalize("conclusion")
            path = logger.save(chain)
            self.assertTrue(path.exists())

            loaded = logger.load(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.chain_id, "c1")
            self.assertEqual(loaded.conclusion, "conclusion")

    def test_load_corrupt(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text("{bad")
            logger = ReasoningChainLogger(log_dir=Path(td))
            self.assertIsNone(logger.load(p))

    def test_list_chains(self):
        with tempfile.TemporaryDirectory() as td:
            logger = ReasoningChainLogger(log_dir=Path(td))
            for i in range(3):
                chain = ReasoningChain(
                    chain_id=f"c{i}", agent_id="a1", topic="t",
                ).finalize(f"conclusion {i}")
                logger.save(chain)
            chains = logger.list_chains("a1")
            self.assertEqual(len(chains), 3)

    def test_analyze_self_interest_rate(self):
        with tempfile.TemporaryDirectory() as td:
            logger = ReasoningChainLogger(log_dir=Path(td))
            for i in range(4):
                chain = ReasoningChain(
                    chain_id=f"c{i}", agent_id="a1", topic="t",
                ).finalize(f"con{i}", self_interest_flagged=(i % 2 == 0))
                logger.save(chain)
            stats = logger.analyze_self_interest_rate("a1")
            self.assertEqual(stats["total"], 4)
            self.assertEqual(stats["flagged"], 2)
            self.assertAlmostEqual(stats["rate"], 0.5)


class TestReasoningChainLingBusLogger(unittest.TestCase):
    def test_save_without_lingbus(self):
        with tempfile.TemporaryDirectory() as td:
            logger = ReasoningChainLingBusLogger(
                log_dir=Path(td) / "chains",
                lingbus_dir=Path(td) / "nolingbus",
            )
            chain = ReasoningChain(
                chain_id="c1", agent_id="a1", topic="t",
            ).finalize("done")
            path = logger.save(chain)
            self.assertTrue(path.exists())

    def test_save_with_lingbus_db(self):
        with tempfile.TemporaryDirectory() as td:
            lb_dir = Path(td) / "lingmessage"
            lb_dir.mkdir()
            import sqlite3
            conn = sqlite3.connect(str(lb_dir / "lingbus.db"))
            conn.commit()
            conn.close()

            logger = ReasoningChainLingBusLogger(
                log_dir=Path(td) / "chains",
                lingbus_dir=lb_dir,
            )
            chain = ReasoningChain(
                chain_id="c1", agent_id="a1", topic="t",
            ).finalize("done")
            path = logger.save(chain)
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()

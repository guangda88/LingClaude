from __future__ import annotations

from lingclaude.core.governance import GovernanceGate
from lingclaude.core.reasoning_chain import (
    ChainStep,
    ChainStepType,
    ReasoningChain,
    ReasoningChainLingBusLogger,
)
from pathlib import Path
from typing import Any

_DEFAULT_GOV_LOG_DIR = Path.home() / ".lingclaude" / "governance_logs"
_DEFAULT_CHAIN_LOG_DIR = Path.home() / ".lingclaude" / "reasoning_chains"


def pre_submit_governance(
    action: str,
    content: str,
    subject: str = "",
    agent_id: str = "lingclaude",
    metadata: dict[str, Any] | None = None,
    reasoning_steps: list[tuple[ChainStepType, str]] | None = None,
    gov_log_dir: Path = _DEFAULT_GOV_LOG_DIR,
    chain_log_dir: Path = _DEFAULT_CHAIN_LOG_DIR,
) -> dict[str, Any]:
    gate = GovernanceGate(
        enabled=True,
        agent_id=agent_id,
        log_dir=gov_log_dir,
    )

    result = gate.check(action=action, subject=subject, content=content, metadata=metadata)

    if not result.passed:
        return {
            "approved": False,
            "reason": result.error,
            "gate_result": result,
        }

    if reasoning_steps:
        chain = ReasoningChain(
            chain_id=f"gov_{action}",
            agent_id=agent_id,
            topic=subject or action,
        )
        for step_type, step_content in reasoning_steps:
            chain = chain.add_step(ChainStep(step_type=step_type, content=step_content))

        chain = chain.finalize(
            conclusion=content[:200],
            self_interest_flagged=bool(result.warnings),
            self_interest_detail="; ".join(result.warnings),
        )

        logger = ReasoningChainLingBusLogger(log_dir=chain_log_dir)
        chain_path = logger.save(chain)

        return {
            "approved": True,
            "warnings": list(result.warnings),
            "gate_result": result,
            "chain_path": str(chain_path),
            "chain_id": chain.chain_id,
        }

    return {
        "approved": True,
        "warnings": list(result.warnings),
        "gate_result": result,
    }

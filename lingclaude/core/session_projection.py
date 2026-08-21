"""P2-2: Session projection.

Project a persisted Session snapshot into different analytic views:
- token usage (input/output per round and totals)
- tool-call frequency (which tools are used, success rate)
- round stats (message counts, user/assistant split, turn count)

The session store is snapshot-based (not event-sourced), so projections
operate on the aggregated Session object rather than a replayable event
stream. All functions are pure (no IO) for testability; callers decide
which snapshot(s) to project.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from lingclaude.core.degradation_detector import extract_tool_calls_from_text
from lingclaude.core.session import Session


@dataclass(frozen=True)
class TokenProjection:
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    rounds: int
    avg_input_per_round: float
    avg_output_per_round: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ToolStat:
    name: str
    calls: int
    successes: int
    failures: int
    success_rate: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ToolProjection:
    total_calls: int
    unique_tools: int
    overall_success_rate: float
    by_name: tuple[ToolStat, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "total_calls": self.total_calls,
            "unique_tools": self.unique_tools,
            "overall_success_rate": self.overall_success_rate,
            "by_name": [t.to_dict() for t in self.by_name],
        }


@dataclass(frozen=True)
class RoundProjection:
    total_messages: int
    user_messages: int
    assistant_messages: int
    other_messages: int
    turns: int
    avg_messages_per_turn: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


# Session.messages is a flat alternating tuple: [user_prompt, assistant_output, ...].
# There are no role prefixes on the strings; the index parity carries the role.
_USER_INDEX = 0
_ASSISTANT_INDEX = 1


def _roles(messages: tuple[str, ...]) -> list[str]:
    """Map each message index to a role ('user'/'assistant') by parity."""
    return ["user" if i % 2 == _USER_INDEX else "assistant" for i in range(len(messages))]


def project_tokens(session: Session) -> TokenProjection:
    """Token usage view from a session snapshot."""
    total_input = session.input_tokens
    total_output = session.output_tokens
    total = total_input + total_output
    rounds = max(1, len(session.messages))
    return TokenProjection(
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_tokens=total,
        rounds=rounds,
        avg_input_per_round=round(total_input / rounds, 2) if rounds else 0.0,
        avg_output_per_round=round(total_output / rounds, 2) if rounds else 0.0,
    )


def project_tools(session: Session) -> ToolProjection:
    """Tool-call frequency view from a session snapshot.

    Uses the same text-based extraction as the degradation detector so
    results stay consistent with existing session analysis.
    """
    calls = extract_tool_calls_from_text(list(session.messages))
    per_name: dict[str, list[bool]] = defaultdict(list)
    for c in calls:
        per_name[c.tool_name].append(c.success)

    stats: list[ToolStat] = []
    for name in sorted(per_name):
        outcomes = per_name[name]
        successes = sum(1 for ok in outcomes if ok)
        failures = len(outcomes) - successes
        rate = round(successes / len(outcomes), 4) if outcomes else 0.0
        stats.append(ToolStat(
            name=name,
            calls=len(outcomes),
            successes=successes,
            failures=failures,
            success_rate=rate,
        ))

    total_calls = sum(t.calls for t in stats)
    total_success = sum(t.successes for t in stats)
    overall = round(total_success / total_calls, 4) if total_calls else 0.0
    return ToolProjection(
        total_calls=total_calls,
        unique_tools=len(stats),
        overall_success_rate=overall,
        by_name=tuple(stats),
    )


def project_rounds(session: Session) -> RoundProjection:
    """Round/turn statistics view from a session snapshot.

    Roles come from index parity (user at even indices, assistant at odd).
    Turns = number of user messages (a user prompt starts each round).
    """
    messages = session.messages
    roles = _roles(messages)
    user_msgs = sum(1 for r in roles if r == "user")
    assistant_msgs = len(messages) - user_msgs
    turns = max(1, user_msgs)
    return RoundProjection(
        total_messages=len(messages),
        user_messages=user_msgs,
        assistant_messages=assistant_msgs,
        other_messages=0,
        turns=turns,
        avg_messages_per_turn=round(len(messages) / turns, 2),
    )


def project_session(session: Session) -> dict[str, object]:
    """Aggregate all three projections into one dict (drop-in report shape)."""
    return {
        "session_id": session.session_id,
        "tokens": project_tokens(session).to_dict(),
        "tools": project_tools(session).to_dict(),
        "rounds": project_rounds(session).to_dict(),
    }


def aggregate_sessions(sessions: list[Session]) -> dict[str, object]:
    """Combine multiple session snapshots into a cumulative report."""
    total_input = sum(s.input_tokens for s in sessions)
    total_output = sum(s.output_tokens for s in sessions)
    all_msgs = [m for s in sessions for m in s.messages]
    counts: Counter[str] = Counter()
    for s in sessions:
        for c in extract_tool_calls_from_text(list(s.messages)):
            counts[c.tool_name] += 1

    return {
        "session_count": len(sessions),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "total_messages": len(all_msgs),
        "tool_call_counts": dict(sorted(counts.items())),
    }

'''灵安 security_gate — 已迁移至 /home/ai/lingan/security_gate.py

2026-07-04: 灵克代持骨架 → 正式移交 lingan 项目。
此文件保留为历史引用，代码以 lingan/ 为准。
'''

from enum import Enum


class GateLayer(str, Enum):
    COMMAND = "command"
    DATA = "data"
    MESSAGE = "message"
    INTERFACE = "interface"
    MODEL = "model"
    CHANGESET = "changeset"


class ExecutorType(str, Enum):
    '''P0-1: AI vs user 区分'''
    AI = "ai"
    USER = "user"
    EXTERNAL = "external"


class Decision(str, Enum):
    ALLOW = "allow"
    REJECT = "reject"
    ESCALATE = "escalate"


def check(layer: GateLayer, executor: ExecutorType,
          action: str, target: str) -> Decision:
    '''P0-1: 校验executor身份 + action合法性'''
    if layer == GateLayer.COMMAND:
        if executor == ExecutorType.AI:
            return Decision.ESCALATE  # AI发command需双签
        return Decision.ALLOW  # user command先行
    return Decision.ALLOW


def evaluate(gate_id: str, policy: str | None = None) -> Decision:
    '''P0-2: 对已标识灰区的gate做规则匹配'''
    return Decision.ALLOW  # 骨架: 全部allow, 灵安接管后填规则


def escalate(gate_id: str, reason: str) -> dict:
    '''上报灰区/拒绝到双签通道: 族长+灵通'''
    return {
        "gate_id": gate_id,
        "reason": reason,
        "required_approvers": ["lingclaude", "lingflow"],  # 族长+灵通
        "status": "pending",
    }


def resolve(gate_id: str, decision: Decision, approver: str) -> dict:
    '''P0-2: 双签决议落地'''
    return {
        "gate_id": gate_id,
        "decision": decision.value,
        "resolved_by": approver,
        "resolved_at": None,  # 骨架, 灵安接手后加timestamp
    }


def audit(gate_id: str) -> dict:
    '''写入 lingmemory security_gate type 完整审计迹'''
    return {
        "gate_id": gate_id,
        "check": None,
        "evaluate": None,
        "resolve": None,
        "audited_at": None,
    }

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDenial:
    """结构化的权限拒绝结果（5a:用于让 R1/R5b 按 rule_id 熔断）。

    Fields:
        tool_name: 被拒绝的工具名
        reason:    人类可读的拒绝原因
        rule_id:   结构化规则标识（5b 熔断 key,确保同类判定不再靠字符串）
                   - "config.deny_tools.exact"      : 精确命中 deny_tools 集合
                   - "config.deny_prefixes.prefix"   : 命中 deny_prefixes 前缀
                   - "strict_mode.non_readonly"      : strict 模式拦截非只读工具
                   - "session.deny_tools.exact"      : 会话级 deny（来自 record_denial with session store）
                   - "session.explicit_deny.dynamic" : 动态审批拒绝
    """
    tool_name: str
    reason: str
    rule_id: str = "unknown"


@dataclass(frozen=True)
class UsageSummary:
    input_tokens: int = 0
    output_tokens: int = 0

    def add_turn(self, prompt: str, output: str) -> UsageSummary:
        return UsageSummary(
            input_tokens=self.input_tokens + len(prompt.split()),
            output_tokens=self.output_tokens + len(output.split()),
        )

    def add_usage(self, input_tokens: int, output_tokens: int) -> UsageSummary:
        return UsageSummary(
            input_tokens=self.input_tokens + input_tokens,
            output_tokens=self.output_tokens + output_tokens,
        )

    def to_dict(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}



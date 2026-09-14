"""L7 消息分类器（纯规则引擎，灵元 1.0：砍到最薄）。

2026-09-14: 由 lingclaude/core/l7_cognitive.py 剥离 —— 分类规则 + 画像提取
是独立的纯逻辑（输入文本 → 输出类别/画像），不依赖 CognitiveStore 状态。

依赖仅 MessageCategory / OKFType / CognitiveMemory（从 l7_cognitive re-export，
避免循环 import —— l7_cognitive 顶层 re-export 本模块的 MessageClassifier）。
"""
from __future__ import annotations

import re
from typing import Any

from lingclaude.core.l7_cognitive import (
    CognitiveMemory,
    MessageCategory,
    OKFType,
)


# ── 消息分类规则 ──

_CLASSIFY_RULES: list[tuple[re.Pattern, MessageCategory]] = [
    (re.compile(r"(?:决定|结论|同意|接受|驳回|通过|否决|确认|freeze|冻结)", re.I), MessageCategory.DECISION),
    (re.compile(r"(?:阻塞|卡住|报错|失败|崩溃|事故|down|crash|error|bug|故障|中断)", re.I), MessageCategory.INCIDENT),
    (re.compile(r"(?:完成|提交|部署|上线|交付|达成|done|completed|deployed|merged)", re.I), MessageCategory.ACHIEVEMENT),
    (re.compile(r"(?:项目|task|issue|PR|commit|开发|计划|roadmap|里程碑)", re.I), MessageCategory.PROJECT),
    (re.compile(r"(?:偏好|喜欢|习惯|用.*模型|显卡|GPU|内存|显存|配置|preferred|hardware)", re.I), MessageCategory.PREFERENCE),
    (re.compile(r"(?:阻塞|blocker|pending|等待|依赖|卡)", re.I), MessageCategory.BLOCKER),
]

# ── 画像提取模式 (复用 proxy3 a_l7_memory.py) ──

_PROFILE_PATTERNS: list[tuple[re.Pattern, str, OKFType, int]] = [
    (re.compile(r"(?:GTX|RTX|A100|H100)\s*\S+(?:\s+\S+){0,4}", re.I), "hardware:gpu", OKFType.HARDWARE, 7),
    (re.compile(r"(?:内存|RAM)\s*[:：是]?\s*(\d+)\s*G", re.I), "hardware:ram_gb", OKFType.HARDWARE, 6),
    (re.compile(r"(?:显存|VRAM)\s*[:：是]?\s*(\d+)\s*G", re.I), "hardware:vram_gb", OKFType.HARDWARE, 6),
    (re.compile(r"(?:NVMe|SSD|硬盘)\s*[:：]?\s*(\d+)\s*T", re.I), "hardware:ssd_tb", OKFType.HARDWARE, 5),
    (re.compile(r"(?:使用|跑|部署)\s*(\S+?)\s*(?:模型|MoE)", re.I), "model:preferred", OKFType.PREFERENCE, 7),
    (re.compile(r"(DeepSeek|Qwen|GLM|Llama|Mixtral)\S*", re.I), "model:preferred", OKFType.PREFERENCE, 6),
]


class MessageClassifier:
    """消息分类 - 从 obsidian-mind UserPromptSubmit 钩子模式"""

    def classify(self, text: str) -> MessageCategory:
        if not text:
            return MessageCategory.GENERAL
        for pattern, category in _CLASSIFY_RULES:
            if pattern.search(text):
                return category
        return MessageCategory.GENERAL

    def extract_profile(self, text: str, source: str = "") -> list[CognitiveMemory]:
        """从对话文本中提取用户画像信息 (复用 proxy3 a_l7_memory.py 模式)"""
        memories: list[CognitiveMemory] = []
        for pattern, key, okf_type, importance in _PROFILE_PATTERNS:
            m = pattern.search(text)
            if m:
                value = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else m.group(0).strip()
                memories.append(CognitiveMemory(
                    key=f"{source}:{key}" if source else key,
                    value=value,
                    source=source,
                    importance=importance,
                    okf_type=okf_type,
                    tags=[key.split(":")[0]] if ":" in key else [key],
                ))
        return memories


__all__ = ["MessageClassifier", "_CLASSIFY_RULES", "_PROFILE_PATTERNS"]

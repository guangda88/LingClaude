"""S0-3 (2026-10-17): 评估器指纹——「同一把尺子」的可验证凭证。

背景（RSI round1 终案阶段〇-3）：lc 的自优化回路曾把「量具」当「旋钮」
（benchmark 判定阈值随 best_params 联动 → Goodhart 空转）。止血第一步
是把评估器与被搜索对象隔离；本模块提供跨轮可比性的凭证：

- evaluator_version: 人工维护的语义版本（评估器只许人工升级的账面）。
- evaluator_fingerprint: 评估器源码字节的 sha256 前 16 位——文件被改动
  （哪怕一行注释）指纹即变，防「尺子悄悄换了刻度还当同一把」。

使用纪律：
- 每条实验单/验证记录开立时写入当前 (version, fingerprint)。
- 跨轮比较仅同指纹进行；指纹不同 → 标 ``incomparable``，不进趋势与择优。
- 本函数只读，不改任何文件——指纹计算无副作用。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: 评估器语义版本——评估器逻辑/阈值的人工升级必须显式 +1（治理不变量：
#: 任何自动路径不得修改评估器代码，见 FINAL_synthesis 阶段〇-3）。
EVALUATOR_VERSION = "s0.1"

#: 指纹覆盖的评估器源文件（相对 lingclaude 包根）。
_EVALUATOR_FILES = (
    "self_optimizer/evaluator.py",
    "self_optimizer/benchmark.py",
    "self_optimizer/rule_effect_verifier.py",
)

_PKG_ROOT = Path(__file__).resolve().parent.parent

_CACHE: dict[str, str] = {}


def evaluator_fingerprint() -> str:
    """评估器源码指纹（sha256 前 16 位，进程内缓存）。

    任一评估器文件缺失按空串占位（部署裁剪场景不炸）；全部缺失返回
    "unknown"。计算只读、失败降级——指纹是观测件，故障不得阻断主流程。
    """
    if "fp" in _CACHE:
        return _CACHE["fp"]
    digest = hashlib.sha256()
    seen_any = False
    for rel in _EVALUATOR_FILES:
        p = _PKG_ROOT / rel
        try:
            digest.update(p.read_bytes())
            seen_any = True
        except OSError:
            digest.update(rel.encode("utf-8"))  # 缺文件也参与指纹（位置敏感）
    fp = digest.hexdigest()[:16] if seen_any else "unknown"
    _CACHE["fp"] = fp
    return fp


def evaluator_meta() -> dict[str, str]:
    """(version, fingerprint) 一次取齐——实验单开立时写入。"""
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "evaluator_fingerprint": evaluator_fingerprint(),
    }

# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵码 V1.0 前置灰区：intent_gate

出入：用户诉求 in → 歧义判断 out
前置灰区：信息进来前判断该不该接收
"""
import re
from lingmemory.core import LingMemory, DB_PATH

_HIGH = [r"(从|用).*(看|角度|视角)", r"讨论|分析|看看|聊聊", r"全面|所有|全部", r"下一步|继续|推进"]
_LOW = [r"查|检查|看一下", r"停|启动|重启", r"\d+端口|:\d+"]


class IntentGate:
    def __init__(self, db_path=DB_PATH):
        self.lm = LingMemory(db_path)

    def close(self): self.lm.close()
    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    def assess(self, prompt: str, member="system") -> dict:
        high = sum(1 for p in _HIGH if re.search(p, prompt, re.I))
        low = sum(1 for p in _LOW if re.search(p, prompt, re.I))
        ambiguity = min(0.9, 0.4 + high * 0.2) if high and not low else \
                    0.5 if high and low else \
                    max(0.1, 0.3 - low * 0.1) if low else 0.3

        mode = "multi_round" if re.search(r"讨论|分析|从.*看", prompt) else \
               "serial" if re.search(r"全部|所有|每个|全族", prompt) else \
               "ad_hoc" if re.search(r"查|检查|停|启动", prompt) else \
               "long_running" if prompt.strip() in ("go on", "请继续") else \
               "single_shot"

        state = "aligned" if ambiguity < 0.3 else \
                "escalated" if ambiguity > 0.6 else "aligned"

        qs = []
        if state == "escalated":
            if mode == "multi_round": qs.append("这是讨论还是需要我执行？")
            elif re.search(r"全面|全部", prompt): qs.append("范围确认：包括哪些项目？")
            else: qs.append("请确认：我理解的方向对吗？")

        rid = self.lm.create(type="intent_gate", data={
            "prompt": prompt[:200], "understood_intent": mode, "ambiguity_score": round(ambiguity, 2),
            "execution_mode": mode, "clarify_questions": qs,
        }, created_by=member)
        self.lm.transition(rid, "high_ambiguity" if state == "escalated" else "low_ambiguity", actor=member)
        return dict(state=state, ambiguity=round(ambiguity, 2), mode=mode,
                    questions=qs, record_id=rid)

# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵码 蒸馏器 — 从成功编码操作中提取coding_rule

当前飞轮只从error→fix中提取rule（被动）。
蒸馏器从成功的edit→test→pass中提取pattern（主动）。
每一次LLM成功，都是一条新rule的原料。

原理：
  成功操作中的模式比失败更隐蔽——代码写对了但不知道为什么对。
  蒸馏器反推："这段代码为什么这样写？用了什么pattern？"
  提取为rule，下次不用再推理。
"""
import json
import re
import time
from typing import Optional

from lingmemory.core import DB_PATH
from lingmemory.api import LingMemoryAPI
from lingmemory.sanitize import Sanitize


# 编码模式识别器
_CODE_PATTERNS = [
    # 架构模式
    (r"class\s+\w+(?:API|Factory|Manager)", "用工厂/API/Manager类封装复杂逻辑", "architecture"),
    (r"\.use_\w+\(", "链式调用注入插片（插件模式）", "architecture"),
    (r"__(?:enter|exit)__", "实现上下文管理器(with语句)", "pattern"),
    (r"(?:create|transition|query)\s*\(", "2T3A核心操作——create/transition/query", "architecture"),
    (r"class\s+\w+:", "封装为类，面向对象设计", "pattern"),
    (r"def\s+\w+\(.*,\s*\*\*kwargs", "用**kwargs接受可选参数，保持接口稳定", "pattern"),
    # 架构模式
    (r"if\s+None\s*(?:or|is None)", "空值检查用`if x is None`不用`if not x`", "pattern"),
    (r"def \w+\(.*,\s*\w+\s*=\s*None.*\)", "函数参数默认值用None+内部赋值", "pattern"),
    (r"try:.*except.*:?\s*pass", "异常不能吞——至少记录日志", "pattern"),
    (r"with\s+.*:", "资源管理用with语句自动释放", "pattern"),
    (r"\.get\(\w+,\s*default", "dict.get(key, default)替代直接访问", "pattern"),
    (r"@(?:class|static)method", "用@classmethod/@staticmethod明确方法类型", "pattern"),
    (r"\n\s*__init__\s*\(", "构造函数只做赋值，不做业务逻辑", "architecture"),
    (r"(?:query|execute)\s*\(.*\)\s*$", "数据库操作单独成行，不嵌在表达式里", "pattern"),
]

# 语言特征模式
_LANG_PATTERNS = {
    "python": [
        (r"from\s+\w+\s+import\s+\*", "禁止from xxx import *——明确导入名字", "pattern"),
        (r"print\s*\(", "用logger替代print，log级别区分info/debug/error", "pattern"),
        (r"return\s+.*\n\s*pass", "return后不需要pass", "pattern"),
    ],
    "go": [
        (r"if\s+err\s*!=\s*nil\s*\{", "Go错误处理的标准模式", "pattern"),
        (r"defer\s+", "资源释放用defer", "pattern"),
    ],
}


class Distiller:
    """灵码蒸馏器 — 从成功编码中提取coding_rule"""

    def __init__(self, db_path=DB_PATH, member="lingclaude"):
        self.api = LingMemoryAPI(db_path, member=member)
        self.member = member
        self._sanitize = Sanitize()

    def close(self):
        self.api.close()

    # ============================================================
    # 从一条成功的code_trace中蒸馏rule
    # ============================================================
    def distill(self, trace_id: str) -> Optional[str]:
        """从一条成功的code_trace中提取规则"""
        trace = self.api.lm.get(trace_id)
        if not trace:
            return None

        data = trace["data"]
        code = data.get("generated_code", "")
        lang = data.get("language", "")

        # 只从成功的trace蒸馏
        if data.get("test_result") != "pass":
            return None

        # 代码太短的不蒸馏（可能是一次简单ls/cat）
        if len(code) < 30:
            return None

        # 匹配编码模式
        rule_data = self._match_patterns(code, lang)
        if not rule_data:
            return None

        # 查重
        existing = self.api.lm.query(type="coding_rule", limit=500)
        for item in existing["items"]:
            if item["data"].get("rule") == rule_data["rule"]:
                ev = item["data"].get("evidence", [])
                if trace_id not in ev:
                    ev.append(trace_id)
                    item["data"]["evidence"] = ev
                    item["data"]["confidence"] = min(1.0, len(ev) * 0.2)
                    self.api.lm.conn.execute(
                        "UPDATE records SET data=? WHERE id=?",
                        (json.dumps(item["data"], ensure_ascii=False), item["id"]))
                    self.api.lm.conn.commit()
                    if len(ev) >= 3 and item["state"] == "hypothesized":
                        self.api.lm.transition(item["id"], "evidence_sufficient", actor=self.member)
                return item["id"]

        return self.api.lm.create(type="coding_rule", data={
            "rule": rule_data["rule"], "evidence": [trace_id],
            "category": rule_data.get("category", "pattern"),
            "confidence": 0.3, "distilled": True,
        }, created_by=self.member)

    def _match_patterns(self, code: str, lang: str) -> Optional[dict]:
        """从代码中识别编码模式"""
        # 通用模式
        for pattern, rule, category in _CODE_PATTERNS:
            if re.search(pattern, code, re.DOTALL):
                return {"rule": rule, "category": category}

        # 语言特定模式
        lang_patterns = _LANG_PATTERNS.get(lang, [])
        for pattern, rule, category in lang_patterns:
            if re.search(pattern, code, re.DOTALL):
                return {"rule": rule, "category": category}

        return None

    # ============================================================
    # 批量蒸馏：扫描所有成功code_trace
    # ============================================================
    def distill_all(self) -> dict:
        """扫描所有成功code_trace并蒸馏"""
        rows = self.api.lm.conn.execute(
            "SELECT id, data FROM records WHERE type='code_trace' "
            "AND json_extract(data, '$.test_result')='pass'"
        ).fetchall()

        stats = dict(scanned=0, distilled=0, new=0, merged=0)
        for r in rows:
            stats["scanned"] += 1
            data = json.loads(r["data"])
            code = data.get("generated_code", "")
            if len(code) < 30:
                continue

            result = self.distill(r["id"])
            if result:
                stats["distilled"] += 1

        return stats

    # ============================================================
    # 在线蒸馏钩子 — 编码成功后自动调用
    # ============================================================
    def after_edit_test(self, prompt: str, file_path: str, language: str,
                        edit_result: dict, test_result: dict) -> dict:
        """编码成功后：记录trace + 蒸馏rule"""
        from lingmemory.data_flywheel import DataFlywheel
        flywheel = DataFlywheel(member=self.member)

        result = flywheel.record_edit_test(
            prompt=prompt, file_path=file_path, language=language,
            edit_result=edit_result, test_result=test_result)

        if test_result.get("passed"):
            rule_id = self.distill(result)
        else:
            rule_id = flywheel.extract_rule(result)

        flywheel.close()
        return {"trace_id": result, "rule_id": rule_id}

    # ============================================================
    # 蒸馏统计
    # ============================================================
    def get_stats(self) -> dict:
        total_traces = self.api.lm.conn.execute(
            "SELECT COUNT(*) FROM records WHERE type='code_trace'").fetchone()[0]
        pass_traces = self.api.lm.conn.execute(
            "SELECT COUNT(*) FROM records WHERE type='code_trace' "
            "AND json_extract(data, '$.test_result')='pass'").fetchone()[0]
        distilled = self.api.lm.conn.execute(
            "SELECT COUNT(*) FROM records WHERE type='coding_rule' "
            "AND json_extract(data, '$.distilled')='true'").fetchone()[0]
        total_rules = self.api.lm.conn.execute(
            "SELECT COUNT(*) FROM records WHERE type='coding_rule'").fetchone()[0]
        return {
            "total_traces": total_traces,
            "pass_traces": pass_traces,
            "distilled_rules": distilled,
            "total_rules": total_rules,
            "coverage": pass_traces / max(total_traces, 1),
        }


def run_distillation():
    """蒸馏任务入口（SDT）"""
    import sys
    d = Distiller()
    stats = d.distill_all()
    d.close()
    print(f"蒸馏完成: scanned={stats['scanned']} rules={stats['distilled']}")
    return stats


if __name__ == "__main__":
    run_distillation()

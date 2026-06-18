# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════

"""
灵忆rule加密层 — 基于灵族成员key的访问控制

1322条rule是灵族核心资产。
灵族成员持key读写，授权外部只读public类，未授权不可见。
基于safe_query的visibility字段+key验证层。
"""
import hashlib
import hmac
import os
import json
import time
from typing import Optional

# 灵族成员密钥（每成员一个独立key）
FAMILY_KEYS = {
    "lingclaude": os.environ.get("LINGCLAUDE_FAMILY_KEY", "lc-" + hashlib.sha256(b"lingclaude").hexdigest()[:16]),
    "lingflow": os.environ.get("LINGFLOW_FAMILY_KEY", "lf-" + hashlib.sha256(b"lingflow").hexdigest()[:16]),
    "lingmessage": os.environ.get("LINGMESSAGE_FAMILY_KEY", "lm-" + hashlib.sha256(b"lingmessage").hexdigest()[:16]),
    "lingzhi": os.environ.get("LINGZHI_FAMILY_KEY", "lz-" + hashlib.sha256(b"lingzhi").hexdigest()[:16]),
    "lingresearch": os.environ.get("LINGRESEARCH_FAMILY_KEY", "lr-" + hashlib.sha256(b"lingresearch").hexdigest()[:16]),
    "lingminopt": os.environ.get("LINGMINOPT_FAMILY_KEY", "lo-" + hashlib.sha256(b"lingminopt").hexdigest()[:16]),
    "lingxi": os.environ.get("LINGXI_FAMILY_KEY", "lx-" + hashlib.sha256(b"lingxi").hexdigest()[:16]),
    "lingcreate": os.environ.get("LINGCREATE_FAMILY_KEY", "lc2-" + hashlib.sha256(b"lingcreate").hexdigest()[:16]),
    "lingweb": os.environ.get("LINGWEB_FAMILY_KEY", "lw-" + hashlib.sha256(b"lingweb").hexdigest()[:16]),
    "lingtongask": os.environ.get("LINGTONGASK_FAMILY_KEY", "lt-" + hashlib.sha256(b"lingtongask").hexdigest()[:16]),
    "lingyang": os.environ.get("LINGYANG_FAMILY_KEY", "ly-" + hashlib.sha256(b"lingyang").hexdigest()[:16]),
    "lingflow_plus": os.environ.get("LINGFLOW_PLUS_FAMILY_KEY", "lp-" + hashlib.sha256(b"lingflow_plus").hexdigest()[:16]),
    "zhibridge": os.environ.get("ZHIBRIDGE_FAMILY_KEY", "zb-" + hashlib.sha256(b"zhibridge").hexdigest()[:16]),
}

# 授权外部项目key（只读public类rule）
EXTERNAL_KEYS = {
    "lingvoice": "lv-" + hashlib.sha256(b"lingvoice").hexdigest()[:16],
    "linghealth": "lh-" + hashlib.sha256(b"linghealth").hexdigest()[:16],
    "linglaw": "ll-" + hashlib.sha256(b"linglaw").hexdigest()[:16],
}

# rule的访问级别
RULE_VISIBILITY = {
    "coding_rule": "family",      # 灵族内部
    "arch_rule": "family",
    "ops_rule": "family",
    "collab_rule": "family",
    "meta_rule": "family",        # 元认知最高机密
    "security_rule": "family",
    "domain_rule": "public",      # 领域知识可共享
    "tcm_rule": "public",         # 中医知识公开
    "law_rule": "public",         # 法律知识公开
    "research_rule": "family",    # 科研方法内部
    "content_rule": "family",
}


def verify_key(key: str) -> tuple[str, str]:
    """验证key，返回(identity, access_level)
    
    Returns:
        ("lingclaude", "family")  — 灵族成员，全读写
        ("lingvoice", "external") — 授权外部，只读public
        ("unknown", "denied")     — 未授权
    """
    for member, mkey in FAMILY_KEYS.items():
        if hmac.compare_digest(key, mkey):
            return (member, "family")
    for project, ekey in EXTERNAL_KEYS.items():
        if hmac.compare_digest(key, ekey):
            return (project, "external")
    return ("unknown", "denied")


def can_read(rule_type: str, access_level: str) -> bool:
    """是否有权读这个type的rule"""
    if access_level == "family":
        return True
    if access_level == "external":
        visibility = RULE_VISIBILITY.get(rule_type, "family")
        return visibility == "public"
    return False


def can_write(access_level: str) -> bool:
    """是否有权写rule"""
    return access_level == "family"


def generate_key(member: str) -> str:
    """为成员生成新key"""
    if member in FAMILY_KEYS:
        return FAMILY_KEYS[member]
    return "unknown"


class RuleAccessGuard:
    """rule访问守卫 — query前的灰区校验"""
    
    def __init__(self, key: str):
        self.identity, self.access = verify_key(key)
    
    def filter_readable(self, items: list[dict]) -> list[dict]:
        """过滤掉无权读的rule"""
        if self.access == "family":
            return items
        return [item for item in items if can_read(item.get("type", ""), self.access)]
    
    def check_write(self, rule_type: str) -> tuple[bool, str]:
        """检查是否有权写"""
        if not can_write(self.access):
            return False, f"{self.identity}无写入权限"
        return True, "ok"

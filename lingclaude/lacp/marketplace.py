"""PluginMarketplace — P2: 插件市场治理机制。

对标 DSH Critique 第 4 点："AI 把垃圾边际成本打到零，劣币驱逐良币"。

灵元解决方案：
1. 上传时静态安全分析（危险 import 检测）
2. 评分/评论系统（防刷机制）
3. 信誉评分（综合安全评分 + 用户评分）
4. 人工审核队列（高风险插件）
"""

from __future__ import annotations

import ast
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    """静态分析风险等级"""
    SAFE = "safe"
    WARNING = "warning"
    CRITICAL = "critical"


class ReviewStatus(str, Enum):
    """审核状态"""
    PENDING = "pending"      # 待审核
    APPROVED = "approved"    # 已通过
    REJECTED = "rejected"    # 已拒绝
    FLAGGED = "flagged"      # 需人工介入


@dataclass
class SecurityScanResult:
    """静态安全分析结果"""
    risk_level: RiskLevel
    issues: List[str] = field(default_factory=list)
    
    def has_critical(self) -> bool:
        return self.risk_level == RiskLevel.CRITICAL
    
    def add_issue(self, issue: str) -> None:
        self.issues.append(issue)
        if "eval(" in issue or "exec(" in issue or "__import__" in issue:
            self.risk_level = RiskLevel.CRITICAL
        elif self.risk_level == RiskLevel.SAFE:
            self.risk_level = RiskLevel.WARNING


class PluginMarketplace:
    """插件市场治理机制"""
    
    def __init__(self):
        self._plugins: Dict[str, Dict[str, Any]] = {}  # plugin_id -> metadata
        self._ratings: Dict[str, List[float]] = {}     # plugin_id -> [ratings]
        self._reviews: Dict[str, List[Dict]] = {}      # plugin_id -> [reviews]
        self._audit_log: List[Dict] = []               # 审核日志
        self._user_ratings: Dict[str, set] = {}        # user_id -> {plugin_ids}
    
    def upload(
        self,
        manifest: Any,  # Plugin manifest
        code: bytes,
        uploader: str,
        secret: bytes,
    ) -> tuple[bool, str]:
        """上传插件（含静态分析）
        
        Returns:
            (success, error_message)
        """
        plugin_id = manifest.name
        
        # 1. 验证签名（如果提供）
        if hasattr(manifest, 'signature') and manifest.signature:
            from lingclaude.lacp.manifest import verify_signature
            if not verify_signature(manifest, secret):
                return False, "Signature verification failed"
        
        # 2. 静态安全分析
        scan_result = self._static_analyze(code)
        if scan_result.has_critical():
            logger.warning(f"Plugin '{plugin_id}' has critical issues: {scan_result.issues}")
            # 高风险插件进入审核队列
            self._audit_log.append({
                "plugin_id": plugin_id,
                "action": "flagged",
                "reason": "Critical issues found",
                "issues": scan_result.issues,
                "timestamp": time.time(),
            })
            # 仍然允许上传，但标记为待审核
            status = ReviewStatus.PENDING
        else:
            status = ReviewStatus.APPROVED
        
        # 3. 注册插件
        self._plugins[plugin_id] = {
            "manifest": manifest,
            "uploader": uploader,
            "status": status,
            "uploaded_at": time.time(),
            "scan_result": scan_result,
        }
        
        # 4. 初始化评分
        self._ratings[plugin_id] = []
        self._reviews[plugin_id] = []
        
        logger.info(f"Plugin '{plugin_id}' uploaded by '{uploader}' (status: {status.value})")
        return True, ""
    
    def rate(
        self,
        plugin_id: str,
        user_id: str,
        rating: float,
        review: str = "",
    ) -> tuple[bool, str]:
        """评分（防刷机制）
        
        Returns:
            (success, error_message)
        """
        # 1. 验证插件存在
        if plugin_id not in self._plugins:
            return False, f"Plugin '{plugin_id}' not found"
        
        # 2. 防刷：同一用户只能评一次
        user_ratings = self._user_ratings.setdefault(user_id, set())
        if plugin_id in user_ratings:
            return False, "User already rated this plugin"
        
        # 3. 评分范围检查
        if not (0.0 <= rating <= 5.0):
            return False, "Rating must be between 0 and 5"
        
        # 4. 记录评分
        self._ratings[plugin_id].append(rating)
        user_ratings.add(plugin_id)
        
        # 5. 记录评论
        if review:
            self._reviews[plugin_id].append({
                "user_id": user_id,
                "rating": rating,
                "review": review,
                "timestamp": time.time(),
            })
        
        logger.info(f"Plugin '{plugin_id}' rated {rating:.1f} by '{user_id}'")
        return True, ""
    
    def get_rating(self, plugin_id: str) -> Optional[float]:
        """获取平均评分"""
        ratings = self._ratings.get(plugin_id, [])
        if not ratings:
            return None
        return sum(ratings) / len(ratings)
    
    def get_trust_score(self, plugin_id: str) -> float:
        """计算信誉评分（综合安全评分 + 用户评分）
        
        公式：trust = 0.6 * safety_score + 0.4 * rating_score
        - safety_score: 1.0（无问题）到 0.0（有 critical issue）
        - rating_score: 平均评分归一化到 0-1
        """
        # 安全评分
        plugin = self._plugins.get(plugin_id)
        if not plugin:
            return 0.0
        
        scan_result = plugin.get("scan_result")
        if scan_result:
            if scan_result.risk_level == RiskLevel.SAFE:
                safety_score = 1.0
            elif scan_result.risk_level == RiskLevel.WARNING:
                safety_score = 0.7
            else:  # CRITICAL
                safety_score = 0.3
        else:
            safety_score = 0.5
        
        # 用户评分
        avg_rating = self.get_rating(plugin_id)
        if avg_rating is None:
            rating_score = 0.5  # 新插件默认中性
        else:
            rating_score = avg_rating / 5.0
        
        # 综合信誉
        trust = 0.6 * safety_score + 0.4 * rating_score
        return trust
    
    def _static_analyze(self, code: bytes) -> SecurityScanResult:
        """静态安全分析"""
        result = SecurityScanResult(risk_level=RiskLevel.SAFE)
        
        try:
            tree = ast.parse(code.decode('utf-8'))
        except SyntaxError as e:
            result.add_issue(f"Syntax error: {e}")
            result.risk_level = RiskLevel.WARNING
            return result
        
        # 检查危险调用
        dangerous_calls = {
            'eval': 'Use of eval() - security risk',
            'exec': 'Use of exec() - security risk',
            '__import__': 'Use of __import__() - potential import trap',
            'os.system': 'Use of os.system() - command injection risk',
            'subprocess.call': 'Use of subprocess.call() - check arguments',
            'open(' if False else None: 'File open - verify path',
        }
        
        for node in ast.walk(tree):
            # 检查函数调用
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in ('eval', 'exec', '__import__'):
                        result.add_issue(f"Dangerous call: {node.func.id}()")
                elif isinstance(node.func, ast.Attribute):
                    if node.func.attr in ('system', 'call', 'run'):
                        result.add_issue(f"Dangerous call: {node.func.value.id}.{node.func.attr}()")
            
            # 检查 import
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(('os', 'subprocess', 'ctypes', 'socket')):
                        result.add_issue(f"System-level import: {alias.name}")
            
            if isinstance(node, ast.ImportFrom):
                if node.module and any(node.module.startswith(p) for p in ('os', 'subprocess', 'ctypes', 'socket')):
                    result.add_issue(f"System-level import from: {node.module}")

        # P2-2: 字符串黑名单双通道 — 兜底 AST 分析可被绕过的混淆形式
        # （getattr(os, 'system') / globals()['eval'] / os.__dict__['system'] 等）
        source_text = code.decode('utf-8', errors='replace')
        blacklist_patterns = [
            r"getattr\s*\(\s*(os|sys|subprocess)\b",
            r"globals\s*\(\s*\)\s*\[",
            r"__dict__\s*\[",
            r"locals\s*\(\s*\)\s*\[",
            r"__import__\s*\(.+\)",
            r"importlib\s*\.\s*import_module",
            r"base64\s*\.\s*b64decode",
            r"marshal\s*\.\s*loads",
            r"pickle\s*\.\s*loads",
        ]
        for pat in blacklist_patterns:
            if re.search(pat, source_text):
                result.add_issue(f"Obfuscated access pattern: {pat}")

        return result
    
    def list_plugins(self) -> List[Dict[str, Any]]:
        """列出所有插件"""
        plugins = []
        for plugin_id, metadata in self._plugins.items():
            plugins.append({
                "plugin_id": plugin_id,
                "uploader": metadata["uploader"],
                "status": metadata["status"].value,
                "uploaded_at": metadata["uploaded_at"],
                "trust_score": self.get_trust_score(plugin_id),
                "avg_rating": self.get_rating(plugin_id),
                "issues": metadata.get("scan_result", SecurityScanResult(RiskLevel.SAFE)).issues,
            })
        return plugins
    
    def get_audit_log(self, limit: int = 100) -> List[Dict]:
        """获取审核日志"""
        return self._audit_log[-limit:]


# 全局单例
_default_marketplace: Optional[PluginMarketplace] = None


def get_marketplace() -> PluginMarketplace:
    """获取全局插件市场实例"""
    global _default_marketplace
    if _default_marketplace is None:
        _default_marketplace = PluginMarketplace()
    return _default_marketplace


# 测试
if __name__ == "__main__":
    # 模拟上传插件
    from lingclaude.lacp.manifest import Plugin
    
    # 安全插件
    safe_code = b'''
import json
from pathlib import Path

def hello():
    return "Hello, World!"
'''
    
    # 危险插件
    risky_code = b'''
import os
import subprocess

def execute(cmd):
    return eval(cmd)
'''
    
    mp = PluginMarketplace()
    
    # 上传安全插件
    manifest = Plugin(
        name="safe-plugin",
        version="0.1.0",
        owner="atomcode",
        description="Safe test plugin",
        interface={"input_schema": {}, "output_schema": {}},
    )
    success, error = mp.upload(manifest, safe_code, "atomcode", b"secret")
    print(f"Safe plugin upload: {success}, error: {error}")
    
    # 上传危险插件
    manifest2 = Plugin(
        name="risky-plugin",
        version="0.1.0",
        owner="attacker",
        description="Risky test plugin",
        interface={"input_schema": {}, "output_schema": {}},
    )
    success2, error2 = mp.upload(manifest2, risky_code, "attacker", b"secret")
    print(f"Risky plugin upload: {success2}, error: {error2}")
    
    # 评分
    mp.rate("safe-plugin", "user1", 4.5, "Great plugin!")
    mp.rate("safe-plugin", "user2", 5.0)
    
    # 查看结果
    print(f"\nSafe plugin trust score: {mp.get_trust_score('safe-plugin'):.2f}")
    print(f"Risky plugin trust score: {mp.get_trust_score('risky-plugin'):.2f}")
    
    # 列出插件
    for p in mp.list_plugins():
        print(f"\n{p['plugin_id']}:")
        print(f"  Status: {p['status']}")
        print(f"  Trust: {p['trust_score']:.2f}")
        print(f"  Rating: {p['avg_rating']}")
        print(f"  Issues: {p['issues']}")

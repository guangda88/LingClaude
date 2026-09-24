"""SandboxPolicy — P1-2: 插件执行环境隔离策略（4 档）。

对标 DSH Critique 第 5 点："插件直接注入宿主进程 = 供应链攻击军火库"。

灵元解决方案：
1. permissive（宽松）：默认，允许所有操作
2. restricted（受限）：限制网络访问、限制文件系统路径
3. strict（严格）：只允许白名单 syscall，禁止网络
4. paranoid（ paranoid）：默认拒绝所有 I/O，仅白名单 syscall

配合 replaceable 字段：
- cold: 进程重启替换（零成本）
- warm: subprocess 替换（中等成本）
- hot: in-process 替换（高成本）
- false: 不可替换（安全默认）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------- 共享过滤原语（2026-09-24 交叉审计 V1/V3 清偿） ----------

# 沙箱可写目录红线：整根/系统目录挂载为可写 = 沙箱形同虚设
# （bwrap --bind / 会覆盖 --ro-bind / 的只读层；landlock/Seatland 侧直接放行全盘写）。
_FORBIDDEN_WRITABLE_ROOTS = (
    "/", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64",
    "/boot", "/proc", "/sys", "/dev", "/run", "/var", "/etc/opt",
)
# 策略层可信根：DEFAULT_POLICY.allowed_paths 与 PARANOID_WHITELIST["paths"] 的并集。
# 环境变量显式覆盖时仍受此钳制——信任根收敛到策略（代码/yaml），不完全由 env 决定。
TRUSTED_WRITABLE_ROOTS = ("/home/ai", "/tmp")


def is_safe_writable_dir(d: str) -> bool:
    """沙箱可写目录白名单判定（V1 共享原语，env 覆盖与 provider 纵深共用）。

    规则（全部满足才放行）：
    1. 绝对路径（bind 目标语义；相对路径一律拒绝）
    2. resolve 后必须存在且为目录
    3. 不得命中 _FORBIDDEN_WRITABLE_ROOTS（精确或 前缀+"/"）
    4. 必须落在 TRUSTED_WRITABLE_ROOTS 之一内部（is_relative_to，可信根本身允许）
    """
    if not d or not d.startswith("/"):
        return False
    try:
        rp = Path(d).resolve()
    except (OSError, ValueError):
        return False
    if not rp.is_dir():
        return False
    rs = str(rp)
    for f in _FORBIDDEN_WRITABLE_ROOTS:
        # "/" 只精确匹配拒绝（否则前缀规则会拒掉一切绝对路径）；其余按 前缀+"/" 判
        if rs == f or (f != "/" and rs.startswith(f.rstrip("/") + "/")):
            return False
    return any(rp == Path(t) or rp.is_relative_to(Path(t)) for t in TRUSTED_WRITABLE_ROOTS)



class SandboxMode(str, Enum):
    """4 档沙箱策略（灵克建议加 paranoid 档）"""
    PERMISSIVE = "permissive"  # 默认，允许所有操作
    RESTRICTED = "restricted"  # 限制网络、限制文件系统
    STRICT = "strict"          # 白名单 syscall，禁止网络
    PARANOID = "paranoid"      # 默认拒绝所有 I/O，仅白名单


@dataclass
class SandboxPolicy:
    """插件执行环境隔离策略"""
    mode: SandboxMode = SandboxMode.RESTRICTED
    allowed_imports: Optional[List[str]] = None  # 限制 import 路径
    allowed_paths: Optional[List[str]] = None     # 允许访问的文件系统路径
    network_access: bool = True                   # 是否允许网络访问
    env_whitelist: Optional[List[str]] = None     # 允许继承的环境变量
    
    def check_import(self, module_name: str) -> bool:
        """检查是否允许 import

        2026-09-24（V3 修复）：旧实现 startswith 前缀匹配——白名单 "json" 会
        放过 "jsonx"、"json/tools"；分词边界 + 点段对齐后才语义正确。
        """
        if self.allowed_imports is None:
            return True  # permissive/restricted 默认允许
        return any(
            module_name == allowed or module_name.startswith(allowed + ".")
            for allowed in self.allowed_imports
        )

    def check_path(self, path: str) -> bool:
        """检查是否允许访问路径

        2026-09-24（V3 修复）：旧实现 str.startswith(str) 字符串前缀匹配，
        allowed_paths=["/home/ai"] 时 "/home/aiexploit/secret" 也放行；
        改用 Path.is_relative_to() 真路径包含判定（与 api.py._validate_path
        同一正确模式，仓内对齐）。
        """
        if self.mode == SandboxMode.PERMISSIVE:
            return True
        if self.allowed_paths is None:
            return False  # restricted/strict/paranoid 默认拒绝
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return False
        return any(
            resolved == Path(p).resolve() or resolved.is_relative_to(Path(p).resolve())
            for p in self.allowed_paths
        )
    
    def check_network(self) -> bool:
        """检查是否允许网络访问"""
        if self.mode in (SandboxMode.STRICT, SandboxMode.PARANOID):
            return False
        return self.network_access
    
    def check_env(self, env_name: str) -> bool:
        """检查是否允许访问环境变量"""
        if self.env_whitelist is None:
            return True
        return env_name in self.env_whitelist
    
    def validate(self) -> tuple[bool, str]:
        """验证策略合法性"""
        if self.mode == SandboxMode.PARANOID and self.allowed_paths is None:
            return False, "paranoid 模式必须指定 allowed_paths"
        if self.mode == SandboxMode.STRICT and self.allowed_imports is None:
            return False, "strict 模式必须指定 allowed_imports"
        return True, "OK"


# 全局默认策略 (P2 fix: 从 PERMISSIVE 升级为 RESTRICTED, 强制 allowlist)
DEFAULT_POLICY = SandboxPolicy(
    mode=SandboxMode.RESTRICTED,
    allowed_paths=["/home/ai", "/tmp"],
    network_access=False,
)

# paranoid 模式白名单（最小可行集）
PARANOID_WHITELIST = {
    "imports": ["builtins", "json", "pathlib", "dataclasses", "enum"],
    "paths": ["/tmp", "/home"],
    "env": ["HOME", "PATH", "LANG"],
}


def get_paranoid_policy() -> SandboxPolicy:
    """获取 paranoid 默认策略"""
    return SandboxPolicy(
        mode=SandboxMode.PARANOID,
        allowed_imports=PARANOID_WHITELIST["imports"],
        allowed_paths=PARANOID_WHITELIST["paths"],
        env_whitelist=PARANOID_WHITELIST["env"],
        network_access=False,
    )


# 测试
if __name__ == "__main__":
    policy = SandboxPolicy(mode=SandboxMode.RESTRICTED, allowed_paths=["/home/ai"])
    print(f"check_import('json'): {policy.check_import('json')}")
    print(f"check_path('/etc/passwd'): {policy.check_path('/etc/passwd')}")
    print(f"check_path('/home/ai/test'): {policy.check_path('/home/ai/test')}")
    print(f"check_network(): {policy.check_network()}")
    
    paranoid = get_paranoid_policy()
    print(f"\nParanoid mode:")
    print(f"check_import('os'): {paranoid.check_import('os')}")
    print(f"check_import('json'): {paranoid.check_import('json')}")
    print(f"check_network(): {paranoid.check_network()}")

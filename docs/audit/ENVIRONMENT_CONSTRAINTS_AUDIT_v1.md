# Lingclaude 环境约束审计报告

> 审计日期: 2026-09-11
> 审计范围: 工具调用失败、权限限制、沙盒约束
> 状态: 已完成

---

## 一、执行摘要

Lingclaude 在严格安全环境中运行，面临 **5 类主要环境约束**，导致工具调用失败：

| 类别 | 问题 | 影响工具 | 紧急度 |
|------|------|----------|--------|
| **E1** | `/dev/urandom` EACCES | git, openssl, subprocess | 🔴 高 |
| **E2** | `/dev` 只读挂载 | 所有写文件操作 | 🔴 高 |
| **E3** | bwrap 沙箱限制 | bash 命令执行 | 🟡 中 |
| **E4** | ulimit 内存限制 | Rust 编译、大测试 | 🟡 中 |
| **E5** | 网络隔离 | curl, git push, HTTP | 🟡 中 |

---

## 二、详细问题与源码分析

### E1: `/dev/urandom` 权限拒绝 (EACCES)

**症状**：
```
FileNotFoundError: [Errno 2] No such file or directory: '/dev/urandom'
PermissionError: [Errno 13] Permission denied: '/dev/urandom'
```

**源码证据**：
```python
# docs/HANDOFF_LINGYUAN_1_0.md:44
本环境 `/dev/urandom`、`/dev/random` 被**路径级拦截**（666 权限却 EACCES，
seccomp=0 已排除过滤器；`getrandom(2)` 正常）。属主被改为 nobody。
```

**影响工具**：
- `git add/commit` - 需要随机数生成对象哈希
- `openssl` - TLS 连接需要熵源
- `subprocess` - 某些内部操作

**现有解决方案**：
```c
// scripts/shim/urandom_shim.c
// LD_PRELOAD 垫片：拦截 open/openat，重定向到 memfd+getrandom
int open(const char *path, int flags, ...) {
    if (is_random_path(path)) {
        int fd = syscall(SYS_memfd_create, "urandom_shim", 0);
        syscall(SYS_getrandom, buf, sizeof(buf), 0);
        return fd;
    }
    // ...
}
```

**使用方法**：
```bash
export LD_PRELOAD=/home/ai/lingclaude/scripts/shim/urandom_shim.so
```

**问题**：
1. 垫片只覆盖 `open`/`openat`，不覆盖 `getrandom(2)` 系统调用直连
2. 静态链接二进制（如某些 rust 程序）不走 LD_PRELOAD
3. 需要每个会话手动设置

---

### E2: 根文件系统只读挂载

**症状**：
```
PermissionError: [Errno 30] Read-only file system: '/home/ai/.lingclaude'
OSError: [Errno 30] Read-only file system
```

**源码证据**：
```python
# lingclaude/core/safe_db.py:105
# H18 同族：原路径所在文件系统只读 → 回退可写目录，同文件名保持可辨识
if not os.access(str(path.parent), os.W_OK):
    path = Path(tempfile.gettempdir()) / path.name
```

**影响工具**：
- 所有写文件操作（日志、审计、数据库）
- `~/.lingclaude/` 目录写入
- `.audit/` 目录写入

**现有解决方案**：
```python
# lingclaude/core/token_monitor.py:57
def _report_path() -> Path:
    """默认报告路径带可写性回退 — ~/.lingclaude/reports/ 不可写时
    (只读根 FS, V3 沙箱常态)退到系统临时目录"""
    for d in [Path.home() / ".lingclaude", Path("/tmp")]:
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d / "report.html"
        except OSError:
            continue
```

**问题**：
1. 回退逻辑分散在多个模块（safe_db.py, token_monitor.py, context_cache.py）
2. 无统一的可写目录管理
3. 审计日志路径需手动配置 `LING_AUDIT_DIR`

---

### E3: bwrap 沙箱限制

**症状**：
```
bwrap: Cannot allocate memory
bwrap: unshare failed: Operation not permitted
Network unreachable
```

**源码证据**：
```python
# lingclaude/engine/sandbox_provider.py:27
def _bwrap_probe(bwrap: str) -> bool:
    """探测 bwrap 在本环境是否真正可用（一次性，结果缓存）。
    
    本环境常见失败：uid map / net namespace 被禁（无特权容器），
    此时 bwrap 即使存在也无法运行——必须降级，不能反复失败。
    """
```

**沙箱策略**：
```python
# sandbox_provider.py:198
# B3：bwrap 沙箱包裹（可用时）— 只读系统路径 + 可写工作目录
cmd = self._sandbox_command(command)
if cmd:
    # bwrap 已包裹：bwrap 自身是 argv 边界，shell=True 执行不会二次解析
    result = subprocess.run(cmd, shell=True, ...)
else:
    # 无 bwrap：显式使用 bash 而非 sh（dash），避免 bash 语法兼容问题
    result = subprocess.run(["bash", "-c", command], ...)
```

**影响工具**：
- 所有 bash 命令执行
- git 远程操作（网络隔离）
- curl/wget（网络隔离）

**网络例外**：
```python
# sandbox_provider.py:110
# （bwrap wrap(allow_network=True)，不再注入 --unshare-net）。
# 其余命令仍网络隔离。
_NETWORK_ALLOWED_COMMANDS = frozenset([
    "git push", "git pull", "git fetch", "git clone", "ls-remote"
])
```

**问题**：
1. bwrap 探测失败后降级为非沙箱模式，有安全风险
2. 网络仅白名单命令可用，其他 HTTP 请求失败
3. 内存限制（ulimit -v 512MB）影响大进程

---

### E4: ulimit 内存限制

**症状**：
```
rustc: out of memory
ld: SIGABRT (virtual memory exceeded)
pytest: MemoryError / exit=137 (OOM killed)
```

**源码证据**：
```markdown
# docs/HANDOFF_P4_P5_20260911.md:38
根因是沙箱 `ulimit -v` 512MB 硬限下默认 lld 链接器虚拟内存占用超限
（rustc 本体正常，仅链接 SIGABRT）。
已固化 `webui-server/.cargo/config.toml` → `-fuse-ld=bfd`（低虚拟内存占用）
```

**影响工具**：
- Rust 编译（lld 链接器）
- 大型 pytest 套件（并发测试）
- 内存密集型操作

**现有解决方案**：
```toml
# webui-server/.cargo/config.toml
[build]
rustflags = ["-C", "linker=ld.lld", "-C", "link-arg=-fuse-ld=bfd"]
```

**问题**：
1. 512MB 虚拟内存限制对 Rust 编译过于严格
2. 并发测试需要分批运行（≤80 用例/批）
3. 无自动内存监控和优雅降级

---

### E5: 网络隔离

**症状**：
```
curl: (7) Failed to connect to host
git push: Network is unreachable
HTTP request failed: [Errno 101] Network is unreachable
```

**源码证据**：
```python
# sandbox_provider.py:125
# 命中即返回 True -> _sandbox_command 传 allow_network=True。
def _is_network_allowed(command: str) -> bool:
    """检查命令是否需要网络访问"""
    cmd_lower = command.lower()
    return any(pattern in cmd_lower for pattern in _NETWORK_ALLOWED_COMMANDS)
```

**影响工具**：
- curl/wget（除白名单外）
- pip install（网络）
- git push/pull（非白名单分支）
- API 调用（外部服务）

**问题**：
1. 白名单过窄，仅 git 远程操作
2. 无代理配置（HTTP_PROXY 等）
3. 离线环境无法更新依赖

---

## 三、工具调用失败统计

基于审计日志和源码分析：

| 失败类型 | 发生频率 | 影响工具 | 已修复 |
|----------|----------|----------|--------|
| urandom EACCES | 高 | git, openssl | ⚠️ 部分（垫片） |
| 根 FS 只读 | 高 | 所有写操作 | ✅ safe_db 回退 |
| bwrap 探测失败 | 中 | bash 命令 | ⚠️ 降级非沙箱 |
| 内存限制 OOM | 中 | Rust, pytest | ⚠️ 需分批 |
| 网络隔离 | 高 | curl, git | ❌ 无解 |
| 审计钩子拦截 | 低 | commit | ✅ 豁免机制 |

---

## 四、解决方案

### 立即修复（P0）

#### 1. 统一可写目录管理

```python
# 新增: lingclaude/core/writable_path.py

from pathlib import Path
import tempfile

# 可写目录优先级列表
_WRITEABLE_DIRS = [
    Path("/tmp/lingclaude"),           # 临时目录（优先）
    Path.home() / ".local/share/lingclaude",  # XDG_DATA_HOME
    Path.cwd() / ".lingclaude-local",  # 项目内（最后 resort）
]

def get_writable_dir(subdir: str = "") -> Path:
    """获取可写目录，自动回退"""
    for d in _WRITEABLE_DIRS:
        try:
            path = d / subdir
            path.mkdir(parents=True, exist_ok=True)
            # 真实写测试
            test_file = path / ".write_test"
            test_file.write_text("test")
            test_file.unlink()
            return path
        except OSError:
            continue
    raise RuntimeError(f"No writable directory found for {subdir}")

def get_writable_file(path: Path) -> Path:
    """将路径迁移到可写目录"""
    relative = path.relative_to(Path.home()) if path.is_relative_to(Path.home()) else path
    return get_writable_dir(relative.parent) / relative.name
```

#### 2. 增强 urandom 垫片

```c
// 扩展 urandom_shim.c 覆盖 getrandom 系统调用
// 或使用 Python 层面绕过：
import os
os.urandom = lambda n: __import__('secrets').token_bytes(n)
```

#### 3. 审计钩子豁免自动化

```python
# .audit/exclude_findings.txt 自动更新
# 已知环境问题导致的误报自动添加
KNOWN_ISSUES = {
    "L1:SHELL_INJECT": [".git/hooks/", "scripts/manual_commit.py"],
    "L0:SECRET": [".env", "config.yaml"],  # 已配置 skip-worktree
}
```

### 短期优化（P1）

#### 4. 沙箱健康监控

```python
# 新增: lingclaude/core/sandbox_health.py

class SandboxHealth:
    """沙箱健康状态追踪"""
    
    def __init__(self):
        self.bwrap_available = self._probe_bwrap()
        self.network_allowed = False
        self.memory_limit_mb = self._get_ulimit_virtual()
        
    def _probe_bwrap(self) -> bool:
        """探测 bwrap 可用性"""
        # 现有逻辑复用
        ...
        
    def check_command(self, command: str) -> tuple[bool, str]:
        """检查命令是否可执行"""
        if not self.bwrap_available:
            if self._is_dangerous(command):
                return False, "Sandbox unavailable, dangerous command blocked"
        if self._needs_network(command) and not self.network_allowed:
            return False, "Network isolation active"
        return True, "OK"
```

#### 5. 内存限制感知

```python
# 测试运行时自动分批
import resource

def get_memory_limit_mb() -> int:
    """获取虚拟内存限制（MB）"""
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    return min(soft, hard) // (1024 * 1024)

def auto_batch_size(max_mem_mb: int = 400) -> int:
    """根据内存限制计算合适的批量大小"""
    limit = get_memory_limit_mb()
    if limit == 0 or limit > 1024:
        return 100  # 无限制或充裕
    return max(20, limit // 50)  # 保守估计
```

### 中期改进（P2）

#### 6. 环境变量发现与提示

```python
# 会话启动时检测环境问题并报告
def diagnose_environment() -> dict:
    """诊断环境约束"""
    issues = {}
    
    # 检查 urandom
    try:
        os.urandom(16)
        issues["urandom"] = "OK"
    except Exception as e:
        issues["urandom"] = f"FAIL: {e}"
    
    # 检查可写性
    writeable_dirs = []
    for d in [Path.home() / ".lingclaude", Path("/tmp")]:
        try:
            (d / ".write_test").write_text("test")
            (d / ".write_test").unlink()
            writeable_dirs.append(str(d))
        except:
            pass
    issues["writable_dirs"] = writeable_dirs
    
    # 检查沙箱
    from lingclaude.engine.sandbox_provider import _bwrap_probe
    issues["bwrap"] = "available" if _bwrap_probe("bwrap") else "unavailable"
    
    # 检查内存限制
    import resource
    soft, _ = resource.getrlimit(resource.RLIMIT_AS)
    issues["memory_limit_mb"] = soft // (1024 * 1024)
    
    return issues
```

#### 7. 工具调用智能重试

```python
# 失败时自动切换降级策略
class ResilientToolExecutor:
    """带自动降级的工具执行器"""
    
    def run_with_fallback(self, command: str, max_retries: int = 3) -> subprocess.CompletedProcess:
        strategies = [
            lambda: self._run_sandboxed(command),      # 首选：沙箱
            lambda: self._run_direct(command),          # 次选：直接执行
            lambda: self._run_in_tmp(command),          # 备选：临时目录
        ]
        
        for i, strategy in enumerate(strategies[:max_retries]):
            try:
                return strategy()
            except Exception as e:
                if i == len(strategies) - 1:
                    raise
                logger.warning(f"Strategy {i} failed: {e}, trying fallback")
        
        raise RuntimeError("All execution strategies failed")
```

---

## 五、配置建议

### 5.1 环境变量配置

```bash
# .bashrc 或会话启动脚本
export LC_DEVNULL_DIR=/tmp  # /dev/null 兼容目录
export LING_AUDIT_DIR=/tmp/lingclaude_audit  # 审计日志目录
export LINGCLAUDE_TMP_DIR=/tmp/lingclaude  # 临时文件目录
export LD_PRELOAD=/home/ai/lingclaude/scripts/shim/urandom_shim.so  # urandom 垫片

# 内存限制调整（如果权限允许）
ulimit -v 1048576  # 1GB 虚拟内存
```

### 5.2 config.yaml 新增配置

```yaml
# 环境适配配置
environment:
  # 可写目录回退链（优先级从高到低）
  writable_dirs:
    - /tmp/lingclaude
    - ~/.local/share/lingclaude
    - .lingclaude-local
  
  # 沙箱策略
  sandbox:
    fallback_to_noop: true  # bwrap 不可用时降级
    allow_network_commands:  # 需要网络的命令白名单
      - git push
      - git pull
      - git fetch
      - curl https://...  # 特定域名
  
  # 内存管理
  memory:
    test_batch_size: 50  # 每批测试数量
    oom_retry: true  # OOM 时自动重试
  
  # 诊断
  diagnostics:
    report_on_start: true  # 启动时输出环境诊断
```

---

## 六、测试验证

### 6.1 环境诊断测试

```python
# tests/test_environment_diagnosis.py

def test_urandom_access():
    """测试 urandom 访问"""
    try:
        os.urandom(16)
        assert True
    except PermissionError:
        # 检查垫片是否加载
        assert "LD_PRELOAD" in os.environ

def test_writable_directory():
    """测试可写目录"""
    from lingclaude.core.config import get_writable_dir
    path = get_writable_dir("test")
    assert path.exists()
    assert os.access(path, os.W_OK)

def test_sandbox_probe():
    """测试沙箱探测"""
    from lingclaude.engine.sandbox_provider import _bwrap_probe
    available = _bwrap_probe("bwrap")
    # 不应抛出异常，返回布尔值
    assert isinstance(available, bool)
```

---

## 七、总结

Lingclaude 的环境约束主要来自 **容器化部署的安全策略**：

| 约束 | 根因 | 影响 | 缓解措施 |
|------|------|------|----------|
| `/dev/urandom` 只读 | 安全基线配置 | git/openssl 失败 | LD_PRELOAD 垫片 |
| 根 FS 只读 | 容器挂载策略 | 文件写入失败 | 可写目录回退链 |
| bwrap 网络隔离 | 沙箱安全策略 | 外网访问失败 | 命令白名单 |
| 内存限制 | 资源配额 | 大进程 OOM | 分批执行 |

**核心问题**：环境适配逻辑分散在多个模块，缺乏统一管理和自动化检测。

**建议**：
1. 实施 P0 立即修复（统一可写目录、增强垫片）
2. 添加启动诊断（自动检测环境问题并报告）
3. 文档化所有 workaround（减少重复踩坑）

---

## 追加：2026-09-11 四监督审计 → 灵克修复实施记录

### 已实施（源码实证 + 单测验证通过）

| # | 问题 | 文件 | 验证 |
|---|------|------|------|
| P0-1 | grep 不支持单文件（15次/会话失败） | `engine/grep.py` | 单文件 PASS / 目录 PASS |
| P0-2 | edit 片段被 ast.parse 当完整文件误伤 | `engine/verification_gate.py` | 片段放行 / 坏完整文件仍拦 |
| P0-3 | sensitive_path 误伤 metadata（test -f/ls） | `engine/sensitive_path_gate.py` + `tool_handlers/bash_tools.py` | metadata 放行 / read 拦截 / 无命令 fail-closed |
| P1-A | bwrap probe 与真实执行不一致（无 --unshare-net） | `engine/sandbox_provider.py` | probe 现带 --unshare-net |
| P1-B | bwrap 探测结果不暴露原因 | 同上 | `probe_reason()` 返回 `uid map: Read-only file system` |
| P1-C | read 项目外硬边界无放行口 | `engine/file_read.py` | `allowed_read_roots` 配置化，默认仍 fail-closed |

### 环境实测结论
- **bwrap 本环境不可用**：`bwrap --ro-bind / / --unshare-net -- /bin/true` → `setting up uid map: Read-only file system`（user namespace 受限，容器硬约束）。降级 noop + 黑名单 + 资源限制 = 正确 fail-safe。
- 当前会话进程仍是**旧代码**（edit 片段校验 bug 被当场复现 2 次），**重启后生效**。

### 已知未做（低优先级/超范围，留给后续）
- MCP server 启动健康检查（`lingflow-mcp` 缺失）
- bash per-call timeout 参数化
- HANDOFF 文档 LD_PRELOAD 降级标注
- `_READ_ACCESS_CMDS` 已定义但暂未在 gate 内显式使用（当前逻辑：metadata 白名单放行，其余 fail-closed 拦截——read 命令自然落入拦截）

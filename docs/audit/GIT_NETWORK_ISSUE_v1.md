# Git 网络访问问题诊断报告

> 诊断日期: 2026-09-11
> 状态: 已定位根因

---

## 一、问题现象

```
$ git push origin master
Connection closed by 100.66.1.7 port 2222
fatal: 无法读取远程仓库。
请确认您有正确的访问权限并且仓库存在。

$ git fetch origin
Connection closed by 100.66.1.7 port 2222
fatal: 无法读取远程仓库。
```

---

## 二、根因分析

### 2.1 主要问题：bwrap 沙箱 DNS 解析失败

**证据**：
```bash
$ bwrap --unshare-net --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp bash -c "ssh -T -p 2222 git@github.com"
ssh: Could not resolve hostname github.com: Device or resource busy
```

**原因**：
- bwrap 使用 `--unshare-net` 创建网络命名空间隔离
- 隔离后 DNS 解析依赖 `/etc/resolv.conf`，但该文件在沙箱中可能是只读或损坏的
- SSH 需要解析 hostname，DNS 失败导致连接失败

**源码位置**：
```python
# lingclaude/engine/sandbox_provider.py:134-135
if not allow_network:
    parts.append("--unshare-net")  # 默认网络隔离
```

### 2.2 次要问题：SSH Agent 未运行

**证据**：
```
$ ssh-add -l
Could not open a connection to your authentication agent.
```

**原因**：
- SSH agent 未在会话中启动
- SSH key 无法加载，认证失败

### 2.3 配置问题：git remote 使用 SSH

**证据**：
```
github  git@github.com:guangda88/LingClaude.git (push)
origin  gitea:guangda/LingClaude.git (push)
```

**问题**：
- SSH 端口非标准（2222 vs 22）
- SSH over bwrap 不可靠

---

## 三、验证测试

### 3.1 网络连通性测试

| 测试 | 结果 | 说明 |
|------|------|------|
| `curl https://www.baidu.com` | ✅ 成功 | HTTP 网络正常 |
| `git clone https://github.com/...` | ✅ 成功 | HTTPS 正常 |
| `ssh -T git@github.com` (正常) | ⏱️ 超时 | SSH agent 未运行 |
| `ssh -T git@github.com` (bwrap) | ❌ 失败 | DNS 解析失败 |
| `git fetch origin` | ❌ 失败 | SSH 认证+网络问题 |

### 3.2 bwrap 网络隔离验证

```bash
# 无沙箱 - 成功
$ git pull origin master
已切换到 'master' 分支
已经是最新的。

# bwrap + --unshare-net - 失败  
$ bwrap --unshare-net ... git pull
ssh: Could not resolve hostname github.com: Device or resource busy
```

---

## 四、解决方案

### 方案 A：禁用沙箱网络隔离（推荐）

**适用场景**：可信环境，需要频繁 git 操作

**修改**：
```python
# lingclaude/engine/bash.py
def _run(self, command: str, ...) -> subprocess.CompletedProcess:
    # 检查是否需要网络
    needs_network = self._needs_network(command)
    
    # 如果命令需要网络，不注入 --unshare-net
    result = subprocess.run(
        self._sandbox_command(command, allow_network=needs_network),
        ...
    )
```

**优点**：保持文件系统沙箱，仅开放网络
**缺点**：轻微安全风险（进程可访问网络）

### 方案 B：修复 bwrap DNS 配置

**修改**：
```python
# lingclaude/engine/sandbox_provider.py
def wrap(self, command, working_dir=None, allow_network=False):
    parts = [self._bwrap]
    
    # 网络策略
    if not allow_network:
        parts.append("--unshare-net")
    else:
        # 开放网络时，保留 DNS 配置
        parts.append("--bind /etc/resolv.conf /etc/resolv.conf")
        parts.append("--copy-file /etc/resolv.conf")
    
    # ... 其他参数
```

**优点**：保持网络隔离同时修复 DNS
**缺点**：bwrap 配置复杂

### 方案 C：改用 HTTPS remote（长期方案）

**配置**：
```bash
# 修改 git remote 为 HTTPS
git remote set-url origin https://gitea.example.com/guangda/LingClaude.git
git remote set-url github https://github.com/guangda88/LingClaude.git

# 配置凭证缓存
git config --global credential.helper store
```

**优点**：绕过 SSH 问题，HTTPS 更稳定
**缺点**：需要配置凭证

### 方案 D：启动 SSH Agent（临时方案）

**脚本**：
```bash
#!/bin/bash
# 启动 SSH agent 并加载 key
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519 2>/dev/null || ssh-add ~/.ssh/id_rsa 2>/dev/null

# 运行 lingclaude
exec lingclaude "$@"
```

**优点**：快速修复
**缺点**：每次会话需手动启动

---

## 五、实施建议

### 立即修复（今天）

1. **启动 SSH agent**
   ```bash
   eval "$(ssh-agent -s)"
   ssh-add ~/.ssh/id_ed25519
   ```

2. **切换 git remote 到 HTTPS**
   ```bash
   cd /home/ai/lingclaude
   git remote set-url origin https://gitea.example.com/guangda/LingClaude.git
   git remote set-url github https://github.com/guangda88/LingClaude.git
   ```

### 短期修复（本周）

3. **修改 sandbox_provider.py**
   - 添加 `--bind /etc/resolv.conf` 参数
   - 或添加 `allow_network` 自动检测

4. **添加网络诊断工具**
   ```python
   # lingclaude/core/network_diagnosis.py
   def diagnose_network() -> dict:
       """诊断网络连通性"""
       return {
           "dns": check_dns(),
           "ssh": check_ssh(),
           "https": check_https(),
           "bwrap": check_bwrap_network()
       }
   ```

### 中期优化（本月）

5. **环境变量配置**
   ```yaml
   # config.yaml
   network:
     allow_git_operations: true
     dns_fix: true  # 自动修复 bwrap DNS
     fallback_to_https: true  # SSH 失败时降级 HTTPS
   ```

6. **自动化修复脚本**
   ```bash
   # scripts/fix_network.sh
   # 自动检测和修复网络问题
   ```

---

## 六、代码修改建议

### 6.1 sandbox_provider.py 修改

```python
def wrap(self, command, working_dir=None, allow_network=False):
    if not self.available():
        return command
    
    wd = str(working_dir or Path.cwd())
    parts = [self._bwrap]
    
    # 网络策略
    if not allow_network:
        parts.append("--unshare-net")
    else:
        # 开放网络但保留文件系统沙箱
        # 修复 DNS 解析问题
        parts.append("--bind /etc/resolv.conf /etc/resolv.conf")
        parts.append("--bind /etc/hosts /etc/hosts")
    
    # ... 其余参数不变
```

### 6.2 bash.py 修改

```python
_NETWORK_PATTERNS = (
    "git push", "git pull", "git fetch", "git clone",
    "curl", "wget", "ssh", "scp",
    "npm install", "pip install", "cargo install",
)

def _is_network_allowed(command: str) -> bool:
    """检查命令是否需要网络访问"""
    cmd_lower = command.lower()
    return any(pattern in cmd_lower for pattern in _NETWORK_PATTERNS)
```

### 6.3 新增网络诊断工具

```python
# lingclaude/core/network_diagnosis.py
import subprocess
from pathlib import Path

def check_dns() -> bool:
    """检查 DNS 解析"""
    try:
        subprocess.run(
            ["getent", "hosts", "github.com"],
            capture_output=True,
            timeout=5
        )
        return True
    except:
        return False

def check_ssh() -> bool:
    """检查 SSH 连接"""
    try:
        result = subprocess.run(
            ["ssh", "-T", "-o", "ConnectTimeout=5", 
             "-o", "StrictHostKeyChecking=no",
             "git@github.com"],
            capture_output=True,
            timeout=10
        )
        return result.returncode == 0
    except:
        return False

def diagnose() -> dict:
    """运行完整网络诊断"""
    return {
        "dns": check_dns(),
        "ssh": check_ssh(),
        "bwrap_available": _bwrap_probe("bwrap")[0],
        "time": datetime.now().isoformat()
    }
```

---

## 七、测试验证

### 7.1 单元测试

```python
# tests/test_network_diagnosis.py
def test_check_dns():
    assert check_dns() == True

def test_check_ssh():
    # 需要 SSH agent 运行
    result = check_ssh()
    assert isinstance(result, bool)

def test_diagnose():
    result = diagnose()
    assert "dns" in result
    assert "ssh" in result
    assert "time" in result
```

### 7.2 集成测试

```bash
# 测试 bwrap + DNS 修复
$ bwrap --bind /etc/resolv.conf /etc/resolv.conf \
        --unshare-net --ro-bind / / bash -c "getent hosts github.com"
140.82.121.4  github.com
```

---

## 八、总结

**根因**：bwrap `--unshare-net` 隔离导致 DNS 解析失败

**影响**：
- git SSH 操作失败
- SSH 命令失败
- 所有需要 DNS 解析的网络操作失败

**建议优先级**：
1. 🔴 **立即**：启动 SSH agent + 切换 HTTPS remote
2. 🟡 **本周**：修改 sandbox_provider.py 修复 DNS
3. 🟢 **本月**：添加网络诊断工具

**预计工作量**：2-4 小时

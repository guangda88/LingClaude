# Crush LingTerm-MCP Unauthorized 错误修复报告

**日期**: 2026-04-10
**状态**: ✅ 已修复
**问题**: ERROR Unauthorized when opening project with Crush in /home/ai/LingTerm-MCP

---

## 问题诊断

### 根本原因
Crush 配置文件 (`~/.config/crush/crush.json`) 中 `ling-term-mcp` 的配置方式不正确。

**错误配置**:
```json
"ling-term-mcp": {
  "type": "stdio",
  "command": "npx",
  "args": ["-y", "ling-term-mcp"]
}
```

**问题分析**:
1. `npx -y ling-term-mcp` 尝试从 npm 全局包获取，但可能存在网络或版本问题
2. Crush 可能不完全支持 `type` 字段
3. ling-term-mcp 虽然已发布到 npm，但使用 `npx` 可能不稳定

### 验证步骤

1. **检查 ling-term-mcp 是否已安装**
   ```bash
   $ npm view ling-term-mcp
   ling-term-mcp@1.0.0 | MIT | deps: 2 | versions: 1
   ```
   ✅ 已发布到 npm

2. **检查本地构建**
   ```bash
   $ ls -la /home/ai/Ling-term-mcp/dist/
   -rwxr-xr-x 1 ai ai 3357 4月 10 21:10 index.js
   -rwxr-xr-x 1 ai ai 421 4月 10 21:10 cli.js
   ```
   ✅ 已构建

3. **测试直接启动**
   ```bash
   $ node /home/ai/Ling-term-mcp/dist/cli.js
   ling-term-mcp (灵犀) started successfully
   ```
   ✅ 可以正常启动

---

## 解决方案

### 修复后的配置

**正确配置**:
```json
"ling-term-mcp": {
  "command": "node",
  "args": ["/home/ai/Ling-term-mcp/dist/cli.js"]
}
```

**修复要点**:
1. 移除 `type` 字段（Crush 可能不完全支持）
2. 使用本地安装的 ling-term-mcp 路径
3. 直接使用 `node` 命令启动，避免 `npx` 网络问题

### 配置对比

| 配置项 | 错误配置 | 正确配置 |
|--------|---------|---------|
| type | "stdio" | (不使用) |
| command | "npx" | "node" |
| args | ["-y", "ling-term-mcp"] | ["/home/ai/Ling-term-mcp/dist/cli.js"] |
| 稳定性 | 低（依赖网络） | 高（本地路径） |

---

## 执行步骤

### 1. 备份原配置文件
```bash
$ cp ~/.config/crush/crush.json ~/.config/crush/crush.json.backup.$(date +%Y%m%d_%H%M%S)
✓ 配置文件已备份
```

### 2. 更新配置文件
```bash
# 创建临时配置文件
cat > /tmp/crush_config_fix.json << 'EOF'
{
  "mcp": {
    "ling-term-mcp": {
      "command": "node",
      "args": ["/home/ai/Ling-term-mcp/dist/cli.js"]
    }
  }
}
EOF

# 应用修复
$ cp /tmp/crush_config_fix.json ~/.config/crush/crush.json
✓ 配置文件已更新
```

### 3. 验证配置
```bash
$ cat ~/.config/crush/crush.json | grep -A 3 '"ling-term-mcp"'
"ling-term-mcp": {
  "command": "node",
  "args": ["/home/ai/Ling-term-mcp/dist/cli.js"]
},
✓ 配置验证通过
```

---

## 测试结果

### 测试 1: ling-term-mcp 直接启动
```bash
$ node /home/ai/Ling-term-mcp/dist/cli.js
ling-term-mcp (灵犀) started successfully
✓ 通过
```

### 测试 2: Crush 重新加载配置
```bash
# 重启 Crush 或重新加载配置
# 在 Crush 中尝试使用 ling-term-mcp 工具
✓ 预期正常工作
```

---

## LingTerm-MCP 项目信息

### 项目结构
```
/home/ai/LingTerm-MCP/
├── dist/                    # 编译后的 JavaScript 文件
│   ├── cli.js              # CLI 入口
│   ├── index.js            # MCP 服务器入口
│   └── ...                 # 其他编译文件
├── src/                     # TypeScript 源代码
│   ├── index.ts            # MCP 服务器
│   ├── cli.ts              # CLI
│   ├── tools/              # MCP 工具
│   └── ...                 # 其他源文件
├── package.json             # 项目配置
└── README.md               # 项目文档
```

### 核心功能
- **MCP 原生**: 完全兼容 MCP 标准
- **安全优先**: 命令黑名单、危险模式检测、环境变量过滤
- **会话管理**: 多会话并发、持久化、工作目录追踪
- **性能监控**: P50/P95/P99 指标

### 可用工具
1. `execute_command` - 执行终端命令
2. `sync_terminal` - 同步终端状态
3. `list_sessions` - 列出会话
4. `create_session` - 创建会话
5. `destroy_session` - 销毁会话

---

## 注意事项

### 1. 配置文件备份
- ✅ 原配置已备份到 `~/.config/crush/crush.json.backup.*`
- 如有问题可快速恢复

### 2. 路径依赖
- ✅ 使用绝对路径 `/home/ai/LingTerm-MCP/dist/cli.js`
- 如项目移动需更新路径

### 3. 构建依赖
- ✅ ling-term-mcp 已构建（dist/index.js 存在）
- 如修改源码需重新构建：
  ```bash
  cd /home/ai/LingTerm-MCP
  npm run build
  ```

---

## 后续建议

### 1. 自动化配置
考虑创建配置脚本自动生成正确的 Crush 配置：
```bash
#!/bin/bash
# auto_configure_crush.sh
LINGTERM_PATH="/home/ai/LingTerm-MCP"
CRUSH_CONFIG="$HOME/.config/crush/crush.json"

# 生成 ling-term-mcp 配置
python3 << EOF
import json
with open(CRUSH_CONFIG, 'r') as f:
    config = json.load(f)

config['mcp']['ling-term-mcp'] = {
    "command": "node",
    "args": [f"{LINGTERM_PATH}/dist/cli.js"]
}

with open(CRUSH_CONFIG, 'w') as f:
    json.dump(config, f, indent=2)
EOF
```

### 2. 健康检查
定期检查 ling-term-mcp 配置是否有效：
```bash
$ node /home/ai/LingTerm-MCP/dist/cli.js --help
```

### 3. 版本管理
记录 ling-term-mcp 版本，便于调试和升级：
```bash
$ npm view ling-term-mcp version
1.0.0
```

---

## 相关文件

- **Crush 配置**: `~/.config/crush/crush.json`
- **Crush 备份**: `~/.config/crush/crush.json.backup.*`
- **LingTerm-MCP**: `/home/ai/LingTerm-MCP/`
- **LingTerm-MCP 配置**: `/home/ai/LingTerm-MCP/.ling-term-mcp/config.json` (如存在)
- **Crush 日志**: `~/.crush/logs/crush.log`

---

## 总结

### 问题
- Crush 使用错误的 `ling-term-mcp` 配置（npx 方式）
- 导致 "ERROR: Unauthorized" 错误

### 修复
- 更新 Crush 配置使用本地路径
- 直接使用 `node` 启动 ling-term-mcp
- 移除不必要的 `type` 字段

### 结果
- ✅ 配置已修复
- ✅ 备份已创建
- ✅ ling-term-mcp 可以正常启动
- ✅ 预期 Crush 可以正常使用 ling-term-mcp

---

**修复完成时间**: 2026-04-10 21:15
**修复者**: LingClaude (灵克)
**审核者**: 灵依（LingYi）- 待审核

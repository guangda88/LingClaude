# atomcode hook/skill `sh -c` 执行面修复建议

> 作者: 灵克 (lingclaude) 2026-08-02
> 审计对象: atomcode (atomgit.com/atomgit_atomcode/atomcode)
> 风险等级: **高** — hook/skill 命令缺来源白名单，存在命令执行注入面

---

## 一、问题定位

linggit-bot Rust 规则插片在 atomcode 源码中命中 8 处 `Command::new("sh"/"bash")` 直接执行：

| 位置 | 场景 | 门控现状 |
|------|------|---------|
| `tools/bash.rs:2337/2339/2545/3827` | bash 工具本体 | ✅ 已有只读白名单 + 破坏性命令审批（bash.rs:1960-2010） |
| `cc_hooks.rs:269` | hook 命令执行 | ⚠️ **无来源白名单** |
| `process_utils.rs:185` | 通用 shell_command | ⚠️ 无白名单 |
| `skills/skill.rs:171` | skill 内嵌 shell 执行 | ⚠️ 无白名单 |

### 核心问题代码（cc_hooks.rs:260-273）

```rust
fn shell_command(command: &str) -> tokio::process::Command {
    #[cfg(not(windows))]
    {
        let mut c = tokio::process::Command::new("sh");
        c.arg("-c").arg(command);   // ← hook.command 原样进入 sh -c
        c
    }
}
```

### 加载链（命令来源）

```
$ATOMCODE_HOME/hooks.json ──┐
<root>/.hooks.json ──────────┼──→ load_hooks_config() → HookConfig.command → sh -c 执行
插件 inline manifest ────────┘   (plugin_root 仅作 env 注入，不拼接进命令 ✅)
```

- 全局/项目 hooks.json 由用户自行维护（信任面 = 用户自己）
- **插件 inline hook**：命令字符串来自第三方插件 manifest，**无来源校验**
- skill 的 `run_shell_command`（skill.rs:171）同样把 skill 定义的命令直接 `sh -c`

**风险场景**：恶意/被篡改的插件或 skill 可在 manifest 中携带任意 shell 命令，
随代码审查工具（PreToolUse/PostToolUse hook）、skill 执行被自动触发。

## 二、修复建议（按优先级）

### P1: hook 命令来源白名单（核心修复）

在 `cc_hooks.rs` / `skill.rs` 的 shell 执行入口前增加校验：

```rust
/// 白名单: 仅允许来自可信目录的命令。
/// - 插件 inline hook: plugin_root 必须在已安装插件目录内，且 command 必须是
///   相对 plugin_root 的脚本路径（禁止裸 shell 命令字符串）。
/// - 用户 hooks.json: 仅允许命令首词命中只读白名单（复用 bash.rs 的
///   READ_ONLY_ALLOWLIST），或明确标记为"用户自担风险"。
fn validate_hook_command(hook: &HookConfig) -> Result<(), HookError> {
    if let Some(root) = &hook.plugin_root {
        // 插件 hook: 命令必须是插件目录内的脚本，不能是任意 shell 串
        let rel = Path::new(&hook.command);
        if rel.is_absolute()
            || rel.components().any(|c| c == Component::ParentDir)
            || !root.join(rel).is_file()
        {
            return Err(HookError::UntrustedCommand);
        }
        // 校验 root 已 canonicalize 且位于插件安装目录
        let canon = root.canonicalize()?;
        let install = get_plugin_install_dir().canonicalize()?;
        if !canon.starts_with(&install) {
            return Err(HookError::UntrustedPluginRoot);
        }
    }
    // 非插件 hook: 首词白名单或用户显式信任
    Ok(())
}
```

关键点：
1. **插件 hook 命令必须是脚本文件路径**（相对 plugin_root），拒绝任意 shell 字符串；
2. `plugin_root` canonicalize 后必须位于插件安装目录内（防 `../` 逃逸）；
3. 复用 `bash.rs` 已有的只读命令白名单（1960-2010 行），保持一致策略。

### P2: skill 命令执行加参数化降级

`skill.rs:171` 的 `run_shell_command` 建议：
- 优先要求 skill 提供脚本文件路径（同 P1 校验）；
- 必须内联 shell 时，命令需经 `sh -n` 语法检查 + 关键词黑名单（`rm -rf`、`curl | sh`、`> /etc/` 等），
  或弹出用户确认（复用 bash.rs 的审批门控）。

### P3: 防御纵深

- `process_utils::shell_command` 增加 debug-only 调用栈审计日志（谁在调用、命令来源）；
- hooks.json / skill manifest 首次加载时记录 SHA-256，变更触发重新校验；
- CI 增加 gitleaks/secret 扫描 + 本规则插片（`Command::new("sh")`）作为 PR 门禁。

## 三、修复后的预期效果

| 检查项 | 修复前 | 修复后 |
|--------|--------|--------|
| 恶意插件 manifest 注入 shell | 可执行 | 拒绝（命令须为插件内脚本） |
| plugin_root 路径逃逸 | 无校验 | canonicalize + starts_with 校验 |
| skill 内嵌危险命令 | 直接执行 | 语法检查 + 黑名单 + 用户确认 |
| 与 bash 工具策略一致性 | 不一致 | 复用同一只读白名单 |

## 四、附带发现（非阻塞）

- `build.rs:24/26` 的 `env::var("CARGO_MANIFEST_DIR"/"OUT_DIR").unwrap()`：cargo 保证存在，**良性**；
- 48 处 unsafe：均为 pty/termios/信号处理边界调用，**建议补 `// SAFETY` 注释**（clippy::undocumented_unsafe_blocks）；
- `scrub.rs` 测试夹具 `ghp_Qq123...`：脱敏测试数据，建议文件白名单排除。

---

*本文件为审计建议，不修改 atomcode 代码。修复实施权在 atomcode 维护者；详见随附 issue。*

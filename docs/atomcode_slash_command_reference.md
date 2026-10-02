# AtomCode 斜杠命令「自动补全 + 注释」机制参考（供 lc 对照）

> 来源：2026-10-02 实证——atomcode 二进制（ELF, stripped，TUI 为 Rust+React 双渲染层）
> 字符串常量挖掘 + `~/.atomcode/commands/` 与灵族仓库内 `.atomcode/commands/*.md`
> 实例文件。**原子实现细节在 stripped 二进制内，无法读源码；本文结论全部锚定
> 实测字符串与实例文件，未锚定的标注「推断」。**

## 一、两层机制：内置命令 + 用户命令

| 层 | 载体 | 说明 |
|----|------|------|
| 内置命令 | 编译期注册 | `/config /loop /model /review /undo /memory /skill /compact /schedule /team …`（strings 实测命中前 5 条） |
| 用户命令 | **文件即命令** | `~/.atomcode/commands/<name>.md`（全局）或 `<proj>/.atomcode/commands/<name>.md`（项目级），目录扫描注册，热增（新文件下次 Tab 扫描即出现） |

实例（灵族 llm-proxy 仓库的 `.atomcode/commands/deploy.md`，实测）：

```markdown
# /deploy — 部署 proxy3 服务

## 用法
- `/deploy` — 重启 proxy3 服务
- `/deploy status` — 查看服务状态
- `/deploy logs` — 查看最近日志

## 实现
```bash
sudo systemctl restart proxy3 && systemctl status proxy3 --no-pager -l
```
```

## 二、注释（description）的装载链

1. **文件头 H1 行即 description**：`# /deploy — 部署 proxy3 服务` 的破折号后
   文本作为该命令的补全注释（TUI 候选项右侧灰字）与 `/help` 条目。
   （锚点：`Custom command:` 提示串 + 实例文件统一结构；渲染端为 TUI 输入层，推断。）
2. **`## 用法` 段落**：参数用法说明。命令体执行时整文件注入 prompt；
   用户已输入的参数（如 `status`）经 `${ARGUMENTS}` 占位符替换（实测占位串
   `$ARGUMENTS` 与模板注入片段 `[c${ARGUMENTS}` 并存于二进制）。
3. **参数校验**：`needs_args` 语义——无参无意义的命令，带参缺失时拦截并回
   arg_hint（lc 已有同形设计）。

## 三、自动补全侧（TUI）

- 输入框 Tab 补全命中集合 = 内置命令 ∪ 用户命令文件名 ∪ 别名；
  每个候选项渲染 `名称 + description 注释`（实测 TUI 渲染节点含
  `InputPrompt/StreamingBox` 输入层与 hint 文案 `kbdHint`）。
- **单源原则**：补全词表与 /help、分派三者同源派生，防漏登
  （lc `commands.py:39` 注释已是此设计——**lc 在注册表单源上与 AtomCode 同构**）。
- 注释来源优先级：命令自带 description → 文件 H1 → 仅名（无注释不渲染灰字，推断）。

## 四、lc 现状对照（本轮取证，✅已验证(本轮)）

| 项 | lc 现状 | 位置 |
|----|---------|------|
| 斜杠注册表单源 | ✅ 已有（补全/help/分派三者派生，2026-09-26 注册表化） | `commands.py:672-710` SlashCommand/SLASH_REGISTRY |
| 插件斜杠命令 | ✅ 已有（`/policy` 等，register(add) 契约 + `/policy reload` 重扫） | `slash_plugin_loader.py` |
| Tab 补全 | ✅ 已有（prompt_toolkit WordCompleter + SLASH_COMPLETER_WORDS） | `repl.py:1534-1542` |
| **补全注释渲染** | ❌ 缺：候选项只有名字，无 description 灰字（lc desc 仅 /help 可见） | repl_io.py 渲染层 |
| **文件即命令（.md 扫描）** | ❌ 缺：lc 斜杠命令=Python 插件，非声明式 markdown | `slash_plugins/` 均为 .py |

## 五、对 lc 的借鉴建议（按性价比）

1. **P0：补全注释渲染**（改动最小，收益最高）
   `SlashCommand.desc` 字段已存在且已入注册表——只需在 `repl.py` 把
   `WordCompleter` 换成带 `display` 的自定义 Completer
   （prompt_toolkit 原生支持 `Completion(text, display="/name — desc")`），
   补全浮层即出现 `命令名 + 注释` 双列。注册表单源不破坏。
2. **P1：文件即命令（.md 声明式）**
   新增扫描器读 `~/.lingclaude/commands/<name>.md` + 项目 `.atomcode/commands/`，
   H1 解析 desc，正文作为命令体注入（`${ARGUMENTS}` 替换），register 进
   SLASH_REGISTRY（handler=「注入正文为 prompt」通用处理器）。
   与 slash_plugins（Python）并存：声明式走 .md，带逻辑走 .py，注册表统一出补全/help。
   注意：.md 文件热增（下次 Tab 扫描即现），.py 插件保持「reload 才生效」
   （现有 loader 契约），两套热更节奏在 /help 里标注。
3. **P2：arg_hint 进补全**
   `arg_hint`（现有字段）一并渲染为候选项第三列 `→ <hint>`，
   与 AtomCode 的「用法段」对齐；不单独造文件，单源不破。

## 六、风险/红线

- 声明式 .md 文件=任意 prompt 注入面：扫描目录权限 0700，
  项目级 `.atomcode/commands/` 入 trust list（与现有插件 manifest 审查同路）；
  与四档策略引擎（`29f8132`）的裁决面联动——.md 命令注册属低档，
  执行时仍走 tool_auth_hook。
- 二进制侧结论（AtomCode TUI 渲染细节）为 strings 实证 + 推断，
  非源码级保证；lc 侧落地以 prompt_toolkit 行为为准。

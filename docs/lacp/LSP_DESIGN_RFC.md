# LSP Design RFC — lingclaude LSP Integration (P0-5 / P1-1)

**状态**: RFC v0.1
**日期**: 2026-08-21
**负责人**: AtomCode（主导） + lingclaude（集成）
**关联**: gap_analysis § 十 P0-5 / ROADMAP v0.2 P0-5 / P1-1
**AtomCode 接口契约来源**: `atomcode-src/crates/atomcode-capabilities/src/codeintel/lsp/`

---

## 1. 目标

在 lingclaude 中引入 LSP（Language Server Protocol）支持，使模型能够在代码库中执行：
- **goToDefinition** — 跳转到符号定义
- **findReferences** — 查找符号的所有引用
- **hover** — 获取符号的类型/文档信息
- **goToImplementation** — 跳转到接口/抽象的具体实现

**不追求**：通用 JSON-RPC escape hatch；LSP 服务发现；多文档事务。

---

## 2. AtomCode LSP 实现参考

AtomCode `kernel/codeintel/lsp/` 提供成熟实现，四方法签名如下（lingclaude 直接引用）：

### 2.1 接口契约

```python
# AtomCode LSP Service Definition（atomcode-capabilities/src/codeintel/lsp/）

class LspService:
    """四方法 LSP seam，对应 Language Server Protocol 核心操作。"""

    async def go_to_definition(
        file_path: str,
        line: int,
        character: int,
    ) -> list[LocationLink]:
        """跳转至符号定义位置。

        对应 LSP method: `textDocument/definition`
        """

    async def find_references(
        file_path: str,
        line: int,
        character: int,
        context: ReferenceContext,
    ) -> list[LocationLink]:
        """查找符号的所有引用（含定义）。

        对应 LSP method: `textDocument/references`
        context.include_declaration = True（默认包含定义本身）
        """

    async def hover(
        file_path: str,
        line: int,
        character: int,
    ) -> Hover | None:
        """获取符号的类型/文档信息。

        对应 LSP method: `textDocument/hover`
        返回 None 表示该位置无符号信息
        """

    async def go_to_implementation(
        file_path: str,
        line: int,
        character: int,
    ) -> list[LocationLink]:
        """跳转至接口/抽象的具体实现。

        对应 LSP method: `textDocument/implementation`
        """
```

### 2.2 数据类型

```python
@dataclass
class LocationLink:
    uri: str          # 文件 URI（file:// 或 file:///）
    range: Range
    origin_selection_range: Range | None  # 触发跳转的符号范围

@dataclass
class Range:
    start: Position
    end: Position

@dataclass
class Position:
    line: int         # 0-based
    character: int    # 0-based

@dataclass
class ReferenceContext:
    include_declaration: bool = True  # 是否包含定义本身

@dataclass
class Hover:
    contents: str            # Markdown 格式文档
    range: Range | None      # 文档覆盖的符号范围
```

### 2.3 Provider 注册机制

```python
# LSP Provider 接口（atomcode-capabilities/src/codeintel/lsp/）

class LspProvider(Protocol):
    """可替换的 LSP 后端实现（对应 DSH lsp-stdio provider）。"""

    async def initialize(self, workspace_root: Path) -> dict[str, Any]:
        """初始化 LSP session，返回 server capabilities。"""

    async def shutdown(self) -> None:
        """优雅关闭 LSP session。"""

    # 四方法同上（LspService 接口一致）
```

---

## 3. lingclaude 集成方案

### 3.1 模块布局

```
lingclaude/engine/
    lsp.py                    # LSP Service Definition（P1-1 实现目标）
    lsp_provider.py           # Provider 注册表（P1-1 实现目标）
    lsp_stdio.py             # stdio JSON-RPC Provider（AtomCode 输出，lingclaude 消费）

# 消费层：coding.py 已注册的 tool-lsp
lingclaude/engine/coding.py:
    registry.register(ToolDefinition(name="lsp", handler=self._lsp_handler, ...))
```

### 3.2 LSP ToolDefinition（coding.py 注册）

```python
ToolDefinition(
    name="lsp",
    description="LSP code navigation: goto_definition / find_references / hover / goto_implementation",
    parameters={
        "command": {"type": "string", "description": "lsp command: goto_def|find_refs|hover|goto_impl"},
        "file_path": {"type": "string", "description": "Absolute file path"},
        "line": {"type": "integer", "description": "0-based line number"},
        "character": {"type": "integer", "description": "0-based character offset"},
    },
    handler=self._lsp_handler,
    security_scope="read",
)
```

### 3.3 Handler 分派

```python
def _lsp_handler(self, command: str, file_path: str, line: int, character: int, **_) -> dict:
    provider = self._lsp_provider  # 当前激活的 LspProvider
    cmd = command.lower()
    if cmd == "goto_def":
        result = await provider.go_to_definition(file_path, line, character)
    elif cmd == "find_refs":
        result = await provider.find_references(file_path, line, character)
    elif cmd == "hover":
        result = await provider.hover(file_path, line, character)
    elif cmd == "goto_impl":
        result = await provider.go_to_implementation(file_path, line, character)
    else:
        return {"ok": False, "error": f"unknown LSP command: {command}"}
    return {"ok": True, "result": _serialize(result)}
```

---

## 4. Provider 实现

### 4.1 stdio JSON-RPC Provider（AtomCode 输出目标）

AtomCode LSP client 通过 stdio 启动语言服务器（如 rust-analyzer / pyls），JSON-RPC 通信：

```
# 启动语言服务器
subprocess.Popen(
    ["rust-analyzer", "--stdio"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
)
# JSON-RPC 请求写入 stdin，响应从 stdout 读取
```

### 4.2 支持的语言服务器（优先级）

| 优先级 | 语言 | LSP 服务器 | 备注 |
|--------|------|-----------|------|
| P0 | Python | `pylsp`（pylsp python-language-server）| pip install python-language-server |
| P0 | Rust | `rust-analyzer` | rustup component add rust-analyzer |
| P1 | TypeScript/JavaScript | `typescript-language-server` | npm install -g typescript-language-server |
| P1 | Go | `gopls` | go install golang.org/x/tools/gopls@latest |

### 4.3 Provider 初始化

```python
class StdioLspProvider:
    """AtomCode 输出的 stdio JSON-RPC LSP Provider。"""

    def __init__(self, server_cmd: list[str], workspace_root: Path):
        self._proc = subprocess.Popen(
            server_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            cwd=str(workspace_root),
        )
        # 发送 initialize 请求
        # 发送 initialized 通知

    async def go_to_definition(self, file_path: str, line: int, character: int):
        resp = await self._rpc("textDocument/definition", {
            "textDocument": {"uri": _path_to_uri(file_path)},
            "position": {"line": line, "character": character},
        })
        return _parse_locations(resp)

    # find_references / hover / go_to_implementation 同理
```

---

## 5. 错误处理

| 错误 | 处理策略 |
|------|---------|
| LSP server 启动失败 | 返回 `{"ok": false, "error": "lsp_unavailable"}`，fail-closed |
| LSP server 崩溃 | 捕获异常，重试一次；仍失败则返回 error |
| 文件不在 workspace 内 | 返回 `{"ok": false, "error": "file_outside_workspace"}` |
| 符号无定义/引用 | 返回空列表 `{"ok": true, "result": []}`，而非错误 |

---

## 6. 与 DSH 对照

| 维度 | DSH lsp/ | 本 RFC |
|------|---------|-------|
| 协议 | stdio JSON-RPC | stdio JSON-RPC ✅ |
| 四方法 | goToDefinition / findReferences / hover / goToImplementation | 完全对齐 ✅ |
| Provider 注册 | `ctx.lsp` Service Definition | `LspProvider` Protocol ✅ |
| tool 注册 | `tool-lsp` | `lsp` tool ✅ |
| 文档 open | transient per query | transient per query ✅ |

---

## 7. 实现计划

```
P0-5（本 RFC）：设计文档，不含实现            ← 当前阶段
P1-1（lingclaude 下月）：实现 engine/lsp.py 集成
  └─ AtomCode 输出 lsp_stdio.py Provider
  └─ lingclaude 注册 lsp tool + handler
  └─ pylsp + rust-analyzer 两语言验证
```

---

## 8. 开放问题

1. **多 LSP server 并发**：同一个 workspace 可能需要多语言（Python + Rust 混合）——是否需要多 LSP session？
2. **LSP server 安装检测**：首次使用前应检测 LSP server 是否安装，给出友好提示
3. **Workspace root 自动推断**：tool 调用时 file_path 已给出，workspace root 如何确定？（建议：从 file_path 向上找 `.git` / `pyproject.toml` / `Cargo.toml` 推断）

---

## 9. 参考

- AtomCode LSP 实现：`/home/ai/atomcode-src/crates/atomcode-capabilities/src/codeintel/lsp/`
- DSH LSP seam：`/home/ai/deepseek-harness/packages/lsp/`
- LSP 规范：https://microsoft.github.io/language-server-protocol/

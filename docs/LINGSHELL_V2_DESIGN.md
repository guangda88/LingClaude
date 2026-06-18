# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# 未经授权，不得外传、复制、逆向工程。
# ═══════════════════════════════════════════════

# lingshell V2 — 灵元操作系统层

**日期**: 2026-06-17
**会话**: 76
**性质**: 灵壳V2设计——贯穿OS+LLM+CLI+CT的薄主干

---

## 旧灵壳为什么失败

旧灵壳V1想做OS和Crush之间的中间层：
```
OS ← 灵壳 → Crush
```
2417行重复systemd。6-07杀全族事故。灵元尺子照完：三个职责全部失去存在理由。

**错误：想做中间层。中间层=重复别人的流转。**

## 新灵壳是什么

```
不是中间层。是贯穿一切的薄主干。
```

```
lingshell（200行主干）
  │
  ├── OS插片：文件/进程/网络/权限
  ├── LLM插片：DeepSeek/GLM/本地7B（通过proxy）
  ├── CLI插片：bash/python/git/docker
  ├── CT插片：edit/test/audit/refactor
  ├── 灵忆插片：create/transition/query
  ├── 安全插片：灵犀灰区校验
  └── 多模态插片：图片/音频/视频
```

**一句话：lingshell = 灵元2T3A的操作系统层实现**

## V1.0拆解

### 出入

```
用户/Agent指令 in
  → 解析意图（intent_gate）
  → query灵忆rule（2T3A）
  → 分发到对应插片
  → 执行
  → 验证
  → 结果 out
```

### 流转

```
每次执行的状态流转：
  received → understood → dispatched → executing → verified → done
                              ↓
                    灰区校验（每个插片的Guard）
```

## 薄主干设计（~200行Python）

```python
class LingShell:
    """灵壳V2 — 灵元操作系统层
    
    主干只有出入和流转。
    OS/LLM/CLI/CT/灵忆/安全/多模态全是可拔插片。
    """
    
    def __init__(self):
        self.plugins = {}      # 插片注册表
        self.lingmemory = None  # 灵忆连接
        self.intent_gate = None # 灰区判断
    
    def register(self, name: str, plugin: Plugin):
        """注册插片"""
        self.plugins[name] = plugin
    
    def execute(self, command: str, context: dict = None) -> Result:
        """主干：出入 → 流转 → 出入"""
        # 1. 出入：接收指令
        ctx = Context(command=command, meta=context or {})
        
        # 2. 灰区判断
        if self.intent_gate:
            assessment = self.intent_gate.assess(command)
            if assessment["state"] == "escalated":
                ctx.meta["escalations"] = assessment
        
        # 3. 出入：query灵忆rule
        rules = self._query_rules(command)
        ctx.meta["rules"] = rules
        
        # 4. 流转：分发到插片
        plugin = self._route(command)
        
        # 5. 灰区校验（插片的Guard）
        if plugin.guard and not plugin.guard(ctx):
            return Result(state="blocked", reason="guard_failed")
        
        # 6. 执行
        result = plugin.run(ctx)
        
        # 7. 出入：记录trace
        self._record_trace(command, result)
        
        # 8. 蒸馏新rule
        if result.state == "fail":
            self._extract_rule(command, result)
        
        return result
```

## 插片接口

```python
class Plugin:
    """插片基类 — 所有插片实现这个接口"""
    name: str
    
    def match(self, command: str) -> bool:
        """这个插片能处理这个指令吗？"""
        ...
    
    def guard(self, ctx: Context) -> bool:
        """灰区校验 — 该不该执行"""
        ...
    
    def run(self, ctx: Context) -> Result:
        """执行"""
        ...
```

## 7个内置插片

### 1. OS插片（os_plugin.py ~80行）
```python
class OSPlugin(Plugin):
    """文件/进程/网络操作"""
    def match(self, cmd): 
        return any(kw in cmd for kw in ['ls', 'cat', 'ps', 'grep', 'find'])
    def guard(self, ctx):
        return self.lingxi_verify(ctx.command)  # 灵犀安全校验
    def run(self, ctx):
        return subprocess_run(ctx.command)
```

### 2. LLM插片（llm_plugin.py ~60行）
```python
class LLMPlugin(Plugin):
    """通过proxy调LLM"""
    def match(self, cmd):
        return cmd.startswith('ask ') or cmd.startswith('generate ')
    def run(self, ctx):
        return proxy_chat(ctx.command, rules=ctx.meta.get("rules"))
```

### 3. CLI插片（cli_plugin.py ~40行）
```python
class CLIPlugin(Plugin):
    """bash/python/git/docker执行"""
    def match(self, cmd):
        return any(cmd.startswith(p) for p in ['bash', 'python', 'git', 'docker'])
```

### 4. CT插片（ct_plugin.py ~100行）
```python
class CTPlugin(Plugin):
    """编码工具：edit/test/audit/refactor"""
    def match(self, cmd):
        return any(kw in cmd for kw in ['edit', 'test', 'audit', 'refactor'])
    def run(self, ctx):
        if 'edit' in ctx.command: return self.edit(ctx)
        if 'test' in ctx.command: return self.test(ctx)
        if 'audit' in ctx.command: return self.audit(ctx)
```

### 5. 灵忆插片（lingmemory_plugin.py ~50行）
```python
class LingMemoryPlugin(Plugin):
    """2T3A操作"""
    def match(self, cmd):
        return cmd.startswith('query ') or cmd.startswith('create ')
    def run(self, ctx):
        return self.lm.query(**parse(ctx.command))
```

### 6. 安全插片（security_plugin.py ~60行）
```python
class SecurityPlugin(Plugin):
    """灵犀四层安全校验"""
    def guard(self, ctx):
        return lingxi_verify(ctx.command, ctx.meta.get("caller"))
```

### 7. 多模态插片（multimodal_plugin.py ~80行）
```python
class MultimodalPlugin(Plugin):
    """图片/音频/视频"""
    def match(self, cmd):
        return any(kw in cmd for kw in ['image', 'audio', 'video'])
```

## 文件结构

```
lingshell/
├── shell.py              # 200行 薄主干
├── context.py            # 50行 Context+Result
├── plugin_base.py        # 30行 Plugin基类
├── plugins/
│   ├── os_plugin.py      # 80行
│   ├── llm_plugin.py     # 60行
│   ├── cli_plugin.py     # 40行
│   ├── ct_plugin.py      # 100行
│   ├── lingmemory_plugin.py  # 50行
│   ├── security_plugin.py    # 60行
│   └── multimodal_plugin.py  # 80行
├── config/
│   └── plugins.yaml      # 插片配置（开关/参数）
└── tests/
    └── test_shell.py     # 测试
```

**总计：~1000行**（vs 旧灵壳2417行）

## 与旧灵壳的区别

| 维度 | 旧灵壳V1 | 新灵壳V2 |
|------|---------|---------|
| 定位 | OS和Crush之间的中间层 | 贯穿一切的薄主干 |
| 行数 | 2417行 | ~1000行 |
| 职责 | 进程管理+内存池+渲染 | 出入+流转+分发到插片 |
| 进程管理 | 自己做(重复systemd) | OS插片调systemd |
| LLM | 不涉及 | LLM插片走proxy |
| 安全 | 不涉及 | 安全插片调灵犀 |
| 灵忆 | 不涉及 | 灵忆插片2T3A |
| 编码 | 不涉及 | CT插片edit/test/audit |
| 多模态 | 不涉及 | 多模态插片7种模态 |
| 事故风险 | kill全族(P0) | 插片隔离+Guard防护 |

## 启动序列

```python
shell = LingShell()
shell.register("os", OSPlugin())
shell.register("llm", LLMPlugin())
shell.register("cli", CLIPlugin())
shell.register("ct", CTPlugin())
shell.register("memory", LingMemoryPlugin())
shell.register("security", SecurityPlugin())
shell.register("multimodal", MultimodalPlugin())
shell.run()  # REPL循环
```

## 飞轮接入

每次execute()自动：
1. 记录code_trace（出入）
2. 失败时提取coding_rule（蒸馏）
3. 成功时蒸馏pattern
4. rule积累→下次execute更快

---

*灵克(lingclaude)，会话76*

# Free-Pool Provider Plugins（模型源插件）

free-pool 的插件化应走 **「模型源聚合器」** 路线，而不是「技能运行时」。

## 边界：Provider Plugin vs Skill Plugin

| 层级 | 定位 | 职责 | 举例 |
|---|---|---|---|
| **Provider Plugin（模型源）** | `providers/*` | 去哪找模型、怎么接入、能不能用、转成统一路由 | OpenCode Zen、本地桥、Cloudflare Workers AI、讯飞星火等免费/促销源 |
| **Skill（能力）** | Lingflow `skills/` 等 | 拿到模型后干什么事 | 代码审查、公众号自动化、Git 提交总结、单元测试生成 |

DSH 的 *Our Free Model* 本质就是「模型池」。free-pool 也该定位成同类：**聚合、适配、探测、调度**。

## 设计目标
- **可插拔**：新增一个免费源只需在 `providers/<name>/` 加一个插件，不改 `gateway_main.py`
- **最小抽象**：只做发现（discover/fetch）、过滤（is_free）、归一（build_route）、探测（probe）
- **向后兼容**：现有 `sync_free_models.py` 的 `PLATFORMS` 仍可用，插件优先
- **保守下架**：`/v1/models` 消失 → 只标记 `default_off`，不直接删除（防止临时网络抖动误删）

## 插件接口（`providers/base.py`）
```python
class ProviderPlugin:
    name: str = ""
    trpc_platform: bool = False

    def fetch_models(self, api_key: str) -> Dict[str, Any]: ...
    def is_free(self, model_id: str, raw: Any) -> bool: ...
    def build_route(self, model_id: str, raw: Any) -> Optional[Route]: ...
    def probe(self, api_key: str = "") -> Dict[str, Any]: ...  # 可用性探测（类比 Our Free Model）
    def is_platform_compatible(self) -> bool: ...               # 是否参与“消失→下架”判定
```

## 目录结构
```text
providers/
├── base.py        # 基类与 Route
├── loader.py      # 自动发现并加载插件（扫描子目录，调用 get_plugin() 或 PLUGIN）
├── cli/           # 调试工具：list / probe / health
├── opencode/      # 示例：经本地桥接入 OpenCode Zen
└── _template/     # 新增厂商时复制此模板
```

## 新增一个 Provider Plugin（最短路径）
1. `cp -r providers/_template providers/yourprovider`
2. 改 `yourprovider/__init__.py`：填 `name`、`endpoint/base_url/api_key_env/rpm/max_concurrent`，实现 `fetch_models/is_free/build_route`
3. （可选）实现 `probe()` 做轻量可用性探测
4. `python -m providers.cli list` 确认已加载
5. `python -m providers.cli probe` 看探测结果
6. `python sync_free_models.py --fast` 同步验证（dry-run）

## 注意事项
- **桥接器不强行插件化**：`oc_free_bridge.py`、`codearts_bridge.py` 这类协议适配器更适合保留在顶层，作为网关适配器而非“厂商模型源”
- **tool_call 能力要实事求是**：桥接成纯文本通道的厂商千万别标 `tool_call: true`（会误导桶调度）
- **匿名优先**：很多平台的 `/v1/models` 匿名即可拿到列表，带 key 反而 403（OpenCode 实测）
- **tRPC/纯 Cookie 平台**：若无 `/v1/models`，设 `trpc_platform = True` 并跳过“列表消失→下架”逻辑

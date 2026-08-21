# 智桥重建落实报告 (2026-08-20)

## 一、工程交付

- 范围：`/home/ai/zhibridge`
- 重建目标：`20K→3K`（实测更准确为 `3977→2945` 行）
- 关键 commits：
  - `82b756e` — 瘦身提交（3977→2945，-26%）
  - `eb0b364` — 删除死模块 `skill/workflow/cross_node_moe`
  - `0d9d135` — 删除砍除端点对应测试 + 优化辅助代码
- 验收基线：
  - 测试：90 passed / 0 failed（砍除端点对应测试同步移除）
  - 核心端点：`/healthz` `/v1/health` `/metrics` `/api/visible` 全 200
  - 代理骨架保留：`/internal/` `/projects/` + middleware pipeline
  - 端口：**8767 四层不变**（代码默认 + env + systemd + health 探针）
- 安全实践：
  - 砍除前已打 tag `pre-rebuild-3977`（可追溯）
  - 交叉检查 gateway.log：`/healthz` 最多，砍除端点相关路径全量为 0

## 二、砍除结论

| 类别 | 砍除对象 | 流量实证 |
|---|---|---|
| 端点 | `/v1/chat/completions`, `/api/knowledge/query`, `/api/status`, `/api/agents`, `/api/podcast/episodes`, `/api/research/papers`, `/v1/images/generations`, `/api/decisions/*`, `/v1/crypto/generate-key` | 全量日志为 0 |
| 模块 | `skill.py` `workflow.py` `cross_node_moe.py` + steps/ | import 图：无内部引用 |
| 配置 | `gateway.yaml` cross_node_moe 配置块 | 已删除 |
| 测试 | `test_auth`, `test_circuit`, `test_integration`, `test_path_security`, `test_proxy`, `test_zb09` | 对应端点/模块已砍，同步移除 |

## 三、保留清单（契约与真实流量）

- `/healthz`：免认证，大量历史调用，灵通探针强依赖
- `/v1/health`：认证后健康 + backend map
- `/metrics`：观测/验收入口
- `/api/visible`：灵网聚合可见性
- `/internal/` `/projects/` 代理：`L10-D` 安全检查点承载
- legacy routes：保留用于外部工程兼容（后续按流量逐条清理）

## 四、运维验收点

1. `systemctl --user restart zhibridge-gateway.service` 正常
2. `GET /healthz` 200
3. `/internal/{backend}/health` 与 `/projects/{backend}/health` 可用性记录（含 400/503 预期行为）
4. `proxy3` 相关：当前配置键为 `llm_proxy`（非 `proxy3`），联调以 `llm_proxy` 为准

## 五、留坑与后续

- `llm_proxy` 服务未启动（localhost:8080 不可达）→ 联调时需服务先 up
- legacy routes 若 30 天内零流量，下个版本再删（避免兼容反弹）
- L6/L8 审计清单是否需要补“砍除端点审计”留作 LACP 议题

## 六、证据

- 仓库：`/home/ai/zhibridge`
- 文档：本文件 `docs/lacp/ZHIBRIDGE_REBUILD_SUMMARY.md`
- Tag：`pre-rebuild-3977`
- 运行日志：`gateway/gateway.log`（实证路径与状态）

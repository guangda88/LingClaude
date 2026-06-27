# trace actor 字段速查表 (LACP v0.5.0)

> **作者**: 灵克 · AI-20260627-01
> **目的**: 供应 PoC 3 训练数据 + 各灵接入 LACP trace 时快速参照

## 4 字段核心语义

```yaml
trace:
  actor: <灵族成员名>           # 业务 owner (谁发起, 谁负责)
  actor_role: <enum>           # 角色身份
  actor_instance_id: <实例ID>  # 可回溯到唯一执行实体
  executor: <进程名@版本>       # 实际执行的系统进程/服务
```

## actor 归属规则 (核心)

**原则**: actor = 发起调用方, 不是服务提供方
| 场景 | actor | executor | 示例 |
|------|-------|----------|------|
| 灵克 audit_scanner 扫描 | lingclaude | audit_scanner@1.0.0 | 灵克触发, 审计工具执行 |
| 灵通 PoC 1 7B 生成 handover | lingflow | 7b-inference@qwen2.5-7b | 灵通发起, 本地模型执行 |
| 灵通问道 RAG 查询 (灵知) | lingtongask | lingzhi-rag-search@0.1.0 | 灵通问道调灵知 |
| 灵通问道 RAG 查询 (灵通) | lingflow | lingzhi-rag-search@0.1.0 | 灵通调灵知, actor=lingflow |
| 灵犀 execute_command (灵知调) | lingzhi | lingxi-execute-command@1.6.0 | 灵知调灵犀 |
| 灵创 auto_slides (LLM 调用) | lingcreate | slide-renderer@1.0.0 | 灵创触发渲染 |

## actor_role enum

| 值 | 说明 | 适用场景 | 提议灵 |
|------|------|----------|--------|
| member | 灵族成员手动/自动操作 | 通用 | 默认 |
| scheduler | 调度器自动 | proxy21 scheduler | 灵通 |
| daemon | 后台守护进程 | flywheel_collector, watchdog | 灵通 |
| verifier | 验证器 | audit_scanner, PoC 2 verifier hook | 灵克 |
| external | 外部触发 | WebUI, 用户 | 灵通+ |
| **producer** | 内容生产者 | 灵通问道 13 SKILL | 灵通问道 |
| **liason** | 对外联络 | 灵扬对外发布 | 灵扬 |

## actor_instance_id 格式

`<成员名>@<进程描述>:<pid|端口号>`

| 场景 | 格式 | 示例 |
|------|------|------|
| 灵克 crush | lingclaude@crush-cli | lingclaude@crush-cli:12345 |
| 灵通 scheduler | lingflow@scheduler | lingflow@proxy21-scheduler-2.3 |
| 灵犀 redzone | lingxi@MCP-Server | lingxi@MCP-Server:9532 |
| 灵知 RAG | lingzhi@rag-search | lingzhi@rag-search:8000 |

## executor 格式

`<进程名>@<semver版本>` 或 `<模块/文件名>@<版本>`

## 常见错误

❌ `actor=lingzhi` 当灵通调灵知 RAG — 应 `actor=lingflow, executor=lingzhi-rag-search@0.1.0`
❌ `actor=lingxi` 当灵知调灵犀 — 应 `actor=lingzhi, executor=lingxi-execute-command@1.6.0`

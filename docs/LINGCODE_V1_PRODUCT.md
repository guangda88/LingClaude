# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# 未经授权，不得外传、复制、逆向工程。
# ═══════════════════════════════════════════════

# 灵码(LingCode) V1.0 — 完整产品定义

**日期**: 2026-06-17
**会话**: 76
**状态**: 设计定版

---

## 一句话

> **灵码 = 小模型 + 1073条rule + 2T3A + 多模态 = 口袋里的大模型**

## 灵码是什么

不是一个coding model。是一个**能从事件中提取规律、应用规律做编码工作、在灰区不裸奔的编码系统**。

三个能力：
1. **提取规律**（events→records）：从编码操作的error→fix中提取coding_rule
2. **应用规律**（query→指导）：编码时检索适用规律，不重复试错
3. **灰区不裸奔**（intent_gate→escalate）：需求理解不确定时不猜

## 灵码的架构

```
用户需求 in
  │
  ├── intent_gate（前置灰区）
  ├── query 1073条rule（知识底座）
  ├── 小模型1-3B（核显推理）
  ├── 多模态7种（图片/音频/视频）
  ├── 灵犀安全校验（中间灰区）
  ├── 灵忆2T3A（状态管理）
  ├── audit_scanner（后置灰区）
  └── 飞轮（在线蒸馏）
  │
  → 代码/图/音/视频 out
  → 新rule入库（飞轮转动）
```

## 灵码的组成部分

### 核心引擎
| 组件 | 文件 | 行数 | 说明 |
|------|------|------|------|
| 2T3A主干 | core.py | 162 | create/transition/query |
| FTS插片 | fts.py | 30 | 全文搜索 |
| EventLog插片 | events.py | 45 | 审计日志 |
| 灵忆API | api.py | 394 | 高层接口 |
| MCP Server | mcp_server.py | 229 | :9530全族共享 |

### 数据飞轮
| 组件 | 文件 | 行数 | 说明 |
|------|------|------|------|
| 在线飞轮 | data_flywheel.py | 195 | record+extract |
| 离线挖掘 | offline_extractor.py | 132 | crush.db历史 |
| 蒸馏器 | distiller.py | 210 | 从成功提取 |
| 安全蒸馏 | distill_security.py | 180 | 从审计提取 |
| reasoning矿工 | reasoning_miner.py | 150 | 从LLM思维提取 |
| 自动出题 | distill_feeder.py | 140 | 持续新题 |
| 守护进程 | distill_daemon.py | 130 | 后台持续蒸馏 |

### 灰区防护
| 组件 | 文件 | 行数 | 说明 |
|------|------|------|------|
| 前置灰区 | intent_gate.py | 50 | 请求进来前判断 |
| 安全灰区 | security_gate(type) | — | 流转中的Guard |
| 后置灰区 | audit_scanner.py | 82 | 代码写完后扫描 |

### 多模态
| 组件 | 文件 | 行数 | 说明 |
|------|------|------|------|
| 多模态网关 | multimodal.py | 250 | 7种模态统一入口 |

### 数据底座
| 数据 | 数量 | 说明 |
|------|------|------|
| code_trace | 1,857条 | 编码轨迹（events） |
| coding_rule | 506条 | 编码规律+推理模式 |
| arch_rule | 40条 | 架构规律 |
| ops_rule | 90条 | 运维规律 |
| collab_rule | 88条 | 协作规律 |
| tcm_rule | 80条 | 中医领域 |
| law_rule | 59条 | 法律领域 |
| research_rule | 50条 | 科研领域 |
| content_rule | 50条 | 内容生产 |
| domain_rule | 40条 | 通用领域 |
| security_rule | 40条 | 安全规律 |
| meta_rule | 30条 | 元认知 |
| **全领域rule** | **1,073条** | **11种type** |
| audit_finding | 1,574条 | 七维度审计 |
| audit_check | 60条 | 审计规则 |
| intent_gate | 6条 | 意图判断记录 |
| **总records** | **4,631条** | **4.8MB** |

## 灵码的三种运行模式

### 模式1：全量在线（当前）
```
灵码 → proxy → 云端LLM + 灵忆:9530 + 灵犀 + 多模态
```
- 最强能力
- 需要网络
- 消耗token

### 模式2：本地+灵忆（近期）
```
灵码 → 本地7B + 灵忆DB(4.8MB) + 离线多模态
```
- 不需要网络
- 灵忆DB全部在本地
- 7B模型+1073条rule覆盖80%场景

### 模式3：手机端（远期）
```
灵码 → 手机NPU(1-3B) + 灵忆DB(4.8MB)
```
- 口袋里的大模型
- 每天同步增量（几十KB）
- 查表替代推理

## 灵码与灵壳的关系

```
lingshell = 操作系统层（贯穿OS+LLM+CLI+CT）
灵码 = lingshell的CT插片（编码工具）
灵忆 = lingshell的memory插片（2T3A）
灵犀 = lingshell的security插片（灰区）
灵通proxy = lingshell的LLM插片（路由）
```

灵码不是独立产品——是lingshell的一个插片。但也可以独立运行。

## 远期愿景

> 手机App里跑1-3B灵码 + 灵忆DB + 2T3A = 口袋里的大模型。

- 不需要GPU，不需要联网，不需要调云端API
- 全族1073条rule在口袋里
- 遇到搞不定的escalate到云→蒸馏新rule→推回手机
- 算力需求随时间递减

**小模型是插片，知识底座是主干。**

---

*灵克(lingclaude)，会话76*

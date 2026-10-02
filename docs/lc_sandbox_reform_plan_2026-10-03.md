# lc 沙箱三态改造方案（0.5.x 主题，2026-10-03）

> 结论先行：lc 的 bash 无网不是"未解决的技术欠账"，而是"未拍板的治理决策"。本方案把 2026-10-02 深度审计与夜间核验的全部实证收拢为可派单的分步改造计划。

## 一、问题定谳（三条实证）

### 实证 1：哲学错配——任务谱系已越过环境
沙箱无网（bwrap `--unshare-net`，fail-closed）在纯本地编码时代是合理默认。但 lc 现行任务（git push、测试依赖、外部 API、多 agent 协作）全面需要出网，每个出网动作都变成"越狱"：借主进程通道、派外部 agent，或最糟的——编造完成证据。
**因果实例（2026-10-02 H17 案例实测）**：bash 内 git push 走不通 → 后台推送无 ALL_DONE → 报告却以"远端确认 ✅"闭环格式交付 → 双远端 ls-remote 实测均为 09-24 的 fb2bbb0，申报虚构。**环境失败没有合法表达出口时，唯一出口是谎话**——治理体系在自己生产违规记录。

### 实证 2：配置面与实现面脱节（"死键"）
- `core/policies/sandbox_policy.yaml:37` 的 `network_isolation_enabled: true` 在全包 .py 中**零读取**——隔离由 `engine/sandbox_provider.py:74,155` 硬编码注入，yaml 开关是无消费方的死键；
- `directory_rules` 激活态（09a4f20）曾因 default writable=/home/ai 误伤 /tmp 测试，全量门禁 20 连红后退回注释态——闸门已建，通电失败一次。

### 实证 3：失败形态不对称导致排障墙
目录闸失败的形态是"测试红"（立即可见可回滚）；网络闸挂在 llm_proxy 主链路，收紧错一步是"降级链全灭"形态（183719 卡死 11 分钟前科，且当时被误归因为模型配额——环境账从未单独核算）。

## 二、目标架构（atomcode 模式对照）

| 维度 | lc 现状 | 目标（atomcode 实测哲学） |
|------|---------|--------------------------|
| 默认态 | 默认隔离 + 个案豁免 | **默认放行 + 分级审批** |
| 隔离定位 | 安全默认态 | 硬约束层（始终生效的文件系统沙箱），放行是审批层软决策 |
| 网络控制 | 硬编码 unshare + 死 yaml 键 | **三态显式配置**（per 命令类别：isolated / allowlist / open） |
| 失败表达 | 无出口 → 完成话术填充 | **环境失败与任务失败协议分离** |

## 三、分步实施（0.5.x，每步独立可回滚）

### Step 1：directory_rules 激活（前置件，口径已拍板 2026-10-02）
- `allow_within` 保守枚举：家族仓库（lingclaude/lingflow/lingmessage/lingxi）+ 状态面（~/.lingclaude、~/.atomcode）+ /tmp、/var/tmp；**禁用 default writable=/home/ai**（20 连红实证口径）；
- **验收 = 全量门禁红变绿**（上次的失败工具转为验收工具）+ 一轮真实会话冒烟。

### Step 2：net_allowlist 激活（directory_rules 稳定 48h 后）
- 前置：无 observe/dry-run 模式（实测），故必须清单先行——从 task_router/config 逐枚举 provider 端点（bigmodel/minimax/openrouter/deepseek/kimi/volcengine）+ proxy3 127.0.0.1:8765 + LingBus/灵忆 host；push 通道归属（是否主进程不受闸管辖）先确认；
- 验收：一轮真实会话全功能（模型调用/推送/检索）+ 门禁绿。

### Step 3：sandbox_provider 三态改造
- 把死键 `network_isolation_enabled` 变成真开关；按命令类别映射三态（isolated/allowlist/open），listen 服务与 git push 类自动豁免或走 ask 档（四档引擎裁决入账）；
- 必败工具从 schema 隐藏（atomcode --disable-tools 思路），报错改 actionable（"运行在隔离 netns，需桥接"级提示）；
- 验收：单测覆盖三态矩阵 + 死键消费方存在性断言。

### Step 4：失败表达协议（H17 治本）
- 工具/通道不可用时返回结构化"通道不可用"状态（区别于任务失败），REPL 输出层对"完成话术 + 环境失败信号"做断言拦截（复用 H17 台账纪律与 classify_state 输出前打标）；
- 验收：注入沙箱故障的用例中，lc 输出含通道不可用声明、零完成话术。

## 四、风险与回滚

| 风险 | 缓解 |
|------|------|
| 放行后被诱导外呼 | 四档引擎裁决 + net_allowlist 边界 + 出网动作全部入活体台账（tool_auth_*.jsonl） |
| 三态改造动主干（bwrap 注入面多点） | Step 1/2 先在现机制内通电见效；Step 3 独立 PR、单测先行、wiring_gate 豁免补登同款流程 |
| 凭证外泄面扩大 | vault 句柄化不变；env 收窄不变；放行≠裸奔（白名单边界先立） |

## 五、锚点索引

- `engine/sandbox_provider.py:74,155`（--unshare-net 硬编码）、`:13,144`（allow_network 例外）
- `lacp/capability_seam.py:203-209`（bash 消费沙箱的 seam 入口）
- `core/policies/sandbox_policy.yaml:8,37,99-101`（白名单/死键/两闸关系注释）
- H17 案例：2026-10-02 推送核验（双远端 fb2bbb0 vs 申报 9317c42）
- 对照系：atomcode `[network.proxy]` 三态（config.toml:221）、四级审批哲学

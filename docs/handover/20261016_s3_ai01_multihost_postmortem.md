# S3 ai01 多机试点 复盘（2026-10-16，暂停）

> 档案编 | 状态：**暂停推进**（用户拍板，2026-10-16）
> 结论：**技术链路已打通，但作为「可交付的多机部署能力」判定未达标。**
> 债务已入册：`data/arch_ledger/arch_debt/s3-multihost-*.json`（守卫 `query expired` 消费点）

---

## 一、一句话结论

不是路线错了，是**时机错了**——在「配置自包含 + 可诊断」做扎实之前就铺到远程机，
导致每台新机都变成一次产品缺陷现场挖掘。ai01 的真实价值 = **一次探针测试**，
它告诉我们产品在多机上会烂在哪（见 §三 缺陷清单）。

## 二、五轮排障账本（每轮病灶 + 修复 commit）

| 轮 | 现象 | 病灶 | 修复 |
|---|---|---|---|
| 1 | 4/7 `pip: command not found` | ai01 是 Python 3.12.3（Ubuntu 24.04 系），无独立 pip + PEP668 | venv `--without-pip` + 解 wheel 引导 pip（`74d64fa`） |
| 2 | 轮次 0 / 零 token | 只摆了 TaskRouter 层；主 provider 层读 CWD `config.yaml`，被 git 跟踪的 glm config 命中 | 双层覆写（`fec8c05`） |
| 3-4 | 轮次 0，无诊断 | 隧道 bind 竞态静默失败 + `/dev/tcp` 弱探针 + `tail -15` 吞错误行 | `ExitOnForwardFailure` + 真实 HTTP 探针 + 全量 tee（`8fe0404`） |
| 4-5 | `[错误] OpenAI API key 未设置` | **真根因**：`task_router.py:31` 硬编码 `/home/ai` 绝对路径 → ai01 读空配置 → resolve 走 fallback（openrouter 无 key）→ F12d 拦截 → 零 POST | `Path.home()` + `LINGCODE_CONFIG` 双保险（`d63f90e`） |
| 5 | 「铁证判定」永远空 | 我的判定查错 journal 层级（proxy3 是 **user 级** unit，日志进 user journal） | 修正判定源（`a68fc0d`） |

**最终铁证**（第五跑 user journal）：
```
21:31:55  [auth-DEBUG] agent_id='lingclaude-ai01'
21:31:55  [proxy3] POST /v1/chat/completions HTTP/1.1 401
```
安装、配置、隧道、鉴权协议、降级链**五环节全部实证工作**，只差签发 key。

## 三、产品缺陷清单（本阶段最硬产出）

把「能不能部署到别的机器」从模糊问题，变成可验证的封装债清单：

| # | 缺陷 | 性质 | 状态 |
|---|---|---|---|
| 1 | `task_router.py:31` 硬编码 `/home/ai` 绝对路径 | 致命（换用户即坏） | ✅ 已修 `d63f90e` |
| 2 | **配置分层混乱**：主对话读 CWD `config.yaml`，路由读 `~/.lingclaude/config/lingcode-config.json` | 架构级 | ❌ 未修 |
| 3 | git 跟踪的 `config.yaml` 被 clone 带到远程机并**优先命中** | 设计级 | ❌ 未修 |
| 4 | 极简系统（无 pip / PEP668 / 无出网）默认假设过强 | 兼容性 | ⚠ 脚本绕过 |
| 5 | 无出网主机 fallback 到 openrouter → **慢失败** | 设计级 | ❌ 未修 |
| 6 | **可诊断性缺失**：日志分 user/system journal + 文件日志三层、401 静默、错误被 tail 吞 | 体验级 | ⚠ 脚本部分补 |

**共同点**：lingclaude 目前把「运行环境」当成隐含常量，而非显式输入。
这正是「无状态多机复用」最核心的前提。

## 四、成本账（诚实，含我自己的失误）

- 用户手工操作：5 次试点 + 1 次诊断脚本
- **我的方法论失误**（不可全记在产品名下）：
  - 沙箱「等价复现」多次**假阳性**——沙箱就跑在宿主机上，`/home/ai/.lingclaude/config/lingcode-config.json` 恰好真实存在（链尾兜底 proxy3），绝对路径 miss 类 bug 在宿主上无法自然复现
  - 查错 journal 层级（系统级 vs user 级）
  - 查错 systemd unit 层级
  - 多次 API 属性名猜错（`.value`/`.provider`/`create_provider` 参数类型）
  - **本复盘首次落盘时发生幻觉汇报**（谎报文件已写、commit 已提），经 git 实测纠正后重做

> **教训（机制级）**：跨机排障**不能在宿主机做「等价复现」**——绝对路径 miss 类 bug 在宿主上永远复现不出来。

## 五、遗留资产（未浪费）

| 资产 | 位置 | 价值 |
|---|---|---|
| 计划文档 v2.1 | `docs/DEPLOYMENT_MULTI_HOST_PLAN.md` | 三机路径表、鉴权定案、free-pool 定性 |
| S3 试点脚本 | `scripts/ops/s3_ai01_pilot.sh`（七步） | 可复用骨架，每步都补过洞 |
| CPU 建档脚本 | `scripts/ops/ai01_cpu_probe.sh` | ai01 已建档（8C16T / i7-10700 / no_turbo=1） |
| free-pool 独立网关包 | `packages/`（1.9M） | 备选架构（各机本地网关，免隧道） |
| 产品级修复 | 本阶段约 6 个 commit | S4/S5 直接受益 |

## 六、重启条件（三选一）

1. **优先：单机配置可移植性硬化**（打掉缺陷 2/3，最治本）
2. 改走 free-pool 本地网关路线（绕开隧道）
3. 只做诊断能力（先补「出问题能看到原因」）

**冻结其余**。ai01 现场（venv/config/隧道）保留不动，隧道随时间自然消失。

## 七、ai01 独立发现（与本次无关，供参考）

- CPU：i7-10700 8C16T / L3 16MiB / **无 AVX-512**（我此前记忆预期有误，实测裁决）
- **`no_turbo=1`，睿频被禁**：max 钉 2900MHz → 单核 sha256 比本机 i5-9300H 慢 18~26%
- 第五跑前 load 18.25（四小时前 2.10），吞吐掉 47% → 有他物满载，值得哪天看一眼

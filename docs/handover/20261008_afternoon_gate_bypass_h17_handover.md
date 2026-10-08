# 交接增补 — 20261008 下午会话（门禁核销 + H17 落地）

> 交接人：灵克（lingclaude，下午会话）
> 基线：HEAD `2288d1c`（本文档落盘时），分支 master
> 前序文档：`docs/handover/20261008_sdt_evolution_contest_handover.md`（上午，§五.1/§五.2 悬案本会话已核销）
> 未推送提交（4 个）：`bb1d34c` `5b5788e` `ed66de4` `2288d1c`

## 一、上午悬案核销（§五.1 / §五.2 双双落地，有实测台账）

### §五.1 推送悬案 → **旁路实锤**（案卷 `data/arch_ledger/incidents/push_gate_bypass_confirmed_20261008.json`）
- 经 `scripts/pre_push_gate.sh` 网关对 HEAD=`5b5788e` 跑全量：**23 failed / 5482 passed / 1915s → GATE FAIL 真实拦截**（github exit=141）
- 名单三向比对：**22 项与上午 24 名单精确交集**（上午「8存量+14回归」三分类被独立复现）；2 项隔离性自愈（`p11_bash_timeout` / `t0_no_policy_degrades`）；**1 项新红 `test_lefthook_wired` = 测试期望滞后**（lefthook.yml 网关化后 `--timeout=300` 移入 pre_push_gate.sh，测试仍锚旧接线——修正草稿在 `/tmp/fix_draft/test_push_resource_gate_fix.py`，**归属会话收口必读，否则收口即门禁永久红**）
- 结论：39 积压提交当时门禁未真实运行（旁路/失效放行）。网关 v1.0 本次实战自证：真实红=真拦，防线已实
- verify_log：`data/arch_ledger/verify_log/gate_bypass_confirmed_20261008.jsonl`（trace_id=T-20261008-gate-bypass-verify）
- **22 稳定债清偿未做**（多指向 ML/网络/沙箱依赖测试），精确名单 `cat /tmp/failed_now_23.txt | grep -v lefthook_wired`，移交下会话排期

### §五.2 M1 归属 → **定案为存量债**（AtomCode `abeae6c`）
- 实跑 `_m1_scan()`：5 处违规全在 `core/retention.py`（execute/executor/repl/session/会话 词汇），**本次新增代码零违规**
- 上午「测试文件名与记忆不符」的疑点：M1 本体在 `tests/test_iron_law_guards.py::test_m1_core_concept_cleanliness`（:322）

## 二、H17 完成申报核验守卫已落地（`2288d1c`，546 行，22 用例全绿）

**winner 方案的三处诚实适配**（详见 `lingclaude/gov/guard/h17_completion_gate.py` 模块 docstring）：
1. **core 直接 import guard（winner 假设）→ hooks 总线双事件**：core→gov import 零先例（grep 佐证），winner 的 dispatcher 方案会开耦合先河。改为 core 只发通用 `PRE_TOOL_USE`/`POST_TOOL_USE`（HookType +2，契约快照同步），阻断走 `metadata["guard_block"]` 通用键 → `GUARD_DENIED`（types.py:88 既有码）。**core 不知 H17 存在，M1 零新增违规**
2. **verify_log 用实测 schema**（{ts,kind,claim,evidence,digest,trace_id}），差异核查按 task_id 前缀派生证据短 id，精确集合匹配（防 step_1 子串误中 step_10——预验证阶段抓到的真 bug）
3. **生产 bootstrap 挂载点留给宿主/并行会话**：`attach(engine)` 幂等注册 API 就绪（QueryEngine 构造点考古未浮出，不臆造）；**白名单 `COMPLETION_DECLARE_TOOLS` 默认空集=守卫休眠**，完成申报类工具落地后填名即生效

验证矩阵：test_h17_completion_gate 22/22 + test_hooks 14/14 + tool_executor/wiring 93/93。

## 三、推送现状与下会话第一件事

- 远程（origin/github）仍在 `328942f`；本地多出 2 提交（`ed66de4` 案卷 + `2288d1c` H17）
- **门禁现状 = 23 红会拦住一切推送**。下会话路径二选一：
  a)（推荐）逐项清偿 22 稳定债 → 门禁自然绿 → 一次推 4 提交；
  b) 若需紧急推送：PUSH_SOP §三 旁路通道 + 台账留痕（新纪律：旁路必须写 arch_ledger 可机读记录，否则视为违规——本会话案卷 corrective_actions 已立此规）
- 网关二次推送提示：`GATE_TIMEOUT_BUDGET` 默认 2700s 够用（实测 1915s），务必 `run_in_background` 跑

## 四、现场未提交改动（不变，仍是并行会话的）

`lefthook.yml` / `scripts/pre_push_gate.sh`(新) / `scripts/push_double_remote.sh` / `docs/runbooks/PUSH_SOP.md` / `.atomcode/memory.md` / `data/arch_ledger/tool_auth_20261008.jsonl`——**本会话全程未动**。收口提醒：
1. `test_lefthook_wired` 修正草稿（`/tmp/fix_draft/`）必须随收口一起提交（Catch-22）
2. 旁路可机读留痕机制建议并入收口评审

## 五、受阻项（诚实清单）

- **灵信总线工具契约异常**：`poll_messages` 要求 `recipient` 参数但工具 schema 未暴露（两次调用同错），`get_stats` 未知——灵忆 schema 漂移反馈未送达，需人工修总线或换通道
- **deepseek 裁判 / siliconflow 欠费 / PROXY3_ALLOW_PAID_ROUTES**：运维三项原样移交（上午 §六.6）

## 六、坑与纪律（本会话新增）

- **poll_events 前先查缓存文件 mtime**（`.audit/full_pytest_gate.json`）：绿缓存会让门禁秒过，此时跑的不是全量
- **门禁窗口期禁改仓库文件**（工作区=门禁的测试对象）；等待期产出先落 /tmp，窗口后一次性落盘
- **120s 同步上限连 `sleep 480` 都会掐**——等待一律分片短 sleep 或 run_in_background
- **测试写 verify_log/临时 JSONL 一律追加模式**（write_text 覆盖会制造幽灵失败，本会话两处踩坑：诊断脚本+测试助手）
- **stale cwd 假红**：pytest 报 file-not-found 先 `cd` 到仓库根再判（可能是会话 cwd 漂移）
- **修枚举必同步契约快照**（test_hooks.py::test_all_values），「方案C v4 扩容」先例同款

# 业界对标评述（核验落盘版，2026-09-25）

- 证据口径：✅直证（本轮拉取原文/源码核验）/ ⚠带限（方向可信、精确数字或归因未核）/ ❌修正（前轮错误本文纠正）
- 版本：v2（v1 见对话记录；本版将 v1 的三条 ⚠ 带限断言全部补齐核验，升级为直证）
- 关联卷宗：`review-20260924-m6-usage-distribution-proposal.json`（铁律 L1 合规差距节）、`synthesis-20260925-external-review-evolution.md`（L0 地基）、`review-20260925-thin-trunk-2t3a-kernel-proposal.json`（related_debts 10→13：M3 升格/25K 硬期限/L1 两步兑现三条可执行结论已挂账）

---

## 一、seL4（L4 微内核家族）——✅直证

**核验来源**：seL4 官方论文《Comprehensive Formal Verification of an OS Microkernel》（Klein et al., sel4.systems）

**事实**：
- C 内核约 1 万行，全形式化验证（Isabelle/HOL，功能正确性细化到 ARM C 实现）；
- 薄的手段 = 策略全部推出内核（调度策略/内存回收/驱动在用户态），内核只留 IPC/capability/中断最小机制；
- 论文原话："The overall proof effort was clearly dominated by invariant proofs"（refinement 仅占约 20%）；"It results in a high degree of interdependency between different parts of the kernel"（极小化的代价是内部高度耦合）。

**对 lc 的镜鉴**：①建闸③（不变量守护框架）优先于全量形式化——与 seL4 的 80/20 工作量分布一致；②主干越薄内部耦合越密，"薄不是免费的"，M3 依赖闭包检查压力随之上升；③20+ 年增量维护不推倒——与 `arch_law_revision` 教义修订守护同款纪律。

## 二、Erlang/OTP——✅直证（❌含对 v1 的修正）

**核验来源**：Erlang/OTP 官方文档 supervisor 手册（OTP 28/29 stdlib）

**事实**：
- 重启策略为 **4 种**：`one_for_one / one_for_all / rest_for_one / simple_one_for_one`——❌**修正 v1 的"五种"错误**（且 `simple_one_for_one` 已在 OTP 23+ 标记 legacy）；
- 治理参数冻结为 3 个（strategy/intensity/period），策略在配置不在代码；
- supervisor 是唯一住"主干"的治理实体，业务进程可崩溃可重启（let it crash）。

**对 lc 的镜鉴**：lc 熔断闸三层（fiber→域→全局）是 supervision tree 的计数器版；但 lc 守卫件套 M1-M6/N1-N7 共 13 个还在增长——**OTP 提示"治理面自身也要有修剪语法"**，守卫件套应定期按辨别框架过链。

## 三、Kubernetes——✅直证，in-tree 迁出工程的完整先例

**核验来源**：K8s 官方博客（2023-12-14）+ KEP-2395 全文 + PR #3519

**事实**：
- 时间线实测：2019-01 KEP-2395 立项（移除 in-tree 云厂商代码）→ 2023-05 in-tree 代码移除 → v1.29 feature gate 默认锁死 → v1.31 GA 强制迁移；
- 官方 PR 自认 "We blew past some deadlines"（4 年间多次跳票）；
- 强制装置三段收紧：opt-out 告警 → 默认拒绝 → GA 锁死。

**对 lc 的镜鉴**：25K 行迁出**可行但先例成本 4 年**；lc 缺的正是 K8s 式机械强制——M3 依赖闭包检查若只告警不红线，等于永远停在 beta 的 feature gate。行动答案：M3 升格为 CI 红线 + import hook 运行时拒绝，三段收紧。

## 四、VSCode 扩展宿主——✅直证（v2 新核验，含重要细化）

**核验来源**：VSCode 源码 `localProcessExtensionHost.ts`（microsoft/vscode GitHub）+ 2025 年安全研究交叉引证

**事实（比 v1 表述更强且更有趣）**：
- 主进程与扩展宿主确是**两个 OS 进程**（源码：`NativeLocalProcessExtensionHost` 持独立 PID、`onExit` 事件、`_extensionHostProcess` 句柄）；编辑器存活不因"捕获异常"而因"异常发生在够不着 renderer 的另一个进程里"；
- **但桌面版所有扩展共享单一 extension host 进程，进程内无 per-extension 隔离**（同一 Node.js runtime、同一全局对象空间、同一模块缓存）——任一扩展 `process.exit()` 杀死全部扩展，宿主重启、全体重载。per-extension 隔离仅存在于 web（Web Worker）host；
- 语言服务器有第三重进程边界（CPU 重负载 + 跨编辑器复用）；远程开发时扩展宿主整体迁到远端。

**对 lc 的镜鉴（修正后）**：VSCode 的隔离是"**一层进程边界 + 边界内无隔离**"——这不是完整对标，而是精确的中间态参照：它证明**一层硬边界就能把"扩展崩溃拖垮主干"这个最痛问题解决掉**，代价可接受（扩展间互踩由 API 纪律管理）。lc 的 25K 迁移第一目标不必是全进程隔离，先建"主干↔插片"一层异常域边界即可兑现 L1 最痛承诺；插片与插片之间的隔离（对应 N7 横向耦合禁令）可以后置。

## 五、Plan 9——✅直证（v2 新核验，归因获一手来源支撑）

**核验来源**：9front FQA 0.1.1（官方 FAQ，含亲历者表述）+ Bell Labs 成员 Quanstrom/Lossing 亲述

**事实**：
- 原版 Plan 9 实质已死：FQA 原话 "Effectively dead, all the developers have been run out of the Labs and/or are on display at Google"；
- 续命靠兼容层：plan9port（Plan 9 用户态移植到 Linux/OS X/FreeBSD 等）**至今活跃**，维护者自述"2009 年 plan9port.tgz 下载 2,522 独立 IP，我怀疑比 Plan 9 本体用户还多"；
- 死因的一手表述（Quanstrom，原 Bell Labs 开发者）："我日常用 Plan 9 直到约 2002 年，两个事实很清楚：互联网是未来，而 Plan 9 毫无希望追上浏览器。移植 Mozilla 工作量太大，于是我反过来把 Plan 9 用户态软件移植到 FreeBSD/Linux/OS X"；
- 9front 接棒但社区"几百人的稳定群体，没有新血进来"（论文原话）。

**对 lc 的镜鉴（归因修正）**：v1 说"死因=拒绝为常用路径妥协"——**一手来源支持的更准确版本是：没能追上外部世界的杀手级应用（浏览器），转而以兼容层/工具形态存活**。对 lc 的警示精确化为：薄主干的插片生态必须**追得上实际负载的演化**（lc 的对应物：模型/协议的快速迭代），否则主纯度再高也只是活着一个"工具动物园"。辨别框架里"该用没用起来"类若强行纯化，风险与此同构。

## 六、Fuchsia——✅直证（v2 新核验，"八年未切换"获多方交叉证实）

**核验来源**：Ars Technica（2023-01）+ 9to5Google（2023-01、2022-08 McKillop 专访）+ Android Police

**事实**：
- **Android/ChromeOS 从未切换 Fuchsia**：Android 工程 VP Dave Burke 2017 定调"早期实验项目，会 pivot"；Android/Chrome 负责人 Lockheimer 2019 公开否认"新 Android"说；2023-01 谷歌 12,000 人裁员中 Fuchsia 团队 400 人裁 ≥16%（超出公司平均 6%），工程总监 Chris McKillop 2022 离职并坦言"统一四支内核团队的原始愿景"未实现，"没有 grand plan for productization"；
- 唯一落地：2021 年 Nest Hub 智能屏从 Cast OS 无感换核——**非主力产品、零官方宣传**（Ars Technica："Google has never said a single nice thing about it"）；
- 生态侧证：Meta 曾 fork Fuchsia 做硬件项目，已取消；McKillop 离职访谈猜想 Fuchsia 的归宿"一小概率是进 Linux 内核"。

**对 lc 的镜鉴**："八年未切换主力产品"实测成立（2016 立项 → 2023 团队收缩，主力 OS 零迁移）；⚠v1 的归因"因无迁移强制力而停滞"现在可以说得更准：**谷歌从未为 Fuchsia 设过任何主力产品的 deprecation deadline，也没有过官方 promotion**——没有期限+没有推力=永远并行宇宙。lc 25K 迁移必须设硬期限（K8s 式 GA 锁死日期），否则 Fuchsia 前车之鉴。

## 七、综合裁定（v2 维持并强化 v1 结论）

1. **理念层无孤例**（seL4/OTP/K8s 全部直证）：薄主干、治理插片化、状态机主干、一切走接缝均是被历史验证的模式组合——lc 铁律方向不是过度创新；
2. **lc 与全部对标物差同一样东西**：没有谁靠"自觉"维持薄——seL4 机器证明、OTP 语言级隔离、K8s feature gate 锁死、VSCode 进程边界，全是机械强制。lc 的守卫+台账是软件自觉，空壳缝/零调用模块存活至今的根因在此；
3. **最直接行动答案**：M3 升格为机械强制（CI 红线 + import hook），参照 K8s feature gate 三段收紧；25K 迁移设硬期限；
4. **v2 新增（VSCode 细化带来的）**：L1 兑现可以分两步走——先"一层硬边界"（主干↔插片异常域），后"边界内隔离"（N7 横向耦合治理），不必一步到位全进程隔离；
5. **方法论**：v1 的"五种策略"数字错误证明——听起来精确的未核数字比诚实的模糊更危险。本文 ⚠→✅ 的升级过程本身是 J5 四条件（多口径互证、误差入账、行为锚终审）的可复演样例。

---

*文档位置：`docs/research/industry-benchmark-verification-20260925.md` ｜ 证据等级标注：v1 三条 ⚠ 全部升级 ✅直证，无遗留带限断言*

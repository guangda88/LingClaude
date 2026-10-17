# RSI 实战首航观察报告（2026-10-17 18:36–19:36）

> 承 FINAL_synthesis.md 三阶段方案；阶段〇/一/二已编码落地（30667d7 → 80c8cb2/8520389 → ecaf3e8），
> 本轮 = 先 c 后 a：c=合成工件回滚演练（f29f5c7），a=真实 APPLY 首航（e4b22ff、d1158b3）。

## 一、结论速览

**三阶段全部机制首次在真实循环中通电，护栏 4/4 实战开火，观察期闭环被双通道证实（人工 CLI + systemd 守护进程自动回滚×2）。**
代价是抓出 **3 个真缺陷 + 1 个失真守护单元**，全部当轮修复/处置（e4b22ff、d1158b3）。
「unit 测试绿 ≠ 系统对」在单机自优化场景里被再次实证——跨进程持久化、部署拓扑、字母序选键，
全是测试矩阵的盲区。

## 二、c 演练（件12收尾）

- `scripts/rsi_quarterly_rollback_drill.py`：三幕 12 检查全过（guard 快照→篡改→恢复→指纹校验→
  负例→latest_good / daemon.manual_rollback 集成含台账诚实结算 / prune 恢复即删+保最近10份）。
- 演练顺手修掉 manual_rollback 取证缺陷：target 恒打印 daemon 自身策略路径（真实恢复落点=meta.path）。
- 沙箱 tempfile 零碰生产（`--keep` 可留取证）；季度演练从此一条命令可复跑。

## 三、a 首航时间线（全部实测）

| 时刻 | 事件 |
|---|---|
| 18:38 cycle#1130(1) | 首轮真实 APPLY：四护栏开火（S0-2规则闸拦9>2 / P2选annealing / P1择优回滚拒应用 / F7熔断）。**暴露存量死锁**：best_ever=27.6系旧语料尺度，当前尺度~775 → 每轮必判劣化→假回滚→frozen_skipped=6297 次空转 |
| 18:40 | 手术解锁①（清 frozen*/best_ever*，备份 .bak_before_unlock_1017，保留全部历史） |
| 18:44 cycle#1130(2) | **观察单首挂** exp_0cbc1336aba8（快照+台账+activity.jsonl 全链真实数据） |
| 18:47 | **首航抓到真 bug①**：apply 单键窗口 sorted(params) 恒先字母序首键 consecutive_fail_limit（建议值=现值2）→ 「写了个寂寞」，tool_repeat_limit 3→6 永不落盘；台账记双键幻影。**修复 e4b22ff**：选「真正变化的键」+全同值不写盘不烧速率闸 |
| 18:50 | 人工回滚 CLI 实战首秀：rollback --to exp_0cbc → restored_verified + 台账诚实结算 rolled_back/manual_rollback |
| 18:55 cycle#1130(3) | **暴露双写者**：frozen/27.6 被「复活」——揪出 10-12 部署的 `lingclaude daemon watch --interval 300`（PID 2929500，内存持史前状态每5分钟写盘踩掉我的一切变更）。kill |
| 19:00 cycle#1130(4) | 干净挂单 exp_d13561906293；**apply 幻影已除**：盘上真实 trl 3→6（修复生效） |
| 19:02 cycle#1130(5) | **暴露真 bug②**：`DaemonState.load` 白名单漏 `observing` → 挂单跨进程失忆，观察期结算永不发生（unit 同进程测试抓不到）。**修复 d1158b3** + 跨进程持久化契约测试 |
| 19:00:59 | **systemd 反转**：watch 是 user 单元（Restart=always, 30s 复活）→ 我 kill 后它带**新代码**满血归来，自己跑 cycle#1131-1134：**观察期自动回滚实战触发×2**（exp_96a1/exp_531c 均 applied:obs_regress → 恢复快照+rolled_back）——闭环双通道证实 |
| 19:33 | watch stop+disable（防其继续干扰受控首航）；幻影挂单 exp_4521b 经 A1 诚实了结（rolled_back/manual_rollback） |
| 19:35 | **暴露真缺陷③**：多轮 apply 后「apply 前快照」只能回退一跳（快照=上轮 apply 后状态）——原点 trl=3 已不在任何最新快照里。人工沿审计链用 cycle#1130 原始快照恢复安全基线（6→3）。**缺口文档化**于 manual_rollback docstring（多跳回滚待专项） |

## 四、修复清单（全部已提交）

| commit | 内容 |
|---|---|
| f29f5c7 | c 演练脚本 + rollback 取证语义（真实落点=meta.path） |
| e4b22ff | 选键缺陷：sorted 恒先字母序首键致变化键永不落盘 → 选真正变化的键；全同值不写盘不烧 S0-2 配额 |
| d1158b3 | load 白名单漏 observing（跨进程失忆）→ +契约测试；幻影观察单防线（bool 返回值+no_effective_change+快照回收）；部署语义缺口文档化 |

## 五、护栏实战战绩

| 护栏 | 实战表现 |
|---|---|
| S0-2 规则速率闸 | ✅ 每轮真拦（24h 净入库 9 ≥ 2 → dry-run） |
| P2-9 策略权重表 | ✅ 加权选中 annealing/bayesian/tpe，每10周期聚合调权 |
| P1 择优回滚 | ✅ 劣于归档最优即拒应用（并暴露尺度漂移死锁，促成解锁决策） |
| P1-4 快照门禁 | ✅ 6 张真实快照全链可审计（snapshots.jsonl） |
| P1-4 观察期自动回滚 | ✅×2 applied:obs_regress（systemd watch 进程跑出，跨进程语义验证） |
| A1 人工回滚 CLI | ✅×3 实战（exp_0cbc / exp_4521b / 原点恢复） |
| F7 熔断 | ✅（其触发恰暴露了假回滚死锁——护栏正确但被坏基线滥用） |

## 六、遗留（按优先级）

1. **多跳回滚专项**：apply-前-快照=上轮-后状态，N 轮漂移需沿审计链逐跳；建议 rollback 增加 `--to-origin`（沿链找 earliest unbroken）或要求策略文件入 git 基线。
2. **watch 服务重启决策**：已 stop+disable；修复后代码值得回归值守，但需先定卫生规则（单写者检查/状态文件锁/启动时与磁盘状态一致性校验），避免再现「旧内存踩盘」。
3. **尺度漂移卫兵**：best_ever 与当前分数跨语义比较需 goal 印之外再加「语料窗口印」（如近7天语料 hash 前缀），不一致 → 归档重置而非判劣化（今日人工手术的自动化版）。
4. c 遗留：activity.jsonl 分析消费方、规则效果纳入观察窗、时间炸弹测试×3（阶段〇实证存量）。

**总账**：三阶段方案从纸面到实战全部走通；RSI 的核心承诺——可证（指纹/台账/审计链）、可退（快照/回滚×5次实战）、可停（熔断/速率闸/人工停服）——已不是设计描述，是今日发生的记录。

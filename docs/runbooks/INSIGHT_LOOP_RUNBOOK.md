# 方案设计五道闸 Runbook（INSIGHT_LOOP_RUNBOOK）

> 来源：2026-10-06 四 agent（cc/codex/opencode/atomcode）同题方案对比复盘。
> 适用：任何「以X为内核的方案设计 / 采集调度 / 机制固化」类任务。
> 教训库：lc 上一轮推荐「以灵研 intel 为内核」，opencode 子代理实测证明它不是网络采集器、持久化为空——载体判断未实测即推荐，返工 1.5 天。

## 门 1 · 复用普查（方案前置，强制）

方案里每一个「以X为内核」「复用X」的判断，必须先实测 X 的真实状态：

```bash
# 1) X 有源吗？有持久化吗？在跑吗？产出了什么？
find /home/ai/<owner> -maxdepth 3 -name "*<X>*"
ls /home/ai/<owner>/<X>/                      # 模块齐≠能用
wc -c <产出目录>/*.jsonl 2>/dev/null          # 0 字节=空转实锤（opencode 实证法）
tail -5 <cron日志路径>                        # cron.log 比代码诚实
# 2) 大方案主会话只做综合与设计，摸底派 sub_agent（换回源码级事实）
```

判据：主循环工具预算留给设计；摸底结论必须带「谁在跑/产出非空/持久化在哪」三问答案。

## 门 2 · 网络实测（采集类方案强制）

- 通道先行：采集器第一动作=通道探测（proxy3/直连/代理），失败降级并记录降级事件，禁止静默。
- 每条采集记录带 `fetch_channel` 字段；单源失败「仅记录缺采」不阻断日报。
- 已知实证：HF/arXiv 直连曾 timeout、GitHub 匿名 403 限流 → GitHub 带 `GITHUB_TOKEN` env；HF 用 `sort=createdAt` 才是增量。
- 历史 0 字节 jsonl = 网络故障证据链，别只看「代码逻辑对」。

## 门 3 · 调度冲突检查（定时任务强制）

```bash
# 主会话黑名单拦 crontab/systemctl 时：换路径重试，不许基于假设给时间点
cat /etc/crontab /etc/cron.d/* 2>/dev/null        # 文件视角（主会话可用）
crontab -l                                        # 用户视角（可派 sub_agent 或请求用户贴）
```

本机已知锚点（2026-10-06 实测）：`17 */6` opencode 客户端模型同步（SDT-lc-007，撞 06:17/12:17/18:17/00:17）、`0 */6` proxy3 路由同步、`50 9` zai_grabber、`08:00` small_model_news 采集（待迁移并入本闭环）、`*/30` health_inspect。
判据：新 cron 落点必须与上述锚点错峰 ≥5 分钟，并在台账登记。

## 门 4 · 交互裁定（方案定稿前强制）

「末尾开放问题」升级为 `request_user_input` 当场裁定（atomcode 模式）：
方案→结构化选择题→用户选定→参数立即锁进方案 v1.0→`memory remember` 写入项目记忆。
禁止：把决策悬置成 4 条开放问题等用户自发回复（lc/cc/codex/opencode 四家通病）。

## 门 5 · 红线清单（涉外部数据/敏感业务强制）

- 敏感线（医疗/法律/气功内容/主人分身语料）：只采元数据，正文不出网，使用需显式授权（灵安把关）。
- 摘要类输出强制「源 URL + 抓取时间 + ✅已验证/⚠未验证」双标注，无锚点结论视为缺陷。
- 爬取合规：只采公开 API/RSS、限速、遵守 ToS；LLM 摘要设 token 预算，超限降级为只入库。

## 效率基准（对比实测）

| 模式 | 调用量 | 效果 |
|---|---|---|
| atomcode：52 工具并行 + request_user_input | ~52/4min | 方案定稿+入记忆，唯一真闭环 |
| cc：主循环 8 + 3 Explore 子代理 | 薄主循环 | 3 个改变方案形状的事实 |
| codex：8 次高密度读 | 最少 | 三轴打分+预算闸门，推断全带⚠ |
| opencode：子代理源码级+运行时证据 | 中 | 行号级证据，0字节实证 |
| lc（反例）：单线程 15 次、零实测载体 | — | 内核推荐错误+调度基于假设 |

## 本机制产物锚点

- 登记簿：`lingresearch/data/business_line_profiles.yaml`（22 线，owner 裁定=灵研）
- 采集器：`lingresearch/scripts/ling_insight_daily.py`（每日 06:23，错峰 SDT-lc-007）
- 台账：`lingresearch/data/insight_ledger/`（情报→决策→实施→验证 四段式）
- 用户四项裁定（2026-10-06）：宿主=灵研 / 06:17（实施错峰为 06:23）/ LLM摘要启用 / 22 线全量

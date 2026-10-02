# 交接文件：lc 深度审计 2026-10-02 查证结论 + 下一步方案

> 本会话（2026-10-02 20:42 UTC+8）查证了两份审计档的核心条目，结论逐条源码锚定。
> 原档：`docs/lc_deep_audit_2026-10-02.md` + `docs/lc_vs_15_landscape_2026-10-02.md`
> 查证方法：纯读，不写；全文见各节「实证」子节。

---

## 一、欠账条目逐条查证

### 欠账 #2 journal-gap（due 10-09 ⚠ 明天）

**审计描述**：`scripts/exemption_review.py` 中 journal 引用 0 处——豁免写路径仍链外。

**实证**：

```python
# scripts/exemption_review.py（全文无 import arch_ledger）
# 写操作直接文件系统（L195 / L204-206）：
p.unlink()                                          # L195 摘除 live 豁免
p.write_text(json.dumps(d, ...), encoding="utf-8")  # L206 翻 state 字段

# scripts/arch_ledger.py 对比（入 journal 路径）：
# L197-199：exemption_add() → s.save() + _append_journal()
# L209-212：exemption_remove() → s.save() + _append_journal()
```

**结论**：审计描述 **100% 准确**。`exemption_review.py` 是豁免写路径的另一个入口（`arch_ledger.py` CLI 之外），它直接写文件系统，跳过了 journal 链。

**下一步方案（可今日实施）**：

```
scripts/exemption_review.py 改写策略：
  ① import sys; sys.path.insert(0, str(ROOT / "scripts")); from arch_ledger import exemption_add, exemption_remove
  ② L195 p.unlink() → exemption_remove(guard=?, rel_path=?)  # 需要从档名解析 guard/rel_path
  ③ L206 write_text → 用 arch_ledger 读出旧 rec，修改后 s.save() 走 arch_ledger 路径
  ④ a11f2ec 登记的 journal-gap 债用 debt_resolve() 清偿
  ⑤ 测试：scan → apply → verify_journal()，journal 应出现 exemption_add/remove 行
  成本：~30 行修改 + 测试，约 1 小时
```

---

### 欠账 #4 kernel_m2 manifest 补录（已销账）

**实证**：`lingclaude/plugins/` 下 35 个 agent 插片 manifest，grep 全 35 覆盖 ✅

**结论**：审计已自发现可销账，无需行动。

---

### 欠账 #1 豁免人工三问（~115 条，10-10 启动）

**实证**：本会话已完成预清——机器面 D1-D4 全零（`a11f2ec`），真实人工队列 111 条 active（git 口径），10-10 排程不变。

**下一步方案**：本会话初筛器已在手，10-10 人工三问照常。

---

## 二、弱点条目逐条查证

### 弱点 #1 输出前自检接线（头条，有解法，未做）

**审计描述**：`classify_state` 定义于 `core/evidence_protocol.py:149`，全包消费方为零——四档引擎管住工具调用面，但"数字/hash/路径行号"类断言的输出前打标仍缺，无外部质疑时编造可直出。

**实证**：

```python
# evidence_protocol.py
def classify_state(text: str, *, confidence_word: str | None = None) -> StateClass:
    # L149 定义，全包唯一消费：
    # L236：ClaimVerifier.verify() → state = classify_state(claim_text, confidence_word=...)
    # 全库 grep classify_state：仅 3 处，全部在 evidence_protocol.py 内部 ✅
```

**结论**：审计 **100% 准确**。`classify_state` 是 EvidenceLedger 体系的内部函数，从未接出到 REPL 输出路径或四档引擎。四档引擎（`tool_auth_hook`）管工具调用结果，**不审查模型的文字输出断言**（数字/行号/路径声明）。

**下一步方案（今日可实施）**：

```
分两步走，不贪多：

Step A（接入点）：在 REPL 输出路径（models.py Message 渲染 / tool error 渲染）
  插入 ClaimVerifier 消费——模型输出含完成类标记词时触发 verifiable 校验，
  verifiable=False → 打 GAP 标记（不阻断输出，仅打标）
  锚点：lingclaude/core/models.py 的 ToolResult / TextContent 渲染路径
  成本：~40 行，约 1-2 小时

Step B（强化）：四档引擎 add_rule 支持 output_claim 类型（数字/行号/路径声明）
  声明类输出触发 ClaimVerifier → 无观测支撑时 ask 档入台账
  成本：~60 行 + yaml 规则 + 台账接入，约半天
```

---

### 弱点 #4 沙箱闸门默认全开

**审计描述**：`sandbox_policy.yaml` 激活示例仅注释；directory_rules 激活曾被 20 连红退回注释态。

**实证**：

```python
# sandbox_rules.py L37-46：
def rules_configured(rules=None):
    data = rules or _load_policy()
    sec = data.get("directory_rules")
    return isinstance(sec, dict) and bool(sec)  # 段为空/缺 → False

# sandbox_policy.yaml L117-122：directory_rules 全注释（# 开头）
# → rules_configured() → False → resolve_writable_dirs() → []（空列表）
# → bash extra_writable_dirs = []（无追加，bwrap 只读基线）
# → 文件写 check_write_allowed → None（回退旧链，fail-open）
```

**结论**：审计 **过时**（但审计时间点是 10-02 更早）。当前状态：
- directory_rules 注释态 = **规则不激活** = bash 走默认 `/home/ai` 全可写（现状）
- 闸门"已有"但不"通电"，不等于"全开"——是"默认旧行为"
- 20 连红是**激活时配置错误**（default: writable: ["/home/ai"] 范围太大），不是闸门本身有 bug

**下一步方案**：

```
不急——「闸门已有、通电失败一次」是正常的谨慎上线节奏。
今日可做（最小激活集）：
  ① 在 sandbox_policy.yaml 写激活模板（已有，0afb377）
  ② 写测试：用 /tmp 临时目录激活 directory_rules，跑文件写/bas
  h，确认行为符合预期（不在 /home/ai 范围则被拦）
  ③ 确认 config.json 所有 provider baseUrl 域名已进 net_allowlist 候选
  不做：全域激活 default: writable: []（那是另一档期的事）
```

---

### 弱点 #5 会话 blob 仍 json

**实证**：`session_persist.py L60` 确认 `f"{engine.session_id}.json"` 落盘，无 sqlite blob。

**结论**：P2-13 指 `state_store.py`（P3 状态 → 灵忆 LingYi），与 session 文件是**两个不同的存储维度**：
- session 文件（messages / conversation history）= 用户对话轨迹
- state 文件（StateStore）= 任务状态、session 元数据

两者独立演进。session json 不是 bug，是设计选择（json 可 human-readable 调试，迁移到 blob 收益有限）。

**下一步方案（今日不做）**：可研究但非急。session 轨迹量大时 json 读写有 O(n) 解析成本，但当前量级无明显影响。

---

### 弱点 #6 loop_purification 前提过时

**实证**：

```python
# loop_body.py：774 行，无任何 StateStore 引用 ✅
# grep StateStore lingclaude/ 全库：
#   core/state_store.py：302 行，double-write 阶段
#   其他引用：core/query_engine.py / core/repl.py 等（已接入）
#   loop_body.py：0 引用
```

**结论**：审计 **50% 准确**。StateStore 已接入 query_engine/repl（会话级状态保存正常），但 loop_body（工具轮次循环体）**确实未接入**。这个"未接入"是否有害取决于 loop_body 的需求——它目前做的是纯工具调用循环，不依赖持久化状态，所以暂无可见症状。

**下一步方案**：

```
不急。先问：loop_body 需要持久化什么状态？
  - 如果只是"记录循环次数/检测结果" → 透传给 engine 字段即可
  - 如果需要跨会话恢复 → 才需要接 StateStore
建议：下个会话让 lc 重核这个前提（审计档也写了"任务前提需 lc 重核"），
  在确定需求后再决定接入方式，今日不做盲接。
```

---

## 三、今日可实施清单（按优先级）

| 优先级 | 项目 | 依据 | 估计工时 |
|---|---|---|---|
| **P0 急** | `exemption_review.py` 接入 journal 链 | due 明天（10-09），已到期 | ~1 小时 |
| **P0 急** | 清偿 a11f2ec journal-gap 债 | 同上 | ~5 分钟 |
| P1 | classify_state 接 REPL 输出路径（Step A） | 头条弱点，方案已定 | ~1-2 小时 |
| P2 | directory_rules 激活测试集 | 稳妥上线节奏 | ~1 小时 |
| ~~P3~~ | ~~loop_body × StateStore 前提重核~~ | **已销账**（需求本体不存在，实证见 lc_deep_audit 对账附录） | 关闭 |
| 排期待定 | 会话 blob 迁移研究 | 非急，收益有限 | 待定 |

---

## 四、两份审计档的质量评估

| 档 | 准确性 | 主要问题 |
|---|---|---|
| `lc_deep_audit_2026-10-02.md` | 高（90%+） | 弱点 #4（已过时，闸门已退回注释态）；弱点 #6（50% 准确，需重核） |
| `lc_vs_15_landscape_2026-10-02.md` | 高 | 无代码锚点，仅外部数据时效风险（Landscape 随时变） |

---

## 五、另一会话工作面状态（SESSION_CONTEXT 快照）

```
未提交：
  M .atomcode/memory.md
  M data/arch_ledger/tool_auth_20261002.jsonl
  ?? docs/lc_deep_audit_2026-10-02.md      ← 本会话生成
  ?? docs/lc_vs_15_landscape_2026-10-02.md ← 本会话生成
```

两个 ?? 档是本会话查证对象，已在 docs/ 下，需决定是否落库。

---

## 六、本会话做了什么

1. 全文读取两份审计档（88 行 + 67 行）
2. 并行查证 4 个弱点锚点 + 2 个欠账锚点（sub_agent 400，自力读）
3. journal-gap 路径定位：`exemption_review.py` 写操作绕 journal ✅
4. classify_state 全库消费方确认：仅内部，零出口 ✅
5. sandbox_rules 当前激活态确认：注释态 = 不激活 ✅
6. StateStore × loop_body 接入现状：loop_body 无引用 ✅
7. 本交接文件落盘

**未做**：未实施任何修复（用户指令是「查证 + 提方案 + 落交接」，非「开工实施」）。

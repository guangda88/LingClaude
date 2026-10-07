# 外部项目参考价值评估：Spec Kit / Caddy / GODMOD3

> 2026-10-07 灵克(lingclaude) 调研整理。三个项目均已上网核实，与视频描述的偏差已标注。

---

## 一、GitHub Spec Kit — 参考价值：⭐ 高（强烈建议纳入流程）

**GitHub**: https://github.com/github/spec-kit
**文档**: https://github.github.io/spec-kit/

### 1.1 项目核实结论

- GitHub 官方开源的规范驱动开发（SDD）工具包，定位是给 AI 编码 Agent 的"流程骨架"。
- 视频说"固定四阶段"已过时：现行核心流程为 **Specify → Plan → Tasks → Implement → Converge** 五阶段，外加每个项目一次的 **Constitution（立宪）**。
- 每阶段产出一份 Markdown 工件（spec.md → plan.md → tasks.md），层层引用、喂给下一阶段，让 Agent 拿到结构化上下文而非临时拼 prompt——这正是"上下文丢失、幻觉"的解法。
- `/converge` 会拿**代码库现状反向核对** spec/plan/tasks，缺口自动追加进 tasks.md，再 implement、再 converge，循环直到报告 Converged。
- 另有 bug-fix（评估根因→限定修复→记录验证）和 idea-assessment（证据支撑的 go/clarify/stop 决策）两个可选扩展流程。
- 兼容 38 款 Agent（Copilot、Codex、Cursor、Claude 等），未支持的有 `generic` 兜底集成。

### 1.2 与灵族现有体系的对照

| Spec Kit 概念 | 灵族已有对应物 | 差距 / 可借鉴点 |
|---|---|---|
| Constitution（项目宪法，一次立宪全局生效） | AGENTS.md / 铁律 J1-J5 / 守卫 H1-H17 | **已有且更强**，无需引入 |
| 每阶段 Markdown 工件链（spec→plan→tasks） | lingmate session record、verify_ledger | 灵族重"验证记录"、轻"意图工件链"。多日长任务断线后靠会话记录恢复，而非结构化 spec |
| tasks.md 的 dependency-order + checkbox 状态门控（implement 前检查未勾选项即停） | todowrite | 门控思想值得抄：**未勾选先决任务即拒绝继续**，防跳步 |
| Converge（拿交付物反查规格找缺口） | SDT-lc-006 返审触发器（fingerprint 变化驱动） | 返审是"变化驱动"，converge 是"目标驱动"。**H17 闭环申报管了"申报证据"，没管"交付物 vs 原始意图"的对照**——这是缺失的半边 |
| Bug-fix 流程（根因→限定修复→记录验证） | REPRODUCE→DIAGNOSE→FIX→VERIFY | 基本同构 |

### 1.3 落地建议方案（不引入工具本身，吸收机制）

**P1（建议本季度做）：为 SDT 长任务建立三工件模板**

- 位置：`docs/sdt/templates/` 下放 `spec.tmpl.md` / `plan.tmpl.md` / `tasks.tmpl.md`。
- 适用对象：灵元推理栈这类多日跨会话任务（正是 task#3"改到一半忘了为什么改"踩坑场景）。
- 触发规则：预计 >1 天或跨 ≥3 会话的 SDT 任务，启动时必产出三件套；tasks.md 带复选框和依赖序号。
- 收敛步骤：任务自报完成前，对照 spec.md 逐条核对交付物（写入 H17 闭环申报的证据链），缺口追加进 tasks.md 而不是口头带过。

**P2（择机）：把 converge 思想并入 SDT-lc-006 返审触发器**

- 现有 fingerprint 变化触发保持不变，另加一类触发：**任务收尾时自动跑一次"spec 反查"**——若任务有三工件，则核对其 checklist 完成率，未 100% 即挂 arch_audit_task。

**不建议做的**：直接装 spec-kit CLI。它的价值在流程思想，不在工具；灵族已有 AtomCode/lingxi 自己的执行栈，再叠一层 CLI 只增加体系复杂度（违反薄主干）。

---

## 二、Caddy — 参考价值：⭐ 中-高（对外开放计划触发升级）

**GitHub**: https://github.com/caddyserver/caddy
**官网**: https://caddyserver.com/

### 2.1 项目核实结论

视频描述基本准确。核心事实：

- 2015 年起首创自动 HTTPS：绑定域名即自动 ACME 申请/续期 Let's Encrypt 证书、HTTP 自动跳转，"配置即忘"。
- Caddyfile 接近自然写法，反代/静态文件几行配完；原生 HTTP/2、HTTP/3、TLS 1.3、WebSocket 代理。
- **API 动态更新配置**（`/load` 端点热更新，无需重启）——适合自动化运维，SDT 脚本可编程管理。
- 单二进制零依赖，Linux/Windows/macOS/容器通吃。
- 缺点属实：超高流量下 Go GC 开销不如 Nginx；基础内存几十 MB；复杂 rewrite/精细缓存生态偏弱；部分插件需 xcaddy 重编译。
- 前置条件（对外服务必查）：**服务器在国内时，域名需 ICP 备案**，否则运营商拦 80/443，ACME 的 HTTP/TLS-ALPN challenge 也会失败。解法：先备案，或用 Caddy 原生 DNS challenge（装对应 DNS 插件）。

### 2.2 选型判断：用 Caddy，不自研

灵族现状（2026-10-07 更新后评估）：已有域名 **lingflow.top** 和网站，灵依、灵声等服务有对外开放计划。

| 灵族现实 | Caddy 对应能力 |
|---|---|
| lingflow.top 对外开放 | 域名即自动证书，零维护 |
| 灵依/灵声等多服务对外 | 单 Caddy 统一入口反代，按子域名或路径分发 |
| Agent 团队而非运维团队 | ACME 续期/证书落盘/到期告警自研坑极多，Caddy 全部内置 |
| 服务增减频繁 | Caddyfile 几行加一个反代，或走 API 热更新适配 SDT 脚本 |
| 灵族流量量级 | 初期对外流量对 Caddy 零负担，GC 劣势只在超高流量显现 |

自研的"正确姿势"：TLS/ACME/反代这类轮子已圆，重造只有成本。若需要深度定制的鉴权/计费逻辑，**Caddy 管接入层，定制逻辑做成后端中间件**（可用 `forward_auth` 对接自建鉴权服务），而不是自己写 Web 服务器。

### 2.3 落地建议方案

**架构原则**：
- 80/443 归 Caddy 独占；8765 proxy3 / 9530 灵忆 / 23458 webui 等保持**内网监听不对外**，由 Caddy 按域名/路径分发。
- 对外服务的"门"统一放 Caddy 层（basic auth / API key / forward_auth），不让各服务各写一套。

**分步实施**：
1. **试水**（1 天）：先拿 `test.lingflow.top`——装 Caddy → 两行 Caddyfile 反代一个现有服务 → 验证证书自动签发。若 80/443 被拦（未备案），先确认备案状态或改 DNS challenge。
2. **试运行一周**：观察证书续期、日志、内存占用，纳入 `scripts/health_inspect.py` 一条"Caddy 入口健康"检查。
3. **迁入灵依/灵声**：逐个加反代 + 定对外鉴权方案（建议 forward_auth 对接统一鉴权插片，可借 mcp-wrap 思路做成铁律合规的 MCP/插片）。
4. **纳入 SDT**：SDT-lc-002 巡检加 Caddy 健康项；备份清单只加两样——Caddyfile + 证书目录。

**示例 Caddyfile**（试水用）：

```caddyfile
test.lingflow.top {
    reverse_proxy 127.0.0.1:23458
}
```

---

## 三、GODMOD3 (Godmode) — 参考价值：⭐ 中（借思想，不借代码；注意安全边界）

**GitHub**: https://github.com/elder-plinius/g0dm0d3

### 3.1 项目核实结论

视频描述基本准确，一处重要定位偏差需纠正：**这不是普通聚合聊天界面，本体是红队/越狱研究工具**（作者是知名 LLM 越狱研究者 Pliny；Parseltongue 是红队输入扰动引擎）。

- 确为单 `index.html`、AGPL-3.0、无构建步骤、密钥存浏览器 localStorage、经 OpenRouter 聚合 50-60+ 模型（另有 Venice 与本地 OpenAI 兼容端点）。纯静态部署即可用，遥测是可选 Cloudflare Function，local-only 模式可全关。
- **GODMODE CLASSIC**：5 个验证过的 prompt+模型组合并行竞赛，最优胜出。
- **ULTRAPLINIAN**：5 层级（12-60 模型）多模型评估引擎，复合指标打分选赢家。
- AutoTune 自适应采样、STM 语义转换、4 主题等属实。

### 3.2 对灵族的参考点（取其架构思想）

1. **多模型竞赛 + 评审收敛**（最有价值）：审计结论、架构评审、返审判定这类高风险判断，可让 3-5 个便宜模型并行跑、一个裁判模型复合打分选优——降低单模型幻觉，成本可控。灵族已有 proxy3 聚合多模型的基础设施（MODEL_REGISTRY 13 端口 + free_pool 目录闸门），**缺的只是竞赛/评审调度层**。
2. **单文件 HTML 零依赖交付**：与灵元铁律"薄主干·分形插片"哲学同构，可作灵族对外 UI 原型参考。
3. ⚠️ **安全边界**：Parseltongue 扰动引擎**禁止用于任何生产模型或对外服务**（lingflow.top 开放后尤其注意）；只借鉴 Classic/Ultraplinian 的"并行竞赛+评审收敛"架构。AGPL-3.0 许可证若借鉴代码需注意传染性——建议只读思想、不抄代码。

### 3.3 落地建议方案

**P2（择机做）：基于 proxy3 建轻量"评审竞赛"插片**

- 形态：一个 Python 插片（对齐 mcp-wrap 铁律要求），输入=待评审问题 + N 个候选模型 ID（从 proxy3 目录取），输出=各模型回答 + 裁判打分 + 胜者。
- 裁判模型用现有 deepseek-v4-flash（eval_runner_v2 已验证的裁判链，注意当时教训：显式传 model，ERROR 前缀先判 -1）。
- 首个应用场景：SDT-lc-006 返审判定 + 架构评审（AUDIT_20260928 这类）双盲复核。
- 规模控制：默认 3-5 模型竞赛即可，不追求 GODMOD3 的 55 模型规模。

---

## 四、GODMOD3 × browse-agg × nav.lingflow.top 三方对照（2026-10-07 补）

| 维度 | GODMOD3 | browse-agg (127.0.0.1:13461) | nav.lingflow.top |
|---|---|---|---|
| 本质 | 单 HTML，浏览器内直连 OpenRouter/Venice/本地端点 | Playwright 承载 12 个网页版 AI 登录态 → OpenAI 兼容 API | 导航站：提示词自动带入/复制到各平台，手机号+扫码登录 |
| 聚合方式 | API key → HTTP API | 网页会话 → 浏览器自动化 → API | 不聚合，纯入口分发 + 提示词携带 |
| 账号/密钥 | localStorage | 本地 state 目录，cron 3h 续签 | 本地隔离目录，明确"不代理账号" |
| 竞赛/评审编排 | ✅ Classic / Ultraplinian | ❌ 单请求单 provider | ❌ |

三者同题（一个入口用多 AI 平台）且同立场（凭证留本地）。差异在路线：GODMOD3=API 路线，上限在编排层；browse-agg=网页会话路线，价值在免费额度与无 API 平台，代价是 DOM/风控/登录态脆弱；nav=纯前端分发，最轻最合规。

**互相借鉴**：
1. browse-agg 加 Classic 式竞赛路由（2-3 个已登录 provider 并行 + 裁判打分）——已是 OpenAI 兼容接口，比等 proxy3 更现成，可直接落"评审竞赛插片"。
2. nav 面向外部用户轻分发，browse-agg 面向内部深集成；两者"账号本地隔离"红线在 lingflow.top 对外后继续守。
3. ⚠️ **合规/安全**：browse-agg 网页登录态转 API 的 ToS 风险高于自带 key 直连；网页会话 cookie 泄露危害大于 API key——**browse-agg 只留内网，不进 Caddy 对外清单**，若必须暴露则强制过鉴权门控。GODMOD3 的 AGPL 传染性红线不变（只借思想不抄代码）。

---

## 五、总结与优先级

| 项目 | GitHub | 参考价值 | 一句话结论 | 优先级 |
|---|---|---|---|---|
| **Spec Kit** | github/spec-kit | ⭐ 高 | spec→plan→tasks 工件链 + converge 反查，直接补 H17 闭环申报"意图对照"缺失半边 | P1：三工件模板纳入 SDT 长任务 |
| **Caddy** | caddyserver/caddy | ⭐ 中-高 | 对外开放计划触发：直接选 Caddy 不自研，先试水子域验证备案/证书链路 | P1：试水 → 一周试运行 → 迁入灵依/灵声 |
| **GODMOD3** | elder-plinius/g0dm0d3 | ⭐ 中 | 借"多模型竞赛+评审收敛"思想建在 proxy3 上；本体是红队工具，勿复用其扰动引擎与代码（AGPL） | P2：轻量评审插片 |

**共同哲学印证**：三个项目殊途同归——结构化工件对抗上下文丢失（Spec Kit）、自动化对抗运维负担（Caddy）、多源交叉对抗单点幻觉（GODMOD3）。这与灵族已有的铁律/守卫/台账体系方向一致，都是"用工程机制替代模型自觉"。

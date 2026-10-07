---
name: feishu-doc-authoring
description: 以「真人编辑形态」读写飞书云文档：生成带真标题/真表格/真图片的文档。四条路径决策：真人粘贴(默认,保URL)/docx导入(批量图文,换URL)/API原位重建(保URL+程序化,首选本文档Path D)/官方MCP(读/清单)。适用关键词：飞书文档、wiki、云文档、图文并茂、作业提交、原位重建、user_access_token。
version: 2.0.0
author: 灵克
date: 2026-10-07
changelog: v2.0 增补 API 原位重建全链（Path D）：OAuth 用户令牌获取、blocks 契约、幂等收敛模式、图片块平台限制。v1 的 CDP 粘贴路径保留为 Path A。
evidence: 实测于 o094sc9k2y.feishu.cn 租户 2026-10-07；A/B 两篇 wiki 说明书原位重建收敛（22/23 块，match=True）；脚本均在 scripts/
---

# 飞书云文档自动化编写 SKILL

## 0. 决策树（先选路径，再动手）

```
要写飞书云文档
├─ 已有目标文档URL，要程序化重写/更新内容，且URL不能变？
│   └─ Path D API原位重建（本文档新增，首选）：OAuth用户令牌 + 清块/重建 + 幂等收敛
├─ 已有目标文档URL，一次性替换、文档较短、9228浏览器可用？
│   └─ Path A 真人粘贴：保URL、触发原生Markdown转换
├─ 从零新建富图文长文档 / 批量多份 / 需要真图片块？
│   └─ Path B docx导入：python-docx 生成 → 飞书云空间「导入」→ 新文档（注意：产物是新URL）
└─ 需要程序化清单操作(读/搜索/权限)，且有开发者应用凭证？
    └─ Path C 官方MCP：npx @larksuiteoapi/lark-mcp（不支持直接编辑云文档正文）
```

## 0.5 Path D：API 原位重建（v2.0 核心增量，保URL+程序化）

### 0.5.1 身份与授权（先拿钥匙）

- tenant_access_token（app_identity）对 wiki 空间文档默认**只读**（131006 tenant needs edit permission），即使知识空间加了应用也不稳——**用户身份是正解**（文档所有者的 user_access_token 全链可写，且实测无限流墙）
- user_access_token 获取全流程（scripts/oauth_listen_v4.py 一键封装）：
  1. open.feishu.cn 应用「安全设置」登记 `http://localhost:18765/callback`（**保存后刷新复读确认落库**，DOM 看到≠存进服务端）
  2. 授权链接 scope 用实证组合：`docx:document wiki:wiki drive:file:upload docs:permission.setting:write_only`（有效用户授权串见官方权限页「访问凭证」列，标 user 的才对）
  3. 监听器必须跑在**宿主网络**（run_in_background 工具启动），bash 沙箱里起的进程回调永远打不到
  4. 换 token：`POST /open-apis/authen/v2/oauth/token`，**Basic auth（app_id:app_secret）**，body 含 grant_type=authorization_code + code + **redirect_uri（必带，缺→400）**；成功响应**扁平结构**（顶层 code=0 + access_token，无 data 包裹）；**先落盘原始响应再解析**；code 一次性，监听器必须单实例（起前清场+端口探测）
  5. 令牌 2 小时有效，无 refresh_token（飞书不下发）——长任务设计成幂等可续跑，过期重新授权一次即可

### 0.5.2 blocks API 契约表（全部实测）

| 操作 | 端点/要点 |
| --- | --- |
| 读全量块 | `GET /docx/v1/documents/{obj}/blocks?page_size=500`（返回**扁平全量列表**：cell/段落混在页面子块里，diff 必须过滤 `parent_id==页面块ID`） |
| 插子块 | `POST /docx/v1/documents/{obj}/blocks/{页面块ID}/children`，body.blocks 用扁平结构 + children 端点自动拼接引用（见 scripts/api_rebuild_idempotent.py） |
| 删块 | `DELETE .../blocks/{页面块ID}/children/batch_delete`，startIndex/endIndex 排他边界；**end 不可指到图片块，不可删末块（末块粘滞）** |
| 改文本 | `PATCH .../blocks/{block_id}` 按块类型改 text elements |
| 表格 | 两步法：先插 table 空壳（property）→ 系统生成 cell→ 对每个 cell 段落逐格 PATCH 文本 |
| 改 wiki 标题 | `POST /wiki/v2/spaces/{sid}/nodes/{nid}/update_title`（tenants 也可用，需编辑权） |

块类型枚举（踩过坑的）：**有序列表=13**（14 是代码块！错误类型→1770001 永久失败，退避救不回）；文本=2、标题1-9级=3-11、无序列表=12、代码=14、引用=17、todo=18、表格=31、图片=27。

### 0.5.3 幂等收敛模式（核心方法论）

一次性大脚本必死于：限流、令牌过期、进程被清、锁窗口。正确姿势（scripts/api_rebuild_idempotent.py 完整实现）：

```
循环直到 cur_keys == want_keys:
  1. GET blocks → 过滤真子块 → cur_keys（快照）
  2. diff(cur, want)：尾部 junk 删（batch_delete 小批量，end 避开图片）、
     缺失块插（children 端点，小批量+重试）、可改造块 PATCH（旧末块→落款）
  3. 每步失败退避（10/30/60/180s），永久性 1770001 先查块类型/边界再重试
  4. 验收：cur==want 全量比对 + 表格逐格核对 → 落日志
```

配合纪律：动破坏性操作前先 `GET blocks` 存 JSON 备份；失败重试与首次尝试之间**必须复读现状**（revision 已变）；多进程并发写同一文档禁止（起前清场）。

### 0.5.4 硬限制（别再撞墙）

- **图片块创建在 wiki 挂载文档上被平台级封锁**：五身份组合实测全拒（tenant→自有/wiki、user→wiki、交叉组合），replace_image 只认已有图块。真图两条路：Path B docx 导入（换URL）或 Path A 真人粘贴（保URL）
- 末块粘滞：不能在 len 处插入、不能删最后一个孩子；对策=旧末块 PATCH 改造（如改成落款）
- tenant 身份对 wiki 文档的写权限不稳定（即使知识空间加了应用），一切以用户身份为准

## 1. 环境前置（四件套，缺一降级）

| 依赖 | 用途 | 检查命令 |
| --- | --- | --- |
| 9228 登录态 Chrome | 携带飞书会话 | `curl -s localhost:9228/json/version`（须在后台job内） |
| xclip + DISPLAY | 真实系统剪贴板 | `xclip -o -selection clipboard` |
| xdotool + DISPLAY | 受信任键盘事件 | `xdotool getactivewindow getwindowname` |
| python3 + markdown 库 | md→HTML/文本预处理 | `python3 -c "import markdown"` |

已知环境事实（2026-10-07 实测）：主 shell 与每次 bash 调用都是 bwrap --unshare-net 独立沙箱（网络隔离），CDP 与常驻服务必须走后台 job（run_in_background）——宿主网络命名空间；X11 :1 可从主 shell 直连（X socket 走文件系统）；系统剪贴板在 :1。

## 2. Path A：真人粘贴（保URL一次性替换）

原理：向系统剪贴板写入 Markdown 原文 → 用 XTEST 发受信任 Ctrl+V → 飞书检测到 Markdown 弹「一键转换」→ 点转换 → 生成真标题/真表格。全程 URL 不变、行为与真人一致。

操作序列（scripts/paste_v5.py 已封装）：
1. CDP `/json/list` 找目标 tab（附身已有 tab，勿新开）；tab 不在则 `/json/new` 打开
2. CDP `Page.bringToFront` + xdotool `windowactivate` Chrome 窗口（护栏：按键必须落对窗口）
3. 聚焦正文编辑器：`.zone-container.text-editor` 上发真实 mousedown/mouseup/click 序列
4. 光标定位：`ctrl+End`（文末）或全选 `ctrl+a`（整篇替换前必须已备份原文）
5. `printf %s "$MD" | xclip -selection clipboard -i`
6. `xdotool key ctrl+v` → 等 2-4 秒
7. DOM 探测「转换」按钮（文本含「转换」）→ click → 等 3 秒
8. 验证：标题元素数>0、表格元素数>0、正文无 `##` 残留

## 3. Path B：docx 导入（批量图文/真图片块唯一程序化通路）

1. python-docx 生成：Heading 1-3 层级、Table、`add_picture`（宽设 Inches(6)）；参考 scripts/make_docx.py
2. 样式统一：中文字体 Noto Sans CJK，图表配色 #3370ff/#34c724/#ff8800；参考 scripts/make_charts.py
3. 9228 打开 `o094sc9k2y.feishu.cn/drive` → 「新建」→「导入」→ 上传 .docx
4. 导入产物为新文档；如需保持原 URL，把导入结果全文复制粘贴回原文档（Path A 步骤 3-7）

## 4. Path C：官方 MCP（清单化操作）

- 包：`@larksuiteoapi/lark-mcp`（实测 latest 0.5.1，Beta）
- 前提：open.feishu.cn 注册自建应用拿 App ID/Secret；用户身份需 OAuth（redirect http://localhost:3000/callback）
- 能力边界（README 实测引用）：「Direct editing of Feishu cloud documents is not supported (only importing and reading)」——写正文走 Path D/A/B，MCP 适合读/检索/清单操作与导入新文档

## 5. 质检清单（交付前全过）

- API 路径（Path D）：`GET blocks` 全量比对 cur==want；表格逐格核对；wiki 节点 title 检查（不能是整段正文）
- 浏览器路径（Path A/B）：标题元素数 ≥ 章节数（非纯文本 ##）；表格元素数 = md 表格数；img 元素数 = 插图数；正文无 `##`、`|---|`、`**` 残留
- URL 未变（Path A/D）；或新文档已拿到分享链接（Path B）
- 截图/JSON 快照留证

## 6. 已知坑表（每条都实测踩过）

| 坑 | 症状 | 解法 |
| --- | --- | --- |
| bash 沙箱起监听器 | Chrome 回调 Connection refused | 常驻服务必须 run_in_background（宿主网络），起后同侧探测端口（404=有人应答≠refused） |
| 多 OAuth 监听实例 | code 被并发烧掉（20014） | 起前清场精确 kill（禁 pkill -f 模糊匹配，会误杀同名子串守护进程）+ 端口单实例确认 |
| v2 换 token 判成功失败 | code=0 却当失败重试烧 code | 响应是扁平结构：判顶层 code==0；先落盘原始响应再解析 |
| 换 token 400 | redirect_uri 缺失/不一致 | body 必带 redirect_uri 且与授权时逐字一致 |
| 有序列表插失败 1770001 | 同位置永久失败退避无用 | block_type 用 13（14 是代码块）；连续失败先查类型/边界再怀疑限流 |
| 删除 1770001 | end_index 指到图片/末块 | 排他边界避图片；末块粘滞→PATCH 改造 |
| diff 误删 cell 段落 | 对表格 cell 段落发删除被拒 | blocks API 是扁平全量列表，diff 只取 parent_id==页面块 |
| WS 帧未掩码 | BrokenPipeError 秒断 | 客户端帧必须 XOR 掩码（RFC 6455） |
| WS 缓冲不跨帧 | JSONDecodeError(char 0) | recv 缓冲区持久化，粘包按长度切 |
| Input.insertText 进 Quill | 内容进 ql-clipboard 隐身元素 | 选择器排除 .ql-clipboard；或改 XTEST |
| 合成 paste 事件 | 飞书不理会（isTrusted=false） | 只用 XTEST 受信任输入 |
| 程序化输入不触发 md 转换 | insertText 后纯文本无格式 | 必须走剪贴板+真实 Ctrl+V |

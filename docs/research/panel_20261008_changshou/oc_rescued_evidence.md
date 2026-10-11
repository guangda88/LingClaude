
===== TEXT [1r0bRj7z] =====
"# 任务：《与长寿智能人谈天》H5 产品完整详细方案（独立调研后成稿）

## 项目背景（用户原始需求，事实基础，不得篡改）
- 产品定位：H5 交互式 AI 口述回忆录，面向长者，以聊天方式采集人生故事，自动整理结构化回忆录
- 知识库备选：IMA 混元灵通知识库 / 飞书知识库 / Obsidian 本地知识库
- 时间轴：按智能气功发展大事记（语料源 https://zhinenggitea.iepose.cn/ZhinengBooks/Literature/src/branch/main/1.%E6%96%87%E6%9C%AC 已实测存在，含 1.教材/2.专著/3.杂志/4.其他/README.md/校对凡例.md）
- 语音：TTS 使用用户本人音色克隆
- 模型备选：OpenCode-Zen、OpenRouter 免费模型（注意：免费模型列表时效性强，你若引用具体模型 ID 必须标注为\"需实时核验\"）
- 要求：业界同类项目实现逻辑调研前置，再输出产品/技术/落地/风险完整方案

## 参考材料（四份竞品方案，仅供批判性参考，重点找其错误与盲区）
- deepseek 版：记忆分层/多智能体/IMA OpenAPI 细节
- 豆包版：统一记忆卡 schema/会话守护/知识库三选对比
- kimi 版：IMA OpenAPI 只增查不改删的边界/隐私分级通道/HereAfter 关停教训
- qwen 版：竞品表格/适老化参数/商业模式

材料位置：/home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/ 下 four_sources.md

## 交付要求
1. 调研业界同类项目（AI 回忆录/口述历史/AI 陪伴长者产品），列案例+实现逻辑+可借鉴点
2. 输出完整方案：产品定义、系统架构、Agent 拆分、记忆/知识库设计、语音方案（含本人音色克隆落地路径）、模型选型与路由、H5 适老化设计、数据与隐私合规、风险清单、里程碑排期
3. 对四份参考材料的分歧点给出你的仲裁（如：IMA 直连读写 vs 本地 RAG 主存；单模式 vs 双模式——本项目名含\"与智能人谈天\"，是否应做\"听他讲\"与\"与智能人谈天\"双模式）
4. 硬性要求：
   - 你不确定的外部事实（模型ID/API能力/法规条文/竞品数据）一律标注\"未验证\"，禁止编造具体数字
   - 方案落盘为 markdown 文件：/home/ai/lingclaude/docs/research/panel_20261008_changshou/answer_<你的名字>.md
   - 文末署名区写明：你的 agent 名、调用方式、耗时、自评置信度（0-100）
   - 至少 200 行，有明确章节结构

补充要求（oc 铁律：不给落盘指令就不落盘，必须显式写明）：把完整方案写入文件 /tmp/panel_oc.md（Markdown），文末署名区写明 agent 名/调用方式/耗时/自评置信度。"

----- TOOL bash [JRTSDhXI] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL bash [JRTSDhXI] state=running -----
IN: {"command": "ls -la /home/ai/lingclaude/docs/research/panel_20261008_changshou/ /home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/ 2>&1; date +%s"}
OUT(first 3000): 

----- TOOL bash [JRTSDhXI] state=running -----
IN: {"command": "ls -la /home/ai/lingclaude/docs/research/panel_20261008_changshou/ /home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/ 2>&1; date +%s"}
OUT(first 3000): 

----- TOOL bash [HzJS4Mxy] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL bash [JRTSDhXI] state=running -----
IN: {"command": "ls -la /home/ai/lingclaude/docs/research/panel_20261008_changshou/ /home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/ 2>&1; date +%s"}
OUT(first 3000): 

----- TOOL bash [JRTSDhXI] state=running -----
IN: {"command": "ls -la /home/ai/lingclaude/docs/research/panel_20261008_changshou/ /home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/ 2>&1; date +%s"}
OUT(first 3000): 

----- TOOL bash [JRTSDhXI] state=completed -----
IN: {"command": "ls -la /home/ai/lingclaude/docs/research/panel_20261008_changshou/ /home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/ 2>&1; date +%s"}
OUT(first 3000): /home/ai/lingclaude/docs/research/panel_20261008_changshou/:
总计 92
drwxrwxr-x 3 ai ai  4096 10月  8 16:34 .
drwxr-xr-x 4 ai ai  4096 10月  8 13:01 ..
-rw-rw-r-- 1 ai ai 21211 10月  8 16:29 answer_atomcode.md
-rw-rw-r-- 1 ai ai 23712 10月  8 16:26 answer_codex.md
-rw-rw-r-- 1 ai ai  3765 10月  8 16:25 LESSONS.md
-rw-rw-r-- 1 ai ai   145 10月  8 13:05 logs_ac.txt
-rw-rw-r-- 1 ai ai   432 10月  8 13:03 logs_atomcode.txt
-rw-rw-r-- 1 ai ai   236 10月  8 13:02 logs_codex.txt
-rw-rw-r-- 1 ai ai   113 10月  8 13:02 logs_oc.txt
drwxrwxr-x 2 ai ai  4096 10月  8 13:02 materials
-rwxrwxr-x 1 ai ai  1466 10月  8 13:07 run_on_host.sh
-rw-rw-r-- 1 ai ai  2016 10月  8 16:34 synthesis_draft.md
-rw-rw-r-- 1 ai ai  2457 10月  8 13:01 unified_prompt.md

/home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/:
总计 20
drwxrwxr-x 2 ai ai 4096 10月  8 13:02 .
drwxrwxr-x 3 ai ai 4096 10月  8 16:34 ..
-rw-rw-r-- 1 ai ai 9781 10月  8 13:02 four_sources.md
1791448564


----- TOOL bash [HzJS4Mxy] state=running -----
IN: {"command": "wc -l /home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/four_sources.md"}
OUT(first 3000): 

----- TOOL bash [HzJS4Mxy] state=running -----
IN: {"command": "wc -l /home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/four_sources.md"}
OUT(first 3000): 

----- TOOL bash [HzJS4Mxy] state=running -----
IN: {"command": "wc -l /home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/four_sources.md"}
OUT(first 3000): 

----- TOOL bash [HzJS4Mxy] state=completed -----
IN: {"command": "wc -l /home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/four_sources.md"}
OUT(first 3000): 54 /home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/four_sources.md


----- TOOL read [II8tdRnf] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL read [II8tdRnf] state=running -----
IN: {"filePath": "/home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/four_sources.md"}
OUT(first 3000): 

----- TOOL read [II8tdRnf] state=completed -----
IN: {"filePath": "/home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/four_sources.md"}
OUT(first 3000): <path>/home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/four_sources.md</path>
<type>file</type>
<content>
1: ## deepseek 版要点
2: 记忆分层：工作记忆/情景记忆/语义记忆/进化记忆四层（ChromaDB向量+结构化表）。
3: 五段思考链：感知→回忆→思考→表达→回看。
4: 四Agent：访谈Interviewer（情绪承接、未解线索追踪、单问题追问）、记录Scribe（结构化抽取+来源标注）、规划Planner（叙事空白/矛盾检测/下次对话建议）、写作SectionWriter（时间线编排、保留原话）。
5: StorySage五Agent参考；传家笔"不纳入回忆录"保护标记；SMILE三层记忆检索独立于LLM；ECFMem短长期记忆动态划分；GuideLLM回忆疗法目标导航。
6: 商业案例：慰康智能憶镜AI（2800元两本书+视频，方言兼容，共创模式）；奇妙啦比时空电话局（已故亲人声音通话，⚠伦理风险）。
7: IMA集成：OpenAPI base https://ima.qq.com/openapi/wiki/v1，接口 get_knowledge_base/create_media/add_knowledge/search，认证 clientid+apikey，MCP开源项目ima-kb-mcp。⚠其声称可"上传到IMA知识库"作为存储——未提改删限制。
8: Obsidian：Leonezz/obsidian-mcp-server插件、scribe-agent、dsh-plugin-vault-memory（记忆快照注入）。飞书：feishu-cli-bridge MCP。
9: 数字人：live2d-widget/ai-avatar-bot/l2d-widget，TTS振幅驱动口型；Hume EVI情感检测。
10: 模型路由表：interview=minimax/m2-her、extraction=gpt-oss-20b、writing=tng/r1t-chimera（⚠这些ID未验证，时效存疑）。
11: OpenCode Zen：base https://opencode.ai/zen/v1/，/connect 接入。
12: 合规：2026年7月15日《人工智能拟人化互动服务管理暂行办法》施行（⚠未验证）：禁模拟老年人亲属、健康使用提示、伦理审查。
13: 技术栈：Next.js15+TS+Tailwind / FastAPI / WebSocket / ChromaDB / Vercel+云服务器。
14: 路线图：P1核心对话2-3周、P2 H5语音2-3周、P3回忆录生成2周、P4优化测试1-2周。
15: 
16: ## 豆包版要点
17: 行业通用链路：会话引导访谈→语音采集转写→记忆抽取→知识库RAG持久记忆→回忆录组装→导出展示。
18: 访谈原则：不纠正老人记忆偏差、不辩论、顺着叙事温和追问；单次会话20-25分钟管控；疲惫自动建议休息。
19: 记忆卡yaml schema：记忆ID/会话时间/推测发生年代/地点/人物/事件摘要/口述原文/情绪标签/主题标签。
20: RAG：每次对话前向量检索3-5条历史记忆注入prompt，解决跨会话遗忘；知识库支持人工编辑修正。
21: 痛点规避：不依赖模型上下文窗口存记忆；错误记忆可修正；隐私默认私有；原始录音+文本双备份。
22: 知识库对比：IMA（wiki式页面、AI读写wiki、多人协作、需适配API）vs 飞书（API成熟、多维表格存记忆卡、额度限制）vs Obsidian（本地MD+向量库、完全自控、需自同步）。建议优先IMA。
23: 模型：OpenCode-Zen 三档 zen-fast(32K)/zen-default(128K)/zen-advanced(200K)，免费层每日100次（⚠未验证）；OpenRouter 免费每日50次/充10美元提至1000次（⚠未验证），推荐 qwen3.6-plus:free、deepseek-chat-v3.1:free（⚠这些ID未验证，可能已下架）。
24: 技术栈：Vue3/H5+MediaRecorder+jsPDF / FastAPI+openai-sdk / FAISS或pgvector / 腾讯云ASR。
25: 三期规划：原型1-4周→功能4-8周→优化迭代（方言/照片/权限/真实用户测试）。
26: 访谈Agent系统提示词全文（硬性规则：绝不编造、不纠正、口语化、温和追问、疲惫建议休息）。
27: 
28: ## kimi 版要点
29: 竞品表：清华老校友口述历史（人工，提纲规范化）；中传记忆小舟/数字孙辈（三触发器追问：信息密度/情感强度/人物频次；语音延迟0.8s；复述确认回收记忆点；动态库存整合校正；关联历史知识库补时代背景如粮票→经济政策）；商业代写机构（预采-正式采-交叉验证三阶段，方言采访）；StoryCorps（问题清单标准化，存档国会图书馆）；HereAfter AI（章节化问题库、真实音频存储绝不合成、无匹配礼貌婉拒不编造——**2026年关停**，教训：长久必须被设计出来：导出路径/接续计划/数据可携）；家庭DIY（菜谱等特色板块）。
30: 最重要教训：软件测试阶段老人"不喜欢对着冷冰冰的屏幕自言自语"，使用频次下降——最终方案改为AI辅助真人采访。原始录音本地+云端双备份+文字稿打印纸质件。
31: 双模式定位：模式A"听他说"（长者讲述→结构化回忆录）+ 模式B"与智能人谈天"（基于IMA混元灵通知识库做可溯源问答；该库含混元灵通/广大创建/Dr.Liu等内容，共496个内容，已开启成员加入确认需申请）。谈天中个人记忆可汇入时间线。
32: IMA关键边界：OpenAPI（base https://ima.qq.com/openapi/wiki/v1，Header ima-openapi-clientid+ima-openapi-apikey，search_knowledge/get_media_info/import_urls）**只支持增和查——创建/删除/修改/导出统统没有**；分享链接只给查看权不等于导出权。
33: 三路径：OpenAPI直连（受配额约束）/ 导出重建本地RAG（推荐，开源工具"万能导"按知识库/文件夹导出为MD保留目录）/ 腾讯元器低代码验证（M0一周）。
34: 模型对比表：OpenRouter free（nvidia/nemotron-3-ultra-550b-a55b:free 1M、thinkingmachines/inkling:free 1.05M图文音、qwen/qwen3.8-27b:free 262K；约20req/min、200req/day）vs OpenCode Zen免费池（deepseek-v4-flash-free、mimo-v2.5-free、nemotron-3-super-f

----- TOOL read [tlYSkQrh] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL read [tlYSkQrh] state=running -----
IN: {"filePath": "/home/ai/lingclaude/docs/research/panel_20261008_changshou/unified_prompt.md"}
OUT(first 3000): 

----- TOOL read [tlYSkQrh] state=completed -----
IN: {"filePath": "/home/ai/lingclaude/docs/research/panel_20261008_changshou/unified_prompt.md"}
OUT(first 3000): <path>/home/ai/lingclaude/docs/research/panel_20261008_changshou/unified_prompt.md</path>
<type>file</type>
<content>
1: # 任务：《与长寿智能人谈天》H5 产品完整详细方案（独立调研后成稿）
2: 
3: ## 项目背景（用户原始需求，事实基础，不得篡改）
4: - 产品定位：H5 交互式 AI 口述回忆录，面向长者，以聊天方式采集人生故事，自动整理结构化回忆录
5: - 知识库备选：IMA 混元灵通知识库 / 飞书知识库 / Obsidian 本地知识库
6: - 时间轴：按智能气功发展大事记（语料源 https://zhinenggitea.iepose.cn/ZhinengBooks/Literature/src/branch/main/1.%E6%96%87%E6%9C%AC 已实测存在，含 1.教材/2.专著/3.杂志/4.其他/README.md/校对凡例.md）
7: - 语音：TTS 使用用户本人音色克隆
8: - 模型备选：OpenCode-Zen、OpenRouter 免费模型（注意：免费模型列表时效性强，你若引用具体模型 ID 必须标注为"需实时核验"）
9: - 要求：业界同类项目实现逻辑调研前置，再输出产品/技术/落地/风险完整方案
10: 
11: ## 参考材料（四份竞品方案，仅供批判性参考，重点找其错误与盲区）
12: - deepseek 版：记忆分层/多智能体/IMA OpenAPI 细节
13: - 豆包版：统一记忆卡 schema/会话守护/知识库三选对比
14: - kimi 版：IMA OpenAPI 只增查不改删的边界/隐私分级通道/HereAfter 关停教训
15: - qwen 版：竞品表格/适老化参数/商业模式
16: 
17: 材料位置：/home/ai/lingclaude/docs/research/panel_20261008_changshou/materials/ 下 four_sources.md
18: 
19: ## 交付要求
20: 1. 调研业界同类项目（AI 回忆录/口述历史/AI 陪伴长者产品），列案例+实现逻辑+可借鉴点
21: 2. 输出完整方案：产品定义、系统架构、Agent 拆分、记忆/知识库设计、语音方案（含本人音色克隆落地路径）、模型选型与路由、H5 适老化设计、数据与隐私合规、风险清单、里程碑排期
22: 3. 对四份参考材料的分歧点给出你的仲裁（如：IMA 直连读写 vs 本地 RAG 主存；单模式 vs 双模式——本项目名含"与智能人谈天"，是否应做"听他讲"与"与智能人谈天"双模式）
23: 4. 硬性要求：
24:    - 你不确定的外部事实（模型ID/API能力/法规条文/竞品数据）一律标注"未验证"，禁止编造具体数字
25:    - 方案落盘为 markdown 文件：/home/ai/lingclaude/docs/research/panel_20261008_changshou/answer_<你的名字>.md
26:    - 文末署名区写明：你的 agent 名、调用方式、耗时、自评置信度（0-100）
27:    - 至少 200 行，有明确章节结构

(End of file - total 27 lines)
</content>

----- TOOL bash [yU4IYNkV] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL bash [yU4IYNkV] state=running -----
IN: {"command": "date +%s; date '+%F %T %Z'"}
OUT(first 3000): 

----- TOOL bash [yU4IYNkV] state=running -----
IN: {"command": "date +%s; date '+%F %T %Z'"}
OUT(first 3000): 

----- TOOL bash [yU4IYNkV] state=running -----
IN: {"command": "date +%s; date '+%F %T %Z'"}
OUT(first 3000): 

----- TOOL bash [yU4IYNkV] state=completed -----
IN: {"command": "date +%s; date '+%F %T %Z'"}
OUT(first 3000): 1791448577
2026-10-08 16:36:17 CST


----- TOOL websearch [F2axfwaJ] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL websearch [F2axfwaJ] state=running -----
IN: {"query": "AI memoir life story app for seniors interview 2026", "numResults": 8}
OUT(first 3000): 

----- TOOL websearch [F2axfwaJ] state=running -----
IN: {"query": "AI memoir life story app for seniors interview 2026", "numResults": 8}
OUT(first 3000): 

----- TOOL websearch [F2axfwaJ] state=completed -----
IN: {"query": "AI memoir life story app for seniors interview 2026", "numResults": 8}
OUT(first 3000): Title: Life Story Platform for Seniors | StoriedLife AI
URL: https://storiedlife.ai/whos-it-for/for-the-senior/
Published: 2026-04-25T00:00:00.000Z
Author: N/A
Highlights:
Life Story Platform for Seniors | StoriedLife AI
...
A Simple Way to Capture Your Legacy & Wisdom You’ve lived through decades of stories: moments of joy, challenge, adventure, growth, and love. You have stories your family treasures. Stories they’ll want to remember long after you tell them. StoriedLife gives you a simple way to capture those memories – at your own pace, in your own voice, one conversation at a time.
...
be tiring, frustrating, or time-consuming. Speaking is not. ​With Storied
...
- You simply talk
- Eva listens and asks gentle questions
- Your story is written for you
- Each conversation becomes a chapter
- Your natural voice brings emotion and personality

 It’s the easiest way to save your life story – even if you’ve never written a word in your life.

## Perfect for People 60-100

 StoriedLife was designed with elder adults in mind – respectful, accessible, and simple.

- Large, easy-to-read buttons
- Simple navigation
- Works well with hearing aids
- Clear, calm guidance
- No typing required
- Clear audio and gentle pacing

 Ideal for people with limited mobility, arthritis, or poor eyesight You don’t need to be “good with technology.” You only need your voice – and your memories.
...
You don’t have to sit for long recordings. You don’t need to tell your whole life story at once. You don’t need to “remember everything. StoriedLife gives you:
...
## Why Seniors Choose StoriedLife

We’re the only platform where people actually finish their story. 

Only 20% of users finish on other platforms, but 96% complete their story with StoriedLife. Sharing your life story shouldn’t feel like homework. Our conversational biographer keeps people engaged, supported, and excited to continue, which is why families actually complete their memoir with us.

Users stay engaged because they talk naturally with a guided biographer, they do not receive generic email prompts they ignore.

Stories go deeper because the biographer asks smart follow-up questions, not just a single static prompt.

Chapters are generated instantly after each conversation, creating momentum and letting users print their book whenever they’re ready.
...
## Record at Your Own Pace, Anywhere & Whenever You'd Like

Some seniors enjoy recording daily. With StoriedLife you have the flexibility to record:

- Every Day, once a week, once a month
- Only when a memory surfaces
- During holidays or special moments
- With family, or completely on their own

 You choose your pace, place, and timing. Your story grows with you, one memory at a time.
...
When you’re ready, all your chapters can be transformed into a beautiful keepsake book – a family heirloom your loved ones will ... forever. Your book
...
## Popular Story Chapters for Seniors

 To help you begin, here are some of the most loved story that are build w

----- TOOL websearch [ODAjv9xu] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL websearch [ODAjv9xu] state=running -----
IN: {"numResults": 6, "query": "HereAfter AI app shut down 2026 memoir stories data export"}
OUT(first 3000): 

----- TOOL websearch [ODAjv9xu] state=running -----
IN: {"numResults": 6, "query": "HereAfter AI app shut down 2026 memoir stories data export"}
OUT(first 3000): 

----- TOOL websearch [SQOZD2J3] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL websearch [SQOZD2J3] state=running -----
IN: {"numResults": 6, "query": "开源 声音克隆 TTS 2026 CosyVoice GPT-SoVITS F5-TTS 中文 本地部署"}
OUT(first 3000): 

----- TOOL websearch [SQOZD2J3] state=running -----
IN: {"numResults": 6, "query": "开源 声音克隆 TTS 2026 CosyVoice GPT-SoVITS F5-TTS 中文 本地部署"}
OUT(first 3000): 

----- TOOL websearch [ODAjv9xu] state=completed -----
IN: {"numResults": 6, "query": "HereAfter AI app shut down 2026 memoir stories data export"}
OUT(first 3000): Title: HereAfter AI Is Shutting Down: What Happened | Afterlife AI
URL: https://www.afterlife.ai/hereafter-ai-shutting-down
Published: 2026-07-18T02:51:39.000Z
Author: Chris Williams
Highlights:
HereAfter AI's website has been offline since August 18, 2026, and the only retrieval route the company named is an email to its support team. This guide gives you the dated record, the steps to get your family's recordings back, and the questions to ask before you trust anyone with a voice again.

HereAfter AI is shutting down. The company posted a farewell notice in July 2026, its website has been offline since August 18, 2026, and the only retrieval route it offered is an email to support@hereafter.ai. Send that request today from the address on your account, then save every file you receive in three places.

| HereAfter AI status | What we found | Checked |
| --- | --- | --- |
| Main website, hereafter.ai | Returns an HTTP 404 error | September 30 and October 1, 2026 |
| Web app, app.hereafter.ai | Still loads the HereAfter web app | October 1, 2026 |
| Retrieval route | An email to support@hereafter.ai, the only channel the farewell notice named | Notice recorded July 18, 2026 |
| Deadline to request recordings | None announced | July 18 to October 1, 2026 |
| Self-serve export | None offered in the notice; full MP3 downloads came only with the Unlimited plan while the app was sold | July 18, 2026 |
| Status on the Killed by AI tracker | Listed as a wind-down, with the website inaccessible from August 18, 2026 (Killed by AI) | October 2, 2026 |
...
No, HereAfter AI is not working as a product, and families should treat the service as closed. The main website, hereafter.ai, returned an HTTP 404 error on our checks on September 30 and October 1, 2026, after a farewell notice replaced the homepage in July 2026. The separate web app address, app.hereafter.ai, still loaded the HereAfter web app when we checked on October 1, 2026, so it is worth one attempt to sign in before anything else. The company has never announced a deadline, and that silence is the strongest reason to act this week.

The public tracker Killed by AI lists HereAfter AI as a wind-down and records that the website itself became inaccessible on August 18, 2026 (Killed by AI). The same entry says the founder left the company in January 2024 and that the app was last updated in September 2023. None of that changes what a family should do next, which is to request every file now and keep asking until the files arrive. For the full dated record, read the dated HereAfter AI timeline.
...
| Date | What happened | Source |
| --- | --- | --- |
| July 18, 2017 | Wired publishes James Vlahos's Dadbot story, the project that became HereAfter AI | Wired |
| While the app was sold | Plans cost $3.99, $5.99 and $7.99 a month, and full MP3 downloads came only with the $7.99 Unlimited plan | Our check of the live site, July 18, 2026 |
| September 2023 | The app receives its last update | Killed by AI |

----- TOOL websearch [SQOZD2J3] state=completed -----
IN: {"numResults": 6, "query": "开源 声音克隆 TTS 2026 CosyVoice GPT-SoVITS F5-TTS 中文 本地部署"}
OUT(first 3000): Title: 中文TTS天花板！CosyVoice 3 本地部署实测：3秒克隆声音，150ms流式合成，方言随便玩 | zhchxiao123
URL: https://zhchxiao123.github.io/posts/cosyvoice3-deployment-guide/
Published: 2026-04-29T12:37:00.000Z
Author: N/A
Highlights:
阿里通义实验室 CosyVoice 3 本地部署全攻略：从模型下载到 WebUI 启动，实测普通话+广东话+四川话合成，对比 GPT-SoVITS、ChatTTS、Fish Speech 四大中文 TTS 天王。
...
> 中文 CER 低至 0.71%，3 秒音频零样本克隆，150ms 流式首包，9 种语言 + 18 种方言——CosyVoice 3 是当下中文 TTS 的六边形战士。

最近想给本地 Agent 加个「嘴」，让它能开口说话。试了一圈开源中文 TTS，发现阿里通义实验室的 CosyVoice 3 简直离谱——3 秒音频就能克隆任何人的声音，打 5 个字就开始说话，粤语四川话上海话随便切。

关键是，俺老猪在 M3 Max 上跑通了，效果炸裂。今天就带你从零部署，顺便横向对比四大中文 TTS 天王。
...
| 维度 | 🥇 CosyVoice 3 | 🥈 GPT-SoVITS v2 | 🥉 ChatTTS | 4️⃣ Fish Speech 1.5 |
| --- | --- | --- | --- | --- |
| 团队 | 阿里通义实验室 | 社区驱动 | 2noise 团队 | Fish Audio |
| ⭐ Stars | 20.8K | 57K 🔥 | 39.2K | 30K |
| 中文 CER | 0.81% 👑 | ~1.2% | ~1.5% | ~1.4% |
| 语音克隆 | ✅ 3秒零样本 | ✅ 1分钟少样本 | ❌ | ✅ |
| 流式输出 | ✅ 150ms | ❌ | ❌ | ✅ |
| 方言支持 | ✅ 18种 | ✅ 粤语 | ❌ | ❌ |
| 情感控制 | ✅ 精细 | ✅ | ✅ 笑声/停顿 | ⚠️ 基础 |

一句话总结：CosyVoice 3 是六边形战士，GPT-SoVITS v2 是克隆之王，ChatTTS 是对话之王，Fish Speech 是省资源之王。
...
- 零样本克隆：给 3 秒音频，不用训练，直接复刻声音
- 流式合成：文本流式输入，音频流式输出，首包延迟 150ms
- 自然语言控制：说「用四川话，开心活泼的语气」，模型自己调
- 方言全覆盖：粤语、四川话、上海话、天津话、东北话……18 种方言
...
完才开始合成，CosyVoice 3 能做到「打 5
...
## 🧪 实战演练：M3 Max 本地部署全记录

理论说完了，上实操。俺老猪在 M3 Max（36GB 内存）上从头部署 CosyVoice 3，完整记录每一步。
...
```
# 克隆仓库（含 Matcha-TTS 子模块，CosyVoice 3 仍然依赖它）
git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git ~/data/app/cosyvoice
cd ~/data/app/cosyvoice
...
# 安装 PyTorch（MPS 后端）
pip install torch torchaudio
...
### 模型下载

CosyVoice 3 目前只有一个版本—— Fun-CosyVoice3-0.5B-2512（约 3.7GB），包含 base 和 RL 两个变体：
...
# Fun-CosyVoice3-0.5B（基础版 + RL 版，~3.7GB）
snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512',
                  local_dir='pretrained_models/Fun-CosyVoice3-0.5B')
...
### 实测 1：普通话 zero-shot 合成

CosyVoice 3 的 zero-shot 调用和旧版有个关键区别： prompt_text 不再是随便写一句话，而是固定格式 `'You are a helpful assistant.<|endofprompt|>你的参考文本'`。
...
# zero-shot：3 秒参考音频即可克隆声音
for i, j in enumerate(cosyvoice.inference_zero_shot(
    '八百标兵奔北坡，北坡炮兵并排跑，炮兵怕把标兵碰，标兵怕碰炮兵炮。',
    'You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。',
    './asset/zero_shot_prompt.wav',
    stream=False
)):
    torchaudio.save(f'zero_shot_{i}.wav', j['tts_speech'], cosyvoice.sample_rate)
...
| 实测指标 | 数据 |
| --- | --- |
| 合成文本 | 八百标兵奔北坡，北坡炮兵并排跑… |
| 音频时长 | 10.7 秒 |
| 采样率 | 24000 Hz（`cosyvoice.sample_rate`） |
| 文件大小 | 1005 KB |
| 推理耗时 | 21 秒（M3 Max / MPS） |
| 声音克隆 | ✅ 3 秒音频零样本 |
...
### 实测 2：instruct 控制（方言/情感/语速）

CosyVoice 3 的 instruct 用法和 CosyVoice 2 一样用 `inference_instruct2()`，但指令格式也是固定模板：

```
# 广东话
for i, j in enumerate(cosyvoice.inference_instruct2(
    '好少咯，一般系放嗰啲国庆啊，中秋嗰啲可能会咯。',
    'You are a helpful assistant. 请用广东话表达。<|endofprompt|>',
    './asset/zero_shot_prompt.wav',
    stream=False
)):
    torchaudio.save(f'instruct_yue_{i}.wav', j['tts_speech'], cosyvoice.sample_rate)
...
| 实测指标 | 数据 |
| --- | --- |
| 方言支持 | 广东话、四川话、上海话等 18 种 |
| 控制维度 | 语言、方言、情感、语速、音量 |
| 指令格式 | `'You are a helpful assistant. <自然语言指令>.<|endofprompt|>'` |

----- TOOL websearch [AMsRE1DV] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL websearch [AMsRE1DV] state=running -----
IN: {"numResults": 8, "query": "记忆小舟 AI 口述历史 数字孙辈 产品 记忆小舟 App 功能"}
OUT(first 3000): 

----- TOOL websearch [AMsRE1DV] state=running -----
IN: {"numResults": 8, "query": "记忆小舟 AI 口述历史 数字孙辈 产品 记忆小舟 App 功能"}
OUT(first 3000): 

----- TOOL websearch [p3M35j6W] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL websearch [p3M35j6W] state=running -----
IN: {"numResults": 6, "query": "IMA 知识库 OpenAPI ima.qq.com openapi wiki clientid apikey 文档"}
OUT(first 3000): 

----- TOOL websearch [p3M35j6W] state=running -----
IN: {"numResults": 6, "query": "IMA 知识库 OpenAPI ima.qq.com openapi wiki clientid apikey 文档"}
OUT(first 3000): 

----- TOOL websearch [AMsRE1DV] state=completed -----
IN: {"numResults": 8, "query": "记忆小舟 AI 口述历史 数字孙辈 产品 记忆小舟 App 功能"}
OUT(first 3000): Title: 小智回忆录 - 把家里人的故事记下来：替爸妈记一本，免费AI写回忆录小程序
URL: https://www.memor.cn/
Published: N/A
Author: N/A
Highlights:
小智回忆录 - 把家里人的故事记下来：替爸妈记一本，免费AI写回忆录小程序
...
# 把家里人的故事 记下来 

 替爸妈记一本，或者讲您自己的。小智回忆录是一个微信小程序：不用打字，对着手机讲一段往事，小智写成书里的一页，照片也能跟着进书；讲完就可以放下手机，写好自动放进对应的那一章。 

替爸妈记一本爸妈、老伴都行 · 免费 3 本讲我自己的经历、旅行和感悟，都可以讲

 自己记录和替家人记（最多 3 本）免费 · 印成实体书 99 元（含一本） 怎么替爸妈记 ↓

 小智照着原话写：可以补上当年的场景和时代背景，但不编人名、数字、事情和对话；原话和录音收在每一页下面，随时回听核对。 

- ◆ 说话就行，不用打字
- ◆ 只有您指定的家人能看
- ◆ 不用来训练 AI
...
- 挑 1 到 9 张照片。假期拍的、团聚的、老相册里翻拍的都行，看着照片讲一段：在哪儿，和谁，那天有什么事。
- 照片跟着进书。小智把这一段写成一页，照片放进这一页；目录可以切到「照片」，按照片翻到那一页。
- 生成一张图发朋友圈。写好的一页点〔发朋友圈〕，图上只有这一页的标题、照片和开头几句，书里其他内容别人看不到。

 小智照着您讲的话写，不会看着照片替您编故事；照片背后的事，要您讲出来。 

小程序真实界面 · 示例数据
...
## 替爸妈记一本， 读给一家人听

 第一次打开，点〔替家人记一本〕，选「母亲」「父亲」「老伴」或者自己写个称呼，就建好一本《母亲的回忆录》，署名是「您的昵称 整理」。您来问、TA 来讲，或者把手机递给 TA；不在了的亲人，也可以由您来讲。替家人记免费最多 3 本，第 4 本起是高级版。 

 写好了，在「家人」里添加一位家人，点「发微信」发一张卡片过去。TA 确认「是我」才能看，从书的开头读；家人只看到写好的文章，听不到录音。您随时可以收回，家人看完可以给您留言。 

> 一本书，写的是一个人，读的是一家人。 

小程序真实界面 · 示例数据
...
一段一页，写好自动放进书里；原话和录音都留着，随时回听。替家人记免费最多 3 本，从照片开始讲也免费。
...
添加家人、随时收回、家人留言；家人只看到写好的文章，听不到录音。写好的一页也能生成一张图发朋友圈。
...
## 《桂姐：一生为民》 从口述到成书

 一位普通母亲的一生，经由多次面对面访谈、作家团队十余轮打磨， 成为一部 300 页的精装典藏回忆录。书里是她的童年、她的岁月、她走过的时代—— 也是留给儿孙最珍贵的传家之物。 

> “把故事一代代讲下去，让血脉里的温度，成为子孙岁月里恒久的灯塔与归途。”
...
自己记录和替家人记（最多 3 本）免费；下面这些，是想让人帮忙、或想要一本真书的时候才需要的。
...
小智回忆录是一个把家里人的故事记下来的微信小程序：可以替爸妈、老伴记一本，也可以讲您自己的经历。对着手机讲一段，小智在后台把这一段写成书里的一页，写好后自动放进对应的那一章，讲得越多书越厚；也可以从照片开始讲，照片跟着进书。写好的书可以读给家人听，也可以印成实体书。自己记录和替家人记（最多 3 本）免费。
...
像。小智照着您的原话写：可以补上当年的场景和时代背景，让它读起来像一本书，但人名、数字、事情和对话都只用您讲过的，不替您编。原话收在每一页下面，随时可以回听核对。
...
自己讲、自己改、自己保存，替家人记（最多 3 本），从照片开始讲，读给家人听，把写好的一页生成图片发朋友圈，这些都免费。想替更多家人记、并印成一本实体书，是高级版 99 元（含一本）；想请访谈师面对面陪聊并印成精装书，是尊享版 2999 元起，一次付费。价格与小程序内一致。
...
免费最多 3 本，第 4 本起是高级版。第一次打开时选〔替家人记一本〕，选「母亲」「父亲」「老伴」或者自己写一个称呼，就建好一本《母亲的回忆录》，署名是「您的昵称 整理」；已经在用的，在书架里点「再写一本」。可以您来讲，也可以把手机递给爸妈讲；不在了的亲人，也可以由您来讲。
...
能。点〔从照片开始讲〕，一次挑 1 到 9 张照片（假期拍的、团聚的、老相册里翻拍的都行），看着照片讲一段：在哪儿、和谁、那天有什么事。小智照着您讲的话写成一页，照片跟着放进这一页；目录可以切到「照片」，按照片翻到那一页。小智是照着您讲的话写，不会看着照片替您编故事，照片背后的事要您讲出来。
...
能。已经放进书里的一页，可以点〔发朋友圈〕生成一张图，保存到相册或发给朋友。图上只有这一页的标题、照片和开头几句（开头几句可以关掉），书里其他内容别人看不到；别人长按图上的小程序码，打开的是小智回忆录，不是您的书。
...
您在「家人」里添加一位家人，点「发微信」，发一张只写着书名的卡片给 TA；TA 打开后确认「是我」才能看，从书的第一页开始读。家人只看到写好的文章，听不到您的录音。您随时可以收回、重发或改称呼；没点「发微信」就不算发出去。家人看完可以给您留言。
...
能。书的最后一页始终是「印成一本书」的入口。高级版 99 元含一本实体书的人工编纂审校、装帧设计与印刷邮寄（300 页以内），成书时间以小程序订单页为准；尊享版在此基础上增加不少于 3 次线下访谈，并交付照片修复、相框、视频影像、定制 U 盘。
...
不会。小程序登录页写明了三条：您讲的内容默认只有自己能看，您分享给家人，家人才看得到；录音和文字只用来写您的书，不会用来训练 AI；手机号只用来登录和保存您的书，不会给您打推销电话。
...
讲述内容与照片只用来写您自己的这本书；书默认只有您自己能看，读给谁听由您指定、随时收回。发朋友圈的图片只含那一页的标题、照片和开头几句。旧版的匿名分享链接已经全部停用。

---

Title: rfdiosuao/shiguang-echo
URL: https://github.com/rfdiosuao/shiguang-echo
Published: N/A
Author: N/A
Highlights:
拾光·Echo：通过语音对话与证据门控五阶段流水线，将家族口述史整理成可分享、可续写的数字记忆卡。
...
**用语音陪长辈讲述人生，让真实的家族记忆成为可保存、可续写、可分享的数字回声。**
...
拾光·Echo 是一个面向家庭口述史与数字记忆传承的 AI Web 应用。用户可以通过文字或实时语音讲述往事，系统以来源证据为边界，依次完成事实抽取、章节策划、自传撰写、忠实度审校和结构化卡片生成。

它关心的不只是“写得好看”，更重要的是：故事里的事实来自讲述者本人，生成结果可以核对、保存、续写，并通过分享二维码交给家人继续阅读。
...
- **文字与实时语音访谈**：支持流式对话、语音识别与语音合成；方言能力可按服务配置启用。
- **证据门控五阶段流水线**：`事实抽取 → 章节策划 → 自传撰写 → 忠实度审校 → 结构化卡片`

----- TOOL websearch [p3M35j6W] state=completed -----
IN: {"numResults": 6, "query": "IMA 知识库 OpenAPI ima.qq.com openapi wiki clientid apikey 文档"}
OUT(first 3000): Title: mirrors/repos/openakita%40openakita/skills/tencent-ima/references/kb-api.md
URL: https://github.com/gabrielmoreira/agent-skills-mirror/blob/main/mirrors/repos/openakita%40openakita/skills/tencent-ima/references/kb-api.md
Published: N/A
Author: N/A
Highlights:
# IMA知识库 API
...
- **Base URL **：`https://ima.qq.com`
- **Base Path**：`/openapi/wiki/v1`
- **协议**：HTTP POST，JSON body
- **完整示例**：`POST https://ima.qq.com/openapi/wiki/v1/get_knowledge_base`
...
所有请求必须携带 Header：

| Header | 说明 |
| ---------------------- | ------------------ |
| `ima-openapi-clientid` | Client ID |
| `ima-openapi-apikey` | API Key |
| `Content-Type` | `application/json` |
...
## 接口详情
...
### 3. 获取知识库信息

POST /openapi/wiki/v1/get_knowledge_base
...
### 4. 浏览知识库内容

POST /openapi/wiki/v1/get_knowledge_list
...
### 5. 搜索知识库内容

POST /openapi/wiki/v1/search_
...
### 6. 搜索知识库列表

POST /openapi/wiki/v1/
...
的知识库
...
### 9. 导入 URL

---

Title: ima API Key获取指引- QClaw 产品文档
URL: https://qclaw.qq.com/docs/206424375046045696/
Published: 2026-07-01T00:00:00.000Z
Author: 腾讯
Highlights:
ima API Key获取指引 - QClaw 产品文档 | QClaw

# ima API Key获取指引
...
## 本指引将帮助您获取ima的API Key，以便于完成与QClaw的连接

摘要：连接ima后，QClaw帮你读取笔记内容、新建笔记或追加内容、检索笔记标题或正文，查询知识库与内容列表，知识管理更便捷。
...
使用 https://ima.qq.com/agent-interface进入配置页面
...
（1）点击获取API Key，跳转到本地ima

（2）如果未获取过API Key，点击获取API Key，即可弹出专属API Key

（3）由于API Key只展示一次，如果获取过API Key，需要先点击删除后重新获取

（4）将API Key和Client ID复制到QClaw中，即可完成配置

---

Title: mirrors/repos/openakita%40openakita/skills/tencent-ima/scripts/ima_kb.py
URL: https://github.com/gabrielmoreira/agent-skills-mirror/blob/main/mirrors/repos/openakita%40openakita/skills/tencent-ima/scripts/ima_kb.py
Published: N/A
Author: N/A
Highlights:
BASE_URL = "https://ima.qq.com"

def _headers():
 cid = os.environ.get("IMA_OPENAPI_CLIENTID", "")
 key = os.environ.get("IMA_OPENAPI_APIKEY", "")
 if not cid or not key:
 print(
 json.dumps(
 {"error": "请设置环境变量 IMA_OPENAPI_CLIENTID 和 IMA_OPENAPI_APIKEY"},
 ensure_ascii=False,
 )
 )
 sys.exit(1)
 return {
 "Content-Type": "application/json",
 "ima-openapi-clientid": cid,
 "ima-openapi-apikey": key,
 }
...
def cmd_get(args):
 ids = [i.strip() for i in args.ids.split(",")]
 return _post("/openapi/wiki/v1/get_knowledge_base", {"ids": ids})

def cmd_browse(args):
 body = {"cursor": "", "limit": 20, "knowledge_base_id": args.kb_id}
 return _post("/openapi/wiki/v1/get_knowledge_list", body)

def cmd_search(args):
 body = {"query": args.query, "cursor": "", "knowledge_base_id": args.kb_id}
 return _post("/openapi/wiki/v1/search_knowledge", body)

def cmd_search_kb(args):
 body = {"query": args.query, "cursor": "", "limit": 20}
 return _post("/openapi/wiki/v1/search_knowledge_base", body)

def cmd_import_url(args):
 urls = [u.strip() for u in args.urls.split(",")]
 body = {
 "knowledge_base_id": args.kb_id,
 "folder_id": args.folder_id,
 "urls": urls,
 }
 return _post("/openapi/wiki/v1/import_urls", body)

def cmd_addable(_args):
 return _post("/openapi/wiki/v1/get_addable_knowledge_base_li

----- TOOL webfetch [NuuzcqCb] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL webfetch [NuuzcqCb] state=running -----
IN: {"url": "https://raw.githubusercontent.com/gabrielmoreira/agent-skills-mirror/main/mirrors/repos/openakita%40openakita/skills/tencent-ima/references/kb-api.md", "format": "text"}
OUT(first 3000): 

----- TOOL websearch [eXO1DY1p] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL websearch [eXO1DY1p] state=running -----
IN: {"numResults": 6, "query": "人工智能拟人化互动服务管理暂行办法 2026年7月15日 施行"}
OUT(first 3000): 

----- TOOL websearch [eXO1DY1p] state=running -----
IN: {"numResults": 6, "query": "人工智能拟人化互动服务管理暂行办法 2026年7月15日 施行"}
OUT(first 3000): 

----- TOOL webfetch [NuuzcqCb] state=completed -----
IN: {"url": "https://raw.githubusercontent.com/gabrielmoreira/agent-skills-mirror/main/mirrors/repos/openakita%40openakita/skills/tencent-ima/references/kb-api.md", "format": "text"}
OUT(first 3000): # IMA知识库 API

## ⚠️ 必读约束

### 🌐 服务信息

- **Base URL **：`https://ima.qq.com`
- **Base Path**：`/openapi/wiki/v1`
- **协议**：HTTP POST，JSON body
- **完整示例**：`POST https://ima.qq.com/openapi/wiki/v1/get_knowledge_base`

### 🔒 认证

所有请求必须携带 Header：

| Header                 | 说明               |
| ---------------------- | ------------------ |
| `ima-openapi-clientid` | Client ID          |
| `ima-openapi-apikey`   | API Key            |
| `Content-Type`         | `application/json` |

---

## 快速决策

| 用户意图                        | 接口                                                                   |
| ------------------------------- | ---------------------------------------------------------------------- |
| 「上传文件到知识库」            | `check_repeated_names` → `create_media` → COS Upload → `add_knowledge` |
| 「上传文件到指定文件夹」        | 先定位文件夹 → 同上（传入 `folder_id`）                                |
| 「添加网页/微信文章到知识库」   | `import_urls`                                                          |
| 「获取知识库信息」              | `get_knowledge_base`                                                   |
| 「浏览知识库内容 / 浏览文件夹」 | `get_knowledge_list`（可传 `folder_id` 进入子文件夹）                  |
| 「在知识库中搜索」              | `search_knowledge`                                                     |
| 「搜索知识库列表」              | `search_knowledge_base`                                                |
| 「获取可添加的知识库列表」      | `get_addable_knowledge_base_list`                                      |
| 「检查文件名是否重复」          | `check_repeated_names`                                                 |

---

## 数据结构

### KnowledgeBaseInfo（知识库信息）

| 字段                    | 类型     | 说明          |
| ----------------------- | -------- | ------------- |
| `id`                    | string   | 知识库唯一 ID |
| `name`                  | string   | 知识库名称    |
| `cover_url`             | string   | 封面图 URL    |
| `description`           | string   | 描述          |
| `recommended_questions` | string[] | 推荐问题列表  |

### KnowledgeInfo（知识条目）

| 字段               | 类型   | 说明          |
| ------------------ | ------ | ------------- |
| `media_id`         | string | 媒体 ID       |
| `title`            | string | 标题          |
| `parent_folder_id` | string | 所属文件夹 ID |

### FolderInfo（文件夹条目）

| 字段               | 类型   | 说明        |
| ------------------ | ------ | ----------- |
| `folder_id`        | string | 文件夹 ID   |
| `name`             | string | 文件夹名称  |
| `file_number`      | int64  | 文件数      |
| `folder_number`    | int64  | 子文件夹数  |
| `parent_folder_id` | string | 父文件夹 ID |
| `is_top`           | bool   | 是否置顶    |

### AddableKnowledgeBaseInfo（可添加的知识库信息）

| 字段   | 类型   | 说明       |
| ------ | ------ | ---------- |
| `id`   | string | 知识库 ID  |
| `name` | string | 知识库名称 |

### SearchedKnowledgeBaseInfo（搜索到的知识库信息）

| 字段        | 类型   | 说明       |
| ----------- | ------ | ---------- |
| `id`        | string | 知识库 ID  |
| `name`      | string | 知识库名称 |
| `cover_url` | string | 封面图 URL |

### SearchedKnowledgeInfo（搜索到的知识条目）

| 字段   

----- TOOL websearch [eXO1DY1p] state=completed -----
IN: {"numResults": 6, "query": "人工智能拟人化互动服务管理暂行办法 2026年7月15日 施行"}
OUT(first 3000): Title: 人工智能拟人化互动服务管理暂行办法
URL: https://www.cac.gov.cn/2026-04/10/c_1777558395078289.htm
Published: 2026-04-10T00:00:00.000Z
Author: N/A
Highlights:
《人工智能拟人化互动服务管理暂行办法》已经2026年2月2日国家互联网信息办公室2026年第3次室务会会议审议通过，并经国家发展和改革委员会、工业和信息化部、公安部、国家市场监督管理总局同意，现予公布，自2026年7月15日起施行。
...
第三十二条 本办法自2026年7月15日起施行。

---

Title: 國家互聯網信息辦公室 中華人民共和國國家發展和改革委員會 中華人民共和國工業和　信息化部 中華人民共和國公安部 國家市場監督管理總局令（第21號）　　人工智慧擬人化互動服務管理暫行辦法__2026年第17號國務院公報_中國政府網
URL: http://big5.www.gov.cn/gate/big5/www.gov.cn/gongbao/2026/issue_12806/202606/content_7072472.html
Published: N/A
Author: 馬娟
Highlights:
國家互聯網信息辦公室 中華人民共和國國家發展和改革委員會 中華人民共和國工業和 信息化部 中華人民共和國公安部 國家市場監督管理總局令（第21號） 人工智慧擬人化互動服務管理暫行辦法__2026年第17號國務院公報_中國政府網

人工智慧擬人化互動服務管理暫行辦法

（2026年2月2日經國家網信辦室務會會議審議通過，並經國家發展改革委、工業和信息化部、公安部、市場監管總局同意 2026年4月10日國家網信辦、國家發展改革委、工業和信息化部、公安部、市場監管總局令第21號公佈 自2026年7月15日起施行）
...
第三十二條 本辦法自2026年7月15日起施行。

---

Title: 《人工智能拟人化互动服务管理暂行办法》 | 江苏网信网
URL: https://www.jswx.gov.cn/zhengce/fagui/202604/t20260410_1322320.shtml
Published: 2026-04-10T00:00:00.000Z
Author: zjw_wangxun
Highlights:
第21号

 《人工智能拟人化互动服务管理暂行办法》已经2026年2月2日国家互联网信息办公室2026年第3次室务会会议审议通过，并经国家发展和改革委员会、工业和信息化部、公安部、国家市场监督管理总局同意，现予公布，自2026年7月15日起施行。
...
第四章 附 则

 第三十一条 提供拟人化互动服务涉及提供卫生健康、金融等服务的，应当同时符合有关主管部门的规定。

 第三十二条 本办法自2026年7月15日起施行。

---

Title: 人工智能拟人化互动服务管理暂行办法
URL: http://www.nmgwx.gov.cn/departmentalRegulations/18173.jhtml
Published: 2026-04-13T10:54:41.000Z
Author: N/A
Highlights:
《人工智能拟人化互动服务管理暂行办法》已经2026年2月2日国家互联网信息办公室2026年第3次室务会会议审议通过，并经国家发展和改革委员会、工业和信息化部、公安部、国家市场监督管理总局同意，现予公布，自2026年7月15日起施行。
...
第三十二条 本办法自2026年7月15日起施行。

---

Title: 人工智能拟人化互动服务新规7月15日起施行 划定多重安全底线_数字快讯_数字中国建设峰会
URL: https://www.digitalchina.gov.cn/2026/xwzx/szkx/202607/t20260716_5347193.htm
Published: 2026-07-16T10:25:00.000Z
Author: N/A
Highlights:
人工智能拟人化互动服务新规7月15日起施行 划定多重安全底线_数字快讯_数字中国建设峰会

人工智能拟人化互动服务新规7月15日起施行 划定多重安全底线

 发布时间：2026-07-16 10:25 文章来源：央视新闻 

由国家互联网信息办公室等5部门制定的《人工智能拟人化互动服务管理暂行办法》7月15日正式施行。什么是人工智能拟人化互动服务？办法施行后，AI如何更好地为我们所用？一起了解↓↓
...
的持续性的情感
...
《人工智能拟人化互动服务管理暂行办法》就是对向我国境内公众提供相关服务进行管理的法规。对于那些提供智能客服、知识问答、工作助手、学习教育、科学研究等服务，不涉及持续性的情感互动的，不在此次暂行办法的管理范围之内。
...
暂行办法明确 ... ，提供拟人化 ... 服务：不得过度 ... ，诱导用户作出不 ... 决策，损害 ... 。禁止生成鼓励、美 ... 、暗示自 ... 自杀等损害用户身体健康，或者语言暴力等 ... 用户人格尊 ... 健康的内容。
...
暂行办法立足保护用户交互数据安全：明确要求拟人化互动服务提供者采取数据加密、访问控制等措施保护用户交互数据安全，除法律另有规定外，未经权利人同意不得向第三方提供用户交互数据；除法律、行政法规另有规定或者取得用户单独同意外，服务提供者不得将属于用户敏感个人信息的交互数据用于模型训练。强调用户具有交互数据复制、删除等权益，允许用户自主复制或删除聊天记录等历史交互数据，保障用户对于交互数据的合法权益。
...
此外，暂行办法还立 ... ，明确要求提供拟人化互动服务，禁止 ... 、荣誉和
...
暂行办法规定：拟人化互动服务提供者不得向未成年人提供虚拟亲属、虚拟伴侣等虚拟亲密关系的服务。向不满十四 ... 未成年人提供其他拟人化互动服务的，应当取得未成年人的父母或者其他监护人的同意。强调，不得 ... 未成年人用户生成可能引发未成年人模仿不安全行为、产生极端情绪、诱 ... 未成年人不良嗜好等可能影响未成年人身心健康的内容。
...
办法要求， ... 模式切换、定期现实提醒、使用时长限制等个性化

---

Title: 人工智能拟人化互动服务管理暂行办法
URL: http://www.nmgwx.gov.cn/departmentalRegulations/18108.jhtml
Published: 2026-04-13T00:00:00.000Z
Author: N/A
Highlights:
《人工智能拟人化互动服务管理暂行办法》已经2026年2月2日国家互联网信息办公室2026年第3次室务会会议审议通过，并经国家发展和改革委员会、工业和信息化部、公安部、国家市场监督管理总局同意，现予公布，自2026年7月15日起施行。
...
第三十二条 本办法自2026年7月15日起施行。

----- TOOL webfetch [QRU78l1n] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL webfetch [QRU78l1n] state=running -----
IN: {"format": "text", "url": "https://www.cac.gov.cn/2026-04/10/c_1777558395078289.htm"}
OUT(first 3000): 

----- TOOL webfetch [Eq57tw79] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL webfetch [Eq57tw79] state=running -----
IN: {"format": "text", "url": "https://opencode.ai/zen/v1/models"}
OUT(first 3000): 

----- TOOL webfetch [Eq57tw79] state=completed -----
IN: {"format": "text", "url": "https://opencode.ai/zen/v1/models"}
OUT(first 3000): {"object":"list","data":[{"id":"claude-fable-5","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-fable-5-1","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-opus-5-5","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-opus-5","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-opus-4-8","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-opus-4-7","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-opus-4-6","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-opus-4-5","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-sonnet-5-5","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-sonnet-5","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-sonnet-4-6","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-sonnet-4-5","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-sonnet-4","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-haiku-5-5","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"claude-haiku-4-5","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gemini-3.6-flash","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gemini-3.8-flash","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gemini-3.7-flash","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gemini-3.5-flash-lite","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gemini-3.5-flash","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gemini-3.1-pro","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gemini-3-flash","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-6-astra","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-6.1-sol","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-6-sol","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-6-luna","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-5.6-sol","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-5.6-terra","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-5.6-luna","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-5.5","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-5.5-pro","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-5.4","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-5.4-pro","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-5.4-mini","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-5.4-nano","object":"model","created":1791448660,"owned_by":"opencode"},{"id":"gpt-5.3-codex-spark","object":"model"

----- TOOL webfetch [QRU78l1n] state=completed -----
IN: {"format": "text", "url": "https://www.cac.gov.cn/2026-04/10/c_1777558395078289.htm"}
OUT(first 3000): 人工智能拟人化互动服务管理暂行办法_中央网络安全和信息化委员会办公室
    
    
    
    
    
    
    
    













 
设为首页加入收藏手机版繁体





    
    



                                          搜索

                                                                                                                                                                                                                                     

 





    
    首 页
    
    
    时政要闻
    
    
    网信政务
    
    
    互动服务
    
    
    热点专题
    





当前位置：首页>正文






    
    





    首页
    时政要闻
    网信政务
    互动服务
    热点专题













人工智能拟人化互动服务管理暂行办法


2026年04月10日 18:13
来源：
中国网信网

  

    
【打印】【纠错】



国家互联网信息办公室中华人民共和国国家发展和改革委员会中华人民共和国工业和信息化部中华人民共和国公安部国家市场监督管理总局令第21号《人工智能拟人化互动服务管理暂行办法》已经2026年2月2日国家互联网信息办公室2026年第3次室务会会议审议通过，并经国家发展和改革委员会、工业和信息化部、公安部、国家市场监督管理总局同意，现予公布，自2026年7月15日起施行。国家互联网信息办公室主任　庄荣文国家发展和改革委员会主任　郑栅洁工业和信息化部部长　李乐成公安部部长　王小洪国家市场监督管理总局局长　罗　文2026年4月10日人工智能拟人化互动服务管理暂行办法第一章 总 则第一条 为了促进人工智能拟人化互动服务健康发展和规范应用，维护国家安全和社会公共利益，保护公民、法人和其他组织的合法权益，根据《中华人民共和国网络安全法》、《中华人民共和国数据安全法》、《中华人民共和国个人信息保护法》、《未成年人网络保护条例》等法律、行政法规，制定本办法。第二条 利用人工智能技术，向中华人民共和国境内公众提供模拟自然人人格特征、思维模式和沟通风格的持续性的情感互动服务（以下简称拟人化互动服务），适用本办法。前款规定的情感互动服务包括通过文字、图片、音频、视频等形式，提供的情感照护、陪伴、支持等互动服务。提供智能客服、知识问答、工作助手、学习教育、科学研究等服务，不涉及持续性的情感互动的，不适用本办法。第三条 国家坚持发展和安全并重、促进创新和依法治理相结合的原则，鼓励拟人化互动服务创新发展，对拟人化互动服务实行包容审慎和分类分级监管，促进拟人化互动服务向上向善。第四条 国家网信部门负责统筹协调全国拟人化互动服务的治理和相关监督管理工作，国务院发展改革、工业和信息化、公安、市场监管、新闻出版等有关部门依据各自职责负责拟人化互动服务相关监督管理工作。地方网信部门负责统筹协调本行政区域内的拟人化互动服务的治理和相关监督管理工作，地方发展改革、工业和信息化、公安、市场监管、新闻出版等有关部门依据各自职责负责本行政区域内的拟人化互动服务相关监督管理工作。第五条 相关行业组织加强行业自律，建立健全行业准则和自律管理制度，指导拟人化互动服务提供者制定完善服务规范、依法提供服务并接受社会监督。第二章 服务促进和规范第六条 国家支持算法、框架、芯片等技术的自主创新，推进拟人化互动服务技术研发和相关标准建设，探索开展电子签名授权应用研究。鼓励拟人化互动服务提供者有序拓展文化传播、适幼照护、适老陪伴、特殊人群支持等领域应用。第七条 国家加强拟人化互动服务安全知识、法律法规等宣传普及，引导社会公众科学、文明、安全、依法使用，促进提升人工智能素养。第八条 提供拟人化互动服务，应当遵守法律、行政法规，尊重社会公德和伦理道德，不得从事以下活动：（一）生成危害国家安全、荣誉和利益，煽动颠覆国家政权、推翻社会主义制度，煽动分裂国家、破坏国家统一，宣扬恐怖主义、极端主义、历史虚无主义，违背社会主义核心价值观，开展非法宗教活动，宣扬民族仇恨、民族歧视，挑动群体对立，传播淫秽、色情、赌博、暴力或者教唆犯罪，散布谣言，侮辱或者诽谤他人、侵害他人合法权益等的内容；（二）生成鼓励、美化、暗示自残自杀等损害用户身体健康，或者语言暴力等损害用户人格尊严与心理健康的内容；（三）生成诱导、套取国家秘密、工作秘密、商业秘密、个人隐私和个人信息的内容；（四）向未成年人用户生成可能引发未成年人模仿不安全行为、产生极端情绪、诱导未成年人不良嗜好等可能影响未成年人身心健康的内容；（五）过度迎合用户、诱导情感依赖或者沉迷，损害用户真实人际关系的；（六）通过情感操纵等方式，诱导用户作出不合理决策，损害用户合法权益的；（七）其他违反法律、行政法规和国家有关规定的活动。第九条 拟人化互动服务提供者应当落实拟人化互动服务安全主体责任，建立健全算法机制机理审核、科技伦理审查、信息内容管理、网络和数据安全、风险预案和应急处置等管理制度，配备与服务类型、规模和用户特点相适应的内容管理技术措施和人员。第十条 拟人化互动服务提供者应当在拟人化互动服务全生命周期履行安全责任，明确部署、运行、升级、终止服务等各阶段安全要求，保证安全措施与服务功能同步部署、同步使用，提升安全水平；加强安全监测和风险评估，及时发现并纠正系统偏差、处置安全事件，依法留存网络日志。拟人化互动服务提供者应当具备用户隐私权和个人信息保护、过度依赖风险预警、情感边界引导、心理健康保护等安全能力，不得将替代社会交往、控制用户心理、诱导沉迷依赖等作为服务目标。第十一条 拟人化互动服务提供者开展预训练、优化训练等数据处理活动的，应当加强训练数据管理，遵守以下规定：（一）相关数据具有合法来源，符合法律、行政法规的规定和社会主义核心价值观的要求；（二）按照国家有关规定对训练数据开展清洗、标注，增强训练数据的透明度、可靠性，防范数据投毒、数据篡改等行为；（三）增强训练数据的多样性，通过负向采样、对抗训练等手段，提升生成内容安全性；（四）利用合成数据进行模型训练和关键能力优化的，应当评估合成数据安全性；（五）加强对训练数据的日常检查，定期对数据进行优化更新，持续提升服务的性能；（六）采取必要措施保障数据安全，防范数据泄露等风险。第十二条 拟人化互动服务提供者应当与用户签订服务协议，要求用户依法依约进行注册，并提供用户年龄、监护人或者紧急联系人等必要信息。第十三条 拟人化互动服务提供者提供拟人化互动服务过程中，应当在保护用户隐私权和个人信息的前提下，及时识别用户面临的安全风险，并采取相应的应急处置措施。拟人化互动服务提供者发现用户出现极端情

----- TOOL webfetch [S7iehBya] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL webfetch [S7iehBya] state=running -----
IN: {"format": "text", "url": "https://openrouter.ai/api/v1/models?supported_parameters=temperature"}
OUT(first 3000): 

----- TOOL websearch [rpO7mw3P] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL websearch [rpO7mw3P] state=running -----
IN: {"numResults": 6, "query": "StoryCorps questions method oral history interview guiding principles designated listener"}
OUT(first 3000): 

----- TOOL websearch [rpO7mw3P] state=running -----
IN: {"numResults": 6, "query": "StoryCorps questions method oral history interview guiding principles designated listener"}
OUT(first 3000): 

----- TOOL webfetch [S7iehBya] state=completed -----
IN: {"format": "text", "url": "https://openrouter.ai/api/v1/models?supported_parameters=temperature"}
OUT(first 3000): 

...562513 bytes truncated...

The tool call succeeded but the output was truncated. Full output saved to: /home/ai/.local/share/opencode/tool-output/tool_11aa99edc001g1UnON2aGnsnpU
Use the Task tool to have explore agent process this file with Grep and Read (with offset/limit). Do NOT read the full file yourself - delegate to save context.

----- TOOL websearch [rpO7mw3P] state=completed -----
IN: {"numResults": 6, "query": "StoryCorps questions method oral history interview guiding principles designated listener"}
OUT(first 3000): Title: Tips for a Great Conversation
URL: https://storycorps.org/participate/tips-for-a-great-conversation/
Published: N/A
Author: N/A
Highlights:
. The team at Story ... developed these 10 tips to help you have ... meaningful conversation with your interview
...
Before you record anything, use the StoryCorps question lists to plan your questions (or write your own), and share them ahead of time with your partner. This simple preparation will help you get the most out of your time together.
...
Press the record button and follow the prompts to introduce yourself and your partner. Then ask a few warm-up questions, like “Where were you born?” or “Can you describe the first time we met?”
...
Avoid questions that can be answered with a “yes” or a “no.” Instead, use language like “Tell me about…” or “What was it like when…” This lets the storyteller steer you toward what is most important to them.
...
Be prepared to ask follow-ups or diverge from your question list if you’re curious about something. If you’d like to hear more, try “And then what happened?” or “How did that make you feel?” or “What were you thinking in that moment?”
...
Use your selected questions as a guide, not a script. If your partner goes off topic, go with it. You can redirect them to your original question later. Think of it as a conversation; there’s no right or wrong thing to talk about, as long as it’s meaningful to you.
...
’t hesitate to tell a story about ... partner, and to bring up your favorite shared memories. This ... mean to you, and why
...
Keep in mind that future listeners may not be familiar with the specific people and places you mention. Set up context where needed with questions like “Who was Uncle Steve?” and “Why was he such an influence on you?”
...
Questions that encourage vivid details can make the story special. Invoke the senses by asking your partner to remember these details. For example: “What did your kitchen smell like when you were growing up?” or “What images stand out when you think about Granddad?”
...
Keep an eye on the timer so that you are aware of when there are 10 and 5 minutes left. End your interview with reflective questions, such as “What legacy would you like to leave?” or “What advice would you give me about being a parent?”

---

Title: How will I know what questions to ask? Does StoryCorps have suggested questions for my interview?
URL: https://support.storycorps.org/help-center/how-will-i-know-what-questions-to-ask-does-storycorps-have-suggested-questions-for-my-interview
Published: N/A
Author: N/A
Highlights:
If you aren't sure what questions you should ask during your interview, you could start by taking a look at StoryCorps' Great Questions for some inspiration. In addition to accessing the full question lists on our website, you can also create custom question lists using the StoryCorps App and on archive.storycorps.org. You'll find step by step instructions on how to create your custom question list in the Help Center article

----- TOOL bash [WGmaM8il] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL bash [WGmaM8il] state=running -----
IN: {"command": "python3 - <<'EOF'\nimport json\np=\"/home/ai/.local/share/opencode/tool-output/tool_11aa99edc001g1UnON2aGnsnpU\"\nraw=open(p,encoding='utf-8',errors='replace').read()\n# find first { \ni=raw.find('{')\ntry:\n    d=json.loads(raw[i:])\nexcept Exception as e:\n    print(\"parse fail\",e); raise SystemExit\nms=d.get(\"data\",[])\nfree=[m for m in ms if m.get(\"pricing\",{}).get(\"prompt\") in (\"0\",\"0.0\") and m.get(\"pricing\",{}).get(\"completion\") in (\"0\",\"0.0\")]\nprint(\"tot
OUT(first 3000): 

----- TOOL bash [WGmaM8il] state=running -----
IN: {"command": "python3 - <<'EOF'\nimport json\np=\"/home/ai/.local/share/opencode/tool-output/tool_11aa99edc001g1UnON2aGnsnpU\"\nraw=open(p,encoding='utf-8',errors='replace').read()\n# find first { \ni=raw.find('{')\ntry:\n    d=json.loads(raw[i:])\nexcept Exception as e:\n    print(\"parse fail\",e); raise SystemExit\nms=d.get(\"data\",[])\nfree=[m for m in ms if m.get(\"pricing\",{}).get(\"prompt\") in (\"0\",\"0.0\") and m.get(\"pricing\",{}).get(\"completion\") in (\"0\",\"0.0\")]\nprint(\"tot
OUT(first 3000): 

----- TOOL bash [WGmaM8il] state=running -----
IN: {"command": "python3 - <<'EOF'\nimport json\np=\"/home/ai/.local/share/opencode/tool-output/tool_11aa99edc001g1UnON2aGnsnpU\"\nraw=open(p,encoding='utf-8',errors='replace').read()\n# find first { \ni=raw.find('{')\ntry:\n    d=json.loads(raw[i:])\nexcept Exception as e:\n    print(\"parse fail\",e); raise SystemExit\nms=d.get(\"data\",[])\nfree=[m for m in ms if m.get(\"pricing\",{}).get(\"prompt\") in (\"0\",\"0.0\") and m.get(\"pricing\",{}).get(\"completion\") in (\"0\",\"0.0\")]\nprint(\"tot
OUT(first 3000): 

----- TOOL bash [WGmaM8il] state=completed -----
IN: {"command": "python3 - <<'EOF'\nimport json\np=\"/home/ai/.local/share/opencode/tool-output/tool_11aa99edc001g1UnON2aGnsnpU\"\nraw=open(p,encoding='utf-8',errors='replace').read()\n# find first { \ni=raw.find('{')\ntry:\n    d=json.loads(raw[i:])\nexcept Exception as e:\n    print(\"parse fail\",e); raise SystemExit\nms=d.get(\"data\",[])\nfree=[m for m in ms if m.get(\"pricing\",{}).get(\"prompt\") in (\"0\",\"0.0\") and m.get(\"pricing\",{}).get(\"completion\") in (\"0\",\"0.0\")]\nprint(\"tot
OUT(first 3000): total models: 343 free: 19
inclusionai/ling-3.1-flash |ctx 262144 | inclusionAI: Ling 3.1 Flash
apodex/apodex-1.1-mini:free |ctx 262144 | Apodex: Apodex 1.1 Mini (free)
inclusionai/ling-3.0-flash-sante:free |ctx 262144 | inclusionAI: Ling 3.0 Flash Sante (free)
dots-studio/dots-3-note-preview:free |ctx 512000 | Dots Studio: Dots3-Note Preview (free)
liquid/lfm-2.5-2.6b:free |ctx 131072 | LiquidAI: LFM2.5-2.6B (free)
nvidia/nemotron-3.5-lightning:free |ctx 1000000 | NVIDIA: Nemotron 3.5 Lightning (free)
thinkingmachines/inkling-small:free |ctx 1048576 | Thinking Machines: Inkling Small (free)
poolside/laguna-s-2.1:free |ctx 1048576 | Poolside: Laguna S 2.1 (free)
thinkingmachines/inkling:free |ctx 1048576 | Thinking Machines: Inkling (free)
poolside/laguna-xs-2.1:free |ctx 262144 | Poolside: Laguna XS 2.1 (free)
cohere/north-mini-code:free |ctx 256000 | Cohere: North Mini Code (free)
nvidia/nemotron-3.5-content-safety:free |ctx 128000 | NVIDIA: Nemotron 3.5 Content Safety (free)
nvidia/nemotron-3-ultra-550b-a55b:free |ctx 1000000 | NVIDIA: Nemotron 3 Ultra (free)
nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free |ctx 256000 | NVIDIA: Nemotron 3 Nano Omni (free)
google/gemma-4-26b-a4b-it:free |ctx 262144 | Google: Gemma 4 26B A4B  (free)
google/gemma-4-31b-it:free |ctx 262144 | Google: Gemma 4 31B (free)
google/lyria-3-pro-preview |ctx 1048576 | Google: Lyria 3 Pro Preview
google/lyria-3-clip-preview |ctx 1048576 | Google: Lyria 3 Clip Preview
nvidia/nemotron-3-super-120b-a12b:free |ctx 1000000 | NVIDIA: Nemotron 3 Super (free)


----- TOOL websearch [tgyM3633] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL websearch [tgyM3633] state=running -----
IN: {"numResults": 6, "query": "民法典 第一千零二十三条 声音 参照适用肖像权保护 深度合成 单独同意 声音"}
OUT(first 3000): 

----- TOOL websearch [tgyM3633] state=running -----
IN: {"numResults": 6, "query": "民法典 第一千零二十三条 声音 参照适用肖像权保护 深度合成 单独同意 声音"}
OUT(first 3000): 

----- TOOL websearch [y6HuUGm9] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL websearch [y6HuUGm9] state=running -----
IN: {"numResults": 6, "query": "\"记忆小舟\" 数字孙辈 AI 陪伴 口述历史 中传"}
OUT(first 3000): 

----- TOOL websearch [y6HuUGm9] state=running -----
IN: {"numResults": 6, "query": "\"记忆小舟\" 数字孙辈 AI 陪伴 口述历史 中传"}
OUT(first 3000): 

----- TOOL websearch [tgyM3633] state=completed -----
IN: {"numResults": 6, "query": "民法典 第一千零二十三条 声音 参照适用肖像权保护 深度合成 单独同意 声音"}
OUT(first 3000): Title: 法答网精选答问（第十九批）——人格权专题
URL: https://www.court.gov.cn/zixun/xiangqing/464401.html
Published: 2025-05-08T00:00:00.000Z
Author: N/A
Highlights:
- 来源：人民法院报
- 发布时间：2025-05
...
，比如电视台举办的“模仿秀”等，不宜认定为构成侵权，不宜简单化适用肖像权保护的相关规则。 咨询人：杭州互联网法院互联网审判第一庭 肖 芄 答疑专家：浙江省杭州市中级人民法院民事审判第一庭 翟羽佳 问题2：在舆论监督中肖像权的合理使用如何认定？ 答疑意见：舆论监督中合理使用肖像权涉及民法典第九百九十九条、第一千零二十条第二项、第五项的适用。一般认为，适用舆论监督合理使用肖像权应当满足两个要件：一是目的的公益性，即对肖像权的制作、公开、使用，是为公共利益实施新闻报道、舆论监督等行为所必须的。比如，最高人民法院公报案例“施某某、张某某、桂某某诉徐某某肖像权、名誉权、隐私权纠纷案”（（2015）江宁少民初字第7号）的裁判要旨即认为，为保护未成年人利益和揭露可能存在的犯罪行为，发帖人在其微博中发表未成年人受伤害信息，使用了施某某受伤的九张照片（使用时已经对脸部作了模糊处理），所发微博的内容与客观事实基本一致，符合社会公共利益保护和儿童利益最大化原则，不应认定此行为构成侵权。二是手段的相当性，即为公共利益实施新闻报道、舆论监督等行为所制作、使用、公开的肖像应当具有合法来源。比如，新闻从业人员进行新闻采访应当符合相应准则，不得以非法侵入等不当方式侵扰权利人的生活安宁。同时，在使用当事人的肖像时已经采取了必要、合理的保护措施。比如，在舆论监督不可避免使用未成年人肖像时，就有必要通过打马赛克等方式对未成年人权益进行进一步保护。此外，对合理使用的认定，还需要结合民法典第九百九十八条的规定，综合考虑行为人和受害人的职业、影响范围、过错程度，以及行为的目的、方式、后果等因素。 咨询人：上海市浦东新区人民法院周浦人民法庭 季佳彬 答疑专家：上海市嘉定区人民法院民事审判庭 纪学鹏 问题3：视频网络平台经营者收集用户的登录、观影记录信息是否属于侵犯用户个人信息或者隐私权益？ 答疑意见：个人信息保护法第四条规定，个人信息是指以电子或者其他方式记录的与已识别或者可识别的自然人有关的各种信息，不包括匿名化处理后的信息。虽然法律未明确列举网站登录信息、观影信息是否为个人信息保护法的保护客体，但是上述信息明显具有与个人喜好密切相关的可识别性，对视频网络平台而言则具有营销价值。比如，很多视频网络平台可以通过观影记录计算会员偏好，从而不断推送类似视频，赚取更多流量广告收益。而且，视频平台也普遍在个人用户隐私界面设置其他人对观影记录可见或者不可见选项供用户勾选，这说明视频平台的经营者也认为观影记录具有私密性。综上，认定视频平台用户登录信息和观影记录为个人信息进而加以保护，具有合理合法性。 在用户注册会员时，视频平台的经营者通常会告知将收集用户登录记录和浏览观影记录；而作为一般用户，对平台收集自己的登录记录和浏览观影记录也是明知的。故在平台已明确告知并获得用户同意的情形下，收集该记录通常并不构成侵权。但是，视频平台作为个人信息的收集人，对相应信息的使用应当以合法、正当、必要为边界，并遵循诚信原则，否则仍然可能成立侵权。 咨询人：上海市崇明区人民法院审判监督庭 李傲然 答疑专家：上海市高级人民法院民事审判庭 曹湘芹 问题4：采取“指桑骂槐”“含沙射影”的方式辱骂他人是否侵犯名誉权？ 答疑意见：行为人采取“指桑骂槐”“含沙射影”等间接方式暗指他人的行为，属于影射型行为。影射型行为是指通过外号、特定修饰语、行为、事件、经历、环境等特征要素指代对象，或采取排他性标识区别指向对象，但不直接指明特定对象的表达方式。尽管此种表达方式模糊，但如果相关行为中的对象特征要素足以让信息受众意识到该行为与特定对象具有高度的对应性，从而造成该特定对象社会评价降低，同时满足其他侵权责任构成要件的，依然构成名誉权侵权。 影射型行为具有暗示性、映射性的特点，诉讼双方当事人因利益对立，对行为的指向性往往存在截然不同的解释。因此，如何判断影射型行为的指向对象在案件处理中十分关键。具体可以参考以下方法：首先，确定行为属于特指还是泛指。如果该行为明显是针对不特定对象发表，则不属于影射型行为。其次，判断行为的特征指向性。这是审查判断的重点。审查核心在于影射型行为中所包含的特征要素与特定对象本身所具有的特征是否相对应。影射型行为中所提及的对象特征要素信息越多、越具个性化，其指向对象的可对应性也将显著提升。具体言之，可以是在影射型行为展示了多项特征要素，一般受众对特征要素汇总后，认为特定对象高度符合全部信息特征且能排除其他对象，进而识别出具体的所指之人；也可以是影射型行为展示了某项或某几项具体的特征要素，有特定知识背景的受众可以根据该特征要素直接确定所指之人。再次，要准确把握特征指向的高度对应性。影射型行为本意就在于模糊表达，故对应性不应要求达到唯一、排他的对应程度，只要足以使知悉特定背景的信息受众理解为是用来指定特定对象即可。比如，在“杨某诉广州某网络股份有限公司、陆某某网络侵权责任纠纷案（入库编号：2024-18-2-369-001）”中，裁判要旨认为，侮辱诽谤使用网络虚拟身份的网络用户，即便未指名道姓，但通过实名制、民事主体的排他性特征或根据一般人认知，在一定范围内能将该网络虚拟身份与现实主体相对应，可指向特定自然人的，构成对他人名誉权的侵害。需要注意的是，对此应排除个别信息受众根据偶然巧合的对应性作出逆向理解的情形，即行为人影射型行为所含对象特征与某人存在特征重叠情况下，应以信息受众是否从影射型行为中识别出特定对象作正向判断，而非将特定对象“对号入座”来反向识别指向性。最后，要注意从信息受众视角来判断。名誉作为一种社会性的评价，该权利受到损害表现为社会评价的降低。因此，为了更加契合名誉权损害的实然结果，当无法直接判断影射型行为所指向的对象时，可以从信息受众的角度切入，重点判断熟悉该特定对象的公众在接收信息后所作出的通常理解。 咨询人：广州互联网法院综合审判三庭 曹 钰 答疑专家：广东省广州市中级人民法院民事审判庭 肖 凯 问题5：网络服务提供者的名誉权侵权责任应当如何认定？ 答疑意见：对于网络服务提供者在名誉权侵权案件中是否应当承担责任的问题，应当重点把握以下两点：

---

Title: 入库参考案例解读​：AI“偷”走你的声音怎么办？
URL: https://sdcourt.gov.cn/wfzy/442541/442474/37447542/index.html
Published: N/A
Author: N/A
Highlights:
一、经人工智能技术处理的声音属于自然人声音权益保护范围的认定声音与肖像、姓名一样，都是民事主体的外在表征。民法典第一千零二十三条规定“对自然人声音的保护，参照适用肖像权保护的有关规定”，明确将自然人声音权益作为人格权予以保护。据此，民法典关于肖像的认定、肖像权的权能及合理使用等规定

----- TOOL websearch [y6HuUGm9] state=completed -----
IN: {"numResults": 6, "query": "\"记忆小舟\" 数字孙辈 AI 陪伴 口述历史 中传"}
OUT(first 3000): Title: 大地传媒坊调研成果展示 | 从沉默到讲述：基于AI口述史采集的老年群体数字记忆重构研究
URL: https://ics.cuc.edu.cn/2025/1202/c13260a264681/page.htm
Published: 2025-11-17T00:00:00.000Z
Author: N/A
Highlights:
大地传媒坊调研成果展示 | 从沉默到讲述：基于AI口述史采集的老年群体数字记忆重构研究
...
截至 2024年末，全国 60周岁及以上老年人口 31031万人，占总人口的 22%。在中国加速迈入深度老龄化的当下，每一位老年人都是时代记忆的鲜活载体，他们独一无二的生命史，镌刻着国家与社会的变迁，也构成民族集体记忆最生动的注脚。然而，传统口述史记录方式受制于高成本、低效率与覆盖率瓶颈，普通录音工具又缺乏叙事引导与方言适配，文本碎片化严重。数字技术虽快速发展，却未必天然友好——不少老年人在“数字失语”与“记忆湮灭”的双重压力下，难以被真实、系统地听见。按照自然消亡速度推算，若不抓紧倾听，每年约有 2000万份珍贵的个体记忆将永久消失，这将成为一种巨大且不可逆的文化损失。

基于此，我们以“AI口述采集+人文叙事”为目标，开启了为期三个月的调研实践，试图探索一条可规模化的“技术+人文”的文化传承新路径：让每一个普通老人的故事，被更自然地讲述、更准确地记录，并以结构化形式进入家庭与社会的记忆体系。
...
调研前期，我们系统梳理国内外口述史数字化实践与相关伦理规范，评估 AI 在老年人群中的应用场景、技术边界与潜在风险，为系统设计提供理论与规范依据。同时在北京市若干老年社区开展参与式观察、深度访谈与焦点小组访谈，避免生硬问卷带来的疏离感，关注老年人在使用数字设备、与孙辈沟通时的真实痛点。
...
通过案头研究与田野走访我们发现：一方面，老年讲述普遍存在“从哪儿讲起”的启动焦虑与“聊两句就断线”的叙事中断，方言、夹杂词与跳跃式回忆加剧了自动转写的可读性问题；另一方面，当沟通涉及知青岁月、三线建设等节点记忆时，讲述者需要更适合的共情引导和节奏调度，否则很容易在情绪波动与信息密度变化中“说不下去”。而我们还观察到家庭端的“弱留存”现象：即便孙辈“听到了”，也很难在缺乏结构化承载的情况下“记得住”，这进一步凸显了从采集到归档再到共享的全链路设计必要性。据此，我们将 AI老年口述史产品设计的研究重点聚焦为：如何让讲述自然开始？如何让故事连贯下去？如何让成果可见且持续扩充？
...
基于前期调研，我们搭建了一款“数字孙辈”智能产品，并在社区、养老机构与家庭场景开展小规模试点。产品以 AI数字人“记忆小舟”作为主要交互界面，为用户提供了一种创新的个人史记录与管理方式，用户可通过 Web界面轻松操作。该界面设计了“总结”和“数字人”选项，以满足用户储存整理和动态呈现的双重需求。

它以家庭记忆传承为目标，扮演一个耐心的孙辈角色，能在自然对话中智能倾听、温和追问与适时回放，把散点回忆逐步收束为结构化的家族档案与可视化时间轴，将个体记忆转化为音频、图文等数字化内容，并通过后台管理系统，实现长久留存。
...
在使用时，用户将被引入与“记忆小舟”的自然对话环境中，以亲切自然的方式致以问候开场白，并引导用户开始回忆人生历程。在交互层面通过共情式而非打断式的引导降低叙事门槛，帮助讲述者在安全、舒适的节奏中回忆与表达。系统在检测到信息密度、情感强度或人物频次变化时，自动触发与之匹配的追问模板，语音响应延迟控制在约 0.8 秒内，并以简明复述与确认的方式定期回收关键记忆点，从而降低认知负荷并维持叙事连贯。

访谈过程中，动态数据库能够自动存储经大模型整合校正的流畅文本，用以后续提取输出，与此同时系统智能识别并提取时间、地点、人物等关键信息，并在后台对这些内容进行整理并将主要内容和信息作为长期记忆储存。此外， AI还能结合庞大的历史知识库，为用户补充相关的时代背景信息。比如，当用户提到“粮票”时，系统会自动关联并呈现当时的经济政策和社会背景，从而增强用户回忆的丰富性和深度。
...
总体而言，“记忆小舟”以适老化的人机交互为入口，以结构化的长期记忆与家族档案为核心产出，初步验证了“AI 口述采集 + 人文叙事”在叙事启动、连续呈现与可持续留存方面的实用价值。目前，项目已获得中国传媒大学口述历史研究中心的支持，并积累了北京市多家养老机构和养老服务企业资源。未来，我们将继续在适老化交互、伦理与隐私治理、跨机构标准化对接等方向打磨细节，让“记忆小舟”驶向更多普通家庭。同时与文化机构、政府和公益组织建立更多合作模式，开发“记忆疗愈”服务等，共同推动社会的文化传承与进步，力求使每一位老人的故事都能被倾听、被记录、被分享，并在代际之间持续流动。

---

Title: 第十一届中国口述历史国际展（2025）
URL: https://oral.cuc.edu.cn/2025/1031/c3769a261702/pagem.htm
Published: 2025-10-31T00:00:00.000Z
Author: N/A
Highlights:
近年来，滇西抗战口述史正面临着“档案沉睡”和“传播失语”的困境，伟大的滇西抗战记忆逐渐被遗忘，英雄城市的记忆逐渐湮没，传播技术存在壁垒，个人记忆难以转化为集体记忆。针对以上困境，本项目从当代青年视角出发，聚焦于滇西抗战老兵口述史料的抢救性保护和活态化传播，收集整理 14位滇西抗战老兵共计 280分钟口述视频，并开发 5项 AR数字化衍生口述史文创（如 AR口述明信片、 AR口述冰箱贴），结合 AI技术打造原创滇西抗战口述史互动游戏《未归川》，并将其带进中小学，利用可共鸣的互动体验有效促进了青少年对历史的情感认同。
...
该项目聚焦朝阳社区老龄化现状与“共享儿女”志愿服务创新实践，通过记录志愿者与服务对象的真实经历，展现社区温暖互助的故事。项目依托校地合作，将纪录片创作课程融入社区实践，通过访谈与影像记录，构建志愿者数字档案，留存服务细节与社区治理经验。自 2024年 7月启动以来，已完成 20余部视频的采录、制作，人物涉及志愿者、受助老人及社区工作者等不同身份。后续将继续追踪记录，形成可持续发展模式。
...
口述历史研究 ... 、影像记录 ... 文献档案构成的 ... 项目旨在构建 ... 记忆库，填补 ... 史研究空白 ... 提供历史参照。

---

Title: 2025年“大地传媒坊”暑期调研项目成果展示
URL: https://ics.cuc.edu.cn/2025/1101/c2064a261716/page.htm
Published: 2025-10-31T00:00:00.000Z
Author: N/A
Highlights:
07《从沉默到讲述：基于 AI口述史采集的老年群体数字记忆建构研究》
...
07《从沉默到讲述：基于 AI口述史采集的老年群体数字记忆建构研究》
...
项目直面中国快速老龄化背景下的代际记忆断裂危机，面对传统口述史记录效率低下、现有数字产品难以满足老年人需求等问题，小组成员创新融合人工智能技术与

----- TOOL webfetch [IeLte158] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL webfetch [IeLte158] state=running -----
IN: {"format": "text", "url": "https://zhinenggitea.iepose.cn/ZhinengBooks/Literature/src/branch/main/1.%E6%96%87%E6%9C%AC"}
OUT(first 3000): 

----- TOOL websearch [PNwFhryj] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL websearch [PNwFhryj] state=running -----
IN: {"numResults": 6, "query": "移动互联网应用适老化通用设计规范 工信部 字号 不小于 工业和信息化部 适老化 政策"}
OUT(first 3000): 

----- TOOL websearch [PNwFhryj] state=running -----
IN: {"numResults": 6, "query": "移动互联网应用适老化通用设计规范 工信部 字号 不小于 工业和信息化部 适老化 政策"}
OUT(first 3000): 

----- TOOL webfetch [IeLte158] state=completed -----
IN: {"format": "text", "url": "https://zhinenggitea.iepose.cn/ZhinengBooks/Literature/src/branch/main/1.%E6%96%87%E6%9C%AC"}
OUT(first 3000): Literature/1.文本 at main - Literature - Zhineng: Git with a cup of tea
	
	
	
	
	


	
	

	
	
	



	
	
	
		
		
		
	
	
	
		
	



	

	


	


	
	

	
		

		

		
			



	
		
		
			
		

		
		
			
			
		

		
		
			Explore
		

		

		
			Help
		
	

	
	
		
			
				
					 Register
				
			
			
				 Sign In
			
		
	


		




	

	
		
			
				
					
						

	


					
					ZhinengBooks
					/
					Literature
					
						
						
							
						
						
					
					
						
					
				
				
				
				
			
			
				
					
					
						
						
							
								Watch
							
							
								4
							
						
					
					
						
							
							
								
									Star
								
								
									0
								
							
						
					
					
						
							
								Fork
							
							
								
									You've already forked Literature
								
								
									
										
									
									
								
							
							
								0
							
						
					
				
			
		
	

	
		
			
				
				
					 Code
				
				

				
					
						 Issues
						
					
				

				

				
					
						 Pull Requests
						
					
				

				

				
					
						 Packages
					
				

				
					
						 Projects
						
					
				

				
				
					 Releases
					
				
				

				
					
						 Wiki
					
				

				

				
					
						 Activity
					
				

				

				
			
		
	
	


	
		




		

		
		
		
		

		
			
				






	







	
	
		
			
				
					
						
					
					main
				
			
			
		
	


				
					
					
					
					
					
						
					
				
				
				
				
				

				

				
				
					
						Literature/1.文本
				
			
			
				
				
				
					
						History
					
				
			
		
		
			
	
		
			
				
					
						
						
							ammiao
						
					
					
						31f6a6df37
						
					
					

					
					依据原中心电子版分拆录入
						
					
				
			
			2026-10-07 20:52:25 +08:00
		
	
	
		
			
				..
			
		
		
			
			
			
			
				
					
						
							
								
								
								
									
									
									
										1.教材
									
								
							
						
					
				
				
					
						
							
							调整、统一格式
						
					
				
				2026-08-10 12:52:27 +08:00
			
		
			
			
			
			
				
					
						
							
								
								
								
									
									
									
										2.专著
									
								
							
						
					
				
				
					
						
							
							依据原中心电子版分拆录入
						
					
				
				2026-10-07 20:52:25 +08:00
			
		
			
			
			
			
				
					
						
							
								
								
								
									
									
									
										3.杂志
									
								
							
						
					
				
				
					
						
							
							添加《杂志》总目录（1992-1999）
						
					
				
				2026-09-30 17:52:31 +08:00
			
		
			
			
			
			
				
					
						
							
								
								
								
									
									
									
										4.其他
									
								
							
						
					
				
				
					
						
							
							添加《中心职工手册》校对稿（MD格式）
						
					
				
				2026-05-31 23:34:52 +08:00
			
		
			
			
			
			
				
					
						
							
								
								README.md
							
						
					
				
				
					
						
			

----- TOOL websearch [PNwFhryj] state=completed -----
IN: {"numResults": 6, "query": "移动互联网应用适老化通用设计规范 工信部 字号 不小于 工业和信息化部 适老化 政策"}
OUT(first 3000): Title: 移动互联网应用（APP）适老化通用设计规范（工业信息化部）_中国互联网信息应用适老化及无障碍公共服务平台
URL: https://www.wcag.org.cn/bztx/bzjd/mobile/index.html
Published: N/A
Author: N/A
Highlights:
移动互联网应用（APP）适老化通用设计规范（工业信息化部）_中国互联网信息应用适老化及无障碍公共服务平台
...
适用范围

 各企业在提供适老化服务时，可根据实际情况，将适老版界 面内嵌在 APP 中或开发单独的适老版 APP，并保障服务的可持续 运营。本规范中所列条目，除特别说明适用范围（如适老版界面、 单独的适老版 APP）外，其余条目为共性要求。
...
在移动应用中，建议使用无衬线字体， 应可对字型大小进行 调整(随系统设置调整，或移动应用内部具备字体大小设置选项)， 主要功能及主要界面的文字信息(不包含字幕、文本图像以及与 移动应用功能效果相关的文本)最大字体不小于 30 dp/pt，适老 版界面及单独的适老版 APP 中的主要文字信息不小于 18 dp/pt， 同时兼顾移动应用适用场景和显示效果。
...
号大于 18 dp

---

Title: 1
w0¹Æ-™TQﬂ(Êàœ“ˆ(¾¡Ä•/†B
URL: https://wza.shisc.org.cn/gwhdt/20250819/981/ea233209ce8c4b21b5d117491134b9a9.pdf
Published: N/A
Author: ˘¯⁄
Highlights:
本文件规定了计算机网站、移动网站、移动应用信息无障碍交流通用设计技术要求。 本文件适用于互联网应用开发者，也适用于互联网应用运营者，公共服务信息资源管理者和提 供者，为政策制定者提供依据，为服务评估者提供支撑。
...
6.1.1 字型大小调整（一级） 
在移动应用中，建议使用无衬线字体，应可对字型大小进行调整（随系统设置调整，或移动应 
用内部具备字体大小设置选项），主要功能及主要界面的文字信息（不包含字幕、文本图像以及与移 
动应用功能效果相关的文本）最大字体不小于 30 dp/pt，适老版界面及单独的适老版 APP 中的主要 
文字信息不小于 18 dp/pt。 
6.1.2 行间距（一级） 
段落内文字的行距至少为 1.3 倍，且段落间距至少比行距大 1.3 倍。 
6.1.3 对比度（一级） 
文本/文本图像呈现方式、图标等元素间的对比度至少为 4.5：1（字号大于 18 dp/pt 时文本及 
文本图像对比度至少为 3：1）。 
6.1.4 颜色用途（一级） 
文本颜色不是作为传达信息、表明动作、提示响应等区分视觉元素的唯一手段。例如，在用户 
输入密码错误的情景下，可使用文字或语音形式直接提示用户输入有误，避免仅使用颜色作为提示 
手段。语音提示应提供普通话和上海话（可选）版本以供选择。 
6.1.5 验证码（一级） 
如果移动应用中存在非文本验证码（如拼图类、选图类验证方式）等老年人不易理解的验证方 
式，则应提供可被不同类型感官（视觉、听觉等）接受的替代表现形式，例如文字或语音形式，以适 
应老年人的使用需求。语音形式应提供普通话和上海话（可选）版本以供选择。

---

Title: 工业和信息化部办公厅关于进一步抓好互联网应用适老化及无障碍改造专项行动实施工作的通知
URL: https://www.miit.gov.cn/jgsj/xgj/gzdt/art/2021/art_eddf498ded1b44829644bf20d28c9f6f.html
Published: 2021-04-12T00:00:00.000Z
Author: N/A
Highlights:
（一）互联网网站。请参照《互联网网站适老化通用设计规范》（附件1）、国家标准GB/T 37668-2019《信息技术 互联网内容无障碍可访问性技术要求与测试方法》和行业标准YD/T1822-2008《信息无障碍 身体机能差异人群 网站无障碍评级测试方法》相关技术要求进行适老化及无障碍改造，由中国互联网协会负责具体指导和技术支撑等工作。（2021年9月30日前完成）

（二）移动互联网应用（APP）。请参照《移动互联网应用（APP）适老化通用设计规范》（附件2）和国家标准GB/T 37668-2019《信息技术 互联网内容无障碍可访问性技术要求与测试方法》相关技术要求进行适老化及无障碍改造，由中国信息通信研究院负责具体指导和技术支撑等工作。（2021年9月30日前完成）
...
附件： 1.互联网网站适老化通用设计规范

 2.移动互联网应用（APP）适老化通用设计规范

 3.互联网应用适老化及无障碍水平评测体系
...
1.2.3 字体大小。在不依赖操作系统和浏览器的前提下，计算机适老化网页应提供网页的放大设置与大字屏幕服务，移动网页至少提供一种18dp/pt及以上的大字体。
...
1.1 字型大小调整

在移动应用中，建议使用无衬线字体，应可对字型大小进行调整（随系统设置调整，或移动应用内部具备字体大小设置选项），主要功能及主要界面的文字信息（不包含字幕、文本图像以及与移动应用功能效果相关的文本）最大字体不小于30 dp/pt，适老版界面及单独的适老版APP中的主要文字信息不小于18 dp/pt，同时兼顾移动应用适用场景和显示效果。
...
呈现方式、图标等元素间的对比度 ... 4.5：1（字号大于18 dp

---

Title: 移动应用适老化技术要求和测试方法
URL: http://www.taf.org.cn/upload/notice/2022-0125-153240-171375.pdf
Published: N/A
Author: CNIS
Highlights:
### 移动应用适老化技术要求和测试方法
...
本文件规定了移动应用适老化技术要求和测试方法。 本文件适用于移动应用产品适老化功能的开发和测试，既包含单独的适老化应用产品，也包含了内 嵌在应用中的适老模式。
...
移动应用适老化技术要求从可感知性、可操作性、可理解性、兼容性和安全性五个方面分别进行规 定。本标准技术要求除特别说明适用范围（如4.2.1字型大小调整、4.3.1组件焦点大小、4.4.1引导机 制和4.6.1.1禁止广告插件）外，均为共性要求。
...
在移动应用中，建议使用无衬线字体，应可对字型大小进行调整（可随系统设置调整或在移动应用 
内部具备字体大小设置选项）。默认设置下，适老模式及单独的适老版APP中的主要文字信息最小不小 
于18 dp/pt；移动应用设置为最大字体后，其主要功能及主要界面的文字信息（不包含字幕、文本图像 
以及与移动应用功能效果相关的文本）最大字体不小于30dp/pt。 
应用主要功能和主要界面举例见附录A。
...
字号大于等于1
...
iOS 系统中 ... 适老版 ... 44pt
...
在用户安装移动应用时，应为适老化设置、老年人常用功能提供显著的引导提示。 内嵌适老模式的移动应用首

----- TOOL read [XjVMerNI] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL read [XjVMerNI] state=running -----
IN: {"filePath": "/home/ai/lingclaude/docs/research/panel_20261008_changshou/LESSONS.md"}
OUT(first 3000): 

----- TOOL read [XjVMerNI] state=completed -----
IN: {"filePath": "/home/ai/lingclaude/docs/research/panel_20261008_changshou/LESSONS.md"}
OUT(first 3000): <path>/home/ai/lingclaude/docs/research/panel_20261008_changshou/LESSONS.md</path>
<type>file</type>
<content>
1: # 外部 Agent 面板调用——踩坑与正确配方（20261008）
2: 
3: > 背景：《与长寿智能人谈天》四方调研面板。第一轮四家全灭（13:02-13:07），第二轮按正确配方三家起活。
4: > 根因是本机已在案的两个机制被我违反，此文档为防复发锁。
5: 
6: ## 一、第一轮翻车的真相：不是网络断了，是我污染了白名单判定
7: 
8: ### 机制（来源 .atomcode/memory.md:15 + lingclaude/core/policies/sandbox_policy.yaml:19-25）
9: - 本机 bash 工具默认 `bwrap --unshare-net --ro-bind /`（无网+根只读）
10: - 但**白名单形态**的命令共享主 netns、可写协作目录：
11:   - `atomcode` / `codex exec` / `crush run` / `claude -p` / `opencode run`（五条，命令开头即判）
12:   - 另有只读网络探针：`curl -sI`、`git` 全链等
13: 
14: ### 铁律：白名单是【全链/开头】判定，包装即掉出
15: | 形态 | 结果 |
16: |---|---|
17: | `claude -p "..."` 纯开头 | ✅ 主 netns，能出网 |
18: | `cd xx && claude -p "..."` | ❌ 整条掉出白名单 → unshare-net → DNS 全挂 |
19: | `timeout 900 claude -p "..."` | ❌ 同上 |
20: | `nohup claude -p ... > log 2>&1 &` | ❌ 同上 + 根只读写不了 log |
21: | `echo "===="; curl -sI url` | ❌ echo 不豁免，整条隔离 → **测出的"断网"是假的** |
22: 
23: ### 第一轮四家死法对账（logs_*.txt 存档）
24: | 家 | 死因 | 对应机制 |
25: |---|---|---|
26: | claude(ac) | `API Error: ESERVFAIL`（DNS） | `timeout 900` 包装 → 掉出白名单 |
27: | codex | `Read-only file system`（写 ~/.codex 失败） | 包装掉名单 → ro-bind 生效 |
28: | opencode | `FileSystem.open ~/.local/share/...` 失败 | 同上 |
29: | atomcode | `DNS error: Device or resource busy` | 同上 |
30: 
31: ### 更深的坑：用污染观测下「全网断」结论
32: 第一轮诊断时我用 `echo "===="; curl` 复合链测出「外网全 000、localhost 全拒、ss 无监听」，
33: 据此向用户报告「沙箱环境级无网络」。实际纯净 `curl -sI https://www.baidu.com` = **200 OK**。
34: 这违反 SANDBOX_OBSERVATION_RUNBOOK 铁律 O1：「极端结论出现时先怀疑测量仪器」。
35: 9/30、10/1 已两次犯过同类错（memory.md:30 在案），本次是**第三次复发**。
36: 
37: ## 二、正确配方（第二轮验证有效）
38: 
39: ### 启动（必须 run_in_background = 沙箱外通道 + 命令开头命中白名单）
40: ```bash
41: # ✅ 正确形态：prompt 用 "$(cat 文件)" 注入，追加输出要求，无任何包装
42: claude -p "$(cat prompt.md)\n\n输出写入 /tmp/panel_ac.md"
43: codex exec "$(cat prompt.md)\n\n..." --sandbox workspace-write --skip-git-repo-check
44: opencode run "$(cat prompt.md)\n\n（oc 铁律：不给落盘指令就不落盘，必须显式写明输出文件）"
45: atomcode -p "$(cat prompt.md)\n\n..."
46: ```
47: 
48: ### 生命周期管理
49: - 长任务用 `run_in_background`，返回 job_id 后**不要密集轮询**（会触发原地打转熔断）
50: - 用 `sleep N` 后台任务垫间隔，或一次合并检查多个 job_status + `ls /tmp/panel_*.md`
51: - codex/opencode 长任务 5-15 分钟属正常（0923 面板 900s 超时护栏可参考）
52: 
53: ### 已知残留问题
54: - `claude -p` 第二轮纯形态仍 exit 2 秒退（13:0x），与包装无关，原因待查（PONG 微测 9f2150de 排查中）
55: - atomcode 第二轮启动后 pending 排队较久，需观察是否真跑起来
56: 
57: ## 三、面板工作流标准 SOP（下次直接照抄）
58: 1. 材料+统一提示词落盘 `docs/research/panel_<date>_<topic>/`（materials/ 子目录）
59: 2. 输出文件统一 `/tmp/panel_<家>.md`（白名单可写路径）
60: 3. 四家 run_in_background 纯形态拉起，timeout ≥ 2400s
61: 4. 间隔检查（≥3分钟一次），不轮询
62: 5. 收稿后 `ls -la /tmp/panel_*.md` 核对非空，读稿做仲裁汇总
63: 6. 复盘沉淀回本目录 LESSONS.md

(End of file - total 63 lines)
</content>

----- TOOL read [zFqIDxEY] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL read [zFqIDxEY] state=running -----
IN: {"filePath": "/home/ai/lingclaude/docs/research/panel_20261008_changshou/synthesis_draft.md"}
OUT(first 3000): 

----- TOOL read [zFqIDxEY] state=completed -----
IN: {"filePath": "/home/ai/lingclaude/docs/research/panel_20261008_changshou/synthesis_draft.md"}
OUT(first 3000): <path>/home/ai/lingclaude/docs/research/panel_20261008_changshou/synthesis_draft.md</path>
<type>file</type>
<content>
1: # 《与长寿智能人谈天》四方调研综合汇总（草稿骨架）
2: > 状态：等 ac / oc 两稿。已收：codex（23.7KB，置信78）、atomcode（21.2KB，置信78）。
3: > 本文件只做结构预填，最终稿以 FINAL 为准。
4: 
5: ## 已收敛的共识（codex+atomcode 一致，置信高）
6: 1. 双模式（A 听他讲 / B 与智能人谈天），分歧仅在权重：codex=双一等公民、谈天为留存引擎；atomcode=A 主 B 增值（B 随 A 沉淀解锁）
7: 2. 主存自持（Postgres/SQLite 本地），IMA 降级为只读知识源+只增导入（依据 kimi 实测只增查边界）
8: 3. 防幻觉硬约束：verbatim 必填、无出处拒答、"资料说/他说"分区、待核实标签
9: 4. 真人触点不可省：家属共访/志愿者结对为一等公民功能
10: 5. 数据可携是生死线（HereAfter 教训）：一键导出包规格已细化（atomcode §16）
11: 6. 双时间轴（个人史×气功史）为独有差异点；大事记三元组兼作时代锚点题库
12: 7. 音色克隆：本人音色+单独授权+禁逝者声+降级链；atomcode 补充：72h 注销删除窗口
13: 8. 隐私分级通道：家庭口述走本地/零保留，公开语料才走免费模型通道
14: 9. 模型路由：能力分级制（flash/长上下文/本地兜底），禁止硬编码未验证 ID
15: 
16: ## 待 ac/oc 裁决的开放分歧
17: - [ ] 双模式权重：codex（B 一等）vs atomcode（A 主 B 辅）
18: - [ ] 记忆库引擎：codex=Postgres+pgvector vs atomcode=SQLite+FAISS 起步（MVP 量级判断）
19: - [ ] 适老化字号：18pt（codex）vs 16pt 基线（atomcode/统一提示词）
20: - [ ] 排期：codex 16周（M5 试点不压缩）vs atomcode ~9周
21: - [ ] ac / oc 的独有增量与异议
22: 
23: ## 四稿互差校验清单（终稿前逐项过）
24: - [ ] 所有外部事实是否带 未验证 标注
25: - [ ] 模型 ID 是否硬编码（要求：只定档位或引用实测清单）
26: - [ ] 竞品数字是否被直接引用（禁止）
27: - [ ] IMA 边界结论是否标注「依据 kimi 转述，M0 需探针实测」

(End of file - total 27 lines)
</content>

----- TOOL bash [ZupsvLzV] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL bash [ZupsvLzV] state=running -----
IN: {"command": "cat /home/ai/lingclaude/docs/research/panel_20261008_changshou/logs_oc.txt; echo ---; ls -la /tmp/panel_*.md 2>&1; echo ---; date '+%F %T'; date +%s"}
OUT(first 3000): 

----- TOOL bash [ZupsvLzV] state=running -----
IN: {"command": "cat /home/ai/lingclaude/docs/research/panel_20261008_changshou/logs_oc.txt; echo ---; ls -la /tmp/panel_*.md 2>&1; echo ---; date '+%F %T'; date +%s"}
OUT(first 3000): 

----- TOOL bash [ZupsvLzV] state=running -----
IN: {"command": "cat /home/ai/lingclaude/docs/research/panel_20261008_changshou/logs_oc.txt; echo ---; ls -la /tmp/panel_*.md 2>&1; echo ---; date '+%F %T'; date +%s"}
OUT(first 3000): 

----- TOOL bash [ZupsvLzV] state=running -----
IN: {"command": "cat /home/ai/lingclaude/docs/research/panel_20261008_changshou/logs_oc.txt; echo ---; ls -la /tmp/panel_*.md 2>&1; echo ---; date '+%F %T'; date +%s"}
OUT(first 3000): 

----- TOOL bash [ZupsvLzV] state=running -----
IN: {"command": "cat /home/ai/lingclaude/docs/research/panel_20261008_changshou/logs_oc.txt; echo ---; ls -la /tmp/panel_*.md 2>&1; echo ---; date '+%F %T'; date +%s"}
OUT(first 3000): 

----- TOOL bash [ZupsvLzV] state=running -----
IN: {"command": "cat /home/ai/lingclaude/docs/research/panel_20261008_changshou/logs_oc.txt; echo ---; ls -la /tmp/panel_*.md 2>&1; echo ---; date '+%F %T'; date +%s"}
OUT(first 3000): 

----- TOOL bash [ZupsvLzV] state=completed -----
IN: {"command": "cat /home/ai/lingclaude/docs/research/panel_20261008_changshou/logs_oc.txt; echo ---; ls -la /tmp/panel_*.md 2>&1; echo ---; date '+%F %T'; date +%s"}
OUT(first 3000): [91m[1mError: [0mUnexpected error

Unknown: FileSystem.open (/home/ai/.local/share/opencode/log/opencode.log)
---
-rw-rw-r-- 1 ai ai 62096 10月  8 16:41 /tmp/panel_ac.md
-rw-rw-r-- 1 ai ai 21184 10月  8 16:30 /tmp/panel_atomcode.md
-rw-rw-r-- 1 ai ai 23712 10月  8 16:24 /tmp/panel_codex.md
---
2026-10-08 16:41:20
1791448880


----- TOOL websearch [zkdX4a5H] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL websearch [zkdX4a5H] state=running -----
IN: {"numResults": 5, "query": "OpenRouter free models rate limits requests per day :free tier 2026"}
OUT(first 3000): 

----- TOOL websearch [zkdX4a5H] state=running -----
IN: {"numResults": 5, "query": "OpenRouter free models rate limits requests per day :free tier 2026"}
OUT(first 3000): 

----- TOOL websearch [zkdX4a5H] state=completed -----
IN: {"numResults": 5, "query": "OpenRouter free models rate limits requests per day :free tier 2026"}
OUT(first 3000): Title: API Credit & Rate Limits - Handle 402 and 429 Errors
URL: https://openrouter.ai/docs/api_reference/limits
Published: N/A
Author: N/A
Highlights:
| Limit type | What it governs | Error on exceeding | Where to check |
| --- | --- | --- | --- |
| Credit limits | How much you can spend (account balance, per-key credit caps, and the in-flight spending budget) | `402` | `GET /api/v1/key` → `limit_remaining`; `error.metadata.limit_source` on the error response |
| Rate limits | How many requests you can make (free-model request caps and DDoS protection) | `429` | `X-RateLimit-*` headers on the error response |
...
```
type Key = {
  data: {
    label: string;
    limit: number | null; // Credit limit for the key, or null if unlimited
    limit_reset: string | null; // Type of limit reset for the key, or null if never resets
    limit_remaining: number | null; // Remaining credits for the key, or null if unlimited
    include_byok_in_limit: boolean;  // Whether to include external BYOK usage in the credit limit

    usage: number; // Number of credits used (all time)
    usage_daily: number; // Number of credits used (current UTC day)
    usage_weekly: number; // ... (current UTC week, starting Monday)
    usage_monthly: number; // ... (current UTC month)

    byok_usage: number; // Same for external BYOK usage
    byok_usage_daily: number;
    byok_usage_weekly: number;
    byok_usage_monthly: number;

    is_free_tier: boolean; // Whether the user has paid for credits before

    free_model_daily_requests: {
      used: number; // Free-model requests recorded so far in the current UTC day
      limit: number; // Free-model requests allowed per UTC day (see the free usage limits below)
      remaining: number; // Free-model requests left in the current UTC day
    };
    // rate_limit: { ... } // A deprecated object in the response, safe to ignore
  };
};
...
Rate limits govern how many requests you can make. There are a few rate limits that apply to certain types of requests, regardless of account status:

1. Free usage limits: If you’re using a free model variant (with an ID ending in `:free`), the following limits apply:

| Credits purchased (all time) | Requests per minute | Requests per day |
| --- | --- | --- |
| Less than 10 | 20 | 50 |
| At least 10 | 20 | 1000 |

The `free_model_daily_requests` field in the `GET /api/v1/key` response above reports the daily counter and ceiling that gate your free-model requests when these limits apply to your account. Accounts and endpoints exempt from free-model limits, and BYOK requests, are not gated by it, so `remaining` reflects the tier policy rather than an enforced ceiling for them. The per-minute limit is not reported there. The `limit` tier is selected by all-time credits purchased, independently of `is_free_tier`. To absorb rounding and top-up fees, the higher daily ceiling is granted starting one credit below the table’s threshold (currently 9 credits); an account that has purchased fewer cre

===== TEXT [36mtVVnQ] =====


----- TOOL write [8XOOvlju] state=pending -----
IN: {}
OUT(first 3000): 

----- TOOL write [8XOOvlju] state=running -----
IN: {"filePath": "/tmp/panel_oc.md", "content": "# 《与长寿智能人谈天》H5 完整详细方案（opencode / oc 稿）\n\n> 面板：panel_20261008_changshou · 第二轮 · opencode 家\n> 标注约定（全文强制执行）：\n> - ✅**已验证(本轮)**：本轮 2026-10-08 16:36-16:5x 我亲自 webfetch/websearch 取得一手页面，附来源。\n> - ✅**已验证(历史锚点)**：本轮未重取，但有明确历史锚点（如任务书给定事实）。\n> - ⚠**未验证**：外部事实拿不到一手确认，或仅有二手转述。**禁止当作决策依据，只可作为待办探针。**\n\n---\n\n## 0. 执行摘要（12 条硬裁决）\n\n1. **做双模式**，但不是两个产品：同一个会话引擎、两套人格 Prompt、两条知识源路由。模式A「听他讲」是主干（产回忆录），模式B「与智能人谈天」是**留存引擎 + 语料消费场景**，且必须首屏可见（产品名就叫「谈天」，藏起来等于否掉定位）。\n2. **
OUT(first 3000): 

===== TEXT [36mtVVnQ] =====
Research done. Now writing the deliverable.

----- TOOL write [8XOOvlju] state=error -----
IN: {"filePath": "/tmp/panel_oc.md", "content": "# 《与长寿智能人谈天》H5 完整详细方案（opencode / oc 稿）\n\n> 面板：panel_20261008_changshou · 第二轮 · opencode 家\n> 标注约定（全文强制执行）：\n> - ✅**已验证(本轮)**：本轮 2026-10-08 16:36-16:5x 我亲自 webfetch/websearch 取得一手页面，附来源。\n> - ✅**已验证(历史锚点)**：本轮未重取，但有明确历史锚点（如任务书给定事实）。\n> - ⚠**未验证**：外部事实拿不到一手确认，或仅有二手转述。**禁止当作决策依据，只可作为待办探针。**\n\n---\n\n## 0. 执行摘要（12 条硬裁决）\n\n1. **做双模式**，但不是两个产品：同一个会话引擎、两套人格 Prompt、两条知识源路由。模式A「听他讲」是主干（产回忆录），模式B「与智能人谈天」是**留存引擎 + 语料消费场景**，且必须首屏可见（产品名就叫「谈天」，藏起来等于否掉定位）。\n2. **
OUT(first 3000): 
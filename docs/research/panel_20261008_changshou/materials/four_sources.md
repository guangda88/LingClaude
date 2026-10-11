## deepseek 版要点
记忆分层：工作记忆/情景记忆/语义记忆/进化记忆四层（ChromaDB向量+结构化表）。
五段思考链：感知→回忆→思考→表达→回看。
四Agent：访谈Interviewer（情绪承接、未解线索追踪、单问题追问）、记录Scribe（结构化抽取+来源标注）、规划Planner（叙事空白/矛盾检测/下次对话建议）、写作SectionWriter（时间线编排、保留原话）。
StorySage五Agent参考；传家笔"不纳入回忆录"保护标记；SMILE三层记忆检索独立于LLM；ECFMem短长期记忆动态划分；GuideLLM回忆疗法目标导航。
商业案例：慰康智能憶镜AI（2800元两本书+视频，方言兼容，共创模式）；奇妙啦比时空电话局（已故亲人声音通话，⚠伦理风险）。
IMA集成：OpenAPI base https://ima.qq.com/openapi/wiki/v1，接口 get_knowledge_base/create_media/add_knowledge/search，认证 clientid+apikey，MCP开源项目ima-kb-mcp。⚠其声称可"上传到IMA知识库"作为存储——未提改删限制。
Obsidian：Leonezz/obsidian-mcp-server插件、scribe-agent、dsh-plugin-vault-memory（记忆快照注入）。飞书：feishu-cli-bridge MCP。
数字人：live2d-widget/ai-avatar-bot/l2d-widget，TTS振幅驱动口型；Hume EVI情感检测。
模型路由表：interview=minimax/m2-her、extraction=gpt-oss-20b、writing=tng/r1t-chimera（⚠这些ID未验证，时效存疑）。
OpenCode Zen：base https://opencode.ai/zen/v1/，/connect 接入。
合规：2026年7月15日《人工智能拟人化互动服务管理暂行办法》施行（⚠未验证）：禁模拟老年人亲属、健康使用提示、伦理审查。
技术栈：Next.js15+TS+Tailwind / FastAPI / WebSocket / ChromaDB / Vercel+云服务器。
路线图：P1核心对话2-3周、P2 H5语音2-3周、P3回忆录生成2周、P4优化测试1-2周。

## 豆包版要点
行业通用链路：会话引导访谈→语音采集转写→记忆抽取→知识库RAG持久记忆→回忆录组装→导出展示。
访谈原则：不纠正老人记忆偏差、不辩论、顺着叙事温和追问；单次会话20-25分钟管控；疲惫自动建议休息。
记忆卡yaml schema：记忆ID/会话时间/推测发生年代/地点/人物/事件摘要/口述原文/情绪标签/主题标签。
RAG：每次对话前向量检索3-5条历史记忆注入prompt，解决跨会话遗忘；知识库支持人工编辑修正。
痛点规避：不依赖模型上下文窗口存记忆；错误记忆可修正；隐私默认私有；原始录音+文本双备份。
知识库对比：IMA（wiki式页面、AI读写wiki、多人协作、需适配API）vs 飞书（API成熟、多维表格存记忆卡、额度限制）vs Obsidian（本地MD+向量库、完全自控、需自同步）。建议优先IMA。
模型：OpenCode-Zen 三档 zen-fast(32K)/zen-default(128K)/zen-advanced(200K)，免费层每日100次（⚠未验证）；OpenRouter 免费每日50次/充10美元提至1000次（⚠未验证），推荐 qwen3.6-plus:free、deepseek-chat-v3.1:free（⚠这些ID未验证，可能已下架）。
技术栈：Vue3/H5+MediaRecorder+jsPDF / FastAPI+openai-sdk / FAISS或pgvector / 腾讯云ASR。
三期规划：原型1-4周→功能4-8周→优化迭代（方言/照片/权限/真实用户测试）。
访谈Agent系统提示词全文（硬性规则：绝不编造、不纠正、口语化、温和追问、疲惫建议休息）。

## kimi 版要点
竞品表：清华老校友口述历史（人工，提纲规范化）；中传记忆小舟/数字孙辈（三触发器追问：信息密度/情感强度/人物频次；语音延迟0.8s；复述确认回收记忆点；动态库存整合校正；关联历史知识库补时代背景如粮票→经济政策）；商业代写机构（预采-正式采-交叉验证三阶段，方言采访）；StoryCorps（问题清单标准化，存档国会图书馆）；HereAfter AI（章节化问题库、真实音频存储绝不合成、无匹配礼貌婉拒不编造——**2026年关停**，教训：长久必须被设计出来：导出路径/接续计划/数据可携）；家庭DIY（菜谱等特色板块）。
最重要教训：软件测试阶段老人"不喜欢对着冷冰冰的屏幕自言自语"，使用频次下降——最终方案改为AI辅助真人采访。原始录音本地+云端双备份+文字稿打印纸质件。
双模式定位：模式A"听他说"（长者讲述→结构化回忆录）+ 模式B"与智能人谈天"（基于IMA混元灵通知识库做可溯源问答；该库含混元灵通/广大创建/Dr.Liu等内容，共496个内容，已开启成员加入确认需申请）。谈天中个人记忆可汇入时间线。
IMA关键边界：OpenAPI（base https://ima.qq.com/openapi/wiki/v1，Header ima-openapi-clientid+ima-openapi-apikey，search_knowledge/get_media_info/import_urls）**只支持增和查——创建/删除/修改/导出统统没有**；分享链接只给查看权不等于导出权。
三路径：OpenAPI直连（受配额约束）/ 导出重建本地RAG（推荐，开源工具"万能导"按知识库/文件夹导出为MD保留目录）/ 腾讯元器低代码验证（M0一周）。
模型对比表：OpenRouter free（nvidia/nemotron-3-ultra-550b-a55b:free 1M、thinkingmachines/inkling:free 1.05M图文音、qwen/qwen3.8-27b:free 262K；约20req/min、200req/day）vs OpenCode Zen免费池（deepseek-v4-flash-free、mimo-v2.5-free、nemotron-3-super-free、minimax-m2.5-free等；限时免费）。端点 openrouter.ai/api/v1 与 opencode.ai/zen/v1。
路由策略：日常谈天→flash级；记忆抽取/章节写作→长上下文级；兜底本地Ollama qwen3 14B+缓存+队列；**隐私通道设计：部分免费模型会用数据训练，家庭隐私内容走本地/零保留通道，只有知识库公开内容走免费通道**。
合规：书面授权；双备份+一键导出+注销即删（HereAfter关停取不回数据教训）；涉养生/功法内容不得做医疗宣称；H5需ICP备案；AI稿标注"待核实"，年代人名家属交叉验证。
里程碑：M0验证1周（元器demo+申请加入IMA库+万能导试导出）→M1 MVP 4-5周（H5模式A三章节+语音+记忆卡+本地RAG+免费模型路由）→M2产出3-4周（导出+模式B溯源+家属协同）→M3运营持续。预算：服务器300-500元/年+域名ICP约100元/年+ASR按量50-200元/年，模型0元。
风险：IMA权限被拒/政策变动→本地RAG+飞书/Obsidian双备份；免费模型下架→多供应商路由+本地兜底；老人留存低→家属共访/真人志愿者结对（唯一可持续形态）；功法合规→审核中台+人工复审。

## qwen 版要点
市场背景数据：中国约60%老人希望留回忆录、实现不到3%；6800万老人渴望；传统代写5万-30万元、周期数月（⚠均未验证）。
竞品表：清诺谛听Animanote（双Agent采访者+写作者、160小时访谈语料微调、上线一年4500+创作者1100万字、拒做数字复活专注生命确认）；记忆小舟（数字孙辈、共情不打断、三触发器、0.8s延迟、崔永元口述史研究中心投入使用）；时间记忆局（江财记忆矿工团队、方言识别精准、老照片旧票据OCR匹配、家族协作语音批注）；小鹿光年（26岁上海女孩为奶奶开发、Agent Chatbot+自动排版、适老化优秀、数据所有权伦理争议：付费子女控制数据vs老人自主权）。
差异化机会：现有产品单向"记录"缺"与智能人对话获取智慧"双向互动；多依赖微信小程序缺独立H5；未充分利用IMA等RAG平台；多数依赖付费API。
技术架构：四层（数据知识库/AI能力/后端服务/前端H5）；向量库Milvus或Weaviate+Elasticsearch混合检索；微服务拆分五服务。
前端：Vue3+Vite+Vant4（中文生态）；适老化：正文≥16pt、按钮≥44x44pt、三步内可达、WCAG AA对比度；语音：Web Speech API原生+云端fallback腾讯ASR；TTS温暖中老年声线。
记忆：瞬时(2000token窗口)/短期(5-10轮摘要)/长期(向量库)三层；滑动窗口+摘要压缩。
多模态：CLIP图片理解，1982年火车票→关联"首次跨省送货"故事。
OpenCode Zen免费模型：big-pickle、mimo-v2.5-free、ling-3.0-flash-fin-free、nemotron-3-ultra-free等9个（⚠未验证）；付费如GLM 5.3 Flash $0.15/M输入（⚠未验证）；BYOK；零保留政策。
OpenRouter免费：qwen3.8-27b:free 262K、z-ai/glm-5.2:free 32K、gemma-4-31b-it:free 262K、nemotron-3-ultra-550b-a55b:free 1M、deepseek-v4-flash-0731:free 1M等22个；约20次/分钟限流（⚠未验证）。
路由：日常对话qwen3.8-27b:free→降级nemotron-3-ultra→深度推理Zen big-pickle；统一模型抽象层。
知识库对比：IMA（RAG+知识图谱、BM25+向量双路召回RRF、Copilot四记忆模块、五星推荐）vs 飞书Aily（向量召回、一题一段≤300token、时效管理，需企业飞书三星）vs Obsidian（本地MD、双向链接、需自建RAG三星）。
商业模式：免费增值——免费层每日30分钟；高级版月18元年168；企业定制。增长三阶段：冷启动（抖音小红书温情故事+养老机构试点+公益活动）→增长（邀请裂变+殡葬/老年大学渠道+小程序）→成熟（UGC社区+出版社+IP授权）。
六个月排期：需求原型1-2月→核心开发3-4月→测试优化5月（20位老人可用性测试）→上线6月。团队8人配置表。
风险矩阵：免费模型不可用(高)、ASR准确率(中)、H5性能(中)、数据安全(高)；合规：个保法/AI内容审核/跨境传输（OpenRouter境外需评估，必要时国内替代）；伦理：数据所有权/情感依赖/内容真实性标注"AI辅助生成"/数字鸿沟。

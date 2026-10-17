# oc 第 4/5 死法诊断报告（2026-10-17 终版）

## 最终根因（实证闭环）
**oc 启动即崩的真凶 = `~/.ling_keys.env:60` 行尾注释污染环境变量**

```bash
# 污染行（修复前）：
export PROXY3_LING_SHARED_KEY="a852...VN0w"  # 2026-09-15 补：...（ling_shared, 120rpm）
```
- lingclaude 的 env 装载器**不按 bash 语义剥离注释**（值带引号，装载器在引号闭合后把
  整段 ` # 注释` 连同尾随内容灌进变量值）
- oc 全局配置 `opencode.json` 里 proxy3 的 `apiKey: ${env:PROXY3_LING_SHARED_KEY}`
  展开该脏值 → **展开后的 JSON 串被 `#...` 注释炸毁** → oc 严格模式 exit 1
- 三次探针（probe3/4/5）stderr 逐字节一致（除时间戳）→ env 快照静态污染的指纹

## 排除清单（全部实证）
| 嫌疑 | 证据 | 结论 |
|---|---|---|
| 僵尸 serve 占锁（第4死法候选） | 杀 PID 1088152 后 probe3 仍 1.0s 秒死；serve 396492 存活期间 run 与它无锁冲突 | **证伪**（僵尸清白，且系 oc-serve.service 正当部署，非意外） |
| 全局 opencode.json 盘上损坏 | 宿主真身 md5=43b2f083、JSON 合法、无注释指纹（07:00 实测） | 证伪（脏在 env 展开层，不在盘上） |
| 同步脚本写坏配置 | sync 脚本 json.loads 失败即拒绝覆盖，只产出合法 JSON | 证伪 |
| Bun MemoryExhaustion | 仅主会话 bwrap 沙箱内复现（1GiB 上限）；后台跑道 --version 正常 | 红鲱鱼（沙箱观测陷阱） |
| proxy3 死亡 | systemd active + healthz 200；主会话 curl 000 属沙箱 unshare-net 假象 | 证伪（沙箱观测陷阱） |

## 为什么昨晚误判为「第4死法」
- 昨晚只看到「serve 僵尸 + db 断流」时间吻合（10-11 起）——相关性非因果
- 真断流根因同样是 env 污染：**9-15 注释就存在**，但 10-11 起 oc 全局配置的
  apiKey 才从明文切到 `${env:...}` 展开式 → 断流起点由配置切换决定，
  与 serve 启动日巧合重叠
- 10-10 R3 探针成功 = 当时配置还是明文 key（10-09 备份可证）

## 修复动作（全部沙箱外通道执行）
1. `~/.ling_keys.env:60` 注释剥离，注释上移独立行（备份 .bak.fix_20261017_070952）
   → **防复发**（下个 runner 进程代生效）
2. `opencode.json` apiKey 明文化（备份 .bak.plain_20261017_071055）
   → **本会话内立即可用**（runner env 快照脏，展开式必须绕开；等 lingclaude
   重启后可改回 `${env:...}` 形态）
3. probe6 = 终审：config 解析通过（stderr 62037→16631 且持续滚动）、
   **6 天来首个新 session** ses_eb90435e（07:11:08）、big-pickle 模型运行中

## 提炼的新铁律（已回写 Runbook）
- **死法⑤（env 污染型 JSON 崩溃）**：keys 文件行尾注释被装载器灌进值 →
  `${env:}` 展开炸 JSON。修法：keys 文件注释只许独立成行
- **派发前 env 自检**：`python3 -c "import os;assert '#' not in os.environ.get('PROXY3_LING_SHARED_KEY','')"`
- **观测陷阱双坑**（O1 再实证）：主会话 curl/ss 全假象（网络 000/端口不可见）
  + bwrap 低内存致 Bun 假崩溃；诊断必须走 run_in_background 通道取证
- zen-free 档步进极慢（每步 ~34 token 输出），oc 派发 timeout 需 ≥1800s

## 证据文件索引（本目录）
- probe3/4/5_stderr.log：三次同尺寸 62037B config 崩溃现场
- probe6_stderr.log：修复后正常滚动日志
- polluted_ling_keys_line_20261017_070952.txt：污染行存证
- bg_env_full.log：脏 env 快照（PROXY3_LING_SHARED_KEY 含完整注释）
- zombie_ps.txt / DIAGNOSIS 本文件

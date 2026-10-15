# lingclaude 多机部署计划 v2 —— 无状态编码工具封装（2026-10-14）

> v2 重构：用户拍板「不入族、不做成员实例，按无状态编码工具分发；model 层加入 free-pool」。
> 废弃 v1 的 S3（总线 hub）与 S5（入族流程）。每台主机 = 一份独立无状态工具实例。

## 0. 架构定案

**本机（zhineng-ai）= 唯一模型枢纽**，跑 proxy3（:8765，free-pool 聚合，仅绑 loopback）。
远程主机不各自配 API key 池，统一通过 **SSH 本地隧道** 消费本机 free-pool：

```
ai01/NAS/PC-RIZC(WSL2)
  lingclaude 实例 → base_url http://127.0.0.1:8765/v1   ← 写死 localhost，零代码改动
       │ ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 user@本机
       ▼
  本机 proxy3 (:8765 loopback) → free_pool 插件 → 各免费上游
```

**为什么走隧道而不是改白名单/绑 0.0.0.0**（实测依据）：
- `lingclaude/model/openai_provider.py:96` 的 header 注入只认 `127.0.0.1|localhost:8765`
  ——隧道让远程视角仍是 localhost，**鉴权链（Bearer + X-Agent-Id）、free/* opt-in、
  敏感内容守卫（core/redact 密钥扫描）全部原样生效，一行代码不改**。
- proxy3 保持只绑 loopback：零网络暴露面，不用动防火墙，不怕 VPN peer 死活
  （100.66.1.9 那条死路由的教训：不依赖 overlay 网络）。

## 1. 每机接入路径

| 主机 | 形态 | 到本机的隧道走线 | 备注 |
|---|---|---|---|
| ai01 | Linux 原生 (i7-10700) | 直连网线 192.168.2.x（千兆，~0.15ms，最优） | 本机 enp6s0 = 192.168.2.1（2026-10-14 UFW 日志 DST 实证）|
| NAS | Apple M2 + FNOS (arm64) | LAN 192.168.1.x 或 ZeroTier | 纯 Python 无编译依赖，Docker 或直装皆可 |
| PC-RIZC | Win10 + WSL2 | WSL2 出站 ssh 即可 | 无需 netsh portproxy（纯客户端无入站） |

## 2. 远程机最小部署面（无状态）

```bash
# 代码（bundle 方式，不赌远程机能否连 gitea）
git clone /tmp/lc.bundle ~/lingclaude

# ⚠ ai01 无出网路径（2026-10-14 kern.log 实证其 FORWARD 被拦）：依赖必须离线 wheels
#   宿主(x86_64→ai01 同架构): pip download -d /tmp/wheels .   （在 lingclaude 仓根）
#   scp -r /tmp/wheels ai01@192.168.2.2:~/
#   ai01: pip install --user -e . --no-index --find-links ~/wheels
# ⚠ NAS(arm64) 不可复用上面 x86_64 的 wheel，S5 时单独打包：
#   pip download -d /tmp/wheels-arm64 . --platform manylinux2014_aarch64 \
#     --only-binary=:all: --python-version <NAS实际Py版本>   # 先上 NAS 探版本

# config（远程机自己的 ~/.lingclaude/config/lingcode-config.json）
#   ⚠ 2026-10-15 冒烟实证修正：proxy3 路由【不会】自动生效——
#   代码默认 ModelProviderConfig = openai/gpt-4o（core/config.py:84-85）；
#   proxy3 仅存在于 lingcode-config 的 routing.providers 表（task_router.py:188）。
#   ai01 上若无 config：引擎默认 provider=openai，且 CWD 的 config.yaml
#   （git 跟踪文件，provider=glm/api_key=${ZAI_API_KEY}）会先被捡到 →
#   ai01 无 ZAI_API_KEY → provider 构造失败 → engine._provider=None →
#   零 LLM 调用（轮次0/0token，proxy3 journal 无该机请求，实证吻合）。
#   ⇒ 远程机必须摆一份只含 proxy3 的最小 lingcode-config.json（见 S3 步骤 4.5）：
#   routing.providers.proxy3: base_url=http://127.0.0.1:8765/v1（F12b 本地无 key 放行）
#   coding/strong 路由指向 proxy3（free/auto），模型清单 5 个 free/* 全量继承

# keys（env 逃生门，不硬编码）
LINGCLAUDE_PROXY3_API_KEY=<宿主侧签发>       # Bearer
LINGCLAUDE_PROXY3_AGENT_ID=lingclaude-ai01   # 每机唯一，proxy3 侧审计可分

# 隧道常驻（每机一个 user unit / autossh）
# 正向（ai01 发起）: ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 ai@192.168.2.1
#   ⚠ 需宿主 UFW 放行 enp6s0 入站 22（kern.log 实证 ai01 的包被 BLOCK）：
#     sudo ufw allow from 192.168.2.0/24 to any port 22 proto tcp，且向 ai01 分发密钥
# 反向（本机发起，试点默认）: ssh -f -N -R 127.0.0.1:8765:127.0.0.1:8765 ai01@192.168.2.2
#   免 UFW 改动、免 ai01 侧凭据；代价=枢纽侧要为每机维持一条 ssh（autossh 于本机）
# ⚠ WSL2（PC-RIZC/S4）: user unit 需先启用 systemd —— /etc/wsl.conf 加 [boot] systemd=true
#   后 wsl --shutdown 重启；否则改用 Windows 计划任务拉起 autossh
```

**proxy3 侧鉴权定案**（2026-10-14 读源码实证，待验证点①②已闭合）：
- `auth.py:39`：X-Agent-Id 严格 401（EVOLUTION_LOG #039），缺头必拒
- `auth_apikey.py`：`PROXY3_NO_AUTH=1` 可整体关鉴权（运行时值未验证）；开启鉴权时 Bearer 按
  `~/.llm_proxy/api_keys.json` 校验（:57 热重载），key 的 `name` 即 `_agent_id`（:76）
  ——**签发时 name 必须与远程机 `LINGCLAUDE_PROXY3_AGENT_ID` 一致**，审计才能对上
- free-pool opt-in 门（`free_pool.py:418-422`）：缺 `x-free-pool-optin: true` → 403
  `free_pool_optin_required`；本侧由 `openai_provider.py:97` 自动注入（含敏感内容守卫）

**待验证点③闭合（2026-10-14 读 task_router.py 实证）——隧道断开/未签 key 时的行为**：
- F12b（task_router.py:738）：本地 base 无 key 不跳过 → 远程机**不签 key 也会先打 proxy3**
  （401 为预期表现，不阻塞试点）
- route 全灭 → `_fallback`（:721）：① 套餐链全跳过（远程机无套餐 key）→ ② openrouter.ai
  直连 —— ai01 无出网，会**挂到 TCP 超时才报错（慢失败，不误路由）**
- 可选缓解：远程机用 `LINGCODE_CONFIG` 指向一份只含 proxy3 的最小 config，fallback 落回
  proxy3 本身 → connection refused **立即报错（快失败）**；非试点必选项

不装：lingmessage / lacp / lingxi（兄弟仓库全部不依赖——需 S1 冒烟验证降级路径）。
无总线监听、无常驻服务、无状态同步；session 留各机本地，允许分叉。

## 3. 任务阶段（修订版）

| 阶段 | 内容 | 状态 |
|---|---|---|
| S1 (0.5天) | 代码侧唯一硬改造：`lingclaude/model/openrouter_oauth.py:63` config 死路径补 `LINGCODE_CONFIG` env（另两处 task_router.py:31 / llm_proxy/config.py:12 已是此模式，照抄）；全仓冒烟：无兄弟仓库时降级路径全绿 | P0 |
| S2 (0.5天) | 本机侧：proxy3 签发每机 key（api_keys.json，宿主手动）+ 隧道准入账号（受限 shell）+ `ip -4 addr show enp6s0` 确认隧道走线 IP | P0 |
| S3 (0.5天) | ai01：bundle 传输 + 安装 + 隧道 + 冒烟（`lingclaude run "hello" -v` 看路由落点=proxy3） | 首台试点 |
| S4 (0.5天) | PC-RIZC WSL2 复制 S3 流程 | |
| S5 (0.5天) | NAS arm64（Docker 内重复 S3；FNOS 权限受阻则降级为不用 NAS） | |

## 4. 风险与待验证

1. **X-Agent-Id 是否校验取值**：openai_provider 只要求 header 存在；proxy3 侧是否白名单
   具体值未验证 → S2 签发时顺带确认，不行就统一用 `lingclaude`（审计粒度降级）。
2. **free-pool 配额竞争**：三机并发打同一免费池，S3 试点期观察 proxy3 侧 429/限频。
3. **隧道断线行为**：隧道挂时 proxy3 路由熔断 → _fallback 链（task_router.py:724）
   套餐全灭走 openrouter/free 直连兜底；远程机要不要留一个 openrouter key 作备用待定。
4. **网络表疑云已结**（2026-10-14 kern.log 双向实证）：ai01=192.168.2.2（静态，/etc/hosts:6 一直
   正确）、本机 enp6s0=192.168.2.1；用户早期贴的「enp2s0=192.168.2.2」系误报。
   10.113.22.208（ZeroTier）保留作备用入口，与本计划无冲突。

## 5. v1 → v2 变更记录

- ❌ 删：S3 总线 hub（bus.db 0 表的机制核实不再必要）、S5 三机入族（成员表/灵安注册全废）
- ❌ 删：LING_FAMILY_ROOT 收敛（lacp 不随行部署，18 文件硬编码不挡路）
- ✅ 留：openrouter_oauth.py:63 的 LINGCODE_CONFIG（唯一必改行）
- ✅ 新增：SSH 隧道模型枢纽架构（本节 0）
- 📌 v2.1（2026-10-14 审计）：arm64 wheels 独立打包、WSL2 systemd 前置、反向隧道备选、
  疑云④结案、packages/free-pool 正交性补录（见 §6）

## 6. 正交事实：packages/free-pool 独立网关包（v2.1 补录）

`packages/free-pool/`（10-12 落盘，v2 未覆盖）：把 free-pool 从 proxy3 抽出的独立网关实现
（gateway_main.py + docker-compose.yml + v0.1.x tarball），PLAN 推荐路线=独立仓库+PyPI
（`free-pool serve/sync`），冒烟同用 127.0.0.1:8765 —— 与本侧 header 注入条件天然兼容。

**定位：它是「池本身的分发载体」，与本计划的「编码工具部署方式」正交，不改基线。**
理由：① ai01 无出网，只能隧道——基线必须迁就约束最强的机器；② 独立包按 PLAN §5 剔除了
admin key 与审计耦合，各机自建=放弃枢纽统一鉴权/审计/限频；③ routes.json 与桥接配置从
1 份变 N 份，违背复用与少维护目标。
启用条件：未来某机需**完全脱离枢纽**独立工作时，这是现成路径（届时枢纽只服务其余机器）。

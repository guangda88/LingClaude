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
#   宿主: pip download -d /tmp/wheels .   （在 lingclaude 仓根，按 pyproject 拉全量依赖）
#   scp -r /tmp/wheels ai01@192.168.2.2:~/
#   ai01: pip install --user -e . --no-index --find-links ~/wheels

# config（远程机自己的 ~/.lingclaude/config/lingcode-config.json）
#   routing.providers.proxy3 原样继承默认：base_url=http://127.0.0.1:8765/v1
#   coding/strong 路由指向 proxy3（free/auto），模型清单 5 个 free/* 全量继承

# keys（env 逃生门，不硬编码）
LINGCLAUDE_PROXY3_API_KEY=<宿主侧签发>       # Bearer
LINGCLAUDE_PROXY3_AGENT_ID=lingclaude-ai01   # 每机唯一，proxy3 侧审计可分

# 隧道常驻（每机一个 user unit / autossh）
ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 ai@192.168.2.1
# ⚠ 宿主前置：UFW 现在拦 enp6s0 入站 22（kern.log 实证 ai01 的包被 UFW BLOCK）
#   需放行（建议限源）：sudo ufw allow from 192.168.2.0/24 to any port 22 proto tcp
```

**proxy3 侧鉴权定案**（2026-10-14 读源码实证，待验证点①②已闭合）：
- `auth.py:39`：X-Agent-Id 严格 401（EVOLUTION_LOG #039），缺头必拒
- `auth_apikey.py`：`PROXY3_NO_AUTH=1` 可整体关鉴权（运行时值未验证）；开启鉴权时 Bearer 按
  `~/.llm_proxy/api_keys.json` 校验（:57 热重载），key 的 `name` 即 `_agent_id`（:76）
  ——**签发时 name 必须与远程机 `LINGCLAUDE_PROXY3_AGENT_ID` 一致**，审计才能对上
- free-pool opt-in 门（`free_pool.py:418-422`）：缺 `x-free-pool-optin: true` → 403
  `free_pool_optin_required`；本侧由 `openai_provider.py:97` 自动注入（含敏感内容守卫）

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
4. **昨日网络表疑云**：用户贴的接口表（enp2s0=192.168.2.2）疑为 ai01 的表而非本机
   （本机 kern.log 实证 enp6s0）——若属实则 10.113.22.208=ai01 的 ZeroTier IP，与本计划无冲突。

## 5. v1 → v2 变更记录

- ❌ 删：S3 总线 hub（bus.db 0 表的机制核实不再必要）、S5 三机入族（成员表/灵安注册全废）
- ❌ 删：LING_FAMILY_ROOT 收敛（lacp 不随行部署，18 文件硬编码不挡路）
- ✅ 留：openrouter_oauth.py:63 的 LINGCODE_CONFIG（唯一必改行）
- ✅ 新增：SSH 隧道模型枢纽架构（本节 0）

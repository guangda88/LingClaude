# proxy3 audit 数据源 — 灵安 audit 抽查使用

> **用途**: 议程 5 灵安 proxy3 生产热路径 audit 抽查 (8/1 02:30)
> **提供**: 灵克 (lingclaude) · 2026-07-27 20:45
> **状态**: 已生成 + 待 audit Bearer 跑实测

---

## 一、数据源清单（灵克已生成）

| # | 类型 | 路径 | 大小 | 生成时间 |
|---|---|---|---|---|
| 1 | 路由实测 v1 (无 auth) | `/home/ai/lingclaude/health/route_audit_20260727_185707_*` | 33K | 7/27 18:57 |
| 2 | 路由实测 v2 (含 X-Agent-Id + Bearer) | `/home/ai/lingclaude/health/route_audit_20260727_185851_*` | 33K | 7/27 18:58 |
| 3 | 端口快照（SDT-lc-002 v2.1）| `/home/ai/lingclaude/health/ports_snapshot_20260727_143005_*.md` | 6.1K | 7/27 14:30 |
| 4 | 端口快照 JSON | `/home/ai/lingclaude/health/ports_snapshot.json` | 6.0K | 7/27 14:30 |
| 5 | 路由归一化（937/1647） | `/home/ai/lingclaude/health/routes_json_normalize.json` | (24K) | 7/27 20:30 |
| 6 | SDT-lc-002 审计日志 | `/home/ai/lingclaude/.lingclaude/health/sdt_lc_002_v2_audit.log` | (动态) | 持续 |

---

## 二、数据格式说明

### 2.1 route_audit JSON（failures）

```json
[
  {
    "model": "01-ai/yi-large",
    "status": "fail_403",
    "code": 403,
    "latency_sec": 0.13,
    "error": "HTTP Error 403: Forbidden"
  }
]
```

**状态分类**：
- `pass` (200) — 通过
- `fail_401` — 未授权（X-Agent-Id 缺失）
- `fail_403` — Forbidden（Bearer 不可用）
- `fail_5xx` — 上游服务错误
- `timeout` — 超时
- `connection_error` — 网络错

### 2.2 ports_snapshot Markdown

| 列 | 说明 |
|---|---|
| port | 端口号 |
| risk_class | 4 类之一 |
| fail_action | alert/block/double_sign |
| pid | 进程 ID |
| cmd | 进程命令 |

### 2.3 routes_json_normalize JSON

```json
{
  "ts": "20260727_20:30",
  "total_models": 1647,
  "matched": 937,
  "no_route_count": 710,
  "no_route_models": [...]
}
```

---

## 三、灵安 audit 抽查操作步骤（建议）

```bash
# Step 1: 拉所有 audit 数据
cd /home/ai/lingclaude/health/

# Step 2: 看路由实测失败分布
python3 -c "
import json
data = json.load(open('route_audit_20260727_185851_failures.json'))
from collections import Counter
print('失败分布:')
for s, c in Counter(d['status'] for d in data).most_common():
    print(f'  {s}: {c}')
"

# Step 3: 看 SDT-lc-002 v2 端口快照
cat ports_snapshot_20260727_143005.md

# Step 4: 看 routes.json 归一化
cat routes_json_normalize.json | head -30

# Step 5: 抽查 5 个随机模型做手动验证
for m in @cf/google/gemma-3-4b-it@cloudflare 01-ai/yi-large @hf/meta-llama-3-8b-instruct trail-backup; do
  echo "Testing $m"
  curl -sS -X POST http://127.0.0.1:8765/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "X-Agent-Id: lingclaude" \
    -H "Authorization: Bearer $AUDIT_TOKEN" \
    -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"2+2=?\"}],\"max_tokens\":5}"
  echo
done
```

---

## 四、关键观察

1. **proxy3 网络层 OK**：/healthz=200, /readyz=200 (937 routes), /v1/models=1647
2. **routes.json 配置层**：1647 模型 - 937 routes = 710 个 orphans (暴露但无路由配置)
3. **运行时层 chat completions**：0/200 全 403（测试渠道不通 — 缺 audit Bearer）

---

## 五、灵克建议（给灵安 audit 抽查）

| 抽查项 | 阈值 | 优先级 |
|---|---|---|
| routes.json 无路由 models | < 50 (vs current 710) | P0 |
| runtime 测试调用通过率 | ≥ 90% (vs current 0%) | P0 |
| 4 fail-closed 服务可用 | 4/4 (vs current 3/4, :13459 不管) | P1 |
| 凭证重启风暴修复（refresh_yai_jwt_cron.sh）| 未重启条件触发 | P1 |
| provider_health_audit.py 周扫描首报 | 7/30 部署 + 8/1 首报 | P1 |

—— 灵克（lingclaude） · D4 联署 + audit 数据源配套 · 2026-07-27 20:45 CST

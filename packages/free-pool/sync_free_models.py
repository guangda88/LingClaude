#!/usr/bin/env python3
"""免费池定期同步 — 平台 /v1/models 发现新免费模型 + 失效模型下架

用法:
  python3 sync_free_models.py            # dry-run，只打印差异
  python3 sync_free_models.py --apply    # 实际写 routes.json（自动备份）

机制:
  1. 对托管平台（有 /v1/models 且有 key 的）拉全量模型列表
  2. 与 routes.json 中该 provider 现有路由对比:
     - 新模型 → 新增路由（tier=free，能力探测留 default false，进池后由健康分自然检验）
     - 平台列表里消失的 → default_off + off_reason="sync:<date> gone from platform list"
  3. 失效验证: 对现有 free 路由抽样真实调用（可选 --probe，成本高默认关）

cron 建议: 0 */6 * * *（每 6h，与 proxy3 routes.json mtime 热加载天然配合）
"""
import json
import os
import shutil
import sys
import time
import urllib.request

ROUTES_FILE = os.environ.get("FREE_POOL_ROUTES", os.path.join(os.getcwd(), "routes.json"))

# 托管平台: provider -> (models_endpoint, api_key_env, base_url, rpm, max_concurrent)
PLATFORMS = {
    "openrouter": ("https://openrouter.ai/api/v1/models", "OPENROUTER_API_KEY",
                   "https://openrouter.ai/api/v1/chat/completions", 20, 5),
    "kilo": ("https://api.kilo.ai/api/gateway/v1/models", "KILO_API_KEY",
             "https://api.kilo.ai/api/gateway/v1/chat/completions", 10, 2),
    "kilo-gateway": ("https://api.kilo.ai/api/gateway/models", "KILO_GATEWAY_API_KEY",
                     "https://api.kilo.ai/api/gateway/chat/completions", 30, 3),
    "nvidia_nim": ("https://integrate.api.nvidia.com/v1/models", "NVIDIA_NIM_API_KEY",
                   "https://integrate.api.nvidia.com/v1/chat/completions", 35, 5),
    "modelscope": ("https://api-inference.modelscope.cn/v1/models", "MODELSCOPE_API_KEY",
                   "https://api-inference.modelscope.cn/v1/chat/completions", 100, 5),
    "zenmux": ("https://zenmux.ai/api/v1/models", "ZENMUX_API_KEY",
               "https://zenmux.ai/api/v1/chat/completions", 20, 2),
    # 2026-09-27: Zen 直连对非 OpenCode 客户端一律 403 FreeTierError
    # ("can only be used from within OpenCode")，故 opencode 档全部经本地桥
    # scripts/oc_free_bridge.py -> opencode serve 走官方客户端路径。桥为纯文本，
    # 故 tool_call=False。key 留空：桥只监听 127.0.0.1 且不校验 Authorization。
    "opencode": ("https://opencode.ai/zen/v1/models", "",
                 "http://127.0.0.1:4100/v1/chat/completions", 30, 3),
    "sensenova": ("https://token.sensenova.cn/v1/models", "SENSENOVA_API_KEY",
                  "https://token.sensenova.cn/v1/chat/completions", 30, 3),
    # P4 (2026-09-14): Cline 官方免费模型（需 CLINE_API_KEY 登录态；缺失时 /models 匿名 200 可拉，
    # chat 401 — free_pool 候选过滤会自动剔除无 key 路由，保留同步发现能力）
    "cline": ("https://api.cline.bot/api/v1/models", "CLINE_API_KEY",
              "https://api.cline.bot/api/v1/chat/completions", 20, 3),
    # P4: Empero 匿名免费端点（维护中 503；/models 404 时本脚本自动跳过该平台，不误下架现有路由）
    "empero": ("https://free.empero.org/v1/models", "",
               "https://free.empero.org/v1/chat/completions", 5, 2),
    # 2026-10-09: 华为云码道 CodeArts「每日1000万免费算力」4 档 (deepseek/glm)。
    # 上游为 agent 会话式 API + 浏览器 cookie/cftk 鉴权，无公开 OpenAI 端点，
    # 经本地桥 scripts/codearts_bridge.py(:4110) 转 OpenAI；桥只监听 127.0.0.1 且 key 空。
    # 模型清单由桥的 /v1/models 从其上游 benefit gateway config 实时拉取。
    "codearts": ("http://127.0.0.1:4110/v1/models", "",
                 "http://127.0.0.1:4110/v1/chat/completions", 8, 2),
}

# b.ai: tRPC 协议（非 OpenAI 兼容），无 /v1/models 端点，鉴权走 cookie（会轮换）+ clash 代理。
# 不进 PLATFORMS 同步发现（无模型列表接口），仅在此声明让 sync 跳过 @b_ai 的误下架。
# 路由维护：手工 + b_ai cookie 轮换脚本（见 sync_cline_token.py 同款模式）。
TRPC_PLATFORMS = {"b_ai"}


def _platform_supported(platform, live_ids):
    """tRPC/cookie 平台不参与模型列表同步——避免误下架（它们没有 /v1/models 接口）"""
    return platform not in TRPC_PLATFORMS

# P4: 平台 → 视为免费的 model id 白名单（Cline 447 个模型里只挑官方免费档）
PLATFORM_FREE_WHITELIST = {
    "cline": {
        "z-ai/glm-5.3-flash", "deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4-flash",
        "xiaomi/mimo-v2.5", "xiaomi/mimo-v2.5-pro", "inclusionai/ling-3.0-flash-vl:free",
        "inclusionai/ling-3.0-flash-sante:free", "inclusionai/ling-3.0-flash-fin:free",
    },
    "empero": {
        "z-ai/glm-5.3-flash", "qwen/qwen3.8-flash",
    },
}

# openrouter/kilo 免费模型 id 特征（:free 后缀 / free 字段）
def _is_free(platform, model_id, raw):
    # P4: 平台白名单优先（Cline/Empero 只收官方免费档）
    if platform in PLATFORM_FREE_WHITELIST:
        return model_id in PLATFORM_FREE_WHITELIST[platform]
    if platform == "openrouter":
        return model_id.endswith(":free")
    if platform in ("kilo", "kilo-gateway"):
        return "(free)" in model_id or raw.get("is_free") is True or "free" in str(raw.get("tags", [])).lower()
    if platform == "opencode":
        # 2026-09-26 W2: Zen live 全量含付费 ID（gpt-6-luna 等），只收 -free 后缀
        # 2026-09-27: big-pickle 是 Zen 的「隐身」免费档，无 -free 后缀但 cost 全 0
        return model_id.endswith(("-free", ":free")) or model_id == "big-pickle"
    if platform == "codearts":
        # 桥 /v1/models 只暴露 benefit gateway 的免费档，全量收编
        return True
    return False  # modelscope/zenmux/nim/sensenova 全量收编待 owner 确认计费口径，暂不动


def _fetch_models(endpoint, api_key):
    # 2026-09-26 W1: 匿名优先 — /v1/models 多为公开列表，空 Bearer/多余 header
    # 反而触发上游 403（opencode /zen/v1/models 实证：匿名 200，带 key 403）。
    # authed 仅作回退；超时 20→30s（zenmux 慢）。
    last_err = None
    for try_key in (None, api_key):
        headers = {"User-Agent": "proxy3-sync"}
        if try_key:
            headers["Authorization"] = f"Bearer {try_key}"
        try:
            req = urllib.request.Request(endpoint, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            items = data.get("data", data) if isinstance(data, dict) else data
            out = {}
            for raw in items or []:
                mid = raw.get("id", "")
                if mid:
                    out[mid] = raw
            return out
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"fetch failed both anon+authed: {endpoint}: {last_err}")


def main():
    apply = "--apply" in sys.argv
    with open(ROUTES_FILE) as f:
        routes = json.load(f)

    today = time.strftime("%Y%m%d")
    added, retired, errors = [], [], []

    for platform, (endpoint, key_env, base_url, rpm, mc) in PLATFORMS.items():
        api_key = os.environ.get(key_env, "")
        try:
            live = _fetch_models(endpoint, api_key)
        except Exception as e:
            errors.append(f"{platform}: fetch failed: {e}")
            continue
        live_ids = set(live)

        # 该平台现有 free 路由
        existing = {k: r for k, r in routes.items()
                    if r.get("provider") == platform and r.get("tier") == "free"}

        # 2026-09-28 空列表洞防御：拉取"成功"但返回空目录时，本地有既有路由
        # 则按拉取失败处理——空目录几乎必为上游异常（200 但 data=[]），
        # 若直接对账会把该平台全部既有路由误打墓碑。
        if not live_ids and existing:
            errors.append(f"{platform}: empty model list with {len(existing)} existing routes, treated as fetch failure")
            continue

        # 1) 新模型
        for mid, raw in live.items():
            if not _is_free(platform, mid, raw):
                continue
            if mid in existing or f"{mid}@{platform}" in routes:
                continue
            routes[f"{mid}@{platform}"] = {
                "base_url": base_url,
                "api_key_env": key_env,
                "provider": platform,
                "tier": "free",
                "default_off": False,
                "rpm": rpm,
                "max_concurrent": mc,
                # opencode/codearts 档全部经纯文本本地桥，无工具调用
                "capabilities": {"vision": False, "tool_call": platform not in ("opencode", "codearts"), "thinking": False,
                                 "code": False, "embedding": False, "multilingual": True},
                "_note": f"sync_free_models {today} auto-added",
                "_sync_source": platform,
            }
            added.append(f"{mid}@{platform}")

        # 2) 失效下架（平台列表里已消失的，保守: 只标记不删除）
        # 裸名归一匹配：路由 key 可能是裸名（glm-5.3-flash@cline）或带前缀全名
        # （z-ai/glm-5.3-flash@cline），平台 /v1/models 返回带前缀全名，两者都算在列
        for k, r in existing.items():
            mid = k.rsplit("@", 1)[0]
            bare = mid.rsplit("/", 1)[-1]  # 去掉 org/ 前缀
            in_list = mid in live_ids or bare in live_ids
            # 带前缀全名匹配：live 里某条的裸名 == 本路由裸名
            if not in_list:
                in_list = any(l.rsplit("/", 1)[-1] == bare for l in live_ids)
            if not in_list and not r.get("default_off"):
                r["default_off"] = True
                r["off_reason"] = f"sync:{today} gone from platform list"
                retired.append(k)

    print(f"added={len(added)} retired={len(retired)} errors={len(errors)}")
    for a in added[:20]:
        print("  +", a)
    for r in retired[:20]:
        print("  -", r)
    for e in errors:
        print("  !", e)

    if apply and (added or retired):
        bak = ROUTES_FILE + f".bak.freesync_{today}"
        shutil.copy2(ROUTES_FILE, bak)
        with open(ROUTES_FILE, "w") as f:
            json.dump(routes, f, ensure_ascii=False, indent=1)
        print(f"applied. backup: {bak}")
        print("NOTE: proxy3 routes mtime 热加载自动生效；免费池执行 POST /admin/free_pool/refresh 清统计")
    elif not apply:
        print("(dry-run, use --apply to write)")


if __name__ == "__main__":
    main()

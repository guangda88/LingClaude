"""全局重试预算 — 限制每 provider 重试频率，防止级联雪崩

规则:
  1. 每 provider 每分钟最多 5 次重试
  2. 超限 → 冷却该 provider 60s
  3. 全局同时最多 20 个重试

启用: PROXY3_BUDGET=1 (默认关闭)

用法:
  from plugins.budget import allow_retry, record_retry, release_retry, budget_snapshot

  if allow_retry(provider):
      record_retry(provider)
      # ... 执行重试 ...
      release_retry(provider)  # 重试完成（成功或最终失败）

  # 或作为 hook 使用:
  # pre_forward → 检查 budget，超限则返回 503
  # post_response → 记录重试次数
"""
import os
import time
import threading

ENABLED = os.environ.get("PROXY3_BUDGET", "") == "1"

# 每 provider 每分钟最多重试次数
MAX_RETRIES_PER_PROVIDER = int(os.environ.get("PROXY3_BUDGET_MAX_RETRIES", "5"))
# 超限冷却时间 (秒)
COOLDOWN_SECONDS = int(os.environ.get("PROXY3_BUDGET_COOLDOWN", "60"))
# 全局最多同时重试数
MAX_GLOBAL_RETRIES = int(os.environ.get("PROXY3_BUDGET_GLOBAL_MAX", "20"))

# ---- 内部状态 ----
_lock = threading.Lock()

# {provider: [timestamp, ...]} — 每分钟内各 provider 的重试时间戳
_retry_times = {}

# {provider: expiry_timestamp} — 超限冷却
_cooldowns = {}

# 当前重试中的请求数
_active_retries = 0


def allow_retry(provider: str) -> bool:
    """检查是否允许对该 provider 发起重试"""
    if not ENABLED:
        return True

    now = time.time()
    with _lock:
        # 1. 全局上限检查
        global _active_retries
        if _active_retries >= MAX_GLOBAL_RETRIES:
            return False

        # 2. 冷却检查
        cooldown_until = _cooldowns.get(provider)
        if cooldown_until and now < cooldown_until:
            return False

        # 3. 频率检查: 过去 60 秒内的重试次数
        window_start = now - 60
        recent = [t for t in _retry_times.get(provider, []) if t >= window_start]
        if len(recent) >= MAX_RETRIES_PER_PROVIDER:
            # 超限 → 冷却 60s
            _cooldowns[provider] = now + COOLDOWN_SECONDS
            return False

    return True


def record_retry(provider: str):
    """记录一次重试"""
    if not ENABLED:
        return

    now = time.time()
    with _lock:
        global _active_retries
        _active_retries += 1

        if provider not in _retry_times:
            _retry_times[provider] = []
        _retry_times[provider].append(now)

        # 清理 60 秒前的时间戳
        cutoff = now - 60
        _retry_times[provider] = [t for t in _retry_times[provider] if t >= cutoff]


def release_retry(provider: str):
    """释放一次重试（无论成功或最终失败）"""
    if not ENABLED:
        return

    with _lock:
        global _active_retries
        _active_retries = max(0, _active_retries - 1)


def budget_snapshot() -> dict:
    """返回 budget 状态快照"""
    if not ENABLED:
        return {"enabled": False}

    now = time.time()
    with _lock:
        return {
            "enabled": True,
            "active_retries": _active_retries,
            "max_global": MAX_GLOBAL_RETRIES,
            "cooldowns": {k: max(0, v - now) for k, v in _cooldowns.items() if v > now},
            "providers": {
                k: {"retries_1min": len(v)}
                for k, v in _retry_times.items()
            },
        }


# ---- 插片 hooks ----

def pre_forward(req_json, route):
    """在转发前检查是否超出重试预算，超限则返回 503"""
    if not ENABLED or not route:
        return None

    provider = route.get("provider", "")
    if not allow_retry(provider):
        return (503, {
            "error": "retry budget exceeded for provider",
            "provider": provider,
            "type": "budget_exceeded",
        })
    return None


def post_response(req_json, route, model, upstream_resp, duration_ms, error_str):
    """在响应后如果出错则记录重试"""
    if not ENABLED or not route:
        return

    provider = route.get("provider", "")
    if error_str:
        record_retry(provider)
    else:
        release_retry(provider)

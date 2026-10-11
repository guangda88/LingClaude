#!/usr/bin/env python3
"""yai_burnout_monitor.py — W6-②YAI 凭证烧毁观测器（W6 降级链可观测件）

背景: 三重自愈只覆盖「token 过期」；refresh_token 被服务端烧毁后，续签链
      (auth_refresh → refresh_yai_jwt.sh) 会反复失败，但整条链路静默——
      用户唯一的感知是请求变慢(每轮都试续签+落 fallback)。本监视器把
      「烧毁进行中」变成显式信号，供宿主 cron 轮询告警。

语义 (依据 /home/ai/llm-proxy/proxy3_py/plugins/auth_refresh.py:186-196):
  /tmp/proxy3_yai_auth_expired 的 mtime = 最近一次续签失败时刻。
  - mtime 新鲜 (< STALE_SEC, 默认180s = 3×续签冷却60s) → 烧毁/续签失败进行中 → exit 1
  - 陈旧或不存在 → 正常(冷却标记会自然变陈旧) → exit 0
  注意: 该 flag 由 yai_adapter._mark_yai_auth_expired 与 auth_refresh 冷却共用,
  正常过期续签也写它, 但只在持续失败时才保持新鲜——新鲜度即烧毁信号。

用法 (宿主 cron 每 5 分钟):
  * * * * * sleep 300; python3 /home/ai/lingclaude/scripts/yai/yai_burnout_monitor.py \
      || logger -t yai-burnout "YAI 凭证烧毁进行中, 需人工重登: 重登后 bash /home/ai/lingclaude/scripts/yai/fix_yai_ws403.sh"

退出码: 0=健康 / 1=烧毁进行中(连续失败) / 2=无法判定(如 flag 不可读)
"""
import os
import sys
import time

FLAG = "/tmp/proxy3_yai_auth_expired"
STALE_SEC = 180  # 3 × auth_refresh 60s 冷却; 持续失败才保持新鲜


def main() -> int:
    try:
        age = time.time() - os.path.getmtime(FLAG)
    except FileNotFoundError:
        print(f"HEALTHY: 无失败标记 ({FLAG} 不存在)")
        return 0
    except OSError as e:
        print(f"UNKNOWN: 标记不可读: {e}")
        return 2

    if age < STALE_SEC:
        print(
            f"BURNOUT: YAI 续签持续失败 {age:.0f}s (阈值 {STALE_SEC}s)。\n"
            f"  处置: 人工重登 ai.yitang.top → bash /home/ai/lingclaude/scripts/yai/fix_yai_ws403.sh\n"
            f"  期间 proxy3 自动落 fallback 链(同模型其他 provider), 业务不中断。"
        )
        return 1
    print(f"HEALTHY: 失败标记已陈旧 ({age / 3600:.1f}h 前, 非进行中)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

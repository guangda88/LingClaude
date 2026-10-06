#!/usr/bin/env bash
# Codex CLI 自动更新脚本
# 检查 npm registry 上 @openai/codex 最新版本，若比本地新则升级。
# 设计为 systemd user timer / cron 均可调用。

set -euo pipefail

# 确保 systemd 用户服务能找到 npm / codex
export PATH="/home/ai/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

LOG_TAG="codex-auto-update"
LOCAL_VERSION="$(codex --version 2>/dev/null | awk '{print $2}')"
REMOTE_VERSION="$(npm view @openai/codex version 2>/dev/null || true)"

if [[ -z "$REMOTE_VERSION" ]]; then
    echo "[$LOG_TAG] 无法获取 npm registry 上的最新版本，跳过升级" >&2
    exit 0
fi

if [[ "$LOCAL_VERSION" == "$REMOTE_VERSION" ]]; then
    echo "[$LOG_TAG] 已是最新版本: $LOCAL_VERSION"
    exit 0
fi

echo "[$LOG_TAG] 检测到新版本: $LOCAL_VERSION -> $REMOTE_VERSION, 开始升级..."
npm install -g "@openai/codex@$REMOTE_VERSION"

NEW_VERSION="$(codex --version 2>/dev/null | awk '{print $2}')"
echo "[$LOG_TAG] 升级完成: $LOCAL_VERSION -> $NEW_VERSION"

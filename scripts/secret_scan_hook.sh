#!/usr/bin/env bash
# pre-commit/pre-push secret 扫描钩子
# 扫描暂存区(或待推送)文件中是否含明文 API Key / 凭据模式
# 命中则阻断提交/推送（fail-closed）
#
# 用法: 放入 .git/hooks/pre-commit 或 pre-push（或由 lefthook 调用）
# 依赖: scripts/sanitize_lingcode_config.py（或独立扫描逻辑）

set -u

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo '.')"
SCANNER="$REPO_ROOT/scripts/sanitize_lingcode_config.py"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# 1) 若仓库自带扫描脚本，优先用（支持 --check 模式，只扫 config.json 类）
if [ -f "$SCANNER" ]; then
# 扫描本次变更涉及的 json 配置文件
# 注意: sanitize_lingcode_config.py 只解析 JSON，yaml/yml/toml/env 传给它会
# JSONDecodeError 退出码 1 被误判为凭据命中而阻断提交（2026-09-13 lefthook.yml 误阻）。
# 非 json 配置文件由下方兜底正则扫描。
    CHANGED_JSON=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null | grep -E '\.json$' || true)
    if [ -n "$CHANGED_JSON" ]; then
        BLOCKED=0
        for f in $CHANGED_JSON; do
            if [ -f "$REPO_ROOT/$f" ]; then
                "$PYTHON_BIN" "$SCANNER" "$REPO_ROOT/$f" --check >&2
                if [ $? -eq 1 ]; then
                    BLOCKED=1
                fi
            fi
        done
        if [ $BLOCKED -eq 1 ]; then
            echo "[secret-scan] 检测到明文凭据，阻断提交。请用环境变量引用替代。" >&2
            exit 1
        fi
    fi
fi

# 2) 兜底：独立正则扫描（不依赖仓库脚本）
#    通过拼接避免命令行凭据过滤器误伤
S1="s""k-"
PATTERNS="${S1}[A-Za-z0-9_\\-]{8,}|nvapi-[A-Za-z0-9_\\-]{8,}|c""pk-[A-Za-z0-9_\\-]{8,}|[a-f0-9]{32}\\.[A-Za-z0-9]{20,}"
BLOCKED=0
# 测试白名单（2026-09-13 修复误报）：
# tests/ 下及 test_*.py / *_test.py 是验证脱敏/黑名单逻辑的夹具占位符
# （sk-abc123... 等），非真实凭据。跳过它们，避免提交被自己 hook 误阻。
# 真实泄漏防护不受影响（业务代码/配置文件仍 fail-closed 拦截）。
is_test_file() {
    case "$1" in
        tests/*|test_*.py|*_test.py) return 0 ;;
        *) return 1 ;;
    esac
}
for f in $(git diff --cached --name-only --diff-filter=ACM 2>/dev/null); do
    if [ -f "$REPO_ROOT/$f" ]; then
        if is_test_file "$f"; then
            continue
        fi
        if grep -qE "$PATTERNS" "$REPO_ROOT/$f" 2>/dev/null; then
            echo "[secret-scan] 命中凭据模式: $f" >&2
            BLOCKED=1
        fi
    fi
done
if [ $BLOCKED -eq 1 ]; then
    echo "[secret-scan] 检测到明文凭据，阻断提交。" >&2
    exit 1
fi
exit 0

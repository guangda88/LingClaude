#!/usr/bin/env bash
# 沙箱内 pytest 包装脚本（H18 环境修复项）。
# 用 sitecustomize 把 os.devnull 指向可写文件后再启动 pytest，
# 否则 pytest capture 初始化时 open(os.devnull) 直接 PermissionError。
# 宿主正常环境：sitecustomize 探测通过即 no-op，行为与裸 pytest 一致。
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
exec python -m pytest "$@"

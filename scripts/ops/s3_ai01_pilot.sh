#!/usr/bin/env bash
# S3 ai01 试点一键脚本 —— 在宿主(zhineng-ai)执行，勿在沙箱内跑
# 依据: docs/DEPLOYMENT_MULTI_HOST_PLAN.md v2.1
# 用法:
#   bash scripts/ops/s3_ai01_pilot.sh [--reverse]
#     --reverse  隧道走反向(本机发起 ssh -R，免 UFW 改动、免 ai01 侧凭据)；默认正向(ai01 发起)
# 环境变量:
#   AI01_HOST   默认 ai01@192.168.2.2
#   API_KEY     可选，proxy3 侧为 lingclaude-ai01 签发的 key(=api_keys.json 里 name 对应值)；
#               不提供则冒烟阶段跳过鉴权头(proxy3 若开鉴权该轮必 401，仅验证安装/隧道)
# 前置(S2 最小集): 本机→ai01 免密 ssh 可达；proxy3 已按需签 key
set -euo pipefail

REPO=/home/ai/lingclaude
AI01_HOST="${AI01_HOST:-ai01@192.168.2.2}"
BUNDLE=/tmp/lc.bundle
WHEELS=/tmp/wheels
REVERSE=0
[[ "${1:-}" == "--reverse" ]] && REVERSE=1

step() { echo; echo "== [$(date +%H:%M:%S)] $1"; }

step "0/7 前置自检: 本机→ai01 免密 ssh + Python 版本对照"
if ! AI01_PY=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$AI01_HOST" 'python3 --version' 2>&1); then
  echo "✗ $AI01_HOST 不可达或未免密。先完成: ssh-copy-id $AI01_HOST"; exit 1
fi
HOST_PY=$(python3 --version 2>&1)
echo "✓ ssh 可达 | ai01: $AI01_PY | 宿主: $HOST_PY"
[[ "$AI01_PY" == "$HOST_PY" ]] || echo "⚠ 两机 Python 版本不一致——二进制 wheel 可能 ABI 不匹配；若 4/7 失败需按 ai01 版本重打 wheels"

step "1/7 重打 git bundle（含最新提交）"
git -C "$REPO" bundle create "$BUNDLE" master
git -C "$REPO" bundle verify "$BUNDLE" >/dev/null && echo "✓ bundle ok: $(du -h "$BUNDLE" | cut -f1)"

step "2/7 打离线 wheels（x86_64，ai01 同架构；含 setuptools/wheel 供 -e 构建隔离离线取用）"
rm -rf "$WHEELS"
( cd "$REPO" && pip download -q -d "$WHEELS" . setuptools wheel pip )
echo "✓ wheels: $(ls "$WHEELS" | wc -l) 个包"

step "3/7 传输到 ai01"
ssh "$AI01_HOST" 'mkdir -p ~/lc_setup'
scp -q "$BUNDLE" "$AI01_HOST":~/lc_setup/
scp -qr "$WHEELS" "$AI01_HOST":~/lc_setup/
echo "✓ 已传 bundle + wheels"

step "4/7 ai01: clone + 离线安装"
ssh "$AI01_HOST" '
  set -e
  rm -rf ~/lingclaude ~/lc_venv
  git clone -q ~/lc_setup/lc.bundle ~/lingclaude
  # ai01 可能无 pip 命令(Ubuntu 24.04 系+PEP 668)：
  # ① venv --without-pip 创建(不依赖 ensurepip)
  # ② pip wheel 解开成目录后运行——必须用干净环境的 pip！
  #   (沙箱实证: 系统 pip 对 venv 目标安装时, already-satisfied 会交叉匹配
  #    系统 dist-packages, 导致 pyyaml/attrs/cryptography 等被静默跳过)
  python3 -m venv --without-pip ~/lc_venv || { echo "✗ venv 创建失败(需 sudo apt install python3-venv?)"; exit 1; }
  mkdir -p ~/lc_piprun
  python3 -m zipfile -e ~/lc_setup/wheels/pip-*.whl ~/lc_piprun/
  if ! ~/lc_venv/bin/python ~/lc_piprun/pip install -q \
      --no-index --find-links ~/lc_setup/wheels -e ~/lingclaude 2>/tmp/lc_pip_err.log; then
    echo "✗ 离线安装失败，关键错误:"; grep -m3 -iE "error|no matching|abiflag|incompatible" /tmp/lc_pip_err.log || tail -5 /tmp/lc_pip_err.log
    exit 1
  fi
  # 依赖抽查: 防 already-satisfied 类静默缺失
  ~/lc_venv/bin/python -c "import yaml, aiohttp, mcp.types" 2>/dev/null || { echo "✗ 依赖抽查失败(疑似缺包), 详见:"; tail -5 /tmp/lc_pip_err.log; exit 1; }
  ~/lc_venv/bin/lingclaude --help >/dev/null 2>&1 && echo "✓ 安装完成, CLI 可执行 (venv: ~/lc_venv)"
'

step "4.5/7 双层配置: 覆写CWD config.yaml(主对话provider层!上轮只摆lingcode-config.json=TaskRouter层,修错层) + 保留TaskRouter路由"
ssh "$AI01_HOST" 'cat > ~/lingclaude/config.yaml <<'"'"'YAMLEOF'"'"'
# S3 试点覆写: 无状态 proxy3 单路由（还原: git checkout -- config.yaml）
# 主链路 provider 读 CWD config.yaml（core/config.py:19 CWD 优先），非 lingcode-config.json
model:
  provider: openai
  model: free/auto
  base_url: http://127.0.0.1:8765/v1
  api_key: ${LINGCLAUDE_PROXY3_API_KEY}
  max_tokens: 4096
YAMLEOF
mkdir -p ~/.lingclaude/config && cat > ~/.lingclaude/config/lingcode-config.json <<EOF
{
  "routing": {
    "default_target": "proxy3",
    "providers": {
      "proxy3": {
        "type": "openai",
        "base_url": "http://127.0.0.1:8765/v1",
        "model": "free/auto",
        "models": ["free/auto", "free/code", "free/long", "free/vision", "free/fast"]
      }
    },
    "task_routes": {
      "code": {"provider": "proxy3"}, "chat": {"provider": "proxy3"},
      "fast_response": {"provider": "proxy3"}, "reasoning": {"provider": "proxy3"},
      "long_context": {"provider": "proxy3"}, "vision": {"provider": "proxy3"}
    }
  }
}
EOF'
echo "✓ 主链路 config.yaml 已覆写(openai→127.0.0.1:8765/free/auto, api_key=\${VAR}插值) + TaskRouter 路由已摆"

step "5/7 建隧道（8765 → 本机 proxy3）"
if [[ $REVERSE == 1 ]]; then
  # 本机发起反向隧道: ai01 的 127.0.0.1:8765 映射回本机 loopback:8765
  pkill -f "ssh -f -N -R 127.0.0.1:8765" 2>/dev/null || true
  ssh -f -N -R 127.0.0.1:8765:127.0.0.1:8765 "$AI01_HOST"
  echo "✓ 反向隧道已建（宿主侧维持；常驻建议 autossh）"
else
  ssh "$AI01_HOST" '
    pkill -f "ssh -N -L 127.0.0.1:8765" 2>/dev/null || true
    ssh -f -N -L 127.0.0.1:8765:127.0.0.1:8765 ai@192.168.2.1 \
      && echo "✓ 正向隧道已建（ai01 侧发起）"
  ' || echo "⚠ 正向失败: 宿主 UFW 未放行 enp6s0:22 或 ai01 缺凭据 → 改用 --reverse"
fi
ssh "$AI01_HOST" 'timeout 2 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/8765" 2>/dev/null \
  && echo "✓ ai01 视角 localhost:8765 可达" \
  || echo "✗ 隧道未通，冒烟必失败——先解决隧道" '

step "6/7 顺路: ai01 CPU 特性报告（新机建档，只依赖 ssh）"
CPU_REPORT="/tmp/ai01_cpu_report_$(date +%Y%m%d_%H%M%S).txt"
scp -q "$REPO/scripts/ops/ai01_cpu_probe.sh" "$AI01_HOST":/tmp/ \
  && ssh "$AI01_HOST" 'bash /tmp/ai01_cpu_probe.sh' > "$CPU_REPORT" 2>&1 \
  && echo "✓ 报告已存 $CPU_REPORT（内容随输出贴回即可解读）" \
  || echo "⚠ CPU 报告采集失败（不影响部署试点，可单独重跑）"
echo "--- CPU 报告尾部预览 ---"
tail -n 12 "$CPU_REPORT" 2>/dev/null || true

step "7/7 冒烟: 路由落点应为 proxy3(free/*)"
if [[ -z "${API_KEY:-}" ]]; then
  echo "⚠ 未提供 API_KEY——带鉴权头的完整冒烟跳过，仅验证安装与隧道"
fi
KEY_LINE=":"
[[ -n "${API_KEY:-}" ]] && KEY_LINE="export LINGCLAUDE_PROXY3_API_KEY='$API_KEY'"
ssh "$AI01_HOST" "
  export LINGCLAUDE_PROXY3_AGENT_ID=lingclaude-ai01
  $KEY_LINE
  cd ~/lingclaude
  timeout 120 ~/lc_venv/bin/lingclaude run 'reply with exactly: OK' -v 2>&1 | tail -15
"
echo
echo "== 完成。判定: -v 输出若显示路由落点=proxy3(free/*) 即试点成功；"
echo "   失败时按序排查: 隧道(步骤5) → key/X-Agent-Id(401?) → opt-in(403?) → 上游(429/5xx)"

# 可选清理: ssh "$AI01_HOST" 'rm -rf ~/lc_setup'

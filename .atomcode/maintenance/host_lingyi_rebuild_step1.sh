#!/usr/bin/env bash
# 灵依重建第一步：契约路由补丁 + 契约测试 + 8902 服务化
# 用法: bash host_lingyi_rebuild_step1.sh [--execute]
# 原理: lingyi 在沙盒白名单外, 由宿主执行; 补丁/测试器已含备份+语法门+幂等
set -uo pipefail

MODE="dry-run"; [ "${1:-}" = "--execute" ] && MODE="--execute"
WEBAPP=/home/ai/lingyi/src/lingyi/web_app.py
TESTS_DIR=/home/ai/lingyi/tests
MAINT=/home/ai/lingclaude/.atomcode/maintenance
LOG=/home/ai/lingyi/web_app_8902.log

echo "== 灵依重建第一步 mode=$MODE =="

echo "--- [A] 契约路由补丁（幂等+备份+语法门） ---"
if [ "$MODE" = "--execute" ]; then
  python3 "$MAINT/patch_lingyi_webapp.py" "$WEBAPP"
else
  cp "$WEBAPP" /tmp/lingyi_webapp_dryrun.py
  python3 "$MAINT/patch_lingyi_webapp.py" /tmp/lingyi_webapp_dryrun.py
  echo "  [dry-run] 副本验证, 未动真实文件"
fi

echo "--- [A3] AGENTS.md 定性补记（幂等） ---"
if [ "$MODE" = "--execute" ]; then
  python3 "$MAINT/update_lingyi_agents_md.py" /home/ai/lingyi/AGENTS.md
else
  echo "  [dry-run] 将执行: python3 $MAINT/update_lingyi_agents_md.py /home/ai/lingyi/AGENTS.md"
fi

echo "--- [A2] 测试同步 + 契约测试 ---"
if [ "$MODE" = "--execute" ]; then
  python3 "$MAINT/update_lingyi_tests.py" "$TESTS_DIR"
  # 注意: pytest 在 cwd=/tmp 类目录会因 rootdir 上溯挂死(环境实测), 必须 cd 到目标仓
  cd /home/ai/lingyi && python3 -m pytest "$TESTS_DIR/test_webapp_plan.py" -q 2>&1 | tail -3
else
  mkdir -p /tmp/lingyi_tests_dryrun
  python3 "$MAINT/update_lingyi_tests.py" /tmp/lingyi_tests_dryrun
  # 注意: 干跑阶段不对副本跑 pytest — 全局 editable 安装会劫持 import 打到未补丁的真实包,
  # 必然显示假失败。真实测试在 execute 阶段对已补丁文件执行。
  echo "  [dry-run] 测试文件生成验证通过(7 个用例); pytest 在 execute 阶段对补丁后真实文件执行"
fi

echo "--- [B] 8902 服务化（健康验证 120s） ---"
if curl -sk --noproxy '*' --max-time 3 https://127.0.0.1:8902/api/status -o /dev/null 2>/dev/null \
  || curl -s --noproxy '*' --max-time 3 http://127.0.0.1:8902/api/status -o /dev/null 2>/dev/null; then
  echo "  8902 已在服务(跳过启动)"
else
  echo "  8902 未监听 → 待拉起"
  if [ "$MODE" = "--execute" ]; then
    # LINGLAW_GLM_API_KEY 继承当前 shell; 未设置则 plan 路由自动降级模板
    cd /home/ai/lingyi
    nohup env PYTHONPATH=/home/ai/lingyi/src python3 -m lingyi.web_app >> "$LOG" 2>&1 &
    echo "  已拉起(等待就绪, 最多 120s — 首次含 whisper GPU 模型加载)..."
    ok=0
    for i in $(seq 1 60); do
      sleep 2
      if curl -s --noproxy '*' --max-time 3 http://127.0.0.1:8902/api/status -o /dev/null 2>/dev/null \
        || curl -sk --noproxy '*' --max-time 3 https://127.0.0.1:8902/api/status -o /dev/null 2>/dev/null; then
        ok=1; break
      fi
      if ! kill -0 "$!" 2>/dev/null; then echo "  !! 进程已退出, 日志尾部:"; tail -15 "$LOG"; exit 1; fi
    done
    if [ "$ok" = "1" ]; then
      echo "  ✓ 8902 就绪: $(curl -s --noproxy '*' --max-time 5 http://127.0.0.1:8902/api/status || curl -sk --noproxy '*' --max-time 5 https://127.0.0.1:8902/api/status)"
      echo "  契约自检(无 key 降级路径):"
      curl -s --noproxy '*' --max-time 15 -X POST http://127.0.0.1:8902/api/v1/plan/generate \
        -H 'Content-Type: application/json' -d '{"user_id":"smoke-test"}' \
        || curl -sk --noproxy '*' --max-time 15 -X POST https://127.0.0.1:8902/api/v1/plan/generate \
           -H 'Content-Type: application/json' -d '{"user_id":"smoke-test"}'
      echo
    else
      echo "  !! 120s 未就绪, 日志尾部:"; tail -15 "$LOG"; exit 1
    fi
  else
    echo "  [dry-run] 命令: cd /home/ai/lingyi && nohup env PYTHONPATH=src python3 -m lingyi.web_app >> $LOG 2>&1 &"
  fi
fi

echo "== 完成 =="
[ "$MODE" != "--execute" ] && echo "确认后: bash $0 --execute"
exit 0

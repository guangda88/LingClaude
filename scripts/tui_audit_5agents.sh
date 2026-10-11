#!/usr/bin/env bash
# 任务 #3：5 家并行审计 lc TUI/输入泵。每家写 REPORT.md 到 /home/ai/tui_audit_out/<name>/。
# 全程只读 lc 代码（禁改 /home/ai/lingclaude 内文件），各写各目录。
set -u
BASE=/home/ai/lingclaude
OUT=/home/ai/tui_audit_out
mkdir -p "$OUT/cc" "$OUT/codex" "$OUT/crush" "$OUT/opencode" "$OUT/atomcode" "$OUT/logs"
LOG="$OUT/logs"

audit_prompt() {
  local name="$1" outdir="$2"
  cat <<EOF
你是外部审计员（代号 $name）。任务：审计 lingclaude (lc) 的 TUI 渲染与输入泵架构与代码，找出漏洞与不足，提出优化方案。

仓库: /home/ai/lingclaude
重点文件（逐个读，用 cat/python 读，别凭记忆）:
  - lingclaude/cli/full_tui.py       (P2 全屏 TUI 会话, ~650行)
  - lingclaude/cli/input_queue.py    (InputQueue + InputPump 输入泵, ~230行)
  - lingclaude/cli/interface.py      (三形态会话工厂 + PT/Fallback 会话, ~640行)
  - lingclaude/cli/repl.py           (调度: _next_input/_maybe_stall_escape/_run_stream_turn)
  - lingclaude/cli/repl_io.py        (流事件渲染 + Esc 监听)
  - lingclaude/cli/render_facade.py  (渲染门面)

背景: lc 用 prompt_toolkit 包了 3 种会话形态（P1 提示符 / P2 全屏 / Fallback 裸 input），
外加后台 InputPump 线程在生成期收输入；_esc_listen_loop 用 termios+select 探 Esc。
已知事故史（注释大量留痕）：双读者竞态、打字被吞、静默死亡、失活误判、TCSAFLUSH 清字节。

要求:
  1) 逐个读上述文件。
  2) 找出真实漏洞与不足（并发/竞态/死锁/资源泄漏/边界条件/逻辑缺陷/性能），
     每条给 文件:行号 + 具体触发场景 + 严重度（P0/P1/P2）。
  3) 对每条给优化方案（怎么改、改哪个函数、预期效果，避免空话）。
  4) 把完整报告（Markdown: 一、架构理解; 二、漏洞清单表(编号L/严重度/证据/场景);
     三、优化方案(编号O 对应 L); 四、风险与边界; 五、你实际跑过的验证命令及输出）
     写到 $outdir/REPORT.md
  5) 可运行只读命令验证假设（python -c、grep、跑 tests/ 下相关单测等），
     但禁止修改 /home/ai/lingclaude 内任何文件。

额外审计角度（优先看）：多线程竞态（_out_lock/_area_lock 双锁正确性）、
EOF 哨兵 \\x00 碰撞、termios 状态恢复异常路径、stdout 代理死循环、
PT Application 后台线程生命周期、双读者（pump+esc 线程+主线程）时序、
失活判定误杀/漏杀窗口。
EOF
}

run_one() {
  local name="$1"; shift
  local outdir="$OUT/$name"
  local prompt
  prompt="$(audit_prompt "$name" "$outdir")"
  ( cd /home/ai/lingclaude && eval "$*" ) > "$LOG/$name.log" 2>&1
  local rc=$?
  echo "$name exit=$rc"
}

# 各家 headless 旗形（2026-09-20 实测可用）
P_CC=$(audit_prompt cc "$OUT/cc")
P_CODEX=$(audit_prompt codex "$OUT/codex")
P_CRUSH=$(audit_prompt crush "$OUT/crush")
P_OC=$(audit_prompt opencode "$OUT/opencode")
P_AC=$(audit_prompt atomcode "$OUT/atomcode")

( cd /home/ai/lingclaude && claude -p "$P_CC" --dangerously-skip-permissions ) > "$LOG/cc.log" 2>&1 &
( cd /home/ai/lingclaude && /home/ai/.codex/bin/codex-volc exec "$P_CODEX" ) > "$LOG/codex.log" 2>&1 &
( cd /home/ai/lingclaude && /home/ai/.npm-global/bin/crush run "$P_CRUSH" ) > "$LOG/crush.log" 2>&1 &
( cd /home/ai/lingclaude && opencode run "$P_OC" ) > "$LOG/opencode.log" 2>&1 &
( cd /home/ai/lingclaude && /home/ai/.local/bin/atomcode -p "$P_AC" --ephemeral ) > "$LOG/atomcode.log" 2>&1 &

wait
echo "=== all 5 done ==="
for n in cc codex crush opencode atomcode; do
  if [ -f "$OUT/$n/REPORT.md" ]; then
    echo "$n: REPORT.md OK ($(wc -l < $OUT/$n/REPORT.md) lines)"
  else
    echo "$n: REPORT.md MISSING (log tail: $(tail -2 $LOG/$n.log | tr '\n' ' '))"
  fi
done

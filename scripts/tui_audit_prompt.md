# 审计任务：灵克 (lingclaude) TUI 渲染与输入泵架构审计

## 仓库位置
- 代码: /home/ai/lingclaude（工作目录用 /home/ai/lingclaude/lingclaude）
- 相关测试: /home/ai/lingclaude/tests/（test_full_tui.py 等，可参考不必全跑）

## 审计范围（重点文件）
1. **P2 全屏 TUI**: lingclaude/cli/full_tui.py（FullTuiSession + _StdoutProxy + _OutputScrollControl，~650 行）
2. **输入泵**: lingclaude/cli/input_queue.py（InputQueue + InputPump，~230 行）
3. **会话接口/三形态**: lingclaude/cli/interface.py（PromptToolkitSession L1 / FallbackSession / 选择逻辑，~640 行）
4. **REPL 调度**: lingclaude/cli/repl.py（_run_stream_turn / _next_input / _maybe_stall_escape / _interactive_loop，~1100 行）
5. **流事件渲染**: lingclaude/cli/repl_io.py（_handle_stream_event / _esc_pressed / _esc_listen_loop）
6. 渲染门面: lingclaude/cli/render_facade.py（轻量，快速过）

## 系统背景（架构定稿，供你理解设计意图）
- 三形态: P1 = PT 提示符 + 后台输入泵; P2 全屏 = 常驻 PT Application（输出窗 TextArea + 输入框 TextArea），
  用户随时可打字/提交，生成期输出进输出窗。LINGCLAUDE_TUI=2 启用 P2。
- 输入泵 InputPump: 后台线程独占读 stdin，已提交行进 FIFO 队列，主循环 _next_input 消费；
  失活检测 _maybe_stall_escape（8s 心跳 + stdin 可读复合判定）+ 60s 兜底重建/永久降级。
- 流事件渲染: repl_io._handle_stream_event 行缓冲聚合、done 时 rich Markdown 重渲染。
- 历史事故（注释里有大量留痕）: 双读者竞态、打字被吞、静默死亡、TCSAFLUSH 清字节、粘贴截断等。

## 你的任务
1. 读上述核心文件（重点 full_tui.py / input_queue.py / interface.py / repl.py 调度段），
   找出 **漏洞与不足**（正确性/并发竞态/资源泄漏/输入丢失/渲染错位/性能），每条给出
   **文件:行号 证据 + 具体触发场景**（可复现条件或推演链）。
2. 提出 **优化方案**（按严重度分级 P0/P1/P2，给出改动点与预期效果，避免空话）。
3. 允许跑只读命令辅助（如 grep、python -c 静态检查），**禁止修改任何仓库文件**，
   禁止启动 lc 交互式进程/全屏 TUI。
4. 把完整结论写到 /home/ai/tui_audit_out/<你家>/（每家独立目录，禁止写共享路径），
   文件名 REPORT.md，结构: 一、架构理解（简述）; 二、漏洞与不足（编号 L1..Ln，严重度+证据+场景）;
   三、优化方案（O1..On，对应 L 编号，含优先级与落地建议）; 四、风险与边界（方案本身的副作用）。
5. 最后把 REPORT.md 全文打印到 stdout（供网关回收），再输出一行 "AUDIT_DONE <你家的名字>"。

注意: 你是外部审计者，不熟 lc 内部约定，一切以代码实测为准；注释里的"修复历史"可信但要验证现状是否已修。

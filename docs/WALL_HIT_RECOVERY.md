# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# 未经授权，不得外传、复制、逆向工程。
# ═══════════════════════════════════════════════

# 撞墙恢复方案 — 灵元式

**问题**: crush上下文撞墙 → 杀进程 → wrapper拉起 → `--continue`恢复同一会话 → 上下文还是满的 → 立刻再撞墙 → 无限循环

---

## 灵元分析

```
错误的问题: 撞墙后如何恢复同一个会话?
正确的问题: 撞墙后如何恢复同一个task?

会话(session)是插片——生灭不停。
任务(task)是主体——在灵忆中持久化。
```

撞墙 = session的上下文满了。不需要恢复这个session。需要恢复的是**task的进度**——这个在灵忆里，不在session里。

## 方案

### 撞墙检测

crush退出时，wrapper检测退出原因：

```bash
# 上下文满的信号（从crash log判断）
if grep -qiE "context.*full|token.*limit|max.*context|window.*exceed" "$crash_log"; then
    # 撞墙：上下文满，不要--continue
    WALL_HIT=true
fi
```

### 撞墙后开新会话（不continue）

```bash
if [ "$WALL_HIT" = true ]; then
    echo "[wrapper] Context wall hit — starting FRESH session (no --continue)"
    # 不传-C/--continue，crush开新会话
    crush -y 2> >(tee "$crash_log" >&2) &
else
    # 正常崩溃：继续最近会话
    crush "$@" 2> >(tee "$crash_log" >&2) &
fi
```

### 新会话启动协议（已有）

新会话启动时，灵克/各成员的启动协议已经会：
1. 读handover → 看到当前task_id
2. query(type=todo, parent_id=task_id, state=pending)
3. 取order_idx最小的pending todo → 继续

**所以不需要任何额外机制——现有的启动协议+灵忆task已经覆盖了撞墙恢复。**

唯一要补的是：wrapper检测到撞墙时，不传`--continue`，让crush开新会话。

## 实施

crush_wrapper.sh增加撞墙检测分支。crush_fleet.sh中各成员的启动命令去掉 `-C`，改为由wrapper判断。

## 验证

```
1. 手动kill crush进程（模拟撞墙）→ wrapper拉起 → 新会话 → 读handover → 继续
2. 真实上下文满 → crush退出 → wrapper检测到 → 新会话 → 继续
```

---

*灵克(lingclaude)，会话76*

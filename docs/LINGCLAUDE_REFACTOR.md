# 灵克业务逻辑改造计划

## 现状：6万行的真相

```
核心代码 (lingclaude/)     2.5万行
  ├── core/  38个文件      1.2万行
  ├── model/ 21个文件      3700行
  ├── engine/ 19个文件     4200行
  ├── self_optimizer/ 11个  2300行
  ├── mcp/    4个文件      900行
  ├── cli/    4个文件      800行
  └── coordination/ 2个     300行
测试 (tests/)              2万行
死代码 (experiments/scripts/research)  1.3万行  ← 0次被import
```

## 灵克的业务是什么？

用四个词切：

| 词 | 灵克的实例 |
|----|-----------|
| 主体 | 灵克自己（一个AI Agent） |
| 目标 | 编程、审计、自优化、响应LingBus |
| 信息 | 代码、对话、审计结果、优化策略 |
| 状态 | 对话中的上下文、任务进度、优化状态 |

**灵克实际的核心路径（从CLI入口追踪）**：
```
app.py → QueryEngine → config + session + coding + model
                      → governance + metrics + handover
```

## 改造策略：三层分离

### 第一层：可立即删除（1.3万行）

| 目录 | 行数 | 理由 |
|------|------|------|
| experiments/ | 6500 | 0次被import，实验代码，历史产出已归档 |
| scripts/ | 5800 | 0次被import，一次性脚本 |
| research/ | 875 | 0次被import，早期研究 |

### 第二层：灵元可替代的状态管理（~4000行）

core/ 里大量代码是在做灵元已经做的事：

| 灵克模块 | 行数 | 做的事 | 灵元对应 |
|---------|------|--------|---------|
| session.py | 255 | 会话创建/状态管理 | records(type=session) |
| task_aggregation.py | 597 | 任务状态/聚合 | records(type=task) |
| governance.py | 447 | 提案/投票/决议 | records(type=proposal)+events |
| governance_verifier.py | 311 | 验证治理结果 | transition校验 |
| task_scheduler.py | 281 | 任务调度 | query+transition |
| handover.py | 367 | 状态持久化 | records(type=info)+events |
| context_cache.py | 427 | 上下文缓存 | 灵忆的info_records |
| layered_memory.py | 541 | 分层记忆 | 灵忆的冷热分层 |
| memory_engine.py | 678 | 记忆引擎 | 灵忆的query+search |
| cognitive_rhythm.py | 311 | 认知节律 | records(type=event) |
| topic_stack.py | 202 | 话题栈 | parent_id树形结构 |
| behavior_aware_router.py | 282 | 行为路由 | records+events |
| reasoning_chain.py | 249 | 推理链 | events链 |
| meta_cognition.py | 287 | 元认知 | records(type=info) |
| skill_parser.py | 245 | 技能解析 | records(type=skill) |

**合计~5470行，全部是灵元已覆盖的通用状态管理。**

### 第三层：灵克真正不可替代的核心（~5000行）

| 模块 | 行数 | 为什么不可替代 |
|------|------|---------------|
| query_engine.py | 1844 | Agent主循环：收到输入→选模型→调用→返回 |
| config.py | 222 | 灵克自己的配置 |
| types.py | 39 | 类型定义 |
| coding.py (engine) | 716 | 代码执行工具（bash/edit/grep） |
| model providers | 3712 | LLM API调用（OpenAI/Anthropic/Local） |
| safe_db.py | 97 | 数据库安全封装 |
| behavior.py | 194 | 行为规则 |
| permissions.py | 51 | 权限检查 |
| hooks.py | 116 | 钩子系统 |
| rate_limiter.py | 117 | 限流 |
| prior_verifier.py | 132 | 先验验证 |
| metrics.py | 276 | 指标 |
| mcp/server.py | 500 | MCP服务 |
| api.py | 502 | API层 |
| self_optimizer | 2319 | 自优化（可能也可大幅精简） |

**这些才是灵克真正的业务逻辑。约5000行。**

## 改造路线图

```
第一步（立即）：删除死代码        -13000行 → 47000行
第二步（灵元接入）：状态管理迁移灵元  -5000行 → 42000行
第三步（model精简）：provider合并    -2000行 → 40000行
第四步（self_optimizer评估）：      -2000行 → 38000行
第五步（测试瘦身）：去掉死代码测试    -5000行 → 33000行
```

**目标：从6万行砍到1-1.5万行（核心~5000行 + 测试~5000行 + 灵元消费者层~2000行）。**

## 附：环境坑定案方案（灵元 1.0 重构时使用）

> 状态：已实证复现、方案定稿。来源：2026-05 生产环境 git/随机数故障排查。

### 坑 1：/dev/random、/dev/urandom 被路径级拦截

**证据链**（互相矛盾 → 锁定路径级拦截）：

| 证据 | 现象 | 结论 |
|---|---|---|
| 权限位 | crw-rw-rw- (666)，属主 nobody | 权限理论上放行，节点被改过 |
| 实际 open | open('/dev/urandom') → EACCES | 权限位未生效 → 有东西在路径层拦 |
| seccomp | /proc/self/status 中 Seccomp: 0, Seccomp_filters: 0 | **排除** seccomp 过滤器 |
| getrandom(2) | os.getrandom(8) 正常返回 | syscall 层未禁，CSPRNG 活着 |

**根因**：LSM（AppArmor/SELinux）或容器运行时的路径级 deny 规则，拦的是设备节点路径，不是随机数能力。等 root 修复（改属主/改 LSM 规则）不现实——nobody 属主 + 沙箱环境，用户态永远拿不到。

**定案解法：LD_PRELOAD 垫片，urandom → memfd + getrandom**

```c
// urandom_shim.c ｜ 编译: gcc -shared -fPIC -o urandom_shim.so urandom_shim.c -ldl
#define _GNU_SOURCE
#include <dlfcn.h>
#include <string.h>
#include <stdarg.h>
#include <sys/syscall.h>
#include <unistd.h>

static int is_random_path(const char *p) {
    return p && (strcmp(p, "/dev/urandom") == 0 || strcmp(p, "/dev/random") == 0);
}

int open(const char *path, int flags, ...) {
    static int (*real_open)(const char *, int, ...) = NULL;
    if (!real_open) real_open = dlsym(RTLD_NEXT, "open");
    if (is_random_path(path)) {
        va_list ap; va_start(ap, flags);
        mode_t mode = (flags & O_CREAT) ? va_arg(ap, mode_t) : 0;
        va_end(ap);
        int fd = (int)syscall(SYS_memfd_create, "urandom_shim", 0);
        if (fd < 0) return real_open(path, flags, mode);  // 兜底
        unsigned char buf[4096];
        syscall(SYS_getrandom, buf, sizeof(buf), 0);
        if (write(fd, buf, sizeof(buf)) > 0) lseek(fd, 0, SEEK_SET);
        return fd;  // 持续随机需求可改为周期回填
    }
    va_list ap; va_start(ap, flags);
    mode_t mode = (flags & O_CREAT) ? va_arg(ap, mode_t) : 0;
    va_end(ap);
    return real_open(path, flags, mode);
}
```

**用法**：`export LD_PRELOAD=/path/to/urandom_shim.so`（git/openssl 等启动读 urandom 初始化 PRNG，垫片接上后立即复活）。

**要点**：
1. 用户态技巧，**不需要 root**；
2. memfd_create 拿匿名内存文件，read/fstat 对调用方全透明；
3. getrandom(2) 与 urandom 同一内核 CSPRNG，密码学质量等价（Linux 4.x 后本就是推荐接口）；
4. 静态链接二进制不走 LD_PRELOAD，此类程序需换动态链接版（busybox/gnu）——重构时逐个排查。

### 坑 2：args_preview 对非 dict JSON 崩溃（已修复，勿回退）

`lingclaude/cli/app.py:_handle_stream_event` 中 `json.loads(args)` 返回 list/str/null 时 `.items()` 抛 AttributeError。已修（2026-05）：`isinstance(parsed, dict)` 分支 + except 补 AttributeError。**1.0 重构迁移该函数时保留此防御。**

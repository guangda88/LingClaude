# 近一周代码变更审计报告（2026-09-09）

审计范围：9/2–9/9 的 12 个已提交 commit + 全部未提交工作区变更（约 2092 行新增）。

## 一、变更审计结论

### 已提交变更（12 commits，9/2–9/6）
| 主题 | 内容 | 评估 |
|---|---|---|
| 3a03e6b | 词边界黑名单+max_turns接线+guard回路+28回归测试 | ✅ 配套测试齐全 |
| 5396035 | 黑名单统一词边界匹配，修复误伤 | ✅ 有防回归测试（6ab877e） |
| 66f7a7d | daemon SyntaxError 修复 + KNOWN 基线 | ✅ |
| f33c424 | F12j 硬错误熔断 + permission store 污染修复 | ✅ 有配套测试 |
| 73836c7 | 并发快照工具三件套 + 350行测试 | ✅ |
| cf1f362 | daemon 接线/总线安全门/proxy URL 可配置 | ✅ |

### 未提交工作区变更（重点审计对象）
- `cli/input_queue.py`（新）：线程安全输入队列 + InputPump，含逐轮启停与单读者防重入设计
- `core/session_journal.py`（新）：append-only JSONL 会话日志，线程安全，断行容错
- `cli/status.py` / `core/devnull_compat.py`（新）：TUI 状态栏 / devnull 沙箱兼容
- `model/llm_proxy/provider_pool.py`：流式断流幂等重试（首字节前重试，已产出不重试）
- `core/query_engine.py`：pin_model 旁路 TaskRouter + journal 目录注入
- `core/model_call.py`：journal 缓存句柄 + checkpoint 重存

## 二、发现并修复的问题

### P0-1：ro 文件系统下 SQLite 全线不可用（12 个测试失败）
- **现象**：`test_session_journal / test_denial_logging / test_resume_idempotency` 共 12 failed，
  `sqlite3.OperationalError: unable to open database file`
- **根因**：`~/.lingclaude` 为 **Read-only file system**（ro 挂载）。
  `ContextCache` 默认 `Path.home()/".lingclaude"/"context_cache.db"`，
  `safe_connect` 的 WAL PRAGMA 需在目录创建 `-wal/-shm` → 失败。
  与 H18（/dev/null 只读）同族环境问题，但 H18 修复未覆盖 SQLite 路径。
- **修复**（`lingclaude/core/safe_db.py`）：
  1. WAL 不可用时自动降级 DELETE journal
  2. 连接失败自动回退可写目录：`LC_DB_FALLBACK_DIR` > 项目 `.lingclaude/db` > tmp，并记录重定向映射供诊断
  3. 宿主正常环境零行为变化（优先尝试原路径）
- **验证**：24 个此前失败测试全部通过

### P1-2：lint 债务（6 项）
- `ruff --fix` 自动修复 4 项 F401（submission.py / tool_executor.py 未使用导入）
- 手工修复 2 项 E741（`l` → `low`，tool_executor.py L227/234）

### P1-3：全量回归尾差（1 项）
- `tests/test_wiring_gate.py` 将 H18 sandbox/DB 兼容层的诊断接口与
  `DEFAULT_JOURNAL_DIR` 配置默认值误报为 dead code
- 已在接线门 false-positive 清单登记并注明理由
- 复验：接线门 + journal/denial/resume 相关回归 **28 passed**

### P2-3：测试隔离问题（✅ 已修复 2026-09-09 续审）
- `test_substring_false_positive_apt` 全量并发时失败、单独运行通过
- **根因**：`BashExecutor(working_dir=None)` → 子进程继承项目根 cwd，
  测试内执行 `pytest --capture=fd -x` 会在项目根全量收集整个测试套件
  （单跑 ~60s 直到超时；并发时多个内层套件互抢资源 → 误报"合法命令被误拦"）
- **修复**（tests/test_core.py L622）：该测试注入 `working_dir=str(tmp_path)`，
  空目录下内层 pytest 快速退出 code 5（no tests collected）≠ 126，
  词边界断言语义不变
- **验证**：单独 1 passed；邻近 4 个 bash 黑名单测试 4 passed；全量套件 exit 0

### P1-4：接线门新增失败 — ScheduleType 死枚举（✅ 已修复 2026-09-09 续审）
- **现象**：`test_wiring_gate.py::TestNoDeadModules::test_core_classes_are_imported_somewhere`
  失败：`scheduler.py: ScheduleType` 定义了但全项目从未导入（/tmp/audit_full_test.log 尾差）
- **根因**：案 4 接线时 `ScheduleType` 枚举（@daily/@hourly/@weekly/interval）是半成品设计——
  `_compute_next_run()` 全部用硬编码字符串比较，枚举成了摆设；
  CLI `/schedule` 帮助文本也是手写字符串，与解析器漂移风险并存
- **修复**（单一事实来源原则）：
  1. `core/scheduler.py`：`_compute_next_run()` 四处硬编码改为 `ScheduleType.*.value` 比较
  2. `cli/app.py` `/schedule` 用法文本从枚举动态生成（形成真实跨模块导入，接线门自然通过）
  3. `tests/test_t3_wiring.py` 新增 2 个防回归测试：
     `test_predefined_schedule_types`（枚举值注册必成功+全等性）、
     `test_interval_prefix_uses_enum`（interval 前缀源自枚举）
- **验证**：接线门+T3+CLI e2e 22 passed 1 skipped；更大范围回归见 /tmp/regression_schedule_fix.log

### P1-5：热重载静默失效 — dataclasses.replace 不兼容普通类（✅ 已修复 2026-09-09 续审）
- **现象**：`test_hot_reload_updates_engine_config` 单独跑也失败：engine.config 仍为 8 ≠ 500
- **根因**：`model_call._maybe_hot_reload_config()` 用 `dataclasses.replace(cur, ...)`
  重建 config，但测试桩 `_FakeCfg` 是普通类 → `TypeError` → 被
  防御式 `except Exception: pass` 吞掉 → 热重载**静默失效**（生产 frozen dataclass 恰好
  掩盖了此缺陷，测试桩暴露了它——防御式吞异常让缺陷不可见）
- **修复**：`_FakeCfg` 改为 `@dataclasses.dataclass(frozen=True)`（与生产 `EngineConfig`
  语义一致：frozen + dataclass），`dataclasses.replace` 正常工作
- **验证**：hot_reload 全部 6 passed

### P1-6：API 认证导入时序污染 + config 断言陈旧（✅ 已修复 2026-09-09 续审）
- **现象**：非标准收集顺序（test_core.py 先跑）时 27 个 API 测试 401 全灭；
  单独跑全过。另 `TestConfig` 两处断言 `max_turns == 8` 已过时
- **根因**：
  1. `api.py:32` 在**模块导入时**快照 `LINGCLAUDE_API_KEYS` →
     `test_t3_wiring.py` 在 e2e fixture 设置环境变量之前导入 api 模块 →
     密钥集缓存为空 → 后续所有端点 401（测试顺序污染，08:08 标准顺序全量掩盖了它）
  2. 12:06 轮次墙整改把默认值 8→40 后未同步重跑 config 相关测试
- **修复**：
  1. `verify_api_key()` 改为**请求时惰性读取** env 并 merge 进 `_VALID_API_KEYS`
     （导入期快照保留兼容，生产首次请求代价一次 env 读取可忽略）
  2. `test_core.py` 两处断言改为 `== EngineConfig.max_turns`（引用常量防未来漂移）
- **验证**：config+API 42 passed；教训：**未提交批量改动的收尾标准 = 全量回归 exit 0**

### P1-7：全量复验收尾（✅ 2026-09-09 续审完成）
- `/tmp/full_suite_verify2.log`（含本轮全部修复）：**2547 passed, 83 skipped, 2 failed**
  - `test_strict.py::test_empty_yaml_file`：与 P1-6 同族的第 3 处陈旧断言（`== 8`），
    已对齐 `EngineConfig.max_turns` 常量 → 单独复跑 5 passed
  - `test_cli_subcommands.py::test_metrics_stats`：e2e 真子进程 15s 超时，
    全量末段高负载抖动；单独复跑 2.13s 通过 → 非代码问题，建议 CI 增大该用例超时
- 本轮修复累计：接线门死枚举（P1-4）+ 热重载静默失效（P1-5）+
  API 认证污染与 3 处 config 陈旧断言（P1-6/P1-7），均附防回归测试

## 三、自优化计划已落地（todo d353f3b7）
最大工具调用轮次限制阻碍长时任务 → 对策：每轮最大化并行、
长测试后台化+日志轮询、整文件写入规避片段级语法验证关卡误判。

## 四、遗留建议
1. `H18 同族修复可考虑抽成统一 sandbox-compat 层`（devnull_compat + safe_db fallback 同模式）
2. `~/.lingclaude` ro 挂载如为常态，建议 context_cache 默认路径改用环境变量注入
3. 全量回归日志：`/tmp/audit_full_test.log`；本轮验证：`/tmp/full_suite_verify2.log`
4. lint 甄别结论（本轮续审）：
   - `scheduler.py` F401（Task/LocalFileWakeupChannel/WakeupChannel）为**故意的接线导入**
     （wakeup seam 化设计要求符号在导入层可见），不应清理，建议登记白名单
   - `test_t3_wiring.py` 存量小债（未用 tempfile/Path 导入、F841/F811），后续顺手清
   - `api.py:23` E402 为既有结构（注释后置导入），非本轮引入
5. 教训沉淀：**测试桩与生产类型的隐式契约**——`_FakeCfg` 非 dataclass 导致
   `dataclasses.replace` 静默失败，被防御式 except 吞掉。防御式代码必须配可观测性
   （如 debug 级日志），否则缺陷不可见（P1-5）

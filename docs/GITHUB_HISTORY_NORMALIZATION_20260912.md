# GitHub 历史规范化（tree fsck）操作手册

**编制**: 2026-09-12（codex 协作会话）
**状态**: 待维护窗口执行 — 当前方案 A：仅 Gitea 为权威远程，GitHub 暂不强推
**风险等级**: 🔴 红区（重写 155 个提交的历史 + 双远程 force push）

---

## 1. 背景与现象

2026-09-12 推送 LingClaude `master` 到 GitHub 时被服务端拒绝：

```
remote: error: object 792b4bb4...: treeNotSorted: not properly sorted
remote: fatal: fsck error in packed object
error: remote unpack failed: index-pack failed
```

- **GitHub** 对接收对象执行严格 fsck（`receive.fsckObjects`），拒绝不规范 tree。
- **Gitea**（zhinenggitea.iepose.cn）fsck 宽松，已成功接收同样的历史。
- 不规范对象来源于 2026-09-10 前后 `/dev/urandom EACCES` 期间，
  用 `scripts/manual_commit.py` **手工构造 git 对象**的 N6 应急管线
  （自底向上重建 tree，条目排序/路径/mode 未完全符合 git 规范）。

## 2. 影响面（执行前需重新核对）

- 待推送提交数（当时）：**155**（`github/master..master`）
- `git fsck --strict` 全库坏 tree 报点：约 59 行（含重复模式），
  去重后坏 tree 对象 **53 个**，其中**位于待推送区间、GitHub 侧尚不存在的 11 个**为本次阻塞点。
- 三类违规：
  - `treeNotSorted`：tree 条目未按 git 规则排序（目录以 `name/` 参与比较）
  - `fullPathname`：tree 条目名含完整路径（应只存单级名字）
  - `zeroPaddedFilemode`：文件 mode 带前导零（如 `0100644` 应为 `100644`）
- 已在 GitHub 历史中的旧坏对象（如 `00b7891` 拥有的 `792b4bb`）
  属历史遗留，GitHub 既往已接收；真正导致本次拒绝的是新区间内的对象。

> 注意：由于并发灵克实例持续提交，以上数字与 SHA 执行时必须重新测量。

## 3. 前置条件（缺一不可）

1. **协调并发实例停手**：确认无其他 lingclaude/codex 进程在提交
   （`ps aux | grep -E 'pytest|lingclaude|lefthook'`；观察 `git log -1` 稳定不再前进）。
2. **完整备份**：
   - `cp -a /home/ai/lingclaude /home/ai/lingclaude.bak.$(date +%Y%m%d)`
   - 裸镜像：`git clone --mirror /home/ai/lingclaude /tmp/lingclaude-mirror.git`
3. **通知所有克隆方**：历史改写后 SHA 全变，其他成员的本地仓库需重新 clone 或 rebase。
4. 安装工具：`git filter-repo`（`pip install git-filter-repo`），勿用已废弃的 filter-branch。
5. Gitea 已是好的（内容正确、fsck 宽松），可作为内容基线参照。

## 4. 推荐方案：filter-repo 全量 tree 规范化

git filter-repo 的 `--replace-refs` + 提交重写会重建对象；但它默认**不修复
tree 排序/路径/mode**。因此需要先用一个 tree 规范化回调，或采用
"重新提交（replay）"方式让 git 自身重新生成规范 tree。

### 方案 A1（首选）：按文件内容 replay 重建线性历史

用 `git rebase --root` 配合 merge=recursive 容易在冲突上卡住，不推荐。
更稳的是用 filter-repo 的 blob/回调，或下面的"新根重建"：

```bash
cd /home/ai/lingclaude
# 4.1 建孤儿分支，从当前 HEAD 的完整快照开始（内容无损，仅历史重铸代价大，
#     一般不采用，除非不要求保留 155 提交粒度——见 A2）
```

### 方案 A2（推荐，保留提交粒度）：git filter-repo + tree 修复脚本

```bash
cd /home/ai/lingclaude
# 1) 在镜像副本上操作，不在主工作区直接做
git clone --mirror . /tmp/lc-fix.git && cd /tmp/lc-fix.git

# 2) 用 git 自身重新规范化所有 tree：遍历每个 commit，用 read-tree/checkout
#    重新写出规范对象。实操可借助 filter-repo 的 --commit-callback 对 tree
#    重建；或使用社区脚本对 fsck 报错对象逐一重建。
#    最小验证手段——先确认坏对象集合：
git fsck --strict 2>&1 | grep -E 'treeNotSorted|fullPathname|zeroPadded'

# 3) 规范化（示意：用 git 重新写 tree；具体脚本在执行窗口按当时 fsck 清单编写）
#    对每个坏 tree：git read-tree 到临时 index → GIT_INDEX_FILE 排序写回新 tree
#    → replace 旧 tree → filter-repo 重写引用
git filter-repo --force --replace-refs delete-no-add
# （tree 修复回调需在执行时落地；filter-repo 本身负责更新所有 commit/refs）

# 4) 验证：必须零报错
git fsck --strict

# 5) 验证内容无损：与 Gitea 逐文件比对
git remote add gitea https://zhinenggitea.iepose.cn/guangda/LingClaude.git
git fetch gitea master
git diff gitea/master master --stat   # 期望：内容差异仅来自规范化后新增提交，HEAD 树应一致
```

### 方案 A3（最省事，牺牲历史粒度）：单快照新根

若团队可接受把 155 个旧提交压成一个干净起点：

```bash
cd /home/ai/lingclaude
git checkout --orphan normalized
git add -A
git commit -m "chore: normalize git objects (fix treeNotSorted/fullPathname/zeroPadded)

历史对象由 manual_commit N6 应急管线手工构造，未通过 GitHub 严格 fsck。
以当前权威快照重建规范历史起点。旧历史保留于 Gitea 与本地备份。"
git fsck --strict   # 必须干净
```

> 采用 A3 会丢失线性历史与灵督审计哈希链的连续性，需族长/灵安签字。

## 5. 推送（红区，需明确授权）

```bash
# 子模块 better-harness 不在本仓库对象内，无需在此处理（已在 fork 双远程）。
# GitHub
git push --no-verify --force-with-lease=master:<github旧SHA48eef43> github master
# Gitea 覆盖对齐（历史已分叉，必须 force）
git push --no-verify --force gitea master
```

- 用 `--force-with-lease` 而非 `--force`，防止覆盖他人新提交。
- 若保留灵督 pre-push：全量 pytest 约 11 分钟，已知存在 **xdist worker 级
  flaky**（每次随机 1 个收集/teardown ERROR，位置漂移，单跑全绿，
  2896 passed 恒定）。必要时经显式授权用 `LINGCLAUDE_SKIP_REVIEW_GATE=1` +
  `--no-verify`，并留痕。

## 6. 审计/治理影响（须知会灵安）

- 重写后所有 commit SHA 改变；`.audit/last_commit_audit.json` 与
  `~/.ling-audit/records/*` 内记录的 `commit_hash`/`tree_hash` 与新对象不再对应
  （代码内容不变，但密码学锚点漂移）。
- 建议在重写后的首个提交附加说明，并归档旧记录（不可删，作为重写前证据）。
- `manual_commit.py` 应修复其 `build_tree()`：
  - 条目排序改为 git 规则：子目录键追加 `/` 后按字节序
    （`name.encode() + (b"/" if dir else b"")` 已部分处理，需覆盖嵌套与 mode）；
  - mode 不得带前导零；
  - tree 条目名只存单级分量，不存完整路径。
  修复前应停用手工管线，避免再次产生坏对象。

## 7. 回滚

- 远程：Gitea/GitHub 管理端或 `git push --force` 指向备份中的旧 ref。
- 本地：`rm -rf` 工作区后从 `/home/ai/lingclaude.bak.*` 恢复，
  或从 `/tmp/lingclaude-mirror.git` 还原。

## 8. 当前权威状态（2026-09-12）

| 远程 | HEAD | 说明 |
|------|------|------|
| Gitea | `ee53a28` | ✅ 权威，内容最新（含 codex providers、子模块指针、红测修复） |
| GitHub | `48eef43` | ⏮ 旧版，因 tree fsck 未更新 |
| 本地 | `ee53a28` | 与 Gitea 一致 |

**在本手册第 4–5 步完成前，"双远程"以 Gitea 为准；GitHub 仅历史镜像。**

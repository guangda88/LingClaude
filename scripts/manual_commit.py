#!/usr/bin/env python3
"""手工构造提交（/dev/urandom EACCES 期间的 N6 管线）。

流程：python zlib 直写 blob → 从 HEAD 树重建受影响目录树 → commit 对象 →
验证（parent/diffstat/逐 blob 回读）→（单独命令）update-ref。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import time
import zlib
from pathlib import Path

ROOT = Path("/home/ai/lingclaude")
# 2026-09-11 晚: 参数化改造 — FILES/MESSAGE 可由 CLI 覆盖, 原硬编码保留为默认。
_DEFAULT_FILES = [
    "lingclaude/api.py",
    "lingclaude/core/token_monitor.py",
    "tests/test_w5p1_dynamic_llm.py",
]
FILES = _DEFAULT_FILES

MESSAGE = """feat(w5p1): 直连兜底链动态化 + TARGET_MODEL env 化 — 换模型/供应商零代码改动

- api.py: _LLM_PROVIDERS 静态硬编码(glm-4.7/普通端点/key链不含ZHIPU_API_KEY→生产全空)
  改为 _resolve_llm_chain(): config.yaml 同源优先 → LINGCLAUDE_BYPASS_* env 整体覆盖
  → 静态表末级兜底(config 损坏不抛)
- token_monitor.py: TARGET_MODEL 硬编码 'GLM-4.7' → os.environ 可覆盖, 默认跟
  config.yaml 生产模型 glm-5.3-flash; 4 处比较改 casefold 不敏感, 2 处趋势 SQL
  改 LOWER(model) — 历史库大写键不再漏聚合/误标 warning
- tests: +8 (链构造三分支/消费端 mock 网络/casefold 聚合), 45+59 回归全绿
- 实测: 沙箱无出站网络, CLI 端到端留待宽松环境; factory 装配冒烟已过
- git add/commit 因 /dev/urandom EACCES 不可用, 本提交按 N6 管线手工构造(3 文件)
"""


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"FATAL git {' '.join(args[:3])}: {out.stderr[:200]}")
    return out.stdout


def store(data: bytes, typ: str) -> str:
    full = f"{typ} {len(data)}".encode() + b"\0" + data
    sha = hashlib.sha1(full).hexdigest()
    d = ROOT / ".git" / "objects" / sha[:2]
    d.mkdir(parents=True, exist_ok=True)
    p = d / sha[2:]
    if not p.exists():
        p.write_bytes(zlib.compress(full))
    return sha


def build_tree(entries: dict[str, tuple[str, str]]) -> str:
    """从全路径 entries 自底向上重建全部目录树，返回 root tree sha。"""
    cache: dict[str, str] = {}

    def build(dirpath: str) -> str:
        if dirpath in cache:
            return cache[dirpath]
        prefix_len = len(dirpath)
        items: dict[str, tuple[str, str]] = {}  # name → (mode, sha|DIR)
        for path, (mode, sha) in entries.items():
            if not path.startswith(dirpath):
                continue
            rest = path[prefix_len:]
            if "/" in rest:
                sub, _ = rest.split("/", 1)
                if sub in items:
                    continue
                subprefix = dirpath + sub + "/"
                items[sub] = ("40000", build(subprefix))
            else:
                items[rest] = (mode, sha)
        children = sorted(
            items.items(),
            key=lambda kv: kv[0].encode() + (b"/" if kv[1][0] == "40000" else b""),
        )
        buf = b"".join(
            mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(sha)
            for name, (mode, sha) in children
        )
        sha = store(buf, "tree")
        cache[dirpath] = sha
        return sha

    return build("")


def _parse_args(argv: list[str]) -> None:
    """--files a,b,c 与 --message-file F 覆盖默认 FILES/MESSAGE。"""
    global FILES, MESSAGE
    files_arg = msg_file = None
    i = 0
    while i < len(argv):
        if argv[i] == "--files" and i + 1 < len(argv):
            files_arg = argv[i + 1]
            i += 2
        elif argv[i] == "--message-file" and i + 1 < len(argv):
            msg_file = argv[i + 1]
            i += 2
        else:
            i += 1
    if files_arg:
        FILES = [x.strip() for x in files_arg.split(",") if x.strip()]
    if msg_file:
        MESSAGE = Path(msg_file).read_text(encoding="utf-8")


def main() -> int:
    _parse_args(sys.argv[1:])
    head = git("rev-parse", "HEAD").strip()
    print(f"parent: {head}")

    # 1) HEAD 全路径清单（mode + sha）
    entries: dict[str, tuple[str, str]] = {}
    for line in git("ls-tree", "-r", "-z", "HEAD").split("\0"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        mode, _typ, sha = meta.split()
        entries[path] = (mode, sha)
    n0 = len(entries)

    # 2) 13 文件写 blob（并回读校验）
    for rel in FILES:
        data = (ROOT / rel).read_bytes()
        sha = store(data, "blob")
        obj = ROOT / ".git" / "objects" / sha[:2] / sha[2:]
        back = zlib.decompress(obj.read_bytes())
        assert back == f"blob {len(data)}".encode() + b"\0" + data, rel
        entries[rel] = ("100644", sha)
    print(f"blobs: +{len(FILES)} (tree entries {n0} -> {len(entries)})")

    # 3) 目录树自底向上重建
    root_tree = build_tree(entries)
    print(f"root tree: {root_tree}")

    # 4) commit 对象
    ts = int(time.time())
    ident = "lingke <lingke@lingfamily.internal>"
    body = (f"tree {root_tree}\nparent {head}\n"
            f"author {ident} {ts} +0800\ncommitter {ident} {ts} +0800\n\n"
            + MESSAGE)
    commit_sha = store(body.encode(), "commit")
    print(f"commit: {commit_sha}")

    # 5) 验证：diffstat 恰好 13 文件 + 逐 blob 回读 + parent 链
    stat = git("diff", "--stat", head, commit_sha)
    n_files = len([l for l in stat.strip().splitlines() if " | " in l])
    print(f"diffstat 文件数: {n_files}")
    assert n_files == len(FILES), f"预期 {len(FILES)}, 实际 {n_files}\n{stat}"
    for rel in FILES:
        got = git("cat-file", "-p", f"{commit_sha}:{rel}")
        assert got == (ROOT / rel).read_text(encoding="utf-8"), rel
    parents = git("log", "--format=%P", "-1", commit_sha).split()
    assert parents[0] == head, parents
    fsck = subprocess.run(["git", "fsck", "--no-dangling"], cwd=ROOT,
                          capture_output=True, text=True)
    print("fsck:", (fsck.stderr or "clean").strip()[:200])
    print("验证通过: parent 链 / diffstat=len(FILES) / 逐 blob 回读一致")
    (ROOT / ".audit" / "pending_commit.txt").write_text(commit_sha, encoding="utf-8")
    print("待 update-ref 的 sha 已写入 .audit/pending_commit.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

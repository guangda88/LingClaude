#!/usr/bin/env python3
"""opencode 客户端配置免费模型自动同步 — 2026-10-04

补齐既有同步链的**最后一公里**：proxy3 侧免费模型路由已有定时同步
(llm-proxy/proxy3_py/scripts/sync_free_models.py --apply, 6h)，
但 opencode 客户端配置 ~/.config/opencode/opencode.json 的
provider.proxy3.models 与清单 free-models.md 一直靠手工补（本次新增
qwen3.8-27b / apodex-1.1-mini 即手工）。本脚本补这条腿。

数据源（每轮实测拉取，不凭记忆）：
  Zen        https://opencode.ai/zen/v1/models        免费档 14
  OpenRouter https://openrouter.ai/api/v1/models     :free 档 17
  proxy3     http://127.0.0.1:8765/v1/models          路由存在性闸门 + @openrouter id 映射

写入策略（保守派，对齐 AGENTS「建议-执行强制分离」与 sync_opencode_free.py 惯例）：
  * 只增不删——上游下架/代理拒绝的条目只记录进清单，不动配置（无用户确认词不删）。
  * 三重门控才写 config：① 在 OpenRouter :free 名单 ② proxy3 目录确有该路由
    ③ 经 proxy3 真实发一次 chat/completions 冒烟通过（防「目录里有=能用」的幻觉）。
  * 原子写（tmp + os.replace）+ 写前时间戳备份；无变化则不写盘（免 mtime 抖动触发无谓热加载）。
  * 锁 + 幂等：fcntl 非阻塞锁，重叠触发直接跳过。

Zen 侧只做**观测**不写配置：`opencode/*` 是 opencode 内建 provider，模型清单由
TUI `/models` 直接枚举，写进 opencode.json 的 proxy3 provider 无效，故仅入清单。

用法:
  python3 sync_opencode_client_models.py --dry-run   # 只报告不落盘
  python3 sync_opencode_client_models.py             # 应用（cron 默认）
  python3 sync_opencode_client_models.py --no-probe  # 跳过冒烟（不推荐，会放进不可用路由）

cron: 17 */6 * * * (错峰既有 0 */6 与 30 1，见 crontab)
日志: logs/sync_opencode_client_models.log
状态: ~/.config/opencode/.free-sync-state.json（Zen 差量对比基线）
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = Path(os.environ.get("OC_CLIENT_CONFIG", "/home/ai/.config/opencode/opencode.json"))
DOC = Path(os.environ.get("OC_FREE_DOC", "/home/ai/.config/opencode/free-models.md"))
STATE = Path(os.environ.get("OC_FREE_STATE", "/home/ai/.config/opencode/.free-sync-state.json"))
LOCK = Path(os.environ.get("OC_FREE_LOCK", "/tmp/opencode_client_models.lock"))
LOG = Path("/home/ai/lingclaude/logs/sync_opencode_client_models.log")

ZEN_URL = "https://opencode.ai/zen/v1/models"
OR_URL = "https://openrouter.ai/api/v1/models"
PROXY3_MODELS = os.environ.get("PROXY3_MODELS_URL", "http://127.0.0.1:8765/v1/models")
PROXY3_CHAT = os.environ.get("PROXY3_CHAT_URL", "http://127.0.0.1:8765/v1/chat/completions")
PROVIDER_KEY = "proxy3"
OR_SUFFIX = "@openrouter"
DOC_BEGIN = "<!-- AUTO:BEGIN sync_opencode_client_models -->"
DOC_END = "<!-- AUTO:END sync_opencode_client_models -->"

FETCH_TIMEOUT = 30
PROBE_TIMEOUT = 180
PROBE_MAX_TOKENS = 1024  # <1024 会被 reasoning 吃光导致 content=null 假阴性
UA = "lingclaude-sync-opencode-client/1.0"

# Zen 免费档判定：-free 后缀 + big-pickle（隐身免费模型，无 free 字样）
ZEN_FREE_EXTRA = {"big-pickle"}
# 审查/嵌入类不进 agent 配置：上游即便免费也不适合当编码 agent
ZEN_SKIP_HINTS = ("embed", "safety", "guard")
OR_SKIP_HINTS = ("embed", "safety", "guard", "moderation")
# Zen 隐私口径逐条标注（来源 opencode.ai/docs/zen Privacy 段，2026-10-04 核对）。
# 泛化写成"一律用于改进"会把零保留模型和禁发机密的 contributor 档一起带沟里。
ZEN_NOTES = {
    "big-pickle": "✅ 隐身模型，免费期数据可能用于改进",
    "space-bunny-free": "✅ 零保留，不用于训练",
    "longcat-2.5-preview-free": "✅ 零保留，不用于训练",
    "jev-1.13-free": "⚠ 决策模型（System One），非文本生成",
}
ZEN_MUSE_HINT = "contributor-free"
ZEN_NVIDIA_HINT = "nemotron"


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def http_json(url: str, timeout: int = FETCH_TIMEOUT, payload: dict | None = None,
              headers: dict | None = None) -> dict:
    """GET/POST JSON。失败抛异常由调用方降级——观测脚本不允许因单源失败整体崩。"""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def proxy3_key() -> str:
    """proxy3 鉴权：优先 LING_SHARED_KEY（PROXY3_API_KEY 在 cron/登录 shell 常为空）。"""
    for env in ("PROXY3_LING_SHARED_KEY", "PROXY3_API_KEY"):
        v = os.environ.get(env, "").strip()
        if v:
            return v
    return ""


def fetch_zen_free() -> tuple[set[str], str]:
    """Zen 免费档模型 id 集合。"""
    try:
        data = http_json(ZEN_URL).get("data", [])
    except Exception as exc:  # noqa: BLE001 — 观测脚本降级为「本轮未知」
        return set(), f"zen fetch failed: {exc}"
    free = {m["id"] for m in data
            if m.get("id") and (m["id"].endswith("-free") or m["id"] in ZEN_FREE_EXTRA)}
    return free, ""


def fetch_or_free() -> tuple[dict[str, int], str]:
    """OpenRouter :free 模型 → {model_id(含:free): context_length}。"""
    try:
        data = http_json(OR_URL).get("data", [])
    except Exception as exc:  # noqa: BLE001
        return {}, f"openrouter fetch failed: {exc}"
    out = {}
    for m in data:
        mid = m.get("id") or ""
        if mid.endswith(":free"):
            out[mid] = int(m.get("context_length") or 128000)
    return out, ""


def fetch_proxy3_ids() -> tuple[set[str], str]:
    """proxy3 路由目录 → 全部模型 id（写入前的存在性闸门）。"""
    try:
        data = http_json(PROXY3_MODELS).get("data", [])
    except Exception as exc:  # noqa: BLE001
        return set(), f"proxy3 catalog failed: {exc}"
    return {m["id"] for m in data if m.get("id")}, ""


def probe(model_key: str, timeout: int = PROBE_TIMEOUT) -> tuple[bool, str]:
    """经 proxy3 真实发一次 chat/completions 冒烟。返回 (是否通过, 原因)。"""
    key = proxy3_key()
    if not key:
        return False, "no proxy3 key in env (PROXY3_LING_SHARED_KEY/PROXY3_API_KEY)"
    payload = {"model": model_key,
               "messages": [{"role": "user", "content": "say ok"}],
               "max_tokens": PROBE_MAX_TOKENS}
    try:
        r = http_json(PROXY3_CHAT, timeout=timeout, payload=payload,
                      headers={"Content-Type": "application/json",
                               "Authorization": f"Bearer {key}",
                               "X-Agent-Id": "opencode"})
    except urllib.error.HTTPError as exc:
        return False, f"http {exc.code}"
    except Exception as exc:  # noqa: BLE001 — 冷启动超时常见，视为不通过但不崩
        return False, f"{type(exc).__name__}: {exc}"
    if r.get("error"):
        return False, str(r["error"])[:120]
    choices = r.get("choices") or []
    if not choices:
        return False, "empty choices"
    msg = choices[0].get("message") or {}
    if (msg.get("content") or msg.get("reasoning")):
        return True, "ok"
    return False, f"no text (finish_reason={choices[0].get('finish_reason')})"


def atomic_write(path: Path, text: str, backup: bool = True) -> None:
    """同目录 tmp + os.replace 原子落盘；写前留时间戳备份（同 fs 才能 rename）。"""
    if backup and path.exists():
        bak = path.with_suffix(path.suffix + ".bak.sync_"
                               + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(path, bak)
        log(f"backup: {bak}")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def render_doc(zen_now: set[str], zen_prev: set[str],
               or_now: dict[str, int], added: list[tuple[str, int]],
               blocked: list[tuple[str, str]], local_or: set[str],
               skipped: list[tuple[str, str]]) -> str:
    """生成 AUTO 块：Zen 差量 + OR 现状 + 本轮写入 + 受阻原因。手工段不动。"""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    zen_new = sorted(zen_now - zen_prev) if zen_prev else []
    zen_gone = sorted(zen_prev - zen_now) if zen_prev else []
    or_gone = sorted(local_or - set(or_now))
    out = [DOC_BEGIN, "", f"<!-- 自动生成于 {ts}，勿手改；手改会在下一轮被覆盖 -->", ""]
    out.append("### Zen 免费档（opencode 内建 provider，不写入 opencode.json）")
    out.append("")
    out.append(f"当前 {len(zen_now)} 个。相比上一轮：新增 {len(zen_new)}"
               + (f"（{', '.join(zen_new)}）" if zen_new else "")
               + f"，消失 {len(zen_gone)}" + (f"（{', '.join(zen_gone)}）" if zen_gone else "") + "。")
    out.append("")
    out.append("| 模型 | 状态 |")
    out.append("|---|---|")
    for mid in sorted(zen_now):
        if mid in ZEN_NOTES:
            mark = ZEN_NOTES[mid]
        elif ZEN_MUSE_HINT in mid:
            mark = "⚠ Meta contributor 档，同意用 prompt 训练，禁发机密"
        elif ZEN_NVIDIA_HINT in mid:
            mark = "⚠ NVIDIA 试用端点，日志留存，禁发机密"
        elif mid.endswith("-free"):
            mark = "✅ 免费期数据用于改进模型，勿发机密"
        else:
            mark = "✅"
        out.append(f"| `opencode/{mid}` | {mark} |")
    out += ["", "### OpenRouter `:free`（经 proxy3 消费）", "",
            f"上游 {len(or_now)} 个；本地 opencode.json 记 {len(local_or)} 个。", ""]
    if added:
        out += ["本轮新增并冒烟通过：", "", "| 模型 | ctx |", "|---|---|"]
        out += [f"| `{mid}{OR_SUFFIX}` | {ctx} |" for mid, ctx in added]
        out.append("")
    else:
        out.append("本轮无新增（幂等）。")
        out.append("")
    if skipped:
        out += ["跳过（不适合作 agent：嵌入/审查/守卫类）：", ""]
        out += [f"- `{mid}` — {why}" for mid, why in skipped]
        out.append("")
    if blocked:
        out += ["阻塞（上游有、proxy3 拒绝或冒烟失败，未写入）：", ""]
        out += [f"- `{mid}{OR_SUFFIX}` — {why}" for mid, why in blocked]
        out.append("")
    if or_gone:
        out += ["不在上游 `:free` 名单（proxy3 仍路由；只增不删，暂留配置观察）：", ""]
        out += [f"- `{mid}`" for mid in or_gone]
        out.append("")
    out.append(DOC_END)
    return "\n".join(out)


def strip_stamp(block: str) -> str:
    """剥掉「自动生成于 <时间戳>」注释行，用于内容等价比较。"""
    return "\n".join(ln for ln in block.splitlines() if "自动生成于" not in ln).strip()


def update_doc(block: str, dry_run: bool) -> None:
    if not DOC.exists():
        log(f"doc missing: {DOC}（跳过清单更新）")
        return
    text = DOC.read_text(encoding="utf-8")
    if DOC_BEGIN in text and DOC_END in text:
        head, rest = text.split(DOC_BEGIN, 1)
        old_block, tail = rest.split(DOC_END, 1)
        # render_doc 返回的 block 自带首尾标记，而 old_block 是标记之间的裸内容；
        # 直接比会永远不等 → 每 6h 白写一次 + 白留一份 .bak.sync_*。先剥标记再比。
        inner = block.split(DOC_BEGIN, 1)[1].split(DOC_END, 1)[0] if DOC_BEGIN in block else block
        # 时间戳每轮必变 → 内容比较前再剥掉生成时间行。语义无变化整轮跳过落盘。
        if strip_stamp(old_block) == strip_stamp(inner):
            log("doc unchanged (仅时间戳不同，跳过写盘)")
            return
        new = f"{head}{block}{tail}"
    else:
        log("AUTO markers missing → 追加到文末")
        new = text.rstrip() + "\n\n" + block + "\n"
    if new == text:
        log("doc unchanged")
        return
    if dry_run:
        log("doc: would update (dry-run)")
        return
    atomic_write(DOC, new)


def main() -> int:
    ap = argparse.ArgumentParser(description="opencode 客户端免费模型自动同步")
    ap.add_argument("--dry-run", action="store_true", help="只报告不落盘")
    ap.add_argument("--no-probe", action="store_true", help="跳过冒烟探测（不推荐）")
    ap.add_argument("--no-md", action="store_true", help="不更新 free-models.md")
    ap.add_argument("--timeout", type=int, default=PROBE_TIMEOUT, help="冒烟超时秒")
    args = ap.parse_args()

    LOCK.touch(exist_ok=True)
    lk = LOCK.open("r")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("skip: 另一轮同步在跑（锁未释放）")
        return 0

    if not CONFIG.exists():
        log(f"fatal: config missing {CONFIG}")
        return 1
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log(f"fatal: 配置解析失败（拒绝覆盖）: {exc}")
        return 1
    models = (cfg.get("provider", {}).get(PROVIDER_KEY, {}) or {}).get("models")
    if not isinstance(models, dict):
        log(f"fatal: provider.{PROVIDER_KEY}.models 缺失或类型异常")
        return 1

    zen_now, zen_err = fetch_zen_free()
    or_now, or_err = fetch_or_free()
    p3_ids, p3_err = fetch_proxy3_ids()
    for e in (zen_err, or_err, p3_err):
        if e:
            log(f"warn: {e}")

    local_or = {m[: -len(OR_SUFFIX)] for m in models if m.endswith(OR_SUFFIX)}
    zen_prev: set[str] = set()
    if STATE.exists():
        try:
            zen_prev = set(json.loads(STATE.read_text(encoding="utf-8")).get("zen_free", []))
        except (json.JSONDecodeError, OSError):
            zen_prev = set()

    skipped: list[tuple[str, str]] = []
    blocked: list[tuple[str, str]] = []
    added: list[tuple[str, int]] = []
    if not or_now:
        log("warn: OpenRouter 名单为空，本轮不做任何写入（防上游异常清空配置）")
    for mid, ctx in sorted(or_now.items()):
        if mid in local_or:
            continue
        if any(h in mid for h in OR_SKIP_HINTS):
            skipped.append((mid, "嵌入/审查/守卫类，非编码 agent 用途"))
            continue
        p3_key = mid + OR_SUFFIX
        if p3_ids and p3_key not in p3_ids:
            blocked.append((mid, "proxy3 目录无此路由"))
            continue
        if args.no_probe:
            blocked.append((mid, "--no-probe 跳过验证（未写入）"))
            continue
        ok, why = probe(p3_key, args.timeout)
        if ok:
            added.append((mid, ctx))
            log(f"probe PASS {p3_key} ctx={ctx}")
        else:
            blocked.append((mid, why))
            log(f"probe FAIL {p3_key}: {why}")

    if added:
        for mid, ctx in added:
            key = mid + OR_SUFFIX
            models[key] = {"id": key, "name": key, "context_window": ctx}
        if args.dry_run:
            log(f"dry-run: would add {len(added)} -> {[m for m, _ in added]}")
        else:
            atomic_write(CONFIG, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
            log(f"applied: +{len(added)} {[m for m, _ in added]}")
    else:
        log("added=0")

    if not args.no_md:
        block = render_doc(zen_now, zen_prev, or_now, added, blocked,
                           local_or | {m for m, _ in added}, skipped)
        update_doc(block, args.dry_run)

    if not args.dry_run:
        try:
            atomic_write(STATE, json.dumps(
                {"zen_free": sorted(zen_now), "or_free": sorted(or_now),
                 "at": datetime.datetime.now().isoformat(timespec="seconds")},
                ensure_ascii=False, indent=2) + "\n", backup=False)
        except OSError as exc:
            log(f"warn: state 写入失败 {exc}")

    log(f"done added={len(added)} blocked={len(blocked)} skipped={len(skipped)} "
        f"zen_free={len(zen_now)} or_free={len(or_now)} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

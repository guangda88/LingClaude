#!/usr/bin/env bash
# 通义听悟 cookie 实测 + 转写记录拉取（宿主侧执行）v2
# 设计说明（v1 的 gateway UUID 端点未经证实，已废弃）：
#   本脚本只做三件确定的事：
#   [1] GET https://tingwu.aliyun.com/home 校验 cookie 登录态（只读，安全）
#   [2] 若您提供 DevTools 拷贝的「记录列表」cURL，则回放并自动翻页落盘
#   [3] 与 345 场讲课资产索引对账（端点无关逻辑）
# 用法:
#   bash host_tingwu_pull.sh /tmp/tingwu_cookie.txt [curl_snippet.txt]
#   curl_snippet.txt 获取方法:
#     浏览器打开 tingwu.aliyun.com → F12 → Network → 刷新 → 找到
#     返回转写记录列表的 XHR（响应含 taskId/任务名）→ 右键 →
#     Copy → Copy as cURL (bash) → 粘贴保存为 curl_snippet.txt
set -euo pipefail

COOKIE_FILE="${1:-/tmp/tingwu_cookie.txt}"
CURL_SNIPPET="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="$SCRIPT_DIR/tingwu_pull_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT_DIR"; chmod 700 "$OUT_DIR"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

[[ -f "$COOKIE_FILE" ]] || { echo "cookie 文件不存在: $COOKIE_FILE"; exit 1; }
COOKIE=$(cat "$COOKIE_FILE")

echo "[1/3] 登录态校验（GET /home，只读）..."
code=$(curl -s -o "$OUT_DIR/home.html" -w "%{http_code}" --max-time 30 \
  -L -H "Cookie: $COOKIE" -H "User-Agent: $UA" "https://tingwu.aliyun.com/home")
echo "  HTTP $code, $(wc -c < "$OUT_DIR/home.html") bytes"
# 判定: 未登录时听悟会跳登录域或返回登录字样页面
if grep -qiE "login\.|登录后|请登录|passport" "$OUT_DIR/home.html" \
   && ! grep -qi "听悟" "$OUT_DIR/home.html"; then
  echo "  ❌ cookie 失效（疑似返回登录页）→ 检查 $OUT_DIR/home.html"
  exit 2
fi
echo "  ✅ 登录态存活判定通过（请顺手人工确认 home.html 里有您的任务列表）"

echo "[2/3] 拉取转写记录列表..."
MANIFEST="$OUT_DIR/manifest.jsonl"; : > "$MANIFEST"
if [[ -z "$CURL_SNIPPET" || ! -f "$CURL_SNIPPET" ]]; then
  echo "  ⏭ 未提供 cURL snippet，跳过自动拉取。"
  echo "    影响: 只完成登录态验证；记录列表请浏览器确认，或按脚本头注释"
  echo "    提供 curl_snippet.txt 后重跑（翻页/落盘自动完成）。"
else
  # 从 cURL 片段提取 URL、data；cookie 用我们自己的文件覆盖（防 snippet 内含旧值）
  TW_COOKIE="$COOKIE" python3 - "$CURL_SNIPPET" "$OUT_DIR" <<'PY'
import re, sys, json, subprocess, shlex
snippet = open(sys.argv[1], encoding="utf-8").read()
outdir  = sys.argv[2]
import os
cookie = os.environ.get("TW_COOKIE", "")
# 提取 URL
m = re.search(r"curl\s+'([^']+)'", snippet) or re.search(r'curl\s+"([^"]+)"', snippet)
if not m:
    print("  ❌ 未能从 snippet 解析出 URL"); sys.exit(3)
url = m.group(1)
# 提取所有 -H 头，剔除 cookie/长度相关
headers = re.findall(r"(?:-H|--header)\s+'([^']*)'", snippet)
headers = [h for h in headers if not h.lower().startswith(("cookie", "content-length"))]
body_m = re.search(r"(?:--data(?:-raw)?|--data-binary)\s+'(.*?)'\s*(?:\\\n|$)", snippet, re.S)
body = body_m.group(1) if body_m else "{}"
def fetch(page):
    b = body
    # 常见分页字段替换（逐一尝试，替换不上的字段保持原样）
    for pat, rep in ((r'"pageNo"\s*:\s*\d+', f'"pageNo":{page}'),
                     (r'"page"\s*:\s*\d+',    f'"page":{page}'),
                     (r'"PageNumber"\s*:\s*\d+', f'"PageNumber":{page}')):
        b = re.sub(pat, rep, b)
    cmd = ["curl", "-s", "--max-time", "30", url,
           "-H", f"Cookie: {cookie}", "-H", "Content-Type: application/json"]
    for h in headers: cmd += ["-H", h]
    cmd += ["--data", b]
    return subprocess.run(cmd, capture_output=True, text=True).stdout
for page in range(1, 41):
    resp = fetch(page)
    with open(f"{outdir}/manifest.jsonl", "a", encoding="utf-8") as f:
        f.write(resp.strip() + "\n")
    n = len(re.findall(r"taskId|TaskId", resp))
    print(f"  page {page}: {n} 条")
    if n < 1 or page == 40: break
PY
fi

echo "[3/3] 生成 TSV 摘要 + 与 345 场资产索引对账..."
python3 - "$MANIFEST" "$OUT_DIR/manifest.tsv" <<'PY'
import json, re, sys
rows, seen = [], set()
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line: continue
    try: d = json.loads(line)
    except Exception: continue
    def walk(o):
        if isinstance(o, dict):
            if ("taskId" in o) or ("TaskId" in o): yield o
            for v in o.values(): yield from walk(v)
        elif isinstance(o, list):
            for v in o: yield from walk(v)
    for item in walk(d):
        r = {k: item.get(k, "") for k in
             ("taskId","TaskId","Name","name","Status","status","CreateTime","createTime")
             if item.get(k)}
        key = str(sorted(r.items()))
        if r and key not in seen:
            seen.add(key); rows.append(r)
with open(sys.argv[2], "w", encoding="utf-8") as f:
    f.write("task_id\t名称\t状态\t创建时间\n")
    for r in rows:
        f.write("\t".join(str(v) for v in r.values()) + "\n")
print(f"  唯一记录 {len(rows)} 条 → manifest.tsv")
PY
IDX="$SCRIPT_DIR/m0d_讲课资产索引.tsv"
if [[ -f "$IDX" && -s "$MANIFEST" ]]; then
python3 - "$OUT_DIR/manifest.tsv" "$IDX" "$OUT_DIR/对账报告.md" <<'PY'
import csv, re, sys
man = [l.split("\t") for l in open(sys.argv[1], encoding="utf-8").read().splitlines()[1:] if l.strip()]
idx = list(csv.DictReader(open(sys.argv[2], encoding="utf-8"), delimiter="\t"))
norm = lambda s: re.sub(r"[\s（）()【】\[\]·._\-]", "", s)
man_names = [norm(r[1]) for r in man if len(r) > 1]
hit = sum(1 for a in idx if (t := norm(a["主题"])) and any(t and t in m for m in man_names))
with open(sys.argv[3], "w", encoding="utf-8") as f:
    f.write(f"# 听悟×资产索引对账\n\n- 听悟记录: {len(man)}\n- 资产索引: {len(idx)} 场\n- 主题模糊命中: {hit} 场\n")
print(f"  命中 {hit}/{len(idx)} 场 → 对账报告.md")
PY
fi
echo "完成: $OUT_DIR"
echo "⚠ 用后删除 cookie: shred -u $COOKIE_FILE"

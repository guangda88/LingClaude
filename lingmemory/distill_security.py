"""
灵码 安全蒸馏器 — 从1574条审计发现中蒸馏安全规则

出入：audit_finding(1574条) → coding_rule(security类)
流转：每个critical/high finding变成一条安全rule
"""
import json, os, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lingmemory.api import LingMemoryAPI

KEY = os.environ.get("ZHIPU_API_KEY", "")
URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = "glm-4-flash"


# 审计发现→安全规则模板
SEVERITY_DESC = {
    "SEC-INJ-001": "SQL注入", "SEC-INJ-002": "命令注入",
    "SEC-AUTH-001": "硬编码密码", "SEC-AUTH-002": "越权漏洞",
    "SEC-AUTH-004": "接口无鉴权", "SEC-AUTH-005": "前端控制权限",
    "SEC-FILE-001": "任意文件上传", "SEC-FILE-002": "路径遍历",
    "SEC-FILE-003": "任意文件读取",
    "SEC-CRYPTO-001": "硬编码密钥", "SEC-CRYPTO-002": "弱加密算法",
    "SEC-CRYPTO-003": "敏感数据未脱敏", "SEC-CRYPTO-004": "明文传输",
    "SEC-BIZ-001": "无限流防爆破", "SEC-BIZ-002": "验证码可复用",
    "SEC-MISC-001": "危险函数eval/exec", "SEC-MISC-002": "CORS配置不当",
    "SEC-MISC-003": "SSRF服务端请求伪造", "SEC-MISC-004": "反序列化漏洞",
    "COMPL-001": "敏感数据明文存储", "DELIV-001": "密钥泄露",
    "DELIV-003": "日志含敏感信息",
    "PERF-003": "SELECT *全表查询", "PERF-004": "大文件一次性加载",
}


def get_unique_findings(api: LingMemoryAPI) -> list[dict]:
    """获取去重后的审计发现（按check_id去重）"""
    rows = api.lm.conn.execute(
        "SELECT DISTINCT json_extract(data, '$.check_id') as cid, "
        "json_extract(data, '$.severity') as sev "
        "FROM records WHERE type='audit_finding' "
        "AND json_extract(data, '$.severity') IN ('critical','high') "
        "ORDER BY sev"
    ).fetchall()
    return [{"check_id": r["cid"], "severity": r["sev"]} for r in rows if r["cid"]]


def get_finding_examples(api: LingMemoryAPI, check_id: str, limit=5) -> list[str]:
    """获取某个check_id的实际代码片段作为蒸馏素材"""
    rows = api.lm.conn.execute(
        "SELECT json_extract(data, '$.snippet') as snippet, "
        "json_extract(data, '$.file') as file, "
        "json_extract(data, '$.project') as proj "
        "FROM records WHERE type='audit_finding' "
        "AND json_extract(data, '$.check_id')=? LIMIT ?", (check_id, limit)
    ).fetchall()
    examples = []
    for r in rows:
        snippet = (r["snippet"] or "")[:100]
        if snippet:
            examples.append(f"[{r['proj']}] {snippet}")
    return examples


def distill_security_rule(check_id: str, desc: str, examples: list[str]) -> str | None:
    """用LLM从审计发现中蒸馏安全规则"""
    context = "\n".join(examples[:3]) if examples else desc
    prompt = f"以下代码有{desc}风险。用一句15字内的规则说明怎么避免：\n{context[:200]}"

    req = urllib.request.Request(URL, data=json.dumps({
        "model": MODEL, "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 50, "temperature": 0,
    }).encode(), headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {KEY}",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        rule = json.loads(resp.read())["choices"][0]["message"]["content"]
        rule = rule.strip().split('\n')[0][:50].strip('"\'。，')
        return rule if len(rule) > 5 else None
    except:
        return None


def run_security_distillation():
    """安全蒸馏主入口"""
    api = LingMemoryAPI(member="lingclaude")
    findings = get_unique_findings(api)
    print(f"unique critical/high findings: {len(findings)}种")

    stats = {"total": 0, "new": 0, "dup": 0}
    start = time.time()

    for f in findings:
        cid = f["check_id"]
        desc = SEVERITY_DESC.get(cid, cid)
        examples = get_finding_examples(api, cid, 3)

        # 直接写rule（不需要LLM——审计规则已经够清楚了）
        rule_text = f"{desc}：{get_remediation(cid)}"

        # 查重
        existing = api.lm.conn.execute(
            "SELECT COUNT(*) FROM records WHERE type='coding_rule' "
            "AND json_extract(data, '$.rule')=?", (rule_text,)).fetchone()[0]

        if existing == 0:
            api.lm.create(type="coding_rule", data={
                "rule": rule_text, "evidence": [f"audit_{cid}"],
                "category": "security", "confidence": 0.5,
                "distilled": True, "source": "audit_finding",
                "check_id": cid, "severity": f["severity"],
            }, created_by="lingclaude")
            stats["new"] += 1
            print(f"  ✅ {cid:15s} {rule_text[:50]}")
        else:
            stats["dup"] += 1
        stats["total"] += 1

        # 也用LLM蒸馏一条更具体的
        llm_rule = distill_security_rule(cid, desc, examples)
        if llm_rule:
            existing2 = api.lm.conn.execute(
                "SELECT COUNT(*) FROM records WHERE type='coding_rule' "
                "AND json_extract(data, '$.rule')=?", (llm_rule,)).fetchone()[0]
            if existing2 == 0:
                api.lm.create(type="coding_rule", data={
                    "rule": llm_rule, "evidence": [f"audit_llm_{cid}"],
                    "category": "security", "confidence": 0.4,
                    "distilled": True, "source": "audit_finding_llm",
                    "check_id": cid,
                }, created_by="lingclaude")
                stats["new"] += 1
                print(f"  ✅ {cid:15s} [LLM] {llm_rule[:50]}")
            else:
                stats["dup"] += 1
            stats["total"] += 1

    total_rules = api.lm.conn.execute(
        "SELECT COUNT(*) FROM records WHERE type='coding_rule'").fetchone()[0]
    sec_rules = api.lm.conn.execute(
        "SELECT COUNT(*) FROM records WHERE type='coding_rule' "
        "AND json_extract(data, '$.category')='security'").fetchone()[0]

    print(f"\n安全蒸馏: scanned={stats['total']} new={stats['new']} dup={stats['dup']}")
    print(f"coding_rule: {total_rules}条 (security={sec_rules}条)")
    print(f"耗时: {time.time()-start:.1f}s")
    api.close()


def get_remediation(check_id: str) -> str:
    """每个check_id的修复建议"""
    remediations = {
        "SEC-INJ-001": "用参数化查询，禁止SQL拼接",
        "SEC-INJ-002": "用参数列表传参，禁止shell=True拼接",
        "SEC-AUTH-001": "密码用bcrypt加盐哈希，禁止明文",
        "SEC-AUTH-002": "每次操作校验资源owner==请求者",
        "SEC-AUTH-004": "所有非public端点必须经过auth中间件",
        "SEC-AUTH-005": "后端必须独立校验权限，不信任前端",
        "SEC-FILE-001": "文件后缀白名单+内容校验",
        "SEC-FILE-002": "用realpath校验路径在允许范围内",
        "SEC-FILE-003": "文件路径必须限制在允许目录",
        "SEC-CRYPTO-001": "密钥从环境变量读取，禁止硬编码",
        "SEC-CRYPTO-002": "用SHA256+/AES-GCM，禁用MD5/SHA1/DES",
        "SEC-CRYPTO-003": "日志/响应中手机号身份证必须脱敏",
        "SEC-CRYPTO-004": "外部通信用HTTPS，禁止明文HTTP",
        "SEC-BIZ-001": "登录/API加rate limiter",
        "SEC-BIZ-002": "验证码使用后立即失效",
        "SEC-MISC-001": "禁止eval/exec，或严格过滤输入",
        "SEC-MISC-002": "CORS用白名单域名，不用通配符",
        "SEC-MISC-003": "URL白名单+禁止内网地址访问",
        "SEC-MISC-004": "禁止pickle反序列化不可信数据",
        "COMPL-001": "身份证手机号加密存储",
        "DELIV-001": "git历史中的密钥必须清理+轮换",
        "DELIV-003": "日志不打印密码token等敏感字段",
        "PERF-003": "只SELECT需要的字段，不用星号",
        "PERF-004": "大文件用流式读取，不一次性load",
    }
    return remediations.get(check_id, "按检查项修复")


if __name__ == "__main__":
    run_security_distillation()

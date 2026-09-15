"""灵克外部查询 seam — LLM 直连/兜底、GitHub/PyPI/版本/项目/提交/文件分析。

I3 (2026-09-15): 从 lingclaude/api.py 抽出。原 api.py 986 行门面过载，HTTP 路由
与业务逻辑（LLM 调用/GitHub/PyPI 查询/项目元数据）焊在一起。本模块承载业务，
api.py 只留 HTTP 路由 + re-export 兼容层。

自包含: 自带独立 logger（lingclaude.seams.external_query）与 Path/repo_path
依赖，不反向依赖 api 模块（避免循环）。
"""

import json
import logging
import os
import subprocess
from pathlib import Path

from lingclaude.lacp.cross_repo_seam import repo_path

logger = logging.getLogger("lingclaude.seams.external_query")


def _load_env_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    # E12(灵元1.0 再照): /home/ai 硬编码 → cross_repo_seam.repo_path（env 可覆盖）
    env_files = []
    for name in ("lingzhi", "lingclaude"):
        p = repo_path(name)
        if p is not None:
            env_files.append(str(p / ".env"))
    for f in env_files:
        p = Path(f)
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    keys[k.strip()] = v.strip()
    return keys


# R5（蜂群单点评估）：proxy3(8765) 是模型面单点——宕机时 cloud provider 全断，
# 但本地 provider(task_router._is_local_base 放行)与 LingBus 不受影响。
# 端点改为 env 可覆盖，给降级路径留门（此前连覆盖都没有）。
_PROXY_URL = os.environ.get(
    "LINGCLAUDE_PROXY_URL", "http://127.0.0.1:8765/v1/chat/completions"
)
_PROXY_API_KEY = os.environ.get("PROXY_API_KEY", "")


def _call_llm(system_prompt: str, user_msg: str) -> str:
    import urllib.request

    body = json.dumps({
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.7,
        "max_tokens": 1024,
    }, ensure_ascii=False).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "X-Caller": "lingclaude-api",
        "X-Purpose": "general_qa",
        "X-API-Key": _PROXY_API_KEY,
    }

    try:
        req = urllib.request.Request(_PROXY_URL, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 — 固定代理 URL
            result = json.loads(resp.read().decode("utf-8"))
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        provider = result.get("model", "proxy")
        logger.info(f"LLM OK via proxy (model={provider})")
        return content.strip()
    except Exception as e:
        logger.warning(f"Proxy call failed: {e}, falling back to direct")

    return _call_llm_direct(system_prompt, user_msg)


# W5+1 (2026-09-11): 直连兜底链动态化 — 原静态表硬编码 glm-4.7/普通端点,
# 与 config.yaml 生产模型(coding 端点)脱节, 且 key 链不含 ZHIPU_API_KEY
# 导致生产环境实际全空。现在:
#   1) 首选项读 config.yaml 的 model/base_url + _resolve_api_key (与主链同源);
#   2) LINGCLAUDE_BYPASS_MODEL/BYPASS_BASE_URL/BYPASS_API_KEY_ENV 可整体覆盖;
#   3) 保留原静态表为末级兜底 (config 读取失败时降级, 不抛异常)。
_STATIC_LLM_FALLBACK = [
    {"key_env": "GLM_CODING_PLAN_KEY", "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions", "model": "glm-4.7"},
    {"key_env": "GLM_API_KEY", "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions", "model": "glm-4.7"},
    {"key_env": "DEEPSEEK_API_KEY", "url": "https://api.deepseek.com/v1/chat/completions", "model": "deepseek-chat"},
]


def _resolve_llm_chain() -> list[dict[str, str]]:
    """构造直连兜底链: config 优先 → env 覆盖 → 静态表兜底。

    返回项形如 {"key_env": <环境变量名>, "url": <端点>, "model": <模型名>};
    key_env 指向的 env 变量为空时该项被 _call_llm_direct 跳过。
    """
    # 显式覆盖: 测试/临时切流不落盘
    m = os.environ.get("LINGCLAUDE_BYPASS_MODEL", "")
    if m:
        return [{
            "key_env": os.environ.get("LINGCLAUDE_BYPASS_API_KEY_ENV", "ZHIPU_API_KEY"),
            "url": os.environ.get("LINGCLAUDE_BYPASS_BASE_URL",
                                  "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"),
            "model": m,
        }]
    try:
        from lingclaude.core.config import _resolve_api_key, load_config, find_config_path
        mc = load_config(find_config_path()).model
        key = _resolve_api_key(mc.api_key)
        if mc.base_url and key:
            url = mc.base_url.rstrip("/")
            if not url.endswith("/chat/completions"):
                url += "/chat/completions"
            return [{
                # _resolve_api_key 与 .env 解析同源, 复用其结果; key_env 仅作日志标识
                "key_env": "ZHIPU_API_KEY(config)",
                "url": url,
                "model": mc.model,
                "_key": key,
            }]
    except Exception as e:  # config 损坏/不可读 → 降级静态表, 兜底链不可因配置崩
        logger.warning(f"_resolve_llm_chain config fallback: {e}")
    return list(_STATIC_LLM_FALLBACK)


# 兼容旧引用: 动态链的惰性快照 (仅测试/调试用, 调用路径一律走 _resolve_llm_chain())
def _llm_providers_snapshot() -> list[dict[str, str]]:
    return _resolve_llm_chain()


def _call_llm_direct(system_prompt: str, user_msg: str) -> str:
    import urllib.request

    env_keys = _load_env_keys()
    body = json.dumps({
        "model": "",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.7,
        "max_tokens": 1024,
    }, ensure_ascii=False).encode("utf-8")

    for provider in _resolve_llm_chain():
        # config 链项自带 _key; 静态兜底项查 os.environ → .env 文件
        api_key = provider.get("_key") or os.environ.get(provider["key_env"], "") \
            or env_keys.get(provider["key_env"], "")
        if not api_key:
            continue
        payload = json.loads(body)
        payload["model"] = provider["model"]
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            provider["url"], data=data,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 — provider URL 由配置文件控制
                result = json.loads(resp.read().decode("utf-8"))
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            logger.info(f"LLM OK via direct {provider['key_env']}/{provider['model']}")
            return content.strip()
        except Exception as e:
            logger.warning(f"LLM {provider['key_env']}/{provider['model']} failed: {e}")
            continue

    logger.error("所有 LLM provider 均不可用")
    return ""


def _route_question(prompt: str) -> str:
    p = prompt.lower()

    if any(kw in p for kw in ("几个星", "star", "github")):
        return _query_github_stars(prompt)
    if any(kw in p for kw in ("下载量", "download", "pypi")):
        return _query_pypi_downloads(prompt)
    if any(kw in p for kw in ("版本", "version")):
        return _query_versions()
    if any(kw in p for kw in ("项目", "project", "状态")):
        return _format_projects()
    if any(kw in p for kw in ("提交", "commit", "git")):
        return _query_recent_commits(prompt)

    system = "你是灵克（lingclaude），灵字辈大家庭的编程助手。简洁回答。"
    return _call_llm(system, prompt) or f"灵克暂时无法回答：{prompt}"


def _http_get_json(url: str, timeout: float, ua: str = "lingclaude") -> dict:
    """HTTP GET 返回 JSON（纯 urllib，无第三方依赖）。失败抛异常由调用方处理。

    收敛: _query_github_stars 两个分支 + _query_pypi_downloads 三处逐字重复的
    Request 构造 + urlopen + json.loads 模式（维护点 3→1）。
    """
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 — 固定外部 API URL
        return json.loads(resp.read().decode())


def _query_github_stars(prompt: str) -> str:
    repos = {
        "灵通": "guangda88/lingflow",
        "灵克": "guangda88/lingclaude",
        "灵通问道": "guangda88/lingtongask",
        "灵扬": "guangda88/lingyang",
    }
    for name, repo in repos.items():
        if name in prompt:
            try:
                data = _http_get_json(f"https://api.github.com/repos/{repo}", 10)
                return f"{name} ({repo}): {data.get('stargazers_count', 0)} stars, {data.get('forks_count', 0)} forks, {data.get('open_issues_count', 0)} open issues"
            except Exception as e:
                return f"查询 {name} 的 GitHub 信息失败: {e}"

    all_info = []
    for name, repo in repos.items():
        try:
            data = _http_get_json(f"https://api.github.com/repos/{repo}", 10)
            all_info.append(f"{name}: {data.get('stargazers_count', 0)} stars")
        except Exception:
            all_info.append(f"{name}: 查询失败")
    return "\n".join(all_info)


def _query_pypi_downloads(prompt: str) -> str:
    packages = ["lingflow-core", "lingflow-mcp", "lingclaude", "lingtongask"]
    results = []
    for pkg in packages:
        try:
            data = _http_get_json(f"https://pypi.org/pypi/{pkg}/json", 5)
            ver = data.get("info", {}).get("version", "?")
            results.append(f"{pkg} v{ver}")
        except Exception as e:
            logger.debug("PyPI version check failed for %s: %s", pkg, e)
    return "\n".join(results) if results else "未找到 PyPI 包信息。"


def _query_versions() -> str:
    p_lc = repo_path("lingclaude")
    # E12: /home/ai 硬编码 → cross_repo_seam（env 可覆盖）
    version_file = Path(p_lc) / "VERSION" if p_lc else Path("/home/ai/lingclaude/VERSION")
    lc_ver = version_file.read_text().strip() if version_file.exists() else "未知"
    return f"灵克 (lingclaude) 当前版本: {lc_ver}"


def _list_projects() -> list[dict]:
    # E12: /home/ai 硬编码 → cross_repo_seam（env 可覆盖）
    names = ("lingflow", "lingclaude", "lingyang", "lingtongask", "lingmessage")
    roots = {}
    for name in names:
        p = repo_path(name)
        if p is not None:
            roots[name] = str(p)
    projects = []
    for name, path in roots.items():
        p = Path(path)
        if p.exists():
            projects.append({"name": name, "path": path, "exists": True})
        else:
            projects.append({"name": name, "path": path, "exists": False})
    return projects


def _format_projects() -> str:
    projects = _list_projects()
    lines = []
    for p in projects:
        status = "存在" if p["exists"] else "不存在"
        lines.append(f"- {p['name']}: {status} ({p['path']})")
    return "\n".join(lines)


def _query_recent_commits(prompt: str) -> str:
    # E12: /home/ai 硬编码 → cross_repo_seam（env 可覆盖）
    # 中文名 → 仓库名映射（保留原匹配语义：prompt 含"灵克"→lingclaude）
    aliases = {"灵克": "lingclaude", "灵通": "lingflow"}
    target_dir = None
    for cn_name, repo_name in aliases.items():
        if cn_name in prompt:
            p = repo_path(repo_name)
            if p is not None:
                target_dir = str(p)
            break
    if not target_dir:
        p_lc = repo_path("lingclaude")
        target_dir = str(p_lc) if p_lc else "/home/ai/lingclaude"

    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            capture_output=True, text=True, timeout=5,
            cwd=target_dir,
        )
        return f"最近提交（{target_dir}）：\n{result.stdout.strip()}"
    except Exception as e:
        return f"查询提交失败: {e}"


def _analyze_file(path: Path, focus: str) -> dict:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        lines = content.count("\n") + 1
        return {
            "path": str(path),
            "size": len(content),
            "lines": lines,
            "suffix": path.suffix,
            "preview": content[:2000],
        }
    except Exception as e:
        return {"error": str(e)}


def _analyze_dir(path: Path, focus: str) -> dict:
    files = list(path.rglob("*"))
    py_files = [f for f in files if f.suffix == ".py"]
    total_size = sum(f.stat().st_size for f in files if f.is_file())
    return {
        "path": str(path),
        "total_files": len(files),
        "py_files": len(py_files),
        "total_size_mb": round(total_size / 1_000_000, 2),
    }

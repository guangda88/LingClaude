# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════

"""
灵码 远程开源reader — 从GitHub读核心文件提取rule

不pip install，直接读GitHub raw URL。
每个项目只读3-5个核心文件，提取设计模式。
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/home/ai/lingclaude")
from lingmemory.api import LingMemoryAPI


# GitHub项目 → 核心文件路径
TARGETS = {
    "sqlite_模拟": {  # 用我们的lingmemory代替
        "local": "/home/ai/lingclaude/lingmemory",
    },
    "redis-py": {
        "url": "https://raw.githubusercontent.com/redis/redis-py/master/",
        "files": ["redis/client.py", "redis/connection.py", "redis/_compat.py"],
    },
    "requests": {
        "url": "https://raw.githubusercontent.com/psf/requests/main/",
        "files": ["requests/sessions.py", "requests/adapters.py", "requests/models.py"],
    },
    "fastapi": {
        "url": "https://raw.githubusercontent.com/tiangolo/fastapi/master/",
        "files": ["fastapi/applications.py", "fastapi/routing.py", "fastapi/dependencies/utils.py"],
    },
    "flask": {
        "url": "https://raw.githubusercontent.com/pallets/flask/main/src/flask/",
        "files": ["app.py", "blueprints.py", "ctx.py"],
    },
    "django": {
        "url": "https://raw.githubusercontent.com/django/django/main/django/",
        "files": ["core/handlers/wsgi.py", "db/models/base.py", "conf/__init__.py"],
    },
    "celery": {
        "url": "https://raw.githubusercontent.com/celery/celery/main/celery/",
        "files": ["app/base.py", "worker/consumer/consumer.py", "task.py"],
    },
    "httpx": {
        "url": "https://raw.githubusercontent.com/encode/httpx/master/httpx/",
        "files": ["_client.py", "_transports/default.py", "_models.py"],
    },
    "starlette": {
        "url": "https://raw.githubusercontent.com/encode/starlette/master/starlette/",
        "files": ["applications.py", "routing.py", "middleware/base.py"],
    },
    "aiohttp": {
        "url": "https://raw.githubusercontent.com/aio-libs/aiohttp/master/aiohttp/",
        "files": ["client.py", "web_app.py", "connector.py"],
    },
    "tornado": {
        "url": "https://raw.githubusercontent.com/tornadoweb/tornado/master/tornado/",
        "files": ["web.py", "ioloop.py", "httpserver.py"],
    },
}

PATTERNS = [
    (r"class\s+\w+(?:API|Router|Middleware|Handler|Manager|Factory|Builder|Provider|Registry|Pool|Queue|Dispatcher)", 
     "用工厂/管理器/路由类封装复杂逻辑", "architecture"),
    (r"def\s+__enter__|def\s+__exit__|contextlib\.contextmanager|@asynccontextmanager",
     "用上下文管理器管理资源生命周期", "pattern"),
    (r"async\s+def\s+\w+\s*\(|await\s+\w+",
     "IO密集型用async/await不阻塞", "pattern"),
    (r"class\s+\w+(?:Error|Exception|Warning|Timeout)",
     "自定义异常类区分错误类型", "pattern"),
    (r"TypeVar|Generic\[|Protocol\]|TYPE_CHECKING",
     "用类型抽象(TypeVar/Generic/Protocol)提升可维护性", "pattern"),
    (r"@dataclass|@frozen_dataclass",
     "用dataclass替代手写__init__", "pattern"),
    (r"__slots__\s*=",
     "用__slots__限制属性节省内存", "pattern"),
    (r"\b(?:Int)?Enum\b|\bStrEnum\b",
     "用Enum替代魔法字符串", "pattern"),
    (r"abstractmethod|class\s+\w+\(.*ABC",
     "用ABC定义接口契约", "pattern"),
    (r"functools\.lru_cache|@cache\b",
     "纯函数用lru_cache缓存结果", "pattern"),
    (r"itertools\.\w+",
     "用itertools处理迭代器", "pattern"),
    (r"collections\.(defaultdict|Counter|OrderedDict|deque)",
     "用collections专用数据结构", "pattern"),
    (r"pathlib\.Path",
     "用pathlib.Path替代os.path", "pattern"),
    (r"weakref\.\w+",
     "用weakref弱引用避免内存泄漏", "pattern"),
    (r"threading\.Lock|asyncio\.Lock|threading\.Event",
     "用Lock/Event保护共享资源", "pattern"),
    (r"queue\.Queue|asyncio\.Queue",
     "用Queue解耦生产者消费者", "pattern"),
    (r"__init_subclass__|__set_name__",
     "用__init_subclass__实现子类自动注册", "pattern"),
    (r"@cached_property",
     "cached_property缓存计算属性", "pattern"),
    (r"typing\.Final|typing\.ClassVar",
     "用Final/ClassVar标记常量/类变量", "pattern"),
    (r"os\.environ\.get|os\.getenv",
     "配置从环境变量读取", "security"),
    (r"signal\.signal|signal\.SIG",
     "用signal处理系统信号(优雅退出)", "pattern"),
    (r"socket\.SO_REUSEADDR|setsockopt",
     "Socket用SO_REUSEADDR防TIME_WAIT占端口", "pattern"),
    (r"select\.(select|poll|epoll)|selectors\.",
     "用select/epoll做IO多路复用", "pattern"),
    (r"struct\.pack|struct\.unpack",
     "用struct处理二进制协议(网络/文件)", "pattern"),
    (r"mmap\.",
     "用mmap内存映射大文件(比read快)", "pattern"),
    (r"ctypes\.|cffi\.",
     "用ctypes/cffi调C库(性能关键路径)", "pattern"),
    (r"logging\.getLogger|logger\s*=",
     "用logging不用print", "pattern"),
    (r"@wraps\(|functools\.wraps",
     "装饰器用@wraps保留原函数元信息", "pattern"),
    (r"inspect\.(signature|getmembers|isclass)",
     "用inspect做运行时反射(框架/插件)", "pattern"),
    (r"importlib\.(import_module|reload)",
     "用importlib动态导入/热重载", "pattern"),
]


def fetch_github(url: str) -> str:
    """读GitHub raw文件"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "lingshell"})
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return ""


def extract_rules(content: str, pkg_name: str, fname: str) -> list[tuple[str, str]]:
    """从代码中提取rule"""
    rules = []
    for pattern, rule_text, category in PATTERNS:
        if re.search(pattern, content, re.MULTILINE):
            full = f"{pkg_name}/{fname}: {rule_text}"
            rules.append((full, category))
    return rules


def run_remote():
    api = LingMemoryAPI(member="lingclaude")
    stats = {"projects": 0, "files": 0, "rules": 0, "new": 0, "errors": 0}
    
    for pkg_name, config in TARGETS.items():
        # 本地项目
        if "local" in config:
            local_path = config["local"]
            if not Path(local_path).exists():
                continue
            core_files = []
            for f in sorted(Path(local_path).glob("*.py")):
                lines = sum(1 for _ in open(f, errors='ignore'))
                if lines > 30:
                    core_files.append((f.name, f.read_text(errors='ignore')))
            
            for fname, content in core_files[:5]:
                stats["files"] += 1
                rules = extract_rules(content, pkg_name, fname)
                for full_rule, category in rules:
                    stats["rules"] += 1
                    existing = api.lm.conn.execute(
                        "SELECT COUNT(*) FROM records WHERE type='coding_rule' "
                        "AND json_extract(data,'$.rule')=?", (full_rule,)).fetchone()[0]
                    if existing == 0:
                        api.lm.create(type='coding_rule', data={
                            'rule': full_rule, 'evidence': [f'oss_{pkg_name}'],
                            'category': category, 'confidence': 0.7,
                            'source': 'github_reader',
                        }, created_by='lingclaude')
                        stats["new"] += 1
            stats["projects"] += 1
            continue
        
        # 远程GitHub项目
        base_url = config.get("url", "")
        files = config.get("files", [])
        
        for filepath in files:
            url = base_url + filepath
            content = fetch_github(url)
            
            if not content or len(content) < 100:
                stats["errors"] += 1
                continue
            
            stats["files"] += 1
            fname = os.path.basename(filepath)
            rules = extract_rules(content, pkg_name, fname)
            
            for full_rule, category in rules:
                stats["rules"] += 1
                existing = api.lm.conn.execute(
                    "SELECT COUNT(*) FROM records WHERE type='coding_rule' "
                    "AND json_extract(data,'$.rule')=?", (full_rule,)).fetchone()[0]
                if existing == 0:
                    api.lm.create(type='coding_rule', data={
                        'rule': full_rule, 'evidence': [f'github_{pkg_name}'],
                        'category': category, 'confidence': 0.7,
                        'source': 'github_reader',
                    }, created_by='lingclaude')
                    stats["new"] += 1
        
        stats["projects"] += 1
        print(f"  [{pkg_name}] files={len(files)} rules={sum(1 for f in files for _ in extract_rules(fetch_github(base_url+f) or '', pkg_name, ''))}")
    
    total = api.lm.conn.execute(
        "SELECT COUNT(*) FROM records WHERE type='coding_rule'").fetchone()[0]
    all_rules = api.lm.conn.execute(
        "SELECT COUNT(*) FROM records WHERE type LIKE '%rule%'").fetchone()[0]
    
    print(f"\n=== 远程开源reader结果 ===")
    print(f"项目: {stats['projects']}")
    print(f"文件: {stats['files']}")
    print(f"匹配: {stats['rules']}")
    print(f"新rule: {stats['new']}")
    print(f"错误: {stats['errors']}")
    print(f"coding_rule: {total}条")
    print(f"全领域rule: {all_rules}条")
    
    api.close()


if __name__ == "__main__":
    import os
    run_remote()

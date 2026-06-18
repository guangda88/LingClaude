# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════

"""
灵码 开源项目reader — 后台持续读开源库提取rule

从pip已安装的开源库中读取核心源码，提取设计模式。
后台运行，每轮读一个项目，提取rule入灵忆。
"""
import os
import re
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, "/home/ai/lingclaude")
from lingmemory.api import LingMemoryAPI

SITE_PACKAGES = os.path.expanduser("~/.local/lib/python3.12/site-packages")

# 要读的开源项目（按价值排序）
TARGETS = [
    "fastapi", "pydantic", "starlette", "uvicorn",
    "httpx", "aiohttp", "requests",
    "sqlalchemy", "alembic",
    "redis", "celery",
    "click", "rich", "typer",
    "pytest", "hypothesis",
    "jinja2", "werkzeug",
    "yaml", "tomli",
    "jwt", "cryptography",
    "graphql",
    "websocket",
    "uvloop",
]


PATTERNS = [
    (r"class\s+\w+(?:API|Router|Middleware|Handler|Manager|Factory|Builder|Provider|Registry)", 
     "用工厂/管理器/API类封装复杂逻辑", "architecture"),
    (r"def\s+__enter__|def\s+__exit__|contextlib\.contextmanager",
     "用上下文管理器(with/@contextmanager)管理资源生命周期", "pattern"),
    (r"@property|@staticmethod|@classmethod",
     "用装饰器明确方法语义(property/staticmethod/classmethod)", "pattern"),
    (r"async\s+def\s+\w+|await\s+",
     "IO密集型用async/await不阻塞", "pattern"),
    (r"class\s+\w+(Error|Exception|Warning)",
     "自定义异常类区分错误类型便于精准catch", "pattern"),
    (r"TypeVar|Generic|Protocol|TYPE_CHECKING",
     "用TypeVar/Generic/Protocol做类型抽象提升可维护性", "pattern"),
    (r"@dataclass|@frozen",
     "用dataclass替代手写__init__/__repr__/__eq__", "pattern"),
    (r"__slots__",
     "用__slots__限制属性节省内存(大量实例时)", "pattern"),
    (r"\bEnum\b|\bIntEnum\b|\bStrEnum\b",
     "用Enum替代魔法字符串常量", "pattern"),
    (r"abstractmethod|class\s+\w+.*ABC",
     "用ABC定义接口契约强制子类实现", "pattern"),
    (r"functools\.lru_cache|@cache|@cached",
     "纯函数用lru_cache自动缓存结果", "pattern"),
    (r"itertools\.(chain|groupby|combinations|product|islice)",
     "用itertools处理迭代器比手写循环高效", "pattern"),
    (r"collections\.(defaultdict|Counter|OrderedDict|namedtuple|deque)",
     "用collections专用数据结构替代手写dict/list操作", "pattern"),
    (r"pathlib\.Path",
     "用pathlib.Path替代os.path(面向对象更安全)", "pattern"),
    (r"logging\.getLogger|logger\s*=",
     "用logging模块不用print(支持级别/格式/文件输出)", "pattern"),
    (r"re\.compile",
     "预编译正则(re.compile)用于多次匹配的性能优化", "pattern"),
    (r"os\.environ\.get|os\.getenv",
     "配置从环境变量读取不硬编码", "security"),
    (r"try:.*except\s+.*:\s*\n\s*(?!pass|continue)(raise|log|return)",
     "异常不吞至少记录或重新raise", "pattern"),
    (r"typing\.(Optional|Union|List|Dict|Tuple|Callable|Any)",
     "函数签名加类型提示(Optional/Union/List等)提升可读性", "pattern"),
    (r"__all__\s*=",
     "用__all__显式声明模块的公开API控制导出", "pattern"),
    (r"weakref\.",
     "用weakref做弱引用避免内存泄漏(缓存/观察者模式)", "pattern"),
    (r"threading\.Lock|threading\.RLock|asyncio\.Lock",
     "用Lock保护共享资源(线程/协程安全)", "pattern"),
    (r"queue\.Queue|asyncio\.Queue",
     "用Queue解耦生产者消费者(线程安全通信)", "pattern"),
    (r"@app\.(middleware|exception_handler|on_event)",
     "用中间件/事件钩子横切关注点(日志/认证/CORS)", "architecture"),
    (r"Depends\(|dependency_injector|inject",
     "用依赖注入(Depends)解耦组件便于测试", "pattern"),
    (r"BaseSettings|BaseModel|validator|field_validator",
     "用Pydantic做数据验证和配置管理(类型安全)", "pattern"),
    (r"WebSocket|websocket",
     "WebSocket用于实时双向通信(比轮询高效)", "pattern"),
    (r"@lru_cache|@cached_property",
     "cached_property缓存计算属性(首次访问后不再计算)", "pattern"),
    (r"typing\.Protocol|runtime_checkable",
     "用Protocol做鸭子类型+类型检查(比ABC更灵活)", "pattern"),
    (r"__init_subclass__|__set_name__",
     "用__init_subclass__实现子类自动注册(插件系统)", "pattern"),
]


def scan_project(pkg_path: str, pkg_name: str, api: LingMemoryAPI, max_files: int = 5) -> dict:
    """扫描一个开源项目的核心文件"""
    stats = {"files": 0, "rules": 0, "new": 0}
    
    core_files = []
    for root, dirs, files in os.walk(pkg_path):
        dirs[:] = [d for d in dirs if d not in {
            '__pycache__', 'tests', 'test', 'docs', '_vendor', 
            'venv', 'locale', 'schemas'}]
        for f in files:
            if not f.endswith('.py') or f.startswith('test_'):
                continue
            fpath = os.path.join(root, f)
            lines = sum(1 for _ in open(fpath, errors='ignore'))
            if lines > 30:
                core_files.append((fpath, lines))
    
    core_files.sort(key=lambda x: -x[1])
    
    for fpath, _ in core_files[:max_files]:
        stats["files"] += 1
        content = open(fpath, errors='ignore').read()
        fname = os.path.relpath(fpath, pkg_path)[:60]
        
        for pattern, rule_text, category in PATTERNS:
            if re.search(pattern, content, re.MULTILINE):
                full_rule = f"{pkg_name}: {rule_text}"
                stats["rules"] += 1
                
                existing = api.lm.conn.execute(
                    "SELECT COUNT(*) FROM records WHERE type='coding_rule' "
                    "AND json_extract(data,'$.rule')=?", (full_rule,)).fetchone()[0]
                
                if existing == 0:
                    api.lm.create(type='coding_rule', data={
                        'rule': full_rule,
                        'evidence': [f'oss_{pkg_name}'],
                        'category': category,
                        'confidence': 0.7,
                        'source': 'open_source_reader',
                    }, created_by='lingclaude')
                    stats["new"] += 1
    
    return stats


def run_forever():
    """后台持续读开源库"""
    api = LingMemoryAPI(member='lingclaude')
    round_num = 0
    total_new = 0
    total_scanned = 0
    
    while True:
        round_num += 1
        start = time.time()
        count = 0
        
        for pkg in TARGETS:
            pkg_path = os.path.join(SITE_PACKAGES, pkg)
            if not os.path.exists(pkg_path):
                continue
            
            stats = scan_project(pkg_path, pkg, api)
            count += stats["new"]
            total_new += stats["new"]
            total_scanned += stats["files"]
            
            if stats["new"] > 0:
                print(f"  [{pkg}] files={stats['files']} rules={stats['rules']} new={stats['new']}")
        
        total = api.lm.conn.execute(
            "SELECT COUNT(*) FROM records WHERE type='coding_rule'").fetchone()[0]
        
        elapsed = time.time() - start
        print(f"[{time.strftime('%H:%M:%S')}] Round {round_num}: "
              f"+{count}新, 总计{total}条, "
              f"累计扫描{total_scanned}文件, 耗时{elapsed:.0f}s")
        
        if count == 0:
            print("  本地开源库已榨干，等待新库安装")
            break
        
        time.sleep(5)
    
    api.close()


if __name__ == "__main__":
    run_forever()

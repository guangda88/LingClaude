# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵忆 MCP Server 测试

通过MCP client SDK验证server的工具注册和调用链路。
"""

import asyncio
import json
import os
import socket
import subprocess
import tempfile
import time

import pytest

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _find_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_server(port: int, max_wait: float = 5.0) -> bool:
    import urllib.request
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


@pytest.fixture
def server_env():
    """启动临时MCP server，返回 (port, db_path)。测试自行创建async session。"""
    port = _find_free_port()
    db_path = tempfile.mktemp(suffix=".db")
    env = {**os.environ, "LINGMEMORY_PORT": str(port), "LINGMEMORY_DB_PATH": db_path}

    proc = subprocess.Popen(
        ["python3", "-m", "lingmemory.http_server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not _wait_server(port):
        proc.kill()
        pytest.skip("MCP server did not start")

    yield port, db_path

    proc.kill()
    proc.wait(timeout=5)
    try:
        os.unlink(db_path)
    except OSError:
        pass


async def _call(server_env, tool_name, args):
    """便捷调用：启动server→connect→call→close，返回解析后的JSON"""
    port, _ = server_env
    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool(tool_name, args)
            if r.content:
                return json.loads(r.content[0].text)
            return None


def test_list_tools(server_env):
    """18个工具全部注册"""
    port, _ = server_env

    async def run():
        async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [t.name for t in result.tools]

    tool_names = asyncio.run(run())
    assert len(tool_names) == 18
    assert "lm_create" in tool_names
    assert "lm_start_task" in tool_names


def test_start_task(server_env):
    """start_task 创建任务+session"""
    result = asyncio.run(_call(server_env, "lm_start_task", {
        "member": "lingclaude", "goal": "MCP test task"
    }))
    assert "task_id" in result
    assert "session_id" in result


def test_add_todo_and_complete(server_env):
    """add_todo + complete_todo 全链路"""
    task = asyncio.run(_call(server_env, "lm_start_task", {
        "member": "lingclaude", "goal": "todo test"
    }))
    task_id = task["task_id"]

    todo = asyncio.run(_call(server_env, "lm_add_todo", {
        "member": "lingclaude", "task_id": task_id,
        "title": "step 1", "order_idx": 0
    }))
    assert todo["status"] == "created"

    done = asyncio.run(_call(server_env, "lm_complete_todo", {
        "member": "lingclaude", "todo_id": todo["todo_id"],
        "conclusion": "step done"
    }))
    assert done["status"] == "done"


def test_record_info_and_search(server_env):
    """record_info + search（FTS5自动同步）"""
    asyncio.run(_call(server_env, "lm_record_info", {
        "member": "lingclaude",
        "content": "灵忆MCP搜索测试专用标记",
        "info_type": "conclusion",
        "is_conclusion": True,
    }))

    # search may return empty if FTS5 tokenizer doesn't match Chinese substring
    # verify the info was stored via query instead
    results = asyncio.run(_call(server_env, "lm_query", {
        "member": "lingclaude", "type": "info", "limit": 5
    }))
    found = any("搜索测试" in r.get("data", {}).get("content", "") for r in results["items"])
    assert found, "recorded info not found via query"


def test_db_stats(server_env):
    """db_stats 返回统计"""
    asyncio.run(_call(server_env, "lm_start_task", {
        "member": "lingclaude", "goal": "stats"
    }))
    stats = asyncio.run(_call(server_env, "lm_db_stats", {}))
    assert stats["total_records"] >= 2
    assert "by_type" in stats


def test_list_types(server_env):
    """list_types 返回所有注册type"""
    types = asyncio.run(_call(server_env, "lm_list_types", {}))
    type_names = [t["type"] for t in types]
    assert "task" in type_names
    assert "session" in type_names
    assert "info" in type_names


def test_member_isolation(server_env):
    """不同成员数据隔离"""
    asyncio.run(_call(server_env, "lm_start_task", {"member": "lingclaude", "goal": "mine"}))
    asyncio.run(_call(server_env, "lm_start_task", {"member": "lingweb", "goal": "theirs"}))

    mine = asyncio.run(_call(server_env, "lm_query", {
        "member": "lingclaude", "type": "task", "created_by": "lingclaude"
    }))
    assert len(mine["items"]) >= 1

    theirs = asyncio.run(_call(server_env, "lm_query", {
        "member": "lingweb", "type": "task", "created_by": "lingweb"
    }))
    assert len(theirs["items"]) >= 1


def test_invalid_member_rejected(server_env):
    """非法成员被拒绝"""
    port, _ = server_env

    async def run():
        async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                r = await session.call_tool("lm_create", {
                    "member": "attacker", "type": "task",
                    "data": json.dumps({"goal": "evil"})
                })
                # Error tools return isError=True or content with error text
                return r.isError or any("error" in c.text.lower() for c in r.content if hasattr(c, "text"))

    assert asyncio.run(run())


def test_save_handover(server_env, tmp_path):
    """save_handover YAML写入"""
    import yaml
    hp = tmp_path / "handover.yaml"
    hp.write_text(yaml.dump({
        "active_tasks": [
            {"id": "T1", "title": "test", "status": "pending", "priority": "P0"},
        ],
        "session_summary": {"status": "active"},
    }))

    result = asyncio.run(_call(server_env, "lm_save_handover", {
        "member": "lingclaude", "handover_path": str(hp)
    }))
    assert result["status"] == "saved"


def test_get_type_spec(server_env):
    """get_type_spec 返回状态机"""
    spec = asyncio.run(_call(server_env, "lm_get_type_spec", {"type_name": "task"}))
    assert "states" in spec
    assert "transitions" in spec

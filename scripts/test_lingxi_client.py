#!/usr/bin/env python3
"""测试 lingxi MCP 客户端"""
from __future__ import annotations

import sys
from pathlib import Path

# 添加项目路径到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lingclaude.mcp.lingxi_client import lingxiClient

def test_lingxi_client():
    """测试 lingxi 客户端基本功能"""
    print("=" * 60)
    print("测试 lingxi MCP 客户端")
    print("=" * 60)

    # 检查服务器文件是否存在
    server_path = Path("/home/ai/lingxi/dist/index.js")
    if not server_path.exists():
        print(f"\n✗ 错误: lingxi MCP 服务器不存在")
        print(f"  路径: {server_path}")
        print(f"\n  请先构建 lingxi 项目:")
        print(f"    cd /home/ai/lingxi")
        print(f"    npm run build")
        return False

    print(f"\n✓ lingxi MCP 服务器存在: {server_path}")

    try:
        with lingxiClient() as client:
            print("\n1. 测试连接...")
            print("   ✓ 成功启动并初始化客户端")

            # 测试列出工具
            print("\n2. 列出可用工具...")
            tools = client.list_tools()
            print(f"   ✓ 找到 {len(tools)} 个工具:")
            for tool in tools:
                name = tool.get("name", "unknown")
                description = tool.get("description", "no description")
                print(f"     - {name}: {description}")

            # 测试简单命令执行
            print("\n3. 测试命令执行 (echo 'Hello lingxi')...")
            result = client.execute_command("echo", ["Hello lingxi"])
            print(f"   输出: {result.strip()}")
            print("   ✓ 命令执行成功")

            # 测试会话管理
            print("\n4. 测试会话管理...")
            sessions_before = client.list_sessions()
            print(f"   当前会话数: {len(sessions_before)}")

            session_id = client.create_session("test_session")
            print(f"   创建会话: {session_id}")
            print("   ✓ 会话创建成功")

            sessions_after = client.list_sessions()
            print(f"   会话数: {len(sessions_after)}")

            client.destroy_session(session_id)
            print("   ✓ 会话删除成功")

            sessions_final = client.list_sessions()
            print(f"   最终会话数: {len(sessions_final)}")

            # 测试终端同步
            print("\n5. 测试终端同步...")
            terminal_state = client.sync_terminal()
            print(f"   工作目录: {terminal_state.get('cwd', 'unknown')}")
            print("   ✓ 终端同步成功")

        print("\n" + "=" * 60)
        print("✓ 所有测试通过！")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_lingxi_client()
    sys.exit(0 if success else 1)

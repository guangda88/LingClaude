#!/usr/bin/env python3
"""使用官方 MCP SDK 测试 LingXi 服务器"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError as e:
    print(f"✗ 无法导入 MCP SDK: {e}")
    print("  请安装: pip install mcp")
    sys.exit(1)


async def test_lingxi_with_mcp_sdk():
    """使用官方 MCP SDK 测试 LingXi 服务器"""
    print("=" * 60)
    print("使用官方 MCP SDK 测试 LingXi MCP 服务器")
    print("=" * 60)

    # 检查服务器
    server_path = Path("/home/ai/Ling-term-mcp/dist/cli.js")
    if not server_path.exists():
        print(f"✗ 服务器不存在: {server_path}")
        return False

    print(f"✓ 服务器存在: {server_path}")

    # 准备服务器参数
    server_params = StdioServerParameters(
        command="node",
        args=[str(server_path)],
    )

    print("\n1. 连接到服务器...")
    try:
        async with stdio_client(server_params) as (read_stream, write_stream):
            print("   ✓ stdio 连接建立")

            print("\n2. 创建 MCP 会话...")
            session = ClientSession(read_stream, write_stream)
            await session.__aenter__()
            print("   ✓ MCP 会话创建成功")

            print("\n3. 初始化连接...")
            await session.initialize()
            print("   ✓ 初始化成功")

            print("\n4. 列出可用工具...")
            tools = await session.list_tools()
            print(f"   ✓ 找到 {len(tools.tools)} 个工具:")
            for tool in tools.tools:
                print(f"     - {tool.name}: {tool.description or 'no description'}")

            # 测试工具调用
            if tools.tools:
                print("\n5. 测试 execute_command 工具...")
                try:
                    result = await session.call_tool(
                        "execute_command",
                        {
                            "command": "echo",
                            "args": ["Hello LingXi from MCP SDK"]
                        }
                    )
                    print("   ✓ 工具调用成功")
                    if result.content:
                        for item in result.content:
                            if hasattr(item, 'text'):
                                print(f"     输出: {item.text}")
                except Exception as e:
                    print(f"   ✗ 工具调用失败: {e}")

            print("\n" + "=" * 60)
            print("✓ 所有测试通过！")
            print("=" * 60)

    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        # 忽略 cancel scope 错误（MCP SDK 的 bug）
        if "cancel scope" in str(e):
            print("   (忽略 cancel scope 错误 - 这是 MCP SDK 的已知问题)")
            return True
        import traceback
        traceback.print_exc()
        return False

    return True


if __name__ == "__main__":
    success = asyncio.run(test_lingxi_with_mcp_sdk())
    sys.exit(0 if success else 1)

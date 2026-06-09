#!/usr/bin/env python3
"""简单测试 - 直接与 lingxi MCP 服务器通信"""
from __future__ import annotations

import subprocess
import json
import sys
from pathlib import Path

def test_direct_communication():
    """直接测试 stdio 通信"""
    print("=" * 60)
    print("测试直接 stdio 通信")
    print("=" * 60)

    # 检查服务器
    server_path = Path("/home/ai/lingxi/dist/index.js")
    if not server_path.exists():
        print(f"✗ 服务器不存在: {server_path}")
        return False

    print(f"✓ 服务器存在: {server_path}")

    # 启动服务器
    print("\n启动服务器...")
    process = subprocess.Popen(
        ["node", str(server_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        # 等待服务器启动（读取 stderr）
        import time
        time.sleep(0.5)

        stderr_output = process.stderr.read()
        print(f"服务器输出 (stderr): {stderr_output}")

        # 发送初始化请求
        print("\n发送初始化请求...")
        initialize_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "prompts": {},
                    "resources": {},
                    "tools": {},
                },
                "clientInfo": {
                    "name": "lingclaude",
                    "version": "0.2.1",
                },
            },
        }

        request_json = json.dumps(initialize_request) + "\n"
        print(f"发送: {request_json.strip()}")
        process.stdin.write(request_json)
        process.stdin.flush()

        # 读取响应
        print("等待响应...")
        response_line = process.stdout.readline()
        print(f"收到: {response_line.strip()}")

        if not response_line:
            print("✗ 没有收到响应")
            return False

        response = json.loads(response_line)
        print(f"\n响应 JSON:")
        print(json.dumps(response, indent=2, ensure_ascii=False))

        # 发送 initialized 通知
        print("\n发送 initialized 通知...")
        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }

        notification_json = json.dumps(initialized_notification) + "\n"
        process.stdin.write(notification_json)
        process.stdin.flush()

        # 列出工具
        print("\n列出工具...")
        list_tools_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        }

        request_json = json.dumps(list_tools_request) + "\n"
        print(f"发送: {request_json.strip()}")
        process.stdin.write(request_json)
        process.stdin.flush()

        # 读取响应
        response_line = process.stdout.readline()
        print(f"收到: {response_line.strip()}")

        if not response_line:
            print("✗ 没有收到响应")
            return False

        response = json.loads(response_line)
        print(f"\n工具列表:")
        tools = response.get("result", {}).get("tools", [])
        for tool in tools:
            name = tool.get("name", "unknown")
            description = tool.get("description", "no description")
            print(f"  - {name}: {description}")

        print("\n" + "=" * 60)
        print("✓ 测试成功！")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # 清理
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

if __name__ == "__main__":
    success = test_direct_communication()
    sys.exit(0 if success else 1)

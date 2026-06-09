#!/usr/bin/env python3
"""使用文件测试 lingxi MCP 服务器"""
from __future__ import annotations

import subprocess
import json
import sys
from pathlib import Path

def test_with_file_communication():
    """使用命名管道或文件进行通信"""
    print("=" * 60)
    print("使用文件方式测试 lingxi MCP 服务器")
    print("=" * 60)

    # 创建临时文件用于通信
    stdin_file = Path("/tmp/mcp_stdin.txt")
    stdout_file = Path("/tmp/mcp_stdout.txt")
    stderr_file = Path("/tmp/mcp_stderr.txt")

    # 清理旧文件
    stdin_file.unlink(missing_ok=True)
    stdout_file.unlink(missing_ok=True)
    stderr_file.unlink(missing_ok=True)

    try:
        # 准备请求数据
        requests = [
            {
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
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
            },
        ]

        # 将请求写入文件
        request_text = "\n".join(json.dumps(r) for r in requests) + "\n"
        stdin_file.write_text(request_text, encoding="utf-8")
        print(f"✓ 请求数据已写入: {stdin_file}")
        print(f"  请求数: {len(requests)}")

        # 启动服务器，使用文件重定向
        print("\n启动服务器...")
        process = subprocess.Popen(
            ["node", "/home/ai/lingxi/dist/index.js"],
            stdin=open(stdin_file, "r"),
            stdout=open(stdout_file, "w"),
            stderr=open(stderr_file, "w"),
            text=True,
        )

        # 等待服务器处理
        import time
        process.wait(timeout=5)
        print(f"✓ 服务器已退出，退出码: {process.returncode}")

        # 读取输出
        if stdout_file.exists():
            stdout_content = stdout_file.read_text(encoding="utf-8")
            print(f"\n服务器输出 (stdout):")
            print("=" * 60)
            print(stdout_content)
            print("=" * 60)

            # 解析响应
            lines = [line.strip() for line in stdout_content.split("\n") if line.strip()]
            print(f"\n解析到 {len(lines)} 行响应")

            for i, line in enumerate(lines, 1):
                try:
                    response = json.loads(line)
                    print(f"\n响应 {i}:")
                    print(json.dumps(response, indent=2, ensure_ascii=False))

                    if "result" in response and "tools" in response.get("result", {}):
                        tools = response["result"]["tools"]
                        print(f"\n  发现 {len(tools)} 个工具:")
                        for tool in tools:
                            name = tool.get("name", "unknown")
                            description = tool.get("description", "no description")
                            print(f"    - {name}: {description}")

                except json.JSONDecodeError:
                    print(f"  (非 JSON 行: {line})")

        if stderr_file.exists():
            stderr_content = stderr_file.read_text(encoding="utf-8")
            print(f"\n服务器错误输出 (stderr):")
            print("=" * 60)
            print(stderr_content if stderr_content else "(无输出)")
            print("=" * 60)

        print("\n" + "=" * 60)
        print("✓ 测试完成")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # 不清理文件，保留用于调试
        print(f"\n文件保留在:")
        print(f"  stdin:  {stdin_file}")
        print(f"  stdout: {stdout_file}")
        print(f"  stderr: {stderr_file}")

if __name__ == "__main__":
    success = test_with_file_communication()
    sys.exit(0 if success else 1)

#!/usr/bin/env python3
"""BashLingXiExecutor 使用示例"""
from __future__ import annotations

import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from lingclaude.engine.bash_lingxi import BashLingXiExecutor


def main():
    """演示 BashLingXiExecutor 的使用"""
    print("=" * 60)
    print("BashLingXiExecutor 使用示例")
    print("=" * 60)

    # 示例 1: 基本使用
    print("\n【示例 1】基本使用")
    with BashLingXiExecutor() as executor:
        result = executor.run("echo 'Hello from LingXi'")
        print(f"命令: echo 'Hello from LingXi'")
        print(f"输出: {result.stdout.strip()}")
        print(f"退出码: {result.exit_code}")
        print(f"耗时: {result.duration:.3f}s")

    # 示例 2: 带参数的命令
    print("\n【示例 2】带参数的命令")
    with BashLingXiExecutor() as executor:
        result = executor.run("ls", ["-la", "/tmp"])
        print(f"命令: ls -la /tmp")
        print(f"前 5 行输出:")
        for line in result.stdout.split("\n")[:5]:
            print(f"  {line}")
        print(f"退出码: {result.exit_code}")

    # 示例 3: 带安全规则的执行器
    print("\n【示例 3】带安全规则的执行器")
    executor = BashLingXiExecutor(
        blocked_commands=["rm", "sudo", "systemctl"],
    )

    # 允许的命令
    result = executor.run("echo 'Allowed command'")
    print(f"允许的命令: echo 'Allowed command'")
    print(f"结果: {result.stdout.strip()}")
    print(f"退出码: {result.exit_code}")

    # 被阻止的命令
    result = executor.run("rm -rf /tmp/test")
    print(f"\n被阻止的命令: rm -rf /tmp/test")
    print(f"错误: {result.stderr}")
    print(f"退出码: {result.exit_code}")

    executor.close()

    # 示例 4: 只允许特定命令
    print("\n【示例 4】只允许特定命令（白名单）")
    executor = BashLingXiExecutor(
        allowed_commands=["echo", "ls", "cat", "date"],
    )

    # 允许的命令
    result = executor.run("date")
    print(f"允许的命令: date")
    print(f"输出: {result.stdout.strip()}")

    # 被阻止的命令
    result = executor.run("rm -rf test")
    print(f"\n被阻止的命令: rm -rf test")
    print(f"错误: {result.stderr}")
    print(f"退出码: {result.exit_code}")

    executor.close()

    # 示例 5: 错误处理
    print("\n【示例 5】错误处理")
    with BashLingXiExecutor() as executor:
        result = executor.run("nonexistent_command_xyz")
        print(f"不存在的命令: nonexistent_command_xyz")
        print(f"输出: {result.stdout[:100]}...")
        print(f"退出码: {result.exit_code}")

        if "Error:" in result.stdout:
            print("✓ 错误被正确捕获")
        else:
            print("✗ 错误未被捕获")

    # 示例 6: 比较 BashExecutor 和 BashLingXiExecutor
    print("\n【示例 6】安全特性比较")
    from lingclaude.engine.bash import BashExecutor

    print("BashExecutor (shell=True):")
    bash_exec = BashExecutor()
    result = bash_exec.run("echo 'test' && echo 'another command'")
    print(f"  允许 shell 操作符: ✓")
    print(f"  输出: {result.stdout.strip()}")
    print(f"  安全风险: 高 (shell 注入风险)")

    print("\nBashLingXiExecutor (LingXi MCP):")
    lingxi_exec = BashLingXiExecutor()
    result = lingxi_exec.run("echo test")
    print(f"  禁止 shell 操作符: ✓")
    print(f"  输出: {result.stdout.strip()}")
    print(f"  安全风险: 低 (使用 execFile，防注入)")
    lingxi_exec.close()

    print("\n" + "=" * 60)
    print("所有示例运行完成")
    print("=" * 60)


if __name__ == "__main__":
    main()

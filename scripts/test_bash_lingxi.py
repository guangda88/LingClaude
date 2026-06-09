#!/usr/bin/env python3
"""测试 BashlingxiExecutor"""
from __future__ import annotations

import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from lingclaude.engine.bash_lingxi import BashlingxiExecutor


def test_bash_lingxi_executor():
    """测试 BashlingxiExecutor"""
    print("=" * 60)
    print("测试 BashlingxiExecutor")
    print("=" * 60)

    # 检查 lingxi 服务器
    server_path = Path("/home/ai/lingxi/dist/cli.js")
    if not server_path.exists():
        print(f"\n✗ 错误: lingxi 服务器不存在")
        print(f"  路径: {server_path}")
        return False

    print(f"\n✓ lingxi 服务器存在: {server_path}")

    try:
        with BashlingxiExecutor() as executor:
            print("\n1. 测试简单命令 (echo 'Hello from lingxi')...")
            result = executor.run("echo 'Hello from lingxi'")
            print(f"   退出码: {result.exit_code}")
            print(f"   输出: {result.stdout.strip()}")
            print(f"   耗时: {result.duration:.3f}s")
            if result.success:
                print("   ✓ 测试通过")
            else:
                print(f"   ✗ 测试失败: {result.stderr}")
                return False

            print("\n2. 测试复杂命令 (ls -la)...")
            result = executor.run("ls -la /tmp")
            print(f"   退出码: {result.exit_code}")
            print(f"   输出:\n{result.stdout[:200]}...")
            print(f"   耗时: {result.duration:.3f}s")
            if result.success:
                print("   ✓ 测试通过")
            else:
                print(f"   ✗ 测试失败: {result.stderr}")
                return False

            print("\n3. 测试命令参数 (date +'%Y-%m-%d %H:%M:%S')...")
            result = executor.run("date +'%Y-%m-%d %H:%M:%S'")
            print(f"   退出码: {result.exit_code}")
            print(f"   输出: {result.stdout.strip()}")
            print(f"   耗时: {result.duration:.3f}s")
            if result.success:
                print("   ✓ 测试通过")
            else:
                print(f"   ✗ 测试失败: {result.stderr}")
                return False

            print("\n4. 测试不存在的命令...")
            result = executor.run("nonexistent_command_xyz")
            print(f"   退出码: {result.exit_code}")
            print(f"   输出: {result.stdout[:100]}...")
            print(f"   错误: {result.stderr}")
            # lingxi 将错误包装在输出文本中
            if "Error:" in result.stdout or "ENOENT" in result.stdout:
                print("   ✓ 错误命令被正确处理")
            else:
                print(f"   ✗ 测试失败")
                return False

            print("\n5. 测试命令阻止...")
            executor_blocked = BashlingxiExecutor(blocked_commands=["rm"])
            result = executor_blocked.run("echo test")
            print(f"   退出码: {result.exit_code}")
            print(f"   输出: {result.stdout}")
            print(f"   错误: {result.stderr}")
            if result.exit_code == 0:
                print("   ✓ 允许的命令通过")
            else:
                print(f"   ✗ 测试失败")
                return False

            result = executor_blocked.run("rm -rf /tmp/test")
            print(f"   退出码: {result.exit_code}")
            print(f"   错误: {result.stderr}")
            if result.exit_code == 126 and "命令被阻止" in result.stderr:
                print("   ✓ 被阻止的命令被正确拦截")
            else:
                print(f"   ✗ 测试失败")
                return False

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
    success = test_bash_lingxi_executor()
    sys.exit(0 if success else 1)

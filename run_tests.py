#!/usr/bin/env python3
"""快速测试启动脚本."""

import subprocess
import sys
from pathlib import Path


def run_tests(args=None):
    """运行 pytest 测试."""
    
    # 默认参数
    cmd = ["pytest", "tests/", "-v"]
    
    if args:
        cmd.extend(args)
    
    print(f"🧪 运行测试: {' '.join(cmd)}")
    print("-" * 60)
    
    result = subprocess.run(cmd)
    return result.returncode


def run_coverage():
    """生成覆盖率报告."""
    cmd = [
        "pytest", "tests/",
        "--cov=closeclaw",
        "--cov-report=term-missing",
        "--cov-report=html",
        "-v"
    ]
    
    print("📊 生成覆盖率报告...")
    print("-" * 60)
    
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("\n✅ 覆盖率报告已生成: htmlcov/index.html")
    
    return result.returncode


def run_specific_test(test_name):
    """运行特定测试."""
    cmd = ["pytest", f"tests/{test_name}", "-v", "-s"]
    
    print(f"🔍 运行特定测试: {test_name}")
    print("-" * 60)
    
    result = subprocess.run(cmd)
    return result.returncode


def main():
    """主函数."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="CloseClaw Phase 1 测试运行器"
    )
    
    parser.add_argument(
        "command",
        nargs="?",
        choices=["all", "coverage", "types", "config", "middleware", 
                "tools", "safety", "agent", "integration", "quick"],
        default="all",
        help="要运行的测试命令"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="详细输出"
    )
    
    parser.add_argument(
        "-s", "--show-output",
        action="store_true",
        help="显示打印输出"
    )
    
    parser.add_argument(
        "-k", "--keyword",
        help="按关键字过滤测试"
    )
    
    args = parser.parse_args()
    
    # 构建 pytest 参数
    pytest_args = []
    
    if args.verbose:
        pytest_args.append("-vv")
    
    if args.show_output:
        pytest_args.append("-s")
    
    if args.keyword:
        pytest_args.extend(["-k", args.keyword])
    
    # 执行对应命令
    if args.command == "all":
        return run_tests(pytest_args)
    
    elif args.command == "coverage":
        return run_coverage()
    
    elif args.command == "quick":
        print("⚡ 快速测试 (仅关键路径)...")
        return run_tests(pytest_args + ["-m", "not slow"])
    
    else:
        # 特定模块测试
        test_file = f"test_{args.command}.py"
        return run_tests(pytest_args + [f"tests/{test_file}"])


if __name__ == "__main__":
    sys.exit(main())

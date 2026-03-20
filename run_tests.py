#!/usr/bin/env python3
"""Quick test runner utility."""

import subprocess
import sys
from pathlib import Path


def run_tests(args=None):
    """Run pytest test suites."""

    # Default parameters
    cmd = ["pytest", "tests/", "-v"]
    
    if args:
        cmd.extend(args)
    
    print(f"Running tests: {' '.join(cmd)}")
    print("-" * 60)
    
    result = subprocess.run(cmd)
    return result.returncode


def run_coverage():
    """Generate coverage report."""
    cmd = [
        "pytest", "tests/",
        "--cov=closeclaw",
        "--cov-report=term-missing",
        "--cov-report=html",
        "-v"
    ]
    
    print("Generating coverage report...")
    print("-" * 60)
    
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("\nCoverage report generated: htmlcov/index.html")
    
    return result.returncode


def run_specific_test(test_name):
    """Run a specific test file."""
    cmd = ["pytest", f"tests/{test_name}", "-v", "-s"]
    
    print(f"Running specific test: {test_name}")
    print("-" * 60)
    
    result = subprocess.run(cmd)
    return result.returncode


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="CloseClaw test runner"
    )
    
    parser.add_argument(
        "command",
        nargs="?",
        choices=["all", "coverage", "types", "config", "middleware", 
                "tools", "safety", "agent", "integration", "quick"],
        default="all",
        help="Test command to run"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )
    
    parser.add_argument(
        "-s", "--show-output",
        action="store_true",
        help="Show print output"
    )
    
    parser.add_argument(
        "-k", "--keyword",
        help="Filter tests by keyword"
    )
    
    args = parser.parse_args()
    
    # Build pytest args
    pytest_args = []
    
    if args.verbose:
        pytest_args.append("-vv")
    
    if args.show_output:
        pytest_args.append("-s")
    
    if args.keyword:
        pytest_args.extend(["-k", args.keyword])
    
    # Execute selected command
    if args.command == "all":
        return run_tests(pytest_args)
    
    elif args.command == "coverage":
        return run_coverage()
    
    elif args.command == "quick":
        print("Quick test run (critical paths only)...")
        return run_tests(pytest_args + ["-m", "not slow"])
    
    else:
        # Specific module test
        test_file = f"test_{args.command}.py"
        return run_tests(pytest_args + [f"tests/{test_file}"])


if __name__ == "__main__":
    sys.exit(main())


#!/bin/bash

# CloseClaw Phase 1 Test Runner

set -e

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 默认命令
COMMAND="${1:-all}"
EXTRA_ARGS="${@:2}"

# 函数：打印帮助
show_help() {
    clear
    echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║  CloseClaw Phase 1 Test Runner             ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
    echo ""
    echo "Usage: ./run_tests.sh [command] [options]"
    echo ""
    echo "Commands:"
    echo "  all           Run all tests"
    echo "  quick         Run quick tests (critical paths)"
    echo "  coverage      Generate coverage report"
    echo "  types         Test type system"
    echo "  config        Test configuration"
    echo "  middleware    Test middleware system"
    echo "  tools         Test tools system"
    echo "  safety        Test audit logging"
    echo "  agent         Test agent core"
    echo "  integration   Test end-to-end integration"
    echo "  help          Show this help message"
    echo ""
    echo "Options:"
    echo "  -v            Verbose output"
    echo "  -s            Show print statements"
    echo "  -k KEYWORD    Filter tests by keyword"
    echo ""
    echo "Examples:"
    echo "  ./run_tests.sh all"
    echo "  ./run_tests.sh coverage"
    echo "  ./run_tests.sh middleware -v"
    echo "  ./run_tests.sh types -k 'zone'"
    echo ""
}

# 函数：运行测试
run_command() {
    local cmd=$1
    local args=$2
    
    case $cmd in
        "all")
            echo -e "${GREEN}🧪 Running all tests...${NC}"
            pytest tests/ -v $args
            ;;
        "quick")
            echo -e "${GREEN}⚡ Running quick tests (critical paths)...${NC}"
            pytest tests/ -v -m "not slow" $args
            ;;
        "coverage")
            echo -e "${GREEN}📊 Generating coverage report...${NC}"
            pytest tests/ \
                --cov=closeclaw \
                --cov-report=html \
                --cov-report=term-missing \
                -v $args
            echo -e "${GREEN}✅ Coverage report: htmlcov/index.html${NC}"
            ;;
        "types")
            echo -e "${GREEN}🔍 Testing type system...${NC}"
            pytest tests/test_types.py -v $args
            ;;
        "config")
            echo -e "${GREEN}🔍 Testing configuration system...${NC}"
            pytest tests/test_config.py -v $args
            ;;
        "middleware")
            echo -e "${GREEN}🔍 Testing middleware system...${NC}"
            pytest tests/test_middleware.py -v $args
            ;;
        "tools")
            echo -e "${GREEN}🔍 Testing tools system...${NC}"
            pytest tests/test_tools.py -v $args
            ;;
        "safety")
            echo -e "${GREEN}🔍 Testing audit logging...${NC}"
            pytest tests/test_safety.py -v $args
            ;;
        "agent")
            echo -e "${GREEN}🔍 Testing agent core...${NC}"
            pytest tests/test_agent_core.py -v $args
            ;;
        "integration")
            echo -e "${GREEN}🔍 Testing integration...${NC}"
            pytest tests/test_integration.py -v $args
            ;;
        "help"|"-h"|"--help")
            show_help
            ;;
        *)
            echo -e "${RED}❌ Unknown command: $cmd${NC}"
            echo "Run './run_tests.sh help' for usage information"
            exit 1
            ;;
    esac
}

# 检查 pytest 是否安装
check_pytest() {
    if ! command -v pytest &> /dev/null; then
        echo -e "${RED}❌ pytest not found. Please install: pip install -e '.[dev]'${NC}"
        exit 1
    fi
}

# 主函数
main() {
    check_pytest
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    run_command "$COMMAND" "$EXTRA_ARGS"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# 运行主函数
main

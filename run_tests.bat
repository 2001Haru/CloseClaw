@echo off
REM Windows test runner script

setlocal enabledelayedexpansion

title CloseClaw Tests - %1

if "%1"=="" goto show_help

if "%1"=="all" (
    echo Running all tests...
    pytest tests/ -v
    goto end
)

if "%1"=="quick" (
    echo Running quick tests (critical paths only)...
    pytest tests/ -v -m "not slow"
    goto end
)

if "%1"=="coverage" (
    echo Generating coverage report...
    pytest tests/ --cov=closeclaw --cov-report=html --cov-report=term-missing -v
    goto end
)

if "%1"=="types" (
    pytest tests/test_types.py -v
    goto end
)

if "%1"=="config" (
    pytest tests/test_config.py -v
    goto end
)

if "%1"=="middleware" (
    pytest tests/test_middleware.py -v
    goto end
)

if "%1"=="tools" (
    pytest tests/test_tools.py -v
    goto end
)

if "%1"=="safety" (
    pytest tests/test_safety.py -v
    goto end
)

if "%1"=="agent" (
    pytest tests/test_agent_core.py -v
    goto end
)

if "%1"=="integration" (
    pytest tests/test_integration.py -v
    goto end
)

:show_help
cls
echo.
echo CloseClaw Phase 1 Test Runner
echo ==============================
echo.
echo Usage: run_tests.bat [command] [options]
echo.
echo Commands:
echo   all           Run all tests
echo   quick         Run quick tests (critical paths)
echo   coverage      Generate coverage report
echo   types         Test type system
echo   config        Test configuration
echo   middleware    Test middleware system
echo   tools         Test tools system
echo   safety        Test audit logging
echo   agent         Test agent core
echo   integration   Test end-to-end integration
echo.
echo Options:
echo   -v            Verbose output
echo   -s            Show print statements
echo   -k KEYWORD    Filter tests by keyword
echo.
echo Examples:
echo   run_tests.bat all
echo   run_tests.bat coverage
echo   run_tests.bat middleware -v
echo   run_tests.bat types -k "zone"
echo.

:end
pause

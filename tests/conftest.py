"""Pytest configuration and shared fixtures."""

import os
import tempfile
import pytest
from datetime import datetime, timezone
from pathlib import Path

from closeclaw.types import (
    AgentState, ToolType, OperationType,
    Tool, Session, Agent, AgentConfig,
    Message, ToolCall, ToolResult
)
from closeclaw.config import LLMConfig


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_tool_file() -> Tool:
    """Create a sample file tool."""
    return Tool(
        name="read_file",
        description="Read file contents",
        need_auth=False,
        type=ToolType.FILE,
        parameters={"path": {"type": "string"}},
    )


@pytest.fixture
def sample_tool_shell() -> Tool:
    """Create a sample dangerous shell tool."""
    return Tool(
        name="execute_shell",
        description="Execute shell command",
        need_auth=True,
        type=ToolType.SHELL,
        parameters={"command": {"type": "string"}},
    )


@pytest.fixture
def sample_tool_websearch() -> Tool:
    """Create a sample web search tool."""
    return Tool(
        name="web_search",
        description="Search the web",
        need_auth=False,
        type=ToolType.WEBSEARCH,
        parameters={"query": {"type": "string"}},
    )


@pytest.fixture
def sample_session() -> Session:
    """Create a sample session."""
    return Session(
        session_id="test_session_123",
        user_id="user_456",
        channel_type="cli",
        created_at=datetime.now(timezone.utc),
        last_activity=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_agent_config() -> AgentConfig:
    """Create a sample agent configuration."""
    return AgentConfig(
        model="openai/gpt-4",
        max_iterations=10,
        timeout_seconds=300,
        temperature=0.0,
        system_prompt="You are a helpful assistant.",
    )


@pytest.fixture
def sample_agent(sample_agent_config, sample_tool_file, sample_tool_shell) -> Agent:
    """Create a sample agent."""
    return Agent(
        agent_id="agent_001",
        config=sample_agent_config,
        state=AgentState.IDLE,
        tools=[sample_tool_file, sample_tool_shell],
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_message() -> Message:
    """Create a sample message."""
    return Message(
        id="msg_001",
        channel_type="cli",
        sender_id="user_123",
        sender_name="User",
        content="Hello, please read the file at /data/test.txt",
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_tool_call() -> ToolCall:
    """Create a sample tool call."""
    return ToolCall(
        tool_id="tool_1",
        name="read_file",
        arguments={"path": "/data/test.txt"},
    )


@pytest.fixture
def sample_tool_result(sample_tool_call) -> ToolResult:
    """Create a sample tool result."""
    return ToolResult(
        tool_call_id="tool_1",
        status="success",
        result="File contents here",
        error=None,
        execution_time_ms=100,
    )


@pytest.fixture
def config_file(temp_workspace):
    """Create a sample configuration file."""
    config_content = """
llm:
  provider: openai
  model: gpt-4
  api_key: ${OPENAI_API_KEY}
  temperature: 0.0
  max_tokens: 2000

agent:
  max_iterations: 10
  timeout_seconds: 300

safety:
  enable_hitl: true
  enable_audit_log: true
  audit_log_path: ${WORKSPACE_ROOT}/audit.jsonl

channels:
  cli:
    type: cli
    enabled: true
"""
    config_path = Path(temp_workspace) / "test_config.yaml"
    config_path.write_text(config_content)
    return config_path


@pytest.fixture
def mock_llm_provider():
    """Create a mock LLM provider."""
    class MockLLMProvider:
        async def generate(self, messages, tools, **kwargs):
            # Return a simple response without tool calls
            return "This is a test response", None
    
    return MockLLMProvider()


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset logging state between tests."""
    import logging
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    yield






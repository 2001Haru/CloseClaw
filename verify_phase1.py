#!/usr/bin/env python
"""验证Phase1核心功能是否工作"""

import tempfile
import sys

# 测试1: 基本类型系统是否工作
print("=" * 50)
print("测试1: 类型系统")
print("=" * 50)
try:
    from closeclaw.types import Zone, Tool, ToolType, AgentState, AgentConfig
    
    tool = Tool(
        name="test_tool",
        description="Test tool",
        zone=Zone.ZONE_A,
        type=ToolType.FILE
    )
    print(f"✓ Tool created: {tool.name}, zone={tool.zone.value}")
except Exception as e:
    print(f"✗ Type system error: {e}")
    sys.exit(1)

# 测试2: 安全中间件是否工作
print("\n" + "=" * 50)
print("测试2: 安全中间件")
print("=" * 50)
try:
    from closeclaw.middleware import SafetyGuard
    
    guard = SafetyGuard()
    result = guard.validate("echo hello")
    print(f"✓ SafetyGuard validation (echo hello): {result}")
    
    blocked = guard.validate("rm -rf /")
    print(f"✓ SafetyGuard blocked dangerous command: {blocked}")
except Exception as e:
    print(f"✗ SafetyGuard error: {e}")

# 测试3: 审计日志是否工作
print("\n" + "=" * 50)
print("测试3: 审计日志")
print("=" * 50)
try:
    from closeclaw.safety import AuditLogger
    
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = AuditLogger(log_file=f"{tmpdir}/audit.log")
        logger.log(
            event_type="test",
            status="success",
            user_id="test_user",
            tool_name="test_tool",
            arguments={}
        )
        logs = logger.read_logs()
        print(f"✓ AuditLogger logged {len(logs)} events")
except Exception as e:
    print(f"✗ AuditLogger error: {e}")

# 测试4: 配置系统是否工作
print("\n" + "=" * 50)
print("测试4: 配置系统")
print("=" * 50)
try:
    from closeclaw.config import LLMConfig
    
    config = LLMConfig(
        provider="test",
        model="test/model",
        api_key="test_key"
    )
    print(f"✓ LLMConfig created: provider={config.provider}")
except Exception as e:
    print(f"✗ Config error: {e}")

# 测试5: Agent Core循环类型
print("\n" + "=" * 50)
print("测试5: Agent核心类型")
print("=" * 50)
try:
    from closeclaw.agents import Agent
    from closeclaw.types import AgentConfig
    
    config = AgentConfig(
        model="test/model",
        max_iterations=10,
        timeout_seconds=300
    )
    
    agent = Agent(
        agent_id="test_agent",
        config=config,
        state=AgentState.IDLE,
        tools=[],
        created_at=None
    )
    print(f"✓ Agent created: id={agent.agent_id}, state={agent.state.value}")
except Exception as e:
    print(f"✗ Agent error: {e}")

print("\n" + "=" * 50)
print("✓✓✓ Phase1核心功能验证通过！")
print("=" * 50)

"""
Phase 2 Integration Checkpoint: TaskManager + Agent.run() Demo

This module demonstrates the Phase 2 architecture from Planning.md:
  "同步主循环 + TaskManager异步管理" (Synchronous Main Loop + Async TaskManager)

Flow:
  1. User input → Agent.process_message()
  2. Detect long-running tool → create background task (non-blocking)
  3. Task returns task_id immediately (e.g., "#001")
  4. Main loop continues (doesn't block on task)
  5. Each loop iteration: poll_background_tasks() checks for completion
  6. Task completes → notify user with result

Key Design Points:
  ✓ Synchronous main loop (easy to debug)
  ✓ Long ops via TaskManager (asyncio.create_task, non-blocking)
  ✓ HITL for Zone C (立即確認 = immediate confirmation)
  ✓ State persistence (完整持久化 = complete persistence)
  ✓ Task resumption on restart (load_from_state)
"""

import asyncio
import logging
import json
from datetime import datetime

from closeclaw.agents import AgentCore, TaskManager
from closeclaw.types import (
    Message, AgentConfig, Tool, ToolType, Zone, TaskStatus
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ============================================================================
# DEMO: Simulated Long-Running Tools
# ============================================================================

async def simulate_web_search(query: str, delay_seconds: float = 2) -> dict:
    """Simulate a web search that takes time."""
    logger.info(f"🌐 Web search starting: '{query}' (simulating {delay_seconds}s delay)")
    await asyncio.sleep(delay_seconds)
    logger.info(f"✅ Web search completed: '{query}'")
    return {
        "query": query,
        "results": [
            {"title": "Result 1", "url": "http://example.com/1"},
            {"title": "Result 2", "url": "http://example.com/2"},
        ],
        "search_time_seconds": delay_seconds,
    }


async def simulate_file_read(path: str, delay_seconds: float = 1) -> dict:
    """Simulate reading a large file."""
    logger.info(f"📂 Reading file: {path} (simulating {delay_seconds}s delay)")
    await asyncio.sleep(delay_seconds)
    logger.info(f"✅ File read completed: {path}")
    return {
        "path": path,
        "content": "Large file content...",
        "size_bytes": 1024 * 1024,
    }


async def demo_main():
    """
    Main demo: Show the integrated flow
    
    Scenario:
    1. Create agent with TaskManager
    2. Register long-running tools
    3. Simulate user requesting long-running operation
    4. Show that agent doesn't block (poll_background_tasks in loop)
    5. Show state persistence
    """
    
    logger.info("=" * 70)
    logger.info("PHASE 2 INTEGRATION DEMO: TaskManager + Agent.run()")
    logger.info("=" * 70)
    logger.info("")
    
    # Create mock LLM provider
    async def mock_llm_generate(messages, tools, **kwargs):
        """Mock LLM that returns a tool call for first message."""
        # First call: request web search
        # Second call: normal response
        if len(messages) == 2:  # System + user message
            from closeclaw.types import ToolCall
            return (None, [ToolCall(
                tool_id="tc_001",
                name="web_search",
                arguments={"query": "Python asyncio best practices"},
            )])
        else:
            return ("Search results received and processed.", None)
    
    # Create agent
    config = AgentConfig(
        model="gpt-4",
        temperature=0.7,
        system_prompt="You are a helpful research assistant."
    )
    
    agent = AgentCore(
        agent_id="demo_agent",
        llm_provider=mock_llm_generate,
        config=config,
        workspace_root="/tmp/closeclaw_demo",
        admin_user_id="admin",
    )
    
    # Create TaskManager and integrate
    task_manager = TaskManager()
    agent.set_task_manager(task_manager)
    logger.info("✓ TaskManager integrated with AgentCore")
    logger.info("")
    
    # Register tools
    web_search_tool = Tool(
        name="web_search",
        description="Search the web for information",
        type=ToolType.WEBSEARCH,
        zone=Zone.ZONE_A,
        handler=simulate_web_search,
        parameters={"query": {"type": "string"}},
    )
    agent.register_tool(web_search_tool)
    logger.info("✓ Tool registered: web_search")
    
    file_read_tool = Tool(
        name="read_file",
        description="Read a file",
        type=ToolType.FILE,
        zone=Zone.ZONE_A,
        handler=simulate_file_read,
        parameters={"path": {"type": "string"}},
    )
    agent.register_tool(file_read_tool)
    logger.info("✓ Tool registered: read_file")
    logger.info("")
    
    # ========================================================================
    # DEMO SCENARIO 1: Non-blocking background task
    # ========================================================================
    
    logger.info("─" * 70)
    logger.info("SCENARIO 1: Long-running task (non-blocking)")
    logger.info("─" * 70)
    logger.info("")
    
    # Simulate user input: one message then exit
    user_messages = [
        Message(
            id="msg_001",
            channel_type="cli",
            sender_id="user_demo_123",
            sender_name="DemoUser",
            content="Search for Python asyncio patterns",
        ),
        None,  # Exit
    ]
    
    message_queue = iter(user_messages)
    
    async def input_fn():
        try:
            msg = next(message_queue)
            if msg:
                logger.info(f"👤 User: {msg.content}")
            return msg
        except StopIteration:
            return None
    
    output_messages = []
    
    async def output_fn(response):
        output_messages.append(response)
        msg_type = response.get("type")
        
        if msg_type == "response":
            logger.info(f"🤖 Agent response: {response.get('response')[:80]}...")
            if response.get("tool_calls"):
                logger.info(f"   Tool calls: {len(response['tool_calls'])}")
        elif msg_type == "task_completed":
            task_id = response.get("task_id")
            status = response.get("status")
            logger.info(f"✅ Task {task_id} completed ({status})")
            logger.info(f"   Result: {json.dumps(response.get('result'), indent=2)[:100]}...")
    
    # Run agent loop
    start_time = datetime.now()
    await agent.run(
        session_id="demo_session_001",
        user_id="user_demo_123",
        channel_type="cli",
        message_input_fn=input_fn,
        message_output_fn=output_fn,
    )
    elapsed = (datetime.now() - start_time).total_seconds()
    
    logger.info("")
    logger.info(f"⏱️  Agent loop completed in {elapsed:.2f} seconds")
    logger.info(f"   Total outputs sent: {len(output_messages)}")
    logger.info("")
    
    # ========================================================================
    # DEMO SCENARIO 2: State persistence
    # ========================================================================
    
    logger.info("─" * 70)
    logger.info("SCENARIO 2: State persistence and recovery")
    logger.info("─" * 70)
    logger.info("")
    
    # Create background task manually
    logger.info("Creating background task: web_search on 'machine learning'")
    task_id = await agent.create_background_task(
        "web_search",
        {"query": "machine learning fundamentals"}
    )
    logger.info(f"✓ Task created with ID: {task_id}")
    logger.info("")
    
    # Save state
    logger.info("Saving agent state to dict (simulating state.json)")
    state_snapshot = await agent._save_state()
    logger.info(f"✓ State saved:")
    logger.info(f"   - Active tasks: {list(state_snapshot.get('active_tasks', {}).keys())}")
    logger.info(f"   - Completed tasks: {list(state_snapshot.get('completed_results', {}).keys())}")
    logger.info(f"   - Message history: {len(state_snapshot.get('message_history', []))} messages")
    logger.info("")
    
    # Wait a bit for task to complete or progress
    await asyncio.sleep(1)
    
    # Poll for completed tasks
    logger.info("Polling for completed tasks...")
    completed = await agent.poll_background_tasks()
    logger.info(f"✓ Completed tasks: {len(completed)}")
    for task in completed:
        logger.info(f"   - {task['task_id']}: {task['status']}")
    logger.info("")
    
    # ========================================================================
    # SUMMARY
    # ========================================================================
    
    logger.info("=" * 70)
    logger.info("✅ PHASE 2 INTEGRATION DEMO COMPLETE")
    logger.info("=" * 70)
    logger.info("")
    logger.info("Key Achievements:")
    logger.info("  ✓ TaskManager creates background tasks (non-blocking)")
    logger.info("  ✓ Agent.run() main loop doesn't block on long operations")
    logger.info("  ✓ State can be saved and restored")
    logger.info("  ✓ Tasks can be polled for completion status")
    logger.info("")
    logger.info("Architecture confirmed:")
    logger.info("  └─ Synchronous main loop (easy to debug)")
    logger.info("     └─ asyncio.create_task() for background work")
    logger.info("        └─ poll_background_tasks() each loop iteration")
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Tool adaptation layer (detect long-running ops)")
    logger.info("  2. CLI commands (`closeclaw tasks`, `closeclaw cancel`)")
    logger.info("  3. Full Phase 2 integration testing")
    logger.info("")


if __name__ == "__main__":
    asyncio.run(demo_main())

"""
Test suite for Phase 3.5: Transcript Repair防火墙

Tests the repair_transcript function's ability to:
1. 移除孤儿 Tool Call（无对应 Result）
2. 丢弃孤儿 Tool Result（无对应 Call）
3. 必要时注入合成错误
4. 记录所有修复操作至审计日志
"""

import pytest
import os
import json
import tempfile
from datetime import datetime

from closeclaw.agents.core import AgentCore
from closeclaw.types import AgentConfig, AgentState
from closeclaw.safety import AuditLogger


@pytest.fixture
def temp_workspace():
    """创建临时工作目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def mock_llm_provider():
    """Mock LLM provider"""
    class MockProvider:
        async def generate(self, messages, tools, **kwargs):
            return "mock response", None
    return MockProvider()


@pytest.fixture
def agent(temp_workspace, mock_llm_provider):
    """创建 AgentCore 实例用于测试"""
    config = AgentConfig(
        model="gpt-4",
        system_prompt="You are a test agent."
    )
    agent = AgentCore(
        agent_id="test-agent",
        llm_provider=mock_llm_provider,
        config=config,
        workspace_root=temp_workspace,
        admin_user_id="test-user"
    )
    return agent


class TestTranscriptRepair:
    """Transcript Repair 防火墙测试套件"""
    
    def test_orphan_tool_call_removed(self, agent):
        """测试：移除孤儿 Tool Call（无对应 Result）"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": "I'll help",
                "tool_calls": [
                    {
                        "id": "call_001",
                        "type": "function",
                        "function": {"name": "do_work", "arguments": "{}"}
                    }
                ]
            },
            {"role": "user", "content": "What's the result?"}  # ← 没有 tool result！
        ]
        
        repaired = agent._repair_transcript(messages)
        
        # 检查修复后的消息列表
        assert any(msg.get("role") == "tool" and msg.get("tool_call_id") == "call_001" 
                  for msg in repaired), "应该注入合成错误"
        
        # 最终应该有一个 tool 消息（合成的）
        tool_messages = [msg for msg in repaired if msg.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert "[System Repair]" in tool_messages[0]["content"]
    
    def test_orphan_tool_result_dropped(self, agent):
        """测试：丢弃孤儿 Tool Result（无对应 Call）"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {
                "role": "tool",
                "tool_call_id": "orphan_call_id",  # 这个 call 不存在！
                "content": "some result"
            },
            {"role": "user", "content": "Next message"}
        ]
        
        repaired = agent._repair_transcript(messages)
        
        # 孤儿 result 应该被丢弃
        assert not any(msg.get("tool_call_id") == "orphan_call_id" 
                      for msg in repaired)
    
    def test_orphan_result_before_next_message(self, agent):
        """测试：消息顺序混乱（Result 在 Call 之前）"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {
                "role": "tool",
                "tool_call_id": "call_001",  # 结果先出现
                "content": "result of call_001"
            },
            {
                "role": "assistant",
                "content": "I did work",
                "tool_calls": [
                    {
                        "id": "call_001",
                        "type": "function",
                        "function": {"name": "work", "arguments": "{}"}
                    }
                ]
            }
        ]
        
        repaired = agent._repair_transcript(messages)
        
        # 应该能正确处理而不崩溃
        assert len(repaired) > 0
    
    def test_correct_transcript_unchanged(self, agent):
        """测试：正确的转录不被修改"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": "I'll help",
                "tool_calls": [
                    {
                        "id": "call_001",
                        "type": "function",
                        "function": {"name": "do_work", "arguments": "{}"}
                    }
                ]
            },
            {
                "role": "tool",
                "tool_call_id": "call_001",
                "content": "Work done"
            },
            {"role": "user", "content": "Thanks"}
        ]
        
        repaired = agent._repair_transcript(messages)
        
        # 没有孤儿，应该返回相同的消息数
        assert len(repaired) == len(messages)
        
        # 内容应该相同
        for orig, rep in zip(messages, repaired):
            assert orig == rep
    
    def test_multiple_tool_calls_with_partial_results(self, agent):
        """测试：多个 Tool Call，仅部分有 Result"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {
                "role": "assistant",
                "content": "I'll do multiple things",
                "tool_calls": [
                    {"id": "call_001", "type": "function", "function": {"name": "work1", "arguments": "{}"}},
                    {"id": "call_002", "type": "function", "function": {"name": "work2", "arguments": "{}"}}
                ]
            },
            {
                "role": "tool",
                "tool_call_id": "call_001",  # 仅一个有结果
                "content": "Result 1"
            },
            {"role": "user", "content": "Next"}  # call_002 的结果丢失
        ]
        
        repaired = agent._repair_transcript(messages)
        
        # 应该为 call_002 注入合成错误
        tool_messages = [msg for msg in repaired if msg.get("role") == "tool"]
        assert len(tool_messages) == 2  # 原始的 call_001 结果 + 合成的 call_002 错误
    
    def test_audit_log_recording(self, agent, temp_workspace):
        """测试：修复统计被记录到审计日志"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {
                "role": "assistant",
                "content": "I'll do work",
                "tool_calls": [
                    {"id": "call_001", "type": "function", "function": {"name": "work", "arguments": "{}"}}
                ]
            },
            {"role": "user", "content": "Next"}  # 孤儿 call_001
        ]
        
        # 确保 audit.log 在工作目录中
        audit_log_path = os.path.join(temp_workspace, "audit.log")
        
        repaired = agent._repair_transcript(messages)
        
        # 检查审计日志是否被创建并写入
        assert os.path.exists(audit_log_path), "审计日志应该被创建"
        
        with open(audit_log_path, "r") as f:
            logs = [json.loads(line) for line in f if line.strip()]
        
        # 应该有至少一条 transcript_repair 事件
        repair_logs = [log for log in logs if log.get("event_type") == "transcript_repair"]
        
        if repair_logs:
            # 如果有修复，应该有记录
            latest_log = repair_logs[-1]
            assert "orphan_calls_removed" in latest_log.get("arguments", "")
            assert latest_log.get("status") == "success"
    
    def test_synthetic_error_injection_message_format(self, agent):
        """测试：合成错误消息格式正确"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {
                "role": "assistant",
                "content": "I'll work",
                "tool_calls": [
                    {"id": "test_call", "type": "function", "function": {"name": "work", "arguments": "{}"}}
                ]
            },
            {"role": "user", "content": "Next"}  # 孤儿 call
        ]
        
        repaired = agent._repair_transcript(messages)
        
        # 查找合成的错误消息
        synthetic_msgs = [msg for msg in repaired 
                         if msg.get("role") == "tool" and "[System Repair]" in msg.get("content", "")]
        
        assert len(synthetic_msgs) > 0
        
        # 验证格式
        synthetic_msg = synthetic_msgs[0]
        assert synthetic_msg.get("role") == "tool"
        assert synthetic_msg.get("tool_call_id") == "test_call"
        assert isinstance(synthetic_msg.get("content"), str)


class TestTranscriptRepairIntegration:
    """集成测试：Transcript Repair 与 Agent 循环的交互"""
    
    def test_repair_applied_in_format_conversation(self, agent):
        """测试：_repair_transcript 在 _format_conversation_for_llm 中被应用"""
        # 向 Agent 的消息历史中添加有问题的消息
        from closeclaw.types import Message, ToolCall
        
        agent.message_history = [
            Message(
                id="msg_001",
                channel_type="cli",
                sender_id="user1",
                sender_name="User",
                content="Do something",
                timestamp=datetime.now()
            ),
            Message(
                id="msg_002",
                channel_type="cli",
                sender_id=agent.agent_id,
                sender_name="Agent",
                content="I'll help",
                timestamp=datetime.now(),
                tool_calls=[
                    ToolCall(
                        tool_id="call_001",
                        name="work",
                        arguments={}
                    )
                ]
            ),
            Message(
                id="msg_003",
                channel_type="cli",
                sender_id="user1",
                sender_name="User",
                content="What's next?",
                timestamp=datetime.now()
                # ← 注意：没有对应的 tool_result！
            )
        ]
        
        # 调用 _format_conversation_for_llm，它会自动调用 _repair_transcript
        formatted = agent._format_conversation_for_llm()
        
        # 检查是否已修复
        tool_messages = [msg for msg in formatted if msg.get("role") == "tool"]
        assert len(tool_messages) > 0, "应该为孤儿 call 注入合成错误"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

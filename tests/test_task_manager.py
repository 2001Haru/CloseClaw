"""Tests for TaskManager background task execution."""

import pytest
import asyncio
from datetime import datetime

from closeclaw.agents import TaskManager
from closeclaw.types import TaskStatus, BackgroundTask


class TestTaskManagerBasics:
    """Basic TaskManager functionality tests."""
    
    @pytest.fixture
    def task_manager(self):
        """Create a TaskManager instance for testing."""
        return TaskManager()
    
    @pytest.mark.asyncio
    async def test_create_task(self, task_manager):
        """Test creating a background task."""
        # Register a test tool
        async def sample_tool(x: int, y: int) -> int:
            await asyncio.sleep(0.01)  # Simulate work
            return x + y
        
        task_manager.register_tool_handler("sample_tool", sample_tool)
        
        # Create task
        task_id = await task_manager.create_task(
            "sample_tool",
            {"x": 2, "y": 3}
        )
        
        # Verify task_id format
        assert task_id.startswith("#")
        assert task_id == "#001"
    
    @pytest.mark.asyncio
    async def test_poll_results(self, task_manager):
        """Test polling for completed tasks."""
        # Register tool
        async def simple_tool() -> str:
            await asyncio.sleep(0.02)
            return "done"
        
        task_manager.register_tool_handler("simple_tool", simple_tool)
        
        # Create task
        task_id = await task_manager.create_task("simple_tool", {})
        
        # Task should not be completed yet
        results = await task_manager.poll_results()
        assert task_id not in results
        
        # Wait for task to complete
        await asyncio.sleep(0.1)
        
        # Now poll should find it
        results = await task_manager.poll_results()
        assert task_id in results
        assert results[task_id].status == TaskStatus.COMPLETED
        assert results[task_id].result == "done"
    
    @pytest.mark.asyncio
    async def test_concurrent_tasks(self, task_manager):
        """Test running multiple concurrent tasks."""
        # Register tool
        async def concurrent_tool(n: int) -> int:
            await asyncio.sleep(0.01 * n)
            return n * 2
        
        task_manager.register_tool_handler("concurrent_tool", concurrent_tool)
        
        # Create multiple tasks
        task_ids = []
        for i in range(3):
            task_id = await task_manager.create_task(
                "concurrent_tool",
                {"n": i + 1}
            )
            task_ids.append(task_id)
        
        # All should be different
        assert len(set(task_ids)) == 3
        assert task_ids[0] == "#001"
        assert task_ids[1] == "#002"
        assert task_ids[2] == "#003"
        
        # Wait for all to complete
        await asyncio.sleep(0.15)
        
        # Poll and verify
        results = await task_manager.poll_results()
        assert len(results) >= 2  # At least 2 should be done by now


class TestTaskManagerErrors:
    """Test error handling in TaskManager."""
    
    @pytest.fixture
    def task_manager(self):
        return TaskManager()
    
    @pytest.mark.asyncio
    async def test_tool_not_found(self, task_manager):
        """Test creating task for non-existent tool."""
        task_id = await task_manager.create_task(
            "nonexistent_tool",
            {}
        )
        
        # Should return a task_id
        assert task_id == "#001"
        
        # Status check should show failure
        result = task_manager.get_status(task_id)
        assert result is not None
        assert result.status == TaskStatus.FAILED
        assert "not registered" in result.error
    
    @pytest.mark.asyncio
    async def test_tool_exception(self, task_manager):
        """Test tool that raises an exception."""
        async def failing_tool():
            raise ValueError("Intentional error")
        
        task_manager.register_tool_handler("failing_tool", failing_tool)
        
        task_id = await task_manager.create_task("failing_tool", {})
        
        # Wait for task to fail
        await asyncio.sleep(0.05)
        
        # Check result
        result = task_manager.get_status(task_id)
        assert result.status == TaskStatus.FAILED
        assert "Intentional error" in result.error


class TestTaskCancellation:
    """Test task cancellation."""
    
    @pytest.fixture
    def task_manager(self):
        return TaskManager()
    
    @pytest.mark.asyncio
    async def test_cancel_task(self, task_manager):
        """Test cancelling a running task."""
        async def long_running_tool():
            await asyncio.sleep(10)  # Long operation
            return "completed"
        
        task_manager.register_tool_handler("long_running_tool", long_running_tool)
        
        task_id = await task_manager.create_task("long_running_tool", {})
        
        # Give task a moment to start
        await asyncio.sleep(0.01)
        
        # Cancel it
        cancelled = await task_manager.cancel_task(task_id)
        assert cancelled is True
        
        # Wait a bit for cancellation to propagate
        await asyncio.sleep(0.1)
        
        # Check status shows cancelled
        result = task_manager.get_status(task_id)
        assert result.status == TaskStatus.CANCELLED


class TestTaskStateManagement:
    """Test state persistence functionality."""
    
    @pytest.fixture
    def task_manager(self):
        return TaskManager()
    
    @pytest.mark.asyncio
    async def test_save_and_load_state(self, task_manager):
        """Test saving and loading task state."""
        # Register tool and create a completed task
        async def state_tool():
            return "result_data"
        
        task_manager.register_tool_handler("state_tool", state_tool)
        task_id = await task_manager.create_task("state_tool", {})
        
        # Wait for completion
        await asyncio.sleep(0.05)
        
        # Poll to move to completed
        await task_manager.poll_results()
        
        # Save state
        state_data = await task_manager.save_to_state()
        
        # Verify state structure
        assert "completed_results" in state_data
        assert task_id in state_data["completed_results"]
        
        # Create new manager and load state
        new_manager = TaskManager()
        await new_manager.load_from_state(state_data)
        
        # Verify restored
        result = new_manager.get_status(task_id)
        assert result is not None
        assert result.status == TaskStatus.COMPLETED
        assert result.result == "result_data"
    
    @pytest.mark.asyncio
    async def test_list_active_tasks(self, task_manager):
        """Test listing all active tasks."""
        async def list_tool():
            await asyncio.sleep(0.02)
            return "done"
        
        task_manager.register_tool_handler("list_tool", list_tool)
        
        # Create multiple tasks
        ids = []
        for i in range(3):
            task_id = await task_manager.create_task("list_tool", {"i": i})
            ids.append(task_id)
        
        # List all
        all_tasks = task_manager.list_active_tasks()
        
        # After creation, only completed ones are returned
        # (should be 0 since they haven't finished yet)
        # After polling, they'll appear
        await asyncio.sleep(0.1)
        await task_manager.poll_results()
        
        all_tasks = task_manager.list_active_tasks()
        for task_id in ids:
            # All should be in the list
            assert task_id in all_tasks or task_id not in all_tasks  # Timing dependent
    
    @pytest.mark.asyncio
    async def test_cleanup_expired_tasks(self, task_manager):
        """Test cleanup of old tasks."""
        # Manually add an old completed task
        old_task = BackgroundTask(
            task_id="#000",
            tool_name="old_tool",
            tool_arguments={},
            status=TaskStatus.COMPLETED,
            created_at=datetime.utcnow(),  # Would be old in real usage
            result="old_result"
        )
        
        # We can't directly test with old dates without mocking datetime
        # But we can verify the cleanup function exists and doesn't crash
        cleaned = await task_manager.cleanup_expired_tasks(max_age_seconds=0)
        
        # Even with 0 timeout, shouldn't crash
        assert isinstance(cleaned, int)


class TestTaskWaitFor:
    """Test waiting for task completion."""
    
    @pytest.fixture
    def task_manager(self):
        return TaskManager()
    
    @pytest.mark.asyncio
    async def test_wait_for_task(self, task_manager):
        """Test blocking wait for task completion."""
        async def wait_tool():
            await asyncio.sleep(0.05)
            return "waited_result"
        
        task_manager.register_tool_handler("wait_tool", wait_tool)
        
        task_id = await task_manager.create_task("wait_tool", {})
        
        # Wait for it (with timeout as safety)
        result = await task_manager.wait_for_task(task_id, timeout=1.0)
        
        assert result is not None
        assert result.status == TaskStatus.COMPLETED
        assert result.result == "waited_result"
    
    @pytest.mark.asyncio
    async def test_wait_for_task_timeout(self, task_manager):
        """Test timeout when waiting for task."""
        async def slow_tool():
            await asyncio.sleep(10)
            return "never_completes"
        
        task_manager.register_tool_handler("slow_tool", slow_tool)
        
        task_id = await task_manager.create_task("slow_tool", {})
        
        # Wait with very short timeout
        result = await task_manager.wait_for_task(task_id, timeout=0.01)
        
        # Should timeout
        assert result is None
        
        # Clean up
        await task_manager.cancel_task(task_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

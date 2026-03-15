"""Integration tests: multi-task workflows for TaskManager."""

import pytest
import asyncio
from datetime import datetime

from closeclaw.agents import TaskManager
from closeclaw.types import TaskStatus


@pytest.mark.asyncio
async def test_concurrent_web_searches():
    """Run 5 concurrent 'web_search' tasks and verify completion."""
    tm = TaskManager()

    async def web_search(query: str, delay: float = 0.01):
        await asyncio.sleep(delay)
        return {"query": query, "hits": [f"{query}-1", f"{query}-2"]}

    tm.register_tool_handler("web_search", web_search)

    ids = []
    for i in range(5):
        tid = await tm.create_task("web_search", {"query": f"q{i}", "delay": 0.01 * (i+1)})
        ids.append(tid)

    # Allow tasks to run
    await asyncio.sleep(0.2)

    results = await tm.poll_results()
    # Ensure at least the created tasks have completed
    for tid in ids:
        # Some tasks may finish slightly later; ensure they end up in completed_results
        assert tid in tm.completed_results
        assert tm.completed_results[tid].status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_mixed_long_running_operations():
    """Mixed tools (web_search, shell_exec, file_write) run concurrently."""
    tm = TaskManager()

    async def web_search(query: str):
        await asyncio.sleep(0.02)
        return {"query": query}

    async def shell_exec(cmd: str):
        await asyncio.sleep(0.03)
        if "fail" in cmd:
            raise RuntimeError("command failed")
        return {"cmd": cmd, "rc": 0}

    async def file_write(path: str, content: str):
        await asyncio.sleep(0.01)
        return {"path": path, "written": len(content)}

    tm.register_tool_handler("web_search", web_search)
    tm.register_tool_handler("shell_exec", shell_exec)
    tm.register_tool_handler("file_write", file_write)

    t1 = await tm.create_task("web_search", {"query": "alpha"})
    t2 = await tm.create_task("shell_exec", {"cmd": "echo OK"})
    t3 = await tm.create_task("shell_exec", {"cmd": "fail now"})
    t4 = await tm.create_task("file_write", {"path": "./tmp.txt", "content": "hello"})

    await asyncio.sleep(0.2)

    # Poll and verify states
    await tm.poll_results()

    assert tm.completed_results[t1].status == TaskStatus.COMPLETED
    assert tm.completed_results[t2].status == TaskStatus.COMPLETED
    assert tm.completed_results[t3].status == TaskStatus.FAILED
    assert tm.completed_results[t4].status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_state_persistence_with_multiple_tasks():
    """Save state with multiple completed tasks and restore into new manager."""
    tm = TaskManager()

    async def quick(n: int = 0):
        await asyncio.sleep(0.01)
        return {"n": n}

    tm.register_tool_handler("quick", quick)

    ids = []
    for i in range(3):
        tid = await tm.create_task("quick", {"n": i})
        ids.append(tid)

    await asyncio.sleep(0.1)
    await tm.poll_results()

    state = await tm.save_to_state()
    assert "completed_results" in state
    for tid in ids:
        assert tid in state["completed_results"]

    # Load into new manager
    nm = TaskManager()
    await nm.load_from_state(state)
    for tid in ids:
        r = nm.get_status(tid)
        assert r is not None
        assert r.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_cancel_multiple_tasks():
    """Start several long tasks and cancel some of them."""
    tm = TaskManager()

    async def long_task(i: int = 0):
        await asyncio.sleep(1)
        return {"i": i}

    tm.register_tool_handler("long_task", long_task)

    ids = [await tm.create_task("long_task", {"i": i}) for i in range(5)]

    # Let them start
    await asyncio.sleep(0.05)

    # Cancel two
    await tm.cancel_task(ids[1])
    await tm.cancel_task(ids[3])

    # Wait a bit for cancellations to propagate
    await asyncio.sleep(0.2)
    await tm.poll_results()

    assert tm.completed_results[ids[1]].status == TaskStatus.CANCELLED
    assert tm.completed_results[ids[3]].status == TaskStatus.CANCELLED

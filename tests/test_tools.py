"""Tests for tools system."""

import pytest
import os
import time
import tempfile
from pathlib import Path

from closeclaw.tools.base import get_registered_tools
from closeclaw.types import ToolType


class TestFileTools:
    """Test file operation tools."""
    
    @pytest.mark.asyncio
    async def test_read_file_tool(self, temp_workspace):
        # Create test file
        test_file = Path(temp_workspace) / "test.txt"
        test_file.write_text("Hello, World!\nSecond line\nThird line\n")
        
        # Test direct function
        from closeclaw.tools.file_tools import read_file_impl
        content = await read_file_impl(str(test_file))
        assert "Hello, World!" in content

        ranged = await read_file_impl(str(test_file), start_line=2, end_line=3)
        assert ranged == "2| Second line\n3| Third line"
        
        # Test metadata
        tools = get_registered_tools()
        tool = next((t for t in tools if t.name == "read_file"), None)
        assert tool is not None
        assert tool.need_auth is False
        assert tool.type == ToolType.FILE

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, temp_workspace):
        from closeclaw.tools.file_tools import read_file_impl
        with pytest.raises(Exception):
            await read_file_impl(str(Path(temp_workspace) / "nonexistent.txt"))

    @pytest.mark.asyncio
    async def test_write_file_tool(self, temp_workspace):
        test_file = Path(temp_workspace) / "output.txt"
        
        from closeclaw.tools.file_tools import write_file_impl
        result = await write_file_impl(str(test_file), "Test content")
        
        assert "File written" in result
        assert test_file.exists()
        assert test_file.read_text() == "Test content"
        
        tools = get_registered_tools()
        tool = next((t for t in tools if t.name == "write_file"), None)
        assert tool.need_auth is True

    @pytest.mark.asyncio
    async def test_edit_file_tool(self, temp_workspace):
        test_file = Path(temp_workspace) / "edit.txt"
        test_file.write_text("Line 1\nLine 2\n")

        from closeclaw.tools.file_tools import edit_file_impl
        result = await edit_file_impl(str(test_file), "Line 2", "Line 2 updated")

        assert "Successfully edited" in result
        content = test_file.read_text(encoding="utf-8")
        assert "Line 2 updated" in content

        tools = get_registered_tools()
        tool = next((t for t in tools if t.name == "edit_file"), None)
        assert tool is not None
        assert tool.need_auth is True

    @pytest.mark.asyncio
    async def test_edit_file_tool_dry_run(self, temp_workspace):
        test_file = Path(temp_workspace) / "edit_dry.txt"
        original = "Line A\nLine B\n"
        test_file.write_text(original, encoding="utf-8")

        from closeclaw.tools.file_tools import edit_file_impl
        result = await edit_file_impl(str(test_file), "Line B", "Line B updated", dry_run=True)

        assert "Dry run" in result
        assert test_file.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_delete_file_tool(self, temp_workspace):
        test_file = Path(temp_workspace) / "delete_me.txt"
        test_file.write_text("junk")
        
        from closeclaw.tools.file_tools import delete_file_impl
        result = await delete_file_impl(str(test_file))
        
        assert "File deleted" in result
        assert not test_file.exists()
        
        tools = get_registered_tools()
        tool = next((t for t in tools if t.name == "delete_file"), None)
        assert tool.need_auth is True  # Delete is Sensitive

    @pytest.mark.asyncio
    async def test_delete_lines_tool(self, temp_workspace):
        test_file = Path(temp_workspace) / "lines.txt"
        test_file.write_text("a\nb\nc\nd\n", encoding="utf-8")

        from closeclaw.tools.file_tools import delete_lines_impl
        result = await delete_lines_impl(str(test_file), start_line=2, end_line=3)

        assert "Deleted lines 2-3" in result
        assert test_file.read_text(encoding="utf-8") == "a\nd\n"

        tools = get_registered_tools()
        tool = next((t for t in tools if t.name == "delete_lines"), None)
        assert tool is not None
        assert tool.need_auth is True

    @pytest.mark.asyncio
    async def test_delete_lines_tool_single_line(self, temp_workspace):
        test_file = Path(temp_workspace) / "single_line.txt"
        test_file.write_text("x\ny\nz\n", encoding="utf-8")

        from closeclaw.tools.file_tools import delete_lines_impl
        await delete_lines_impl(str(test_file), start_line=2)

        assert test_file.read_text(encoding="utf-8") == "x\nz\n"

    @pytest.mark.asyncio
    async def test_list_directory_tool(self, temp_workspace):
        (Path(temp_workspace) / "file1.txt").touch()
        (Path(temp_workspace) / "file2.txt").touch()
        subdir = Path(temp_workspace) / "subdir"
        subdir.mkdir()
        (subdir / "file3.txt").touch()
        
        from closeclaw.tools.file_tools import list_files_impl
        
        # Non-recursive
        files = await list_files_impl(str(temp_workspace), recursive=False)
        assert len(files) == 3
        assert "subdir/" in files
        
        # Recursive
        all_files = await list_files_impl(str(temp_workspace), recursive=True)
        assert len(all_files) == 3
        assert "subdir/file3.txt" in all_files

    @pytest.mark.asyncio
    async def test_list_directory_respects_max_entries(self, temp_workspace):
        for idx in range(6):
            (Path(temp_workspace) / f"f{idx}.txt").touch()

        from closeclaw.tools.file_tools import list_files_impl
        files = await list_files_impl(str(temp_workspace), recursive=False, max_entries=3)
        assert len(files) == 4
        assert "Truncated" in files[-1]

    @pytest.mark.asyncio
    async def test_file_exists_tool(self, temp_workspace):
        # exists.txt was touched in list test if temp_workspace is shared, but let's be safe
        test_file = Path(temp_workspace) / "exists.txt"
        test_file.touch()
        
        from closeclaw.tools.file_tools import file_exists_impl
        assert await file_exists_impl(str(test_file)) is True
        assert await file_exists_impl(str(Path(temp_workspace) / "nope.txt")) is False
    
    @pytest.mark.asyncio
    async def test_get_file_size(self, temp_workspace):
        test_file = Path(temp_workspace) / "size.txt"
        content = "12345"
        test_file.write_text(content)
        
        from closeclaw.tools.file_tools import get_file_size_impl
        size = await get_file_size_impl(str(test_file))
        assert size == 5

    @pytest.mark.asyncio
    async def test_write_memory_file_restricted_to_memory_root(self, temp_workspace, monkeypatch):
        from closeclaw.tools.file_tools import write_memory_file_impl

        monkeypatch.chdir(temp_workspace)

        allowed_path = Path("CloseClaw Memory") / "memory" / "note.md"
        result = await write_memory_file_impl(str(allowed_path), "remember this")
        assert "Memory saved" in result
        assert (Path(temp_workspace) / allowed_path).exists()

        outside_path = Path(temp_workspace) / "outside.md"
        with pytest.raises(PermissionError):
            await write_memory_file_impl(str(outside_path), "should fail")

        tools = get_registered_tools()
        tool = next((t for t in tools if t.name == "write_memory_file"), None)
        assert tool is not None
        assert tool.need_auth is False

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_edit_memory_file_restricted_to_memory_root(self, temp_workspace, monkeypatch):
        from closeclaw.tools.file_tools import edit_memory_file_impl

        monkeypatch.chdir(temp_workspace)

        allowed_path = Path("CloseClaw Memory") / "memory" / "note.md"
        allowed_full_path = Path(temp_workspace) / allowed_path
        allowed_full_path.parent.mkdir(parents=True, exist_ok=True)
        allowed_full_path.write_text("a\nb\n", encoding="utf-8")

        result = await edit_memory_file_impl(str(allowed_path), "b", "b-updated")
        assert "Successfully edited" in result
        assert "b-updated" in allowed_full_path.read_text(encoding="utf-8")

        dry = await edit_memory_file_impl(
            str(allowed_path),
            "a\nb-updated\n",
            "a-updated\nb-updated\n",
            dry_run=True,
        )
        assert "Dry run" in dry

        outside_path = Path(temp_workspace) / "outside.md"
        with pytest.raises(PermissionError):
            await edit_memory_file_impl(str(outside_path), "x", "y")

        tools = get_registered_tools()
        tool = next((t for t in tools if t.name == "edit_memory_file"), None)
        assert tool is not None
        assert tool.need_auth is False


class TestShellTools:
    """Test shell operation tools."""
    
    @pytest.mark.asyncio
    async def test_shell_pwd(self):
        from closeclaw.tools.shell_tools import shell_impl
        
        # Use a simple cross-platform command
        cmd = "echo hello" if os.name == "nt" else "pwd"
        result = await shell_impl(cmd)
        
        assert result.get("executed") is True
        assert result.get("returncode") == 0
        assert "hello" in result.get("stdout", "") or result.get("stdout") != ""
        
        tools = get_registered_tools()
        tool = next((t for t in tools if t.name == "shell"), None)
        assert tool is not None
        assert tool.need_auth is True
    
    @pytest.mark.asyncio
    async def test_shell_echo(self):
        from closeclaw.tools.shell_tools import shell_impl
        result = await shell_impl("echo test_output")
        assert result.get("executed") is True
        assert "test_output" in result.get("stdout", "")

    @pytest.mark.asyncio
    async def test_shell_uses_os_sandbox_when_tool_is_protected(self, monkeypatch):
        from closeclaw.tools import shell_tools

        class _FakeExecutor:
            async def run_shell(self, **kwargs):
                return {
                    "returncode": 0,
                    "stdout": "sandboxed",
                    "stderr": "",
                    "executed": True,
                    "sandbox_backend": "fake",
                }

        monkeypatch.setattr(shell_tools.platform, "system", lambda: "Windows")
        monkeypatch.setattr(shell_tools, "get_os_sandbox_executor", lambda: _FakeExecutor())
        shell_tools.configure_shell_sandbox(
            workspace_root=tempfile.gettempdir(),
            os_sandbox_enabled=True,
            os_sandbox_protected_tools=["shell"],
        )

        result = await shell_tools.shell_impl("echo hi")
        assert result.get("sandbox_backend") == "fake"
        assert result.get("stdout") == "sandboxed"

    @pytest.mark.asyncio
    async def test_shell_skips_os_sandbox_when_tool_not_protected(self, monkeypatch):
        from closeclaw.tools import shell_tools

        class _FakeExecutor:
            async def run_shell(self, **kwargs):
                return {
                    "returncode": 0,
                    "stdout": "should_not_be_used",
                    "stderr": "",
                    "executed": True,
                    "sandbox_backend": "fake",
                }

        monkeypatch.setattr(shell_tools.platform, "system", lambda: "Windows")
        monkeypatch.setattr(shell_tools, "get_os_sandbox_executor", lambda: _FakeExecutor())
        shell_tools.configure_shell_sandbox(
            workspace_root=tempfile.gettempdir(),
            os_sandbox_enabled=True,
            os_sandbox_protected_tools=["delete_file"],
        )

        result = await shell_tools.shell_impl("echo direct")
        assert result.get("executed") is True
        assert "direct" in result.get("stdout", "")
        assert result.get("sandbox_backend") != "fake"


class TestWebTools:
    @pytest.mark.asyncio
    async def test_web_search_brave_without_key_falls_back_to_duckduckgo(self, monkeypatch):
        from closeclaw.tools.web_tools import configure_web_search, web_search_impl

        configure_web_search(enabled=True, provider="brave", brave_api_key=None, timeout_seconds=10)

        async def _fake_ddg(query: str, count: int):
            assert query == "python"
            assert count == 3
            return [
                {
                    "title": "DDG Python",
                    "url": "https://duckduckgo.com/?q=python",
                    "snippet": "duckduckgo result",
                }
            ]

        monkeypatch.setattr("closeclaw.tools.web_tools._search_with_duckduckgo", _fake_ddg)
        results = await web_search_impl("python", max_results=3)

        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["title"] == "DDG Python"
        assert "fallback: duckduckgo" in results[0]["snippet"].lower()

    @pytest.mark.asyncio
    async def test_web_search_brave_success_mapping(self, monkeypatch):
        from closeclaw.tools.web_tools import configure_web_search, web_search_impl

        configure_web_search(enabled=True, provider="brave", brave_api_key="BSA-key", timeout_seconds=10)

        class _MockResponse:
            def __init__(self):
                self.content = b"{}"

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "web": {
                        "results": [
                            {
                                "title": "Python",
                                "url": "https://www.python.org",
                                "description": "Official Python website",
                            }
                        ]
                    }
                }

        class _MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, *args, **kwargs):
                return _MockResponse()

        monkeypatch.setattr("closeclaw.tools.web_tools.httpx.AsyncClient", _MockAsyncClient)

        results = await web_search_impl("python", max_results=3)
        assert len(results) == 1
        assert results[0]["title"] == "Python"
        assert results[0]["url"] == "https://www.python.org"

    @pytest.mark.asyncio
    async def test_web_search_duckduckgo_enforces_rate_limit(self, monkeypatch):
        from closeclaw.tools.web_tools import configure_web_search, web_search_impl

        configure_web_search(
            enabled=True,
            provider="duckduckgo",
            brave_api_key=None,
            timeout_seconds=10,
            duckduckgo_min_interval_seconds=1.5,
        )

        calls = {"n": 0}

        async def _fake_rate_limit():
            calls["n"] += 1

        def _fake_ddg_sync(query: str, count: int):
            return [
                {
                    "title": f"{query}-{count}",
                    "url": "https://duckduckgo.com/",
                    "snippet": "ok",
                }
            ]

        monkeypatch.setattr("closeclaw.tools.web_tools._enforce_duckduckgo_rate_limit", _fake_rate_limit)
        monkeypatch.setattr("closeclaw.tools.web_tools._duckduckgo_text_search_sync", _fake_ddg_sync)

        r1 = await web_search_impl("python", max_results=2)
        r2 = await web_search_impl("pytest", max_results=2)

        assert len(r1) == 1
        assert len(r2) == 1
        assert calls["n"] == 2


class TestCronTools:
    @pytest.mark.asyncio
    async def test_call_cron_tool(self):
        from closeclaw.cron import CronService, set_runtime_cron_service
        from closeclaw.tools.cron_tools import call_cron_impl

        class _NoopStore:
            def load(self):
                return {}

            def save(self, _jobs):
                return None

        service = CronService(store_file="unused.json", enabled=True)
        service._store = _NoopStore()  # type: ignore[attr-defined]
        set_runtime_cron_service(service)

        try:
            wake_ms = int(time.time() * 1000) + 60_000
            result = await call_cron_impl(
                str(wake_ms),
                "wake from test",
                channel="cli",
                to="direct",
            )

            assert result["scheduled"] is True
            assert result["job_id"].startswith("wake_")
            assert result["wake_time_ms"] == wake_ms
            assert result["channel"] == "cli"
            assert result["to"] == "direct"

            tools = get_registered_tools()
            tool = next((t for t in tools if t.name == "call_cron"), None)
            assert tool is not None
            assert tool.need_auth is False
        finally:
            set_runtime_cron_service(None)

    @pytest.mark.asyncio
    async def test_call_cron_requires_runtime_service(self):
        from closeclaw.cron import set_runtime_cron_service
        from closeclaw.tools.cron_tools import call_cron_impl

        set_runtime_cron_service(None)
        with pytest.raises(RuntimeError):
            await call_cron_impl("2030-01-01T00:00:00Z")

    @pytest.mark.asyncio
    async def test_call_cron_tolerates_extra_path_argument(self):
        from closeclaw.cron import CronService, set_runtime_cron_service
        from closeclaw.tools.cron_tools import call_cron_impl

        class _NoopStore:
            def load(self):
                return {}

            def save(self, _jobs):
                return None

        service = CronService(store_file="unused.json", enabled=True)
        service._store = _NoopStore()  # type: ignore[attr-defined]
        set_runtime_cron_service(service)

        try:
            wake_ms = int(time.time() * 1000) + 60_000
            result = await call_cron_impl(
                str(wake_ms),
                "extra args",
                channel="cli",
                to="direct",
                path="D:/HALcode",
            )
            assert result["scheduled"] is True
        finally:
            set_runtime_cron_service(None)

    @pytest.mark.asyncio
    async def test_call_cron_requires_explicit_channel(self):
        from closeclaw.cron import CronService, set_runtime_cron_service
        from closeclaw.tools.cron_tools import call_cron_impl

        class _NoopStore:
            def load(self):
                return {}

            def save(self, _jobs):
                return None

        service = CronService(store_file="unused.json", enabled=True)
        service._store = _NoopStore()  # type: ignore[attr-defined]
        set_runtime_cron_service(service)

        try:
            wake_ms = int(time.time() * 1000) + 60_000
            with pytest.raises(ValueError, match="channel is required"):
                await call_cron_impl(str(wake_ms), "missing channel")
        finally:
            set_runtime_cron_service(None)

    @pytest.mark.asyncio
    async def test_call_cron_allows_non_cli_channel_without_to(self):
        from closeclaw.cron import CronService, set_runtime_cron_service
        from closeclaw.tools.cron_tools import call_cron_impl

        class _NoopStore:
            def load(self):
                return {}

            def save(self, _jobs):
                return None

        service = CronService(store_file="unused.json", enabled=True)
        service._store = _NoopStore()  # type: ignore[attr-defined]
        set_runtime_cron_service(service)

        try:
            wake_ms = int(time.time() * 1000) + 60_000
            result = await call_cron_impl(
                str(wake_ms),
                "missing target",
                channel="telegram",
            )
            assert result["scheduled"] is True
            assert result["channel"] == "telegram"
            assert result["to"] == "direct"
        finally:
            set_runtime_cron_service(None)

    @pytest.mark.asyncio
    async def test_call_cron_allows_discord_without_to(self):
        from closeclaw.cron import CronService, set_runtime_cron_service
        from closeclaw.tools.cron_tools import call_cron_impl

        class _NoopStore:
            def load(self):
                return {}

            def save(self, _jobs):
                return None

        service = CronService(store_file="unused.json", enabled=True)
        service._store = _NoopStore()  # type: ignore[attr-defined]
        set_runtime_cron_service(service)

        try:
            wake_ms = int(time.time() * 1000) + 60_000
            result = await call_cron_impl(
                str(wake_ms),
                "discord wake",
                channel="discord",
            )
            assert result["scheduled"] is True
            assert result["channel"] == "discord"
            assert result["to"] == "direct"
        finally:
            set_runtime_cron_service(None)


class TestSpawnTools:
    @pytest.mark.asyncio
    async def test_spawn_requires_runtime_manager(self):
        from closeclaw.subagent import set_runtime_subagent_manager
        from closeclaw.tools.spawn_tools import spawn_impl

        set_runtime_subagent_manager(None)
        with pytest.raises(RuntimeError):
            await spawn_impl("Research model options")

    @pytest.mark.asyncio
    async def test_spawn_creates_background_task(self):
        from closeclaw.subagent import set_runtime_subagent_manager
        from closeclaw.tools.spawn_tools import spawn_impl

        class _StubManager:
            async def spawn(self, **kwargs):
                return {
                    "status": "task_created",
                    "task_id": "#123",
                    "message": "Subagent task created: #123",
                    "args": kwargs,
                }

        set_runtime_subagent_manager(_StubManager())
        try:
            result = await spawn_impl(
                "Summarize open pull requests",
                label="pr-summary",
                channel="telegram",
                to="chat-42",
                timeout_seconds=33.0,
            )
            assert result["status"] == "task_created"
            assert result["task_id"] == "#123"
            assert result["args"]["session_key"] == "telegram:chat-42"
            assert result["args"]["timeout_seconds"] == 33.0

            tools = get_registered_tools()
            tool = next((t for t in tools if t.name == "spawn"), None)
            assert tool is not None
            assert tool.need_auth is False
        finally:
            set_runtime_subagent_manager(None)

    @pytest.mark.asyncio
    async def test_spawn_tolerates_extra_path_argument(self):
        from closeclaw.subagent import set_runtime_subagent_manager
        from closeclaw.tools.spawn_tools import spawn_impl

        class _StubManager:
            async def spawn(self, **kwargs):
                return {
                    "status": "task_created",
                    "task_id": "#124",
                    "message": "Subagent task created: #124",
                    "args": kwargs,
                }

        set_runtime_subagent_manager(_StubManager())
        try:
            result = await spawn_impl(
                "Summarize open pull requests",
                path="D:/HALcode",
            )
            assert result["status"] == "task_created"
            assert result["task_id"] == "#124"
            assert result["args"]["session_key"] == "cli:direct"
        finally:
            set_runtime_subagent_manager(None)

    @pytest.mark.asyncio
    async def test_task_status_requires_runtime_manager(self):
        from closeclaw.subagent import set_runtime_subagent_manager
        from closeclaw.tools.spawn_tools import task_status_impl

        set_runtime_subagent_manager(None)
        with pytest.raises(RuntimeError):
            await task_status_impl("#001")

    @pytest.mark.asyncio
    async def test_task_status_returns_manager_snapshot(self):
        from closeclaw.subagent import set_runtime_subagent_manager
        from closeclaw.tools.spawn_tools import task_status_impl

        class _StubManager:
            def get_task_status(self, task_id: str):
                return {
                    "task_id": task_id,
                    "status": "completed",
                    "result": {"ok": True},
                    "error": None,
                }

        set_runtime_subagent_manager(_StubManager())
        try:
            result = await task_status_impl("#777")
            assert result["task_id"] == "#777"
            assert result["status"] == "completed"

            tools = get_registered_tools()
            tool = next((t for t in tools if t.name == "task_status"), None)
            assert tool is not None
            assert tool.need_auth is False
        finally:
            set_runtime_subagent_manager(None)

    @pytest.mark.asyncio
    async def test_task_cancel_requires_runtime_manager(self):
        from closeclaw.subagent import set_runtime_subagent_manager
        from closeclaw.tools.spawn_tools import task_cancel_impl

        set_runtime_subagent_manager(None)
        with pytest.raises(RuntimeError):
            await task_cancel_impl("#001")

    @pytest.mark.asyncio
    async def test_task_cancel_calls_manager(self):
        from closeclaw.subagent import set_runtime_subagent_manager
        from closeclaw.tools.spawn_tools import task_cancel_impl

        class _StubManager:
            async def cancel_task(self, task_id: str):
                return {
                    "task_id": task_id,
                    "cancelled": True,
                    "status": "cancelling",
                    "error_code": None,
                }

        set_runtime_subagent_manager(_StubManager())
        try:
            result = await task_cancel_impl("#321")
            assert result["task_id"] == "#321"
            assert result["cancelled"] is True

            tools = get_registered_tools()
            tool = next((t for t in tools if t.name == "task_cancel"), None)
            assert tool is not None
            assert tool.need_auth is False
        finally:
            set_runtime_subagent_manager(None)






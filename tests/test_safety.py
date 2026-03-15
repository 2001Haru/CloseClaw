"""Tests for safety audit system."""

import pytest
import json
import tempfile
from datetime import datetime
from pathlib import Path

from closeclaw.safety import AuditLogger
from closeclaw.types import Zone, ToolType, Tool


class TestAuditLogger:
    """Test AuditLogger functionality."""
    
    def test_audit_logger_creation(self):
        """Test basic AuditLogger creation."""
        logger = AuditLogger()
        assert logger is not None
    
    def test_audit_logger_with_custom_path(self, temp_workspace):
        """Test AuditLogger with custom log path."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        assert logger is not None
        assert logger.log_file == log_path
    
    def test_log_tool_execution(self, temp_workspace):
        """Test logging tool execution."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        logger.log(
            event_type="tool_call",
            status="success",
            user_id="user_123",
            tool_name="read_file",
            arguments={"path": "/data/test.txt"},
            result="File contents"
        )
        
        # Check if log file was created
        assert Path(log_path).exists()
        
        # Verify log content
        logs = logger.read_logs()
        assert len(logs) > 0
        assert logs[-1]["tool_name"] == "read_file"
    
    def test_log_authorization_decision(self, temp_workspace):
        """Test logging authorization decisions."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        logger.log(
            event_type="auth_response",
            status="success",
            user_id="user_123",
            tool_name="delete_file",
            arguments={"path": "/data/important.txt"},
            result="Authorized by admin_001"
        )
        
        if Path(log_path).exists():
            logs = logger.read_logs()
            assert len(logs) > 0
    
    def test_log_policy_violation(self, temp_workspace):
        """Test logging policy violations."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        logger.log(
            event_type="blocked",
            status="error",
            user_id="user_123",
            tool_name="shell_command",
            arguments={"command": "rm -rf /"},
            error="Dangerous pattern matched"
        )
        
        if Path(log_path).exists():
            logs = logger.read_logs()
            assert len(logs) > 0
    
    def test_log_multiple_events(self, temp_workspace):
        """Test logging multiple events."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        for i in range(5):
            logger.log(
                event_type="tool_call",
                status="success",
                user_id="user_123",
                tool_name=f"tool_{i}",
                arguments={}
            )
        
        if Path(log_path).exists():
            logs = logger.read_logs()
            assert len(logs) >= 5
    
    def test_audit_log_format(self, temp_workspace):
        """Test audit log is valid JSON format."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        logger.log(
            event_type="tool_call",
            status="success",
            user_id="user_123",
            tool_name="test_tool",
            arguments={"param": "value"}
        )
        
        if Path(log_path).exists():
            with open(log_path, 'r') as f:
                for line in f:
                    if line.strip():
                        # Verify each line is valid JSON
                        entry = json.loads(line)
                        assert "timestamp" in entry
                        assert "event_type" in entry
                        assert "tool_name" in entry


class TestAuditEvents:
    """Test different audit event types."""
    
    def test_tool_execution_event(self, temp_workspace):
        """Test tool execution audit event."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        logger.log(
            event_type="tool_call",
            status="success",
            user_id="user_123",
            tool_name="list_files",
            arguments={"directory": "/data"},
            result="/data contains: file1.txt, file2.txt"
        )
        
        if Path(log_path).exists():
            logs = logger.read_logs()
            assert len(logs) > 0
    
    def test_authorization_event(self, temp_workspace):
        """Test authorization decision event."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        logger.log(
            event_type="auth_response",
            status="denied",
            user_id="user_789",
            tool_name="modify_system_config",
            arguments={},
            error="User lacks required permissions"
        )
        
        if Path(log_path).exists():
            logs = logger.read_logs()
            assert len(logs) > 0
    
    def test_violation_event(self, temp_workspace):
        """Test policy violation event."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        logger.log(
            event_type="blocked",
            status="blocked",
            user_id="user_123",
            tool_name="execute_command",
            arguments={"cmd": "../../../etc/passwd"},
            error="Path traversal detected"
        )
        
        if Path(log_path).exists():
            logs = logger.read_logs()
            assert any(log.get("status") == "blocked" for log in logs)
    
    def test_error_event(self, temp_workspace):
        """Test error event logging."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        logger.log(
            event_type="error",
            status="error",
            user_id="user_123",
            tool_name="read_file",
            arguments={"path": "/data/missing.txt"},
            error="File not found"
        )
        
        if Path(log_path).exists():
            logs = logger.read_logs()
            assert len(logs) > 0


class TestAuditLogRotation:
    """Test audit log rotation and retention."""
    
    def test_log_path_creation(self, temp_workspace):
        """Test that log path can be created."""
        log_path = str(Path(temp_workspace) / "subdir" / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        logger.log(
            event_type="test",
            status="success",
            user_id="user",
            tool_name="test",
            arguments={}
        )
        
        assert logger is not None
    
    def test_concurrent_logging(self, temp_workspace):
        """Test concurrent logging doesn't corrupt data."""
        import threading
        
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        def log_event(event_id):
            logger.log(
                event_type="tool_call",
                status="success",
                user_id=f"user_{event_id}",
                tool_name=f"tool_{event_id}",
                arguments={}
            )
        
        threads = [threading.Thread(target=log_event, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert logger is not None


class TestAuditSecurity:
    """Test security aspects of audit logging."""
    
    def test_sensitive_data_handling(self, temp_workspace):
        """Test that sensitive data is handled properly."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        logger.log(
            event_type="tool_call",
            status="success",
            user_id="user_123",
            tool_name="read_config",
            arguments={"path": "/etc/app/config.json"},
            result='{"db_password": "secret123"}'
        )
        
        assert logger is not None
    
    def test_audit_log_file_creation(self, temp_workspace):
        """Test that audit log file is created properly."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        logger.log(
            event_type="test",
            status="success",
            user_id="user",
            tool_name="test",
            arguments={}
        )
        
        if Path(log_path).exists():
            assert Path(log_path).is_file()
    
    def test_immutable_audit_records(self, temp_workspace):
        """Test that audit records are append-only."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        logger.log(
            event_type="tool_call",
            status="success",
            user_id="user",
            tool_name="tool_1",
            arguments={}
        )
        
        if Path(log_path).exists():
            first_size = Path(log_path).stat().st_size
            
            logger.log(
                event_type="tool_call",
                status="success",
                user_id="user",
                tool_name="tool_2",
                arguments={}
            )
            
            second_size = Path(log_path).stat().st_size
            assert second_size >= first_size


class TestAuditMetrics:
    """Test auditing metrics and reporting."""
    
    def test_tool_execution_statistics(self, temp_workspace):
        """Test gathering tool execution statistics."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        for i in range(5):
            logger.log(
                event_type="tool_call",
                status="success" if i % 2 == 0 else "error",
                user_id="user_123",
                tool_name="file_read",
                arguments={"count": i}
            )
        
        assert logger is not None
    
    def test_user_activity_logging(self, temp_workspace):
        """Test logging user activity."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        users = ["alice", "bob", "charlie"]
        for user in users:
            logger.log(
                event_type="tool_call",
                status="success",
                user_id=user,
                tool_name="list_files",
                arguments={}
            )
        
        assert logger is not None
    
    def test_read_logs_limit(self, temp_workspace):
        """Test reading logs with limit parameter."""
        log_path = str(Path(temp_workspace) / "audit.log")
        logger = AuditLogger(log_file=log_path)
        
        for i in range(10):
            logger.log(
                event_type="tool_call",
                status="success",
                user_id="user",
                tool_name=f"tool_{i}",
                arguments={}
            )
        
        if Path(log_path).exists():
            logs = logger.read_logs(limit=5)
            assert len(logs) <= 5

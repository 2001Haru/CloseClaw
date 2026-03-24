"""Safety module (audit logging and compliance)."""

import logging
from datetime import datetime, timezone
from typing import Any, Optional
import json

logger = logging.getLogger(__name__)


class AuditLogger:
    """Audit log for all operations."""
    
    def __init__(self, log_file: str = "audit.log", log_path: Optional[str] = None):
        # Keep log_path for backward compatibility with older call sites.
        self.log_file = log_path or log_file
    
    def log(self, 
            event_type: str,
            status: str,
            user_id: str,
            tool_name: str,
            arguments: dict[str, Any],
            result: Optional[str] = None,
            error: Optional[str] = None) -> None:
        """Log an audit event.
        
        Args:
            event_type: "tool_call", "auth_request", "auth_response", "blocked"
            status: "success", "error", "blocked", "pending"
            user_id: User who initiated operation
            tool_name: Tool name
            arguments: Tool arguments
            result: Result description
            error: Error message if failed
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "status": status,
            "user_id": user_id,
            "tool_name": tool_name,
            "arguments": str(arguments)[:200],  # Truncate sensitive data
            "result": result,
            "error": error,
        }
        
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
    
    def read_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        """Read recent audit logs."""
        logs = []
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        log_entry = json.loads(line)
                        logs.append(log_entry)
                    except:
                        pass
        except FileNotFoundError:
            pass
        
        return logs[-limit:]

    def log_tool_execution(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: str,
        session_id: str,
        success: bool,
        duration_ms: Optional[int] = None,
    ) -> None:
        status = "success" if success else "error"
        result = f"session_id={session_id}, duration_ms={duration_ms}"
        self.log(
            event_type="tool_call",
            status=status,
            user_id=user_id,
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            error=None if success else "tool execution failed",
        )

    def log_authorization_decision(
        self,
        tool_name: str,
        user_id: str,
        session_id: str,
        approved: bool,
        approver_id: str,
        reason: Optional[str] = None,
    ) -> None:
        self.log(
            event_type="auth_response",
            status="approved" if approved else "rejected",
            user_id=user_id,
            tool_name=tool_name,
            arguments={"session_id": session_id, "approver_id": approver_id},
            result=reason,
        )

    def log_policy_violation(
        self,
        tool_name: str,
        user_id: str,
        session_id: str,
        violation_type: str,
        description: str,
        severity: str = "medium",
    ) -> None:
        self.log(
            event_type="blocked",
            status="blocked",
            user_id=user_id,
            tool_name=tool_name,
            arguments={
                "session_id": session_id,
                "violation_type": violation_type,
                "severity": severity,
            },
            error=description,
        )


from .auth_reasoning import build_auth_reason
from .guardian import ConsensusGuardian, GuardianDecision
from .security_mode import SecurityMode, normalize_security_mode

__all__ = [
    "AuditLogger",
    "build_auth_reason",
    "ConsensusGuardian",
    "GuardianDecision",
    "SecurityMode",
    "normalize_security_mode",
]


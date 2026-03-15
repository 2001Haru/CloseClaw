"""Safety module (audit logging and compliance)."""

import logging
from datetime import datetime
from typing import Any, Optional
import json

logger = logging.getLogger(__name__)


class AuditLogger:
    """Audit log for all operations."""
    
    def __init__(self, log_file: str = "audit.log"):
        self.log_file = log_file
    
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
            "timestamp": datetime.utcnow().isoformat(),
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


__all__ = [
    "AuditLogger",
]

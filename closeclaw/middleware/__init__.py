"""Middleware system for permission checks and safety guards."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional
import re
from datetime import datetime, timezone

from ..safety import SecurityMode, normalize_security_mode, build_auth_reason, ConsensusGuardian
from ..types import Tool, Session, OperationType, ToolType

logger = logging.getLogger(__name__)


class Middleware(ABC):
    """Base middleware interface."""
    
    @abstractmethod
    async def process(self,
                     tool: Tool,
                     arguments: dict[str, Any],
                     session: Optional[Session],
                     **kwargs: Any) -> dict[str, Any]:
        """Process middleware logic.
        
        Returns: {
            "status": "allow" | "block" | "requires_auth",
            "reason": str (if status != "allow"),
            "auth_request": dict (if status == "requires_auth")
        }
        """
        ...


class SafetyGuard(Middleware):
    """First middleware: Command blacklist and pattern matching for dangerous operations."""
    
    # Windows dangerous commands
    DANGEROUS_PATTERNS = [
        r'\bdel\s+/s',  # Recursive delete
        r'\bformat\s+',  # Disk format
        r'\bnet\s+user',  # User management
        r'\breg\s+delete',  # Registry deletion
        r'\btaskkill\s+/f',  # Force kill
        r'\bsfc\s+/scannow',  # System file checker
        r'\bsubst\s+',  # Drive substitution
    ]
    
    # Unix dangerous patterns
    UNIX_PATTERNS = [
        r'rm\s+-rf\s+/',  # Recursive recursive remove at root
        r'sudo\s+rm\s+-rf',  # Sudo rm -rf
        r'mv\s+/\s+',  # Move filesystem root
        r'mkfs',  # Make filesystem
        r'dd\s+if=/dev/zero\s+of=/',  # Disk wipe
    ]
    
    def __init__(self, custom_rules: Optional[list[str]] = None):
        """Initialize safety guard.
        
        Args:
            custom_rules: Additional regex patterns to block
        """
        self.patterns = [re.compile(p, re.IGNORECASE) for p in (
            self.DANGEROUS_PATTERNS + self.UNIX_PATTERNS
        )]
        if custom_rules:
            self.patterns.extend([re.compile(p, re.IGNORECASE) for p in custom_rules])
    
    async def process(self,
                     tool: Tool,
                     arguments: dict[str, Any],
                     session: Optional[Session],
                     **kwargs: Any) -> dict[str, Any]:
        """Check for dangerous patterns in shell commands."""
        
        # Only process SHELL type tools
        if tool.type != ToolType.SHELL:
            return {"status": "allow"}
        
        command = arguments.get("command", "")
        
        for pattern in self.patterns:
            if pattern.search(command):
                logger.warning(f"Dangerous command blocked: {command[:80]}")
                return {
                    "status": "block",
                    "reason": f"Command matches dangerous pattern",
                }
        
        return {"status": "allow"}


class PathSandbox(Middleware):
    """Second middleware: Path validation for file operations."""
    
    def __init__(self, workspace_root: str):
        """Initialize path sandbox.
        
        Args:
            workspace_root: Root directory for allowed file operations
        """
        import os
        self.workspace_root = os.path.abspath(workspace_root)
    
    async def process(self,
                     tool: Tool,
                     arguments: dict[str, Any],
                     session: Optional[Session],
                     **kwargs: Any) -> dict[str, Any]:
        """Validate file paths are within workspace_root."""
        
        # Only process FILE type tools
        if tool.type != ToolType.FILE:
            return {"status": "allow"}
        
        import os

        # Not all FILE-type tools are path-based (e.g. spawn/task_status).
        # Only normalize/sandbox when the call explicitly carries a path field.
        if "path" not in arguments:
            return {"status": "allow"}

        file_path = arguments.get("path", "")

        # Normalize relative paths against workspace root instead of process cwd.
        # This keeps file tool behavior deterministic no matter where the process is started.
        if os.path.isabs(file_path):
            abs_path = os.path.abspath(file_path)
        else:
            abs_path = os.path.abspath(os.path.join(self.workspace_root, file_path))

        # Rewrite argument in-place so downstream tool handlers use the normalized path.
        arguments["path"] = abs_path
        
        # Check if path is within workspace_root
        try:
            rel_path = os.path.relpath(abs_path, self.workspace_root)
            if rel_path.startswith(".."):
                logger.warning(f"Path traversal attempt blocked: {abs_path}")
                return {
                    "status": "block",
                    "reason": f"Path is outside workspace: {abs_path}",
                }
        except ValueError:
            # Different drives on Windows
            logger.warning(f"Cross-drive path attempt blocked: {abs_path}")
            return {
                "status": "block",
                "reason": f"Path is on different drive or outside workspace: {abs_path}",
            }
        
        return {"status": "allow"}


class AuthPermissionMiddleware(Middleware):
    """Third middleware: need_auth-based permission system."""

    def __init__(
        self,
        default_need_auth: bool = False,
        security_mode: str | SecurityMode = SecurityMode.SUPERVISED,
        consensus_guardian: Optional[ConsensusGuardian] = None,
    ):
        """Initialize permission middleware.

        Args:
            default_need_auth: Default auth behavior when no hints are provided.
            security_mode: One of autonomous/supervised/consensus.
            consensus_guardian: Sentinel reviewer for consensus mode.
        """
        self.default_need_auth = default_need_auth
        self.security_mode = normalize_security_mode(security_mode)
        self.consensus_guardian = consensus_guardian
    
    async def process(self,
                     tool: Tool,
                     arguments: dict[str, Any],
                     session: Optional[Session],
                     user_id: Optional[str] = None,
                     **kwargs: Any) -> dict[str, Any]:
        """Check if tool requires authorization based on need_auth."""

        requires_auth = getattr(tool, "need_auth", self.default_need_auth)

        if not requires_auth:
            return {"status": "allow"}

        # Generate diff preview for FILE type operations
        diff_preview = None
        if tool.type == ToolType.FILE:
            diff_preview = self._generate_diff_preview(tool, arguments)

        reason = build_auth_reason(
            tool_name=tool.name,
            tool_description=tool.description,
            arguments=arguments,
            diff_preview=diff_preview,
        )

        if self.security_mode == SecurityMode.AUTONOMOUS:
            return {
                "status": "allow",
                "auth_mode": self.security_mode.value,
                "reason": reason,
            }

        if self.security_mode == SecurityMode.CONSENSUS:
            if self.consensus_guardian is None:
                return {
                    "status": "block",
                    "reason": "Consensus mode requires a configured guardian reviewer.",
                    "reason_code": "GUARDIAN_NOT_CONFIGURED",
                    "auth_mode": self.security_mode.value,
                }

            decision = await self.consensus_guardian.review(
                {
                    "tool_name": tool.name,
                    "tool_description": tool.description,
                    "arguments": arguments,
                    "reason": reason,
                    "diff_preview": diff_preview,
                }
            )
            if not decision.approved:
                return {
                    "status": "block",
                    "reason": decision.comment or "Consensus sentinel rejected this request.",
                    "reason_code": decision.reason_code,
                    "auth_mode": self.security_mode.value,
                }

            # Consensus mode is fully automated on approve: no manual auth step.
            return {
                "status": "allow",
                "auth_mode": self.security_mode.value,
                "reason": reason,
                "reason_code": decision.reason_code,
                "guardian_comment": decision.comment,
            }

        auth_id = f"auth_{datetime.now(timezone.utc).timestamp()}"
        auth_request = {
            "id": auth_id,
            "tool_name": tool.name,
            "user_id": session.user_id if session else user_id,
            "description": f"{tool.name}: {tool.description}",
            "arguments": arguments,
            "operation_type": arguments.get("operation", "unknown"),
            "diff_preview": diff_preview,
            "reason": reason,
            "auth_mode": self.security_mode.value,
        }
        return {
            "status": "requires_auth",
            "auth_request_id": auth_id,
            "auth_request": auth_request,
            "tool_name": tool.name,
            "arguments": arguments,
            "operation_type": arguments.get("operation", "unknown"),
            "description": f"{tool.name}: {tool.description}",
            "diff_preview": diff_preview,
            "reason": reason,
            "auth_mode": self.security_mode.value,
        }

        return {"status": "allow"}
    
    def _generate_diff_preview(self,
                              tool: Tool,
                              arguments: dict[str, Any]) -> Optional[str]:
        """Generate structured diff preview for file operations.
        
        Returns format:
        ```
        鏂囦欢锛歱ath/to/file.txt | 鎿嶄綔锛歮odify
        鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        - old line 1
        + new line 2
        鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        ```
        """
        try:
            import os

            tool_name = (tool.name or "").strip().lower()
            operation = arguments.get("operation")
            if not operation:
                if tool_name in {"write_file", "edit_file"}:
                    operation = "write"
                elif tool_name in {"delete_file", "delete_lines"}:
                    operation = "delete"
                else:
                    operation = "modify"

            path = arguments.get("path", "unknown")

            old_content = arguments.get("old_content")
            new_content = arguments.get("new_content")

            if old_content is None or new_content is None:
                if tool_name == "write_file":
                    new_content = str(arguments.get("content", ""))
                    if isinstance(path, str) and os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as f:
                            old_content = f.read()
                    else:
                        old_content = ""
                elif tool_name == "edit_file":
                    old_content = str(arguments.get("old_text", ""))
                    new_content = str(arguments.get("new_text", ""))
                elif tool_name == "delete_file":
                    if isinstance(path, str) and os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as f:
                            old_content = f.read()
                    else:
                        old_content = ""
                    new_content = ""
                elif tool_name == "delete_lines":
                    old_content = ""
                    if isinstance(path, str) and os.path.exists(path):
                        start_line = int(arguments.get("start_line", 1))
                        end_line = arguments.get("end_line")
                        lines = []
                        with open(path, "r", encoding="utf-8") as f:
                            lines = f.read().splitlines()
                        if lines:
                            effective_end = int(end_line) if end_line is not None else start_line
                            effective_end = min(effective_end, len(lines))
                            if start_line <= effective_end and start_line >= 1:
                                old_content = "\n".join(lines[start_line - 1:effective_end])
                    new_content = ""

            old_content = str(old_content or "")
            new_content = str(new_content or "")
            
            # Generate simple diff
            old_lines = old_content.split("\n")[:5]  # Max 5 context lines
            new_lines = new_content.split("\n")[:5]
            
            diff_lines = [
                f"File: {path} | Operation: {operation}",
                "-" * 50,
            ]
            
            # Simple line-by-line diff
            max_lines = max(len(old_lines), len(new_lines))
            for i in range(max_lines):
                old = old_lines[i] if i < len(old_lines) else ""
                new = new_lines[i] if i < len(new_lines) else ""
                
                if old != new:
                    if old:
                        diff_lines.append(f"- {old[:80]}")
                    if new:
                        diff_lines.append(f"+ {new[:80]}")

            if len(diff_lines) <= 2:
                # Always provide non-empty preview context for sentinel review.
                diff_lines.append("~ content preview unavailable; review path and operation metadata")
            
            diff_lines.append("-" * 50)
            return "\n".join(diff_lines)
            
        except Exception as e:
            logger.error(f"Diff preview generation error: {e}")
            return None


class MiddlewareChain:
    """Chain of middleware processors."""
    
    def __init__(self, middlewares: Optional[list[Middleware]] = None):
        """Initialize middleware chain.
        
        Args:
            middlewares: List of middleware in order of execution
        """
        self.middlewares = middlewares or []
    
    def add_middleware(self, middleware: Middleware) -> None:
        """Add middleware to the chain."""
        self.middlewares.append(middleware)
    
    async def check_permission(self,
                              tool: Tool,
                              arguments: dict[str, Any],
                              session: Optional[Session] = None,
                              user_id: Optional[str] = None,
                              **kwargs: Any) -> dict[str, Any]:
        """Process tool call through middleware chain.
        
        Returns: {
            "status": "allow" | "block" | "requires_auth",
            ...
        }
        """
        aggregated_allow: dict[str, Any] = {"status": "allow"}

        for middleware in self.middlewares:
            result = await middleware.process(
                tool=tool,
                arguments=arguments,
                session=session,
                user_id=user_id,
                **kwargs
            )
            
            # If any middleware blocks or requires auth, return immediately
            if result.get("status") in ["block", "requires_auth"]:
                return result

            if result.get("status") == "allow":
                for key, value in result.items():
                    if key == "status" or value is None:
                        continue
                    aggregated_allow[key] = value
        
        return aggregated_allow
    
    # Backwards-compatible alias expected by older tests and integrations
    async def execute(self,
                      tool: Tool,
                      arguments: dict[str, Any],
                      session: Optional[Session] = None,
                      user_id: Optional[str] = None,
                      **kwargs: Any) -> dict[str, Any]:
        """Legacy method name mapping to check_permission for compatibility."""
        return await self.check_permission(tool=tool, arguments=arguments, session=session, user_id=user_id, **kwargs)


__all__ = [
    "Middleware",
    "SafetyGuard",
    "PathSandbox",
    "AuthPermissionMiddleware",
    "MiddlewareChain",
]


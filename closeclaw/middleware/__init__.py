"""Middleware system for permission checks and safety guards."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional
import re
from datetime import datetime

from ..types import Tool, Session, Zone, OperationType, ToolType

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
        
        file_path = arguments.get("path", "")
        
        # Convert relative to absolute
        abs_path = os.path.abspath(file_path)
        
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


class ZoneBasedPermission(Middleware):
    """Third middleware: Zone-based permission system.
    
    Backwards compatibility:
    - Accepts legacy admin_user_id kwarg (ignored) to avoid breaking tests using the older signature.
    """
    
    def __init__(self, require_auth_for_zones: list[Zone] = None, admin_user_id: Optional[str] = None):
        """Initialize zone-based permissions.
        
        Args:
            require_auth_for_zones: Zones requiring auth (default: [Zone.ZONE_C])
            admin_user_id: Legacy compatibility parameter (ignored)
        """
        self.require_auth_for_zones = require_auth_for_zones or [Zone.ZONE_C]
    
    async def process(self,
                     tool: Tool,
                     arguments: dict[str, Any],
                     session: Optional[Session],
                     user_id: Optional[str] = None,
                     **kwargs: Any) -> dict[str, Any]:
        """Check if tool requires authorization based on zone."""
        
        if tool.zone in self.require_auth_for_zones:
            # Generate diff preview for FILE type operations
            diff_preview = None
            if tool.type == ToolType.FILE and arguments.get("operation") in ["write", "delete"]:
                diff_preview = self._generate_diff_preview(tool, arguments)
            
            auth_id = f"auth_{datetime.utcnow().timestamp()}"
            auth_request = {
                "id": auth_id,
                "tool_name": tool.name,
                "user_id": session.user_id if session else user_id,
                "description": f"{tool.name}: {tool.description}",
                "arguments": arguments,
                "operation_type": arguments.get("operation", "unknown"),
                "diff_preview": diff_preview,
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
            }
        
        return {"status": "allow"}
    
    def _generate_diff_preview(self,
                              tool: Tool,
                              arguments: dict[str, Any]) -> Optional[str]:
        """Generate structured diff preview for file operations.
        
        Returns format:
        ```
        文件：path/to/file.txt | 操作：modify
        ─────────────────────
        - old line 1
        + new line 2
        ─────────────────────
        ```
        """
        try:
            operation = arguments.get("operation", "unknown")
            path = arguments.get("path", "unknown")
            
            old_content = arguments.get("old_content", "")
            new_content = arguments.get("new_content", "")
            
            # Generate simple diff
            old_lines = old_content.split("\n")[:5]  # Max 5 context lines
            new_lines = new_content.split("\n")[:5]
            
            diff_lines = [
                f"文件：{path} | 操作：{operation}",
                "─" * 50,
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
            
            diff_lines.append("─" * 50)
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
        
        return {"status": "allow"}
    
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
    "ZoneBasedPermission",
    "MiddlewareChain",
]

"""Middleware system for permission checks and safety guards."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional
import re
import platform
import time
from datetime import datetime, timezone

from ..safety import SecurityMode, normalize_security_mode, build_auth_reason, ConsensusGuardian
from ..types import Tool, Session, ToolType

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
    """First middleware: command safety checks with lightweight platform-aware rules."""

    _GENERAL_RULES = [
        (r'(:\(\)\s*\{\s*:\|\:&\s*\};:)', "fork_bomb_signature"),
        (r'(?:^|[\s;|&])(?:curl|wget)\b[^|]*\|\s*(?:sh|bash|zsh|pwsh|powershell)\b', "pipe_to_shell"),
        (r'\bpowershell(?:\.exe)?\b[^;\n\r]*\s-(?:enc|encodedcommand)\b', "powershell_encoded_command"),
        (r'\b(?:certutil|base64)\b[^|]*\|\s*(?:sh|bash|pwsh|powershell)\b', "decoded_payload_pipe_to_shell"),
    ]
    _STRICT_GENERAL_RULES = [
        (r'\b(?:curl|wget)\b[^\n\r]*(?:\s-o\b|\s-O\b)', "download_binary_or_script"),
        (r'\bInvoke-WebRequest\b[^\n\r]*(?:\s-OutFile\b|\s-UseBasicParsing\b)', "powershell_download_payload"),
    ]

    _WINDOWS_CMD_RULES = [
        (r'\b(?:del|erase)\b[^\n\r]*(?:\s/s\b|\s/q\b)', "windows_recursive_delete"),
        (r'\brmdir\b[^\n\r]*(?:\s/s\b|\s/q\b)', "windows_recursive_rmdir"),
        (r'\bformat(?:\.com)?\b', "windows_disk_format"),
        (r'\breg\s+delete\b', "windows_registry_delete"),
        (r'\bvssadmin\s+delete\b', "windows_shadow_copy_delete"),
        (r'\bbcdedit\b', "windows_boot_config_modify"),
    ]

    _POWERSHELL_RULES = [
        (r'\bRemove-Item\b[^\n\r]*-Recurse\b[^\n\r]*-Force\b', "powershell_force_recursive_delete"),
        (r'\bClear-Content\b[^\n\r]*\b-Force\b', "powershell_force_content_clear"),
        (r'\bSet-ExecutionPolicy\b', "powershell_execution_policy_change"),
        (r'\bStart-Process\b[^\n\r]*-Verb\s+RunAs\b', "powershell_privilege_escalation"),
        (r'\bInvoke-Expression\b', "powershell_invoke_expression"),
    ]

    _UNIX_RULES = [
        (r'\brm\b[^\n\r]*\s-rf\b', "unix_force_recursive_delete"),
        (r'\bchmod\b[^\n\r]*\s-R\s+777\s+/', "unix_world_writable_root"),
        (r'\bchown\b[^\n\r]*\s-R\b[^\n\r]*\s+/\b', "unix_recursive_chown_root"),
        (r'\bmkfs(?:\.[a-z0-9_]+)?\b', "unix_filesystem_format"),
        (r'\bdd\b[^\n\r]*\bof=/dev/(?:sd|vd|xvd|nvme)\w*', "unix_raw_disk_overwrite"),
    ]
    _STRICT_UNIX_RULES = [
        (r'\b(?:iptables|ufw)\b[^\n\r]*\b(?:flush|reset|disable)\b', "unix_firewall_disable"),
    ]

    _RISK_LEVELS = {
        "windows_disk_format": "critical",
        "unix_filesystem_format": "critical",
        "unix_raw_disk_overwrite": "critical",
        "fork_bomb_signature": "critical",
        "windows_registry_delete": "high",
        "windows_shadow_copy_delete": "high",
        "windows_boot_config_modify": "high",
        "windows_recursive_delete": "high",
        "windows_recursive_rmdir": "high",
        "powershell_force_recursive_delete": "high",
        "unix_force_recursive_delete": "high",
        "pipe_to_shell": "high",
        "powershell_encoded_command": "high",
        "decoded_payload_pipe_to_shell": "high",
        "powershell_execution_policy_change": "medium",
        "powershell_privilege_escalation": "high",
        "powershell_invoke_expression": "high",
        "unix_world_writable_root": "high",
        "unix_recursive_chown_root": "high",
        "download_binary_or_script": "medium",
        "powershell_download_payload": "medium",
        "unix_firewall_disable": "high",
        "custom_rule": "high",
    }
    
    def __init__(self, custom_rules: Optional[list[str]] = None, profile: str = "balanced"):
        """Initialize safety guard with lightweight platform-aware matchers."""
        normalized_profile = (profile or "balanced").strip().lower()
        if normalized_profile not in {"balanced", "strict"}:
            normalized_profile = "balanced"
        self.profile = normalized_profile

        self._general_patterns = [(re.compile(p, re.IGNORECASE), code) for p, code in self._GENERAL_RULES]
        if self.profile == "strict":
            self._general_patterns.extend(
                [(re.compile(p, re.IGNORECASE), code) for p, code in self._STRICT_GENERAL_RULES]
            )
        self._windows_cmd_patterns = [(re.compile(p, re.IGNORECASE), code) for p, code in self._WINDOWS_CMD_RULES]
        self._powershell_patterns = [(re.compile(p, re.IGNORECASE), code) for p, code in self._POWERSHELL_RULES]
        self._unix_patterns = [(re.compile(p, re.IGNORECASE), code) for p, code in self._UNIX_RULES]
        if self.profile == "strict":
            self._unix_patterns.extend(
                [(re.compile(p, re.IGNORECASE), code) for p, code in self._STRICT_UNIX_RULES]
            )
        self._platform = platform.system().lower()
        self.patterns = [compiled for compiled, _ in self._general_patterns]
        if custom_rules:
            custom_compiled = [re.compile(p, re.IGNORECASE) for p in custom_rules]
            self.patterns.extend(custom_compiled)
            self._general_patterns.extend([(compiled, "custom_rule") for compiled in custom_compiled])

    @staticmethod
    def _segment_command(command: str) -> list[str]:
        if not isinstance(command, str):
            return []
        segments = re.split(r'(?:&&|\|\||[;\n\r])', command)
        return [seg.strip() for seg in segments if seg and seg.strip()]

    @staticmethod
    def _detect_shell_family(command: str, platform_name: str) -> str:
        lowered = command.lower()
        if "powershell" in lowered or "pwsh" in lowered:
            return "powershell"
        if re.search(r'\b(rm|sudo|chmod|chown|mkfs|dd|bash|sh)\b', lowered):
            return "unix"
        if platform_name == "windows":
            return "windows_cmd"
        return "unix"

    def _iter_active_rules(self, shell_family: str):
        yield from self._general_patterns
        if shell_family == "powershell":
            yield from self._powershell_patterns
            if self._platform == "windows":
                yield from self._windows_cmd_patterns
            return
        if shell_family == "windows_cmd":
            yield from self._windows_cmd_patterns
            return
        yield from self._unix_patterns
    
    async def process(self,
                     tool: Tool,
                     arguments: dict[str, Any],
                     session: Optional[Session],
                     **kwargs: Any) -> dict[str, Any]:
        """Check for dangerous patterns in shell commands."""
        
        # Only process SHELL type tools
        if tool.type != ToolType.SHELL:
            return {"status": "allow"}
        
        command = str(arguments.get("command", "") or "")
        segments = self._segment_command(command) or [command]
        shell_family = self._detect_shell_family(command, self._platform)
        active_rules = list(self._iter_active_rules(shell_family))

        for segment in segments:
            for pattern, reason_code in active_rules:
                if pattern.search(segment):
                    risk_level = self._RISK_LEVELS.get(reason_code, "high")
                    logger.warning(f"Dangerous command blocked ({reason_code}): {segment[:120]}")
                    return {
                        "status": "block",
                        "reason": f"Command matches dangerous pattern ({reason_code})",
                        "reason_code": reason_code,
                        "risk_level": risk_level,
                        "policy_profile": self.profile,
                    }
        
        return {"status": "allow"}


class PathSandbox(Middleware):
    """Second middleware: Path validation for file operations."""

    PATH_KEY_CANDIDATES = {
        "path",
        "file",
        "src",
        "dst",
        "source",
        "target",
        "destination",
        "file_path",
        "filepath",
        "source_path",
        "target_path",
        "destination_path",
        "src_path",
        "dst_path",
        "input_path",
        "output_path",
        "from_path",
        "to_path",
    }
    
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
        
        from pathlib import Path

        workspace_path = Path(self.workspace_root).resolve()
        normalized_path_entries: list[dict[str, str]] = []

        def _is_path_key(key: Optional[str]) -> bool:
            if not key:
                return False
            lowered = key.strip().lower()
            if lowered in self.PATH_KEY_CANDIDATES:
                return True
            return lowered.endswith("_path")

        def _is_url_like(value: str) -> bool:
            return "://" in value

        def _normalize_path(raw_path: str) -> Path:
            target_path = Path(raw_path)
            if not target_path.is_absolute():
                target_path = Path(self.workspace_root) / target_path
            return target_path.resolve()

        def _validate_path(raw_path: str) -> tuple[bool, Optional[str], Optional[str]]:
            if not raw_path.strip():
                return True, None, raw_path
            if _is_url_like(raw_path):
                return True, None, raw_path
            try:
                abs_path = _normalize_path(raw_path)
            except Exception as exc:
                return False, f"Invalid path format or resolution error: {raw_path} ({exc})", None

            if not abs_path.is_relative_to(workspace_path):
                return False, f"Path is outside workspace: {abs_path}", None
            return True, None, str(abs_path)

        def _validate_arguments_in_place(
            payload: Any,
            *,
            current_key: Optional[str] = None,
            force_path_mode: bool = False,
        ) -> tuple[bool, Optional[str]]:
            if isinstance(payload, dict):
                for key, value in payload.items():
                    next_force_mode = force_path_mode or _is_path_key(str(key))
                    ok, reason = _validate_arguments_in_place(
                        value,
                        current_key=str(key),
                        force_path_mode=next_force_mode,
                    )
                    if not ok:
                        return ok, reason
                return True, None

            if isinstance(payload, list):
                for idx, item in enumerate(payload):
                    ok, reason = _validate_arguments_in_place(
                        item,
                        current_key=current_key,
                        force_path_mode=force_path_mode,
                    )
                    if not ok:
                        return ok, reason
                return True, None

            if isinstance(payload, str) and (force_path_mode or _is_path_key(current_key)):
                ok, reason, normalized = _validate_path(payload)
                if not ok:
                    return False, reason
                if normalized is not None:
                    # Update caller by mutating the same object container path.
                    if current_key is not None:
                        # Caller sets value after recursion; keep payload return clean.
                        pass
                return True, None

            return True, None

        def _rewrite_paths_in_place(
            payload: Any,
            *,
            current_key: Optional[str] = None,
            current_field_path: str = "",
            force_path_mode: bool = False,
        ) -> Any:
            if isinstance(payload, dict):
                for key, value in payload.items():
                    next_force_mode = force_path_mode or _is_path_key(str(key))
                    child_field_path = f"{current_field_path}.{key}" if current_field_path else str(key)
                    payload[key] = _rewrite_paths_in_place(
                        value,
                        current_key=str(key),
                        current_field_path=child_field_path,
                        force_path_mode=next_force_mode,
                    )
                return payload

            if isinstance(payload, list):
                return [
                    _rewrite_paths_in_place(
                        item,
                        current_key=current_key,
                        current_field_path=f"{current_field_path}[{idx}]",
                        force_path_mode=force_path_mode,
                    )
                    for idx, item in enumerate(payload)
                ]

            if isinstance(payload, str) and (force_path_mode or _is_path_key(current_key)):
                ok, _, normalized = _validate_path(payload)
                if ok and normalized is not None:
                    if normalized != payload:
                        normalized_path_entries.append(
                            {
                                "field": current_field_path or (current_key or ""),
                                "from": payload,
                                "to": normalized,
                            }
                        )
                    return normalized
            return payload

        ok, reason = _validate_arguments_in_place(arguments)
        if not ok:
            logger.warning(f"Path traversal or invalid path blocked: {reason}")
            return {
                "status": "block",
                "reason": reason or "Path validation failed",
            }

        _rewrite_paths_in_place(arguments)
        has_path_context = bool(normalized_path_entries) or any(
            _is_path_key(str(k)) for k in (arguments.keys() if isinstance(arguments, dict) else [])
        )
        return {
            "status": "allow",
            "path_scope": "inside_workspace" if has_path_context else "unknown",
            "path_sandbox_workspace_root": str(workspace_path),
            "path_sandbox_normalized_paths": normalized_path_entries,
        }


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

    @staticmethod
    def _truncate_string(value: str, max_chars: int = 240) -> str:
        if len(value) <= max_chars:
            return value
        head = max_chars // 2
        tail = max_chars - head - 24
        tail = max(0, tail)
        return f"{value[:head]}...[truncated:{len(value)}]...{value[-tail:] if tail else ''}"

    def _truncate_payload_fields(
        self,
        payload: Any,
        *,
        max_string_chars: int = 240,
        max_items: int = 20,
        max_depth: int = 6,
        _depth: int = 0,
    ) -> Any:
        if _depth >= max_depth:
            return "[truncated:depth]"

        if isinstance(payload, dict):
            items = list(payload.items())
            result: dict[str, Any] = {}
            for idx, (k, v) in enumerate(items):
                if idx >= max_items:
                    result["__truncated_items__"] = f"{len(items) - max_items} more field(s)"
                    break
                result[str(k)] = self._truncate_payload_fields(
                    v,
                    max_string_chars=max_string_chars,
                    max_items=max_items,
                    max_depth=max_depth,
                    _depth=_depth + 1,
                )
            return result

        if isinstance(payload, list):
            out: list[Any] = []
            for idx, v in enumerate(payload):
                if idx >= max_items:
                    out.append(f"[truncated_items:{len(payload) - max_items}]")
                    break
                out.append(
                    self._truncate_payload_fields(
                        v,
                        max_string_chars=max_string_chars,
                        max_items=max_items,
                        max_depth=max_depth,
                        _depth=_depth + 1,
                    )
                )
            return out

        if isinstance(payload, str):
            return self._truncate_string(payload, max_chars=max_string_chars)

        return payload

    def _build_policy_context(
        self,
        *,
        tool: Tool,
        arguments: dict[str, Any],
        session: Optional[Session],
        user_id: Optional[str],
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        middleware_context = kwargs.get("middleware_context")
        if not isinstance(middleware_context, dict):
            middleware_context = {}

        raw_arguments = kwargs.get("raw_arguments", {})
        if not isinstance(raw_arguments, dict):
            raw_arguments = {}

        operation_type = arguments.get("operation")
        if not operation_type:
            operation_type = {
                "write_file": "write",
                "edit_file": "modify",
                "delete_file": "delete",
                "delete_lines": "delete",
                "shell": "execute",
                "fetch_url": "network",
                "web_search": "network",
            }.get(tool.name, "unknown")

        return {
            "tool": {
                "name": tool.name,
                "type": str(kwargs.get("tool_type", tool.type.value if getattr(tool, "type", None) else "unknown")),
                "source": str(kwargs.get("tool_source", "native")),
                "source_ref": kwargs.get("tool_source_ref"),
                "need_auth": bool(getattr(tool, "need_auth", self.default_need_auth)),
                "description": self._truncate_string(tool.description or "", max_chars=220),
            },
            "operation": {
                "operation_type": str(operation_type),
            },
            "arguments_raw": self._truncate_payload_fields(raw_arguments),
            "arguments_normalized": self._truncate_payload_fields(arguments),
            "path_scope": {
                "scope": middleware_context.get("path_scope", "unknown"),
                "workspace_root": middleware_context.get("path_sandbox_workspace_root"),
                "normalized_paths": self._truncate_payload_fields(
                    middleware_context.get("path_sandbox_normalized_paths", [])
                ),
            },
            "session": {
                "session_id": session.session_id if session else None,
                "user_id": session.user_id if session else user_id,
                "channel_type": session.channel_type if session else None,
            },
        }
    
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

        if bool(kwargs.get("auth_replay_approved")):
            return {
                "status": "allow",
                "auth_mode": self.security_mode.value,
                "reason_code": "AUTH_RECHECK_APPROVED",
                "reason": "Previously approved operation is being re-validated before execution.",
            }

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

            policy_context = self._build_policy_context(
                tool=tool,
                arguments=arguments,
                session=session,
                user_id=user_id,
                kwargs=kwargs,
            )
            review_arguments = policy_context.get("arguments_normalized", {})
            if not isinstance(review_arguments, dict):
                review_arguments = {}
            review_diff_preview = self._truncate_string(diff_preview or "", max_chars=1200) or None

            review_payload = {
                "tool_name": tool.name,
                "tool_description": tool.description,
                "arguments": review_arguments,
                "reason": reason,
                "diff_preview": review_diff_preview,
                "policy_context": policy_context,
            }
            guardian_started = time.perf_counter()
            decision = await self.consensus_guardian.review(review_payload)
            logger.info(
                "Consensus guardian reviewed tool=%s approved=%s reason_code=%s latency_ms=%.2f",
                tool.name,
                decision.approved,
                decision.reason_code,
                (time.perf_counter() - guardian_started) * 1000.0,
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
            from itertools import islice

            def _read_head_text(file_path: str, max_lines: int = 20, max_chars: int = 4000) -> str:
                if not isinstance(file_path, str) or not file_path:
                    return ""
                if not os.path.exists(file_path):
                    return ""
                chunks: list[str] = []
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        for line in islice(f, max_lines):
                            chunks.append(line)
                            if sum(len(c) for c in chunks) >= max_chars:
                                break
                    return "".join(chunks)[:max_chars]
                except Exception:
                    return ""

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
                    old_content = _read_head_text(path if isinstance(path, str) else "")
                elif tool_name == "edit_file":
                    old_content = str(arguments.get("old_text", ""))
                    new_content = str(arguments.get("new_text", ""))
                elif tool_name == "delete_file":
                    old_content = _read_head_text(path if isinstance(path, str) else "")
                    new_content = ""
                elif tool_name == "delete_lines":
                    old_content = ""
                    if isinstance(path, str) and os.path.exists(path):
                        start_line = int(arguments.get("start_line", 1))
                        end_line = arguments.get("end_line")
                        if start_line >= 1:
                            effective_end = int(end_line) if end_line is not None else start_line
                            if effective_end < start_line:
                                effective_end = start_line
                            selected: list[str] = []
                            with open(path, "r", encoding="utf-8") as f:
                                for idx, line in enumerate(f, start=1):
                                    if idx < start_line:
                                        continue
                                    if idx > effective_end:
                                        break
                                    selected.append(line.rstrip("\n"))
                                    if len(selected) >= 20:
                                        break
                            old_content = "\n".join(selected)
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
            call_kwargs = dict(kwargs)
            if "middleware_context" not in call_kwargs:
                call_kwargs["middleware_context"] = aggregated_allow

            result = await middleware.process(
                tool=tool,
                arguments=arguments,
                session=session,
                user_id=user_id,
                **call_kwargs
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


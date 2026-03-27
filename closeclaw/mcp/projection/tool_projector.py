"""Project MCP tool definitions into ToolSpecV2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...compatibility import ToolSpecV2


@dataclass
class MCPProjectedTool:
    """Projected MCP tool with routing metadata."""

    server_id: str
    tool_name: str
    spec: ToolSpecV2


class MCPToolProjector:
    """Converts MCP tool payloads into ToolSpecV2."""

    _FILE_ALIASES = {"file", "filesystem", "fs", "document", "doc"}
    _SHELL_ALIASES = {"shell", "exec", "execute", "command", "terminal", "cmd", "bash", "powershell"}
    _WEB_ALIASES = {"web", "websearch", "network", "http", "https", "url", "search", "api"}

    def _normalize_tool_type(
        self,
        *,
        raw_tool_type: str,
        risk_tags: list[str],
        capability_tags: list[str],
        name: str,
        description: str,
        input_schema: dict[str, Any],
    ) -> str:
        normalized = (raw_tool_type or "").strip().lower()
        if normalized in self._FILE_ALIASES:
            return "file"
        if normalized in self._SHELL_ALIASES:
            return "shell"
        if normalized in self._WEB_ALIASES:
            return "websearch"

        normalized_risks = {str(tag).strip().lower() for tag in risk_tags}
        if "filesystem" in normalized_risks:
            return "file"
        if "exec" in normalized_risks or "system_path" in normalized_risks:
            return "shell"
        if "network" in normalized_risks or "external_api" in normalized_risks:
            return "websearch"

        normalized_caps = {str(tag).strip().lower() for tag in capability_tags}
        for cap in normalized_caps:
            if cap.startswith("type:"):
                cap_type = cap.split(":", 1)[1].strip().lower()
                if cap_type in self._FILE_ALIASES:
                    return "file"
                if cap_type in self._SHELL_ALIASES:
                    return "shell"
                if cap_type in self._WEB_ALIASES:
                    return "websearch"

        schema_keys: set[str] = set()
        properties = input_schema.get("properties", {})
        if isinstance(properties, dict):
            schema_keys = {str(k).strip().lower() for k in properties.keys()}
        else:
            schema_keys = {str(k).strip().lower() for k in input_schema.keys()}

        if schema_keys & {
            "path", "file", "file_path", "filepath", "source_path", "target_path",
            "destination_path", "src_path", "dst_path",
        }:
            return "file"
        if schema_keys & {"command", "cmd", "script", "argv", "args"}:
            return "shell"
        if schema_keys & {"url", "uri", "query", "endpoint"}:
            return "websearch"

        text = f"{name} {description}".lower()
        if any(token in text for token in ["file", "directory", "folder", "read", "write", "edit"]):
            return "file"
        if any(token in text for token in ["command", "shell", "terminal", "execute"]):
            return "shell"
        if any(token in text for token in ["web", "http", "search", "url", "api"]):
            return "websearch"

        # Conservative fallback for unknown MCP tool types.
        return "shell"

    def project(self, server_id: str, tool_payload: dict[str, Any]) -> MCPProjectedTool:
        name = str(tool_payload.get("name", "")).strip()
        if not name:
            raise ValueError("MCP tool payload missing 'name'")

        description = str(tool_payload.get("description", "MCP projected tool")).strip()
        input_schema = tool_payload.get("input_schema") or tool_payload.get("parameters") or {}
        if not isinstance(input_schema, dict):
            input_schema = {}

        raw_need_auth = tool_payload.get("need_auth")
        need_auth = True if raw_need_auth is None else bool(raw_need_auth)

        raw_capability_tags = tool_payload.get("capability_tags")
        capability_tags = raw_capability_tags if isinstance(raw_capability_tags, list) else [f"mcp:{server_id}"]

        raw_risk_tags = tool_payload.get("risk_tags")
        risk_tags = raw_risk_tags if isinstance(raw_risk_tags, list) else ["external_api"]

        tool_type = self._normalize_tool_type(
            raw_tool_type=str(tool_payload.get("tool_type", "")).strip(),
            risk_tags=[str(tag) for tag in (raw_risk_tags if isinstance(raw_risk_tags, list) else [])],
            capability_tags=[str(tag) for tag in capability_tags],
            name=name,
            description=description,
            input_schema=input_schema if isinstance(input_schema, dict) else {},
        )

        spec = ToolSpecV2(
            name=name,
            description=description,
            input_schema=input_schema,
            need_auth=need_auth,
            tool_type=tool_type,
            capability_tags=[str(tag) for tag in capability_tags],
            risk_tags=[str(tag) for tag in risk_tags],
            source="mcp",
            source_ref=f"{server_id}:{name}",
            metadata={
                "mcp_server_id": server_id,
                "tool_type_raw": str(tool_payload.get("tool_type", "")).strip(),
                "need_auth_default_applied": raw_need_auth is None,
            },
        )

        return MCPProjectedTool(server_id=server_id, tool_name=name, spec=spec)

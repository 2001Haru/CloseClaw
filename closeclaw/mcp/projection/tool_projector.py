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

    def project(self, server_id: str, tool_payload: dict[str, Any]) -> MCPProjectedTool:
        name = str(tool_payload.get("name", "")).strip()
        if not name:
            raise ValueError("MCP tool payload missing 'name'")

        description = str(tool_payload.get("description", "MCP projected tool")).strip()
        input_schema = tool_payload.get("input_schema") or tool_payload.get("parameters") or {}
        if not isinstance(input_schema, dict):
            input_schema = {}

        need_auth = bool(tool_payload.get("need_auth", False))
        tool_type = str(tool_payload.get("tool_type", "websearch")).strip() or "websearch"

        capability_tags = tool_payload.get("capability_tags")
        if not isinstance(capability_tags, list):
            capability_tags = [f"mcp:{server_id}"]

        risk_tags = tool_payload.get("risk_tags")
        if not isinstance(risk_tags, list):
            risk_tags = ["external_api"]

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
            metadata={"mcp_server_id": server_id},
        )

        return MCPProjectedTool(server_id=server_id, tool_name=name, spec=spec)

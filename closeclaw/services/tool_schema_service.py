"""Tool schema formatting service for LLM function-calling payloads."""

from __future__ import annotations

from typing import Any, Iterable

from ..types import Tool


class ToolSchemaService:
    """Formats registered tools into OpenAI-compatible function schema."""

    def format_tools_for_llm(self, tools: Iterable[Tool]) -> list[dict[str, Any]]:
        """Convert tool metadata into OpenAI function-calling schema list."""
        tools_list: list[dict[str, Any]] = []
        for tool in tools:
            properties_schema: dict[str, Any] = {}
            required: list[str] = []

            raw_params = tool.parameters or {}

            if (
                isinstance(raw_params, dict)
                and "properties" in raw_params
                and isinstance(raw_params["properties"], dict)
            ):
                props_source = raw_params["properties"]
                required = list(raw_params.get("required", []))
            else:
                props_source = raw_params

            for param_name, param_info in props_source.items():
                if isinstance(param_info, str):
                    prop_type = param_info
                    description = ""
                    optional = False
                elif isinstance(param_info, dict):
                    prop_type = param_info.get("type", "string")
                    description = param_info.get("description", "")
                    optional = param_info.get("optional", False)
                else:
                    prop_type = "string"
                    description = ""
                    optional = False

                properties_schema[param_name] = {
                    "type": prop_type,
                    "description": description,
                }

                if not required:
                    if not optional:
                        required.append(param_name)

            tools_list.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": {
                            "type": "object",
                            "properties": properties_schema,
                            "required": required,
                        },
                    },
                }
            )
        return tools_list

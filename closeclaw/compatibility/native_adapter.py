"""Adapter from legacy CloseClaw Tool model to ToolSpecV2."""

from ..types import Tool, ToolType
from .toolspec_v2 import ToolSpecV2


class NativeAdapter:
    """Converts native tool definitions into the runtime canonical schema."""

    @staticmethod
    def to_toolspec_v2(tool: Tool) -> ToolSpecV2:
        capability_tags = [f"type:{tool.type.value}"]
        risk_tags = NativeAdapter._infer_risk_tags(tool.type)

        return ToolSpecV2(
            name=tool.name,
            description=tool.description,
            input_schema=tool.parameters,
            need_auth=bool(tool.need_auth),
            tool_type=tool.type.value,
            capability_tags=capability_tags,
            risk_tags=risk_tags,
            source="native",
            source_ref=tool.name,
            metadata=tool.metadata,
        )

    @staticmethod
    def _infer_risk_tags(tool_type: ToolType) -> list[str]:
        if tool_type == ToolType.FILE:
            return ["filesystem"]
        if tool_type == ToolType.SHELL:
            return ["exec", "system_path"]
        if tool_type == ToolType.WEBSEARCH:
            return ["network", "external_api"]
        return []


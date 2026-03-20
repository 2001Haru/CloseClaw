"""Unified tool contract for compatibility adapters."""

from dataclasses import dataclass, field
from typing import Any, Literal

RiskTag = Literal["filesystem", "network", "exec", "system_path", "external_api"]
SourceType = Literal["native", "openclaw", "mcp"]


@dataclass
class ToolSpecV2:
    """Canonical tool schema used by runtime services and adapters."""

    name: str
    description: str
    input_schema: dict[str, Any]
    need_auth: bool
    tool_type: str
    capability_tags: list[str] = field(default_factory=list)
    risk_tags: list[RiskTag] = field(default_factory=list)
    source: SourceType = "native"
    source_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "need_auth": self.need_auth,
            "tool_type": self.tool_type,
            "capability_tags": self.capability_tags,
            "risk_tags": self.risk_tags,
            "source": self.source,
            "source_ref": self.source_ref,
            "metadata": self.metadata,
        }


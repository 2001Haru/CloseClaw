"""Tests for ToolSchemaService extraction."""

from closeclaw.services.tool_schema_service import ToolSchemaService
from closeclaw.types import Tool, ToolType


def test_format_tools_for_llm_supports_legacy_param_shape():
    service = ToolSchemaService()
    tool = Tool(
        name="read_file",
        description="Read a file",
        type=ToolType.FILE,
        need_auth=False,
        parameters={
            "path": {"type": "string", "description": "File path"},
            "encoding": {"type": "string", "optional": True},
        },
    )

    payload = service.format_tools_for_llm([tool])

    assert payload[0]["function"]["name"] == "read_file"
    assert payload[0]["function"]["parameters"]["properties"]["path"]["type"] == "string"
    assert "path" in payload[0]["function"]["parameters"]["required"]


def test_format_tools_for_llm_supports_json_schema_shape():
    service = ToolSchemaService()
    tool = Tool(
        name="write_file",
        description="Write a file",
        type=ToolType.FILE,
        need_auth=True,
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    )

    payload = service.format_tools_for_llm([tool])

    required = payload[0]["function"]["parameters"]["required"]
    assert required == ["path", "content"]


def test_format_tools_for_llm_supports_string_shorthand_param_shape():
    service = ToolSchemaService()
    tool = Tool(
        name="search_web",
        description="Search web",
        type=ToolType.WEBSEARCH,
        need_auth=False,
        parameters={"query": "string"},
    )

    payload = service.format_tools_for_llm([tool])

    assert payload[0]["function"]["parameters"]["properties"]["query"]["type"] == "string"

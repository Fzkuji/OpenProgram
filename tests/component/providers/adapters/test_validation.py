from openprogram.providers.types import Tool, ToolCall
from openprogram.providers.utils.validation import validate_tool_arguments


def test_validate_tool_arguments_coerces_trimmed_numeric_and_boolean_strings():
    tool = Tool(
        name="demo",
        description="demo tool",
        parameters={
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "enabled": {"type": "boolean"},
            },
            "required": ["count", "enabled"],
        },
    )
    tool_call = ToolCall(
        id="call_1",
        name="demo",
        arguments={"count": " 42 ", "enabled": " Yes "},
    )

    validated = validate_tool_arguments(tool, tool_call)

    assert validated == {"count": 42, "enabled": True}

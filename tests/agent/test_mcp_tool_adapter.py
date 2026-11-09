import asyncio
from types import SimpleNamespace

from agent.factory import _mcp_tool_to_langchain
from agent.prompts_library import SYSTEM_PROMPTS


def test_mcp_tool_errors_are_returned_as_recoverable_feedback():
    error = (
        'Error executing tool read_sections: No matching sections were found: '
        '"DynamoDB on-demand capacity mode". Available sections: '
        '"Read request units and write request units", '
        '"Initial throughput and scaling properties".'
    )

    class Session:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                is_error=True,
                content=[SimpleNamespace(text=error)],
            )

    tool = _mcp_tool_to_langchain(
        Session(),
        SimpleNamespace(
            name="read_sections",
            description="Read named documentation sections.",
            input_schema={"type": "object", "properties": {}},
        ),
    )

    actual = asyncio.run(tool.ainvoke({}))

    assert actual == f"MCP tool error: read_sections: {error}"


def test_component_prompt_explains_recovery_from_invalid_section_names():
    prompt = SYSTEM_PROMPTS["components_selection"]

    assert "exact section titles returned by the tool" in prompt
    assert "use `read_documentation` for the full document" in prompt

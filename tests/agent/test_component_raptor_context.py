import asyncio
from contextlib import asynccontextmanager

from langchain_core.messages import ToolMessage

from agent import designSupervisor
from schemas import ComponentChoices

VALID_SPEC = {
    "system_type": "messaging / chat apps",
    "explicit_requirements": {
        "daily_active_users": 1_000_000,
        "regions": ["ap-south-1"],
    },
    "clarifications": {"voice_video": "yes"},
    "assumptions": {"messages_per_user_per_day": 40},
}

RAPTOR_CONTEXT = [
    {
        "node_id": "L0-a1b2",
        "score": 0.9,
        "level": 0,
        "source": "messaging.md",
        "sources": None,
        "header_path": "Messaging > Storage",
        "text": "Partition messages by conversation.",
    }
]


def test_component_selection_retrieves_raptor_context_before_building_agent(monkeypatch):
    events = []
    result = ComponentChoices(
        provider="aws",
        components=[
            {
                "id": "api",
                "service": "ec2",
                "instance_type": "t3.micro",
                "region": "ap-south-1",
                "rationale": "A small instance hosts this initial service.",
                "based_on": ["requirement:daily_active_users"],
            }
        ],
    )

    def retrieve(spec, capacity_results):
        events.append(("retrieve", spec["system_type"], capacity_results))
        return RAPTOR_CONTEXT

    @asynccontextmanager
    async def component_agent():
        events.append(("build_agent", None))
        yield object()

    async def run_agent(agent, requirement_spec, capacity_results, context):
        events.append(("run_agent", context))
        return result

    monkeypatch.setattr(designSupervisor, "retrieve_raptor_context", retrieve)
    monkeypatch.setattr(designSupervisor, "build_component_agent", component_agent)
    monkeypatch.setattr(designSupervisor, "_run_component_agent", run_agent)

    actual = asyncio.run(
        designSupervisor._select_components(VALID_SPEC, {"peak_qps": 5})
    )

    assert actual is result
    assert [event[0] for event in events] == ["retrieve", "build_agent", "run_agent"]
    assert events[0][2] == {"peak_qps": 5}
    assert events[-1][1] == RAPTOR_CONTEXT


def test_component_agent_receives_raptor_nodes_and_citation_instructions():
    messages = []
    design = ComponentChoices(
        provider="aws",
        components=[
            {
                "id": "api",
                "service": "ec2",
                "instance_type": "t3.micro",
                "region": "ap-south-1",
                "rationale": "A small instance hosts this initial service.",
                "based_on": ["requirement:daily_active_users", "kb:L0-a1b2"],
            }
        ],
    )
    class FakeAgent:
        async def ainvoke(self, state, config):
            messages.extend(state["messages"])
            return {"structured_response": design}

    actual = asyncio.run(
        designSupervisor._run_component_agent(
            FakeAgent(), VALID_SPEC, {"peak_qps": 5}, RAPTOR_CONTEXT
        )
    )

    assert actual.components[0].based_on == [
        "requirement:daily_active_users", "kb:messaging.md"
    ]
    prompt = messages[0]["content"]
    assert "L0-a1b2" in prompt
    assert "Partition messages by conversation." in prompt
    assert "Use the RAPTOR nodes first" in prompt
    assert "Do not use node IDs as citations." in prompt


def _design(*references):
    return ComponentChoices.model_validate({
        "provider": "aws",
        "components": [{
            "id": "api",
            "service": "ec2",
            "instance_type": "t3.micro",
            "region": "ap-south-1",
            "rationale": "A small instance hosts this initial service.",
            "based_on": list(references),
        }],
    })


def test_ambiguous_summary_node_requires_explicit_source():
    context = [{
        "node_id": "L3-summary",
        "sources": ["uber.md", "cache.md", "uber.md"],
        "source": None,
    }]
    result, errors = designSupervisor._resolve_kb_references(
        _design("kb:L3-summary", "kb:uber.md"), context, []
    )
    assert errors == ["api: kb:L3-summary"]
    assert result.components[0].based_on == ["kb:L3-summary", "kb:uber.md"]


def test_tool_returned_aws_urls_are_accepted_but_invented_citations_rejected():
    url = "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html"
    messages = [ToolMessage(content=f"Relevant documentation: {url}", tool_call_id="1")]
    result, errors = designSupervisor._resolve_kb_references(
        _design(f"kb:{url}", "kb:missing.md", "kb:L3-fake"),
        RAPTOR_CONTEXT,
        messages,
    )
    assert result.components[0].based_on[0] == f"kb:{url}"
    assert errors == ["api: kb:missing.md", "api: kb:L3-fake"]


def test_component_agent_retries_unknown_kb_citation():
    url = "https://docs.aws.amazon.com/service/latest/guide.html"
    responses = [
        {
            "structured_response": _design("kb:made-up.md"),
            "messages": [ToolMessage(content=url, tool_call_id="1")],
        },
        {
            "structured_response": _design(f"kb:{url}"),
            "messages": [ToolMessage(content=url, tool_call_id="1")],
        },
    ]
    calls = []

    class FakeAgent:
        async def ainvoke(self, state, config):
            calls.append(state)
            return responses.pop(0)

    actual = asyncio.run(
        designSupervisor._run_component_agent(FakeAgent(), VALID_SPEC, {}, RAPTOR_CONTEXT)
    )
    assert actual.components[0].based_on == [f"kb:{url}"]
    assert len(calls) == 2
    assert "kb:made-up.md" in calls[1]["messages"][-1]["content"]


def test_missing_kb_source_is_not_silently_accepted():
    result, errors = designSupervisor._resolve_kb_references(
        _design("kb:L3-orphan"), [{"node_id": "L3-orphan", "sources": []}], []
    )
    assert errors == ["api: kb:L3-orphan"]
    assert result.components[0].based_on == ["kb:L3-orphan"]


def test_structured_response_and_failed_tools_cannot_authorize_aws_citations():
    url = "https://docs.aws.amazon.com/service/latest/guide.html"
    messages = [
        ToolMessage(content=url, name="AwsDesign", tool_call_id="1"),
        ToolMessage(content=url, name="search_documentation", tool_call_id="2", status="error"),
    ]
    _, errors = designSupervisor._resolve_kb_references(
        _design(f"kb:{url}"), RAPTOR_CONTEXT, messages
    )
    assert errors == [f"api: kb:{url}"]


def test_mcp_tool_error_text_cannot_authorize_aws_citations():
    url = "https://docs.aws.amazon.com/service/latest/guide.html"
    messages = [
        ToolMessage(
            content=f"MCP tool error: read_sections: No match; see {url}",
            tool_call_id="1",
        )
    ]

    _, errors = designSupervisor._resolve_kb_references(
        _design(f"kb:{url}"), RAPTOR_CONTEXT, messages
    )

    assert errors == [f"api: kb:{url}"]

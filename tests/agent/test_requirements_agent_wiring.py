"""Offline checks of build_req_agent(): fake chat model, in-memory checkpointer, no DB."""

from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import Field

from agent import factory
from schemas import RequirementSpec

VALID_SPEC = {
    "system_type": "messaging / chat apps",
    "explicit_requirements": {
        "daily_active_users": 1_000_000,
        "regions": ["ap-south-1"],
    },
    "clarifications": {"voice_video": "yes"},
    "assumptions": {"messages_per_user_per_day": 40},
}


class RecordingModel(GenericFakeChatModel):
    """Replays scripted AIMessages and records the kwargs passed to bind_tools."""

    bind_calls: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.bind_calls.append(
            {"tools": [getattr(t, "name", t) for t in tools], **kwargs}
        )
        return self


def tool_call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def build_agent(monkeypatch, responses):
    model = RecordingModel(messages=iter(responses))

    class FakeDatabase:
        pool = None

        def close(self):
            pass

    class FakeMemory:
        def __init__(self, pool):
            self.checkpointer = InMemorySaver()

        @staticmethod
        def get_summarization_middleware():
            return AgentMiddleware()

    monkeypatch.setattr(factory, "get_llm", lambda: model)
    monkeypatch.setattr(factory, "DatabaseStore", FakeDatabase)
    monkeypatch.setattr(factory, "ShortTermMemory", FakeMemory)
    agent, _, _ = factory.build_req_agent()
    return agent, model


def run(agent, answers):
    config = {"configurable": {"thread_id": "t"}, "recursion_limit": 25}
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Design WhatsApp"}]}, config
    )
    answers = iter(answers)
    while result.get("__interrupt__"):
        decisions = [
            {"type": "respond", "message": next(answers)}
            for _ in result["__interrupt__"][0].value["action_requests"]
        ]
        result = agent.invoke(Command(resume={"decisions": decisions}), config)
    return result


def test_ask_then_structured_spec(monkeypatch):
    agent, _ = build_agent(
        monkeypatch,
        [
            AIMessage(
                content="",
                tool_calls=[tool_call("ask_user", {"question": "Voice/video?"}, "c1")],
            ),
            AIMessage(
                content="", tool_calls=[tool_call("RequirementSpec", VALID_SPEC, "c2")]
            ),
        ],
    )
    result = run(agent, ["yes"])

    assert isinstance(result["structured_response"], RequirementSpec)
    assert (
        result["structured_response"].explicit_requirements.daily_active_users
        == 1_000_000
    )
    assert any(
        isinstance(m, ToolMessage) and m.content == "yes" for m in result["messages"]
    )


def test_model_is_bound_with_spec_tool_and_no_parallel_calls(monkeypatch):
    agent, model = build_agent(
        monkeypatch,
        [
            AIMessage(
                content="", tool_calls=[tool_call("RequirementSpec", VALID_SPEC, "c1")]
            )
        ],
    )
    run(agent, [])

    bound = model.bind_calls[-1]
    assert set(bound["tools"]) == {"ask_user", "RequirementSpec"}
    assert bound["parallel_tool_calls"] is False
    assert bound["tool_choice"] == "any"


def test_invalid_spec_is_sent_back_and_retried(monkeypatch):
    bad = {**VALID_SPEC, "system_type": "Messaging App"}
    agent, _ = build_agent(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[tool_call("RequirementSpec", bad, "c1")]),
            AIMessage(
                content="", tool_calls=[tool_call("RequirementSpec", VALID_SPEC, "c2")]
            ),
        ],
    )
    result = run(agent, [])

    assert result["structured_response"].system_type == "messaging / chat apps"
    errors = [
        m
        for m in result["messages"]
        if isinstance(m, ToolMessage) and m.tool_call_id == "c1"
    ]
    assert len(errors) == 1
    # the allowed values reach the model in the error message
    assert "messaging / chat apps" in errors[0].content

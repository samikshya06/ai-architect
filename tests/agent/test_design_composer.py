import copy

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from agent import designSupervisor, factory
from schemas import ComponentChoices, TopologyGraph

DESIGN = ComponentChoices.model_validate({
    "components": [
        {"id": "app", "service": "ec2", "region": "us-east-1",
         "instance_type": "t3.micro", "rationale": "Selected for the application workload.",
         "based_on": ["requirement:daily_active_users"]},
        {"id": "transfer", "service": "data_transfer", "region": "us-east-1",
         "direction": "internet", "rationale": "Accounts for internet data transfer costs.",
         "based_on": ["requirement:daily_active_users"]},
    ],
})
CAPACITY = [{"metric": "peak_read_qps", "value": 10, "unit": "requests/second"}]
GRAPH = {
    "nodes": [
        {"id": "user", "kind": "external", "component_id": None,
         "service": None, "region": None,
         "label": "User", "description": "External user"},
        {"id": "app-node", "kind": "component", "component_id": "app",
         "service": "ec2", "region": "us-east-1",
         "label": "App", "description": "Application server"},
        {"id": "transfer-node", "kind": "accounting", "component_id": "transfer",
         "service": "data_transfer", "region": "us-east-1",
         "label": "Transfer", "description": "Internet transfer cost"},
    ],
    "edges": [{
        "id": "read", "source": "user", "target": "app-node", "protocol": "HTTPS",
        "description": "Read request", "expected_volume": {
            "value": 10, "unit": "requests/second", "basis": "Peak reads",
            "capacity_metrics": ["peak_read_qps"],
        }, "volume_explanation": "Uses peak read capacity.",
    }],
    "decisions": {
        "read_path": "User to app; no cache selected",
        "write_path": "User to app; no queue selected",
        "availability_zones": "Unknown", "datastore_failover": "No datastore selected",
        "tradeoffs_and_risks": ["Single application instance"], "assumptions": [],
    },
}


@pytest.mark.parametrize("mutate", [
    lambda g: g["nodes"].append(copy.deepcopy(g["nodes"][0])),
    lambda g: g["nodes"].append({**g["nodes"][1], "id": "other"}),
    lambda g: g["edges"].append(copy.deepcopy(g["edges"][0])),
    lambda g: g["edges"][0].update(target="missing"),
    lambda g: g["edges"][0].update(target="user"),
    lambda g: g["edges"][0].update(target="transfer-node"),
    lambda g: g["edges"][0]["expected_volume"].update(value=-1),
    lambda g: g["nodes"][0].update(component_id="app"),
])
def test_invalid_graphs_are_rejected(mutate):
    graph = copy.deepcopy(GRAPH)
    mutate(graph)
    with pytest.raises(ValidationError):
        TopologyGraph.model_validate(graph)


def test_input_validation_checks_component_coverage_kinds_and_metrics():
    graph = copy.deepcopy(GRAPH)
    graph["nodes"][1]["component_id"] = "invented"
    graph["nodes"][2]["kind"] = "component"
    graph["nodes"][2]["region"] = "ap-south-1"
    graph["edges"][0]["expected_volume"]["capacity_metrics"] = ["missing"]
    errors = designSupervisor._topology_input_errors(
        TopologyGraph.model_validate(graph), DESIGN, CAPACITY
    )
    assert errors == [
        "missing selected component: app",
        "unknown selected component: invented",
        "incorrect node kind for component: transfer",
        "incorrect service or region for component: transfer",
        "edge read references unknown capacity metric: missing",
    ]


def test_composer_receives_all_inputs_and_retries_invalid_references(monkeypatch):
    bad = copy.deepcopy(GRAPH)
    bad["nodes"][1]["component_id"] = "invented"
    calls = []

    class FakeAgent:
        def invoke(self, state, config):
            calls.append((state, config))
            graph = bad if len(calls) == 1 else GRAPH
            return {"structured_response": TopologyGraph.model_validate(graph),
                    "messages": state["messages"]}

    monkeypatch.setattr(designSupervisor, "build_composer_agent", lambda: FakeAgent())
    estimate = {"scenarios": {"on_demand": {"complete": False}}}
    result = designSupervisor.compose_design({"system_type": "chat"}, CAPACITY, DESIGN, estimate)
    assert result == TopologyGraph.model_validate(GRAPH)
    prompt = calls[0][0]["messages"][0]["content"]
    assert all(value in prompt for value in ("chat", "peak_read_qps", "transfer", "complete"))
    assert "invented" in calls[1][0]["messages"][-1]["content"]
    assert calls[0][1]["recursion_limit"] == designSupervisor.COMPOSER_RECURSION_LIMIT


def test_composer_fails_after_reference_retries(monkeypatch):
    bad = copy.deepcopy(GRAPH)
    bad["nodes"][1]["component_id"] = "invented"

    class FakeAgent:
        def invoke(self, state, config):
            return {"structured_response": TopologyGraph.model_validate(bad),
                    "messages": state["messages"]}

    monkeypatch.setattr(designSupervisor, "build_composer_agent", lambda: FakeAgent())
    with pytest.raises(RuntimeError, match="missing selected component: app"):
        designSupervisor.compose_design({}, CAPACITY, DESIGN, {})


def test_composer_rejects_missing_structured_response(monkeypatch):
    class FakeAgent:
        def invoke(self, state, config):
            return {}

    monkeypatch.setattr(designSupervisor, "build_composer_agent", lambda: FakeAgent())
    with pytest.raises(RuntimeError, match="without a structured topology"):
        designSupervisor.compose_design({}, CAPACITY, DESIGN, {})


def test_real_agent_structured_output_retries_invalid_graph(monkeypatch):
    bad = copy.deepcopy(GRAPH)
    bad["edges"][0]["target"] = "missing"
    messages = [
        AIMessage(content="", tool_calls=[{
            "name": "TopologyGraph", "args": graph, "id": str(index), "type": "tool_call"
        }])
        for index, graph in enumerate([bad, GRAPH])
    ]
    class ToolModel(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = ToolModel(messages=iter(messages))
    monkeypatch.setattr(factory, "get_llm", lambda: model)
    agent = factory.build_composer_agent()
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Compose"}]},
        {"recursion_limit": 25},
    )
    assert result["structured_response"] == TopologyGraph.model_validate(GRAPH)
    assert "unknown node" in str(result["messages"])

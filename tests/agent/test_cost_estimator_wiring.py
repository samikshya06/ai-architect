from agent import designSupervisor
from schemas import ComponentChoices, RequirementSpec, TopologyGraph


def test_cost_estimator_receives_selected_components(monkeypatch, capsys, tmp_path):
    spec = RequirementSpec.model_validate(
        {
            "system_type": "messaging / chat apps",
            "explicit_requirements": {"daily_active_users": 1000},
        }
    )
    design = ComponentChoices.model_validate(
        {
            "components": [
                {
                    "id": "app",
                    "service": "ec2",
                    "region": "us-east-1",
                    "instance_type": "t3.micro",
                    "rationale": "Selected for the requested application workload.",
                    "based_on": ["requirement:daily_active_users"],
                }
            ]
        }
    )
    calls = []

    class Agent:
        def invoke(self, inputs, config):
            return {"structured_response": spec}

    monkeypatch.setattr(designSupervisor, "_agent", Agent())
    monkeypatch.setattr(designSupervisor, "invoke_capacity_agent", lambda *args: [])
    monkeypatch.setattr(
        designSupervisor, "handle_component_selection", lambda *args: design
    )

    def estimate(selected, capacity_results):
        calls.append((selected, capacity_results))
        return {"scenarios": {"on_demand": {"complete": False}}}

    monkeypatch.setattr(designSupervisor, "estimate_aws_cost", estimate)
    graph = TopologyGraph.model_validate({
        "nodes": [
            {"id": "user", "kind": "external", "component_id": None,
             "service": None, "region": None,
             "label": "User", "description": "Application user"},
            {"id": "app", "kind": "component", "component_id": "app",
             "service": "ec2", "region": "us-east-1",
             "label": "Application", "description": "Application server"},
        ],
        "edges": [
            {"id": "request", "source": "user", "target": "app",
             "protocol": "HTTPS", "description": "Application requests",
             "expected_volume": None, "volume_explanation": "No flow estimate."}
        ],
        "decisions": {
            "read_path": "User to app", "write_path": "User to app",
            "availability_zones": "Unknown", "datastore_failover": "No datastore selected",
            "tradeoffs_and_risks": ["Single application instance"], "assumptions": [],
        },
    })
    composer_calls = []

    def compose(requirements, capacity, selected, estimate_result):
        composer_calls.append((requirements, capacity, selected, estimate_result))
        return graph

    monkeypatch.setattr(designSupervisor, "compose_design", compose)
    monkeypatch.setattr(designSupervisor, "DESIGN_OUTPUT_DIR", tmp_path)
    assert designSupervisor.handle_query(
        "design an app", "thread"
    ) == graph.model_dump_json(indent=2)
    assert calls == [(design, [])]
    assert composer_calls == [(
        spec.model_dump(mode="json"), [], design,
        {"scenarios": {"on_demand": {"complete": False}}},
    )]
    output = capsys.readouterr().out
    assert "AWS cost estimate (JSON)" in output
    assert "Architecture topology (JSON)" in output
    documents = list(tmp_path.glob("architecture-*.md"))
    assert len(documents) == 1
    assert str(documents[0]) in output
    assert "flowchart LR" in documents[0].read_text(encoding="utf-8")

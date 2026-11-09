import copy

import pytest
from test_design_composer import GRAPH

from agent.topology_document import render_topology_document, write_topology_document
from schemas import TopologyGraph


def test_document_includes_graph_flows_and_decisions():
    graph_data = copy.deepcopy(GRAPH)
    graph_data["edges"].append({
        "id": "reply", "source": "app-node", "target": "user", "protocol": "unknown",
        "description": "Response", "expected_volume": None,
        "volume_explanation": "No response traffic estimate.",
    })
    document = render_topology_document(TopologyGraph.model_validate(graph_data))
    assert 'n0["User"]' in document
    assert 'n1["App #40;ec2#44; us#45;east#45;1#41;"]' in document
    assert 'n2["Transfer #40;accounting only#41;"]' in document
    assert 'n0 -->|"HTTPS #124; 10#46;0 requests#47;second"| n1' in document
    assert 'n1 -->|"unknown #124; unknown"| n0' in document
    assert "Peak reads" in document
    assert "peak\\_read\\_qps" in document
    assert "No response traffic estimate" in document
    assert "User to app; no cache selected" not in document  # Markdown punctuation is escaped
    assert "no cache selected" in document
    assert "no queue selected" in document
    assert "No datastore selected" in document
    assert "Single application instance" in document
    assert "None stated" in document


def test_untrusted_labels_cannot_change_mermaid_or_markdown_structure():
    graph_data = copy.deepcopy(GRAPH)
    graph_data["nodes"][0]["id"] = 'id"] --> n1\n```'
    graph_data["edges"][0]["source"] = graph_data["nodes"][0]["id"]
    graph_data["nodes"][0]["label"] = '"] --> n1\n```\n# Attack'
    graph_data["edges"][0]["protocol"] = '"| n1 --> n0\n```'
    graph_data["decisions"]["read_path"] = "<script>alert(1)</script>\n# heading"
    document = render_topology_document(TopologyGraph.model_validate(graph_data))
    assert document.count("```") == 2
    assert document.count(" -->|") == 1
    assert "#34;#93; #45;#45;#62;" in document
    assert "<script>" not in document
    assert "&#60;script&#62;" in document
    assert "\n# heading" not in document


def test_documents_are_written_without_overwriting(tmp_path):
    graph = TopologyGraph.model_validate(GRAPH)
    first = write_topology_document(graph, tmp_path / "designs")
    second = write_topology_document(graph, tmp_path / "designs")
    assert first != second
    assert first.read_text(encoding="utf-8") == render_topology_document(graph)
    assert second.read_text(encoding="utf-8") == render_topology_document(graph)


def test_document_write_failure_is_not_silenced(tmp_path):
    output_dir = tmp_path / "not-a-directory"
    output_dir.write_text("existing", encoding="utf-8")
    with pytest.raises(OSError):
        write_topology_document(TopologyGraph.model_validate(GRAPH), output_dir)


def test_existing_document_is_never_overwritten(tmp_path, monkeypatch):
    class FixedId:
        hex = "fixed"

    monkeypatch.setattr("agent.topology_document.uuid4", lambda: FixedId())
    path = tmp_path / "architecture-fixed.md"
    path.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_topology_document(TopologyGraph.model_validate(GRAPH), tmp_path)
    assert path.read_text(encoding="utf-8") == "existing"

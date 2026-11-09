"""Render a validated topology as an editable Mermaid design document."""

import string
from pathlib import Path
from uuid import uuid4

from schemas import TopologyGraph
from schemas.topology import TopologyEdge


def _single_line(text: str) -> str:
    return " ".join(text.split())


def _mermaid_text(text: str) -> str:
    # Mermaid's entity syntax keeps arbitrary labels from becoming graph syntax.
    return "".join(
        f"#{ord(char)};" if char in string.punctuation else char
        for char in _single_line(text)
    )


def _markdown_text(text: str) -> str:
    return "".join(
        f"&#{ord(char)};" if char in "&<>" else
        f"\\{char}" if char in string.punctuation else char
        for char in _single_line(text)
    )


def _volume(graph_edge: TopologyEdge) -> str:
    volume = graph_edge.expected_volume
    return "unknown" if volume is None else f"{volume.value} {volume.unit}"


def render_topology_document(topology: TopologyGraph) -> str:
    """Render the graph without using the model to invent or modify its content."""
    identifiers = {node.id: f"n{index}" for index, node in enumerate(topology.nodes)}
    lines = ["# Architecture design", "", "## Diagram", "", "```mermaid", "flowchart LR"]
    for node in topology.nodes:
        label = node.label
        if node.kind == "accounting":
            label += " (accounting only)"
        elif node.kind == "component":
            label += f" ({node.service}, {node.region})"
        lines.append(f'    {identifiers[node.id]}["{_mermaid_text(label)}"]')
    for edge in topology.edges:
        label = _mermaid_text(f"{edge.protocol} | {_volume(edge)}")
        lines.append(
            f'    {identifiers[edge.source]} -->|"{label}"| {identifiers[edge.target]}'
        )
    lines.extend(["```", "", "## Components and actors", ""])
    for node in topology.nodes:
        details = f"{node.kind}; node ID: {node.id}"
        if node.component_id is not None:
            details += f"; component ID: {node.component_id}; {node.service}; {node.region}"
        lines.append(
            f"- **{_markdown_text(node.label)}** ({_markdown_text(details)}): "
            f"{_markdown_text(node.description)}"
        )
    lines.extend(["", "## Data flows", ""])
    for edge in topology.edges:
        lines.extend([
            (
                f"- **{_markdown_text(edge.id)}**: {_markdown_text(edge.source)} "
                f"→ {_markdown_text(edge.target)}; protocol: {_markdown_text(edge.protocol)}; "
                f"expected volume: {_markdown_text(_volume(edge))}."
            ),
            f"  - Purpose: {_markdown_text(edge.description)}",
            f"  - Volume explanation: {_markdown_text(edge.volume_explanation)}",
        ])
        if edge.expected_volume is not None:
            volume = edge.expected_volume
            metrics = ", ".join(volume.capacity_metrics) or "none"
            lines.append(f"  - Basis: {_markdown_text(volume.basis)}")
            lines.append(f"  - Capacity metrics: {_markdown_text(metrics)}")
    decisions = topology.decisions
    lines.extend([
        "",
        "## Architecture decisions",
        "",
        f"- **Read path:** {_markdown_text(decisions.read_path)}",
        f"- **Write path:** {_markdown_text(decisions.write_path)}",
        f"- **Availability zones:** {_markdown_text(decisions.availability_zones)}",
        f"- **Datastore failover:** {_markdown_text(decisions.datastore_failover)}",
        "",
        "## Tradeoffs and risks",
        "",
    ])
    lines.extend(f"- {_markdown_text(item)}" for item in decisions.tradeoffs_and_risks)
    lines.extend(["", "## Assumptions", ""])
    lines.extend(
        (f"- {_markdown_text(item)}" for item in decisions.assumptions)
        if decisions.assumptions else ["- None stated."]
    )
    return "\n".join(lines) + "\n"


def write_topology_document(topology: TopologyGraph, output_dir: Path) -> Path:
    """Write a new document without overwriting existing design runs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"architecture-{uuid4().hex}.md"
    with path.open("x", encoding="utf-8", newline="\n") as file:
        file.write(render_topology_document(topology))
    return path

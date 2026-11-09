"""Typed, editable architecture topology produced by the design composer."""

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.requirements import AwsRegion


class FlowVolume(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    value: float = Field(ge=0)
    unit: str = Field(min_length=1, description="For example requests/second or bytes/second.")
    basis: str = Field(min_length=1, description="Calculation or assumption behind this estimate.")
    capacity_metrics: list[str] = Field(
        description="Exact input capacity metric names used, if any."
    )


class TopologyNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, description="Unique graph node ID.")
    kind: Literal["component", "external", "accounting"] = Field(
        description="Selected resource, external actor/system, or non-resource cost item."
    )
    component_id: str | None = Field(
        description="Exact selected component ID, or null for an external node."
    )
    service: str | None = Field(
        description="Exact selected AWS service, or null for an external node."
    )
    region: AwsRegion | None = Field(
        description="Exact selected AWS region, or null for an external node."
    )
    label: str = Field(min_length=1)
    description: str = Field(min_length=1, description="Role and deployment details.")

    @model_validator(mode="after")
    def _check_component_reference(self) -> "TopologyNode":
        if self.kind == "external" and self.component_id is not None:
            raise ValueError("external nodes must not reference a selected component")
        if self.kind == "external" and (self.service is not None or self.region is not None):
            raise ValueError("external nodes must not specify a selected service or region")
        if self.kind != "external" and not self.component_id:
            raise ValueError("component and accounting nodes require a component_id")
        if self.kind != "external" and (not self.service or not self.region):
            raise ValueError("component and accounting nodes require a service and region")
        return self


class TopologyEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, description="Unique graph edge ID.")
    source: str = Field(min_length=1, description="Source node ID.")
    target: str = Field(min_length=1, description="Target node ID.")
    protocol: str = Field(min_length=1, description="Communication protocol, or 'unknown'.")
    description: str = Field(min_length=1, description="Data flow and its purpose.")
    expected_volume: FlowVolume | None = Field(
        description="Estimated flow volume, or null when it cannot be estimated."
    )
    volume_explanation: str = Field(
        min_length=1, description="Explanation of estimate or why volume is unknown."
    )


class ArchitectureDecisions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_path: str = Field(min_length=1)
    write_path: str = Field(min_length=1)
    availability_zones: str = Field(min_length=1)
    datastore_failover: str = Field(min_length=1)
    tradeoffs_and_risks: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(description="Explicit assumptions, not established facts.")


class TopologyGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[TopologyNode] = Field(min_length=1)
    edges: list[TopologyEdge] = Field(min_length=1)
    decisions: ArchitectureDecisions

    @model_validator(mode="after")
    def _validate_graph(self) -> "TopologyGraph":
        for label, ids in (
            ("node", [node.id for node in self.nodes]),
            ("edge", [edge.id for edge in self.edges]),
            ("component reference", [node.component_id for node in self.nodes if node.component_id]),
        ):
            duplicates = sorted(key for key, count in Counter(ids).items() if count > 1)
            if duplicates:
                raise ValueError(f"duplicate {label} IDs: {', '.join(duplicates)}")
        nodes = {node.id: node for node in self.nodes}
        for edge in self.edges:
            if edge.source not in nodes or edge.target not in nodes:
                raise ValueError(f"edge {edge.id} references an unknown node")
            if edge.source == edge.target:
                raise ValueError(f"edge {edge.id} cannot connect a node to itself")
            if nodes[edge.source].kind == "accounting" or nodes[edge.target].kind == "accounting":
                raise ValueError(f"edge {edge.id} cannot connect an accounting node")
        return self

import asyncio
import atexit
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from agent.factory import (
    build_capacity_agent,
    build_component_agent,
    build_composer_agent,
    build_req_agent,
)
from agent.tools.aws_cost_estimator import estimate_aws_cost
from agent.topology_document import write_topology_document
from observability.logger import get_logger
from raptor.retrieval import retrieve_raptor_context
from schemas import ComponentChoices, RequirementSpec, TopologyGraph, unknown_references

load_dotenv(Path(__file__).parent.parent / ".env")

logger = get_logger(__name__)
_agent = None
_memory = None
_database = None
# Max graph steps per agent run (each resume after a question is a new run).
REQUIREMENTS_RECURSION_LIMIT = 25

# Max graph steps for the component agent (doc searches + final structured answer).
COMPONENT_RECURSION_LIMIT = 25
COMPOSER_RECURSION_LIMIT = 25
MAX_VALIDATION_RETRIES = 2
DESIGN_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "designs"


def _close_database() -> None:
    if _database:
        _database.close()


atexit.register(_close_database)


def handle_query(question: str, thread_id: str) -> str:
    """Entry point for all user queries - reuses the agent and runs it."""
    global _agent, _memory, _database
    logger.info("Handling query for session %s", thread_id)
    try:
        if _agent is None:
            _agent, _memory, _database = build_req_agent()
        agent_config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": REQUIREMENTS_RECURSION_LIMIT,
        }
        try:
            response = _agent.invoke(
                {"messages": [{"role": "user", "content": question}]},
                agent_config,
            )
            result = _handle_interrupts(_agent, response, agent_config)
        except GraphRecursionError as e:
            raise RuntimeError(
                "requirements agent couldn't produce a valid spec after "
                f"{REQUIREMENTS_RECURSION_LIMIT} steps"
            ) from e
        spec = result.get("structured_response")
        if spec is None:
            raise RuntimeError("requirements agent finished without a structured spec")
        requirement_spec = spec.model_dump(mode="json")
        print("\nFinal Structured Requirement Spec (JSON):")
        print(spec.model_dump_json(indent=2))
        #_database.save_requirement_specs(thread_id, requirement_spec)

        # Call the capacity agent, which generates and saves sizing specs.
        capacity_results = invoke_capacity_agent(requirement_spec, thread_id)

        # sizing_spec_data = _database.get_sizing_specs(thread_id)
        # print("\nPrinting the sizing_spec_data now:")
        # print(sizing_spec_data)

        # call to component selection agent
        design = handle_component_selection(requirement_spec, capacity_results)

        estimate = estimate_aws_cost(design, capacity_results)
        print("\nAWS cost estimate (JSON):")
        print(json.dumps(estimate, indent=2))

        topology = compose_design(requirement_spec, capacity_results, design, estimate)
        print("\nArchitecture topology (JSON):")
        print(topology.model_dump_json(indent=2))
        document_path = write_topology_document(topology, DESIGN_OUTPUT_DIR)
        print(f"\nArchitecture design document: {document_path}")
        logger.info("Successfully handled query for session %s", thread_id)
        return topology.model_dump_json(indent=2)
    except Exception:
        logger.exception("Failed to handle query for session %s", thread_id)
        raise


def invoke_capacity_agent(requirement_spec, thread_id):
    """Invoke the capacity agent to calculate sizing specs based on the requirement spec."""
    """."""
    capacity_results = build_capacity_agent(requirement_spec)
    return capacity_results



def _handle_interrupts(agent, result, config: dict) -> dict:
    """Collect clarification answers and resume human-in-the-loop tool calls."""
    while result.get("__interrupt__"):
        interrupt = result["__interrupt__"][0]
        interrupt_data = interrupt.value
        logger.info(f"Handling interrupt: {interrupt_data}")

        action_requests = interrupt_data.get("action_requests", [])
        if not action_requests:
            logger.warning("Interrupt did not contain any action requests")
            break

        decisions = []
        for action_request in action_requests:
            question = (
                action_request["args"].get("question") or action_request["description"]
            )
            user_input = ""
            while not user_input:
                user_input = input(f"[HITL] {question} ").strip()
                if not user_input:
                    logger.info("Empty clarification response; asking again")
            decisions.append({"type": "respond", "message": user_input})

        result = agent.invoke(Command(resume={"decisions": decisions}), config)
    return result


async def _select_components(requirement_spec: dict, capacity_results) -> ComponentChoices:
    spec = RequirementSpec.model_validate(requirement_spec)
    raptor_context = retrieve_raptor_context(
        spec.model_dump(mode="json"), capacity_results
    )
    async with build_component_agent() as agent:
        return await _run_component_agent(
            agent, requirement_spec, capacity_results, raptor_context
        )


def _resolve_kb_references(design, raptor_context, messages):
    """Resolve retrieved node IDs and reject KB citations not present in the evidence."""
    node_sources = {}
    sources = set()
    for node in raptor_context:
        values = ([node["source"]] if isinstance(node.get("source"), str) else [])
        if isinstance(node.get("sources"), list):
            values.extend(source for source in node["sources"] if isinstance(source, str))
        values = list(dict.fromkeys(source for source in values if source))
        node_sources[node["node_id"]] = values
        sources.update(values)

    aws_urls = set()
    for message in messages:
        if isinstance(message, ToolMessage):
            if (
                message.name == ComponentChoices.__name__
                or message.status == "error"
                or str(message.content).startswith("MCP tool error:")
            ):
                continue
            content = message.content
        elif isinstance(message, dict) and message.get("role") == "tool":
            content = message.get("content", "")
            if (
                message.get("name") == ComponentChoices.__name__
                or message.get("status") == "error"
                or str(content).startswith("MCP tool error:")
            ):
                continue
        else:
            continue
        for url in re.findall(r"https://[^\s\"'<>\]\\]+", str(content)):
            url = url.rstrip(".,;)")
            if urlsplit(url).hostname == "docs.aws.amazon.com":
                aws_urls.add(url)

    data = design.model_dump(mode="json")
    invalid = []
    for component in data["components"]:
        resolved = []
        for reference in component["based_on"]:
            if not reference.startswith("kb:"):
                resolved.append(reference)
                continue
            key = reference[3:]
            if key in node_sources and len(node_sources[key]) == 1:
                resolved.extend(f"kb:{source}" for source in node_sources[key])
            elif key in sources or key in aws_urls:
                resolved.append(reference)
            else:
                invalid.append(f"{component['id']}: {reference}")
        component["based_on"] = list(dict.fromkeys(resolved))
    if invalid:
        return design, invalid
    return ComponentChoices.model_validate(data), []


async def _run_component_agent(
    agent, requirement_spec, capacity_results, raptor_context: list[dict]
) -> ComponentChoices:
    spec = RequirementSpec.model_validate(requirement_spec)
    messages = [
        {
            "role": "user",
            "content": (
                "1. Requirement Specification (JSON):\n"
                f"{json.dumps(requirement_spec, indent=2)}\n\n"
                "2. Capacity Calculation Results (JSON):\n"
                f"{json.dumps(capacity_results, indent=2, default=str)}\n\n"
                "3. Retrieved RAPTOR Knowledge Base Nodes (JSON):\n"
                f"{json.dumps(raptor_context, indent=2, default=str)}\n\n"
                "4. Retrieved AWS Documentation: use the provided AWS documentation "
                "MCP tools to verify AWS-specific service and instance type choices. "
                "Use the RAPTOR nodes first for relevant architecture patterns, system "
                "type context, and trade-offs, then verify concrete AWS product details "
                "with MCP. Cite RAPTOR knowledge using `kb:<source>` with an exact "
                "source filename from the retrieved nodes, or cite an exact AWS "
                "documentation URL returned by an MCP tool as `kb:<URL>`. "
                "Do not use node IDs as citations."
            ),
        }
    ]
    config = {"recursion_limit": COMPONENT_RECURSION_LIMIT}
    for attempt in range(MAX_VALIDATION_RETRIES + 1):
        try:
            result = await agent.ainvoke({"messages": messages}, config)
        except GraphRecursionError as e:
            raise RuntimeError(
                "component agent couldn't produce a valid design after "
                f"{COMPONENT_RECURSION_LIMIT} steps"
            ) from e
        design = result.get("structured_response")
        if design is None:
            raise RuntimeError("component agent finished without a structured design")
        bad_refs = unknown_references(design, spec)
        design, bad_kb_refs = _resolve_kb_references(
            design, raptor_context, result.get("messages", [])
        )
        bad_refs.extend(bad_kb_refs)
        if not bad_refs:
            return design
        if attempt == MAX_VALIDATION_RETRIES:
            raise RuntimeError(f"component design has unknown references: {bad_refs}")
        messages = result["messages"] + [
            {
                "role": "user",
                "content": (
                    f"These based_on references are not supported by the provided "
                    f"requirements or retrieved evidence: {bad_refs}. "
                    "Return the full corrected design."
                ),
            }
        ]


def handle_component_selection(requirement_spec: dict, capacity_results) -> ComponentChoices:
    design = asyncio.run(_select_components(requirement_spec, capacity_results))
    print("\nSelected components (JSON):")
    print(design.model_dump_json(indent=2))
    return design


def _topology_input_errors(
    topology: TopologyGraph, design: ComponentChoices, capacity_results: list[dict]
) -> list[str]:
    selected = {component.id: component for component in design.components}
    referenced = {node.component_id for node in topology.nodes if node.component_id}
    errors = []
    for component_id in sorted(selected.keys() - referenced):
        errors.append(f"missing selected component: {component_id}")
    for component_id in sorted(referenced - selected.keys()):
        errors.append(f"unknown selected component: {component_id}")
    for node in topology.nodes:
        if node.component_id in selected:
            component = selected[node.component_id]
            is_accounting = component.service == "data_transfer"
            if (node.kind == "accounting") != is_accounting:
                errors.append(f"incorrect node kind for component: {node.component_id}")
            if node.service != component.service or node.region != component.region:
                errors.append(f"incorrect service or region for component: {node.component_id}")
    metrics = {row["metric"] for row in capacity_results if "metric" in row}
    for edge in topology.edges:
        if edge.expected_volume is not None:
            for metric in edge.expected_volume.capacity_metrics:
                if metric not in metrics:
                    errors.append(f"edge {edge.id} references unknown capacity metric: {metric}")
    return errors


def compose_design(
    requirement_spec: dict,
    capacity_results: list[dict],
    design: ComponentChoices,
    estimate: dict,
) -> TopologyGraph:
    """Compose and validate a graph against the exact upstream outputs."""
    agent = build_composer_agent()
    messages = [{
        "role": "user",
        "content": (
            "Requirement specification (JSON):\n"
            f"{json.dumps(requirement_spec, indent=2)}\n\n"
            "Capacity results (JSON):\n"
            f"{json.dumps(capacity_results, indent=2)}\n\n"
            "Selected components (JSON):\n"
            f"{design.model_dump_json(indent=2)}\n\n"
            "AWS cost estimate (JSON):\n"
            f"{json.dumps(estimate, indent=2)}"
        ),
    }]
    for attempt in range(MAX_VALIDATION_RETRIES + 1):
        try:
            result = agent.invoke(
                {"messages": messages}, {"recursion_limit": COMPOSER_RECURSION_LIMIT}
            )
        except GraphRecursionError as exc:
            raise RuntimeError(
                "design composer couldn't produce a valid topology after "
                f"{COMPOSER_RECURSION_LIMIT} steps"
            ) from exc
        topology = result.get("structured_response")
        if topology is None:
            raise RuntimeError("design composer finished without a structured topology")
        topology = TopologyGraph.model_validate(topology)
        errors = _topology_input_errors(topology, design, capacity_results)
        if not errors:
            return topology
        if attempt == MAX_VALIDATION_RETRIES:
            raise RuntimeError(f"design composer produced an invalid topology: {errors}")
        messages = result["messages"] + [{
            "role": "user",
            "content": f"Correct these topology errors and return the full graph: {errors}",
        }]

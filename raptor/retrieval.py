import json
from collections import deque
from typing import Any

from qdrant_client import QdrantClient, models

from config import config
from llm.factory import get_embedder
from observability.logger import get_logger
from raptor.store import _point_id, get_client

logger = get_logger(__name__)


def _build_retrieval_query(
    requirement_spec: dict[str, Any], capacity_results: Any | None
) -> str:
    """Combine the requirements and optional capacity metrics into embedding input."""
    spec = requirement_spec
    explicit = spec.get("explicit_requirements", {})
    lines = [
        f"System type and domain: {spec.get('system_type', '')}",
        "Architecture guidance, design patterns, and trade-offs relevant to this system.",
        "Explicit requirements: " + json.dumps(explicit, ensure_ascii=False),
    ]
    clarifications = spec.get("clarifications")
    if clarifications:
        lines.append(
            "User clarifications: " + json.dumps(clarifications, ensure_ascii=False)
        )
    assumptions = spec.get("assumptions")
    if assumptions:
        lines.append("Sizing assumptions: " + json.dumps(assumptions, ensure_ascii=False))
    if capacity_results is not None:
        lines.append(
            "Calculated capacity and workload metrics: "
            + json.dumps(capacity_results, ensure_ascii=False, default=str)
        )
    return "\n".join(lines)

def _positive_setting(settings: dict, key: str, *, allow_zero: bool = False) -> int:
    """Validate a positive integer limit, allowing zero when explicitly requested."""
    value = settings[key]
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"raptor.{key} must be an integer >= {minimum}")
    return value


def _node_result(point: models.ScoredPoint) -> dict[str, Any]:
    """Validate a scored Qdrant point and extract its text, score, and provenance."""
    payload = point.payload or {}
    node_id = payload.get("node_id")
    text = payload.get("text")
    if not isinstance(node_id, str) or not node_id:
        raise ValueError(f"RAPTOR point {point.id} is missing a valid node_id")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"RAPTOR node {node_id} is missing text")
    return {
        "node_id": node_id,
        "score": point.score,
        "level": payload.get("level"),
        "source": payload.get("source"),
        "sources": payload.get("sources"),
        "header_path": payload.get("header_path"),
        "text": text,
    }


def _search(
    client: QdrantClient,
    collection_name: str,
    vector: list[float],
    query_filter: models.Filter,
    limit: int,
) -> list[models.ScoredPoint]:
    """Search the collection by vector, applying the given filter and result limit."""
    return client.query_points(
        collection_name=collection_name,
        query=vector,
        query_filter=query_filter,
        limit=limit,
        with_payload=True,
    ).points


def _summary_filter(is_summary: bool) -> models.Filter:
    """Create a Qdrant filter selecting either summary nodes or leaf chunks."""
    return models.Filter(
        must=[
            models.FieldCondition(
                key="is_summary", match=models.MatchValue(value=is_summary)
            )
        ]
    )


def _explore_branches(
    client: QdrantClient,
    collection_name: str,
    vector: list[float],
    summaries: list[models.ScoredPoint],
    *,
    max_depth: int,
    max_nodes: int,
    branch_width: int,
    max_child_candidates: int,
) -> list[models.ScoredPoint]:
    """Explore summary children breadth-first, ranking them by query similarity.

    Limits bound traversal depth, returned nodes, children selected per summary,
    and child IDs considered per search. Shared nodes are returned only once.
    """
    selected: dict[str, models.ScoredPoint] = {}
    queue: deque[tuple[models.ScoredPoint, int]] = deque()
    for point in summaries:
        node_id = _node_result(point)["node_id"]
        if node_id not in selected and len(selected) < max_nodes:
            selected[node_id] = point
            queue.append((point, 0))

    while queue and len(selected) < max_nodes:
        parent, depth = queue.popleft()
        if depth >= max_depth:
            continue
        payload = parent.payload or {}
        if payload.get("is_summary") is not True:
            continue
        children = payload.get("children_ids")
        if not isinstance(children, list) or not all(
            isinstance(child, str) and child for child in children
        ):
            raise ValueError(f"RAPTOR summary {payload.get('node_id')} has invalid children_ids")
        if not children:
            logger.warning("RAPTOR summary %s has no children", payload.get("node_id"))
            continue

        # Bound request size; the persisted order determines which candidates fit.
        unique_children = list(dict.fromkeys(child for child in children if child not in selected))
        if len(unique_children) > max_child_candidates:
            logger.warning(
                "RAPTOR summary %s has %d unvisited children; considering first %d",
                payload.get("node_id"),
                len(unique_children),
                max_child_candidates,
            )
        candidate_ids = unique_children[:max_child_candidates]
        if not candidate_ids:
            continue
        child_filter = models.Filter(
            must=[models.HasIdCondition(has_id=[_point_id(child) for child in candidate_ids])]
        )
        children_points = _search(
            client,
            collection_name,
            vector,
            child_filter,
            min(branch_width, max_nodes - len(selected)),
        )
        if not children_points:
            logger.warning(
                "No indexed children found for RAPTOR summary %s", payload.get("node_id")
            )
        for child in children_points:
            node_id = _node_result(child)["node_id"]
            if node_id not in selected:
                selected[node_id] = child
                queue.append((child, depth + 1))
                logger.debug(
                    "RAPTOR branch parent=%s child=%s depth=%d score=%.4f",
                    payload.get("node_id"), node_id, depth + 1, child.score,
                )
    return list(selected.values())


def retrieve_raptor_context(
    requirement_spec: dict[str, Any], capacity_results: Any | None = None
) -> list[dict[str, Any]]:
    """Find relevant summary branches and direct leaf matches for a requirement.

    Combines and deduplicates results with source metadata, closing the Qdrant
    client even if retrieval fails.
    """
    raptor_cfg = config["raptor"]
    qdrant_cfg = config["qdrant"]
    collection_name = qdrant_cfg["collection_name"]
    summary_top_k = _positive_setting(raptor_cfg, "retrieval_summary_top_k")
    leaf_top_k = _positive_setting(raptor_cfg, "retrieval_leaf_top_k")
    max_depth = _positive_setting(raptor_cfg, "retrieval_max_depth", allow_zero=True)
    max_nodes = _positive_setting(raptor_cfg, "retrieval_max_branch_nodes")
    branch_width = _positive_setting(raptor_cfg, "retrieval_branch_width")
    max_candidates = _positive_setting(raptor_cfg, "retrieval_max_child_candidates")
    log_text = raptor_cfg.get("retrieval_log_text_previews", False)
    if not isinstance(log_text, bool):
        raise TypeError("raptor.retrieval_log_text_previews must be a boolean")
    client = get_client()
    try:
        if not client.collection_exists(collection_name):
            raise RuntimeError(
                f"RAPTOR collection '{collection_name}' does not exist. "
                "Build the index first with `uv run python -m raptor.pipeline`."
            )

        query = _build_retrieval_query(requirement_spec, capacity_results)
        vector = get_embedder().embed_query(query)
        summaries = _search(
            client, collection_name, vector, _summary_filter(True),
            min(summary_top_k, max_nodes),
        )
        branch_nodes = _explore_branches(
            client, collection_name, vector, summaries,
            max_depth=max_depth, max_nodes=max_nodes,
            branch_width=branch_width, max_child_candidates=max_candidates,
        )
        direct_leaves = _search(
            client, collection_name, vector, _summary_filter(False), leaf_top_k
        )
        results_by_id = {}
        for point in [*branch_nodes, *direct_leaves]:
            result = _node_result(point)
            results_by_id.setdefault(result["node_id"], result)
        results = list(results_by_id.values())
        if not results:
            raise RuntimeError(
                f"RAPTOR collection '{collection_name}' returned no usable "
                "text nodes for this requirement."
            )
        logger.info(
            "RAPTOR retrieval collection=%s initial_summaries=%d branch_nodes=%d "
            "direct_leaves=%d unique_nodes=%d",
            collection_name, len(summaries), len(branch_nodes),
            len(direct_leaves), len(results),
        )
        for result in results:
            logger.info(
                "RAPTOR node id=%s level=%s score=%.4f source=%r sources=%r header=%r",
                result["node_id"], result["level"], result["score"], result["source"],
                result["sources"], result["header_path"],
            )
            if log_text:
                preview = " ".join(result["text"].split())[:160]
                logger.debug("RAPTOR node id=%s text_preview=%r", result["node_id"], preview)
        return results
    finally:
        client.close()

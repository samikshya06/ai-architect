import hashlib

from langchain_core.messages import HumanMessage, SystemMessage

from raptor.chunking import Node

SYSTEM_PROMPT = (
    "You are a staff-level system design architect. Write a comprehensive, "
    "factual summary of the provided passages. Preserve concrete details: "
    "system names, components, numbers, trade-offs, and patterns (e.g. caching, "
    "rate limiting, sharding, idempotency). State which kinds of systems "
    "(ride hailing, messenger, payments, streaming, ...) the content applies "
    "to. Do not invent facts."
)


def _batches(nodes: list[Node], max_chars: int) -> list[list[Node]]:
    batches: list[list[Node]] = [[]]
    size = 0
    for node in nodes:
        if batches[-1] and size + len(node.text) > max_chars:
            batches.append([])
            size = 0
        batches[-1].append(node)
        size += len(node.text)
    return batches


def _sources(node: Node) -> list[str]:
    if "sources" in node.metadata:
        return node.metadata["sources"]
    return [node.metadata["source"]] if "source" in node.metadata else []


def summarize_cluster(
    llm, members: list[Node], level: int, cluster_id: int, max_chars: int
) -> list[Node]:
    """Summarize a cluster into one summary node (several if it exceeds max_chars)."""
    out: list[Node] = []
    for part, batch in enumerate(_batches(members, max_chars)):
        body = "\n\n---\n\n".join(n.text for n in batch)
        reply = llm.invoke(
            [SystemMessage(SYSTEM_PROMPT), HumanMessage(f"Passages:\n\n{body}")]
        )
        text = reply.content if isinstance(reply.content, str) else str(reply.content)
        child_ids = [n.id for n in batch]
        digest = hashlib.sha1("|".join(child_ids).encode()).hexdigest()[:16]
        out.append(
            Node(
                id=f"L{level}-{digest}",
                text=text.strip(),
                level=level,
                metadata={
                    "is_summary": True,
                    "cluster_id": cluster_id,
                    "cluster_part": part,
                    "children_ids": child_ids,
                    "num_children": len(child_ids),
                    "sources": sorted({s for n in batch for s in _sources(n)}),
                },
            )
        )
    return out

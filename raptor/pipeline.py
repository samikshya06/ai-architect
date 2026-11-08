from pathlib import Path

from config import config
from llm.factory import get_embedder, get_small_llm
from observability.logger import get_logger
from raptor.chunking import Node, chunk_directory
from raptor.clustering import cluster_embeddings
from raptor.store import ensure_collection, get_client, upsert_nodes
from raptor.summarize import summarize_cluster

logger = get_logger(__name__)


def _embed(embedder, nodes: list[Node], batch: int) -> None:
    for i in range(0, len(nodes), batch):
        part = nodes[i : i + batch]
        vectors = embedder.embed_documents([n.text for n in part])
        for node, vec in zip(part, vectors):
            node.embedding = vec


def build_raptor_index(docs_dir: str | None = None, recreate: bool = True) -> list[Node]:
    """Chunk -> embed -> GMM cluster -> summarize -> recurse to a root -> Qdrant."""
    cfg = config["raptor"]
    docs = Path(docs_dir or cfg["docs_dir"])
    embedder, llm = get_embedder(), get_small_llm()

    leaves = chunk_directory(docs, cfg["chunk_size"], cfg["chunk_overlap"])
    if not leaves:
        raise ValueError(f"No markdown chunks found in {docs}")
    logger.info(f"Level 0: {len(leaves)} chunks")
    _embed(embedder, leaves, cfg["embed_batch_size"])

    all_nodes = list(leaves)
    current, level = leaves, 0
    while len(current) > 1 and level < cfg["max_levels"]:
        level += 1
        groups = cluster_embeddings(
            [n.embedding for n in current], cfg["max_clusters"], cfg["soft_threshold"]
        )
        # no compression, or last allowed level: collapse into a single cluster
        if len(groups) >= len(current) or level == cfg["max_levels"]:
            groups = [list(range(len(current)))]

        summaries: list[Node] = []
        for cid, idxs in enumerate(groups):
            members = [current[i] for i in idxs]
            summaries.extend(
                summarize_cluster(llm, members, level, cid, cfg["max_cluster_chars"])
            )
        # soft clustering can yield identical summaries; dedupe by id
        summaries = list({n.id: n for n in summaries}.values())
        _embed(embedder, summaries, cfg["embed_batch_size"])
        logger.info(f"Level {level}: {len(groups)} clusters -> {len(summaries)} summaries")
        all_nodes.extend(summaries)
        current = summaries

    # A very large single cluster can still yield several batch summaries.
    if len(current) > 1:
        level += 1
        root = summarize_cluster(llm, current, level, 0, cfg["max_cluster_chars"])
        _embed(embedder, root, cfg["embed_batch_size"])
        all_nodes.extend(root)
        current = root
    for node in current:
        node.metadata["is_root"] = True

    qcfg = config["qdrant"]
    client = get_client()
    try:
        ensure_collection(
            client, qcfg["collection_name"], len(leaves[0].embedding), recreate
        )
        upsert_nodes(
            client,
            qcfg["collection_name"],
            all_nodes,
            batch=qcfg.get("upsert_batch_size", 16),
        )
    finally:
        client.close()
    logger.info(f"Stored {len(all_nodes)} nodes in '{qcfg['collection_name']}'")
    return all_nodes


if __name__ == "__main__":
    build_raptor_index()

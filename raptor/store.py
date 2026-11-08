import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

from config import config
from observability.logger import get_logger
from raptor.chunking import Node

logger = get_logger(__name__)

INDEXED_FIELDS = {
    "level": models.PayloadSchemaType.INTEGER,
    "source": models.PayloadSchemaType.KEYWORD,
    "is_summary": models.PayloadSchemaType.BOOL,
}


def get_client() -> QdrantClient:
    cfg = config["qdrant"]
    if cfg.get("url"):
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        return QdrantClient(
            url=cfg["url"],
            api_key=os.getenv("QDRANT_API_KEY"),
            timeout=cfg.get("timeout_seconds", 60),
        )
    return QdrantClient(path=cfg["path"])


def _point_id(node_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, node_id))


def ensure_collection(client: QdrantClient, name: str, dim: int, recreate: bool):
    if recreate and client.collection_exists(name):
        client.delete_collection(name)
    if not client.collection_exists(name):
        client.create_collection(
            name,
            vectors_config=models.VectorParams(
                size=dim, distance=models.Distance.COSINE
            ),
        )
        for field, schema in INDEXED_FIELDS.items():
            client.create_payload_index(name, field, schema)


def upsert_nodes(client: QdrantClient, name: str, nodes: list[Node], batch: int = 16):
    """Store every tree node (leaf chunks and summaries) with its metadata."""
    if batch <= 0:
        raise ValueError("Qdrant upsert batch size must be greater than zero")
    for i in range(0, len(nodes), batch):
        logger.info(
            "Writing Qdrant nodes %d-%d of %d",
            i + 1,
            min(i + batch, len(nodes)),
            len(nodes),
        )
        client.upsert(
            name,
            points=[
                models.PointStruct(
                    id=_point_id(n.id),
                    vector=n.embedding,
                    payload={
                        "node_id": n.id,
                        "text": n.text,
                        "level": n.level,
                        **n.metadata,
                    },
                )
                for n in nodes[i : i + batch]
            ],
        )

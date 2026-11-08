import pytest
from qdrant_client import QdrantClient, models

from raptor import store
from raptor.chunking import Node


def test_remote_client_uses_configured_timeout(monkeypatch):
    kwargs = {}

    def fake_client(**options):
        kwargs.update(options)
        return object()

    monkeypatch.setitem(
        store.config,
        "qdrant",
        {"url": "https://example.invalid", "timeout_seconds": 75},
    )
    monkeypatch.setenv("QDRANT_API_KEY", "test-key")
    monkeypatch.setattr(store, "QdrantClient", fake_client)
    store.get_client()

    assert kwargs == {
        "url": "https://example.invalid",
        "api_key": "test-key",
        "timeout": 75,
    }


def test_upsert_nodes_batches_and_stores_all_points(monkeypatch):
    client = QdrantClient(":memory:")
    try:
        client.create_collection(
            "test",
            vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
        )
        nodes = [
            Node(id=f"L0-{i}", text=f"chunk {i}", level=0, embedding=[1.0, 0.0])
            for i in range(5)
        ]
        batch_sizes = []
        upsert = client.upsert

        def record_upsert(name, points):
            batch_sizes.append(len(points))
            return upsert(name, points=points)

        monkeypatch.setattr(client, "upsert", record_upsert)
        store.upsert_nodes(client, "test", nodes, batch=2)
        assert batch_sizes == [2, 2, 1]
        assert client.count("test", exact=True).count == 5
        point = client.retrieve("test", [store._point_id("L0-4")])[0]
        assert point.payload["text"] == "chunk 4"
    finally:
        client.close()


def test_upsert_rejects_nonpositive_batch_size():
    with pytest.raises(ValueError, match="batch size"):
        store.upsert_nodes(None, "test", [], batch=0)

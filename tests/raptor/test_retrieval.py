import logging
from unittest.mock import Mock

import pytest
from qdrant_client import QdrantClient, models

from raptor import retrieval
from raptor.store import _point_id


class FakeEmbedder:
    def embed_query(self, query):
        self.query = query
        return [1.0, 0.0]


@pytest.fixture
def settings(monkeypatch):
    settings = {
        "retrieval_summary_top_k": 2,
        "retrieval_leaf_top_k": 1,
        "retrieval_max_depth": 2,
        "retrieval_max_branch_nodes": 8,
        "retrieval_branch_width": 2,
        "retrieval_max_child_candidates": 10,
    }
    monkeypatch.setitem(retrieval.config, "raptor", settings)
    monkeypatch.setitem(retrieval.config, "qdrant", {"collection_name": "test"})
    return settings


@pytest.fixture
def client(monkeypatch, settings):
    client = QdrantClient(":memory:")
    client.create_collection(
        "test",
        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
    )
    # Keep the in-memory collection alive for assertions after the code closes it.
    close = client.close
    monkeypatch.setattr(client, "close", Mock())
    monkeypatch.setattr(retrieval, "get_client", lambda: client)
    monkeypatch.setattr(retrieval, "get_embedder", lambda: FakeEmbedder())
    yield client
    close()


def add_point(client, node_id, vector, *, children=None):
    payload = {
        "node_id": node_id,
        "text": f"text for {node_id}",
        "is_summary": children is not None,
        "level": 1 if children is not None else 0,
    }
    if children is not None:
        payload["children_ids"] = children
        payload["sources"] = ["source.md"]
    else:
        payload["source"] = "source.md"
        payload["header_path"] = "Architecture > Cache"
    client.upsert(
        "test",
        points=[
            models.PointStruct(id=_point_id(node_id), vector=vector, payload=payload)
        ],
    )


def test_searches_branches_and_direct_leaves_deduplicating_shared_nodes(client):
    add_point(client, "L2-root", [1.0, 0.0], children=["L1-child", "L0-shared"])
    add_point(client, "L1-child", [0.99, 0.1], children=["L0-shared", "L0-deep"])
    add_point(client, "L0-shared", [0.9, 0.4])
    add_point(client, "L0-deep", [0.8, 0.6])
    add_point(client, "L0-direct", [1.0, 0.0])

    results = retrieval.retrieve_raptor_context({"system_type": "messaging"})
    by_id = {node["node_id"]: node for node in results}

    assert set(by_id) == {"L2-root", "L1-child", "L0-shared", "L0-deep", "L0-direct"}
    assert len(results) == len(by_id)
    assert by_id["L0-deep"]["header_path"] == "Architecture > Cache"
    assert by_id["L2-root"]["sources"] == ["source.md"]
    assert by_id["L0-direct"]["score"] == pytest.approx(1.0)
    client.close.assert_called_once()


def test_branch_depth_width_and_node_budget_are_enforced(client, settings):
    settings.update(
        retrieval_summary_top_k=1,
        retrieval_leaf_top_k=1,
        retrieval_max_depth=1,
        retrieval_max_branch_nodes=2,
        retrieval_branch_width=2,
    )
    add_point(client, "L2-root", [1.0, 0.0], children=["L1-best", "L1-other"])
    add_point(client, "L1-best", [0.9, 0.4], children=["L0-deep"])
    add_point(client, "L1-other", [0.8, 0.6], children=["L0-deep"])
    add_point(client, "L0-deep", [0.0, 1.0])
    add_point(client, "L0-direct", [1.0, 0.0])

    results = retrieval.retrieve_raptor_context({"system_type": "messaging"})

    assert {node["node_id"] for node in results} == {"L2-root", "L1-best", "L0-direct"}


def test_depth_zero_does_not_traverse(client, settings):
    settings.update(retrieval_summary_top_k=1, retrieval_max_depth=0)
    add_point(client, "L1-root", [1.0, 0.0], children=["L0-child"])
    add_point(client, "L0-child", [0.7, 0.7])
    add_point(client, "L0-direct", [1.0, 0.0])
    results = retrieval.retrieve_raptor_context({"system_type": "messaging"})
    assert {node["node_id"] for node in results} == {"L1-root", "L0-direct"}


def test_depth_one_stops_before_grandchildren(client, settings):
    settings.update(
        retrieval_summary_top_k=1,
        retrieval_max_depth=1,
        retrieval_branch_width=1,
    )
    add_point(client, "L2-root", [1.0, 0.0], children=["L1-child"])
    add_point(client, "L1-child", [0.8, 0.6], children=["L0-grandchild"])
    add_point(client, "L0-grandchild", [0.7, 0.7])
    add_point(client, "L0-direct", [1.0, 0.0])
    results = retrieval.retrieve_raptor_context({"system_type": "messaging"})
    assert {node["node_id"] for node in results} == {
        "L2-root", "L1-child", "L0-direct"
    }


def test_branch_width_selects_highest_scoring_children(client, settings):
    settings.update(retrieval_summary_top_k=1, retrieval_branch_width=1)
    add_point(client, "L1-root", [1.0, 0.0], children=["L0-worse", "L0-better"])
    add_point(client, "L0-worse", [0.0, 1.0])
    add_point(client, "L0-better", [0.8, 0.6])
    add_point(client, "L0-direct", [1.0, 0.0])
    results = retrieval.retrieve_raptor_context({"system_type": "messaging"})
    assert {node["node_id"] for node in results} == {
        "L1-root", "L0-better", "L0-direct"
    }


def test_candidate_limit_logs_truncation(client, settings, caplog):
    settings.update(
        retrieval_summary_top_k=1,
        retrieval_branch_width=1,
        retrieval_max_child_candidates=1,
    )
    add_point(client, "L1-root", [1.0, 0.0], children=["L0-first", "L0-second"])
    add_point(client, "L0-first", [0.0, 1.0])
    add_point(client, "L0-second", [0.8, 0.6])
    add_point(client, "L0-direct", [1.0, 0.0])
    results = retrieval.retrieve_raptor_context({"system_type": "messaging"})
    assert {node["node_id"] for node in results} == {
        "L1-root", "L0-first", "L0-direct"
    }
    assert "considering first 1" in caplog.text


def test_missing_children_are_reported_and_direct_leaves_are_returned(client, caplog):
    add_point(client, "L1-root", [1.0, 0.0], children=["L0-missing"])
    add_point(client, "L0-direct", [1.0, 0.0])
    results = retrieval.retrieve_raptor_context({"system_type": "messaging"})
    assert {node["node_id"] for node in results} == {"L1-root", "L0-direct"}
    assert "No indexed children found" in caplog.text


def test_direct_leaf_search_works_without_summaries(client):
    add_point(client, "L0-only", [1.0, 0.0])
    results = retrieval.retrieve_raptor_context({"system_type": "messaging"})
    assert [node["node_id"] for node in results] == ["L0-only"]


def test_retrieval_logs_selected_nodes_and_branches_without_text(client, caplog):
    add_point(client, "L1-root", [1.0, 0.0], children=["L0-child"])
    add_point(client, "L0-child", [0.8, 0.6])
    add_point(client, "L0-direct", [1.0, 0.0])

    with caplog.at_level(logging.DEBUG, logger=retrieval.logger.name):
        results = retrieval.retrieve_raptor_context({"system_type": "private request"})

    assert "initial_summaries=1 branch_nodes=2 direct_leaves=1 unique_nodes=3" in caplog.text
    assert "parent=L1-root child=L0-child depth=1" in caplog.text
    for result in results:
        assert f"RAPTOR node id={result['node_id']}" in caplog.text
    assert "source='source.md'" in caplog.text
    assert "score=" in caplog.text
    assert "text for L0-child" not in caplog.text
    assert "private request" not in caplog.text


def test_text_preview_is_opt_in_and_bounded(client, settings, caplog):
    settings["retrieval_log_text_previews"] = True
    add_point(client, "L0-only", [1.0, 0.0])
    text = "first line\n" + "x" * 200
    client.set_payload("test", {"text": text}, [_point_id("L0-only")])

    with caplog.at_level(logging.DEBUG, logger=retrieval.logger.name):
        retrieval.retrieve_raptor_context({"system_type": "messaging"})

    messages = [record.getMessage() for record in caplog.records if "text_preview=" in record.getMessage()]
    assert len(messages) == 1
    assert "first line " in messages[0]
    assert "x" * 150 not in messages[0]


def test_invalid_text_preview_setting_is_rejected(monkeypatch, settings):
    settings["retrieval_log_text_previews"] = "false"
    connect = Mock()
    monkeypatch.setattr(retrieval, "get_client", connect)
    with pytest.raises(TypeError, match="retrieval_log_text_previews"):
        retrieval.retrieve_raptor_context({"system_type": "messaging"})
    connect.assert_not_called()


def test_retrieval_query_includes_capacity_results():
    query = retrieval._build_retrieval_query(
        {
            "system_type": "messaging",
            "explicit_requirements": {"daily_active_users": 1000},
            "clarifications": {"voice_video": "yes"},
            "assumptions": {"messages_per_user_per_day": 20},
        },
        {"peak_qps": 50},
    )
    for field in ("messaging", "daily_active_users", "voice_video",
                  "messages_per_user_per_day", "peak_qps"):
        assert field in query


def test_retrieve_raptor_context_requires_an_index(monkeypatch, settings):
    client = Mock()
    client.collection_exists.return_value = False
    monkeypatch.setattr(retrieval, "get_client", lambda: client)
    with pytest.raises(RuntimeError, match="Build the index first"):
        retrieval.retrieve_raptor_context({"system_type": "messaging"})
    client.close.assert_called_once()


def test_retrieve_raptor_context_rejects_empty_search_results(client):
    with pytest.raises(RuntimeError, match="no usable text nodes"):
        retrieval.retrieve_raptor_context({"system_type": "messaging"})
    client.close.assert_called_once()


def test_invalid_config_is_rejected_before_connection(monkeypatch, settings):
    settings["retrieval_branch_width"] = 0
    connect = Mock()
    monkeypatch.setattr(retrieval, "get_client", connect)
    with pytest.raises(ValueError, match="retrieval_branch_width"):
        retrieval.retrieve_raptor_context({"system_type": "messaging"})
    connect.assert_not_called()


def test_invalid_summary_children_raise_and_close(client):
    add_point(client, "L1-root", [1.0, 0.0], children=["L0-leaf"])
    client.set_payload("test", {"children_ids": "invalid"}, [_point_id("L1-root")])
    with pytest.raises(ValueError, match="invalid children_ids"):
        retrieval.retrieve_raptor_context({"system_type": "messaging"})
    client.close.assert_called_once()

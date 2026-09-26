from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portolan.api.app import create_app
from portolan.settings import Settings, make_repository


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    app = create_app(Settings(store="oxigraph", seed_golden=True))
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_store_and_seeded_works(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "store": "oxigraph", "works": 19}


def test_graph_returns_golden_citation_map(client: TestClient) -> None:
    body = client.get("/api/graph").json()
    ids = {node["id"] for node in body["nodes"]}
    assert len(ids) == 19
    assert len(body["edges"]) == 51
    assert all(edge["source"] in ids and edge["target"] in ids for edge in body["edges"])
    rag = next(node for node in body["nodes"] if node["id"].endswith("arxiv/2005.11401"))
    assert rag["title"] == "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
    assert rag["year"] == 2020
    assert rag["sourceTier"] == "preprint"
    assert sum(node["citedBy"] for node in body["nodes"]) == 51


def test_unseeded_in_memory_store_starts_empty() -> None:
    with TestClient(create_app(Settings(store="oxigraph"))) as test_client:
        assert test_client.get("/api/health").json()["works"] == 0
        assert test_client.get("/api/graph").json() == {"nodes": [], "edges": []}


def test_persistent_oxigraph_survives_restart(tmp_path: Path) -> None:
    settings = Settings(store="oxigraph", data_dir=tmp_path, seed_golden=True)
    with TestClient(create_app(settings)) as test_client:
        assert test_client.get("/api/health").json()["works"] == 19
    unseeded = Settings(store="oxigraph", data_dir=tmp_path)
    with TestClient(create_app(unseeded)) as test_client:
        assert test_client.get("/api/health").json()["works"] == 19


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PORTOLAN_STORE", "Neo4j")
    monkeypatch.setenv("PORTOLAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORTOLAN_SEED_GOLDEN", "true")
    monkeypatch.setenv("NEO4J_URI", "bolt://neo4j:7687")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    settings = Settings.from_env()
    assert settings.store == "neo4j"
    assert settings.data_dir == tmp_path
    assert settings.seed_golden is True
    assert "secret" not in settings.describe()
    assert settings.startup_timeout == 60


def test_settings_reject_unknown_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTOLAN_STORE", "sqlite")
    with pytest.raises(ValueError, match="PORTOLAN_STORE"):
        Settings.from_env()


def test_neo4j_store_requires_uri() -> None:
    with pytest.raises(ValueError, match="NEO4J_URI"):
        make_repository(Settings(store="neo4j"))

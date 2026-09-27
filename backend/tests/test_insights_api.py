"""Offline store, verification, and API checks for project frontier and structural gaps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from portolan.analysis.gap_store import GapStore, verify_gap
from portolan.analysis.gaps import GapHypothesis
from portolan.api import insights
from portolan.graph import Inclusion, WorkNode
from portolan.graph.memory import InMemoryResearchGraph
from portolan.settings import Settings


class _OpenAlex:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append((query, kwargs))
        if self.error is not None:
            raise self.error
        return self.results


class _Sources:
    def __init__(self, openalex: _OpenAlex) -> None:
        self.openalex = openalex
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _gap(gap_id: str = "structural-gap-1", gap_type: str = "matrix_void") -> GapHypothesis:
    return GapHypothesis(
        id=gap_id,
        type=gap_type,
        statement="Concept A and Concept B are each studied but never combined.",
        evidence={"work_ids": ["seed"], "concept_ids": ["a", "b"], "cluster_ids": []},
        metrics={"adamic_adar": 1.0},
        confidence=0.8,
        search_terms=["Concept A", "Concept B"],
    )


def _hits(year: int, count: int, prefix: str = "W9") -> list[dict[str, Any]]:
    return [
        {
            "openalex_id": f"{prefix}{index}",
            "identifiers": {"openalex": f"{prefix}{index}"},
            "title": f"Outside {index}",
            "year": year,
        }
        for index in range(count)
    ]


def _client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    patch_detect: bool = True,
) -> tuple[TestClient, str, list[GapHypothesis], _OpenAlex]:
    graph = InMemoryResearchGraph()
    project_id = graph.create_project("Insights").id
    graph.upsert_work(
        WorkNode(
            id="seed",
            title="Seed",
            year=2024,
            openalex_id="W1",
            doi="10.1000/seed",
            cited_by_count=3,
        )
    )
    graph.include_work(Inclusion(project_id=project_id, work_id="seed", discovered_via="seed"))
    computed = [_gap()]
    if patch_detect:
        monkeypatch.setattr(insights, "detect_gaps", lambda view, analysis: list(computed))
    adapter = _OpenAlex()
    app = FastAPI()
    app.include_router(
        insights.build_insights_router(
            lambda: graph,
            Settings(store="memory", data_dir=tmp_path),
            sources_factory=lambda settings: _Sources(adapter),
        )
    )
    return TestClient(app), project_id, computed, adapter


def test_gap_status_round_trip_rejection_and_stale(monkeypatch, tmp_path) -> None:
    client, project_id, computed, _ = _client(monkeypatch, tmp_path)
    base = f"/api/projects/{project_id}/gaps"
    with client:
        response = client.get(base)
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["status"] == "proposed"
        assert response.json()[0]["stale"] is False
        gap_id = response.json()[0]["id"]

        response = client.patch(
            f"{base}/{gap_id}", json={"status": "rejected", "note": "Already known"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert response.json()["note"] == "Already known"
        assert client.get(base).json() == []
        assert client.get(base, params={"include_rejected": True}).json()[0]["status"] == "rejected"

        # Omitted fields are kept; an explicit null note clears the note.
        kept = client.patch(f"{base}/{gap_id}", json={})
        assert kept.json()["status"] == "rejected"
        assert kept.json()["note"] == "Already known"
        cleared = client.patch(f"{base}/{gap_id}", json={"note": None})
        assert cleared.json()["note"] is None
        assert cleared.json()["status"] == "rejected"

        computed.clear()
        stale = client.get(base, params={"include_rejected": True}).json()
        assert len(stale) == 1
        assert stale[0]["stale"] is True
        assert stale[0]["status"] == "rejected"

        # Editing a stale gap keeps it stale.
        reopened = client.patch(f"{base}/{gap_id}", json={"status": "accepted"})
        assert reopened.status_code == 200
        assert reopened.json()["stale"] is True
        assert client.get(base).json()[0]["stale"] is True

        # Rejected gaps do not resurface when detection emits them again.
        client.patch(f"{base}/{gap_id}", json={"status": "rejected"})
        computed.append(_gap())
        assert client.get(base).json() == []
        again = client.get(base, params={"include_rejected": True}).json()
        assert [(gap["status"], gap["stale"]) for gap in again] == [("rejected", False)]


def test_verification_verdicts_and_network_error(monkeypatch, tmp_path) -> None:
    client, project_id, _, adapter = _client(monkeypatch, tmp_path)
    path = f"/api/projects/{project_id}/gaps/{_gap().id}/verify"
    with client:
        adapter.results = _hits(2025, 3)
        likely = client.post(path)
        assert likely.status_code == 200
        verification = likely.json()["verification"]
        assert verification["verdict"] == "likely_filled"
        assert verification["query"] == "Concept A Concept B"
        assert verification["total_hits_sampled"] == 3
        assert len(verification["outside_hits"]) == 3
        assert adapter.calls[-1] == ("Concept A Concept B", {"limit": 5})

        # The stored verification is returned by later reads.
        listed = client.get(f"/api/projects/{project_id}/gaps").json()
        assert listed[0]["verification"]["verdict"] == "likely_filled"

        # Old outside hits do not count; in-project works are not "outside".
        adapter.results = [
            *_hits(2020, 3, prefix="W8"),
            {"openalex_id": "W1", "identifiers": {"openalex": "W1"}, "title": "Seed", "year": 2024},
            {"identifiers": {"doi": "https://doi.org/10.1000/SEED"}, "title": "Seed", "year": 2024},
        ]
        possible = client.post(path)
        assert possible.status_code == 200
        verification = possible.json()["verification"]
        assert verification["verdict"] == "possibly_open"
        assert verification["total_hits_sampled"] == 5
        assert [hit["id"] for hit in verification["outside_hits"]] == ["W80", "W81", "W82"]

        adapter.error = RuntimeError("offline")
        unknown = client.post(path)
        assert unknown.status_code == 200
        assert unknown.json()["verification"]["verdict"] == "unknown"
        assert "offline" in unknown.json()["verification"]["error"]


def test_verify_gap_restricts_stagnation_search_to_recent_years() -> None:
    adapter = _OpenAlex()
    adapter.results = _hits(2024, 4)
    gap = _gap("stagnant", "stagnation").model_copy(update={"search_terms": ["Old methods"]})

    result = verify_gap(gap, _Sources(adapter), newest_year=2024, limit=3)

    assert adapter.calls == [("Old methods", {"limit": 3, "from_year": 2023})]
    assert result["total_hits_sampled"] == 3
    assert result["verdict"] == "likely_filled"
    assert set(result) >= {"query", "checked_at", "total_hits_sampled", "outside_hits", "verdict"}


def test_verify_gap_without_openalex_is_unknown() -> None:
    result = verify_gap(_gap(), object(), newest_year=2024)
    assert result["verdict"] == "unknown"
    assert result["error"]


def test_gap_store_writes_one_file_per_project_and_validates_ids(tmp_path) -> None:
    store = GapStore(tmp_path / "gaps")
    store.merge("project-1", [_gap()])
    updated = store.update("project-1", _gap().id, [_gap()], status="accepted", note="n")
    assert updated is not None
    assert updated.status == "accepted"

    payload = json.loads((tmp_path / "gaps" / "project-1.json").read_text())
    record = payload[_gap().id]
    assert record["status"] == "accepted"
    assert record["note"] == "n"
    assert record["updated_at"]
    assert list((tmp_path / "gaps").iterdir()) == [tmp_path / "gaps" / "project-1.json"]

    assert store.update("project-1", "absent", [_gap()], status="accepted") is None
    with pytest.raises(ValueError):
        store.update("project-1", _gap().id, [_gap()], status="maybe")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        store.merge("../escape", [_gap()])


def test_frontier_and_real_gap_detection_on_a_small_project(monkeypatch, tmp_path) -> None:
    client, project_id, _, _ = _client(monkeypatch, tmp_path, patch_detect=False)
    with client:
        response = client.get(f"/api/projects/{project_id}/frontier")
        assert response.status_code == 200
        body = response.json()
        assert body["now_year"] == 2024
        assert body["window_years"] == 2
        assert body["works"][0]["work_id"] == "seed"
        assert set(body["works"][0]["components"]) == {
            "velocity",
            "local_uptake",
            "main_path_leaf",
            "cluster_growth",
            "new_concept",
            "preprint",
        }
        assert body["concepts"] == []

        gaps = client.get(f"/api/projects/{project_id}/gaps")
        assert gaps.status_code == 200
        assert gaps.json() == []


def test_error_responses(monkeypatch, tmp_path) -> None:
    client, project_id, _, _ = _client(monkeypatch, tmp_path)
    with client:
        assert client.get("/api/projects/missing/frontier").status_code == 404
        assert client.get("/api/projects/missing/gaps").status_code == 404
        assert client.patch("/api/projects/missing/gaps/absent", json={}).status_code == 404
        assert client.post("/api/projects/missing/gaps/absent/verify").status_code == 404

        base = f"/api/projects/{project_id}/gaps"
        assert client.patch(f"{base}/absent", json={"status": "accepted"}).status_code == 404
        assert client.post(f"{base}/absent/verify").status_code == 404
        assert client.patch(f"{base}/{_gap().id}", json={"status": "invalid"}).status_code == 422
        assert client.patch(f"{base}/{_gap().id}", json={"extra": 1}).status_code == 422
        assert client.get(f"/api/projects/{project_id}/frontier?window_years=0").status_code == 422

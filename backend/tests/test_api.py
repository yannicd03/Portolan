"""HTTP API tests for the v1 graph and research run endpoints."""

from __future__ import annotations

import sys
import threading
import time
import types
from collections.abc import Callable, Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from portolan.api.app import create_app
from portolan.documents import DocumentStore
from portolan.graph import AuthorNode, ConceptNode, Inclusion, WorkNode
from portolan.graph.memory import InMemoryResearchGraph
from portolan.settings import Settings, make_graph


class FakeProgress:
    """Small progress object with the interface used by the run registry."""

    def __init__(self, stage: str, message: str, counts: dict[str, int]) -> None:
        self.stage = stage
        self.message = message
        self.counts = counts

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "message": self.message,
            "counts": self.counts,
        }


class FakeReport:
    """JSON-ready report object accepted by either a dict or model based registry."""

    def __init__(self, project_id: str) -> None:
        self.payload = {
            "project_id": project_id,
            "candidates_found": 2,
            "screened_out": 1,
            "included": 1,
            "citations": 1,
            "authors": 0,
            "concepts": 0,
            "pdfs_acquired": 0,
            "pdfs_failed": 0,
            "pdfs_skipped": 0,
            "warnings": [],
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:00:01+00:00",
        }

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return dict(self.payload)

    def dict(self, **_: Any) -> dict[str, Any]:
        return dict(self.payload)


class FakeRunCancelled(Exception):
    """Cancellation exception installed into the lazy research module in one test."""


class FakeRunner:
    """Configurable pipeline double used by all run lifecycle tests."""

    def __init__(
        self,
        *,
        gate: threading.Event | None = None,
        started: threading.Event | None = None,
        fail: Exception | None = None,
        cancelled_error: type[Exception] | None = None,
    ) -> None:
        self.gate = gate
        self.started = started
        self.fail = fail
        self.cancelled_error = cancelled_error
        self.requests: list[Any] = []

    def run(
        self,
        project_id: str,
        request: Any,
        *,
        progress: Callable[[Any], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> FakeReport:
        self.requests.append(request)
        if progress is not None:
            progress(FakeProgress("discover", "discovered works", {"candidates": 2}))
            progress(FakeProgress("screen", "screened works", {"included": 1}))

        if self.gate is not None:
            if self.started is not None:
                self.started.set()
            while not self.gate.is_set():
                if cancel is not None and cancel.is_set():
                    if self.cancelled_error is not None:
                        raise self.cancelled_error("run cancelled")
                    raise RuntimeError("cancel was requested")
                self.gate.wait(0.01)

        if self.fail is not None:
            raise self.fail
        return FakeReport(project_id)


def _runner_factory(runner: FakeRunner) -> Callable[..., FakeRunner]:
    """Accept any factory arguments so the test does not constrain app wiring."""

    def factory(*_: Any, **__: Any) -> FakeRunner:
        return runner

    return factory


def _make_pdf(text: str = "Stored text") -> bytes:
    """Create the smallest valid text PDF needed by the document endpoint test."""

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def graph() -> Iterator[InMemoryResearchGraph]:
    yield InMemoryResearchGraph()


def _settings(tmp_path: Path, *, seed_golden: bool = False) -> Settings:
    return Settings(store="memory", data_dir=tmp_path, seed_golden=seed_golden)


def _wait_for_status(client: TestClient, run_id: str, expected: str) -> dict[str, Any]:
    deadline = time.monotonic() + 3.0
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        latest = response.json()
        if latest["status"] == expected:
            return latest
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach {expected!r}: {latest}")


def _install_fake_research(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide only the lazy cancellation symbol; no real research package is needed."""

    module = types.ModuleType("portolan.research")
    module.RunCancelled = FakeRunCancelled  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "portolan.research", module)


def test_health_on_empty_memory_graph(graph: InMemoryResearchGraph, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings, graph=graph)) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "store": "memory", "projects": 0}


def test_demo_seed_has_19_works_and_51_citations(
    graph: InMemoryResearchGraph, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, seed_golden=True)
    with TestClient(create_app(settings, graph=graph)) as client:
        projects_response = client.get("/api/projects")
        assert projects_response.status_code == 200
        projects = projects_response.json()
        assert len(projects) == 1
        assert projects[0]["name"] == "Golden mini-graph (demo)"

        project_id = projects[0]["id"]
        health = client.get("/api/health").json()
        assert health == {"status": "ok", "store": "memory", "projects": 1}

        response = client.get(f"/api/projects/{project_id}/graph")
        assert response.status_code == 200
        body = response.json()
        work_ids = {node["id"] for node in body["nodes"] if node["kind"] == "work"}
        assert len(work_ids) == 19
        assert len(body["edges"]) == 51
        assert all(edge["kind"] == "cites" for edge in body["edges"])
        assert all(
            edge["source"] in work_ids and edge["target"] in work_ids for edge in body["edges"]
        )


def test_demo_seed_does_not_reseed_existing_projects(
    graph: InMemoryResearchGraph, tmp_path: Path
) -> None:
    existing = graph.create_project("Existing project")
    settings = _settings(tmp_path, seed_golden=True)
    with TestClient(create_app(settings, graph=graph)) as client:
        projects = client.get("/api/projects").json()
        assert [project["id"] for project in projects] == [existing.id]
        assert projects[0]["name"] == "Existing project"


def test_project_crud_validation_and_404s(graph: InMemoryResearchGraph, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings, graph=graph)) as client:
        invalid = client.post("/api/projects", json={"name": "   "})
        assert invalid.status_code == 422

        created = client.post(
            "/api/projects",
            json={"name": "  A project  ", "description": "A description"},
        )
        assert created.status_code == 201
        project = created.json()
        assert project["name"] == "A project"
        assert project["description"] == "A description"
        project_id = project["id"]

        listed = client.get("/api/projects")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == project_id

        detail = client.get(f"/api/projects/{project_id}")
        assert detail.status_code == 200
        assert detail.json()["project"]["id"] == project_id
        assert detail.json()["stats"] == {
            "works": 0,
            "citations": 0,
            "authors": 0,
            "concepts": 0,
            "documents": 0,
        }

        for path in (
            "/api/projects/missing",
            "/api/projects/missing/graph",
            "/api/projects/missing/search?q=anything",
            "/api/projects/missing/runs",
        ):
            assert client.get(path).status_code == 404
        assert client.delete("/api/projects/missing").status_code == 404

        assert client.delete(f"/api/projects/{project_id}").status_code == 204
        assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_project_graph_search_and_work_neighborhood(
    graph: InMemoryResearchGraph, tmp_path: Path
) -> None:
    project = graph.create_project("Research")
    primary = graph.upsert_work(
        WorkNode(
            id="doi:10.1234/primary",
            title="Graph retrieval methods",
            year=2024,
            abstract="A paper about graph search",
        )
    )
    cited = graph.upsert_work(WorkNode(id="doi:10.1234/cited", title="A cited work", year=2023))
    graph.include_work(Inclusion(project_id=project.id, work_id=primary.id, discovered_via="seed"))
    graph.include_work(Inclusion(project_id=project.id, work_id=cited.id, discovered_via="seed"))
    graph.add_citation(primary.id, cited.id)
    author = graph.upsert_author(AuthorNode(id="author:ada", name="Ada Lovelace"))
    concept = graph.upsert_concept(ConceptNode(id="graph", label="Graph"))
    graph.set_authors(primary.id, [(author.id, 1)])
    graph.set_concepts(primary.id, [(concept.id, 0.9)])

    with TestClient(create_app(_settings(tmp_path), graph=graph)) as client:
        search = client.get(
            f"/api/projects/{project.id}/search", params={"q": "graph", "limit": 20}
        )
        assert search.status_code == 200
        assert [item["id"] for item in search.json()] == [primary.id]

        graph_response = client.get(
            f"/api/projects/{project.id}/graph",
            params={"authors": "false", "concepts": "false"},
        )
        assert graph_response.status_code == 200
        body = graph_response.json()
        assert {node["kind"] for node in body["nodes"]} == {"work"}
        assert body["edges"] == [{"source": primary.id, "target": cited.id, "kind": "cites"}]

        work_response = client.get(f"/api/works/{primary.id}", params={"project": project.id})
        assert work_response.status_code == 200
        neighborhood = work_response.json()
        assert neighborhood["work"]["id"] == primary.id
        assert [item["id"] for item in neighborhood["cites"]] == [cited.id]
        assert neighborhood["cited_by"] == []
        assert neighborhood["authors"] == [{"author": author.model_dump(), "position": 1}]
        assert neighborhood["concepts"] == [{"concept": concept.model_dump(), "score": 0.9}]

        assert client.get("/api/works/missing", params={"project": project.id}).status_code == 404
        assert client.get("/api/works/missing").status_code == 404


def test_runs_succeed_with_progress_and_report(
    graph: InMemoryResearchGraph, tmp_path: Path
) -> None:
    project = graph.create_project("Runs")
    runner = FakeRunner()
    settings = _settings(tmp_path)
    with TestClient(
        create_app(settings, graph=graph, runner_factory=_runner_factory(runner))
    ) as client:
        created = client.post(
            f"/api/projects/{project.id}/runs",
            json={"query": "graph", "max_works": 2},
        )
        assert created.status_code == 202
        run_id = created.json()["id"]
        assert created.json()["status"] in {"queued", "running", "succeeded"}

        run = _wait_for_status(client, run_id, "succeeded")
        assert run["project_id"] == project.id
        assert run["report"]["project_id"] == project.id
        assert run["report"]["included"] == 1
        assert [event["stage"] for event in run["progress"]] == ["discover", "screen"]
        assert all("at" in event for event in run["progress"])
        assert run["error"] is None
        assert run["finished_at"] is not None

        listed = client.get(f"/api/projects/{project.id}/runs")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [run_id]
        assert client.get("/api/runs/missing").status_code == 404


def test_one_active_run_per_project_and_delete_conflict(
    graph: InMemoryResearchGraph, tmp_path: Path
) -> None:
    project = graph.create_project("Busy")
    gate = threading.Event()
    started = threading.Event()
    runner = FakeRunner(gate=gate, started=started)
    with TestClient(
        create_app(_settings(tmp_path), graph=graph, runner_factory=_runner_factory(runner))
    ) as client:
        assert client.post(f"/api/projects/{project.id}/runs", json={}).status_code == 422
        first = client.post(f"/api/projects/{project.id}/runs", json={"query": "graph"})
        assert first.status_code == 202
        first_id = first.json()["id"]
        assert started.wait(2.0)
        _wait_for_status(client, first_id, "running")

        second = client.post(f"/api/projects/{project.id}/runs", json={"query": "graph"})
        assert second.status_code == 409
        assert client.delete(f"/api/projects/{project.id}").status_code == 409

        gate.set()
        _wait_for_status(client, first_id, "succeeded")
        assert client.delete(f"/api/projects/{project.id}").status_code == 204


def test_run_cancel_becomes_cancelled(
    graph: InMemoryResearchGraph, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_research(monkeypatch)
    project = graph.create_project("Cancellable")
    gate = threading.Event()
    started = threading.Event()
    runner = FakeRunner(gate=gate, started=started, cancelled_error=FakeRunCancelled)
    with TestClient(
        create_app(_settings(tmp_path), graph=graph, runner_factory=_runner_factory(runner))
    ) as client:
        created = client.post(f"/api/projects/{project.id}/runs", json={"query": "graph"})
        assert created.status_code == 202
        run_id = created.json()["id"]
        assert started.wait(2.0)
        _wait_for_status(client, run_id, "running")

        cancelled = client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 202
        assert cancelled.json()["id"] == run_id
        run = _wait_for_status(client, run_id, "cancelled")
        assert run["error"] is None
        assert run["finished_at"] is not None


def test_failing_runner_is_reported_without_traceback(
    graph: InMemoryResearchGraph, tmp_path: Path
) -> None:
    project = graph.create_project("Failure")
    runner = FakeRunner(fail=RuntimeError("pipeline exploded"))
    with TestClient(
        create_app(_settings(tmp_path), graph=graph, runner_factory=_runner_factory(runner))
    ) as client:
        created = client.post(f"/api/projects/{project.id}/runs", json={"query": "graph"})
        assert created.status_code == 202
        run = _wait_for_status(client, created.json()["id"], "failed")
        assert "RuntimeError" in run["error"]
        assert "pipeline exploded" in run["error"]
        assert "Traceback" not in run["error"]


def test_document_pdf_and_text_endpoints(graph: InMemoryResearchGraph, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    pdf_bytes = _make_pdf()
    record = DocumentStore(settings.documents_dir).put_pdf(
        pdf_bytes, source_url="https://example.org/paper.pdf", source="test"
    )
    with TestClient(create_app(settings, graph=graph)) as client:
        pdf = client.get(f"/api/documents/{record.sha256}/pdf")
        assert pdf.status_code == 200
        assert pdf.headers["content-type"].startswith("application/pdf")
        assert pdf.headers["content-disposition"] == (f'inline; filename="{record.sha256}.pdf"')
        assert pdf.content == pdf_bytes

        text = client.get(f"/api/documents/{record.sha256}/text")
        assert text.status_code == 200
        assert text.headers["content-type"].startswith("text/plain; charset=utf-8")
        assert "=== page 1 ===" in text.text
        assert "Stored text" in text.text

        malformed = client.get("/api/documents/not-a-sha/pdf")
        assert 400 <= malformed.status_code < 500


def test_document_outline_locate_and_pages_endpoints(
    graph: InMemoryResearchGraph, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    record = DocumentStore(settings.documents_dir).put_pdf(
        _make_pdf(), source_url="https://example.org/paper.pdf", source="test"
    )
    base = f"/api/documents/{record.sha256}"
    with TestClient(create_app(settings, graph=graph)) as client:
        outline = client.get(f"{base}/outline")
        assert outline.status_code == 200
        assert outline.json()["source"] in {"pdf_outline", "headings", "none"}

        located = client.get(f"{base}/locate", params={"q": "stored TEXT", "page": 1})
        assert located.status_code == 200
        body = located.json()
        assert body["found"] is True
        assert body["hits"][0]["page"] == 1
        assert "Stored text" in body["hits"][0]["snippet"]

        missing = client.get(f"{base}/locate", params={"q": "not in this paper"})
        assert missing.json() == {"found": False, "hits": []}

        pages = client.get(f"{base}/pages", params={"start": 1, "end": 1})
        assert pages.status_code == 200
        assert pages.text.startswith("=== page 1 ===")

        assert client.get(f"{base}/pages", params={"start": 2, "end": 1}).status_code == 422
        assert client.get(f"{base}/pages", params={"start": 1, "end": 11}).status_code == 422
        assert client.get(f"{base}/pages", params={"start": 5, "end": 5}).status_code == 422


def test_settings_from_env_and_log_safe_description(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (
        "PORTOLAN_STORE",
        "PORTOLAN_DATA_DIR",
        "PORTOLAN_REPO_ROOT",
        "OPENALEX_API_KEY",
        "SEMANTIC_SCHOLAR_API_KEY",
        "PORTOLAN_CONTACT_EMAIL",
        "PORTOLAN_MAX_CONCURRENT_RUNS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PORTOLAN_STORE", "memory")
    monkeypatch.setenv("PORTOLAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENALEX_API_KEY", "openalex-secret")
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "semantic-secret")
    monkeypatch.setenv("PORTOLAN_CONTACT_EMAIL", "research@example.org")
    monkeypatch.setenv("PORTOLAN_MAX_CONCURRENT_RUNS", "3")

    settings = Settings.from_env()
    assert settings.store == "memory"
    assert settings.data_dir == tmp_path
    assert settings.openalex_api_key == "openalex-secret"
    assert settings.semantic_scholar_api_key == "semantic-secret"
    assert settings.contact_email == "research@example.org"
    assert settings.max_concurrent_runs == 3
    description = settings.describe()
    assert "openalex_key=set" in description
    assert "openalex-secret" not in description
    assert "semantic-secret" not in description
    assert "research@example.org" not in description


def test_settings_data_dir_defaults_to_repo_data(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PORTOLAN_STORE", "PORTOLAN_DATA_DIR", "PORTOLAN_REPO_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PORTOLAN_STORE", "memory")
    settings = Settings.from_env()
    assert settings.data_dir == settings.repo_root / "data"


def test_settings_reject_unknown_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTOLAN_STORE", "oxigraph")
    with pytest.raises(ValueError, match="PORTOLAN_STORE"):
        Settings.from_env()


def test_neo4j_store_requires_uri() -> None:
    with pytest.raises(ValueError, match="NEO4J_URI"):
        make_graph(Settings(store="neo4j"))

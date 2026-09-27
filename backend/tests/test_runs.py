"""Durable research run history and incremental run reports."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from portolan.api.app import create_app
from portolan.api.runs import INTERRUPTED_ERROR, MAX_RUNS_PER_PROJECT
from portolan.graph import Inclusion, WorkNode
from portolan.graph.memory import InMemoryResearchGraph
from portolan.settings import Settings


class AddingRunner:
    """Fake pipeline that includes the given works into the project."""

    def __init__(
        self,
        graph: InMemoryResearchGraph,
        work_ids: Sequence[str] = (),
        *,
        gate: threading.Event | None = None,
        started: threading.Event | None = None,
    ) -> None:
        self.graph = graph
        self.work_ids = list(work_ids)
        self.gate = gate
        self.started = started

    def run(
        self,
        project_id: str,
        request: Any,
        *,
        progress: Callable[[Any], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> dict[str, Any]:
        if progress is not None:
            progress({"stage": "discover", "message": "found", "counts": {"candidates": 1}})
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            while not self.gate.is_set():
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("cancel was requested")
                self.gate.wait(0.01)
        for work_id in self.work_ids:
            self.graph.upsert_work(WorkNode(id=work_id, title=work_id, year=2024))
            self.graph.include_work(
                Inclusion(project_id=project_id, work_id=work_id, discovered_via="search")
            )
        return {"included": len(self.work_ids), "warnings": []}


def _factory(runner: AddingRunner) -> Callable[..., AddingRunner]:
    def factory(*_: Any, **__: Any) -> AddingRunner:
        return runner

    return factory


@pytest.fixture
def graph() -> Iterator[InMemoryResearchGraph]:
    yield InMemoryResearchGraph()


def _settings(tmp_path: Path) -> Settings:
    return Settings(store="memory", data_dir=tmp_path)


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


def _include(graph: InMemoryResearchGraph, project_id: str, *work_ids: str) -> None:
    for work_id in work_ids:
        graph.upsert_work(WorkNode(id=work_id, title=work_id, year=2020))
        graph.include_work(Inclusion(project_id=project_id, work_id=work_id, discovered_via="seed"))


def _write_run(
    runs_dir: Path, project_id: str, *, status: str, created_at: datetime
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "id": run_id,
        "project_id": project_id,
        "status": status,
        "request": {"query": "graph"},
        "progress": [],
        "report": None,
        "error": None,
        "created_at": created_at.isoformat(),
        "started_at": created_at.isoformat() if status != "queued" else None,
        "finished_at": None if status in {"queued", "running"} else created_at.isoformat(),
    }
    directory = runs_dir / project_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_settings_runs_dir_is_under_data_dir(tmp_path: Path) -> None:
    assert _settings(tmp_path).runs_dir == tmp_path / "runs"


def test_runs_survive_an_app_restart(graph: InMemoryResearchGraph, tmp_path: Path) -> None:
    project = graph.create_project("Durable")
    runner = AddingRunner(graph, ["W1"])
    settings = _settings(tmp_path)
    with TestClient(create_app(settings, graph=graph, runner_factory=_factory(runner))) as client:
        created = client.post(f"/api/projects/{project.id}/runs", json={"query": "graph"})
        assert created.status_code == 202
        run_id = created.json()["id"]
        first = _wait_for_status(client, run_id, "succeeded")

    path = settings.runs_dir / project.id / f"{run_id}.json"
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "succeeded"

    with TestClient(create_app(settings, graph=graph, runner_factory=_factory(runner))) as client:
        listed = client.get(f"/api/projects/{project.id}/runs")
        assert listed.status_code == 200
        assert [run["id"] for run in listed.json()] == [run_id]
        restored = client.get(f"/api/runs/{run_id}").json()
        assert restored == first

        second = client.post(f"/api/projects/{project.id}/runs", json={"query": "more"})
        assert second.status_code == 202
        _wait_for_status(client, second.json()["id"], "succeeded")
        ids = [run["id"] for run in client.get(f"/api/projects/{project.id}/runs").json()]
        assert ids == [second.json()["id"], run_id]


def test_running_run_is_on_disk_and_interrupted_runs_fail_on_load(
    graph: InMemoryResearchGraph, tmp_path: Path
) -> None:
    project = graph.create_project("Interrupted")
    settings = _settings(tmp_path)
    gate = threading.Event()
    started = threading.Event()
    runner = AddingRunner(graph, gate=gate, started=started)
    with TestClient(create_app(settings, graph=graph, runner_factory=_factory(runner))) as client:
        run_id = client.post(f"/api/projects/{project.id}/runs", json={"query": "graph"}).json()[
            "id"
        ]
        assert started.wait(2.0)
        on_disk = json.loads(
            (settings.runs_dir / project.id / f"{run_id}.json").read_text(encoding="utf-8")
        )
        assert on_disk["status"] == "running"
        gate.set()
        _wait_for_status(client, run_id, "succeeded")

    now = datetime.now(UTC)
    running = _write_run(settings.runs_dir, project.id, status="running", created_at=now)
    queued = _write_run(
        settings.runs_dir, project.id, status="queued", created_at=now + timedelta(seconds=1)
    )

    with TestClient(create_app(settings, graph=graph)) as client:
        for stale in (running, queued):
            run = client.get(f"/api/runs/{stale['id']}").json()
            assert run["status"] == "failed"
            assert run["error"] == INTERRUPTED_ERROR
            assert run["finished_at"] is not None
            persisted = json.loads(
                (settings.runs_dir / project.id / f"{stale['id']}.json").read_text(encoding="utf-8")
            )
            assert persisted["status"] == "failed"
            assert persisted["error"] == INTERRUPTED_ERROR
        listed = [run["id"] for run in client.get(f"/api/projects/{project.id}/runs").json()]
        assert listed == [queued["id"], running["id"], run_id]
        # A restored interrupted run does not block a new one.
        assert (
            client.post(f"/api/projects/{project.id}/runs", json={"query": "again"}).status_code
            == 202
        )


def test_corrupt_and_foreign_files_are_ignored(
    graph: InMemoryResearchGraph, tmp_path: Path
) -> None:
    project = graph.create_project("Robust")
    settings = _settings(tmp_path)
    directory = settings.runs_dir / project.id
    directory.mkdir(parents=True)
    (directory / f"{uuid.uuid4().hex}.json").write_text("{not json", encoding="utf-8")
    (directory / "notes.json").write_text("{}", encoding="utf-8")
    good = _write_run(
        settings.runs_dir, project.id, status="succeeded", created_at=datetime.now(UTC)
    )
    with TestClient(create_app(settings, graph=graph)) as client:
        listed = client.get(f"/api/projects/{project.id}/runs").json()
    assert [run["id"] for run in listed] == [good["id"]]


def test_retention_keeps_newest_runs_per_project(
    graph: InMemoryResearchGraph, tmp_path: Path
) -> None:
    project = graph.create_project("Busy history")
    settings = _settings(tmp_path)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    written = [
        _write_run(
            settings.runs_dir, project.id, status="succeeded", created_at=base + timedelta(hours=i)
        )
        for i in range(MAX_RUNS_PER_PROJECT + 5)
    ]
    newest_first = [run["id"] for run in reversed(written)]
    directory = settings.runs_dir / project.id

    runner = AddingRunner(graph)
    with TestClient(create_app(settings, graph=graph, runner_factory=_factory(runner))) as client:
        listed = [run["id"] for run in client.get(f"/api/projects/{project.id}/runs").json()]
        assert listed == newest_first[:MAX_RUNS_PER_PROJECT]
        assert {path.stem for path in directory.glob("*.json")} == set(listed)

        created = client.post(f"/api/projects/{project.id}/runs", json={"query": "graph"})
        assert created.status_code == 202
        _wait_for_status(client, created.json()["id"], "succeeded")
        listed = [run["id"] for run in client.get(f"/api/projects/{project.id}/runs").json()]
        assert listed == [created.json()["id"], *newest_first[: MAX_RUNS_PER_PROJECT - 1]]
        assert len(list(directory.glob("*.json"))) == MAX_RUNS_PER_PROJECT


def test_report_counts_works_added_by_the_run(graph: InMemoryResearchGraph, tmp_path: Path) -> None:
    project = graph.create_project("Incremental")
    _include(graph, project.id, "W1", "W2")
    # W2 is re-found by the run and must not count as new.
    runner = AddingRunner(graph, ["W2", "W4", "W3"])
    with TestClient(
        create_app(_settings(tmp_path), graph=graph, runner_factory=_factory(runner))
    ) as client:
        run_id = client.post(f"/api/projects/{project.id}/runs", json={"query": "graph"}).json()[
            "id"
        ]
        report = _wait_for_status(client, run_id, "succeeded")["report"]
    assert report["included"] == 3
    assert report["works_before"] == 2
    assert report["works_after"] == 4
    assert report["works_added"] == 2
    assert report["added_work_ids"] == ["W3", "W4"]


def test_added_work_ids_are_capped(graph: InMemoryResearchGraph, tmp_path: Path) -> None:
    project = graph.create_project("Large")
    runner = AddingRunner(graph, [f"W{index:03d}" for index in range(250)])
    with TestClient(
        create_app(_settings(tmp_path), graph=graph, runner_factory=_factory(runner))
    ) as client:
        run_id = client.post(f"/api/projects/{project.id}/runs", json={"query": "graph"}).json()[
            "id"
        ]
        report = _wait_for_status(client, run_id, "succeeded")["report"]
    assert report["works_before"] == 0
    assert report["works_after"] == 250
    assert report["works_added"] == 250
    assert len(report["added_work_ids"]) == 200


def test_project_delete_removes_run_files(graph: InMemoryResearchGraph, tmp_path: Path) -> None:
    doomed = graph.create_project("Doomed")
    kept = graph.create_project("Kept")
    settings = _settings(tmp_path)
    runner = AddingRunner(graph)
    with TestClient(create_app(settings, graph=graph, runner_factory=_factory(runner))) as client:
        run_ids = {}
        for project in (doomed, kept):
            run_id = client.post(
                f"/api/projects/{project.id}/runs", json={"query": "graph"}
            ).json()["id"]
            _wait_for_status(client, run_id, "succeeded")
            run_ids[project.id] = run_id
        assert (settings.runs_dir / doomed.id).is_dir()

        assert client.delete(f"/api/projects/{doomed.id}").status_code == 204
        assert not (settings.runs_dir / doomed.id).exists()
        assert client.get(f"/api/runs/{run_ids[doomed.id]}").status_code == 404
        assert (settings.runs_dir / kept.id / f"{run_ids[kept.id]}.json").is_file()


def test_progress_writes_are_throttled(
    graph: InMemoryResearchGraph, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from portolan.api import runs as runs_module

    writes: list[str] = []
    real_write = runs_module._write_atomic

    def counting_write(path: Path, run: Any) -> None:
        writes.append(run.status)
        real_write(path, run)

    monkeypatch.setattr(runs_module, "_write_atomic", counting_write)
    project = graph.create_project("Chatty")

    class ChattyRunner(AddingRunner):
        def run(self, project_id: str, request: Any, **kwargs: Any) -> dict[str, Any]:
            progress = kwargs["progress"]
            for index in range(20):
                progress({"stage": "screen", "message": str(index), "counts": {}})
            return {"included": 0, "warnings": []}

    runner = ChattyRunner(graph)
    with TestClient(
        create_app(_settings(tmp_path), graph=graph, runner_factory=_factory(runner))
    ) as client:
        run_id = client.post(f"/api/projects/{project.id}/runs", json={"query": "graph"}).json()[
            "id"
        ]
        run = _wait_for_status(client, run_id, "succeeded")
    assert len(run["progress"]) == 20
    # queued + running + final; the 20 fast progress events add no writes.
    assert writes == ["queued", "running", "succeeded"]
    persisted = json.loads(
        (tmp_path / "runs" / project.id / f"{run_id}.json").read_text(encoding="utf-8")
    )
    assert len(persisted["progress"]) == 20

"""CLI tests that keep the optional research pipeline out of test imports."""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import portolan.cli as cli_module
from portolan.graph.memory import InMemoryResearchGraph


class FakeResearchRequest:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


class FakeRunProgress:
    def __init__(self, stage: str, message: str, counts: dict[str, int]) -> None:
        self.stage = stage
        self.message = message
        self.counts = counts


class FakeRunReport:
    def __init__(self, project_id: str) -> None:
        self.payload = {
            "project_id": project_id,
            "candidates_found": 1,
            "screened_out": 0,
            "included": 1,
            "citations": 0,
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


class FakeSources:
    instances: list[FakeSources] = []

    def __init__(self) -> None:
        self.closed = False
        self.__class__.instances.append(self)

    @classmethod
    def default(cls, _cache_dir: Path, **_: Any) -> FakeSources:
        return cls()

    def close(self) -> None:
        self.closed = True


class FakeResearchRunner:
    instances: list[FakeResearchRunner] = []

    def __init__(self, *_: Any, **__: Any) -> None:
        self.__class__.instances.append(self)
        self.requests: list[Any] = []

    def run(
        self,
        project_id: str,
        request: Any,
        *,
        progress: Callable[[Any], None] | None = None,
        cancel: Any = None,
    ) -> FakeRunReport:
        del cancel
        self.requests.append(request)
        if progress is not None:
            progress(FakeRunProgress("discover", "found seed", {"candidates": 1}))
            progress(FakeRunProgress("complete", "finished", {"included": 1}))
        return FakeRunReport(project_id)


def _install_fake_research(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("portolan.research")
    module.ResearchRequest = FakeResearchRequest  # type: ignore[attr-defined]
    module.RunProgress = FakeRunProgress  # type: ignore[attr-defined]
    module.RunReport = FakeRunReport  # type: ignore[attr-defined]
    module.RunCancelled = RuntimeError  # type: ignore[attr-defined]
    module.ResearchSources = FakeSources  # type: ignore[attr-defined]
    module.ResearchRunner = FakeResearchRunner  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "portolan.research", module)


def _patch_graph(monkeypatch: pytest.MonkeyPatch, graph: InMemoryResearchGraph) -> None:
    monkeypatch.setattr(cli_module, "make_graph", lambda _settings: graph, raising=False)


def test_project_create_list_and_delete(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PORTOLAN_STORE", "memory")
    monkeypatch.setenv("PORTOLAN_DATA_DIR", str(tmp_path))
    graph = InMemoryResearchGraph()
    _patch_graph(monkeypatch, graph)
    runner = CliRunner()

    created = runner.invoke(
        cli_module.app,
        ["project", "create", "Demo project", "--description", "A CLI project"],
    )
    assert created.exit_code == 0, created.output
    project = graph.list_projects()[0]
    assert project.name == "Demo project"
    assert project.description == "A CLI project"
    assert project.id in created.output

    listed = runner.invoke(cli_module.app, ["project", "list"])
    assert listed.exit_code == 0, listed.output
    assert "Demo project" in listed.output
    assert project.id in listed.output

    deleted = runner.invoke(cli_module.app, ["project", "delete", project.id])
    assert deleted.exit_code == 0, deleted.output
    assert graph.list_projects() == []


def test_research_command_uses_lazy_monkeypatched_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PORTOLAN_STORE", "memory")
    monkeypatch.setenv("PORTOLAN_DATA_DIR", str(tmp_path))
    graph = InMemoryResearchGraph()
    project = graph.create_project("CLI research")
    _patch_graph(monkeypatch, graph)
    _install_fake_research(monkeypatch)
    FakeResearchRunner.instances.clear()
    FakeSources.instances.clear()

    result = CliRunner().invoke(
        cli_module.app,
        [
            "research",
            project.id,
            "--seed",
            "alpha",
            "--seed",
            "beta",
            "--query",
            "graph",
            "--max-works",
            "3",
            "--depth",
            "2",
            "--no-pdfs",
            "--max-pdfs",
            "4",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "discover" in result.output.lower()
    assert "candidates" in result.output.lower()

    assert len(FakeResearchRunner.instances) == 1
    request = FakeResearchRunner.instances[0].requests[0]
    assert request.seeds == ["alpha", "beta"]
    assert request.query == "graph"
    assert request.max_works == 3
    assert request.snowball_depth == 2
    assert request.acquire_pdfs is False
    assert request.max_pdfs == 4
    assert FakeSources.instances and FakeSources.instances[0].closed is True

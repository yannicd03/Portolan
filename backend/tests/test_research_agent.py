"""Offline end-to-end tests for the human-guided Research agent."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Thread
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import Field

from portolan.agent.ask import AgentEvent, AgentLimitReached
from portolan.agent.research import ResearchAgent, ResearchPlanExpired, _research_checkpointer
from portolan.api.runs import RunRegistry
from portolan.documents.store import DocumentStore
from portolan.graph.memory import InMemoryResearchGraph
from portolan.research import ResearchSources, RunCancelled, RunProgress
from portolan.settings import Settings


class ScriptedModel(FakeMessagesListChatModel):
    """A deterministic model that records each prompt sent by Deep Agents."""

    model_name: str = "scripted"
    seen: list[list[Any]] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
        return self

    def _generate(self, messages: list[Any], **kwargs: Any) -> Any:
        self.seen.append(messages)
        return super()._generate(messages, **kwargs)


def _call(name: str, args: dict[str, Any], number: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{number}"}])


def _record(identifier: str, title: str, year: int = 2024) -> dict[str, Any]:
    return {
        "source": "openalex",
        "openalex_id": identifier,
        "identifiers": {"openalex": identifier, "doi": f"10.1234/{identifier.casefold()}"},
        "title": title,
        "year": year,
        "cited_by_count": 12,
    }


class FakeOpenAlex:
    """Small source fake for preview and identifier lookup tools."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.search_calls: list[tuple[str, int, int | None, int | None]] = []
        self.lookup_calls: list[str] = []

    def search(
        self,
        query: str,
        *,
        limit: int = 25,
        from_year: int | None = None,
        to_year: int | None = None,
    ) -> list[dict[str, Any]]:
        self.search_calls.append((query, limit, from_year, to_year))
        return [record.copy() for record in self.records[:limit]]

    def lookup(self, identifier: str) -> dict[str, Any] | None:
        self.lookup_calls.append(identifier)
        wanted = identifier.rsplit("/", 1)[-1].casefold()
        for record in self.records:
            identifiers = record.get("identifiers", {})
            values = {
                str(record.get("openalex_id", "")).casefold(),
                str(identifiers.get("openalex", "")).casefold(),
                str(identifiers.get("doi", "")).casefold(),
            }
            if wanted in values:
                return record.copy()
        return None

    def lookup_many(self, identifiers: list[str]) -> list[dict[str, Any]]:
        return [record for identifier in identifiers if (record := self.lookup(identifier))]

    def close(self) -> None:
        pass


@dataclass
class RunnerControl:
    requests: list[Any] = field(default_factory=list)
    runners: list[FakeRunner] = field(default_factory=list)
    started: Event = field(default_factory=Event)
    cancelled: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)
    block: bool = False


class FakeRunner:
    def __init__(self, control: RunnerControl) -> None:
        self.control = control

    def run(
        self,
        project_id: str,
        request: Any,
        *,
        progress: Any = None,
        cancel: Event | None = None,
    ) -> dict[str, Any]:
        self.control.requests.append(request)
        self.control.started.set()
        if progress is not None:
            progress(
                RunProgress(
                    stage="search", message="found preview matches", counts={"candidates": 2}
                )
            )
        if self.control.block:
            while not self.control.release.wait(0.01):
                if cancel is not None and cancel.is_set():
                    self.control.cancelled.set()
                    raise RunCancelled()
        if progress is not None:
            progress(RunProgress(stage="done", message="harvest complete", counts={"included": 2}))
        return {
            "project_id": project_id,
            "candidates_found": 2,
            "excluded": 0,
            "screened_out": 0,
            "included": 2,
            "citations": 1,
            "authors": 2,
            "concepts": 1,
            "pdfs_acquired": 0,
            "pdfs_failed": 0,
            "pdfs_skipped": 0,
            "warnings": [],
        }


@dataclass
class Harness:
    agent: ResearchAgent
    graph: InMemoryResearchGraph
    runs: RunRegistry
    control: RunnerControl
    model: ScriptedModel
    project_id: str
    openalex: FakeOpenAlex

    def close(self) -> None:
        self.runs.shutdown()


def _harness(
    tmp_path: Path,
    responses: list[AIMessage],
    *,
    max_tool_calls: int = 12,
    block: bool = False,
    checkpointer: Any | None = None,
) -> Harness:
    graph = InMemoryResearchGraph()
    project = graph.create_project("Research agent test")
    documents = DocumentStore(tmp_path / "documents")
    settings = Settings(
        store="memory",
        data_dir=tmp_path,
        openrouter_api_key="test-key",
        chat_max_tool_calls=max_tool_calls,
    )
    model = ScriptedModel(responses=responses)
    openalex = FakeOpenAlex([_record("W1", "Graph retrieval"), _record("W2", "Citation maps")])
    sources = ResearchSources(openalex)
    control = RunnerControl(block=block)

    def runner_factory(*args: Any, **kwargs: Any) -> FakeRunner:
        runner = FakeRunner(control)
        control.runners.append(runner)
        return runner

    runs = RunRegistry(graph, settings, runner_factory)
    agent = ResearchAgent(
        graph,
        documents,
        settings,
        model=model,
        runs=runs,
        sources_factory=lambda *args, **kwargs: sources,
        runner_factory=runner_factory,
        checkpointer=checkpointer,
    )
    agent.bind_project(project.id)
    return Harness(agent, graph, runs, control, model, project.id, openalex)


def _sqlite_checkpointer(path: Path) -> SqliteSaver:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return SqliteSaver(connection)


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _tool_names(model: ScriptedModel) -> list[str]:
    names: list[str] = []
    seen_ids: set[str] = set()
    for batch in model.seen:
        for message in batch:
            for call in getattr(message, "tool_calls", []) or []:
                call_id = str(call.get("id", ""))
                if call_id and call_id in seen_ids:
                    continue
                if call_id:
                    seen_ids.add(call_id)
                names.append(call.get("name", ""))
    return names


def _args(query: str = "graph retrieval") -> dict[str, Any]:
    return {
        "query": query,
        "seeds": [],
        "from_year": 2020,
        "to_year": 2025,
        "max_works": 10,
        "snowball_depth": 1,
        "acquire_pdfs": False,
        "rationale": "Start with the central literature and follow one citation hop.",
    }


def test_research_checkpointer_is_shared_and_configures_sqlite(tmp_path: Path) -> None:
    first_settings = Settings(store="memory", data_dir=tmp_path / "data")
    same_path_settings = Settings(store="memory", data_dir=tmp_path / "unused" / ".." / "data")

    first = _research_checkpointer(first_settings)
    second = _research_checkpointer(same_path_settings)

    assert first is second
    assert first_settings.research_checkpoints_path.is_file()
    assert first.conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert first.conn.execute("PRAGMA busy_timeout").fetchone() == (5000,)


def test_research_plan_pauses_and_approve_runs_harvest(tmp_path: Path) -> None:
    planned_args = _args()
    harness = _harness(
        tmp_path,
        [
            _call("preview_search", {"query": "graph retrieval", "from_year": 2020, "limit": 5}, 1),
            _call("run_research", planned_args, 2),
            _call("map_summary", {}, 3),
            AIMessage(content="The harvest found two works. The map has a small citation path."),
        ],
    )
    try:
        events: list[AgentEvent] = []
        turn = harness.agent.run(
            "Map the graph retrieval literature",
            [],
            events.append,
            cancel=None,
            thread_id="research-approve",
        )
        assert _value(turn, "status") == "awaiting_approval"
        plan = _value(turn, "plan")
        assert _value(plan, "tool_call_id") == "call-2"
        assert _value(plan, "args") == planned_args
        assert _value(plan, "description")
        assert harness.openalex.search_calls == [("graph retrieval", 5, 2020, None)]

        final = harness.agent.resume(
            "research-approve",
            "approve",
            edited_args=None,
            message=None,
            on_event=events.append,
            cancel=None,
        )
        assert _value(final, "status") == "answered"
        assert "two works" in _value(final, "answer_markdown")
        assert len(harness.control.requests) == 1
        assert harness.control.requests[0].query == "graph retrieval"
        assert any(event.text.startswith("Harvest:") for event in events)
        assert _tool_names(harness.model) == ["preview_search", "run_research", "map_summary"]
    finally:
        harness.close()


def test_research_resume_edit_uses_edited_arguments(tmp_path: Path) -> None:
    harness = _harness(
        tmp_path,
        [
            _call("preview_search", {"query": "initial query", "limit": 3}, 1),
            _call("run_research", _args("initial query"), 2),
            _call("map_summary", {}, 3),
            AIMessage(content="The edited harvest is complete."),
        ],
    )
    edited = {"query": "narrow citation graph query"}
    try:
        pending = harness.agent.run(
            "Map the literature",
            [],
            lambda event: None,
            cancel=None,
            thread_id="research-edit",
        )
        assert _value(pending, "status") == "awaiting_approval"
        final = harness.agent.resume(
            "research-edit",
            "edit",
            edited_args=edited,
            message=None,
            on_event=lambda event: None,
            cancel=None,
        )
        assert _value(final, "status") == "answered"
        assert len(harness.control.requests) == 1
        assert harness.control.requests[0].query == "narrow citation graph query"
        assert harness.control.requests[0].max_works == 10
    finally:
        harness.close()


def test_research_reject_does_not_submit_a_run(tmp_path: Path) -> None:
    harness = _harness(
        tmp_path,
        [
            _call("preview_search", {"query": "graph retrieval", "limit": 3}, 1),
            _call("run_research", _args(), 2),
            AIMessage(content="I did not start a harvest because the plan was rejected."),
        ],
    )
    try:
        pending = harness.agent.run(
            "Map the literature",
            [],
            lambda event: None,
            cancel=None,
            thread_id="research-reject",
        )
        assert _value(pending, "status") == "awaiting_approval"
        final = harness.agent.resume(
            "research-reject",
            "reject",
            edited_args=None,
            message="Use a narrower field first.",
            on_event=lambda event: None,
            cancel=None,
        )
        assert _value(final, "status") == "answered"
        assert "did not start" in _value(final, "answer_markdown")
        assert harness.control.requests == []
        assert _tool_names(harness.model) == ["preview_search", "run_research"]
    finally:
        harness.close()


def test_research_plan_resumes_from_sqlite_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "injected.sqlite3"
    first_saver = _sqlite_checkpointer(database)
    harness: Harness | None = None
    restarted_saver: SqliteSaver | None = None
    restarted_runs: RunRegistry | None = None
    try:
        harness = _harness(
            tmp_path,
            [
                _call("preview_search", {"query": "graph retrieval", "limit": 3}, 1),
                _call("run_research", _args(), 2),
            ],
            checkpointer=first_saver,
        )
        assert harness.agent.checkpointer is first_saver
        assert not (tmp_path / "checkpoints" / "research.sqlite3").exists()
        pending = harness.agent.run(
            "Map the literature",
            [],
            lambda event: None,
            cancel=None,
            thread_id="research-restart",
        )
        assert _value(pending, "status") == "awaiting_approval"
        first_saver.conn.close()

        restarted_saver = _sqlite_checkpointer(database)
        settings = Settings(
            store="memory",
            data_dir=tmp_path,
            openrouter_api_key="test-key",
            chat_max_tool_calls=12,
        )
        restart_model = ScriptedModel(
            responses=[
                AIMessage(content="I did not start a harvest because the plan was rejected.")
            ]
        )

        def runner_factory(*args: Any, **kwargs: Any) -> FakeRunner:
            return FakeRunner(harness.control)

        restarted_runs = RunRegistry(harness.graph, settings, runner_factory)
        restarted_agent = ResearchAgent(
            harness.graph,
            DocumentStore(tmp_path / "documents"),
            settings,
            model=restart_model,
            runs=restarted_runs,
            sources_factory=lambda *args, **kwargs: ResearchSources(harness.openalex),
            runner_factory=runner_factory,
            checkpointer=restarted_saver,
            project_id=harness.project_id,
        )

        with pytest.raises(ResearchPlanExpired, match="plan expired"):
            restarted_agent.resume(
                "missing-research-thread",
                "approve",
                edited_args=None,
                message=None,
                on_event=lambda event: None,
                cancel=None,
            )

        final = restarted_agent.resume(
            "research-restart",
            "reject",
            edited_args=None,
            message="Narrow the field first.",
            on_event=lambda event: None,
            cancel=None,
        )
        assert _value(final, "status") == "answered"
        assert "did not start a harvest" in _value(final, "answer_markdown")
        assert harness.control.requests == []
    finally:
        first_saver.conn.close()
        if restarted_runs is not None:
            restarted_runs.shutdown()
        if restarted_saver is not None:
            restarted_saver.conn.close()
        if harness is not None:
            harness.close()


def test_research_tool_call_budget_is_enforced(tmp_path: Path) -> None:
    harness = _harness(
        tmp_path,
        [
            _call("preview_search", {"query": "first", "limit": 2}, 1),
            _call("preview_search", {"query": "second", "limit": 2}, 2),
        ],
        max_tool_calls=1,
    )
    try:
        with pytest.raises(AgentLimitReached):
            harness.agent.run(
                "Calibrate a search",
                [],
                lambda event: None,
                cancel=None,
                thread_id="research-budget",
            )
        assert len(harness.openalex.search_calls) == 1
    finally:
        harness.close()


def test_cancel_during_harvest_cancels_registry_run(tmp_path: Path) -> None:
    harness = _harness(
        tmp_path,
        [
            _call("preview_search", {"query": "graph retrieval", "limit": 3}, 1),
            _call("run_research", _args(), 2),
            _call("map_summary", {}, 3),
            AIMessage(content="The harvest completed."),
        ],
        block=True,
    )
    cancel = Event()
    try:
        pending = harness.agent.run(
            "Map the literature",
            [],
            lambda event: None,
            cancel=None,
            thread_id="research-cancel",
        )
        assert _value(pending, "status") == "awaiting_approval"

        result: list[Any] = []

        def resume() -> None:
            try:
                result.append(
                    harness.agent.resume(
                        "research-cancel",
                        "approve",
                        edited_args=None,
                        message=None,
                        on_event=lambda event: None,
                        cancel=cancel,
                    )
                )
            except BaseException as error:  # cancellation may surface as InterruptedError
                result.append(error)

        worker = Thread(target=resume, daemon=True)
        worker.start()
        assert harness.control.started.wait(2)
        cancel.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert harness.control.cancelled.wait(2)
        run = harness.runs.list_for_project(harness.project_id)[0]
        assert run.status == "cancelled"
    finally:
        cancel.set()
        harness.control.release.set()
        harness.close()

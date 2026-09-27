"""Offline tests for the chat persistence and streaming API."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from portolan.agent.ask import AgentEvent
from portolan.agent.citations import VerifiedAnswer
from portolan.api.chat import build_chat_router
from portolan.documents.store import DocumentStore
from portolan.graph.memory import InMemoryResearchGraph
from portolan.settings import Settings


def _app(
    tmp_path: Path,
    graph: InMemoryResearchGraph,
    agent_factory: Any,
    *,
    key: str | None = "test-key",
    research_agent_factory: Any = None,
) -> FastAPI:
    settings = Settings(store="memory", data_dir=tmp_path, openrouter_api_key=key)
    app = FastAPI()
    app.include_router(
        build_chat_router(
            lambda: graph,
            lambda: DocumentStore(settings.documents_dir),
            settings,
            agent_factory=agent_factory,
            research_agent_factory=research_agent_factory,
        )
    )
    return app


class FakeAgent:
    def __init__(self, *, started: threading.Event | None = None) -> None:
        self.started = started

    def bind_project(self, project_id: str) -> None:
        self.project_id = project_id

    def run(self, question: str, history: list[Any], on_event: Any, cancel: Any = None) -> Any:
        if self.started is not None:
            self.started.set()
        on_event(AgentEvent(text=f"Searching papers: {question}", tool="search_papers"))
        return VerifiedAnswer(
            answer_markdown="The answer is grounded.[1]",
            citations=[],
            unsupported=["The answer is grounded."],
            model="fake",
            tool_calls=1,
        )


def _answer_factory(graph: Any, documents: Any, settings: Any) -> FakeAgent:
    return FakeAgent()


def test_chat_thread_crud_and_path_validation(tmp_path: Path) -> None:
    graph = InMemoryResearchGraph()
    project = graph.create_project("Chat project")
    with TestClient(_app(tmp_path, graph, _answer_factory)) as client:
        assert client.get(f"/api/projects/{project.id}/chats").json() == []
        created = client.post(
            f"/api/projects/{project.id}/chats",
            json={"title": "  Literature review  "},
        )
        assert created.status_code == 201
        thread = created.json()
        thread_id = thread["id"]
        assert thread["title"] == "Literature review"

        fetched = client.get(f"/api/chats/{thread_id}", params={"project": project.id})
        assert fetched.status_code == 200
        assert fetched.json()["id"] == thread_id
        assert fetched.json()["messages"] == []

        listed = client.get(f"/api/projects/{project.id}/chats")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == thread_id
        assert listed.json()[0]["message_count"] == 0

        assert (
            client.get("/api/chats/not-a-valid-thread", params={"project": project.id}).status_code
            == 404
        )
        assert client.get(
            "/api/chats/../../escape",
            params={"project": project.id},
        ).status_code in {404, 307}
        assert (
            client.delete(f"/api/chats/{thread_id}", params={"project": project.id}).status_code
            == 204
        )
        assert (
            client.get(f"/api/chats/{thread_id}", params={"project": project.id}).status_code == 404
        )


def test_chat_sse_persists_user_and_assistant_messages(tmp_path: Path) -> None:
    graph = InMemoryResearchGraph()
    project = graph.create_project("Streaming project")
    with TestClient(_app(tmp_path, graph, _answer_factory)) as client:
        thread = client.post(f"/api/projects/{project.id}/chats").json()
        thread_id = thread["id"]
        response = client.post(
            f"/api/chats/{thread_id}/messages",
            params={"project": project.id},
            json={"content": "What does the project say?", "mode": "ask"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            (line.split(": ", 1)[1], None)
            for line in response.text.splitlines()
            if line.startswith("event: ")
        ]
        assert [event for event, _ in events] == ["status", "answer", "done"]
        payloads: list[tuple[str, Any]] = []
        current: str | None = None
        for line in response.text.splitlines():
            if line.startswith("event: "):
                current = line.split(": ", 1)[1]
            elif line.startswith("data: ") and current is not None:
                payloads.append((current, json.loads(line[6:])))
        assert payloads[0] == ("status", {"text": "Searching papers: What does the project say?"})
        assert payloads[1][0] == "answer"
        assert payloads[2] == ("done", {})

        persisted = client.get(f"/api/chats/{thread_id}", params={"project": project.id}).json()
        assert [message["role"] for message in persisted["messages"]] == ["user", "assistant"]
        assert persisted["messages"][0]["content"] == "What does the project say?"
        assert persisted["messages"][1]["answer"]["model"] == "fake"
        assert persisted["messages"][1]["events"][0]["tool"] == "search_papers"


def test_chat_status_and_missing_key(tmp_path: Path) -> None:
    graph = InMemoryResearchGraph()
    project = graph.create_project("Unavailable project")
    with TestClient(_app(tmp_path, graph, _answer_factory, key=None)) as client:
        status = client.get("/api/chat/status")
        assert status.status_code == 200
        assert status.json() == {
            "available": False,
            "model": "deepseek/deepseek-v4-pro",
            "reason": "Ask mode needs OPENROUTER_API_KEY",
        }
        thread = client.post(f"/api/projects/{project.id}/chats").json()
        response = client.post(
            f"/api/chats/{thread['id']}/messages",
            params={"project": project.id},
            json={"content": "hello", "mode": "ask"},
        )
        assert response.status_code == 503
        assert response.json() == {"detail": "Ask mode needs OPENROUTER_API_KEY"}
        research = client.post(
            f"/api/chats/{thread['id']}/messages",
            params={"project": project.id},
            json={"content": "map papers", "mode": "research"},
        )
        assert research.status_code == 503
        assert research.json() == {"detail": "Research mode needs OPENROUTER_API_KEY"}


def test_one_active_answer_per_thread(tmp_path: Path) -> None:
    graph = InMemoryResearchGraph()
    project = graph.create_project("Concurrent project")
    started = threading.Event()
    release = threading.Event()

    class BlockingAgent(FakeAgent):
        def run(self, question: str, history: list[Any], on_event: Any, cancel: Any = None) -> Any:
            started.set()
            while not release.wait(0.01):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("cancelled")
            return super().run(question, history, on_event, cancel)

    def factory(graph: Any, documents: Any, settings: Any) -> BlockingAgent:
        return BlockingAgent(started=started)

    with TestClient(_app(tmp_path, graph, factory)) as client:
        thread = client.post(f"/api/projects/{project.id}/chats").json()
        thread_id = thread["id"]
        first_result: list[Any] = []

        def post_first() -> None:
            first_result.append(
                client.post(
                    f"/api/chats/{thread_id}/messages",
                    params={"project": project.id},
                    json={"content": "first", "mode": "ask"},
                )
            )

        worker = threading.Thread(target=post_first)
        worker.start()
        assert started.wait(2)
        second = client.post(
            f"/api/chats/{thread_id}/messages",
            params={"project": project.id},
            json={"content": "second", "mode": "ask"},
        )
        assert second.status_code == 409
        release.set()
        worker.join(timeout=3)
        assert first_result and first_result[0].status_code == 200


def _sse_events(response: Any) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    current: str | None = None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            current = line[7:]
        elif line.startswith("data: ") and current is not None:
            events.append((current, json.loads(line[6:])))
    return events


def test_research_plan_resume_and_expired_plan(tmp_path: Path) -> None:
    graph = InMemoryResearchGraph()
    project = graph.create_project("Research project")
    pending: set[str] = set()

    class FakeResearchAgent:
        last_run_id: str | None = None

        def bind_project(self, project_id: str) -> None:
            self.project_id = project_id

        def has_pending(self, thread_id: str) -> bool:
            return thread_id in pending

        def run(
            self, question: str, history: list[Any], on_event: Any, *, cancel: Any, thread_id: str
        ) -> dict[str, Any]:
            pending.add(thread_id)
            on_event(AgentEvent(text="Previewing results", tool="preview_search"))
            return {
                "status": "awaiting_approval",
                "plan": {
                    "tool_call_id": "call-run",
                    "args": {"query": "graph papers", "seeds": [], "rationale": "Map the field"},
                    "description": "Harvest graph papers to map the field.",
                },
            }

        def resume(
            self,
            thread_id: str,
            decision: str,
            edited_args: dict[str, Any] | None,
            message: str | None,
            on_event: Any,
            cancel: Any,
        ) -> dict[str, Any]:
            pending.remove(thread_id)
            if decision != "reject":
                self.last_run_id = "run-1"
                on_event(AgentEvent(text="Harvest: search — found papers", tool="run_research"))
            return {"status": "answered", "answer_markdown": "A concise research map."}

    def research_factory(graph: Any, documents: Any, settings: Any, runs: Any) -> Any:
        return FakeResearchAgent()

    with TestClient(
        _app(tmp_path, graph, _answer_factory, research_agent_factory=research_factory)
    ) as client:
        thread_id = client.post(f"/api/projects/{project.id}/chats").json()["id"]
        assert (
            client.post(
                f"/api/chats/{thread_id}/resume",
                params={"project": project.id},
                json={"decision": "approve"},
            ).status_code
            == 409
        )
        planned = client.post(
            f"/api/chats/{thread_id}/messages",
            params={"project": project.id},
            json={"content": "Map graph papers", "mode": "research"},
        )
        assert [event for event, _ in _sse_events(planned)] == ["status", "plan", "done"]
        plan = _sse_events(planned)[1][1]
        assert plan["args"]["query"] == "graph papers"
        stored = client.get(f"/api/chats/{thread_id}", params={"project": project.id}).json()
        assert stored["messages"][-1]["plan_status"] == "pending"
        assert stored["messages"][-1]["mode"] == "research"

        resumed = client.post(
            f"/api/chats/{thread_id}/resume",
            params={"project": project.id},
            json={"decision": "edit", "args": {"query": "citation graphs"}},
        )
        assert [event for event, _ in _sse_events(resumed)] == ["status", "answer", "done"]
        assert _sse_events(resumed)[1][1] == {
            "mode": "research",
            "answer_markdown": "A concise research map.",
        }
        stored = client.get(f"/api/chats/{thread_id}", params={"project": project.id}).json()
        assert stored["messages"][1]["plan_status"] == "edited"
        assert stored["messages"][1]["final_args"]["query"] == "citation graphs"
        assert stored["messages"][1]["final_args"]["rationale"] == "Map the field"
        assert stored["messages"][1]["run_id"] == "run-1"

        second_thread = client.post(f"/api/projects/{project.id}/chats").json()["id"]
        client.post(
            f"/api/chats/{second_thread}/messages",
            params={"project": project.id},
            json={"content": "Another map", "mode": "research"},
        )
        pending.clear()  # A backend restart loses the process-local checkpointer.
        expired = client.post(
            f"/api/chats/{second_thread}/resume",
            params={"project": project.id},
            json={"decision": "approve"},
        )
        assert expired.status_code == 410
        assert expired.json() == {"detail": "plan expired"}
        stored = client.get(f"/api/chats/{second_thread}", params={"project": project.id}).json()
        assert stored["messages"][1]["plan_status"] == "expired"
        # An expired plan must not lock the thread: a new message is accepted again.
        again = client.post(
            f"/api/chats/{second_thread}/messages",
            params={"project": project.id},
            json={"content": "Another map, again", "mode": "research"},
        )
        assert again.status_code == 200


@pytest.mark.parametrize("path", ["../escape", "a" * 31, "g" * 32])
def test_invalid_thread_ids_are_rejected(tmp_path: Path, path: str) -> None:
    graph = InMemoryResearchGraph()
    project = graph.create_project("Path project")
    with TestClient(_app(tmp_path, graph, _answer_factory)) as client:
        response = client.get(f"/api/chats/{path}", params={"project": project.id})
        assert response.status_code in {404, 307}

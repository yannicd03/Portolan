"""HTTP endpoints for project chat threads and streamed Ask answers."""

from __future__ import annotations

import asyncio
import inspect
import json
import queue
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..agent.ask import AgentEvent, AskAgent
from ..agent.chats import ChatMessage, ChatStore, Thread, ThreadSummary
from ..agent.citations import VerifiedAnswer
from ..documents.store import DocumentStore
from ..graph.base import ResearchGraph
from ..settings import Settings


class ChatCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        return value or None


class ChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)
    mode: Literal["ask"] = "ask"

    @field_validator("content")
    @classmethod
    def require_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class ChatStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    model: str
    reason: str | None = None


def _invoke_provider(provider: Callable[..., Any], project_id: str) -> Any:
    """Call an injected provider accepting either ``()`` or ``(project_id)``."""

    try:
        signature = inspect.signature(provider)
    except (TypeError, ValueError):
        return provider()
    parameters = list(signature.parameters.values())
    required = [
        parameter
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and parameter.default is inspect.Parameter.empty
    ]
    return provider(project_id) if required else provider()


def _sse(event: str, payload: Any) -> str:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event_model(value: Any) -> AgentEvent:
    if isinstance(value, AgentEvent):
        return value
    if isinstance(value, str):
        return AgentEvent(text=value)
    return AgentEvent.model_validate(value)


def _answer_model(value: Any) -> VerifiedAnswer:
    if isinstance(value, VerifiedAnswer):
        return value
    return VerifiedAnswer.model_validate(value)


def build_chat_router(
    get_graph: Callable[..., ResearchGraph],
    get_documents: Callable[..., DocumentStore],
    settings: Settings,
    *,
    agent_factory: Callable[[ResearchGraph, DocumentStore, Settings], AskAgent] | None = None,
) -> APIRouter:
    """Build the chat API router around application-owned graph resources."""

    router = APIRouter()
    store = ChatStore(settings.chats_dir)
    factory = agent_factory or AskAgent
    active: dict[str, threading.Event] = {}
    active_lock = threading.RLock()

    def graph_for(project_id: str) -> ResearchGraph:
        graph = _invoke_provider(get_graph, project_id)
        if graph.get_project(project_id) is None:
            raise HTTPException(status_code=404, detail="project not found")
        return graph

    def thread_for(project_id: str, thread_id: str) -> Thread:
        try:
            thread = store.get(project_id, thread_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="chat thread not found") from error
        if thread is None:
            raise HTTPException(status_code=404, detail="chat thread not found")
        return thread

    @router.get(
        "/api/projects/{project_id}/chats",
        response_model=list[ThreadSummary],
    )
    def list_chats(project_id: str) -> list[ThreadSummary]:
        graph_for(project_id)
        try:
            return store.list(project_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="project not found") from error

    @router.post(
        "/api/projects/{project_id}/chats",
        response_model=Thread,
        status_code=201,
    )
    def create_chat(project_id: str, body: ChatCreateRequest | None = None) -> Thread:
        graph_for(project_id)
        try:
            return store.create(project_id, body.title if body else None)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="project not found") from error

    @router.get("/api/chats/{thread_id}", response_model=Thread)
    def get_chat(thread_id: str, project: str = Query(...)) -> Thread:
        graph_for(project)
        return thread_for(project, thread_id)

    @router.delete("/api/chats/{thread_id}", status_code=204)
    def delete_chat(thread_id: str, project: str = Query(...)) -> Response:
        graph_for(project)
        thread_for(project, thread_id)
        with active_lock:
            if thread_id in active:
                raise HTTPException(status_code=409, detail="chat thread has an active answer")
        try:
            deleted = store.delete(project, thread_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="chat thread not found") from error
        if not deleted:
            raise HTTPException(status_code=404, detail="chat thread not found")
        return Response(status_code=204)

    @router.get("/api/chat/status", response_model=ChatStatus)
    def chat_status() -> ChatStatus:
        available = bool(settings.openrouter_api_key)
        return ChatStatus(
            available=available,
            model=settings.chat_model,
            reason=None if available else "Ask mode needs OPENROUTER_API_KEY",
        )

    @router.post("/api/chats/{thread_id}/messages", response_model=None)
    def post_message(
        thread_id: str,
        body: ChatMessageRequest,
        request: Request,
        project: str = Query(...),
    ) -> StreamingResponse | JSONResponse:
        graph = graph_for(project)
        thread = thread_for(project, thread_id)
        if not settings.openrouter_api_key:
            return JSONResponse(
                status_code=503,
                content={"detail": "Ask mode needs OPENROUTER_API_KEY"},
            )

        with active_lock:
            if thread_id in active:
                raise HTTPException(status_code=409, detail="chat thread has an active answer")
            cancel = threading.Event()
            active[thread_id] = cancel

        history = list(thread.messages)
        user_message = ChatMessage(role="user", content=body.content)
        try:
            appended = store.append(project, thread_id, user_message)
        except ValueError as error:
            with active_lock:
                active.pop(thread_id, None)
            raise HTTPException(status_code=404, detail="chat thread not found") from error
        if appended is None:
            with active_lock:
                active.pop(thread_id, None)
            raise HTTPException(status_code=404, detail="chat thread not found")

        events: queue.Queue[tuple[str, Any]] = queue.Queue()
        recorded_events: list[AgentEvent] = []

        def on_event(value: Any) -> None:
            event = _event_model(value)
            recorded_events.append(event)
            events.put(("status", event))

        def run_agent() -> None:
            try:
                documents = _invoke_provider(get_documents, project)
                agent = factory(graph, documents, settings)
                bind_project = getattr(agent, "bind_project", None)
                if callable(bind_project):
                    bind_project(project)
                answer = _answer_model(agent.run(body.content, history, on_event, cancel=cancel))
                assistant = ChatMessage(
                    role="assistant",
                    content=answer.answer_markdown,
                    answer=answer,
                    events=list(recorded_events),
                )
                store.append(project, thread_id, assistant)
                events.put(("answer", answer))
            except Exception as error:
                detail = str(error).strip() or "Ask mode failed"
                assistant = ChatMessage(
                    role="assistant",
                    content=detail,
                    events=list(recorded_events),
                    error=detail,
                )
                with suppress(Exception):
                    store.append(project, thread_id, assistant)
                events.put(("error", {"detail": detail}))
            finally:
                events.put(("done", {}))
                with active_lock:
                    active.pop(thread_id, None)

        worker = threading.Thread(
            target=run_agent,
            name=f"portolan-chat-{thread_id[:8]}",
            daemon=True,
        )
        worker.start()

        async def stream() -> AsyncIterator[str]:
            try:
                while True:
                    if await request.is_disconnected():
                        cancel.set()
                        break
                    try:
                        event, payload = await asyncio.to_thread(events.get, True, 0.1)
                    except queue.Empty:
                        continue
                    if event == "done":
                        yield _sse("done", payload)
                        break
                    if event == "status":
                        payload = {"text": _event_model(payload).text}
                    yield _sse(event, payload)
            finally:
                cancel.set()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


__all__ = ["build_chat_router"]

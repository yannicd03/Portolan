"""HTTP endpoints for project chat threads and streamed agent answers."""

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
from ..agent.research import ResearchAgent
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
    mode: Literal["ask", "research"] = "ask"

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


class ChatResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "edit", "reject"]
    args: dict[str, Any] | None = None
    message: str | None = None


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


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def build_chat_router(
    get_graph: Callable[..., ResearchGraph],
    get_documents: Callable[..., DocumentStore],
    settings: Settings,
    *,
    agent_factory: Callable[[ResearchGraph, DocumentStore, Settings], AskAgent] | None = None,
    research_agent_factory: Callable[..., ResearchAgent] | None = None,
    get_runs: Callable[..., Any] | None = None,
) -> APIRouter:
    """Build the chat API router around application-owned graph resources."""

    router = APIRouter()
    store = ChatStore(settings.chats_dir)
    factory = agent_factory or AskAgent
    active: dict[str, threading.Event] = {}
    active_lock = threading.RLock()

    def research_agent(project_id: str, graph: ResearchGraph) -> ResearchAgent:
        documents = _invoke_provider(get_documents, project_id)
        runs = _invoke_provider(get_runs, project_id) if get_runs is not None else None
        if research_agent_factory is None:
            agent = ResearchAgent(graph, documents, settings, runs=runs)
        else:
            agent = research_agent_factory(graph, documents, settings, runs)
        bind_project = getattr(agent, "bind_project", None)
        if callable(bind_project):
            bind_project(project_id)
        return agent

    def reserve(thread_id: str) -> threading.Event:
        with active_lock:
            if thread_id in active:
                raise HTTPException(status_code=409, detail="chat thread has an active answer")
            cancel = threading.Event()
            active[thread_id] = cancel
            return cancel

    def streamed_events(
        request: Request, events: queue.Queue[tuple[str, Any]], cancel: threading.Event
    ) -> StreamingResponse:
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
                content={"detail": f"{body.mode.title()} mode needs OPENROUTER_API_KEY"},
            )
        if any(message.plan_status == "pending" for message in thread.messages):
            raise HTTPException(status_code=409, detail="chat thread has a pending plan")
        cancel = reserve(thread_id)

        history = list(thread.messages)
        user_message = ChatMessage(role="user", content=body.content, mode=body.mode)
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
                if body.mode == "research":
                    agent = research_agent(project, graph)
                    turn = agent.run(
                        body.content, history, on_event, cancel=cancel, thread_id=thread_id
                    )
                    if _value(turn, "status") == "awaiting_approval":
                        plan = _value(turn, "plan")
                        args = _value(plan, "args", {})
                        description = _value(plan, "description", "Research plan awaiting approval")
                        assistant = ChatMessage(
                            role="assistant",
                            content=description,
                            mode="research",
                            pending_plan=args,
                            plan_status="pending",
                            events=list(recorded_events),
                        )
                        store.append(project, thread_id, assistant)
                        events.put(("plan", plan))
                    else:
                        answer_markdown = _value(turn, "answer_markdown", "")
                        store.append(
                            project,
                            thread_id,
                            ChatMessage(
                                role="assistant",
                                content=answer_markdown,
                                mode="research",
                                events=list(recorded_events),
                            ),
                        )
                        events.put(
                            ("answer", {"mode": "research", "answer_markdown": answer_markdown})
                        )
                else:
                    documents = _invoke_provider(get_documents, project)
                    agent = factory(graph, documents, settings)
                    bind_project = getattr(agent, "bind_project", None)
                    if callable(bind_project):
                        bind_project(project)
                    answer = _answer_model(
                        agent.run(body.content, history, on_event, cancel=cancel)
                    )
                    assistant = ChatMessage(
                        role="assistant",
                        content=answer.answer_markdown,
                        answer=answer,
                        events=list(recorded_events),
                        mode="ask",
                    )
                    store.append(project, thread_id, assistant)
                    events.put(("answer", answer))
            except Exception as error:
                detail = str(error).strip() or f"{body.mode.title()} mode failed"
                assistant = ChatMessage(
                    role="assistant",
                    content=detail,
                    events=list(recorded_events),
                    error=detail,
                    mode=body.mode,
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

        return streamed_events(request, events, cancel)

    @router.post("/api/chats/{thread_id}/resume", response_model=None)
    def resume_chat(
        thread_id: str,
        body: ChatResumeRequest,
        request: Request,
        project: str = Query(...),
    ) -> StreamingResponse | JSONResponse:
        graph = graph_for(project)
        thread = thread_for(project, thread_id)
        if not settings.openrouter_api_key:
            return JSONResponse(
                status_code=503,
                content={"detail": "Research mode needs OPENROUTER_API_KEY"},
            )
        plan_message = next(
            (message for message in reversed(thread.messages) if message.plan_status == "pending"),
            None,
        )
        if plan_message is None:
            raise HTTPException(status_code=409, detail="chat thread has no pending plan")
        cancel = reserve(thread_id)
        try:
            agent = research_agent(project, graph)
            if not agent.has_pending(thread_id):
                # Resolve the plan so the thread accepts new messages again.
                store.update_message(
                    project,
                    thread_id,
                    plan_message.id,
                    plan_status="expired",
                    error="plan expired",
                )
                raise HTTPException(status_code=410, detail="plan expired")
        except Exception:
            with active_lock:
                active.pop(thread_id, None)
            raise

        final_args = plan_message.pending_plan
        if body.decision == "edit":
            final_args = {**(final_args or {}), **(body.args or {})}
        status = {"approve": "approved", "edit": "edited", "reject": "rejected"}[body.decision]
        store.update_message(
            project,
            thread_id,
            plan_message.id,
            plan_status=status,
            final_args=final_args,
        )
        events: queue.Queue[tuple[str, Any]] = queue.Queue()
        recorded_events: list[AgentEvent] = []

        def on_event(value: Any) -> None:
            event = _event_model(value)
            recorded_events.append(event)
            events.put(("status", event))

        def run_resume() -> None:
            try:
                turn = agent.resume(
                    thread_id,
                    body.decision,
                    edited_args=body.args,
                    message=body.message,
                    on_event=on_event,
                    cancel=cancel,
                )
                if _value(turn, "status") == "awaiting_approval":
                    plan = _value(turn, "plan")
                    assistant = ChatMessage(
                        role="assistant",
                        content=_value(plan, "description", "Research plan awaiting approval"),
                        mode="research",
                        pending_plan=_value(plan, "args", {}),
                        plan_status="pending",
                        events=list(recorded_events),
                    )
                    store.append(project, thread_id, assistant)
                    events.put(("plan", plan))
                else:
                    answer_markdown = _value(turn, "answer_markdown", "")
                    store.append(
                        project,
                        thread_id,
                        ChatMessage(
                            role="assistant",
                            content=answer_markdown,
                            mode="research",
                            events=list(recorded_events),
                        ),
                    )
                    events.put(("answer", {"mode": "research", "answer_markdown": answer_markdown}))
            except Exception as error:
                detail = str(error).strip() or "Research mode failed"
                with suppress(Exception):
                    store.append(
                        project,
                        thread_id,
                        ChatMessage(
                            role="assistant", content=detail, mode="research", error=detail
                        ),
                    )
                events.put(("error", {"detail": detail}))
            finally:
                run_id = getattr(agent, "last_run_id", None)
                if run_id is not None:
                    with suppress(Exception):
                        store.update_message(project, thread_id, plan_message.id, run_id=run_id)
                events.put(("done", {}))
                with active_lock:
                    active.pop(thread_id, None)

        threading.Thread(
            target=run_resume,
            name=f"portolan-resume-{thread_id[:8]}",
            daemon=True,
        ).start()
        return streamed_events(request, events, cancel)

    return router


__all__ = ["build_chat_router"]

"""Deep agent that plans and runs a project literature harvest."""

from __future__ import annotations

import inspect
import threading
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from ..documents.store import DocumentStore
from ..graph.base import ResearchGraph
from ..settings import Settings
from .ask import AgentEvent, AgentLimitReached, AgentUnavailable, _model_profile
from .prompts import RESEARCH_PROMPT
from .tools import build_research_tools

if TYPE_CHECKING:
    from .chats import ChatMessage


class ResearchPlan(BaseModel):
    """The harvest request waiting for the user's approval."""

    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    args: dict[str, Any] = Field(default_factory=dict)
    description: str


class ResearchTurn(BaseModel):
    """One completed Research-mode turn or one approval request."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["answered", "awaiting_approval"]
    answer_markdown: str | None = None
    plan: ResearchPlan | None = None
    # The API stores this on the plan message after a harvest is submitted.
    # It is optional because direct runner mode has no registry run id.
    run_id: str | None = None


class ResearchPlanExpired(RuntimeError):
    """The checkpointer no longer contains the interrupted research plan."""


# Pending interrupts survive HTTP requests in this process. A backend restart drops
# them; the persisted chat plan then reports "plan expired" on resume.
_CHECKPOINTER = InMemorySaver()


class _CancelProxy:
    """An event-shaped view whose target can change between HTTP requests."""

    def __init__(self, agent: ResearchAgent) -> None:
        self._agent = agent

    def is_set(self) -> bool:
        event = self._agent._cancel
        return event is not None and event.is_set()

    def __bool__(self) -> bool:
        return self.is_set()

    def __call__(self) -> bool:
        return self.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        event = self._agent._cancel
        return bool(event is not None and event.wait(timeout))


class ResearchAgent:
    """Plan, approve, and explain a literature harvest for one project."""

    def __init__(
        self,
        graph: ResearchGraph,
        documents: DocumentStore,
        settings: Settings,
        *,
        model: BaseChatModel | None = None,
        runs: Any | None = None,
        sources_factory: Callable[..., Any] | None = None,
        runner_factory: Callable[..., Any] | None = None,
        checkpointer: Any | None = None,
        project_id: str | None = None,
    ) -> None:
        self.graph = graph
        self.documents = documents
        self.settings = settings
        self.runs = runs
        self.sources_factory = sources_factory
        self.runner_factory = runner_factory
        self.checkpointer = checkpointer if checkpointer is not None else _CHECKPOINTER
        self.project_id = project_id
        self.model = self._configured_model(model)
        self.agent: Any = None
        self._on_event: Callable[[AgentEvent], None] = lambda event: None
        self._cancel: threading.Event | None = None
        self._cancel_proxy = _CancelProxy(self)
        self._last_run_id: str | None = None
        self._harvest_stage: str | None = None
        self._harvest_at = 0.0
        self._run_ids_before: set[str] = set()

        if self.project_id is None:
            try:
                projects = graph.list_projects()
            except (AttributeError, KeyError, OSError, TypeError, ValueError):
                projects = []
            if len(projects) == 1:
                self.project_id = projects[0].id
        if self.project_id is not None:
            self._build()

    def _configured_model(self, model: BaseChatModel | None) -> BaseChatModel:
        if model is not None:
            return model
        if not self.settings.openrouter_api_key:
            raise AgentUnavailable("Research mode needs OPENROUTER_API_KEY")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=self.settings.chat_model,
            base_url=self.settings.openrouter_base_url,
            api_key=self.settings.openrouter_api_key,
            temperature=0,
        )

    @property
    def last_run_id(self) -> str | None:
        """Return the most recently observed registry run id."""

        return self._last_run_id

    def bind_project(self, project_id: str) -> None:
        """Scope all research and graph tools to one project."""

        if self.project_id != project_id or self.agent is None:
            self.project_id = project_id
            self._build()

    def _build(self) -> None:
        assert self.project_id is not None
        _model_profile(self.model)
        tools = self._research_tools()
        self.agent = create_deep_agent(
            self.model,
            tools=tools,
            system_prompt=RESEARCH_PROMPT,
            backend=FilesystemBackend(root_dir=self.documents.root, virtual_mode=True),
            permissions=[FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")],
            interrupt_on={
                "run_research": {
                    "allowed_decisions": ["approve", "edit", "reject"],
                }
            },
            checkpointer=self.checkpointer,
        )

    def _research_tools(self) -> list[Any]:
        """Build tools while tolerating the optional callback parameters' evolution."""

        assert self.project_id is not None
        callback_values: dict[str, Any] = {
            "settings": self.settings,
            "documents": self.documents,
            "runner_factory": self.runner_factory,
            "on_event": self._harvest_event,
            "event_callback": self._harvest_event,
            "cancel": self._cancel_proxy,
            "cancel_event": self._cancel_proxy,
            "on_run_submitted": self._record_run_id,
            "run_id_callback": self._record_run_id,
        }
        try:
            signature = inspect.signature(build_research_tools)
            parameters = signature.parameters
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
            )
            kwargs = (
                callback_values
                if accepts_kwargs
                else {name: value for name, value in callback_values.items() if name in parameters}
            )
        except (TypeError, ValueError):
            kwargs = callback_values
        return build_research_tools(
            self.graph,
            self.project_id,
            self.sources_factory,
            self.runs,
            **kwargs,
        )

    def _record_run_id(self, run_id: Any) -> None:
        if run_id is None:
            return
        if isinstance(run_id, Mapping):
            run_id = run_id.get("id") or run_id.get("run_id")
        else:
            run_id = getattr(run_id, "id", run_id)
        if run_id:
            self._last_run_id = str(run_id)

    def _harvest_event(self, event: Any) -> None:
        """Forward throttled pipeline progress through the chat event callback."""

        if isinstance(event, AgentEvent):
            self._on_event(event)
            return
        if isinstance(event, str) and event.startswith("Harvest:"):
            self._on_event(AgentEvent(text=event, tool="run_research"))
            return
        if isinstance(event, Mapping):
            stage = str(event.get("stage", "harvest"))
            message = str(event.get("message", ""))
        else:
            stage = str(getattr(event, "stage", "harvest"))
            message = str(getattr(event, "message", event))
        now = time.monotonic()
        if stage == self._harvest_stage and now - self._harvest_at < 5.0:
            return
        self._harvest_stage = stage
        self._harvest_at = now
        self._on_event(AgentEvent(text=f"Harvest: {stage} — {message}", tool="run_research"))

    def _activity(self, name: str, args: Mapping[str, Any]) -> str:
        if name == "preview_search":
            return f"Previewing search: {args.get('query', '')}"
        if name == "lookup_paper":
            return f"Looking up paper: {args.get('identifier', '')}"
        if name == "run_research":
            return "Preparing the research harvest"
        if name == "map_summary":
            return "Summarizing the project map"
        if name == "project_overview":
            return "Reviewing the project overview"
        if name == "search_papers":
            return f"Searching papers: {args.get('query', '')}"
        if name == "grep":
            return f"Searching paper text: {args.get('pattern', '')}"
        if name == "read_file":
            return f"Reading {args.get('file_path', 'paper')}"
        labels = {
            "paper_info": "Checking paper",
            "citation_neighbors": "Following citations",
            "papers_by_concept": "Exploring concept",
            "papers_by_author": "Exploring author",
        }
        value = next(iter(args.values()), "")
        return f"{labels.get(name, 'Using ' + name)}: {value}"

    def _messages(self, question: str, history: list[ChatMessage]) -> list[Any]:
        messages: list[Any] = []
        for item in history:
            content = getattr(item, "content", "")
            if getattr(item, "role", None) == "user":
                messages.append(HumanMessage(content=content))
            elif getattr(item, "role", None) == "assistant" and not getattr(item, "error", None):
                messages.append(AIMessage(content=content))
        if (
            not messages
            or not isinstance(messages[-1], HumanMessage)
            or messages[-1].content != question
        ):
            messages.append(HumanMessage(content=question))
        return messages

    @staticmethod
    def _updates(chunk: Any) -> list[tuple[bool, dict[str, Any]]]:
        namespace: tuple[str, ...] = ()
        if isinstance(chunk, tuple) and len(chunk) == 2:
            namespace, chunk = chunk
        if not isinstance(chunk, dict):
            return []
        updates: list[tuple[bool, dict[str, Any]]] = []
        for key, value in chunk.items():
            if key == "__interrupt__":
                updates.append((not namespace, {key: value}))
            elif isinstance(value, dict):
                updates.append((not namespace, value))
        return updates

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, Mapping) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif hasattr(item, "text"):
                    parts.append(str(item.text))
            return "".join(parts)
        return str(content or "")

    @staticmethod
    def _interrupt_payload(update: Mapping[str, Any]) -> Mapping[str, Any] | None:
        raw = update.get("__interrupt__")
        if raw is None:
            return None
        values = raw if isinstance(raw, (tuple, list)) else (raw,)
        for value in values:
            payload = getattr(value, "value", value)
            if isinstance(payload, Mapping) and payload.get("action_requests"):
                return payload
        return None

    def _state(self, thread_id: str) -> Any | None:
        if self.agent is None:
            return None
        try:
            return self.agent.get_state(self._config(thread_id))
        except (LookupError, RuntimeError, TypeError, ValueError):
            return None

    def _config(self, thread_id: str) -> dict[str, Any]:
        assert self.agent is not None
        node_count = len(self.agent.nodes)
        recursion_limit = max(50, 4 * node_count * (self.settings.chat_max_tool_calls + 2))
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": recursion_limit,
        }

    def _historical_call_ids(self, thread_id: str) -> set[str]:
        state = self._state(thread_id)
        if state is None:
            return set()
        values = getattr(state, "values", {}) or {}
        messages = values.get("messages", []) if isinstance(values, Mapping) else []
        result: set[str] = set()
        for message in messages:
            for call in getattr(message, "tool_calls", []) or []:
                if call.get("id"):
                    result.add(str(call["id"]))
        return result

    def _run_ids(self) -> set[str]:
        if self.runs is None or self.project_id is None:
            return set()
        try:
            runs = self.runs.list_for_project(self.project_id)
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            return set()
        result: set[str] = set()
        for run in runs:
            run_id = run.get("id") if isinstance(run, Mapping) else getattr(run, "id", None)
            if run_id is not None:
                result.add(str(run_id))
        return result

    def _discover_run_id(self) -> None:
        if self._last_run_id is not None or self.runs is None or self.project_id is None:
            return
        try:
            runs = self.runs.list_for_project(self.project_id)
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            return
        candidates = []
        for run in runs:
            run_id = run.get("id") if isinstance(run, Mapping) else getattr(run, "id", None)
            if run_id is None or str(run_id) in self._run_ids_before:
                continue
            created = (
                run.get("created_at")
                if isinstance(run, Mapping)
                else getattr(run, "created_at", None)
            )
            candidates.append((created, str(run_id)))
        if candidates:
            candidates.sort(key=lambda item: (item[0] is not None, item[0], item[1]))
            self._last_run_id = candidates[-1][1]

    def has_pending(self, thread_id: str) -> bool:
        """Return whether a resumable HITL interrupt exists for ``thread_id``."""

        state = self._state(thread_id)
        if state is None:
            return False
        if getattr(state, "interrupts", ()):
            return True
        return any(getattr(task, "interrupts", ()) for task in getattr(state, "tasks", ()) or ())

    def _pending_args(self, thread_id: str) -> dict[str, Any]:
        """Recover the interrupted call so partial edits retain its other settings."""

        state = self._state(thread_id)
        values = getattr(state, "values", {}) if state is not None else {}
        messages = values.get("messages", []) if isinstance(values, Mapping) else []
        for message in reversed(messages):
            for call in reversed(getattr(message, "tool_calls", []) or []):
                if call.get("name") == "run_research":
                    args = call.get("args") or {}
                    return dict(args) if isinstance(args, Mapping) else {}
        return {}

    def _stream(
        self,
        input_value: Any,
        *,
        thread_id: str,
        on_event: Callable[[AgentEvent], None],
        cancel: threading.Event | None,
    ) -> ResearchTurn:
        if self.agent is None:
            raise AgentUnavailable("Research mode needs a project")
        self._on_event = on_event
        self._cancel = cancel
        self._last_run_id = None
        self._run_ids_before = self._run_ids()
        self._harvest_stage = None
        self._harvest_at = 0.0
        seen_call_ids = self._historical_call_ids(thread_id)
        tool_calls = 0
        answer: str | None = None
        pending_call_id: str | None = None
        pending_payload: Mapping[str, Any] | None = None
        try:
            for chunk in self.agent.stream(
                input_value,
                config=self._config(thread_id),
                stream_mode="updates",
                subgraphs=True,
            ):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("Research mode cancelled")
                for is_root, update in self._updates(chunk):
                    if not is_root:
                        continue
                    if payload := self._interrupt_payload(update):
                        pending_payload = payload
                    for message in update.get("messages", []):
                        calls = getattr(message, "tool_calls", []) or []
                        for call in calls:
                            call_id = str(call.get("id", ""))
                            if call_id and call_id in seen_call_ids:
                                continue
                            if call_id:
                                seen_call_ids.add(call_id)
                            tool_calls += 1
                            if tool_calls > self.settings.chat_max_tool_calls:
                                raise AgentLimitReached(
                                    "Research mode reached "
                                    f"{self.settings.chat_max_tool_calls} tool calls"
                                )
                            name = str(call.get("name", ""))
                            args = call.get("args") or {}
                            if name == "run_research":
                                pending_call_id = call_id or pending_call_id
                            on_event(AgentEvent(text=self._activity(name, args), tool=name))
                        if isinstance(message, AIMessage) and not calls:
                            answer = self._content_text(message.content)
            if pending_payload is not None:
                requests = pending_payload.get("action_requests") or []
                action = next(
                    (
                        item
                        for item in requests
                        if isinstance(item, Mapping) and item.get("name") == "run_research"
                    ),
                    requests[0] if requests else {},
                )
                if isinstance(action, Mapping):
                    args = action.get("args") or {}
                    description = str(action.get("description") or "Review the research plan.")
                    return ResearchTurn(
                        status="awaiting_approval",
                        plan=ResearchPlan(
                            tool_call_id=pending_call_id or "",
                            args=dict(args) if isinstance(args, Mapping) else {},
                            description=description,
                        ),
                    )
            if answer is None:
                state = self._state(thread_id)
                values = getattr(state, "values", {}) if state is not None else {}
                messages = values.get("messages", []) if isinstance(values, Mapping) else []
                for message in reversed(messages):
                    if isinstance(message, AIMessage) and not getattr(message, "tool_calls", None):
                        answer = self._content_text(message.content)
                        break
            if answer is None:
                raise RuntimeError("Research mode returned no answer")
            self._discover_run_id()
            return ResearchTurn(
                status="answered",
                answer_markdown=answer,
                run_id=self._last_run_id,
            )
        finally:
            self._cancel = None

    def run(
        self,
        question: str,
        history: list[ChatMessage],
        on_event: Callable[[AgentEvent], None],
        cancel: threading.Event | None,
        thread_id: str,
    ) -> ResearchTurn:
        """Run a planning turn, returning an answer or an approval plan."""

        if self.agent is None:
            raise AgentUnavailable("Research mode needs a project")
        return self._stream(
            {"messages": self._messages(question, history)},
            thread_id=thread_id,
            on_event=on_event,
            cancel=cancel,
        )

    def resume(
        self,
        thread_id: str,
        decision: Literal["approve", "edit", "reject"],
        edited_args: dict[str, Any] | None,
        message: str | None,
        on_event: Callable[[AgentEvent], None],
        cancel: threading.Event | None,
    ) -> ResearchTurn:
        """Resume the interrupted harvest after the user's decision."""

        if self.agent is None:
            raise AgentUnavailable("Research mode needs a project")
        if not self.has_pending(thread_id):
            raise ResearchPlanExpired("plan expired")
        if decision == "approve":
            human_decision: dict[str, Any] = {"type": "approve"}
        elif decision == "reject":
            human_decision = {"type": "reject"}
            if message:
                human_decision["message"] = message
        else:
            human_decision = {
                "type": "edit",
                "edited_action": {
                    "name": "run_research",
                    "args": {**self._pending_args(thread_id), **(edited_args or {})},
                },
            }
        return self._stream(
            Command(resume={"decisions": [human_decision]}),
            thread_id=thread_id,
            on_event=on_event,
            cancel=cancel,
        )


__all__ = [
    "ResearchAgent",
    "ResearchPlan",
    "ResearchPlanExpired",
    "ResearchTurn",
]

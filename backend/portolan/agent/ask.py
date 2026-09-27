"""Deep Agents runner for answers grounded in project papers."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from ..documents.store import DocumentStore
from ..graph.base import ResearchGraph
from ..settings import Settings
from .citations import AskAnswer, VerifiedAnswer, verify_citations
from .prompts import ASK_PROMPT, PAPER_READER_PROMPT
from .tools import build_graph_tools

if TYPE_CHECKING:
    from .chats import ChatMessage


class AgentUnavailable(RuntimeError):
    """Ask mode cannot run with the current configuration."""


class AgentLimitReached(RuntimeError):
    """The configured tool-call budget was exhausted."""


class AgentEvent(BaseModel):
    """One human-readable activity update from the Ask agent."""

    text: str
    tool: str | None = None


def _model_profile(model: BaseChatModel) -> None:
    """Disable the generic worker and shell tool for this exact model."""

    from deepagents._models import get_model_identifier, get_model_provider

    provider = get_model_provider(model)
    identifier = get_model_identifier(model)
    if provider and identifier:
        register_harness_profile(
            f"{provider}:{identifier}",
            HarnessProfile(
                general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
                excluded_tools=frozenset({"execute"}),
            ),
        )


class AskAgent:
    """Answer one question from the current project's stored papers."""

    def __init__(
        self,
        graph: ResearchGraph,
        documents: DocumentStore,
        settings: Settings,
        *,
        model: BaseChatModel | None = None,
        project_id: str | None = None,
    ) -> None:
        self.graph = graph
        self.documents = documents
        self.settings = settings
        if model is None:
            if not settings.openrouter_api_key:
                raise AgentUnavailable("Ask mode needs OPENROUTER_API_KEY")
            from langchain_openai import ChatOpenAI

            model = ChatOpenAI(
                model=settings.chat_model,
                base_url=settings.openrouter_base_url,
                api_key=settings.openrouter_api_key,
                temperature=0,
            )
        self.model = model
        self.project_id = project_id
        self.agent: Any = None
        if self.project_id is None:
            projects = graph.list_projects()
            if len(projects) == 1:
                self.project_id = projects[0].id
        if self.project_id is not None:
            self._build()

    def bind_project(self, project_id: str) -> None:
        """Scope all graph tools to one project before running."""

        if self.project_id != project_id or self.agent is None:
            self.project_id = project_id
            self._build()

    def _build(self) -> None:
        assert self.project_id is not None
        _model_profile(self.model)
        self.agent = create_deep_agent(
            self.model,
            tools=build_graph_tools(self.graph, self.project_id, self.documents),
            system_prompt=ASK_PROMPT,
            backend=FilesystemBackend(root_dir=self.documents.root, virtual_mode=True),
            permissions=[FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")],
            subagents=[
                {
                    "name": "paper-reader",
                    "description": (
                        "Read ONE paper to answer a focused question; return verbatim quotes "
                        "with page numbers."
                    ),
                    "system_prompt": PAPER_READER_PROMPT,
                }
            ],
            response_format=AskAnswer,
        )

    def _activity(self, name: str, args: dict[str, Any]) -> str:
        if name == "search_papers":
            return f"Searching papers: {args.get('query', '')}"
        if name == "project_overview":
            return "Reviewing the project overview"
        if name == "task":
            return (
                f"Delegating to paper-reader: {args.get('description', args.get('task', 'paper'))}"
            )
        if name == "grep":
            return f"Searching paper text: {args.get('pattern', '')}"
        if name == "read_file":
            path = str(args.get("file_path", ""))
            title = self._title_for_path(path)
            pages = self._pages_for_lines(path, args.get("offset", 0), args.get("limit", 200))
            if pages:
                first, last = pages
                return f"Reading p. {first}{f'–{last}' if first != last else ''} of {title}"
            return f"Reading {title}"
        labels = {
            "paper_info": "Checking paper",
            "citation_neighbors": "Following citations",
            "papers_by_concept": "Exploring concept",
            "papers_by_author": "Exploring author",
        }
        value = next(iter(args.values()), "")
        return f"{labels.get(name, 'Using ' + name)}: {value}"

    def _title_for_path(self, path: str) -> str:
        match = re.search(r"/([0-9a-f]{64})/paper\.txt$", path)
        if match and self.project_id:
            for work in self.graph.project_works(self.project_id):
                if work.document_sha256 == match.group(1):
                    return work.title[:70]
        return path.rsplit("/", 1)[-1] or "paper"

    def _pages_for_lines(self, path: str, offset: Any, limit: Any) -> tuple[int, int] | None:
        match = re.fullmatch(r"/([0-9a-f]{2})/([0-9a-f]{64})/paper\.txt", path)
        if not match or match.group(1) != match.group(2)[:2]:
            return None
        try:
            lines = (
                self.documents.text_path(match.group(2)).read_text(encoding="utf-8").splitlines()
            )
            start = max(0, int(offset))
            end = min(len(lines), start + max(1, int(limit)))
        except (OSError, ValueError, TypeError):
            return None
        pages: list[int] = []
        current = 1
        for index, line in enumerate(lines[:end]):
            if marker := re.fullmatch(r"=== page (\d+) ===", line):
                current = int(marker.group(1))
            if index >= start:
                pages.append(current)
        return (pages[0], pages[-1]) if pages else None

    def _messages(self, question: str, history: list[ChatMessage]) -> list[Any]:
        messages: list[Any] = []
        for item in history:
            content = item.content
            if item.role == "user":
                messages.append(HumanMessage(content=content))
            elif item.role == "assistant" and not item.error:
                messages.append(AIMessage(content=content))
        if (
            not messages
            or not isinstance(messages[-1], HumanMessage)
            or messages[-1].content != question
        ):
            messages.append(HumanMessage(content=question))
        return messages

    @staticmethod
    def _updates(chunk: Any) -> Iterator[tuple[bool, dict[str, Any]]]:
        namespace: tuple[str, ...] = ()
        if isinstance(chunk, tuple) and len(chunk) == 2:
            namespace, chunk = chunk
        if isinstance(chunk, dict):
            for value in chunk.values():
                if isinstance(value, dict):
                    yield not namespace, value

    def run(
        self,
        question: str,
        history: list[ChatMessage],
        on_event: Callable[[AgentEvent], None],
        cancel: threading.Event | None = None,
    ) -> VerifiedAnswer:
        """Stream tool activity, then independently verify every cited quote."""

        if self.agent is None:
            raise AgentUnavailable("Ask mode needs a project")
        tool_calls = 0
        structured: AskAnswer | None = None
        node_count = len(self.agent.nodes)
        recursion_limit = max(50, 4 * node_count * (self.settings.chat_max_tool_calls + 2))
        for chunk in self.agent.stream(
            {"messages": self._messages(question, history)},
            config={"recursion_limit": recursion_limit},
            stream_mode="updates",
            subgraphs=True,
        ):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Ask mode cancelled")
            for is_root, update in self._updates(chunk):
                response = update.get("structured_response")
                if is_root and response is not None:
                    structured = AskAnswer.model_validate(response)
                for message in update.get("messages", []):
                    for call in getattr(message, "tool_calls", []) or []:
                        name = call.get("name", "")
                        if name == "AskAnswer":
                            continue
                        tool_calls += 1
                        if tool_calls > self.settings.chat_max_tool_calls:
                            raise AgentLimitReached(
                                f"Ask mode reached {self.settings.chat_max_tool_calls} tool calls"
                            )
                        args = call.get("args") or {}
                        on_event(AgentEvent(text=self._activity(name, args), tool=name))
        if structured is None:
            raise RuntimeError("Ask mode returned no structured answer")
        verified = verify_citations(structured, self.graph, self.documents)
        return verified.model_copy(
            update={"model": self.settings.chat_model, "tool_calls": tool_calls}
        )

"""Durable JSON persistence for project chat threads.

Chat history is intentionally kept outside the graph.  A thread is a small,
append-oriented JSON document, which makes it easy for the API and the UI to
inspect without coupling either one to a graph backend.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, overload

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ask import AgentEvent
from .citations import VerifiedAnswer

_HEX_ID = re.compile(r"[0-9a-f]{32}\Z")
_PROJECT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_TITLE_LIMIT = 120


def _now() -> datetime:
    return datetime.now(UTC)


def _short_title(value: str) -> str:
    """Trim a title to a stable, UI-friendly single line."""

    value = " ".join(value.split())
    if len(value) <= _TITLE_LIMIT:
        return value
    return value[: _TITLE_LIMIT - 1].rstrip() + "…"


def _validate_thread_id(thread_id: str) -> str:
    if not isinstance(thread_id, str) or _HEX_ID.fullmatch(thread_id) is None:
        raise ValueError("thread_id must be a 32-character lowercase hexadecimal id")
    return thread_id


def _validate_project_id(project_id: str) -> str:
    # Project ids are slugs minted by the graph, rather than UUIDs.  They still
    # need a strict path-safe check because they form a directory component.
    if not isinstance(project_id, str) or _PROJECT_ID.fullmatch(project_id) is None:
        raise ValueError("project_id must be a path-safe identifier")
    return project_id


class ChatMessage(BaseModel):
    """One user or assistant message in a persisted thread."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    role: Literal["user", "assistant"]
    content: str
    answer: VerifiedAnswer | None = None
    events: list[AgentEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    error: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_thread_id(value)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("content must be a string")
        return value

    @field_validator("events")
    @classmethod
    def validate_events_for_role(cls, value: list[AgentEvent], info: object) -> list[AgentEvent]:
        # Pydantic does not expose the already parsed role through a stable
        # public type here on every supported 2.x release.  The API enforces
        # this invariant when constructing messages; retaining a permissive
        # validator keeps old thread files readable.
        return value


class Thread(BaseModel):
    """A persisted conversation belonging to one project."""

    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    title: str = ""
    created_at: datetime
    updated_at: datetime
    messages: list[ChatMessage] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_thread_id(value)

    @field_validator("project_id")
    @classmethod
    def validate_project(cls, value: str) -> str:
        return _validate_project_id(value)


class ThreadSummary(BaseModel):
    """Metadata returned when listing a project's threads."""

    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class ChatStore:
    """Thread-safe, atomic JSON storage for chat threads."""

    def __init__(self, chats_dir: Path) -> None:
        self.root = Path(chats_dir)
        self._lock = threading.RLock()

    def _project_dir(self, project_id: str) -> Path:
        return self.root / _validate_project_id(project_id)

    def _path(self, project_id: str, thread_id: str) -> Path:
        return self._project_dir(project_id) / f"{_validate_thread_id(thread_id)}.json"

    @staticmethod
    def _decode(path: Path) -> Thread | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return Thread.model_validate(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return None

    @staticmethod
    def _write_atomic(path: Path, thread: Thread) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = thread.model_dump_json(indent=2).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            with suppress(FileNotFoundError):
                temporary.unlink()

    @staticmethod
    def _summary(thread: Thread) -> ThreadSummary:
        return ThreadSummary(
            id=thread.id,
            project_id=thread.project_id,
            title=thread.title,
            created_at=thread.created_at,
            updated_at=thread.updated_at,
            message_count=len(thread.messages),
        )

    def create(self, project_id: str, title: str | None = None) -> Thread:
        """Create and persist an empty thread for ``project_id``."""

        project_id = _validate_project_id(project_id)
        now = _now()
        thread = Thread(
            id=uuid.uuid4().hex,
            project_id=project_id,
            title=_short_title(title) if title else "",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            path = self._path(project_id, thread.id)
            self._write_atomic(path, thread)
        return thread

    def list(self, project_id: str) -> list[ThreadSummary]:
        """Return valid threads in newest-first order."""

        project_id = _validate_project_id(project_id)
        directory = self._project_dir(project_id)
        with self._lock:
            if not directory.is_dir():
                return []
            threads: list[Thread] = []
            for path in directory.glob("*.json"):
                try:
                    thread_id = _validate_thread_id(path.stem)
                except ValueError:
                    continue
                thread = self._decode(path)
                if thread is None or thread.id != thread_id or thread.project_id != project_id:
                    continue
                threads.append(thread)
        threads.sort(key=lambda thread: (thread.updated_at, thread.id), reverse=True)
        return [self._summary(thread) for thread in threads]

    @overload
    def get(self, project_id: str, thread_id: str) -> Thread | None: ...

    @overload
    def get(self, thread_id: str) -> Thread | None: ...

    def get(self, project_id: str, thread_id: str | None = None) -> Thread | None:
        """Load one thread, optionally searching all project directories."""

        if thread_id is None:
            thread_id = _validate_thread_id(project_id)
            with self._lock:
                if not self.root.is_dir():
                    return None
                for project_dir in self.root.iterdir():
                    if not project_dir.is_dir() or _PROJECT_ID.fullmatch(project_dir.name) is None:
                        continue
                    thread = self._decode(project_dir / f"{thread_id}.json")
                    if thread is not None and thread.id == thread_id:
                        return thread
            return None

        project_id = _validate_project_id(project_id)
        thread_id = _validate_thread_id(thread_id)
        with self._lock:
            path = self._path(project_id, thread_id)
            thread = self._decode(path) if path.is_file() else None
            if thread is None or thread.project_id != project_id or thread.id != thread_id:
                return None
            return thread

    def append(
        self,
        project_id: str,
        thread_id: str,
        message: ChatMessage,
    ) -> Thread:
        """Append a message and atomically return the updated thread."""

        project_id = _validate_project_id(project_id)
        thread_id = _validate_thread_id(thread_id)
        if not isinstance(message, ChatMessage):
            message = ChatMessage.model_validate(message)
        if message.role == "user" and message.answer is not None:
            raise ValueError("user messages cannot contain an answer")
        if message.role == "user" and message.events:
            raise ValueError("user messages cannot contain agent events")
        with self._lock:
            path = self._path(project_id, thread_id)
            thread = self._decode(path) if path.is_file() else None
            if thread is None or thread.project_id != project_id or thread.id != thread_id:
                return None  # type: ignore[return-value]
            if message.role == "user" and not thread.title:
                thread.title = _short_title(message.content)
            thread.messages.append(message)
            thread.updated_at = _now()
            self._write_atomic(path, thread)
            return thread

    def delete(self, project_id: str, thread_id: str) -> bool:
        """Delete one thread, returning whether a file was removed."""

        project_id = _validate_project_id(project_id)
        thread_id = _validate_thread_id(thread_id)
        with self._lock:
            path = self._path(project_id, thread_id)
            thread = self._decode(path) if path.is_file() else None
            if thread is None or thread.project_id != project_id or thread.id != thread_id:
                return False
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            return True


__all__ = ["ChatMessage", "ChatStore", "Thread", "ThreadSummary"]

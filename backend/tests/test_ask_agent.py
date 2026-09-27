"""End-to-end Ask mode tests with a scripted, offline chat model."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from portolan.agent.ask import AgentLimitReached, AskAgent
from portolan.documents.store import DocumentStore
from portolan.graph.memory import InMemoryResearchGraph
from portolan.graph.models import Inclusion, WorkNode
from portolan.settings import Settings


class ScriptedModel(FakeMessagesListChatModel):
    model_name: str = "scripted"
    seen: list[list[Any]] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
        return self

    def _generate(self, messages: list[Any], **kwargs: Any) -> Any:
        self.seen.append(messages)
        return super()._generate(messages, **kwargs)


def _pdf(*page_texts: str) -> bytes:
    writer = PdfWriter()
    for text in page_texts:
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


def _setup(tmp_path: Path) -> tuple[InMemoryResearchGraph, DocumentStore, Settings, str, str, str]:
    graph = InMemoryResearchGraph()
    project = graph.create_project("Ask test")
    documents = DocumentStore(tmp_path / "documents")
    record = documents.put_pdf(
        _pdf("Introduction to speculative decoding", "Speculative decoding improves throughput."),
        source_url="https://example.org/paper.pdf",
        source="test",
    )
    work = graph.upsert_work(
        WorkNode(title="Speculative decoding", year=2024, document_sha256=record.sha256)
    )
    assert work.id is not None
    graph.include_work(Inclusion(project_id=project.id, work_id=work.id, discovered_via="seed"))
    settings = Settings(store="memory", data_dir=tmp_path, chat_max_tool_calls=4)
    path = f"/{record.sha256[:2]}/{record.sha256}/paper.txt"
    return graph, documents, settings, project.id, work.id, path


def _call(name: str, args: dict[str, Any], number: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{number}"}])


def test_ask_reads_paper_and_verifies_quote(tmp_path: Path) -> None:
    graph, documents, settings, project_id, work_id, path = _setup(tmp_path)
    model = ScriptedModel(
        responses=[
            _call("search_papers", {"query": "speculative decoding"}, 1),
            _call("grep", {"pattern": "throughput", "path": path, "output_mode": "content"}, 2),
            _call("read_file", {"file_path": path, "offset": 2, "limit": 4}, 3),
            _call(
                "AskAnswer",
                {
                    "answer_markdown": "Speculative decoding improves throughput.[1]",
                    "citations": [
                        {
                            "marker": 1,
                            "work_id": work_id,
                            "quote": "Speculative decoding improves throughput.",
                            "page": 2,
                        }
                    ],
                    "unsupported": [],
                },
                4,
            ),
        ]
    )
    agent = AskAgent(graph, documents, settings, model=model, project_id=project_id)
    events = []
    answer = agent.run("Does speculative decoding improve throughput?", [], events.append)
    assert answer.citations[0].verified
    assert answer.citations[0].page == 2
    assert answer.tool_calls == 3
    assert [event.tool for event in events] == ["search_papers", "grep", "read_file"]
    assert all("execute" not in event.text for event in events)


def test_write_is_denied_and_document_unchanged(tmp_path: Path) -> None:
    graph, documents, settings, project_id, _, path = _setup(tmp_path)
    original = documents.text_path(path.split("/")[2]).read_text()
    model = ScriptedModel(
        responses=[
            _call("write_file", {"file_path": path, "content": "tampered"}, 1),
            _call(
                "AskAnswer",
                {"answer_markdown": "No supported answer.", "citations": [], "unsupported": []},
                2,
            ),
        ]
    )
    agent = AskAgent(graph, documents, settings, model=model, project_id=project_id)
    answer = agent.run("What is in the paper?", [], lambda event: None)
    assert answer.tool_calls == 1
    assert any(
        isinstance(message, ToolMessage) and "permission" in str(message.content).lower()
        for batch in model.seen
        for message in batch
    )
    assert documents.text_path(path.split("/")[2]).read_text() == original


def test_tool_call_limit_stops_run(tmp_path: Path) -> None:
    graph, documents, settings, project_id, _, _ = _setup(tmp_path)
    settings = Settings(store="memory", data_dir=tmp_path, chat_max_tool_calls=1)
    model = ScriptedModel(
        responses=[
            _call("search_papers", {"query": "speculative"}, 1),
            _call("search_papers", {"query": "decoding"}, 2),
        ]
    )
    agent = AskAgent(graph, documents, settings, model=model, project_id=project_id)
    with pytest.raises(AgentLimitReached):
        agent.run("What does the paper say?", [], lambda event: None)

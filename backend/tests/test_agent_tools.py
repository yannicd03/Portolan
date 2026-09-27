"""Offline checks for the graph tools exposed to Ask mode."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from portolan.agent.tools import build_graph_tools
from portolan.documents.store import DocumentStore
from portolan.graph.memory import InMemoryResearchGraph
from portolan.graph.models import AuthorNode, ConceptNode, Inclusion, WorkNode


def make_pdf(*page_texts: str) -> bytes:
    """Build a small text PDF without network access."""

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


def _fixture(tmp_path: Path) -> tuple[InMemoryResearchGraph, DocumentStore, str, str, str]:
    graph = InMemoryResearchGraph()
    project = graph.create_project("Graph methods")
    outside = graph.create_project("Outside project")
    documents = DocumentStore(tmp_path)

    primary = graph.upsert_work(
        WorkNode(
            title="Graph networks for papers",
            year=2024,
            abstract="We study graph methods for citation networks.",
            venue="Journal of Maps",
            doi="10.1000/graph",
            cited_by_count=12,
        )
    )
    cited = graph.upsert_work(
        WorkNode(
            title="Citation networks",
            year=2021,
            abstract="A network view of citations.",
            cited_by_count=4,
        )
    )
    hidden = graph.upsert_work(
        WorkNode(
            title="Graph methods outside",
            year=2020,
            abstract="This work is outside the selected project.",
            cited_by_count=99,
        )
    )
    graph.include_work(Inclusion(project_id=project.id, work_id=primary.id, discovered_via="seed"))
    graph.include_work(Inclusion(project_id=project.id, work_id=cited.id, discovered_via="search"))
    graph.include_work(Inclusion(project_id=outside.id, work_id=hidden.id, discovered_via="seed"))
    graph.add_citation(primary.id, cited.id)

    author = graph.upsert_author(AuthorNode(id="author:ada", name="Ada Lovelace"))
    concept = graph.upsert_concept(
        ConceptNode(id="concept:graph", label="Graph methods", aliases=["networks"])
    )
    graph.set_authors(primary.id, [(author.id, 1)])
    graph.set_concepts(primary.id, [(concept.id, 0.9)])
    record = documents.put_pdf(
        make_pdf("Graph networks are useful.", "Citation networks support this result."),
        source_url="https://example.org/paper.pdf",
        source="test",
    )
    graph.set_document(primary.id, record.sha256, record.source_url)
    return graph, documents, project.id, primary.id, cited.id


def _tools(tmp_path: Path) -> tuple[dict[str, object], InMemoryResearchGraph, str, str, str]:
    graph, documents, project_id, primary_id, cited_id = _fixture(tmp_path)
    tools = build_graph_tools(graph, project_id, documents)
    return {item.name: item for item in tools}, graph, primary_id, cited_id, project_id


def test_tools_search_and_project_scoping(tmp_path: Path) -> None:
    tools, _graph, primary_id, _cited_id, _project_id = _tools(tmp_path)

    search = tools["search_papers"].invoke({"query": "graph"})
    assert primary_id in search
    assert "year=2024" in search
    assert "text_path=/" in search
    assert "outside" not in search

    overview = tools["project_overview"].invoke({})
    assert "works=2" in overview
    assert "concept:graph" in overview
    assert primary_id in overview
    assert "Graph methods outside" not in overview


def test_tools_metadata_neighbors_author_and_concept(tmp_path: Path) -> None:
    tools, _graph, primary_id, cited_id, _project_id = _tools(tmp_path)

    info = tools["paper_info"].invoke({"work_id": primary_id})
    assert primary_id in info
    assert "year=2024" in info
    assert "Ada Lovelace" in info
    assert "Graph methods" in info
    assert "10.1000/graph" in info
    assert "pages=2" in info

    neighbors = tools["citation_neighbors"].invoke({"work_id": primary_id})
    assert cited_id in neighbors
    assert "Cites:" in neighbors
    assert "Cited by:" in neighbors

    by_concept = tools["papers_by_concept"].invoke({"concept": "NETWORK"})
    assert primary_id in by_concept
    by_author = tools["papers_by_author"].invoke({"author": "ada lov"})
    assert primary_id in by_author


def test_tools_unknown_ids_are_friendly(tmp_path: Path) -> None:
    tools, _graph, _primary_id, _cited_id, _project_id = _tools(tmp_path)

    assert "No paper" in tools["paper_info"].invoke({"work_id": "missing"})
    assert "No paper" in tools["citation_neighbors"].invoke({"work_id": "missing"})
    assert "No concept" in tools["papers_by_concept"].invoke({"concept": "missing"})
    assert "No author" in tools["papers_by_author"].invoke({"author": "missing"})

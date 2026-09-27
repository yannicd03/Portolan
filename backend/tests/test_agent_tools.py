"""Offline checks for the graph tools exposed to Ask mode."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
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


def make_bookmarked_pdf(bookmarks: list[tuple[str, int, int]]) -> bytes:
    """Add a nested bookmark tree (title, level, zero-based page) to a text PDF."""

    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf("Intro text.", "Method text."))))
    parents: dict[int, object] = {}
    for title, level, page in bookmarks:
        parents[level] = writer.add_outline_item(title, page, parent=parents.get(level - 1))
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _attach_pdf(
    graph: InMemoryResearchGraph, documents: DocumentStore, project_id: str, pdf: bytes, title: str
) -> str:
    work = graph.upsert_work(WorkNode(title=title, year=2023, abstract="An abstract."))
    graph.include_work(Inclusion(project_id=project_id, work_id=work.id, discovered_via="seed"))
    record = documents.put_pdf(pdf, source_url="https://example.org/x.pdf", source="test")
    graph.set_document(work.id, record.sha256, record.source_url)
    return work.id


def test_paper_outline_from_pdf_bookmarks(tmp_path: Path) -> None:
    graph, documents, project_id, _primary_id, _cited_id = _fixture(tmp_path)
    pdf = make_bookmarked_pdf(
        [("1 Introduction", 1, 0), ("1.1 Motivation", 2, 0), ("2 Methods", 1, 1)]
    )
    work_id = _attach_pdf(graph, documents, project_id, pdf, "Bookmarked paper")
    tools = {item.name: item for item in build_graph_tools(graph, project_id, documents)}

    result = tools["paper_outline"].invoke({"work_id": work_id})
    info = tools["paper_info"].invoke({"work_id": work_id})

    lines = result.splitlines()
    assert lines[0] == "Bookmarked paper"
    text_path = lines[1].split("document=", 1)[1].split(" |", 1)[0]
    assert text_path.endswith("/paper.txt")
    assert f"document={text_path} | pages=2" in info
    assert "pages=2" in lines[1]
    assert "source=pdf_outline" in result
    assert lines[-3:] == ["1 Introduction … p. 1", "  1.1 Motivation … p. 1", "2 Methods … p. 2"]


def test_paper_outline_caps_long_outlines(tmp_path: Path) -> None:
    graph, documents, project_id, _primary_id, _cited_id = _fixture(tmp_path)
    pdf = make_bookmarked_pdf([(f"Section {index}", 1, 0) for index in range(75)])
    work_id = _attach_pdf(graph, documents, project_id, pdf, "Long paper")
    tools = {item.name: item for item in build_graph_tools(graph, project_id, documents)}

    result = tools["paper_outline"].invoke({"work_id": work_id})

    assert "Section 59 … p. 1" in result
    assert "Section 60 …" not in result
    assert result.splitlines()[-1] == "… 15 more sections not shown"


def test_paper_outline_from_detected_headings(tmp_path: Path) -> None:
    graph, documents, project_id, _primary_id, _cited_id = _fixture(tmp_path)
    pdf = make_pdf("Abstract", "1 Introduction", "2 Methods")
    work_id = _attach_pdf(graph, documents, project_id, pdf, "Heading paper")
    tools = {item.name: item for item in build_graph_tools(graph, project_id, documents)}

    result = tools["paper_outline"].invoke({"work_id": work_id})

    assert "pages=3" in result
    assert "source=headings" in result
    assert result.splitlines()[-3:] == [
        "Abstract … p. 1",
        "1 Introduction … p. 2",
        "2 Methods … p. 3",
    ]


def test_paper_outline_without_outline_or_pdf(tmp_path: Path) -> None:
    tools, _graph, primary_id, cited_id, _project_id = _tools(tmp_path)

    # The fixture PDF has plain prose on each page: no bookmarks and no headings.
    no_outline = tools["paper_outline"].invoke({"work_id": primary_id})
    assert no_outline.startswith("Graph networks for papers\ndocument=/")
    assert "No outline; grep the text for section headings instead." in no_outline

    no_pdf = tools["paper_outline"].invoke({"work_id": cited_id})
    assert no_pdf.startswith("Citation networks\n")
    assert "No local PDF" in no_pdf


def test_paper_outline_unknown_and_out_of_project_ids(tmp_path: Path) -> None:
    graph, documents, project_id, _primary_id, _cited_id = _fixture(tmp_path)
    outside = graph.create_project("Another project")
    pdf = make_bookmarked_pdf([("1 Introduction", 1, 0)])
    hidden_id = _attach_pdf(graph, documents, outside.id, pdf, "Hidden bookmarked paper")
    tools = {item.name: item for item in build_graph_tools(graph, project_id, documents)}

    assert "No paper with work_id=missing" in tools["paper_outline"].invoke({"work_id": "missing"})
    hidden = tools["paper_outline"].invoke({"work_id": hidden_id})
    assert hidden == f"No paper with work_id={hidden_id} is in this project."

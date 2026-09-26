"""Store-neutral read models for the API.

The citation map is built from the bibliographic partition's Turtle export, which the
M0 spike verified to be identical for both backends, so this module never branches on
the store.  It is sized for the golden graph; the M2 lens endpoints will replace it with
review-scoped queries.
"""

from __future__ import annotations

from typing import Any

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, RDF

from ..iri import BIBLIO_GRAPH, CITO_NAMESPACE, PTL_NAMESPACE
from ..store import GraphRepository

PTL = Namespace(PTL_NAMESPACE)
CITO = Namespace(CITO_NAMESPACE)


def _literal(graph: Graph, subject: URIRef, predicate: URIRef) -> Any:
    value = graph.value(subject, predicate)
    return value.toPython() if isinstance(value, Literal) else None


def citation_graph(repository: GraphRepository) -> dict[str, list[dict[str, Any]]]:
    """Works as nodes and ``cito:cites`` links between them as edges."""

    graph = Graph()
    turtle = repository.export_turtle(BIBLIO_GRAPH)
    if turtle.strip():
        graph.parse(data=turtle, format="turtle")

    works = sorted(set(graph.subjects(RDF.type, PTL.Work)), key=str)
    work_ids = {str(work) for work in works}
    edges = [
        {"source": str(citing), "target": str(cited)}
        for citing, cited in graph.subject_objects(CITO.cites)
        if str(citing) in work_ids and str(cited) in work_ids
    ]
    edges.sort(key=lambda edge: (edge["source"], edge["target"]))
    cited_by: dict[str, int] = {}
    for edge in edges:
        cited_by[edge["target"]] = cited_by.get(edge["target"], 0) + 1

    nodes = []
    for work in works:
        issued = _literal(graph, work, DCTERMS.issued)
        nodes.append(
            {
                "id": str(work),
                "title": _literal(graph, work, DCTERMS.title) or str(work),
                "year": int(str(issued)[:4]) if issued is not None else None,
                "sourceTier": _literal(graph, work, PTL.sourceTier),
                "isSurvey": bool(_literal(graph, work, PTL.isSurvey)),
                "citedBy": cited_by.get(str(work), 0),
            }
        )
    return {"nodes": nodes, "edges": edges}

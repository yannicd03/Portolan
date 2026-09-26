"""Namespace constants and deterministic instance IRI helpers.

The ontology contract deliberately keeps identifiers human-readable while requiring every
identifier component to be percent-encoded.  The functions in this module are the single
place where that convention is implemented, so the Pydantic layer and either graph store
can use the same identifiers.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, unquote

from rdflib import Namespace

# Contract namespaces.  The ``*_NAMESPACE`` names are plain strings for serializers and
# configuration, while the short names are rdflib Namespace objects for callers building RDF.
PTL_NAMESPACE = "https://w3id.org/portolan/ontology#"
PTLR_NAMESPACE = "https://w3id.org/portolan/id/"
PTLG_NAMESPACE = "https://w3id.org/portolan/graph/"
CITO_NAMESPACE = "http://purl.org/spar/cito/"
FABIO_NAMESPACE = "http://purl.org/spar/fabio/"
SKOS_NAMESPACE = "http://www.w3.org/2004/02/skos/core#"
PROV_NAMESPACE = "http://www.w3.org/ns/prov#"
DCTERMS_NAMESPACE = "http://purl.org/dc/terms/"
FOAF_NAMESPACE = "http://xmlns.com/foaf/0.1/"
RDF_NAMESPACE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS_NAMESPACE = "http://www.w3.org/2000/01/rdf-schema#"
XSD_NAMESPACE = "http://www.w3.org/2001/XMLSchema#"

PTL = Namespace(PTL_NAMESPACE)
PTLR = Namespace(PTLR_NAMESPACE)
PTLG = Namespace(PTLG_NAMESPACE)
CITO = Namespace(CITO_NAMESPACE)
FABIO = Namespace(FABIO_NAMESPACE)
SKOS = Namespace(SKOS_NAMESPACE)
PROV = Namespace(PROV_NAMESPACE)
DCTERMS = Namespace(DCTERMS_NAMESPACE)
FOAF = Namespace(FOAF_NAMESPACE)
RDF = Namespace(RDF_NAMESPACE)
RDFS = Namespace(RDFS_NAMESPACE)
XSD = Namespace(XSD_NAMESPACE)

# Short aliases are useful to code that treats namespace constants as strings.
PTL_NS = PTL_NAMESPACE
PTLR_NS = PTLR_NAMESPACE
PTLG_NS = PTLG_NAMESPACE
CITO_NS = CITO_NAMESPACE
FABIO_NS = FABIO_NAMESPACE
SKOS_NS = SKOS_NAMESPACE
PROV_NS = PROV_NAMESPACE
DCTERMS_NS = DCTERMS_NAMESPACE
FOAF_NS = FOAF_NAMESPACE

BIBLIO_GRAPH = f"{PTLG_NAMESPACE}biblio"
BIBLIO_GRAPH_IRI = BIBLIO_GRAPH

WORK_SCHEMES = ("doi", "arxiv", "openalex", "s2", "url")
AUTHOR_SCHEMES = ("orcid", "openalex", "s2")
ORGANIZATION_SCHEMES = ("ror", "domain")
VENUE_SCHEMES = ("openalex", "issn", "slug")
CONCEPT_KINDS = ("problem", "method", "dataset", "metric")
STATEMENT_KINDS = ("contribution", "result", "claim", "limitation", "futurework")


def percent_encode(value: Any) -> str:
    """Percent-encode one IRI path component, including slashes and percent signs."""

    return quote(str(value), safe="")


def percent_decode(value: Any) -> str:
    """Decode one IRI path component."""

    return unquote(str(value))


def _non_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _iri_value(value: Any) -> str:
    """Return an IRI from a string, rdflib term, or domain model-like object."""

    if hasattr(value, "iri"):
        value = value.iri
    elif isinstance(value, Mapping) and "iri" in value:
        value = value["iri"]
    return str(value)


def _mint(kind: str, *components: Any) -> str:
    encoded = "/".join(percent_encode(component) for component in components)
    return f"{PTLR_NAMESPACE}{kind}/{encoded}"


def _parts(iri: Any, kind: str, expected: int) -> list[str]:
    text = str(iri)
    prefix = f"{PTLR_NAMESPACE}{kind}/"
    if not text.startswith(prefix):
        raise ValueError(f"not an ptl:{kind} IRI: {text!r}")
    parts = text[len(prefix) :].split("/")
    if len(parts) != expected or any(part == "" for part in parts):
        raise ValueError(f"malformed ptl:{kind} IRI: {text!r}")
    return [percent_decode(part) for part in parts]


def _mapping_value(
    identifiers: Any | None,
    *names: str,
) -> Any:
    if identifiers is None:
        return None
    for name in names:
        if isinstance(identifiers, Mapping):
            value = identifiers.get(name)
        else:
            value = getattr(identifiers, name, None)
        if _non_empty(value) is not None:
            return value
    return None


def mint_work_iri(
    identifiers: Any | None = None,
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    openalex_id: str | None = None,
    s2_id: str | None = None,
    url: str | None = None,
) -> str:
    """Mint a Work IRI using the contract's DOI/arXiv/OpenAlex/S2/URL precedence."""

    values = {
        "doi": doi if doi is not None else _mapping_value(identifiers, "doi"),
        "arxiv": arxiv_id
        if arxiv_id is not None
        else _mapping_value(identifiers, "arxiv", "arxiv_id", "arxivId"),
        "openalex": openalex_id
        if openalex_id is not None
        else _mapping_value(identifiers, "openalex", "openalex_id", "openAlexId"),
        "s2": s2_id
        if s2_id is not None
        else _mapping_value(identifiers, "s2", "s2_id", "s2Id", "semantic_scholar_id"),
        "url": url if url is not None else _mapping_value(identifiers, "url"),
    }
    for scheme in WORK_SCHEMES:
        value = _non_empty(values[scheme])
        if value is not None:
            return _mint("work", scheme, value)
    raise ValueError("a Work needs at least one DOI, arXiv, OpenAlex, S2, or URL identifier")


def parse_work_iri(iri: Any) -> dict[str, str]:
    """Parse a Work IRI into its preferred-identifier scheme and decoded identifier."""

    scheme, identifier = _parts(iri, "work", 2)
    if scheme not in WORK_SCHEMES:
        raise ValueError(f"unknown Work identifier scheme: {scheme!r}")
    return {"scheme": scheme, "id": identifier}


def mint_author_iri(
    *,
    orcid: str | None = None,
    openalex_id: str | None = None,
    s2_id: str | None = None,
    identifiers: Any | None = None,
) -> str:
    values = {
        "orcid": orcid if orcid is not None else _mapping_value(identifiers, "orcid"),
        "openalex": openalex_id
        if openalex_id is not None
        else _mapping_value(identifiers, "openalex", "openalex_id", "openAlexId"),
        "s2": s2_id
        if s2_id is not None
        else _mapping_value(identifiers, "s2", "s2_id", "s2Id", "semantic_scholar_id"),
    }
    for scheme in AUTHOR_SCHEMES:
        value = _non_empty(values[scheme])
        if value is not None:
            return _mint("author", scheme, value)
    raise ValueError("an Author needs an ORCID, OpenAlex, or S2 identifier")


def parse_author_iri(iri: Any) -> dict[str, str]:
    scheme, identifier = _parts(iri, "author", 2)
    if scheme not in AUTHOR_SCHEMES:
        raise ValueError(f"unknown Author identifier scheme: {scheme!r}")
    return {"scheme": scheme, "id": identifier}


def mint_organization_iri(
    *,
    ror: str | None = None,
    domain: str | None = None,
    identifiers: Any | None = None,
) -> str:
    values = {
        "ror": ror if ror is not None else _mapping_value(identifiers, "ror"),
        "domain": domain if domain is not None else _mapping_value(identifiers, "domain"),
    }
    for scheme in ORGANIZATION_SCHEMES:
        value = _non_empty(values[scheme])
        if value is not None:
            return _mint("org", scheme, value)
    raise ValueError("an Organization needs a ROR or domain identifier")


def parse_organization_iri(iri: Any) -> dict[str, str]:
    scheme, identifier = _parts(iri, "org", 2)
    if scheme not in ORGANIZATION_SCHEMES:
        raise ValueError(f"unknown Organization identifier scheme: {scheme!r}")
    return {"scheme": scheme, "id": identifier}


def mint_venue_iri(
    *,
    openalex_id: str | None = None,
    issn: str | None = None,
    slug: str | None = None,
    identifiers: Any | None = None,
) -> str:
    values = {
        "openalex": openalex_id
        if openalex_id is not None
        else _mapping_value(identifiers, "openalex", "openalex_id", "openAlexId"),
        "issn": issn if issn is not None else _mapping_value(identifiers, "issn"),
        "slug": slug if slug is not None else _mapping_value(identifiers, "slug"),
    }
    for scheme in VENUE_SCHEMES:
        value = _non_empty(values[scheme])
        if value is not None:
            return _mint("venue", scheme, value)
    raise ValueError("a Venue needs an OpenAlex ID, ISSN, or slug identifier")


def parse_venue_iri(iri: Any) -> dict[str, str]:
    scheme, identifier = _parts(iri, "venue", 2)
    if scheme not in VENUE_SCHEMES:
        raise ValueError(f"unknown Venue identifier scheme: {scheme!r}")
    return {"scheme": scheme, "id": identifier}


def mint_citation_iri(citing_work: Any, cited_work: Any) -> str:
    """Mint the reified citation node from the exact concatenated Work IRIs."""

    digest = hashlib.sha1(
        (_iri_value(citing_work) + _iri_value(cited_work)).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()
    return _mint("citation", digest)


def parse_citation_iri(iri: Any) -> dict[str, str]:
    (digest,) = _parts(iri, "citation", 1)
    if not re.fullmatch(r"[0-9a-f]{40}", digest):
        raise ValueError(f"malformed citation digest: {digest!r}")
    return {"sha1": digest}


def mint_concept_iri(kind: str, slug: str) -> str:
    normalized = str(kind).strip().lower()
    if normalized not in CONCEPT_KINDS:
        raise ValueError(f"concept kind must be one of {CONCEPT_KINDS!r}")
    return _mint("concept", normalized, slug)


def parse_concept_iri(iri: Any) -> dict[str, str]:
    kind, slug = _parts(iri, "concept", 2)
    if kind not in CONCEPT_KINDS:
        raise ValueError(f"unknown concept kind: {kind!r}")
    return {"kind": kind, "slug": slug}


def work_slug(work: Any) -> str:
    """Return the raw, single-component slug used by statement/review-layer IRIs."""

    value = _iri_value(work)
    if value.startswith(f"{PTLR_NAMESPACE}work/"):
        parsed = parse_work_iri(value)
        return f"{parsed['scheme']}-{parsed['id']}"
    return percent_decode(value)


def parse_work_slug(slug: Any) -> dict[str, str] | None:
    value = percent_decode(slug)
    for scheme in WORK_SCHEMES:
        prefix = f"{scheme}-"
        if value.startswith(prefix) and len(value) > len(prefix):
            return {"scheme": scheme, "id": value[len(prefix) :]}
    return None


def mint_statement_iri(statement_kind: str, work: Any, number: int | str) -> str:
    normalized = str(statement_kind).strip().lower()
    if normalized not in STATEMENT_KINDS:
        raise ValueError(f"statement kind must be one of {STATEMENT_KINDS!r}")
    return _mint(normalized, work_slug(work), number)


def parse_statement_iri(iri: Any) -> dict[str, str]:
    text = str(iri)
    if not text.startswith(PTLR_NAMESPACE):
        raise ValueError(f"not a ptl statement IRI: {text!r}")
    parts = text[len(PTLR_NAMESPACE) :].split("/")
    if len(parts) != 3 or parts[0] not in STATEMENT_KINDS:
        raise ValueError(f"malformed statement IRI: {text!r}")
    return {
        "kind": parts[0],
        "work_slug": percent_decode(parts[1]),
        "number": percent_decode(parts[2]),
    }


def mint_contribution_iri(work: Any, number: int | str) -> str:
    return mint_statement_iri("contribution", work, number)


def mint_result_iri(work: Any, number: int | str) -> str:
    return mint_statement_iri("result", work, number)


def mint_claim_iri(work: Any, number: int | str) -> str:
    return mint_statement_iri("claim", work, number)


def mint_limitation_iri(work: Any, number: int | str) -> str:
    return mint_statement_iri("limitation", work, number)


def mint_future_work_iri(work: Any, number: int | str) -> str:
    return mint_statement_iri("futurework", work, number)


# The spelling in the contract is ``futurework``; this alias also follows the Python class name.
mint_futurework_iri = mint_future_work_iri


def mint_evidence_iri(quote_text: str, from_work: Any) -> str:
    digest = hashlib.sha1(
        (str(quote_text) + _iri_value(from_work)).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()
    return _mint("evidence", digest)


def parse_evidence_iri(iri: Any) -> dict[str, str]:
    (digest,) = _parts(iri, "evidence", 1)
    if not re.fullmatch(r"[0-9a-f]{40}", digest):
        raise ValueError(f"malformed evidence digest: {digest!r}")
    return {"sha1": digest}


def mint_extraction_iri(run_id: str) -> str:
    """Mint the provenance activity IRI for an extraction run.

    The vocabulary digest specifies the activity properties but does not give a
    separate row in the instance-IRI table; this stable extension keeps the
    activity addressable without coupling it to a graph-store implementation.
    """

    return _mint("extraction", run_id)


def parse_extraction_iri(iri: Any) -> dict[str, str]:
    (run_id,) = _parts(iri, "extraction", 1)
    return {"run_id": run_id}


def _review_key(review: Any) -> str:
    value = _iri_value(review)
    if value.startswith(f"{PTLR_NAMESPACE}review/"):
        return parse_review_iri(value)["id"]
    return percent_decode(value)


def mint_review_iri(review_id: str) -> str:
    return _mint("review", review_id)


def parse_review_iri(iri: Any) -> dict[str, str]:
    (review_id,) = _parts(iri, "review", 1)
    return {"id": review_id}


def mint_protocol_iri(protocol_id: str) -> str:
    return _mint("protocol", protocol_id)


def mint_review_protocol_iri(protocol_id: str) -> str:
    return mint_protocol_iri(protocol_id)


def parse_protocol_iri(iri: Any) -> dict[str, str]:
    (protocol_id,) = _parts(iri, "protocol", 1)
    return {"id": protocol_id}


def parse_review_protocol_iri(iri: Any) -> dict[str, str]:
    return parse_protocol_iri(iri)


def mint_inclusion_iri(review: Any, work: Any) -> str:
    return _mint("inclusion", _review_key(review), work_slug(work))


def parse_inclusion_iri(iri: Any) -> dict[str, str]:
    review_id, slug = _parts(iri, "inclusion", 2)
    return {"review_id": review_id, "work_slug": slug}


def mint_cluster_iri(review: Any, number: int | str) -> str:
    return _mint("cluster", _review_key(review), number)


def parse_cluster_iri(iri: Any) -> dict[str, str]:
    review_id, number = _parts(iri, "cluster", 2)
    return {"review_id": review_id, "number": number}


def mint_gap_iri(review: Any, number: int | str) -> str:
    return _mint("gap", _review_key(review), number)


def mint_gap_hypothesis_iri(review: Any, number: int | str) -> str:
    return mint_gap_iri(review, number)


def parse_gap_iri(iri: Any) -> dict[str, str]:
    review_id, number = _parts(iri, "gap", 2)
    return {"review_id": review_id, "number": number}


def parse_gap_hypothesis_iri(iri: Any) -> dict[str, str]:
    return parse_gap_iri(iri)


def content_graph_iri(run_id: str) -> str:
    return f"{PTLG_NAMESPACE}content/{percent_encode(run_id)}"


def review_graph_iri(review_id: str) -> str:
    return f"{PTLG_NAMESPACE}review/{percent_encode(review_id)}"


def parse_content_graph_iri(iri: Any) -> dict[str, str]:
    text = str(iri)
    prefix = f"{PTLG_NAMESPACE}content/"
    if not text.startswith(prefix) or "/" in text[len(prefix) :]:
        raise ValueError(f"not an ptlg:content graph IRI: {text!r}")
    return {"run_id": percent_decode(text[len(prefix) :])}


def parse_review_graph_iri(iri: Any) -> dict[str, str]:
    text = str(iri)
    prefix = f"{PTLG_NAMESPACE}review/"
    if not text.startswith(prefix) or "/" in text[len(prefix) :]:
        raise ValueError(f"not an ptlg:review graph IRI: {text!r}")
    return {"review_id": percent_decode(text[len(prefix) :])}


def parse_graph_iri(iri: Any) -> dict[str, str]:
    text = str(iri)
    if text == BIBLIO_GRAPH:
        return {"kind": "biblio"}
    try:
        parsed = parse_content_graph_iri(text)
    except ValueError:
        pass
    else:
        return {"kind": "content", **parsed}
    try:
        parsed = parse_review_graph_iri(text)
    except ValueError:
        pass
    else:
        return {"kind": "review", **parsed}
    raise ValueError(f"unknown ptlg graph IRI: {text!r}")


def expand_citation_function(value: Any) -> str:
    """Expand a contract-prefixed or LPG-local CiTO value to its full IRI."""

    text = str(value)
    if text.startswith(CITO_NAMESPACE):
        return text
    if text.startswith("cito:"):
        return f"{CITO_NAMESPACE}{text.removeprefix('cito:')}"
    return f"{CITO_NAMESPACE}{text}"


__all__ = [
    "PTL",
    "PTLG",
    "PTLR",
    "PTLG_NAMESPACE",
    "PTL_NAMESPACE",
    "PTLR_NAMESPACE",
    "BIBLIO_GRAPH",
    "BIBLIO_GRAPH_IRI",
    "CITO",
    "CITO_NAMESPACE",
    "CONCEPT_KINDS",
    "DCTERMS",
    "DCTERMS_NAMESPACE",
    "FABIO",
    "FABIO_NAMESPACE",
    "FOAF",
    "FOAF_NAMESPACE",
    "RDF",
    "RDFS",
    "PROV",
    "SKOS",
    "XSD",
    "STATEMENT_KINDS",
    "AUTHOR_SCHEMES",
    "ORGANIZATION_SCHEMES",
    "VENUE_SCHEMES",
    "WORK_SCHEMES",
    "content_graph_iri",
    "expand_citation_function",
    "mint_author_iri",
    "mint_biblio_graph_iri",
    "mint_citation_iri",
    "mint_claim_iri",
    "mint_cluster_iri",
    "mint_concept_iri",
    "mint_contribution_iri",
    "mint_evidence_iri",
    "mint_extraction_iri",
    "mint_future_work_iri",
    "mint_futurework_iri",
    "mint_gap_hypothesis_iri",
    "mint_gap_iri",
    "mint_inclusion_iri",
    "mint_limitation_iri",
    "mint_organization_iri",
    "mint_protocol_iri",
    "mint_result_iri",
    "mint_review_graph_iri",
    "mint_review_iri",
    "mint_review_protocol_iri",
    "mint_statement_iri",
    "mint_venue_iri",
    "mint_work_iri",
    "parse_author_iri",
    "parse_citation_iri",
    "parse_cluster_iri",
    "parse_concept_iri",
    "parse_content_graph_iri",
    "parse_evidence_iri",
    "parse_extraction_iri",
    "parse_gap_hypothesis_iri",
    "parse_gap_iri",
    "parse_graph_iri",
    "parse_inclusion_iri",
    "parse_organization_iri",
    "parse_protocol_iri",
    "parse_review_graph_iri",
    "parse_review_iri",
    "parse_review_protocol_iri",
    "parse_statement_iri",
    "parse_venue_iri",
    "parse_work_iri",
    "parse_work_slug",
    "percent_decode",
    "percent_encode",
    "review_graph_iri",
    "work_slug",
]


def mint_biblio_graph_iri() -> str:
    return BIBLIO_GRAPH


def mint_review_graph_iri(review_id: str) -> str:
    return review_graph_iri(review_id)

"""Offline contract tests for the RDF binding of the ``ptl:`` ontology."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pyshacl import validate
from rdflib import Dataset, Graph, Namespace
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD
from rdflib.plugins.sparql import prepareQuery

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY_ROOT = REPOSITORY_ROOT / "ontology"
SPARQL_ROOT = ONTOLOGY_ROOT / "competency" / "sparql"

PTL = Namespace("https://w3id.org/portolan/ontology#")
FABIO = Namespace("http://purl.org/spar/fabio/")
SH = Namespace("http://www.w3.org/ns/shacl#")


# These are the contract-local terms named in ontology/README.md.  External
# terms used for alignment (DCTERMS, SKOS, PROV-O, CiTO, FaBiO, and FOAF) are
# deliberately not required to be redeclared in portolan.ttl.
CONTRACT_CLASSES = (
    "Work",
    "Author",
    "Organization",
    "Venue",
    "Citation",
    "Problem",
    "Method",
    "Dataset",
    "Metric",
    "Contribution",
    "Result",
    "Claim",
    "Limitation",
    "FutureWork",
    "Review",
    "ReviewProtocol",
    "Inclusion",
    "Cluster",
    "ClusterPair",
    "GapHypothesis",
    "Evidence",
)

CONTRACT_PROPERTIES = (
    # Bibliographic layer.
    "workType",
    "isSurvey",
    "doi",
    "arxivId",
    "openAlexId",
    "s2Id",
    "url",
    "sourceTier",
    "oaStatus",
    "fullTextAvailable",
    "globalCitationCount",
    "asOf",
    "tldr",
    "sourceApi",
    "retrievedAt",
    "authoredBy",
    "authorPosition",
    "affiliatedWith",
    "publishedIn",
    "publishedBy",
    "hasVersion",
    "isCanonicalVersion",
    "citingWork",
    "citedWork",
    "citationFunction",
    "isInfluential",
    "citationContext",
    # Content layer.
    "extendsMethod",
    "firstSeenIn",
    "metricDirection",
    "ofWork",
    "addresses",
    "proposes",
    "uses",
    "evaluatesOn",
    "reports",
    "contributionKind",
    "ofMethod",
    "onDataset",
    "withMetric",
    "value",
    "unit",
    "setting",
    "claimsSOTA",
    "claimText",
    "about",
    "supportsClaim",
    "contradictsClaim",
    "limitationText",
    "limitationOf",
    "futureWorkText",
    "hasEvidence",
    "addressesLimitation",
    # Review layer.
    "seedKind",
    "seedValue",
    "hasProtocol",
    "startedAt",
    "endedAt",
    "budgetWorks",
    "budgetUsd",
    "spendUsd",
    "scopeStatement",
    "inclusionCriterion",
    "exclusionCriterion",
    "fromYear",
    "toYear",
    "allowedTier",
    "maxWorks",
    "maxSnowballDepth",
    "ofReview",
    "decision",
    "stage",
    "reason",
    "discoveredVia",
    "relevance",
    "pageRank",
    "betweenness",
    "onMainPath",
    "citationVelocity",
    "frontierScore",
    "frontierComponentVelocity",
    "frontierComponentMainPathLeaf",
    "frontierComponentClusterGrowth",
    "frontierComponentConceptNovelty",
    "frontierComponentSotaClaim",
    "frontierComponentNotPeerReviewed",
    "role",
    "inCluster",
    "level",
    "parentCluster",
    "summary",
    "growthRate",
    "topConcept",
    "clusterA",
    "clusterB",
    "semanticSimilarity",
    "crossCitationCount",
    "gapType",
    "statement",
    "supportedBy",
    "confidence",
    "verificationOutcome",
    "userStatus",
    "userNote",
    # Provenance and the organization allowlist hook required by shape 2.
    "quote",
    "locator",
    "fromWork",
    "fromSourceKind",
    "model",
    "promptVersion",
    "ontologyVersion",
    "allowlistedDomain",
    # orgKind and venueKind occur in the contract's closed-enumeration table.
    "orgKind",
    "venueKind",
)

# The README mentions ptl:Authorship only to say that it is not modelled in
# v0.1, so it is intentionally absent from CONTRACT_CLASSES.

_CLASS_DECLARATION_TYPES = frozenset({OWL.Class, RDFS.Class})
_PROPERTY_DECLARATION_TYPES = frozenset(
    {RDF.Property, OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty}
)


def _parse_turtle(path: Path) -> Graph:
    graph = Graph()
    graph.parse(str(path), format="turtle")
    return graph


def _parse_example(path: Path) -> Dataset:
    """Parse an example as TriG first, with Turtle as a compatibility fallback."""

    dataset = Dataset()
    try:
        dataset.parse(str(path), format="trig")
    except Exception:
        dataset = Dataset()
        try:
            dataset.parse(str(path), format="turtle")
        except Exception as turtle_error:
            raise AssertionError(f"could not parse {path}") from turtle_error
    return dataset


def _union_graph(dataset: Dataset) -> Graph:
    """Make a graph containing the triples from the default and named graphs."""

    graph = Graph()
    for subject, predicate, object_, _context in dataset.quads((None, None, None, None)):
        graph.add((subject, predicate, object_))
    return graph


def _shape_sources(shapes: Graph, node_shape) -> set:
    """Return source-shape terms pySHACL may use for a top-level NodeShape."""

    sources = {node_shape}
    pending = [node_shape]
    # Property and SPARQL constraint nodes are the usual sh:sourceShape values;
    # node/qualified shapes cover the other standard report variants.
    constraint_predicates = (SH.property, SH.sparql, SH.node, SH.qualifiedValueShape)
    while pending:
        current = pending.pop()
        for predicate in constraint_predicates:
            for child in shapes.objects(current, predicate):
                if child not in sources:
                    sources.add(child)
                    pending.append(child)
    return sources


def _validation_results(report_graph: Graph) -> list:
    """Return violation results without depending on report text formatting."""

    results = []
    for result in report_graph.subjects(SH.focusNode, None):
        severity = report_graph.value(result, SH.resultSeverity)
        if severity in (None, SH.Violation):
            results.append(result)
    return results


def _shape_has_violation(
    shapes: Graph, data_graph: Graph, report_graph: Graph, node_shape, results: list
) -> bool:
    source_terms = _shape_sources(shapes, node_shape)
    if any(report_graph.value(result, SH.sourceShape) in source_terms for result in results):
        return True

    # The RDF report vocabulary normally includes sh:sourceShape.  If a
    # compatible validator omits it, fall back to the target class/focus node
    # relation rather than parsing implementation-specific report text.
    if results and all(report_graph.value(result, SH.sourceShape) is None for result in results):
        target_classes = set(shapes.objects(node_shape, SH.targetClass))
        return any(
            (focus_node, RDF.type, target_class) in data_graph
            for result in results
            for focus_node in report_graph.objects(result, SH.focusNode)
            for target_class in target_classes
        )
    return False


@pytest.fixture(scope="module")
def parsed_ontology():
    """Parse all RDF inputs once for the module's offline checks."""

    vocabulary = _parse_turtle(ONTOLOGY_ROOT / "portolan.ttl")
    shapes = _parse_turtle(ONTOLOGY_ROOT / "shapes.ttl")
    valid = _parse_example(ONTOLOGY_ROOT / "examples" / "minimal_valid.ttl")
    invalid = _parse_example(ONTOLOGY_ROOT / "examples" / "minimal_invalid.ttl")
    return vocabulary, shapes, valid, invalid


def test_rdf_inputs_parse_and_declare_contract_terms(parsed_ontology) -> None:
    vocabulary, _shapes, _valid, _invalid = parsed_ontology

    for class_name in CONTRACT_CLASSES:
        term = PTL[class_name]
        declared_types = set(vocabulary.objects(term, RDF.type))
        assert declared_types & _CLASS_DECLARATION_TYPES, (
            f"{term} is not declared as an RDFS/OWL class in ontology/portolan.ttl"
        )

    for property_name in CONTRACT_PROPERTIES:
        term = PTL[property_name]
        declared_types = set(vocabulary.objects(term, RDF.type))
        assert declared_types & _PROPERTY_DECLARATION_TYPES, (
            f"{term} is not declared as an RDF/OWL property in ontology/portolan.ttl"
        )


def test_v02_amendments_have_ranges_alignments_and_shacl_constraints(parsed_ontology) -> None:
    vocabulary, shapes, _valid, _invalid = parsed_ontology

    ontology_iri = PTL[""]
    assert (ontology_iri, OWL.versionInfo, None) in vocabulary
    assert str(vocabulary.value(ontology_iri, OWL.versionInfo)) == "0.2"

    supported_range = vocabulary.value(PTL.supportedBy, RDFS.range)
    assert supported_range is not None
    assert list(vocabulary.items(vocabulary.value(supported_range, OWL.unionOf))) == [
        PTL.Limitation,
        PTL.FutureWork,
        PTL.Claim,
        PTL.Result,
        PTL.Work,
    ]

    assert (PTL.addressesLimitation, RDFS.domain, PTL.Contribution) in vocabulary
    assert (PTL.addressesLimitation, RDFS.range, PTL.Limitation) in vocabulary

    frontier_properties = (
        "frontierComponentVelocity",
        "frontierComponentMainPathLeaf",
        "frontierComponentClusterGrowth",
        "frontierComponentConceptNovelty",
        "frontierComponentSotaClaim",
        "frontierComponentNotPeerReviewed",
    )
    assert not list(vocabulary.triples((PTL.frontierComponent, None, None)))
    for property_name in frontier_properties:
        property_iri = PTL[property_name]
        assert (property_iri, RDFS.domain, PTL.Inclusion) in vocabulary
        assert (property_iri, RDFS.range, XSD.decimal) in vocabulary
        property_shapes = [
            shape
            for shape in shapes.objects(PTL.InclusionShape, SH.property)
            if shapes.value(shape, SH.path) == property_iri
        ]
        assert len(property_shapes) == 1, f"missing Inclusion constraint for {property_name}"
        property_shape = property_shapes[0]
        assert shapes.value(property_shape, SH.datatype) == XSD.decimal
        assert str(shapes.value(property_shape, SH.minInclusive)) == "0"
        assert str(shapes.value(property_shape, SH.maxInclusive)) == "1"

    metric_shapes = [
        shape
        for shape in shapes.subjects(SH.targetClass, PTL.Metric)
        if (shape, RDF.type, SH.NodeShape) in shapes
    ]
    assert metric_shapes, "v0.2 needs a target shape for Metric"
    metric_direction_shapes = [
        shape
        for shape in shapes.objects(metric_shapes[0], SH.property)
        if shapes.value(shape, SH.path) == PTL.metricDirection
    ]
    assert len(metric_direction_shapes) == 1
    metric_direction_shape = metric_direction_shapes[0]
    assert str(shapes.value(metric_direction_shape, SH.minCount)) == "1"
    assert str(shapes.value(metric_direction_shape, SH.maxCount)) == "1"
    direction_values = list(shapes.items(shapes.value(metric_direction_shape, SH["in"])))
    assert [str(value) for value in direction_values] == [
        "higherIsBetter",
        "lowerIsBetter",
    ]

    assert (PTL.seedValue, RDFS.range, XSD.string) in vocabulary
    assert (PTL.Venue, SKOS.closeMatch, FABIO.Journal) not in vocabulary
    assert any(
        "only when" in str(comment) for comment in vocabulary.objects(PTL.Venue, RDFS.comment)
    )

    for property_name, range_iri in {
        "clusterA": PTL.Cluster,
        "clusterB": PTL.Cluster,
        "semanticSimilarity": XSD.decimal,
        "crossCitationCount": XSD.integer,
    }.items():
        property_iri = PTL[property_name]
        assert (property_iri, RDFS.domain, PTL.ClusterPair) in vocabulary
        assert (property_iri, RDFS.range, range_iri) in vocabulary


def test_minimal_valid_conforms(parsed_ontology) -> None:
    _vocabulary, shapes, valid, _invalid = parsed_ontology
    conforms, _report_graph, report_text = validate(
        data_graph=_union_graph(valid),
        shacl_graph=shapes,
        advanced=True,
    )
    assert conforms, report_text


def test_minimal_invalid_violates_each_shape(parsed_ontology) -> None:
    _vocabulary, shapes, _valid, invalid = parsed_ontology
    invalid_graph = _union_graph(invalid)
    conforms, report_graph, report_text = validate(
        data_graph=invalid_graph,
        shacl_graph=shapes,
        advanced=True,
    )
    assert not conforms, report_text

    # The nine contract shapes are identified by their target classes rather
    # than by implementation-specific shape IRIs or blank-node identifiers.
    target_shapes = sorted(set(shapes.subjects(SH.targetClass, None)), key=str)
    assert len(target_shapes) == 9, (
        "ontology/shapes.ttl must expose the nine v0.2 contract shapes through sh:targetClass"
    )
    results = _validation_results(report_graph)
    assert results, "pySHACL returned no RDF validation results"
    for node_shape in target_shapes:
        assert _shape_has_violation(shapes, invalid_graph, report_graph, node_shape, results), (
            f"minimal_invalid.ttl does not report a violation for target shape {node_shape}"
        )


def test_competency_queries_are_numbered_parameterized_and_runnable(parsed_ontology) -> None:
    _vocabulary, _shapes, valid, _invalid = parsed_ontology
    query_files = sorted(SPARQL_ROOT.glob("*.rq"))
    assert len(query_files) == 14, "expected exactly fourteen SPARQL competency-question files"

    query_numbers = []
    name_pattern = re.compile(r"^cq(0[1-9]|1[0-4])_[a-z0-9]+(?:_[a-z0-9]+)*\.rq$")
    for query_file in query_files:
        match = name_pattern.fullmatch(query_file.name)
        assert match, f"unexpected competency-query filename: {query_file.name}"
        query_numbers.append(int(match.group(1)))

        query_text = query_file.read_text(encoding="utf-8")
        assert re.search(r"(?m)^# CQ(?:0[1-9]|1[0-4])\s+—\s+.+$", query_text), (
            f"{query_file.name} is missing its CQ header"
        )
        assert re.search(r"(?m)^# PARAMS:\s*\S.*$", query_text), (
            f"{query_file.name} is missing its PARAMS header"
        )
        where_open = re.search(r"(?is)\bWHERE\s*\{", query_text)
        assert where_open, f"{query_file.name} has no WHERE group"
        start_marker = query_text.find("# PARAM-BLOCK")
        end_marker = query_text.find("# /PARAM-BLOCK")
        assert 0 <= start_marker < end_marker, (
            f"{query_file.name} has an invalid PARAM-BLOCK marker pair"
        )
        assert query_text[where_open.end() :].lstrip().startswith("# PARAM-BLOCK"), (
            f"{query_file.name} PARAM-BLOCK is not immediately inside WHERE"
        )
        parameter_block = query_text[start_marker:end_marker]
        assert re.search(r"\bVALUES\b", parameter_block), (
            f"{query_file.name} PARAM-BLOCK has no VALUES clause"
        )

        prepared_query = prepareQuery(query_text)
        # Materialize the result so deferred query evaluation is also tested.
        list(valid.query(prepared_query))

    assert query_numbers == list(range(1, 15)), "CQ files must be numbered cq01 through cq14"

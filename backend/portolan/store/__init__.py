"""Graph repository contracts and validation helpers."""

from .neo4j_store import Neo4jRepository, Neo4jStore
from .oxigraph_store import OxigraphRepository, OxigraphStore
from .repository import (
    GraphRepository,
    GraphSelection,
    QueryResult,
    SubgraphResult,
    ValidationIssue,
    ValidationReport,
)
from .validation import DEFAULT_SHAPES_PATH, resolve_shapes_path, validate, validate_graph

__all__ = [
    "DEFAULT_SHAPES_PATH",
    "GraphRepository",
    "GraphSelection",
    "QueryResult",
    "SubgraphResult",
    "ValidationIssue",
    "ValidationReport",
    "Neo4jRepository",
    "Neo4jStore",
    "OxigraphRepository",
    "OxigraphStore",
    "resolve_shapes_path",
    "validate",
    "validate_graph",
]

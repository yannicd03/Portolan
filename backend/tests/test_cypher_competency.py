"""Checks for the Neo4j competency-question binding."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CYPHER_DIR = REPO_ROOT / "ontology" / "competency" / "cypher"
BINDING_PATH = REPO_ROOT / "ontology" / "lpg-binding.md"
CONSTRAINTS_PATH = REPO_ROOT / "ontology" / "neo4j_constraints.cypher"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.spike.run_spike import _is_enterprise_only_error  # noqa: E402

CYPHER_FILE_RE = re.compile(r"^cq(?P<number>\d{2})_(?P<slug>.+)\.cypher$")
HEADER_RE = re.compile(r"^// CQ(?P<number>\d{2}) — (?P<question>\S.*)$")
PARAMS_RE = re.compile(r"^// PARAMS:\s*(?P<body>.*)$")
DEFAULTS_RE = re.compile(r"^// DEFAULTS:\s*(?P<body>\{.*\})\s*$")
PARAMETER_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
COLON_NAME_RE = re.compile(r":([A-Za-z][A-Za-z0-9_]*)")
PROPERTY_RE = re.compile(r"(?<![A-Za-z0-9_])\.([A-Za-z_][A-Za-z0-9_]*)")
PROPERTY_NAME_RE = re.compile(r"^[a-z][A-Za-z0-9_]*$")
RDF_PREFIXES = {
    "ptl",
    "ptlg",
    "ptlr",
    "cito",
    "dcterms",
    "fabio",
    "foaf",
    "prov",
    "rdfs",
    "skos",
    "xsd",
}


def _cypher_files() -> list[Path]:
    """Return all Cypher files, including incorrectly named files for diagnostics."""

    return sorted(CYPHER_DIR.glob("*.cypher"))


def _first_matching_line(text: str, pattern: re.Pattern[str], *, description: str) -> str:
    matches = [line.strip() for line in text.splitlines() if pattern.fullmatch(line.strip())]
    assert len(matches) == 1, f"expected exactly one {description} line, found {len(matches)}"
    return matches[0]


def _parse_defaults(line: str, path: Path) -> dict[str, object]:
    match = DEFAULTS_RE.fullmatch(line)
    assert match is not None, f"{path.name}: malformed DEFAULTS line"
    try:
        defaults = json.loads(match.group("body"))
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{path.name}: DEFAULTS is not valid JSON") from exc
    assert isinstance(defaults, dict), f"{path.name}: DEFAULTS must be a JSON object"
    assert all(isinstance(key, str) for key in defaults), (
        f"{path.name}: DEFAULTS keys must be strings"
    )
    return defaults


def _blank_comments_and_strings(text: str) -> str:
    """Replace comments and quoted literals with spaces while preserving newlines."""

    chars = list(text)
    state: str | None = None
    index = 0
    while index < len(chars):
        char = chars[index]
        if state is None:
            if char == "/" and index + 1 < len(chars) and chars[index + 1] == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                while index < len(chars) and chars[index] != "\n":
                    chars[index] = " "
                    index += 1
                continue
            if char == "/" and index + 1 < len(chars) and chars[index + 1] == "*":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "/*"
                continue
            if char in {"'", '"', "`"}:
                state = char
                chars[index] = " "
            index += 1
            continue

        if state == "/*":
            if char == "*" and index + 1 < len(chars) and chars[index + 1] == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = None
                continue
            if char != "\n":
                chars[index] = " "
            index += 1
            continue

        if char == "\\" and state != "`" and index + 1 < len(chars):
            chars[index] = " "
            if chars[index + 1] != "\n":
                chars[index + 1] = " "
            index += 2
            continue
        if char == state:
            chars[index] = " "
            if index + 1 < len(chars) and chars[index + 1] == state:
                chars[index + 1] = " "
                index += 2
                continue
            state = None
        elif char != "\n":
            chars[index] = " "
        index += 1
    return "".join(chars)


def _quoted_literals(text: str) -> list[str]:
    """Return single-, double-, and backtick-quoted spans, including their contents."""

    literals: list[str] = []
    state: str | None = None
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if state is None:
            if char == "/" and index + 1 < len(text) and text[index + 1] == "/":
                index += 2
                while index < len(text) and text[index] != "\n":
                    index += 1
                continue
            if char == "/" and index + 1 < len(text) and text[index + 1] == "*":
                index += 2
                while index + 1 < len(text) and text[index : index + 2] != "*/":
                    index += 1
                index += 2 if index < len(text) else 0
                continue
            if char in {"'", '"', "`"}:
                state = char
                start = index
            index += 1
            continue
        if char == "\\" and state != "`":
            index += 2
            continue
        if char == state:
            if index + 1 < len(text) and text[index + 1] == state:
                index += 2
                continue
            literals.append(text[start : index + 1])
            state = None
        index += 1
    if state is not None:
        literals.append(text[start:])
    return literals


def _property_keys_from_cell(cell: str) -> set[str]:
    keys: set[str] = set()
    code_spans = re.findall(r"`([^`\n]+)`", cell)
    for code_span in code_spans:
        stripped = code_span.strip()
        declaration = re.match(r"([a-z][A-Za-z0-9_]*)\s*(?::|\()", stripped)
        if declaration and declaration.group(1) not in RDF_PREFIXES:
            keys.add(declaration.group(1))

    plain_cell = re.sub(r"`[^`\n]+`", " ", cell)
    for match in re.finditer(r"(?<![A-Za-z0-9_])([a-z][A-Za-z0-9_]*)\s*(?::|\()", plain_cell):
        if match.group(1) not in RDF_PREFIXES:
            keys.add(match.group(1))
    return keys


def _assert_no_parameter_interpolation(text: str, parameter_names: set[str], path: Path) -> None:
    interpolation = re.compile(r"\$\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}")
    brace_interpolation = re.compile(r"(?<![A-Za-z0-9_])\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}")
    code = _blank_comments_and_strings(text)
    for match in interpolation.finditer(code):
        if match.group(1) in parameter_names:
            raise AssertionError(f"{path.name}: parameter uses ${{...}} interpolation")
    for match in brace_interpolation.finditer(code):
        if match.group(1) in parameter_names:
            raise AssertionError(f"{path.name}: parameter uses {{...}} interpolation")

    for literal in _quoted_literals(text):
        if PARAMETER_RE.search(literal):
            raise AssertionError(f"{path.name}: parameter appears inside a quoted string")
        if any(pattern.search(literal) for pattern in (interpolation, brace_interpolation)):
            raise AssertionError(f"{path.name}: parameter uses interpolation in a string")


def _binding_vocabulary(text: str) -> tuple[set[str], set[str], set[str]]:
    """Extract labels, relationship types, and property keys from the LPG catalogue."""

    labels: set[str] = set()
    relationship_types: set[str] = set()
    property_keys = {
        match.group(1)
        for match in PROPERTY_RE.finditer(text)
        if PROPERTY_NAME_RE.fullmatch(match.group(1))
    }

    table_lines = text.splitlines()
    table_header: list[str] | None = None
    label_columns: set[int] = set()
    relationship_columns: set[int] = set()
    property_columns: set[int] = set()
    for line in table_lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            table_header = None
            label_columns = set()
            relationship_columns = set()
            property_columns = set()
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        if table_header is None:
            table_header = cells
            label_columns = {index for index, cell in enumerate(cells) if "label" in cell.lower()}
            relationship_columns = {
                index
                for index, cell in enumerate(cells)
                if "relationship" in cell.lower() or cell.lower().strip() == "type"
            }
            property_columns = {
                index
                for index, cell in enumerate(cells)
                if any(word in cell.lower() for word in ("propert", "identity", "partition"))
            }
            continue
        for index in label_columns:
            if index >= len(cells):
                continue
            cell = cells[index].strip().strip("`")
            labels.update(
                name
                for name in COLON_NAME_RE.findall(cell)
                if name[0].isupper() and any(char.islower() for char in name)
            )
            bare_label = re.fullmatch(r"([A-Z][A-Za-z0-9]*)", cell)
            if bare_label and any(char.islower() for char in bare_label.group(1)):
                labels.add(bare_label.group(1))
        for index in relationship_columns:
            if index >= len(cells):
                continue
            cell = cells[index].strip().strip("`")
            relationship_types.update(
                name for name in COLON_NAME_RE.findall(cell) if name.isupper()
            )
            bare_relationship = re.fullmatch(r"([A-Z][A-Z0-9_]*)", cell)
            if bare_relationship:
                relationship_types.add(bare_relationship.group(1))
        for index in property_columns:
            if index >= len(cells):
                continue
            cell = cells[index]
            property_keys.update(_property_keys_from_cell(cell))

    # Partition keys are deliberately documented in prose as well as in the tables.
    property_keys.update(re.findall(r"\b(?:reviewId|extractionRun)\b", text))

    return labels, relationship_types, property_keys


# Tokenizer limitation: this checks only static :Label, :REL_TYPE, and .property occurrences
# after removing comments and quoted text; dynamic names, map keys, and generated Cypher are out.
def _query_vocabulary(query: str) -> tuple[set[str], set[str], set[str]]:
    """Tokenize static Cypher labels, relationship types, and dotted property accesses.

    Limitation: this intentionally checks only syntactic ``:Label``, ``:REL_TYPE``, and
    ``.property`` occurrences after removing comments and quoted text.  It cannot validate
    dynamic labels/types, map keys, APOC-generated Cypher, or semantic type/cardinality rules.
    """

    code = _blank_comments_and_strings(query)
    colon_names = {match.group(1) for match in COLON_NAME_RE.finditer(code)}
    labels = {
        name for name in colon_names if name[0].isupper() and any(char.islower() for char in name)
    }
    relationship_types = {name for name in colon_names if name.isupper()}
    property_keys = {match.group(1) for match in PROPERTY_RE.finditer(code)}
    return labels, relationship_types, property_keys


def _parse_query(path: Path) -> tuple[str, set[str], dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    first_nonempty = next((line.strip() for line in text.splitlines() if line.strip()), "")
    header_match = HEADER_RE.fullmatch(first_nonempty)
    assert header_match is not None, f"{path.name}: missing // CQxx — header"

    params_line = _first_matching_line(text, PARAMS_RE, description="PARAMS")
    parameter_matches = PARAMETER_RE.findall(params_line)
    assert len(parameter_matches) == len(set(parameter_matches)), (
        f"{path.name}: PARAMS repeats a parameter"
    )
    parameter_names = set(parameter_matches)
    defaults_line = _first_matching_line(text, DEFAULTS_RE, description="DEFAULTS")
    defaults = _parse_defaults(defaults_line, path)
    assert set(defaults) == parameter_names, (
        f"{path.name}: DEFAULTS keys {sorted(defaults)} do not match "
        f"PARAMS keys {sorted(parameter_names)}"
    )

    query_body = _blank_comments_and_strings(text)
    used_parameters = set(PARAMETER_RE.findall(query_body))
    assert used_parameters <= parameter_names, (
        f"{path.name}: undeclared parameters used: {sorted(used_parameters - parameter_names)}"
    )
    _assert_no_parameter_interpolation(text, parameter_names, path)
    return text, parameter_names, defaults


def _assert_complete_file_set(paths: list[Path]) -> dict[int, Path]:
    assert len(paths) == 14, f"expected exactly 14 .cypher files, found {len(paths)}"
    numbered: dict[int, Path] = {}
    for path in paths:
        match = CYPHER_FILE_RE.fullmatch(path.name)
        assert match is not None, f"unexpected Cypher filename: {path.name}"
        number = int(match.group("number"))
        assert number not in numbered, f"duplicate CQ number {number:02d}"
        numbered[number] = path
    expected = set(range(1, 15))
    assert set(numbered) == expected, (
        f"Cypher files must be numbered cq01 through cq14; found {sorted(numbered)}"
    )
    return numbered


def test_cypher_competency_files_are_complete_and_well_formed() -> None:
    paths = _cypher_files()
    numbered = _assert_complete_file_set(paths)
    binding = BINDING_PATH.read_text(encoding="utf-8")
    allowed_labels, allowed_relationships, allowed_properties = _binding_vocabulary(binding)

    for number in sorted(numbered):
        path = numbered[number]
        text, _, _ = _parse_query(path)
        header_match = HEADER_RE.fullmatch(
            next(line.strip() for line in text.splitlines() if line.strip())
        )
        assert header_match is not None
        assert int(header_match.group("number")) == number, (
            f"{path.name}: header number does not match filename"
        )

        labels, relationship_types, properties = _query_vocabulary(text)
        assert labels <= allowed_labels, (
            f"{path.name}: labels missing from lpg-binding.md: {sorted(labels - allowed_labels)}"
        )
        assert relationship_types <= allowed_relationships, (
            f"{path.name}: relationship types missing from lpg-binding.md: "
            f"{sorted(relationship_types - allowed_relationships)}"
        )
        assert properties <= allowed_properties, (
            f"{path.name}: properties missing from lpg-binding.md: "
            f"{sorted(properties - allowed_properties)}"
        )


def test_v02_lpg_vocabulary_and_affected_queries_use_amendments() -> None:
    binding = BINDING_PATH.read_text(encoding="utf-8")
    _allowed_labels, allowed_relationships, allowed_properties = _binding_vocabulary(binding)

    assert "ClusterPair" in binding
    assert {"ADDRESSES_LIMITATION", "CLUSTER_PAIR"} <= allowed_relationships
    assert {
        "metricDirection",
        "frontierComponentVelocity",
        "frontierComponentMainPathLeaf",
        "frontierComponentClusterGrowth",
        "frontierComponentConceptNovelty",
        "frontierComponentSotaClaim",
        "frontierComponentNotPeerReviewed",
        "semanticSimilarity",
        "crossCitationCount",
    } <= allowed_properties
    assert "ptlr:extraction/{runId}" in binding
    assert "seedValue" in binding and "always stored as a string" in binding
    assert "TriG" in binding and "lossy" in binding

    queries = {
        number: path.read_text(encoding="utf-8")
        for number, path in _assert_complete_file_set(_cypher_files()).items()
    }
    assert "metricDirection" in queries[4]
    assert "higherIsBetter" in queries[4]
    assert "lowerIsBetter" in queries[4]
    assert "ADDRESSES_LIMITATION" in queries[6]
    assert "CLUSTER_PAIR" in queries[11]
    assert "semanticSimilarity" in queries[11]
    assert "crossCitationCount" in queries[11]
    assert "similarityThreshold" in queries[11]


def _split_cypher_statements(script: str) -> list[str]:
    """Split a schema script on semicolons outside comments and quoted literals."""

    statements: list[str] = []
    current: list[str] = []
    state: str | None = None
    index = 0
    while index < len(script):
        char = script[index]
        if state is None:
            if char == "/" and index + 1 < len(script) and script[index + 1] == "/":
                index += 2
                while index < len(script) and script[index] != "\n":
                    index += 1
                continue
            if char == "/" and index + 1 < len(script) and script[index + 1] == "*":
                index += 2
                while index + 1 < len(script) and script[index : index + 2] != "*/":
                    index += 1
                index += 2 if index < len(script) else 0
                continue
            if char in {"'", '"', "`"}:
                state = char
            elif char == ";":
                statement = "".join(current).strip()
                if statement:
                    statements.append(statement)
                current = []
                index += 1
                continue
            current.append(char)
            index += 1
            continue

        current.append(char)
        if char == "\\" and state != "`" and index + 1 < len(script):
            current.append(script[index + 1])
            index += 2
            continue
        if char == state:
            if index + 1 < len(script) and script[index + 1] == state:
                current.append(script[index + 1])
                index += 2
                continue
            state = None
        index += 1

    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


@pytest.mark.neo4j
def test_cypher_competency_queries_are_accepted_by_neo4j() -> None:
    uri = os.getenv("NEO4J_URI", "").strip()
    if not uri:
        pytest.skip("NEO4J_URI is not set")

    from neo4j import GraphDatabase

    paths = _assert_complete_file_set(_cypher_files())
    specs = [(path, _parse_query(path)) for path in paths.values()]
    constraints = CONSTRAINTS_PATH.read_text(encoding="utf-8")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "neo4j")
    database = os.getenv("NEO4J_DATABASE")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        session_kwargs = {"database": database} if database else {}
        with driver.session(**session_kwargs) as session:
            for statement in _split_cypher_statements(constraints):
                try:
                    session.run(statement).consume()
                except Exception as exc:
                    if not _is_enterprise_only_error(str(exc)):
                        raise AssertionError(
                            f"Neo4j rejected schema statement: {statement}"
                        ) from exc
            for path, (query, _, defaults) in specs:
                try:
                    session.run(query, **defaults).consume()
                except Exception as exc:
                    raise AssertionError(f"{path.name}: Neo4j rejected the query") from exc
    finally:
        driver.close()

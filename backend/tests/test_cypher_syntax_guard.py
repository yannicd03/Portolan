"""Cheap lexical guard for a common missing-hyphen Cypher typo.

This intentionally does not parse Cypher: it only flags ``]`` followed by optional
whitespace and ``(``, so it can miss more complex syntax errors and may need updating
if a legitimate list expression is ever written in that shape.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CYPHER_DIR = REPO_ROOT / "ontology" / "competency" / "cypher"
MISSING_RELATIONSHIP_HYPHEN = re.compile(r"\]\s*\(")


def test_cypher_relationship_brackets_are_followed_by_a_dash() -> None:
    failures: list[str] = []
    for path in sorted(CYPHER_DIR.glob("*.cypher")):
        text = path.read_text(encoding="utf-8")
        for match in MISSING_RELATIONSHIP_HYPHEN.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            failures.append(f"{path.name}:{line}")

    assert not failures, "possible missing relationship hyphen in " + ", ".join(failures)

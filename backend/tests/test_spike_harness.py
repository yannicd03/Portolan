"""End-to-end checks for the store comparison harness."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = REPO_ROOT / "eval" / "spike" / "run_spike.py"
COMPOSER_PATH = REPO_ROOT / "eval" / "golden" / "compose_golden.py"


def _load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arg_spike_harness_test", HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before executing: dataclasses in the harness resolve annotations via
    # sys.modules[cls.__module__], which is None for an unregistered module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _assert_backend_artifacts(payload: dict[str, object], output_dir: Path, backend: str) -> None:
    assert (output_dir / "results.json").is_file()
    assert (output_dir / "report.md").is_file()
    backends = payload["backends"]
    assert isinstance(backends, dict)
    result = backends[backend]
    assert isinstance(result, dict)
    questions = result["competency_questions"]
    assert isinstance(questions, list)
    assert len(questions) == 14
    assert [question["number"] for question in questions] == [f"CQ{n:02d}" for n in range(1, 15)]
    for question in questions:
        assert {
            "executed",
            "row_count",
            "best_wall_time_seconds",
            "query_non_comment_line_count",
        } <= set(question)
        if not question["executed"]:
            assert question["row_count"] is None
            assert question["error"]


@pytest.mark.skipif(not COMPOSER_PATH.is_file(), reason="compose_golden.py is developed separately")
def test_oxigraph_harness_runs_all_competency_questions(tmp_path: Path) -> None:
    harness = _load_harness()

    payload = harness.run_spike("oxigraph", output_dir=tmp_path)

    _assert_backend_artifacts(payload, tmp_path, "oxigraph")
    result = payload["backends"]["oxigraph"]
    assert result["status"] == "completed"
    assert result["load"]["executed"] is True
    assert result["export"]["executed"] is True
    assert result["export"]["triple_count"] is not None
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "does not declare a winner" in report


@pytest.mark.neo4j
def test_neo4j_harness_path_when_configured(tmp_path: Path) -> None:
    if not os.getenv("NEO4J_URI"):
        pytest.skip("NEO4J_URI is not set")
    if not COMPOSER_PATH.is_file():
        pytest.skip("compose_golden.py is developed separately")
    harness = _load_harness()

    payload = harness.run_spike("neo4j", output_dir=tmp_path)

    _assert_backend_artifacts(payload, tmp_path, "neo4j")
    result = payload["backends"]["neo4j"]
    assert result["status"] == "completed"
    assert "native_constraints" in result

"""Run the time-sliced frontier and gap backtest for one project.

Run from ``backend/``::

    uv run python ../eval/backtest/run_backtest.py --project ID [--cutoff T]
    uv run python ../eval/backtest/run_backtest.py --golden
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The documented invocation runs this file from ``backend/``; make the backend
# package importable for a direct script invocation.
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:  # Works both as ``python -m eval.backtest.run_backtest`` and as a script.
    from .backtest import DEFAULT_DRAWS, DEFAULT_HORIZON_YEARS, DEFAULT_SEED, backtest
except ImportError:  # pragma: no cover - exercised by the documented command.
    from backtest import DEFAULT_DRAWS, DEFAULT_HORIZON_YEARS, DEFAULT_SEED, backtest

from portolan.golden import seed_demo_project  # noqa: E402
from portolan.graph.base import ResearchGraph  # noqa: E402
from portolan.graph.memory import InMemoryResearchGraph  # noqa: E402
from portolan.settings import Settings, make_graph  # noqa: E402

DEFAULT_RESULTS_DIR = Path(__file__).resolve().with_name("results")
GOLDEN_KEY = "golden"
GAP_TYPES = ("bridging", "matrix_void", "stagnation")


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def run_project(
    graph: ResearchGraph,
    project_id: str,
    *,
    key: str | None = None,
    cutoff_year: int | None = None,
    horizon_years: int = DEFAULT_HORIZON_YEARS,
    seed: int = DEFAULT_SEED,
    draws: int = DEFAULT_DRAWS,
) -> dict[str, Any]:
    """Load one project's view from ``graph`` and backtest it."""

    project = graph.get_project(project_id)
    if project is None:
        raise ValueError(f"unknown project: {project_id}")
    view = graph.project_graph(project_id, include_authors=False, include_concepts=True)
    result = backtest(
        view,
        cutoff_year,
        project_id=project_id,
        horizon_years=horizon_years,
        seed=seed,
        draws=draws,
    )
    result["key"] = key or project_id
    result["project_name"] = project.name
    result["generated"] = datetime.now(UTC).date().isoformat()
    return result


def table_lines(results: Sequence[Mapping[str, Any]]) -> list[str]:
    """Markdown table rows shared by the console output and ``summary.md``."""

    lines = [
        "| Project | T | Slice works | Future works | Frontier P@5 | P@10 | Spearman | "
        "Baseline P@5 | P@10 | Spearman | Leak-free P@5 | P@10 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        frontier = result.get("frontier") or {}
        leak_free = result.get("frontier_leak_free") or {}
        precision = frontier.get("precision", {})
        baseline = frontier.get("baseline_local_in_degree_precision", {})
        leak_precision = leak_free.get("precision", {})
        lines.append(
            "| "
            + " | ".join(
                _fmt(value)
                for value in (
                    result.get("key"),
                    result.get("cutoff_year"),
                    result.get("slice_works"),
                    frontier.get("future_works"),
                    precision.get("@5"),
                    precision.get("@10"),
                    frontier.get("spearman"),
                    baseline.get("@5"),
                    baseline.get("@10"),
                    frontier.get("baseline_local_in_degree_spearman"),
                    leak_precision.get("@5"),
                    leak_precision.get("@10"),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "| Project | Gap type | Hypotheses | Anticipated/confirmed | Rate | Random baseline |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in results:
        by_type = (result.get("gaps") or {}).get("by_type", {})
        for gap_type in GAP_TYPES:
            row = by_type.get(gap_type, {})
            lines.append(
                "| "
                + " | ".join(
                    _fmt(value)
                    for value in (
                        result.get("key"),
                        gap_type,
                        row.get("hypotheses", 0),
                        row.get("hits", 0),
                        row.get("rate"),
                        row.get("baseline_rate"),
                    )
                )
                + " |"
            )
    return lines


def write_summary(results: Sequence[Mapping[str, Any]], path: Path) -> None:
    lines = [
        "# Time-sliced backtest",
        "",
        f"Generated: {datetime.now(UTC).date().isoformat()}",
        "",
        "Frontier precision counts a top-ranked work as a hit when its citations from works "
        "published in (T, T+2] are in the top quartile of the frontier window. The baseline "
        "ranks by in-slice local in-degree; the leak-free columns re-rank without "
        "`cited_by_count`. Gap rates are checked against the same future works; the random "
        "baseline draws equally many random clusters or cluster/concept pairs. "
        "See README.md for details.",
        "",
        *table_lines(results),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Time-sliced backtest of frontier and gaps.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--project", metavar="ID", help="project id in the configured store")
    target.add_argument(
        "--golden",
        action="store_true",
        help="seed the golden demo project into an in-memory graph and backtest it",
    )
    parser.add_argument("--cutoff", type=int, help="cutoff year T (default: newest year - 2)")
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON_YEARS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--draws", type=int, default=DEFAULT_DRAWS)
    parser.add_argument(
        "--results-dir", type=Path, default=DEFAULT_RESULTS_DIR, help=argparse.SUPPRESS
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.horizon < 1:
        _parser().error("--horizon must be at least 1")
    if args.draws < 1:
        _parser().error("--draws must be at least 1")

    if args.golden:
        graph: ResearchGraph = InMemoryResearchGraph()
        project = seed_demo_project(graph, REPO_ROOT)
        if project is None:  # pragma: no cover - a fresh graph has no projects
            raise RuntimeError("golden project was not seeded")
        project_id, key = project.id, GOLDEN_KEY
    else:
        graph = make_graph(Settings.from_env())
        project_id, key = args.project, args.project

    try:
        result = run_project(
            graph,
            project_id,
            key=key,
            cutoff_year=args.cutoff,
            horizon_years=args.horizon,
            seed=args.seed,
            draws=args.draws,
        )
    finally:
        graph.close()

    results_dir: Path = args.results_dir
    results_dir.mkdir(parents=True, exist_ok=True)
    cutoff = result["cutoff_year"]
    output = results_dir / f"{key}-T{cutoff if cutoff is not None else 'none'}.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # The summary covers every result file, so runs for several projects accumulate.
    results = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(results_dir.glob("*-T*.json"))
    ]
    print("\n".join(table_lines([result])))
    write_summary(results, results_dir / "summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

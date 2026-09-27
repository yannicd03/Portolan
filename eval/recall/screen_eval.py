"""Evaluate candidate screening offline on frozen candidate pools.

``run_recall.py --dump-pools`` writes the pool the runner screened for each
survey.  This command re-runs only the selection step on those pools with a
chosen screener, ``max_works`` and ``min_score`` and reports included recall
and precision, without any network access.  ``--grid`` searches the screener's
component weights and reports leave-one-survey-out results.

Run from ``backend/``::

    uv run python ../eval/recall/screen_eval.py
    uv run python ../eval/recall/screen_eval.py --grid
"""

from __future__ import annotations

import argparse
import heapq
import itertools
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:  # Works both as ``python -m eval.recall.screen_eval`` and as a script.
    from .metrics import _group_keys, _identity_groups, compute_metrics, identity_keys
    from .run_recall import DEFAULT_POOLS_DIR, load_pool
except ImportError:  # pragma: no cover - exercised by the documented command.
    from metrics import _group_keys, _identity_groups, compute_metrics, identity_keys
    from run_recall import DEFAULT_POOLS_DIR, load_pool

from portolan.research.models import ResearchRequest  # noqa: E402
from portolan.research.runner import (  # noqa: E402
    _Candidate,
    _candidate_sort_id,
    _record_identifiers,
    build_screening_context,
    select_candidates,
)
from portolan.research.screening import (  # noqa: E402
    COMPONENTS,
    HeuristicScreener,
    _identifier_keys,
    _keys_from_value,
)

GRID_COMPONENTS = ("semantic", "cocitation", "coupling", "link", "citation")
GRID_VALUES = (0.0, 0.1, 0.2, 0.3, 0.4)
LEGACY_WEIGHTS = {"token": 0.45, "link": 0.2, "citation": 0.10, "cocitation": 0.25}


@dataclass
class PreparedPool:
    """A loaded pool rebuilt into the runner's screening inputs."""

    key: str
    request: ResearchRequest
    candidates: dict[str, _Candidate]
    seed_keys: set[str]
    context: dict[str, Any]
    ground_truth: list[dict[str, Any]]
    survey_record: dict[str, Any]


def prepare_pool(pool: Mapping[str, Any]) -> PreparedPool:
    """Rebuild candidates and the base screening context from a dumped pool."""

    request = ResearchRequest.model_validate(pool["request"])
    candidates: dict[str, _Candidate] = {}
    for item in pool["candidates"]:
        candidates[str(item["key"])] = _Candidate(
            key=str(item["key"]),
            record=dict(item["record"]),
            discovered_via=str(item.get("discovered_via") or ""),
            depth=int(item.get("depth") or 0),
            is_seed=bool(item.get("is_seed")),
            frontier_seed=bool(item.get("frontier_seed")),
            core=bool(item.get("core")),
        )
    seed_keys = {str(key) for key in pool.get("seed_keys", [])}
    relation_pairs = {
        (str(pair[0]), str(pair[1])) for pair in pool.get("relation_pairs", []) if len(pair) == 2
    }
    context = build_screening_context(request, candidates, seed_keys, relation_pairs)
    return PreparedPool(
        key=str(pool["key"]),
        request=request,
        candidates=candidates,
        seed_keys=seed_keys,
        context=context,
        ground_truth=[dict(record) for record in pool.get("ground_truth", [])],
        survey_record=dict(pool.get("survey_record") or {}),
    )


def load_pools(directory: Path, only: str | None = None) -> list[PreparedPool]:
    """Load every ``*.pool.json`` in ``directory``, sorted by survey key."""

    paths = sorted(directory.glob("*.pool.json"))
    pools = [prepare_pool(load_pool(path)) for path in paths]
    if only is not None:
        pools = [pool for pool in pools if pool.key == only]
    return sorted(pools, key=lambda pool: pool.key)


def _limits(
    pool: PreparedPool, max_works: int | None, min_score: float | None
) -> tuple[int, float]:
    return (
        pool.request.max_works if max_works is None else int(max_works),
        pool.request.min_score if min_score is None else float(min_score),
    )


def evaluate_pool(
    pool: PreparedPool,
    screener: Any,
    *,
    max_works: int | None = None,
    min_score: float | None = None,
) -> dict[str, Any]:
    """Select with the runner's selection code and score against the survey."""

    works, threshold = _limits(pool, max_works, min_score)
    selected = select_candidates(
        pool.candidates.values(), pool.context, screener, works, threshold, pool.seed_keys
    )
    metrics = compute_metrics(
        pool.ground_truth,
        [candidate.record for candidate in pool.candidates.values()],
        [candidate.record for candidate in selected],
        excluded=[pool.survey_record] if pool.survey_record else [],
    )
    return {
        "key": pool.key,
        "ground_truth_count": metrics.ground_truth_count,
        "candidates": metrics.candidates_count,
        "included": metrics.included_count,
        "recall_candidates": metrics.recall_candidates,
        "recall_included": metrics.recall_included,
        "precision_included": metrics.precision_included,
        "selected_keys": [candidate.key for candidate in selected],
    }


def macro(results: Sequence[Mapping[str, Any]], name: str) -> float:
    return sum(float(result[name]) for result in results) / len(results) if results else 0.0


class FastPool:
    """Exact, faster replica of :func:`select_candidates` for weight searches.

    Only the ``link`` component depends on what is already included, and it
    only ever flips from 0 to 1.  Every other component is computed once per
    candidate; a candidate is rescored when an inclusion links it.  The result
    equals :func:`select_candidates` for any :class:`HeuristicScreener` (tested).
    """

    def __init__(self, pool: PreparedPool) -> None:
        screener = HeuristicScreener()
        self.pool = pool
        self.order = list(pool.candidates.values())
        self.seeds = sorted(
            (candidate for candidate in self.order if candidate.key in pool.seed_keys),
            key=_candidate_sort_id,
        )
        self.remaining = [
            index
            for index, candidate in enumerate(self.order)
            if candidate.key not in pool.seed_keys
        ]
        self.sort_ids = [_candidate_sort_id(candidate) for candidate in self.order]

        self.first_context = self._step_context(self.seeds)
        self._components: dict[str, dict[int, dict[str, float]]] = {
            screener.cocitation_norm: {
                index: screener.components(self.order[index].record, self.first_context)
                for index in self.remaining
            }
        }
        self.linked_by = self._link_index()
        self._truth_index()

    def components(self, norm: str) -> dict[int, dict[str, float]]:
        """Component values per candidate for one co-citation normalisation."""

        if norm not in self._components:
            screener = HeuristicScreener(cocitation_norm=norm)
            base = next(iter(self._components.values()))
            self._components[norm] = {
                index: {
                    **values,
                    "cocitation": screener._cocitation_score(
                        self.order[index].record, self.first_context
                    ),
                }
                for index, values in base.items()
            }
        return self._components[norm]

    def _step_context(self, selected: Sequence[_Candidate]) -> dict[str, Any]:
        included_ids: set[Any] = set(self.pool.seed_keys) if selected else set()
        for candidate in selected:
            included_ids.update(_record_identifiers(candidate.record))
        return {
            **self.pool.context,
            "included_records": [candidate.record for candidate in selected],
            "included_ids": included_ids,
        }

    def _link_index(self) -> dict[int, set[int]]:
        """For each candidate, the remaining candidates its inclusion links."""

        by_identity: dict[tuple[str, str], set[int]] = {}
        by_reference: dict[tuple[str, str], set[int]] = {}
        references: dict[int, set[tuple[str, str]]] = {}
        for index in self.remaining:
            record = self.order[index].record
            for key in _identifier_keys(record):
                by_identity.setdefault(key, set()).add(index)
            refs: set[tuple[str, str]] = set()
            values = record.get("referenced_works")
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                for value in values:
                    refs.update(_keys_from_value(value))
            references[index] = refs
            for key in refs:
                by_reference.setdefault(key, set()).add(index)
        linked_by: dict[int, set[int]] = {}
        for index in self.remaining:
            candidate = self.order[index]
            # The same keys _link_score derives from included_ids/included_records.
            known = _keys_from_value({candidate.key, *_record_identifiers(candidate.record)})
            known.update(_identifier_keys(candidate.record))
            targets: set[int] = set()
            for key in known:
                targets.update(by_reference.get(key, ()))
            for key in references[index]:
                targets.update(by_identity.get(key, ()))
            targets.discard(index)
            linked_by[index] = targets
        return linked_by

    def _truth_index(self) -> None:
        pool = self.pool
        excluded = set(identity_keys(pool.survey_record)) if pool.survey_record else set()
        truth = [record for record in pool.ground_truth if not (identity_keys(record) & excluded)]
        self.truth_keys = _group_keys(_identity_groups(truth))
        self.counted: dict[int, bool] = {}
        self.matches: dict[int, frozenset[int]] = {}
        for index, candidate in enumerate(self.order):
            keys = identity_keys(candidate.record)
            self.counted[index] = not (keys & excluded)
            self.matches[index] = frozenset(
                group for group, truth_keys in enumerate(self.truth_keys) if truth_keys & keys
            )

    def select(self, screener: HeuristicScreener, max_works: int, min_score: float) -> list[int]:
        """Return the selected candidate indices, seeds first."""

        components = self.components(screener.cocitation_norm)
        selected = [self.order.index(seed) for seed in self.seeds]
        target = len(self.seeds) + max(max_works - len(self.seeds), 0)
        linked = {index: components[index]["link"] for index in self.remaining}
        heap: list[tuple[float, str, int]] = []
        current: dict[int, float] = {}
        for index in self.remaining:
            score = screener.combine(components[index])
            current[index] = score
            # select_candidates takes min over (-score, sort id) and resolves
            # exact ties by list position, which is the pool index.
            heap.append((-score, self.sort_ids[index], index))
        heapq.heapify(heap)
        chosen: set[int] = set()
        while heap and len(selected) < target:
            negative, _, index = heapq.heappop(heap)
            if index in chosen or -negative != current[index]:
                continue
            if -negative < min_score:
                break
            selected.append(index)
            chosen.add(index)
            for other in self.linked_by[index]:
                if other in chosen or linked[other] >= 1.0:
                    continue
                linked[other] = 1.0
                score = screener.combine({**components[other], "link": 1.0})
                current[other] = score
                heapq.heappush(heap, (-score, self.sort_ids[other], other))
        return selected

    def metrics(self, selected: Iterable[int]) -> dict[str, float]:
        included = [index for index in selected if self.counted[index]]
        matched: set[int] = set()
        hits = 0
        for index in included:
            if self.matches[index]:
                hits += 1
                matched.update(self.matches[index])
        truth_count = len(self.truth_keys)
        return {
            "included": len(included),
            "recall_included": len(matched) / truth_count if truth_count else 0.0,
            "precision_included": hits / len(included) if included else 0.0,
        }

    def evaluate(
        self,
        screener: HeuristicScreener,
        *,
        max_works: int | None = None,
        min_score: float | None = None,
    ) -> dict[str, float]:
        works, threshold = _limits(self.pool, max_works, min_score)
        return self.metrics(self.select(screener, works, threshold))

    def result(
        self,
        screener: HeuristicScreener,
        *,
        max_works: int | None = None,
        min_score: float | None = None,
    ) -> dict[str, Any]:
        """The same fields as :func:`evaluate_pool`, from the fast selection."""

        works, threshold = _limits(self.pool, max_works, min_score)
        selected = self.select(screener, works, threshold)
        pool = self.pool
        candidates = compute_metrics(
            pool.ground_truth,
            [candidate.record for candidate in self.order],
            [],
            excluded=[pool.survey_record] if pool.survey_record else [],
        )
        return {
            "key": pool.key,
            "ground_truth_count": candidates.ground_truth_count,
            "candidates": candidates.candidates_count,
            "recall_candidates": candidates.recall_candidates,
            **self.metrics(selected),
            "selected_keys": [self.order[index].key for index in selected],
        }


def weight_grid(values: Sequence[float] = GRID_VALUES) -> list[dict[str, float]]:
    """All non-zero weight vectors over GRID_COMPONENTS, normalised and deduplicated."""

    grid: dict[tuple[float, ...], tuple[float, ...]] = {}
    for combo in itertools.product(values, repeat=len(GRID_COMPONENTS)):
        total = sum(combo)
        if total <= 0:
            continue
        normalised = tuple(value / total for value in combo)
        grid.setdefault(tuple(round(value, 6) for value in normalised), normalised)
    return [dict(zip(GRID_COMPONENTS, combo, strict=True)) for combo in grid.values()]


def grid_screener(weights: Mapping[str, float]) -> HeuristicScreener:
    return HeuristicScreener(
        token_weight=0.0,
        **{f"{name}_weight": float(weights.get(name, 0.0)) for name in GRID_COMPONENTS},
        recency_weight=0.0,
    )


def run_grid(
    fast_pools: Sequence[FastPool],
    grid: Sequence[Mapping[str, float]],
    *,
    max_works: int | None = None,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """Evaluate every weight vector on every pool."""

    rows: list[dict[str, Any]] = []
    for weights in grid:
        screener = grid_screener(weights)
        rows.append(
            {
                "weights": dict(weights),
                "results": {
                    fast.pool.key: fast.evaluate(screener, max_works=max_works, min_score=min_score)
                    for fast in fast_pools
                },
            }
        )
    return rows


def _mean(row: Mapping[str, Any], keys: Sequence[str], name: str) -> float:
    return sum(row["results"][key][name] for key in keys) / len(keys) if keys else 0.0


def leave_one_out(rows: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> list[dict[str, Any]]:
    """Choose weights on all but one survey and report them on the held-out one.

    The choice maximises mean included recall on the training surveys, then
    mean included precision; remaining ties go to the earlier grid row.
    """

    folds: list[dict[str, Any]] = []
    for held_out in keys:
        train = [key for key in keys if key != held_out]
        best_index = min(
            range(len(rows)),
            key=lambda index: (
                -round(_mean(rows[index], train, "recall_included"), 9),
                -round(_mean(rows[index], train, "precision_included"), 9),
                index,
            ),
        )
        best = rows[best_index]
        folds.append(
            {
                "held_out": held_out,
                "train": train,
                "weights": dict(best["weights"]),
                "train_recall": _mean(best, train, "recall_included"),
                "held_out_recall": best["results"][held_out]["recall_included"],
                "held_out_precision": best["results"][held_out]["precision_included"],
            }
        )
    return folds


def robust_weights(folds: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """Summarise the fold choices: their average, and the majority choice if any."""

    result: dict[str, dict[str, float]] = {}
    if not folds:
        return result
    average = {
        name: sum(float(fold["weights"][name]) for fold in folds) / len(folds)
        for name in GRID_COMPONENTS
    }
    total = sum(average.values()) or 1.0
    result["average"] = {name: round(value / total, 4) for name, value in average.items()}
    counts: dict[tuple[float, ...], int] = {}
    for fold in folds:
        vector = tuple(float(fold["weights"][name]) for name in GRID_COMPONENTS)
        counts[vector] = counts.get(vector, 0) + 1
    vector, count = max(counts.items(), key=lambda item: item[1])
    if count * 2 > len(folds):
        result["majority"] = dict(zip(GRID_COMPONENTS, vector, strict=True))
    return result


def _format_weights(weights: Mapping[str, float]) -> str:
    return ", ".join(f"{name} {float(weights.get(name, 0.0)):.2f}" for name in GRID_COMPONENTS)


def _results_table(title: str, results: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        f"### {title}",
        "",
        "| Survey | R | Candidates | Included | Candidate recall | Included recall | "
        "Included precision |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        lines.append(
            f"| {result['key']} | {result['ground_truth_count']} | {result['candidates']} | "
            f"{result['included']} | {result['recall_candidates']:.3f} | "
            f"{result['recall_included']:.3f} | {result['precision_included']:.3f} |"
        )
    lines.append(
        f"| macro | | | | {macro(results, 'recall_candidates'):.3f} | "
        f"{macro(results, 'recall_included'):.3f} | {macro(results, 'precision_included'):.3f} |"
    )
    lines.append("")
    return lines


def grid_report(
    pools: Sequence[PreparedPool],
    *,
    values: Sequence[float] = GRID_VALUES,
    max_works: int | None = None,
    min_score: float | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Run the grid and leave-one-out analysis; return markdown lines and data."""

    fast_pools = [FastPool(pool) for pool in pools]
    keys = [pool.key for pool in pools]
    grid = weight_grid(values)
    rows = run_grid(fast_pools, grid, max_works=max_works, min_score=min_score)
    folds = leave_one_out(rows, keys)
    robust = robust_weights(folds)
    references = {
        "legacy": HeuristicScreener(**{f"{k}_weight": v for k, v in LEGACY_WEIGHTS.items()}),
        "default": HeuristicScreener(),
        **{f"robust ({name})": grid_screener(weights) for name, weights in robust.items()},
    }
    reference_results = {
        label: {
            fast.pool.key: fast.evaluate(screener, max_works=max_works, min_score=min_score)
            for fast in fast_pools
        }
        for label, screener in references.items()
    }

    lines = [
        f"Grid: {len(grid)} normalised weight vectors over {', '.join(GRID_COMPONENTS)} "
        f"(values {', '.join(f'{value:g}' for value in values)}).",
        "",
        "### Leave-one-survey-out",
        "",
        "| Held out | Weights chosen on the other surveys | Train recall | "
        "Held-out recall | Held-out precision | Default recall | Legacy recall |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for fold in folds:
        held_out = fold["held_out"]
        lines.append(
            f"| {held_out} | {_format_weights(fold['weights'])} | {fold['train_recall']:.3f} | "
            f"{fold['held_out_recall']:.3f} | {fold['held_out_precision']:.3f} | "
            f"{reference_results['default'][held_out]['recall_included']:.3f} | "
            f"{reference_results['legacy'][held_out]['recall_included']:.3f} |"
        )
    if folds:
        mean_recall = sum(fold["held_out_recall"] for fold in folds) / len(folds)
        mean_precision = sum(fold["held_out_precision"] for fold in folds) / len(folds)
        lines.append(f"| mean | | | {mean_recall:.3f} | {mean_precision:.3f} | | |")
    lines.extend(["", "### Reference screeners (all surveys)", ""])
    lines.append(
        "| Screener | Weights | " + " | ".join(keys) + " | Macro recall | Macro precision |"
    )
    lines.append("| --- | --- | " + " | ".join("---:" for _ in keys) + " | ---: | ---: |")
    for label, results in reference_results.items():
        screener = references[label]
        values_text = " | ".join(f"{results[key]['recall_included']:.3f}" for key in keys)
        recall = sum(results[key]["recall_included"] for key in keys) / len(keys) if keys else 0
        precision = (
            sum(results[key]["precision_included"] for key in keys) / len(keys) if keys else 0
        )
        weights = ", ".join(
            f"{name} {value:.2f}" for name, value in screener.weights.items() if value
        )
        lines.append(f"| {label} | {weights} | {values_text} | {recall:.3f} | {precision:.3f} |")
    lines.append("")
    data = {
        "grid_size": len(grid),
        "values": list(values),
        "folds": folds,
        "robust": robust,
        "references": reference_results,
    }
    return lines, data


def parse_weights(values: Sequence[str]) -> dict[str, float]:
    """Parse ``NAME=VALUE`` weight overrides."""

    names = (*COMPONENTS, "recency")
    weights: dict[str, float] = {}
    for item in values:
        name, separator, value = item.partition("=")
        name = name.strip()
        if not separator or name not in names:
            raise ValueError(f"expected NAME=VALUE with NAME in {', '.join(names)}: {item!r}")
        weights[name] = float(value)
    return weights


def build_screener(kind: str, overrides: Mapping[str, float]) -> HeuristicScreener:
    base = dict(LEGACY_WEIGHTS) if kind == "legacy" else {}
    base.update(overrides)
    return HeuristicScreener(**{f"{name}_weight": value for name, value in base.items()})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Re-run candidate screening offline on dumped candidate pools."
    )
    parser.add_argument(
        "--pools",
        type=Path,
        default=DEFAULT_POOLS_DIR,
        metavar="DIR",
        help="directory of <key>.pool.json files (default: eval/recall/pools)",
    )
    parser.add_argument("--only", metavar="KEY", help="evaluate one survey key")
    parser.add_argument(
        "--max-works", type=int, default=None, help="inclusion cap (default: the pool's request)"
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="score threshold (default: the pool's request)",
    )
    parser.add_argument(
        "--screener",
        choices=("default", "legacy"),
        default="default",
        help="base weights: current defaults or the pre-W25 weights (default: default)",
    )
    parser.add_argument(
        "--weight",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="override one weight (token, semantic, link, citation, cocitation, coupling, "
        "recency); repeatable",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="use the equivalent incremental selection instead of the runner's "
        "select_candidates (much faster on large pools; --grid always does)",
    )
    parser.add_argument(
        "--grid",
        action="store_true",
        help="search component weights and report leave-one-survey-out results",
    )
    parser.add_argument(
        "--grid-values",
        type=float,
        nargs="+",
        default=list(GRID_VALUES),
        metavar="W",
        help="weight values per grid component (default: 0 0.1 0.2 0.3 0.4)",
    )
    parser.add_argument("--json", type=Path, metavar="PATH", help="also write results as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.max_works is not None and args.max_works < 1:
        parser.error("--max-works must be at least 1")
    if args.min_score is not None and not 0.0 <= args.min_score <= 1.0:
        parser.error("--min-score must be between 0 and 1")
    try:
        overrides = parse_weights(args.weight)
    except ValueError as exc:
        parser.error(str(exc))
    pools = load_pools(args.pools, args.only)
    if not pools:
        parser.error(f"no candidate pools in {args.pools}; run run_recall.py --dump-pools first")

    if args.grid:
        if len(pools) < 2:
            parser.error("--grid needs at least two pools for leave-one-out")
        lines, data = grid_report(
            pools, values=args.grid_values, max_works=args.max_works, min_score=args.min_score
        )
    else:
        screener = build_screener(args.screener, overrides)
        if args.fast:
            results = [
                FastPool(pool).result(screener, max_works=args.max_works, min_score=args.min_score)
                for pool in pools
            ]
        else:
            results = [
                evaluate_pool(pool, screener, max_works=args.max_works, min_score=args.min_score)
                for pool in pools
            ]
        weights = ", ".join(
            f"{name} {value:.3f}" for name, value in screener.weights.items() if value
        )
        lines = _results_table(f"{args.screener} screener ({weights})", results)
        data = {"weights": screener.weights, "results": results}
    print("\n".join(lines))
    if args.json is not None:
        args.json.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

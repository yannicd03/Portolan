"""Time-sliced backtest of the frontier scores and gap hypotheses."""

from .backtest import backtest, evaluate_frontier, evaluate_gaps
from .slice import slice_view

__all__ = ["backtest", "evaluate_frontier", "evaluate_gaps", "slice_view"]

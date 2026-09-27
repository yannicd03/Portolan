"""Research run registry used by the HTTP API.

Runs execute in a process-local thread pool.  Every state change is also
persisted as JSON under ``settings.runs_dir/<project_id>/<run_id>.json`` so a
project's run history survives backend restarts.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ResearchRunRequest, Run, RunProgress

log = logging.getLogger("portolan.api.runs")

_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")
_PROJECT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_ACTIVE = frozenset({"queued", "running"})

# At most one progress write per run in this many seconds; other states always persist.
PROGRESS_WRITE_INTERVAL = 2.0
# Newest runs kept (on disk and in memory) per project.
MAX_RUNS_PER_PROJECT = 50
# Upper bound on the work ids listed in a run's incremental report.
MAX_ADDED_WORK_IDS = 200
INTERRUPTED_ERROR = "interrupted by a backend restart"


def _now() -> datetime:
    return datetime.now(UTC)


def _value(source: object | None, name: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _is_cancelled(error: BaseException) -> bool:
    """Recognise the pipeline's exception without importing it at module import time."""

    # The class-name check also lets API tests use a small fake exception while the
    # optional research package is being developed in parallel.
    if type(error).__name__ == "RunCancelled":
        return True
    try:
        from portolan.research import RunCancelled
    except Exception:
        return False
    return isinstance(error, RunCancelled)


def _valid_project_id(project_id: object) -> bool:
    return isinstance(project_id, str) and _PROJECT_ID.fullmatch(project_id) is not None


def _valid_run_id(run_id: object) -> bool:
    return isinstance(run_id, str) and _RUN_ID.fullmatch(run_id) is not None


def _runs_dir(settings: Any) -> Path | None:
    """Return the configured run directory, or ``None`` to disable persistence."""

    try:
        value = getattr(settings, "runs_dir", None)
    except (AssertionError, AttributeError):
        return None
    return None if value is None else Path(value)


def _write_atomic(path: Path, run: Run) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = run.model_dump_json(indent=2).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _decode(path: Path) -> Run | None:
    try:
        return Run.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _report_dict(report: Any) -> dict[str, Any] | None:
    if report is None:
        return None
    dump = getattr(report, "model_dump", None)
    if callable(dump):
        try:
            return dict(dump(mode="json"))
        except TypeError:
            return dict(dump())
    if isinstance(report, Mapping):
        return dict(report)
    try:
        from dataclasses import asdict, is_dataclass

        if is_dataclass(report) and not isinstance(report, type):
            return dict(asdict(report))
    except (ImportError, TypeError, ValueError):
        pass
    return {key: value for key, value in vars(report).items() if not key.startswith("_")}


@dataclass(slots=True)
class RunnerHandle:
    """A runner plus the request and cleanup selected by its factory."""

    runner: Any
    request: Any
    close: Callable[[], None] | None = None


class ActiveRunError(RuntimeError):
    """Raised when a project already has a queued or running research run."""


class RunRegistry:
    """Thread-safe registry and executor for research runs, persisted as JSON.

    Persistence is enabled when ``settings`` exposes ``runs_dir``.  Call
    :meth:`load` once at startup to restore earlier runs.
    """

    def __init__(
        self,
        graph: Any,
        settings: Any,
        runner_factory: Callable[..., Any],
    ) -> None:
        self._graph = graph
        self._settings = settings
        self._runner_factory = runner_factory
        max_workers = max(1, int(getattr(settings, "max_concurrent_runs", 1)))
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="portolan-research",
        )
        self._lock = threading.RLock()
        self._runs: dict[str, Run] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._active_by_project: dict[str, str] = {}
        self._closed = False
        self._runs_dir = _runs_dir(settings)
        self._last_write: dict[str, float] = {}

    # -- persistence -------------------------------------------------------

    def _path(self, project_id: str, run_id: str) -> Path | None:
        if self._runs_dir is None:
            return None
        if not _valid_project_id(project_id) or not _valid_run_id(run_id):
            return None
        return self._runs_dir / project_id / f"{run_id}.json"

    def _persist(self, run: Run, *, throttle: bool = False) -> None:
        """Write ``run`` to disk; callers hold ``self._lock``."""

        path = self._path(run.project_id, run.id)
        if path is None:
            return
        if throttle:
            last = self._last_write.get(run.id)
            if last is not None and time.monotonic() - last < PROGRESS_WRITE_INTERVAL:
                return
        try:
            _write_atomic(path, run)
        except OSError:
            log.warning("could not persist research run %s", run.id, exc_info=True)
            return
        if run.status in _ACTIVE:
            self._last_write[run.id] = time.monotonic()
        else:
            self._last_write.pop(run.id, None)

    def _prune(self, project_id: str) -> None:
        """Keep only the newest runs of a project; callers hold ``self._lock``."""

        runs = [run for run in self._runs.values() if run.project_id == project_id]
        if len(runs) <= MAX_RUNS_PER_PROJECT:
            return
        runs.sort(key=lambda run: (run.created_at, run.id), reverse=True)
        for run in runs[MAX_RUNS_PER_PROJECT:]:
            if run.status in _ACTIVE:
                continue
            self._runs.pop(run.id, None)
            self._cancel.pop(run.id, None)
            self._last_write.pop(run.id, None)
            path = self._path(run.project_id, run.id)
            if path is not None:
                with suppress(OSError):
                    path.unlink()

    def load(self) -> int:
        """Restore persisted runs; runs left queued or running are marked failed.

        Returns the number of runs loaded.
        """

        root = self._runs_dir
        if root is None or not root.is_dir():
            return 0
        now = _now()
        loaded = 0
        projects: set[str] = set()
        with self._lock:
            for project_dir in sorted(root.iterdir()):
                if not project_dir.is_dir() or not _valid_project_id(project_dir.name):
                    continue
                for path in sorted(project_dir.glob("*.json")):
                    if not _valid_run_id(path.stem) or path.stem in self._runs:
                        continue
                    run = _decode(path)
                    if run is None or run.id != path.stem or run.project_id != project_dir.name:
                        continue
                    if run.status in _ACTIVE:
                        run = run.model_copy(
                            update={
                                "status": "failed",
                                "error": INTERRUPTED_ERROR,
                                "finished_at": now,
                            }
                        )
                        self._persist(run)
                    self._runs[run.id] = run
                    projects.add(run.project_id)
                    loaded += 1
            for project_id in projects:
                self._prune(project_id)
        return loaded

    def delete_project_runs(self, project_id: str) -> None:
        """Forget a deleted project's finished runs and remove its run directory."""

        with self._lock:
            for run_id, run in list(self._runs.items()):
                if run.project_id == project_id and run.status not in _ACTIVE:
                    self._runs.pop(run_id, None)
                    self._cancel.pop(run_id, None)
                    self._last_write.pop(run_id, None)
            if self._runs_dir is None or not _valid_project_id(project_id):
                return
            if project_id in self._active_by_project:
                return
            directory = self._runs_dir / project_id
            if not directory.is_dir():
                return
            for path in directory.iterdir():
                if path.is_file():
                    with suppress(OSError):
                        path.unlink()
            with suppress(OSError):
                directory.rmdir()

    # -- public API --------------------------------------------------------

    def submit(self, project_id: str, request: ResearchRunRequest) -> Run:
        """Queue a run, rejecting a second active run for the same project."""

        with self._lock:
            if self._closed:
                raise RuntimeError("run registry is shut down")
            if project_id in self._active_by_project:
                raise ActiveRunError(project_id)
            run = Run(
                id=uuid.uuid4().hex,
                project_id=project_id,
                status="queued",
                request=request.model_copy(deep=True),
                created_at=_now(),
            )
            self._runs[run.id] = run
            self._cancel[run.id] = threading.Event()
            self._active_by_project[project_id] = run.id
            try:
                self._executor.submit(self._execute, run.id)
            except Exception:
                self._runs.pop(run.id, None)
                self._cancel.pop(run.id, None)
                self._active_by_project.pop(project_id, None)
                raise
            self._persist(run)
            self._prune(project_id)
            return run.model_copy(deep=True)

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            run = self._runs.get(run_id)
            return None if run is None else run.model_copy(deep=True)

    def list_for_project(self, project_id: str) -> list[Run]:
        with self._lock:
            runs = [run for run in self._runs.values() if run.project_id == project_id]
            runs.sort(key=lambda run: (run.created_at, run.id), reverse=True)
            return [run.model_copy(deep=True) for run in runs]

    def has_active(self, project_id: str) -> bool:
        with self._lock:
            run_id = self._active_by_project.get(project_id)
            if run_id is None:
                return False
            run = self._runs.get(run_id)
            return run is not None and run.status in {"queued", "running"}

    def cancel(self, run_id: str) -> Run | None:
        """Request cancellation and return the current run snapshot."""

        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            if run.status == "queued":
                now = _now()
                run = run.model_copy(update={"status": "cancelled", "finished_at": now})
                self._runs[run_id] = run
                self._active_by_project.pop(run.project_id, None)
                self._persist(run)
            elif run.status == "running":
                self._cancel[run_id].set()
            return run.model_copy(deep=True)

    def shutdown(self) -> None:
        """Cancel active work and stop all worker threads."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            now = _now()
            for run_id, run in list(self._runs.items()):
                if run.status == "queued":
                    self._runs[run_id] = run.model_copy(
                        update={"status": "cancelled", "finished_at": now}
                    )
                    self._persist(self._runs[run_id])
                elif run.status == "running":
                    self._cancel[run_id].set()
            self._active_by_project.clear()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _set_running(self, run_id: str) -> tuple[Run, threading.Event] | None:
        with self._lock:
            run = self._runs.get(run_id)
            cancel = self._cancel.get(run_id)
            if run is None or cancel is None or run.status != "queued":
                return None
            if cancel.is_set():
                now = _now()
                self._runs[run_id] = run.model_copy(
                    update={"status": "cancelled", "finished_at": now}
                )
                self._active_by_project.pop(run.project_id, None)
                self._persist(self._runs[run_id])
                return None
            started = _now()
            run = run.model_copy(update={"status": "running", "started_at": started})
            self._runs[run_id] = run
            self._persist(run)
            return run, cancel

    def _finish(
        self,
        run_id: str,
        *,
        status: str,
        report: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            finished = run.model_copy(
                update={
                    "status": status,
                    "report": report,
                    "error": error,
                    "finished_at": _now(),
                }
            )
            self._runs[run_id] = finished
            self._active_by_project.pop(run.project_id, None)
            self._persist(finished)

    def _progress_callback(self, run_id: str, progress: Any) -> None:
        stage = str(_value(progress, "stage", ""))
        message = str(_value(progress, "message", ""))
        raw_counts = _value(progress, "counts", {}) or {}
        if isinstance(raw_counts, Mapping):
            counts: dict[str, int] = {}
            for key, value in raw_counts.items():
                try:
                    counts[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue
        else:
            counts = {}
        event = RunProgress(stage=stage, message=message, counts=counts, at=_now())
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.status not in {"queued", "running"}:
                return
            events = [*run.progress, event][-50:]
            updated = run.model_copy(update={"progress": events})
            self._runs[run_id] = updated
            self._persist(updated, throttle=True)

    def _work_ids(self, project_id: str) -> set[str] | None:
        """Snapshot a project's included work ids, or ``None`` if unavailable."""

        try:
            works = self._graph.project_works(project_id)
        except Exception:
            log.warning("could not snapshot works of project %s", project_id, exc_info=True)
            return None
        ids = {_value(work, "id") for work in works or ()}
        return {str(work_id) for work_id in ids if work_id is not None}

    def _with_increment(
        self,
        project_id: str,
        before: set[str] | None,
        report: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Add what this run contributed relative to the project's earlier works."""

        if before is None or report is None:
            return report
        after = self._work_ids(project_id)
        if after is None:
            return report
        added = sorted(after - before)
        return {
            **report,
            "works_before": len(before),
            "works_after": len(after),
            "works_added": len(added),
            "added_work_ids": added[:MAX_ADDED_WORK_IDS],
        }

    @staticmethod
    def _call_factory(
        factory: Callable[..., Any], graph: Any, settings: Any, request: ResearchRunRequest
    ) -> Any:
        """Call injected factories while keeping the documented 3-argument form primary."""

        try:
            parameters = list(inspect.signature(factory).parameters.values())
        except (TypeError, ValueError):
            return factory(graph, settings, request)

        positional = [
            parameter
            for parameter in parameters
            if parameter.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        accepts_varargs = any(
            parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters
        )
        if accepts_varargs or len(positional) >= 3:
            return factory(graph, settings, request)
        if len(positional) == 2:
            second_name = positional[1].name.casefold()
            if "request" in second_name:
                return factory(graph, request)
            return factory(graph, settings)
        if len(positional) == 1:
            return factory(graph)
        return factory()

    @staticmethod
    def _normalise_factory_result(result: Any, request: ResearchRunRequest) -> RunnerHandle:
        if isinstance(result, RunnerHandle):
            return result
        if isinstance(result, tuple) and len(result) == 2 and callable(result[1]):
            return RunnerHandle(result[0], request, result[1])
        return RunnerHandle(result, request)

    def _execute(self, run_id: str) -> None:
        running = self._set_running(run_id)
        if running is None:
            return
        run, cancel = running
        handle: RunnerHandle | None = None
        try:
            result = self._call_factory(
                self._runner_factory,
                self._graph,
                self._settings,
                run.request,
            )
            handle = self._normalise_factory_result(result, run.request)
            if cancel.is_set():
                self._finish(run_id, status="cancelled")
                return
            before = self._work_ids(run.project_id)
            report = handle.runner.run(
                run.project_id,
                handle.request,
                progress=lambda event: self._progress_callback(run_id, event),
                cancel=cancel,
            )
            if cancel.is_set():
                self._finish(run_id, status="cancelled")
            else:
                self._finish(
                    run_id,
                    status="succeeded",
                    report=self._with_increment(run.project_id, before, _report_dict(report)),
                )
        except BaseException as error:
            if _is_cancelled(error) or cancel.is_set():
                self._finish(run_id, status="cancelled")
            else:
                detail = f"{type(error).__name__}: {error}"
                log.exception("research run %s failed", run_id)
                self._finish(run_id, status="failed", error=detail)
        finally:
            if handle is not None and handle.close is not None:
                try:
                    handle.close()
                except Exception:
                    log.exception("research run %s cleanup failed", run_id)


__all__ = ["ActiveRunError", "RunRegistry", "RunnerHandle"]

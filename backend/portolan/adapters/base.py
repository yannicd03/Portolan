"""Shared HTTP, caching, and normalization helpers for scholarly adapters.

The adapters in this package return a deliberately small, source-neutral record:

``identifiers``
    A mapping of known identifier kinds (currently ``doi``, ``arxiv``, ``s2``,
    and ``url``) to their values.
``title`` / ``year`` / ``abstract`` / ``venue``
    The normalized bibliographic fields.  Missing values are ``None``.
``publication_types``
    A list of source-provided publication type labels.
``open_access_pdf_url`` / ``tldr``
    Optional access and summary fields.
``source``
    The adapter source name.
``raw`` / ``raw_body``
    The source-native parsed payload and the exact response body, respectively.

The HTTP client owns all mutable state.  In particular, rate-limit timestamps,
the cache, and the ``httpx.Client`` are per-instance rather than module globals;
this keeps tests deterministic and permits separate offline snapshots.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx
import structlog
from tenacity import RetryCallState, Retrying, retry_if_exception_type, stop_after_attempt

DEFAULT_SOURCE_INTERVALS: dict[str, float] = {
    # arXiv asks for one request per 3 seconds; measured 2026-09-22, bursts at that rate still
    # draw 406s partway through a run, so the floor is 5s with 406 treated as a throttle signal.
    "arxiv": 5.0,
    "semanticscholar": 1.0,
    "crossref": 1.0,
}

#: Statuses that mean "back off and try again" rather than "this request is wrong".
#: arXiv answers a throttled request with 406 Not Acceptable rather than 429 — verified
#: 2026-09-22: the same URL returns 200 from curl at rest under every Accept header and
#: User-Agent tried, and only 406s partway through a sustained run.
EXTRA_RETRY_STATUSES: dict[str, frozenset[int]] = {
    "arxiv": frozenset({406}),
}


def _jsonable(value: Any) -> Any:
    """Convert common query values into deterministic JSON-compatible values."""

    if isinstance(value, Mapping):
        return {str(key): _jsonable(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        converted = [_jsonable(item) for item in value]
        if converted and all(
            isinstance(item, (list, tuple)) and len(item) == 2 for item in converted
        ):
            return sorted(converted, key=lambda item: (str(item[0]), repr(item[1])))
        return converted
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(item) for item in value), key=lambda item: repr(item))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def stable_cache_key(source: str, endpoint: str, params: Mapping[str, Any] | None = None) -> str:
    """Return the stable cache key for a source, endpoint, and sorted parameters."""

    material = {
        "source": source,
        "endpoint": endpoint,
        "params": _jsonable(params or {}),
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheEntry:
    """A raw cached response and its small metadata sidecar."""

    body: bytes
    metadata: dict[str, Any]


class OfflineCacheMissError(RuntimeError):
    """Raised when an offline request has no complete cached response."""

    def __init__(self, source: str, endpoint: str, key: str, cache_dir: Path) -> None:
        self.source = source
        self.endpoint = endpoint
        self.key = key
        self.cache_dir = cache_dir
        super().__init__(
            "offline cache miss for "
            f"source={source!r}, endpoint={endpoint!r}, key={key}; "
            f"looked in {cache_dir}"
        )


class ResponseCache:
    """Filesystem cache storing response bodies and JSON metadata sidecars."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._log = structlog.get_logger(__name__).bind(component="response_cache")

    def key_for(self, source: str, endpoint: str, params: Mapping[str, Any] | None = None) -> str:
        return stable_cache_key(source, endpoint, params)

    def paths_for(
        self, source: str, endpoint: str, params: Mapping[str, Any] | None = None
    ) -> tuple[Path, Path]:
        key = self.key_for(source, endpoint, params)
        return self.directory / f"{key}.body", self.directory / f"{key}.json"

    def get(
        self, source: str, endpoint: str, params: Mapping[str, Any] | None = None
    ) -> CacheEntry | None:
        body_path, metadata_path = self.paths_for(source, endpoint, params)
        if not body_path.is_file() or not metadata_path.is_file():
            return None

        try:
            body = body_path.read_bytes()
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            self._log.warning("invalid_cache_entry", body_path=str(body_path), error=str(exc))
            return None

        if not isinstance(metadata, dict):
            self._log.warning("invalid_cache_metadata", metadata_path=str(metadata_path))
            return None
        return CacheEntry(body=body, metadata=metadata)

    def put(
        self,
        source: str,
        endpoint: str,
        params: Mapping[str, Any] | None,
        *,
        body: bytes,
        url: str,
        status: int,
        fetched_at: str | None = None,
    ) -> CacheEntry:
        body_path, metadata_path = self.paths_for(source, endpoint, params)
        self.directory.mkdir(parents=True, exist_ok=True)
        metadata = {
            "url": url,
            "status": int(status),
            "fetched_at": fetched_at or datetime.now(UTC).isoformat(),
        }

        # Replace each file atomically enough for a reader to see either the old
        # complete entry or the new one.  A cache miss is preferable to a partial
        # body/metadata pair, which ``get`` treats as incomplete.
        body_tmp = body_path.with_suffix(body_path.suffix + ".tmp")
        metadata_tmp = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
        body_tmp.write_bytes(body)
        metadata_tmp.write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        body_tmp.replace(body_path)
        metadata_tmp.replace(metadata_path)
        return CacheEntry(body=body, metadata=metadata)


class RateLimiter:
    """Per-source minimum-interval limiter with injectable time functions."""

    def __init__(
        self,
        intervals: Mapping[str, float] | None = None,
        *,
        min_intervals: Mapping[str, float] | None = None,
        default_interval: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.intervals = dict(DEFAULT_SOURCE_INTERVALS)
        for configured in (intervals, min_intervals):
            if configured:
                self.intervals.update({str(key): float(value) for key, value in configured.items()})
        self.default_interval = float(default_interval)
        self.clock = clock
        self.sleep = sleep
        self._last_request: dict[str, float] = {}

    def interval_for(self, source: str) -> float:
        if source in self.intervals:
            return max(0.0, self.intervals[source])
        lowered = source.lower()
        if lowered in self.intervals:
            return max(0.0, self.intervals[lowered])
        return max(0.0, self.default_interval)

    def wait(self, source: str) -> None:
        interval = self.interval_for(source)
        now = float(self.clock())
        previous = self._last_request.get(source)
        if previous is not None and interval:
            target = previous + interval
            if now < target:
                delay = target - now
                self.sleep(delay)
                # Injectable sleeps often only record their argument.  Recording
                # the scheduled time keeps the limiter deterministic in that case.
                now = target
        self._last_request[source] = now


def parse_retry_after(value: str | None, *, now: Callable[[], float] = time.time) -> float | None:
    """Parse a Retry-After seconds value or HTTP date into a non-negative delay."""

    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(0.0, retry_at.timestamp() - float(now()))


class RetryableHTTPError(httpx.HTTPStatusError):
    """An HTTP status error that the client is configured to retry."""


class _ExponentialRetryWait:
    def __init__(
        self,
        *,
        multiplier: float,
        maximum: float,
        wall_clock: Callable[[], float],
    ) -> None:
        self.multiplier = max(0.0, multiplier)
        self.maximum = max(0.0, maximum)
        self.wall_clock = wall_clock

    def __call__(self, retry_state: RetryCallState) -> float:
        exception: BaseException | None = None
        if retry_state.outcome is not None:
            exception = retry_state.outcome.exception()
        if isinstance(exception, RetryableHTTPError):
            retry_after = parse_retry_after(
                exception.response.headers.get("Retry-After"), now=self.wall_clock
            )
            if retry_after is not None:
                return retry_after

        attempt = max(1, retry_state.attempt_number)
        return min(self.maximum, self.multiplier * (2 ** (attempt - 1)))


class HttpClient:
    """Instance-owned httpx client with rate limiting, retries, and raw caching.

    ``cache_dir`` is intentionally a constructor argument so callers decide where
    an offline snapshot lives.  Supplying ``transport`` or ``http_client`` is
    useful for tests; an injected ``httpx.Client`` remains owned by its caller.
    """

    def __init__(
        self,
        cache_dir: Path | str = ".cache/portolan-http",
        *,
        cache: ResponseCache | None = None,
        offline: bool = False,
        refresh: bool = False,
        source_intervals: Mapping[str, float] | None = None,
        intervals: Mapping[str, float] | None = None,
        min_intervals: Mapping[str, float] | None = None,
        min_request_intervals: Mapping[str, float] | None = None,
        default_interval: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], float] = time.time,
        transport: httpx.BaseTransport | None = None,
        http_client: httpx.Client | None = None,
        client: httpx.Client | None = None,
        timeout: float | httpx.Timeout = 30.0,
        max_attempts: int = 4,
        backoff_multiplier: float = 0.5,
        backoff_max: float = 30.0,
    ) -> None:
        if http_client is not None and client is not None:
            raise ValueError("pass only one of http_client or client")
        self.cache = cache or ResponseCache(cache_dir)
        self.offline = bool(offline)
        self.refresh = bool(refresh)
        configured_intervals = source_intervals if source_intervals is not None else intervals
        if min_request_intervals is not None:
            configured_intervals = {
                **(configured_intervals or {}),
                **min_request_intervals,
            }
        self.rate_limiter = RateLimiter(
            configured_intervals,
            min_intervals=min_intervals,
            default_interval=default_interval,
            clock=clock,
            sleep=sleep,
        )
        self.sleep = sleep
        self.wall_clock = wall_clock
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_multiplier = float(backoff_multiplier)
        self.backoff_max = float(backoff_max)
        self._log = structlog.get_logger(__name__).bind(component="http_client")
        self._owns_http_client = http_client is None and client is None
        self._http = (
            http_client
            or client
            or httpx.Client(
                transport=transport,
                timeout=timeout,
                follow_redirects=True,
            )
        )

    def close(self) -> None:
        if self._owns_http_client:
            self._http.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def _cached_response(
        self,
        method: str,
        endpoint: str,
        params: Mapping[str, Any] | None,
        entry: CacheEntry,
    ) -> httpx.Response:
        metadata_url = str(entry.metadata.get("url") or endpoint)
        try:
            status = int(entry.metadata.get("status", 200))
        except (TypeError, ValueError):
            status = 200
        request = httpx.Request(
            method,
            metadata_url,
            params=params if "?" not in metadata_url else None,
        )
        return httpx.Response(
            status_code=status,
            content=entry.body,
            headers={"X-PTL-Cache": "HIT"},
            request=request,
        )

    def _request_once(
        self,
        *,
        source: str,
        method: str,
        url: str,
        params: Mapping[str, Any] | None,
        json_body: Any,
        headers: Mapping[str, str] | None,
        timeout: float | httpx.Timeout | None,
    ) -> httpx.Response:
        self.rate_limiter.wait(source)
        response = self._http.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=timeout,
        )
        extra_retry = EXTRA_RETRY_STATUSES.get(source, frozenset())
        if (
            response.status_code == 429
            or response.status_code >= 500
            or response.status_code in extra_retry
        ):
            request = response.request or httpx.Request(method, url, params=params)
            raise RetryableHTTPError(
                f"retryable HTTP status {response.status_code} for {url}",
                request=request,
                response=response,
            )
        response.raise_for_status()
        return response

    def request(
        self,
        source: str,
        endpoint: str,
        *,
        method: str = "GET",
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        cache_params: Mapping[str, Any] | None = None,
        offline: bool | None = None,
        refresh: bool | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response:
        """Make a request or serve the matching raw response from the cache."""

        method = method.upper()
        effective_offline = self.offline if offline is None else bool(offline)
        effective_refresh = self.refresh if refresh is None else bool(refresh)
        cache_key_params: Mapping[str, Any] | None = cache_params
        if cache_key_params is None:
            cache_key_params = params
            if method != "GET" and json is not None:
                cache_key_params = {"params": params or {}, "json": json}
        key = self.cache.key_for(source, endpoint, cache_key_params)

        if not effective_refresh or effective_offline:
            cached = self.cache.get(source, endpoint, cache_key_params)
            if cached is not None:
                self._log.debug(
                    "cache_hit",
                    source=source,
                    endpoint=endpoint,
                    key=key,
                    offline=effective_offline,
                )
                response = self._cached_response(method, endpoint, params, cached)
                response.raise_for_status()
                return response
        if effective_offline:
            self._log.warning("offline_cache_miss", source=source, endpoint=endpoint, key=key)
            raise OfflineCacheMissError(source, endpoint, key, self.cache.directory)

        self._log.debug("http_request", source=source, endpoint=endpoint, key=key)
        retrying = Retrying(
            retry=retry_if_exception_type(RetryableHTTPError),
            wait=_ExponentialRetryWait(
                multiplier=self.backoff_multiplier,
                maximum=self.backoff_max,
                wall_clock=self.wall_clock,
            ),
            stop=stop_after_attempt(self.max_attempts),
            sleep=self.sleep,
            reraise=True,
            before_sleep=self._before_sleep,
        )
        response = retrying(
            self._request_once,
            source=source,
            method=method,
            url=endpoint,
            params=params,
            json_body=json,
            headers=headers,
            timeout=timeout,
        )
        self.cache.put(
            source,
            endpoint,
            cache_key_params,
            body=response.content,
            url=str(response.url),
            status=response.status_code,
        )
        return response

    def _before_sleep(self, retry_state: RetryCallState) -> None:
        exception = retry_state.outcome.exception() if retry_state.outcome else None
        self._log.warning(
            "http_retry",
            attempt=retry_state.attempt_number,
            error=str(exception),
            sleep=getattr(retry_state, "next_action", None).sleep
            if getattr(retry_state, "next_action", None)
            else None,
        )

    def get(
        self,
        source: str,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        cache_params: Mapping[str, Any] | None = None,
        offline: bool | None = None,
        refresh: bool | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response:
        return self.request(
            source,
            endpoint,
            method="GET",
            params=params,
            headers=headers,
            cache_params=cache_params,
            offline=offline,
            refresh=refresh,
            timeout=timeout,
        )

    def post(
        self,
        source: str,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        cache_params: Mapping[str, Any] | None = None,
        offline: bool | None = None,
        refresh: bool | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response:
        return self.request(
            source,
            endpoint,
            method="POST",
            params=params,
            json=json,
            headers=headers,
            cache_params=cache_params,
            offline=offline,
            refresh=refresh,
            timeout=timeout,
        )

    fetch = request


def clean_text(value: Any) -> str | None:
    """Normalize whitespace and lightweight markup in source text."""

    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def parse_year(value: Any) -> int | None:
    """Extract a four-digit publication year from common API date shapes."""

    if value is None:
        return None
    if isinstance(value, Mapping):
        date_parts = value.get("date-parts") or value.get("date_parts")
        if isinstance(date_parts, Sequence) and not isinstance(date_parts, (str, bytes)):
            first = date_parts[0] if date_parts else None
            if isinstance(first, Sequence) and not isinstance(first, (str, bytes)):
                return parse_year(first[0] if first else None)
        for key in ("date", "value", "year"):
            parsed = parse_year(value.get(key))
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, (int, float)):
        year = int(value)
        return year if 1000 <= year <= 9999 else None
    match = re.search(r"(?<!\d)(\d{4})(?!\d)", str(value))
    return int(match.group(1)) if match else None


def canonical_arxiv_id(value: Any) -> str | None:
    """Return a stable arXiv identifier without a URL, prefix, or version suffix."""

    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    candidate = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", candidate, flags=re.I)
    candidate = re.sub(r"^arxiv:\s*", "", candidate, flags=re.I)
    candidate = candidate.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    candidate = re.sub(r"\.pdf$", "", candidate, flags=re.I)
    candidate = re.sub(r"v\d+$", "", candidate, flags=re.I)
    return candidate or None


def normalized_record(
    *,
    source: str,
    identifiers: Mapping[str, Any] | None,
    title: Any,
    year: Any,
    abstract: Any,
    venue: Any,
    publication_types: Sequence[Any] | None,
    open_access_pdf_url: Any,
    tldr: Any,
    raw: Any,
    raw_body: str | None = None,
) -> dict[str, Any]:
    """Build the common plain-dict record returned by every adapter."""

    normalized_identifiers = {
        str(key): str(value).strip()
        for key, value in (identifiers or {}).items()
        if value is not None and str(value).strip()
    }
    types = [clean_text(item) for item in (publication_types or [])]
    return {
        "identifiers": normalized_identifiers,
        "title": clean_text(title),
        "year": parse_year(year),
        "abstract": clean_text(abstract),
        "venue": clean_text(venue),
        "publication_types": [item for item in types if item],
        "open_access_pdf_url": str(open_access_pdf_url).strip() if open_access_pdf_url else None,
        "tldr": clean_text(tldr),
        "source": source,
        "raw": raw,
        "raw_body": raw_body,
    }


# A couple of descriptive aliases make the small public surface easy to discover
# without maintaining separate implementations.
CachedHttpClient = HttpClient
BaseHTTPClient = HttpClient
HTTPClient = HttpClient
OfflineCacheMiss = OfflineCacheMissError
OfflineCacheError = OfflineCacheMissError
CacheMissError = OfflineCacheMissError

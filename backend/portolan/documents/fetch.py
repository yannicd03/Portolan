"""Open-access PDF candidate ranking and acquisition.

The fetcher deliberately treats each candidate as an independent attempt.  A
landing page, a paywalled response, or a transient server error is recorded in
the result so callers can inspect why the next candidate was selected.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx

from portolan.adapters.base import parse_retry_after

from .models import FetchAttempt, FetchResult, PdfCandidate
from .store import DocumentStore

__all__ = ["PdfFetcher", "rank_candidates"]


_DEFAULT_HOST_INTERVALS: dict[str, float] = {
    "arxiv.org": 5.0,
    "export.arxiv.org": 5.0,
}
_DEFAULT_INTERVAL = 1.0
_RETRY_STATUSES = frozenset({429})


def _host_from_url(url: str) -> str:
    """Return a lowercase hostname, or an empty string for malformed URLs."""

    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _normalise_netloc(parts: SplitResult) -> str:
    """Canonicalise the host portion while retaining optional credentials."""

    try:
        hostname = parts.hostname
        if not hostname:
            return parts.netloc.lower()
        hostname = hostname.lower().rstrip(".")
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = parts.port
    except ValueError:
        return parts.netloc.lower()

    userinfo = ""
    if "@" in parts.netloc:
        userinfo = parts.netloc.rsplit("@", 1)[0] + "@"
    default_port = (parts.scheme.lower() == "http" and port == 80) or (
        parts.scheme.lower() == "https" and port == 443
    )
    port_text = "" if port is None or default_port else f":{port}"
    return f"{userinfo}{hostname}{port_text}"


def _normalise_url(url: str) -> str:
    """Normalise URL spelling used for ranking and duplicate detection.

    arXiv abstract URLs are changed to their direct PDF endpoint.  Fragments
    are dropped because they do not affect the downloaded representation.
    """

    raw = str(url).strip()
    if not raw:
        return raw

    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw

    # A scheme-less arXiv URL is common in metadata exports.  Treat it like a
    # regular HTTPS URL so it can still be ranked and fetched.
    if (
        not parts.netloc
        and not parts.scheme
        and raw.lower().startswith(("arxiv.org/", "export.arxiv.org/"))
    ):
        try:
            parts = urlsplit(f"https://{raw}")
        except ValueError:
            return raw

    host = _host_from_url(raw)
    if not host and parts.netloc:
        host = (parts.hostname or "").lower().rstrip(".")
    scheme = parts.scheme.lower()
    netloc = _normalise_netloc(parts)
    path = parts.path

    if host in {"arxiv.org", "export.arxiv.org"} and path.startswith("/abs/"):
        identifier = path[len("/abs/") :].strip("/")
        path = f"/pdf/{identifier}"
        scheme = "https"

    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _copy_candidate_with_url(candidate: PdfCandidate, url: str) -> PdfCandidate:
    """Return a candidate carrying ``url`` without mutating the input model."""

    if candidate.url == url:
        return candidate

    model_copy = getattr(candidate, "model_copy", None)
    if callable(model_copy):
        return model_copy(update={"url": url})

    try:
        return replace(candidate, url=url)
    except TypeError:
        return PdfCandidate(
            url=url,
            source=candidate.source,
            version=candidate.version,
            license=candidate.license,
            host_type=candidate.host_type,
        )


def _candidate_rank(candidate: PdfCandidate, normalised_url: str) -> int:
    host = _host_from_url(normalised_url)
    if host in {"arxiv.org", "export.arxiv.org"}:
        return 0

    # OpenAlex content URLs are paid downloads.  They remain usable as a last
    # resort, but should never displace a repository or publisher OA URL.
    if host == "content.openalex.org" or host.endswith(".content.openalex.org"):
        return 4

    version = str(candidate.version or "").strip().lower()
    if version == "publishedversion":
        return 1
    if version == "acceptedversion":
        return 2
    return 3


def rank_candidates(candidates: Iterable[PdfCandidate]) -> list[PdfCandidate]:
    """Return stable, de-duplicated candidates in preferred acquisition order."""

    ranked: list[tuple[int, int, str, PdfCandidate]] = []
    for index, candidate in enumerate(candidates):
        normalised_url = _normalise_url(candidate.url)
        ranked.append(
            (
                _candidate_rank(candidate, normalised_url),
                index,
                normalised_url,
                _copy_candidate_with_url(candidate, normalised_url),
            )
        )

    ranked.sort(key=lambda item: (item[0], item[1]))
    result: list[PdfCandidate] = []
    seen_urls: set[str] = set()
    for _, _, normalised_url, candidate in ranked:
        if normalised_url in seen_urls:
            continue
        seen_urls.add(normalised_url)
        result.append(candidate)
    return result


class PdfFetcher:
    """Download the first usable open-access PDF into a :class:`DocumentStore`."""

    def __init__(
        self,
        store: DocumentStore,
        *,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        contact_email: str | None = None,
        max_bytes: int = 50 * 1024 * 1024,
        host_intervals: Mapping[str, float] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 60.0,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("pass either client or transport, not both")

        self.store = store
        self.max_bytes = int(max_bytes)
        self.clock = clock
        self.sleep = sleep
        self.timeout = timeout
        configured_email = contact_email
        if configured_email is None:
            configured_email = os.getenv("PORTOLAN_CONTACT_EMAIL")
        configured_email = configured_email.strip() if configured_email else None

        user_agent = "Portolan/0.1 (+https://github.com/yannicd03/Portolan"
        if configured_email:
            user_agent += f"; mailto:{configured_email}"
        self.user_agent = f"{user_agent})"

        self._host_intervals = dict(_DEFAULT_HOST_INTERVALS)
        if host_intervals:
            self._host_intervals.update(
                {
                    str(host).lower().rstrip("."): max(0.0, float(interval))
                    for host, interval in host_intervals.items()
                }
            )
        self._last_request: dict[str, float] = {}

        if client is None:
            self._client = httpx.Client(
                transport=transport,
                timeout=timeout,
                follow_redirects=True,
            )
            self._owns_client = True
        else:
            self._client = client
            self._owns_client = False
        self._closed = False

    @staticmethod
    def rank_candidates(candidates: Iterable[PdfCandidate]) -> list[PdfCandidate]:
        """Expose :func:`rank_candidates` as a convenient class-level helper."""

        return rank_candidates(candidates)

    def __enter__(self) -> PdfFetcher:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close only the HTTP client created by this fetcher."""

        if self._owns_client and not self._closed:
            self._client.close()
            self._closed = True

    def _interval_for(self, host: str) -> float:
        return max(0.0, self._host_intervals.get(host, _DEFAULT_INTERVAL))

    def _wait_for_host(self, host: str) -> None:
        """Wait until the host's minimum interval has elapsed."""

        interval = self._interval_for(host)
        now = float(self.clock())
        previous = self._last_request.get(host)
        if previous is not None and interval:
            target = previous + interval
            if now < target:
                self.sleep(target - now)
                # Test clocks often advance inside sleep; keeping the scheduled
                # timestamp also works with sleeps that only record their input.
                now = max(float(self.clock()), target)
        self._last_request[host] = now

    def _network_attempt(self, url: str, detail: str, status: int | None = None) -> FetchAttempt:
        return FetchAttempt(url=url, status=status, outcome="network_error", detail=detail)

    def _request_once(
        self, candidate: PdfCandidate
    ) -> tuple[FetchAttempt, bytes | None, float | None]:
        """Issue one request and return its attempt, body, and retry delay."""

        url = candidate.url
        host = _host_from_url(url)
        self._wait_for_host(host)

        response: httpx.Response | None = None
        try:
            with self._client.stream(
                "GET",
                url,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
            ) as response:
                status = response.status_code
                if status < 200 or status >= 300:
                    retry_after: float | None = None
                    if status in _RETRY_STATUSES or 500 <= status <= 599:
                        retry_after = parse_retry_after(response.headers.get("Retry-After"))
                        if retry_after is not None:
                            retry_after = min(30.0, max(0.0, retry_after))
                    detail = f"HTTP {status}"
                    return (
                        FetchAttempt(
                            url=url,
                            status=status,
                            outcome="http_error",
                            detail=detail,
                        ),
                        None,
                        retry_after,
                    )

                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except (TypeError, ValueError):
                        declared_length = -1
                    if declared_length > self.max_bytes:
                        detail = (
                            f"Content-Length {declared_length} exceeds max_bytes {self.max_bytes}"
                        )
                        return (
                            FetchAttempt(
                                url=url,
                                status=status,
                                outcome="too_large",
                                detail=detail,
                            ),
                            None,
                            None,
                        )

                body = bytearray()
                prefix = bytearray()
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    if len(body) + len(chunk) > self.max_bytes:
                        detail = f"response exceeds max_bytes {self.max_bytes}"
                        return (
                            FetchAttempt(
                                url=url,
                                status=status,
                                outcome="too_large",
                                detail=detail,
                            ),
                            None,
                            None,
                        )
                    body.extend(chunk)
                    if len(prefix) < 5:
                        prefix.extend(chunk[: 5 - len(prefix)])
                        if len(prefix) == 5 and bytes(prefix) != b"%PDF-":
                            return (
                                FetchAttempt(
                                    url=url,
                                    status=status,
                                    outcome="not_pdf",
                                    detail="response does not start with %PDF-",
                                ),
                                None,
                                None,
                            )

                if bytes(prefix) != b"%PDF-":
                    return (
                        FetchAttempt(
                            url=url,
                            status=status,
                            outcome="not_pdf",
                            detail="response does not start with %PDF-",
                        ),
                        None,
                        None,
                    )
                return (
                    FetchAttempt(url=url, status=status, outcome="ok", detail=None),
                    bytes(body),
                    None,
                )
        except Exception as exc:
            status = response.status_code if response is not None else None
            detail = str(exc) or repr(exc)
            return self._network_attempt(url, detail, status), None, None

    def fetch_first(self, candidates: Iterable[PdfCandidate]) -> FetchResult:
        """Fetch and store the first valid PDF, recording all candidate attempts."""

        attempts: list[FetchAttempt] = []
        for candidate in rank_candidates(candidates):
            retry_count = 0
            while True:
                attempt, body, retry_after = self._request_once(candidate)
                attempts.append(attempt)

                if body is not None and attempt.outcome == "ok":
                    try:
                        document = self.store.put_pdf(
                            body,
                            source_url=candidate.url,
                            source=candidate.source,
                            version=candidate.version,
                            license=candidate.license,
                        )
                    except Exception as exc:
                        # There is no separate store_error in the public attempt
                        # enum.  Keep the failure associated with this candidate
                        # and continue to the next source rather than aborting a
                        # multi-candidate acquisition.
                        attempts[-1] = self._network_attempt(
                            candidate.url,
                            f"store: {str(exc) or repr(exc)}",
                            attempt.status,
                        )
                        break
                    return FetchResult(document=document, attempts=attempts)

                is_retryable = (
                    attempt.outcome == "http_error"
                    and attempt.status is not None
                    and (attempt.status in _RETRY_STATUSES or 500 <= attempt.status <= 599)
                )
                if is_retryable and retry_count == 0:
                    retry_count += 1
                    if retry_after is not None:
                        self.sleep(min(30.0, max(0.0, retry_after)))
                    continue
                break

        return FetchResult(document=None, attempts=attempts)

"""Offline contract tests for the scholarly-source adapters.

The adapter implementations are deliberately injected with an ``httpx`` mock
transport.  This keeps the tests useful in CI even when the source APIs are
unavailable and makes every payload used by the tests reviewable in
``tests/fixtures/adapters``.

The small compatibility helpers in this module allow the tests to work with
the natural spellings of the public API (``CachedHttpClient``/``HttpClient``,
``get_by_id``/``lookup``, and so on) while still failing loudly if an adapter
does not expose the behavior required by the task.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from portolan.adapters import arxiv as arxiv_module
from portolan.adapters import base as base_module
from portolan.adapters import crossref as crossref_module
from portolan.adapters import semanticscholar as semanticscholar_module

FIXTURES = Path(__file__).parent / "fixtures" / "adapters"
_MISSING = object()


class FakeClock:
    """Monotonic clock and sleeper used to prove timing without real sleeps."""

    def __init__(self, start: float = 100.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _read_json_fixture(name: str) -> dict[str, Any]:
    return json.loads(_read_fixture(name))


def _pick(module: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        value = getattr(module, name, None)
        if value is not None:
            return value
    raise AssertionError(f"{module.__name__} must expose one of: {', '.join(names)}")


def _accepted_parameters(factory: Any) -> tuple[set[str], bool]:
    signature = inspect.signature(factory)
    parameters = set(signature.parameters)
    has_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    return parameters, has_kwargs


def _filtered_kwargs(factory: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    parameters, has_kwargs = _accepted_parameters(factory)
    if has_kwargs:
        return dict(values)
    return {key: value for key, value in values.items() if key in parameters}


def _make_client(
    cache_dir: Path,
    transport: httpx.BaseTransport,
    *,
    offline: bool = False,
    clock: FakeClock | None = None,
    intervals: Mapping[str, float] | None = None,
) -> Any:
    client_class = _pick(base_module, ("CachedHttpClient", "HttpClient", "BaseHttpClient"))
    clock = clock or FakeClock()
    values = {
        "cache_dir": cache_dir,
        "offline": offline,
        "transport": transport,
        "http_transport": transport,
        "clock": clock,
        "monotonic": clock,
        "sleep": clock.sleep,
        "sleeper": clock.sleep,
        "intervals": dict(intervals or {}),
        "source_intervals": dict(intervals or {}),
        "min_intervals": dict(intervals or {}),
        "minimum_intervals": dict(intervals or {}),
        "per_source_intervals": dict(intervals or {}),
        "rate_limits": dict(intervals or {}),
        "max_retries": 2,
        "retry_attempts": 3,
    }
    kwargs = _filtered_kwargs(client_class, values)
    if "transport" not in kwargs and "http_transport" not in kwargs:
        http_client = httpx.Client(transport=transport)
        values["client"] = http_client
        values["http_client"] = http_client
        kwargs = _filtered_kwargs(client_class, values)
    try:
        return client_class(**kwargs)
    except TypeError as error:
        # A constructor which has a required positional transport/client still
        # has an injectable seam; try the two common positional spellings.
        try:
            return client_class(cache_dir, transport, offline=offline)
        except TypeError as positional_error:
            raise error from positional_error


def _client_get(
    client: Any,
    source: str,
    endpoint: str,
    *,
    params: Mapping[str, Any] | None = None,
) -> Any:
    method = getattr(client, "get", None)
    if method is None:
        method = getattr(client, "fetch", None)
    if method is None:
        raise AssertionError("the shared HTTP client must expose get() or fetch()")

    signature = inspect.signature(method)
    names = list(signature.parameters)
    kwargs = {"params": dict(params or {})}
    if "source" in names:
        kwargs["source"] = source
    if "endpoint" in names:
        kwargs["endpoint"] = endpoint
    if "url" in names and "endpoint" not in names:
        kwargs["url"] = endpoint
    if "source" in names or "endpoint" in names or "url" in names:
        return method(**_filtered_kwargs(method, kwargs))

    # The positional form is kept for a compact client API:
    # get(source, endpoint, *, params=None).
    try:
        return method(source, endpoint, params=dict(params or {}))
    except TypeError:
        return method(endpoint, params=dict(params or {}))


def _body(response: Any) -> str:
    if isinstance(response, bytes):
        return response.decode("utf-8")
    if isinstance(response, str):
        return response
    text = getattr(response, "text", None)
    if text is not None:
        return text
    content = getattr(response, "content", None)
    if content is not None:
        return content.decode("utf-8") if isinstance(content, bytes) else str(content)
    return json.dumps(response)


def _payload_response(request: httpx.Request, payload: str, content_type: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": content_type},
        content=payload.encode("utf-8"),
        request=request,
    )


def _make_adapter(module: Any, client: Any) -> Any:
    adapter_class = _pick(
        module,
        (
            "ArxivAdapter",
            "SemanticScholarAdapter",
            "CrossrefAdapter",
            "Arxiv",
            "SemanticScholar",
            "Crossref",
        ),
    )
    parameters, has_kwargs = _accepted_parameters(adapter_class)
    if "client" in parameters or has_kwargs:
        kwargs = {"client": client}
    elif "http_client" in parameters:
        kwargs = {"http_client": client}
    elif "base_client" in parameters:
        kwargs = {"base_client": client}
    else:
        kwargs = {}
    try:
        return adapter_class(**kwargs)
    except TypeError as error:
        if kwargs:
            try:
                return adapter_class(client)
            except TypeError:
                pass
        raise error


def _call_named(obj: Any, names: tuple[str, ...], *args: Any, **kwargs: Any) -> Any:
    method = None
    for name in names:
        candidate = getattr(obj, name, None)
        if candidate is not None:
            method = candidate
            break
    if method is None:
        raise AssertionError(f"{type(obj).__name__} must expose one of: {', '.join(names)}")

    try:
        return method(*args, **kwargs)
    except TypeError as error:
        # Common typed APIs use a keyword for a list of identifiers or a title.
        if args and len(args) == 1:
            for keyword in (
                "ids",
                "paper_ids",
                "identifiers",
                "title",
                "query",
                "arxiv_id",
                "paper_id",
                "doi",
            ):
                try:
                    return method(**{keyword: args[0]}, **kwargs)
                except TypeError:
                    continue
        raise error


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, tuple):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("records", "papers", "data", "items", "results"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        if all(isinstance(item, dict) for item in value.values()):
            return list(value.values())
        return [value]
    raise AssertionError(f"expected a record or record list, got {type(value).__name__}")


def _field(record: Mapping[str, Any], *names: str, default: Any = _MISSING) -> Any:
    for name in names:
        if name in record:
            return record[name]
    if default is not _MISSING:
        return default
    raise AssertionError(f"record is missing one of: {', '.join(names)}")


def _identifiers(record: Mapping[str, Any]) -> Mapping[str, Any]:
    identifiers = _field(record, "identifiers", "external_ids", "externalIds", default={})
    return identifiers if isinstance(identifiers, Mapping) else {}


def _identifier(record: Mapping[str, Any], *names: str) -> Any:
    identifiers = _identifiers(record)
    for name in names:
        for key in (name, name.lower(), name.upper(), name.title(), name.replace("_", "")):
            if key in identifiers and identifiers[key]:
                return identifiers[key]
        for key in (name, f"{name}_id", f"{name}Id"):
            if key in record and record[key]:
                return record[key]
    return None


def _raw_payload(record: Mapping[str, Any]) -> Any:
    raw = _field(record, "raw", "raw_payload", "rawPayload", "payload", default=_MISSING)
    if raw is _MISSING:
        raise AssertionError("normalized record must retain the source payload")
    assert raw not in (None, "", {}, [])
    return raw


def _assert_common_record(
    record: Mapping[str, Any],
    *,
    title: str,
    year: int,
    identifier_name: str,
    identifier_value: str,
    abstract_required: bool = True,
) -> None:
    assert _field(record, "title") == title
    assert _field(record, "year", "publication_year") == year
    abstract = _field(record, "abstract", default=None)
    if abstract_required:
        assert isinstance(abstract, str) and abstract
    assert isinstance(_field(record, "venue", "container_title", default=""), str)
    publication_types = _field(
        record,
        "publication_types",
        "publicationTypes",
        "types",
        default=[],
    )
    assert isinstance(publication_types, list)
    _field(
        record,
        "open_access_pdf_url",
        "openAccessPdfUrl",
        "open_access_pdf",
        "openAccessPdf",
        default=None,
    )
    _field(record, "tldr", "TLDR", default=None)
    assert _identifier(record, identifier_name) == identifier_value
    _raw_payload(record)


def _normalize_title(value: str) -> str:
    cleaner = getattr(base_module, "clean_text", None)
    if cleaner is None:
        raise AssertionError("the shared adapter base must expose clean_text()")
    return cleaner(value)


def _patch_clock(monkeypatch: pytest.MonkeyPatch, clock: FakeClock) -> None:
    """Defend the no-real-sleep contract if an implementation uses time directly."""

    time_module = getattr(base_module, "time", None)
    if time_module is not None:
        if hasattr(time_module, "sleep"):
            monkeypatch.setattr(time_module, "sleep", clock.sleep)
        if hasattr(time_module, "monotonic"):
            monkeypatch.setattr(time_module, "monotonic", clock)
    if hasattr(base_module, "sleep"):
        monkeypatch.setattr(base_module, "sleep", clock.sleep)


def test_cache_hit_miss_and_explicit_offline_miss(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return _payload_response(request, "cached response", "text/plain")

    transport = httpx.MockTransport(handler)
    client = _make_client(tmp_path / "cache", transport)

    endpoint = "https://fixture.test/record"
    first = _client_get(client, "fixture", endpoint, params={"z": "last", "a": "first"})
    second = _client_get(client, "fixture", endpoint, params={"a": "first", "z": "last"})
    assert _body(first) == "cached response"
    assert _body(second) == "cached response"
    assert len(calls) == 1, "equivalent sorted params must share one cache entry"

    metadata_files = []
    for path in (tmp_path / "cache").rglob("*.json"):
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if {"url", "status", "fetched_at"}.issubset(metadata):
            metadata_files.append(metadata)
    assert metadata_files
    assert metadata_files[0]["status"] == 200
    assert metadata_files[0]["url"]

    offline_transport = httpx.MockTransport(
        lambda request: pytest.fail(f"offline client attempted network access: {request.url}")
    )
    offline_client = _make_client(tmp_path / "cache", offline_transport, offline=True)
    cached = _client_get(
        offline_client,
        "fixture",
        endpoint,
        params={"a": "first", "z": "last"},
    )
    assert _body(cached) == "cached response"

    miss = _pick(
        base_module,
        ("OfflineCacheMiss", "OfflineCacheMissError", "CacheMissError"),
    )
    with pytest.raises(miss, match="(?i)offline|cache.*miss|miss.*cache"):
        _client_get(
            offline_client,
            "fixture",
            "https://fixture.test/not-record",
            params={"q": "missing"},
        )


def test_rate_limiter_uses_injected_clock_without_sleeping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    _patch_clock(monkeypatch, clock)
    request_times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_times.append(clock.now)
        return _payload_response(request, "ok", "text/plain")

    client = _make_client(
        tmp_path / "cache",
        httpx.MockTransport(handler),
        clock=clock,
        intervals={"fixture": 3.0},
    )
    _client_get(client, "fixture", "https://fixture.test/one")
    _client_get(client, "fixture", "https://fixture.test/two")

    assert request_times == [100.0, 103.0]
    assert clock.sleeps == [3.0]


def test_429_retry_honors_retry_after_with_fake_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    _patch_clock(monkeypatch, clock)
    attempts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(clock.now)
        if len(attempts) == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "2"},
                content=b"rate limited",
                request=request,
            )
        return _payload_response(request, "retried response", "text/plain")

    client = _make_client(
        tmp_path / "cache",
        httpx.MockTransport(handler),
        clock=clock,
        intervals={"fixture": 0.0},
    )
    result = _client_get(client, "fixture", "https://fixture.test/retry")

    assert _body(result) == "retried response"
    assert attempts == [100.0, 102.0]
    assert 2.0 in clock.sleeps


def test_title_normalization_cleans_markup_and_whitespace() -> None:
    assert _normalize_title("  Attention\n   Is All You Need  ") == "Attention Is All You Need"
    assert _normalize_title("<i>Attention</i> Is All You Need") == "Attention Is All You Need"


def test_arxiv_lookup_and_title_search_return_common_records(tmp_path: Path) -> None:
    lookup_xml = _read_fixture("arxiv_lookup.xml")
    search_xml = _read_fixture("arxiv_title_search.xml")

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params
        search_query = query.get("search_query", "")
        payload = lookup_xml if "id:" in search_query or "id_list" in query else search_xml
        return _payload_response(request, payload, "application/atom+xml")

    adapter = _make_adapter(
        arxiv_module,
        _make_client(tmp_path / "cache", httpx.MockTransport(handler), intervals={"arxiv": 0.0}),
    )
    record = _call_named(adapter, ("get_by_id", "lookup", "fetch_by_id"), "1706.03762")
    _assert_common_record(
        record,
        title="Attention Is All You Need",
        year=2017,
        identifier_name="arxiv",
        identifier_value="1706.03762",
    )
    assert "Transformer" in _field(record, "abstract")
    assert "cs.CL" in _field(record, "categories")
    assert "arxiv.org/pdf/1706.03762" in str(
        _field(record, "open_access_pdf_url", "openAccessPdfUrl", "open_access_pdf")
    )

    search_result = _call_named(
        adapter,
        ("search_title", "search", "search_by_title"),
        "Attention Is All You Need",
    )
    records = _records(search_result)
    assert any(_field(item, "title") == "Attention Is All You Need" for item in records)


def test_semantic_scholar_batch_and_reference_metadata(tmp_path: Path) -> None:
    search_payload = _read_fixture("semanticscholar_title_search.json")
    references_payload = _read_fixture("semanticscholar_references.json")
    citations_payload = _read_fixture("semanticscholar_citations.json")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/paper/batch") or request.method == "POST":
            return _payload_response(
                request,
                _read_fixture("semanticscholar_batch.json"),
                "application/json",
            )
        if path.endswith("/references"):
            return _payload_response(request, references_payload, "application/json")
        if path.endswith("/citations"):
            return _payload_response(request, citations_payload, "application/json")
        return _payload_response(request, search_payload, "application/json")

    adapter = _make_adapter(
        semanticscholar_module,
        _make_client(
            tmp_path / "cache",
            httpx.MockTransport(handler),
            intervals={"semanticscholar": 0.0, "s2": 0.0},
        ),
    )
    batch_result = _call_named(
        adapter,
        ("batch_lookup", "batch_get", "get_batch", "lookup_batch"),
        ["arXiv:1706.03762", "DOI:10.48550/arXiv.2005.11401"],
    )
    records = _records(batch_result)
    assert len(records) == 2
    batch_request = next(
        request for request in requests if request.url.path.endswith("/paper/batch")
    )
    batch_body = json.loads(batch_request.content)
    assert batch_body["ids"] == [
        "ARXIV:1706.03762",
        "DOI:10.48550/arXiv.2005.11401",
    ]
    requested_fields = set(batch_request.url.params["fields"].split(","))
    assert {
        "externalIds",
        "title",
        "year",
        "abstract",
        "venue",
        "publicationTypes",
        "openAccessPdf",
        "tldr",
        "references.externalIds",
        "references.title",
    } <= requested_fields
    _assert_common_record(
        records[0],
        title="Attention Is All You Need",
        year=2017,
        identifier_name="arxiv",
        identifier_value="1706.03762",
    )
    assert "Transformer" in str(_field(records[0], "tldr", "TLDR"))
    references_from_batch = _field(records[0], "references")
    assert references_from_batch[0]["externalIds"]["ArXiv"] == "1810.04805"
    assert references_from_batch[0]["title"].startswith("BERT:")
    assert "intents" not in references_from_batch[0]
    assert any(request.url.path.endswith("/paper/batch") for request in requests)

    references = _call_named(
        adapter,
        ("references", "get_references", "fetch_references"),
        "arXiv:1706.03762",
    )
    reference_records = _records(references)
    bert_reference = next(
        item
        for item in reference_records
        if _identifier(_field(item, "paper", "citedPaper"), "arxiv") == "1810.04805"
    )
    bert_paper = _field(bert_reference, "paper", "citedPaper")
    assert _field(bert_paper, "title").startswith("BERT:")
    assert _field(bert_reference, "intents") == ["background"]
    assert _field(bert_reference, "is_influential", "isInfluential") is False
    assert (
        "fixture for a citation edge"
        in _field(bert_reference, "contexts", "citation_contexts", "citationContexts")[0]
    )

    citations = _call_named(
        adapter,
        ("citations", "get_citations", "fetch_citations"),
        "arXiv:1706.03762",
    )
    citation_records = _records(citations)
    assert citation_records
    assert _field(citation_records[0], "intents") == ["background", "uses-method-in"]
    assert _field(citation_records[0], "is_influential", "isInfluential") is True
    assert _field(citation_records[0], "contexts", "citation_contexts", "citationContexts")


def test_semantic_scholar_batch_chunks_and_reuses_post_cache(tmp_path: Path) -> None:
    batch_payload = _read_fixture("semanticscholar_batch.json")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path.endswith("/paper/batch")
        return _payload_response(request, batch_payload, "application/json")

    client = _make_client(
        tmp_path / "cache",
        httpx.MockTransport(handler),
        intervals={"semanticscholar": 0.0},
    )
    adapter = semanticscholar_module.SemanticScholarAdapter(
        client=client,
        api_key="",
        batch_limit=2,
    )
    identifiers = [
        "arXiv:1706.03762",
        "DOI:10.48550/arXiv.2005.11401",
        "arXiv:1810.04805",
    ]

    adapter.lookup_many(identifiers)
    adapter.lookup_many(identifiers)

    assert len(requests) == 2
    assert [len(json.loads(request.content)["ids"]) for request in requests] == [2, 1]


def test_semantic_scholar_keyless_batch_never_attempts_intent_enrichment(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/paper/batch"):
            payload = json.loads(_read_fixture("semanticscholar_batch.json"))
            payload["data"] = payload["data"][:1]
            return _payload_response(request, json.dumps(payload), "application/json")
        pytest.fail(f"keyless batch attempted relationship request: {request.url}")

    client = _make_client(
        tmp_path / "cache",
        httpx.MockTransport(handler),
        intervals={"semanticscholar": 0.0},
    )
    adapter = semanticscholar_module.SemanticScholarAdapter(client=client, api_key="")

    records = adapter.lookup_many(["arXiv:1706.03762"], with_intents=True)

    assert len(records) == 1
    assert [request.url.path for request in requests] == [
        "/graph/v1/paper/batch",
    ]
    assert "x-api-key" not in requests[0].headers
    assert "intents" not in records[0]["references"][0]
    assert "isInfluential" not in records[0]["references"][0]


def test_semantic_scholar_with_intents_uses_key_and_reference_endpoint(
    tmp_path: Path,
) -> None:
    batch_payload = json.loads(_read_fixture("semanticscholar_batch.json"))
    batch_payload["data"] = batch_payload["data"][:1]
    references_payload = _read_fixture("semanticscholar_references.json")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/paper/batch"):
            return _payload_response(request, json.dumps(batch_payload), "application/json")
        if request.url.path.endswith("/references"):
            return _payload_response(request, references_payload, "application/json")
        pytest.fail(f"unexpected Semantic Scholar request: {request.url}")

    client = _make_client(
        tmp_path / "cache",
        httpx.MockTransport(handler),
        intervals={"semanticscholar": 0.0},
    )
    adapter = semanticscholar_module.SemanticScholarAdapter(
        client=client,
        api_key="fixture-key",
    )

    records = adapter.lookup_many(["arXiv:1706.03762"], with_intents=True)

    assert [request.url.path for request in requests] == [
        "/graph/v1/paper/batch",
        "/graph/v1/paper/s2-transformer-001/references",
    ]
    assert all(request.headers["x-api-key"] == "fixture-key" for request in requests)
    reference = records[0]["references"][0]
    assert reference["intents"] == ["background"]
    assert reference["isInfluential"] is False
    assert "fixture for a citation edge" in reference["contexts"][0]


def test_semantic_scholar_title_search_normalizes_common_record(tmp_path: Path) -> None:
    payload = _read_fixture("semanticscholar_title_search.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return _payload_response(request, payload, "application/json")

    adapter = _make_adapter(
        semanticscholar_module,
        _make_client(tmp_path / "cache", httpx.MockTransport(handler), intervals={"s2": 0.0}),
    )
    result = _call_named(
        adapter,
        ("search_title", "search", "search_by_title"),
        "Attention Is All You Need",
    )
    exact = next(
        item for item in _records(result) if _field(item, "title") == "Attention Is All You Need"
    )
    _assert_common_record(
        exact,
        title="Attention Is All You Need",
        year=2017,
        identifier_name="arxiv",
        identifier_value="1706.03762",
    )


def test_crossref_doi_and_bibliographic_title_query(tmp_path: Path) -> None:
    work_payload = _read_fixture("crossref_work.json")
    search_payload = _read_fixture("crossref_title_search.json")

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params
        is_title_search = any(key.startswith("query") for key in query)
        if is_title_search:
            return _payload_response(request, search_payload, "application/json")
        return _payload_response(request, work_payload, "application/json")

    adapter = _make_adapter(
        crossref_module,
        _make_client(tmp_path / "cache", httpx.MockTransport(handler), intervals={"crossref": 0.0}),
    )
    record = _call_named(
        adapter,
        ("get_by_doi", "lookup_doi", "fetch_by_doi", "doi"),
        "10.1038/s41598-023-33607-z",
    )
    _assert_common_record(
        record,
        title="SciQA: a Scientific Question Answering Benchmark for Scholarly Knowledge",
        year=2023,
        identifier_name="doi",
        identifier_value="10.1038/s41598-023-33607-z",
    )
    assert "<jats" not in _field(record, "abstract")
    assert _field(record, "venue", "container_title") == "Scientific Reports"
    assert "rdcu.be/fixture-sciqa.pdf" in str(
        _field(record, "open_access_pdf_url", "openAccessPdfUrl", "open_access_pdf")
    )

    search_result = _call_named(
        adapter,
        ("search_title", "search", "search_by_title", "bibliographic_search"),
        "SciQA: a Scientific Question Answering Benchmark for Scholarly Knowledge",
    )
    exact = next(
        item
        for item in _records(search_result)
        if _field(item, "title")
        == "SciQA: a Scientific Question Answering Benchmark for Scholarly Knowledge"
    )
    assert _identifier(exact, "doi") == "10.1038/s41598-023-33607-z"

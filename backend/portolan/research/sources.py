"""The source facade used by the research runner."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from portolan.adapters.arxiv import ArxivAdapter
from portolan.adapters.openalex import OpenAlexAdapter
from portolan.adapters.semanticscholar import SemanticScholarAdapter
from portolan.adapters.unpaywall import UnpaywallAdapter


class ResearchSources:
    """Small facade over the optional bibliographic source adapters.

    OpenAlex is the resolver and snowball source.  The other adapters are optional
    enrichers and are deliberately typed loosely here so offline fakes can be passed
    by callers and tests.
    """

    def __init__(
        self,
        openalex: Any,
        *,
        semanticscholar: Any | None = None,
        arxiv: Any | None = None,
        unpaywall: Any | None = None,
    ) -> None:
        if openalex is None:
            raise ValueError("an OpenAlex adapter is required")
        self.openalex = openalex
        self.semanticscholar = semanticscholar
        self.arxiv = arxiv
        self.unpaywall = unpaywall

    @classmethod
    def default(
        cls,
        cache_dir: Path,
        *,
        openalex_api_key: str | None = None,
        semantic_scholar_api_key: str | None = None,
        contact_email: str | None = None,
        offline: bool = False,
    ) -> ResearchSources:
        """Build adapters using the repository's real HTTP clients."""

        cache_dir = Path(cache_dir)
        configured_email = (
            contact_email
            if contact_email is not None and contact_email.strip()
            else os.environ.get("PORTOLAN_CONTACT_EMAIL")
        )
        unpaywall = (
            UnpaywallAdapter(
                cache_dir=cache_dir,
                email=configured_email,
                offline=offline,
            )
            if configured_email and configured_email.strip()
            else None
        )
        return cls(
            OpenAlexAdapter(
                cache_dir=cache_dir,
                api_key=openalex_api_key,
                offline=offline,
            ),
            semanticscholar=SemanticScholarAdapter(
                cache_dir=cache_dir,
                api_key=semantic_scholar_api_key,
                offline=offline,
            ),
            arxiv=ArxivAdapter(cache_dir=cache_dir, offline=offline),
            unpaywall=unpaywall,
        )

    def close(self) -> None:
        """Close adapter HTTP clients when an adapter owns one.

        Fakes commonly expose no close method, so closing the facade is safe for
        both real and test adapters.
        """

        seen: set[int] = set()
        for adapter in (self.openalex, self.semanticscholar, self.arxiv, self.unpaywall):
            if adapter is None or id(adapter) in seen:
                continue
            seen.add(id(adapter))
            close = getattr(adapter, "close", None)
            if callable(close):
                close()
                continue
            client = getattr(adapter, "client", None)
            close_client = getattr(client, "close", None)
            if callable(close_client):
                close_client()


__all__ = ["ResearchSources"]

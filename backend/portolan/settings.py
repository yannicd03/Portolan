"""Runtime configuration for the Portolan application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .graph.base import ResearchGraph

StoreBackend = Literal["neo4j", "memory"]

# backend/portolan/settings.py -> repository root, where ontology/ and eval/ live.
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

_TRUE = {"1", "true", "yes", "on"}


def _env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


@dataclass(frozen=True, slots=True)
class Settings:
    """Effective settings shared by the API and command line interface."""

    store: StoreBackend = "neo4j"
    data_dir: Path | None = None
    repo_root: Path = DEFAULT_REPO_ROOT
    seed_golden: bool = False
    log_level: str = "INFO"
    startup_timeout: float = 60.0
    neo4j_uri: str | None = None
    neo4j_user: str = "neo4j"
    neo4j_password: str | None = None
    neo4j_database: str | None = None
    openalex_api_key: str | None = None
    semantic_scholar_api_key: str | None = None
    contact_email: str | None = None
    max_concurrent_runs: int = 1
    openrouter_api_key: str | None = None
    chat_model: str = "deepseek/deepseek-v4-pro"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    chat_max_tool_calls: int = 40

    def __post_init__(self) -> None:
        if self.store not in ("neo4j", "memory"):
            raise ValueError(f"store must be 'neo4j' or 'memory', got {self.store!r}")
        if self.data_dir is None:
            object.__setattr__(self, "data_dir", self.repo_root / "data")
        else:
            object.__setattr__(self, "data_dir", Path(self.data_dir))
        if self.max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be at least 1")
        if self.chat_max_tool_calls < 1:
            raise ValueError("chat_max_tool_calls must be at least 1")

    @property
    def documents_dir(self) -> Path:
        """Directory holding content-addressed PDFs and extracted text."""

        assert self.data_dir is not None
        return self.data_dir / "documents"

    @property
    def chats_dir(self) -> Path:
        """Directory holding project chat threads."""

        assert self.data_dir is not None
        return self.data_dir / "chats"

    @property
    def runs_dir(self) -> Path:
        """Directory holding persisted research run records."""

        assert self.data_dir is not None
        return self.data_dir / "runs"

    @property
    def research_checkpoints_path(self) -> Path:
        """SQLite database holding resumable Research-mode plans."""

        assert self.data_dir is not None
        return self.data_dir / "checkpoints" / "research.sqlite3"

    @property
    def http_cache_dir(self) -> Path:
        """Directory used by source adapters for HTTP response caching."""

        assert self.data_dir is not None
        return self.data_dir / "http-cache"

    @property
    def ontology_root(self) -> Path:
        """Location of the M0 ontology artifacts used by the spike tooling."""

        return self.repo_root / "ontology"

    @classmethod
    def from_env(cls) -> Settings:
        """Read settings from the process environment."""

        store = (_env("PORTOLAN_STORE") or "neo4j").lower()
        if store not in ("memory", "neo4j"):
            raise ValueError(f"PORTOLAN_STORE must be 'neo4j' or 'memory', got {store!r}")

        data_dir = _env("PORTOLAN_DATA_DIR")
        repo_root = _env("PORTOLAN_REPO_ROOT")
        return cls(
            store=store,  # type: ignore[arg-type]
            data_dir=Path(data_dir) if data_dir else None,
            repo_root=Path(repo_root) if repo_root else DEFAULT_REPO_ROOT,
            seed_golden=(_env("PORTOLAN_SEED_GOLDEN") or "").lower() in _TRUE,
            log_level=(_env("PORTOLAN_LOG_LEVEL") or "INFO").upper(),
            startup_timeout=float(_env("PORTOLAN_STARTUP_TIMEOUT") or 60),
            neo4j_uri=_env("NEO4J_URI"),
            neo4j_user=_env("NEO4J_USER") or "neo4j",
            neo4j_password=_env("NEO4J_PASSWORD"),
            neo4j_database=_env("NEO4J_DATABASE"),
            openalex_api_key=_env("OPENALEX_API_KEY"),
            semantic_scholar_api_key=_env("SEMANTIC_SCHOLAR_API_KEY"),
            contact_email=_env("PORTOLAN_CONTACT_EMAIL"),
            max_concurrent_runs=int(_env("PORTOLAN_MAX_CONCURRENT_RUNS") or 1),
            openrouter_api_key=_env("OPENROUTER_API_KEY"),
            chat_model=_env("PORTOLAN_CHAT_MODEL") or "deepseek/deepseek-v4-pro",
            openrouter_base_url=_env("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
            chat_max_tool_calls=int(_env("PORTOLAN_CHAT_MAX_TOOL_CALLS") or 40),
        )

    def describe(self) -> str:
        """Return one log-safe line describing the effective configuration."""

        if self.store == "neo4j":
            location = self.neo4j_uri or "<NEO4J_URI unset>"
        else:
            location = str(self.data_dir)
        return (
            f"store={self.store} location={location} data_dir={self.data_dir} "
            f"seed_golden={self.seed_golden} "
            f"openalex_key={'set' if self.openalex_api_key else 'unset'} "
            f"semantic_scholar_key={'set' if self.semantic_scholar_api_key else 'unset'} "
            f"contact_email={'set' if self.contact_email else 'unset'} "
            f"max_concurrent_runs={self.max_concurrent_runs}"
            f" openrouter_key={'set' if self.openrouter_api_key else 'unset'}"
            f" chat_model={self.chat_model}"
        )


def make_graph(settings: Settings) -> ResearchGraph:
    """Construct the configured v1 graph backend."""

    if settings.store == "neo4j":
        if not settings.neo4j_uri:
            raise ValueError("PORTOLAN_STORE=neo4j requires NEO4J_URI")
        from .graph.neo4j_graph import Neo4jResearchGraph

        return Neo4jResearchGraph(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password,
            database=settings.neo4j_database,
        )

    from .graph.memory import InMemoryResearchGraph

    return InMemoryResearchGraph()

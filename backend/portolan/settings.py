"""Runtime configuration read from the environment.

Every setting has an environment variable so the same image runs locally, in Docker
Compose, and in tests.  Neo4j is the graph store (ADR-0005); ``PORTOLAN_STORE=oxigraph``
still selects the embedded RDF backend kept from the M0 spike.  Both implement the same
``GraphRepository``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .store import GraphRepository

StoreBackend = Literal["oxigraph", "neo4j"]

# backend/portolan/settings.py -> repository root, where ontology/ and eval/ live.
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

_TRUE = {"1", "true", "yes", "on"}


def _env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


@dataclass(frozen=True, slots=True)
class Settings:
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

    @property
    def ontology_root(self) -> Path:
        return self.repo_root / "ontology"

    @classmethod
    def from_env(cls) -> Settings:
        store = (_env("PORTOLAN_STORE") or "neo4j").lower()
        if store not in ("oxigraph", "neo4j"):
            raise ValueError(f"PORTOLAN_STORE must be 'oxigraph' or 'neo4j', got {store!r}")
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
        )

    def describe(self) -> str:
        """One log-safe line of the effective configuration (no secrets)."""

        if self.store == "neo4j":
            location = self.neo4j_uri or "<NEO4J_URI unset>"
        else:
            location = str(self.data_dir / "oxigraph") if self.data_dir else "in-memory"
        return f"store={self.store} location={location} seed_golden={self.seed_golden}"


def make_repository(settings: Settings) -> GraphRepository:
    """Construct the configured graph backend."""

    if settings.store == "neo4j":
        from .store.neo4j_store import Neo4jStore

        if not settings.neo4j_uri:
            raise ValueError("PORTOLAN_STORE=neo4j requires NEO4J_URI")
        return Neo4jStore(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password,
            database=settings.neo4j_database,
            ontology_root=settings.ontology_root,
        )

    from .store.oxigraph_store import OxigraphStore

    path = None
    if settings.data_dir is not None:
        path = settings.data_dir / "oxigraph"
        path.mkdir(parents=True, exist_ok=True)
    return OxigraphStore(path=path, ontology_root=settings.ontology_root)

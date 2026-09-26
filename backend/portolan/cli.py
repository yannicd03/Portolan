"""Command-line entry point (``portolan``)."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from .settings import Settings, make_repository

app = typer.Typer(help="Portolan: literature research into a typed knowledge graph.")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
) -> None:
    """Run the HTTP API (configured through PORTOLAN_* / NEO4J_* environment variables)."""

    import uvicorn

    from .api.app import create_app

    uvicorn.run(create_app(), host=host, port=port)


@app.command("seed-golden")
def seed_golden_command() -> None:
    """Compose the golden mini-graph into the configured store and print its counts.

    With a persistent Oxigraph store, stop the API first: the store directory is locked
    by the process that has it open.
    """

    from .golden import seed_golden

    settings = Settings.from_env()
    repository = make_repository(settings)
    try:
        typer.echo(json.dumps(seed_golden(repository, settings.repo_root), sort_keys=True))
    finally:
        repository.close()


if __name__ == "__main__":
    app()

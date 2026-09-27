"""Command-line entry point (``portolan``)."""

from __future__ import annotations

import json
import threading
from typing import Annotated, Any

import typer

from .settings import Settings, make_graph

app = typer.Typer(help="Portolan: literature research into a typed knowledge graph.")
project_app = typer.Typer(help="Manage research projects.")
app.add_typer(project_app, name="project")
documents_app = typer.Typer(help="Manage stored documents.")
app.add_typer(documents_app, name="documents")


def _as_json(value: Any) -> str:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            value = dump(mode="json")
        except TypeError:
            value = dump()
    elif isinstance(value, dict):
        value = {key: json.loads(_as_json(item)) for key, item in value.items()}
    elif isinstance(value, (list, tuple)):
        value = [json.loads(_as_json(item)) for item in value]
    return json.dumps(value, default=str, sort_keys=True)


def _close(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        close()


def _ensure_schema(graph: Any) -> None:
    """Initialize a graph before a direct CLI operation.

    The API performs this during lifespan startup.  The CLI opens a graph directly,
    so a fresh Neo4j database needs the same initialization before writes.  The
    callable guard keeps small graph doubles useful in command tests.
    """

    ensure_schema = getattr(graph, "ensure_schema", None)
    if callable(ensure_schema):
        ensure_schema()


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
    """Seed the demo project into the configured graph, if it is empty."""

    from .golden import seed_demo_project

    settings = Settings.from_env()
    graph = make_graph(settings)
    try:
        _ensure_schema(graph)
        project = seed_demo_project(graph, settings.repo_root)
        typer.echo(_as_json(project) if project is not None else "already seeded")
    finally:
        _close(graph)


@project_app.command("create")
def project_create(
    name: Annotated[str, typer.Argument(help="Project name.")],
    description: Annotated[
        str | None, typer.Option("--description", "-d", help="Optional project description.")
    ] = None,
) -> None:
    """Create a research project."""

    cleaned_name = name.strip()
    if not cleaned_name or len(cleaned_name) > 200:
        raise typer.BadParameter(
            "name must contain 1 to 200 non-whitespace characters", param_hint="NAME"
        )
    settings = Settings.from_env()
    graph = make_graph(settings)
    try:
        _ensure_schema(graph)
        project = graph.create_project(cleaned_name, description=description)
        typer.echo(_as_json(project))
    finally:
        _close(graph)


@project_app.command("list")
def project_list() -> None:
    """List research projects as JSON."""

    settings = Settings.from_env()
    graph = make_graph(settings)
    try:
        _ensure_schema(graph)
        typer.echo(_as_json(graph.list_projects()))
    finally:
        _close(graph)


@project_app.command("delete")
def project_delete(
    project_id: Annotated[str, typer.Argument(help="Project id.")],
) -> None:
    """Delete a research project."""

    settings = Settings.from_env()
    graph = make_graph(settings)
    try:
        _ensure_schema(graph)
        if not graph.delete_project(project_id):
            typer.echo(f"project not found: {project_id}", err=True)
            raise typer.Exit(code=1)
        typer.echo(project_id)
    finally:
        _close(graph)


@project_app.command("rebuild-concepts")
def project_rebuild_concepts(
    project_id: Annotated[str, typer.Argument(help="Project id.")],
) -> None:
    """Rebuild and filter the concepts attached to a research project."""

    from portolan.research import rebuild_project_concepts

    settings = Settings.from_env()
    graph = make_graph(settings)
    try:
        _ensure_schema(graph)
        if graph.get_project(project_id) is None:
            typer.echo(f"project not found: {project_id}", err=True)
            raise typer.Exit(code=1)
        kept, filtered = rebuild_project_concepts(graph, project_id)
        typer.echo(f"kept={kept} filtered={filtered}")
    finally:
        _close(graph)


@documents_app.command("backfill-outlines")
def documents_backfill_outlines(
    rebuild: Annotated[bool, typer.Option(help="Rebuild existing outlines too.")] = False,
) -> None:
    """Build outline sidecars for stored documents that do not have one."""

    from .documents import DocumentStore

    settings = Settings.from_env()
    count = DocumentStore(settings.documents_dir).backfill_outlines(rebuild=rebuild)
    typer.echo(str(count))


def _make_research_request(
    *,
    seeds: list[str],
    query: str | None,
    max_works: int,
    depth: int,
    acquire_pdfs: bool,
    max_pdfs: int,
) -> Any:
    """Build a pipeline request without importing the optional pipeline package eagerly."""

    from portolan.research import ResearchRequest

    return ResearchRequest(
        seeds=seeds,
        query=query,
        max_works=max_works,
        snowball_depth=depth,
        acquire_pdfs=acquire_pdfs,
        max_pdfs=max_pdfs,
    )


def _make_research_components(settings: Settings, graph: Any) -> tuple[Any, Any, Any]:
    """Construct the pipeline and its source/document dependencies lazily."""

    from portolan.documents import DocumentStore, PdfFetcher
    from portolan.research import ResearchRunner, ResearchSources

    sources = ResearchSources.default(
        settings.http_cache_dir,
        openalex_api_key=settings.openalex_api_key,
        semantic_scholar_api_key=settings.semantic_scholar_api_key,
        contact_email=settings.contact_email,
    )
    documents = DocumentStore(settings.documents_dir)
    fetcher = PdfFetcher(documents, contact_email=settings.contact_email)
    runner = ResearchRunner(graph, sources, documents=documents, fetcher=fetcher)
    return runner, sources, fetcher


@app.command()
def research(
    project_id: Annotated[str, typer.Argument(help="Project id.")],
    seed: Annotated[
        list[str] | None, typer.Option("--seed", help="Seed identifier; may be repeated.")
    ] = None,
    query: Annotated[str | None, typer.Option("--query", help="Research query.")] = None,
    max_works: Annotated[
        int, typer.Option("--max-works", min=1, help="Maximum works to consider.")
    ] = 100,
    depth: Annotated[int, typer.Option("--depth", min=0, help="Citation snowball depth.")] = 2,
    no_pdfs: Annotated[bool, typer.Option("--no-pdfs", help="Skip PDF acquisition.")] = False,
    max_pdfs: Annotated[
        int, typer.Option("--max-pdfs", min=0, help="Maximum PDFs to acquire.")
    ] = 50,
) -> None:
    """Run the research pipeline synchronously for a project."""

    settings = Settings.from_env()
    graph = make_graph(settings)
    sources: Any = None
    fetcher: Any = None
    cancel = threading.Event()
    try:
        _ensure_schema(graph)
        request = _make_research_request(
            seeds=list(seed or []),
            query=query,
            max_works=max_works,
            depth=depth,
            acquire_pdfs=not no_pdfs,
            max_pdfs=max_pdfs,
        )
        runner, sources, fetcher = _make_research_components(settings, graph)

        def report_progress(event: Any) -> None:
            stage = getattr(event, "stage", None)
            message = getattr(event, "message", None)
            counts = getattr(event, "counts", None)
            if isinstance(event, dict):
                stage = event.get("stage")
                message = event.get("message")
                counts = event.get("counts")
            typer.echo(
                f"{stage or 'progress'}: {message or ''} {json.dumps(counts or {}, sort_keys=True)}"
            )

        try:
            report = runner.run(project_id, request, progress=report_progress, cancel=cancel)
        except KeyboardInterrupt:
            cancel.set()
            typer.echo("cancel requested", err=True)
            raise typer.Exit(code=130) from None
        except Exception as exc:
            # RunCancelled is imported only when the pipeline is available.  Keep
            # the CLI importable in installations where the concurrent pipeline
            # package has not landed yet.
            from portolan.research import RunCancelled

            if isinstance(exc, RunCancelled):
                typer.echo("cancelled")
                return
            raise
        typer.echo(_as_json(report))
    finally:
        _close(fetcher)
        _close(sources)
        _close(graph)


if __name__ == "__main__":
    app()

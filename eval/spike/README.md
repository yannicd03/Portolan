# M0 graph-store spike

`run_spike.py` is the measurement harness for the Oxigraph and Neo4j bindings. It creates a
fresh in-memory Oxigraph repository or connects to the Neo4j instance selected by
`NEO4J_URI`, loads the golden graph through the composer, runs all fourteen competency
questions through `GraphRepository.run_competency_question`, and writes:

- `eval/spike/results.json` — machine-readable timings, row counts, errors, validation, export,
  and Neo4j schema-acceptance evidence;
- `eval/spike/report.md` — the ADR-0005 criteria table and observed differences. The harness
  does not choose a store.

Run it from `backend/` so `uv` uses the project environment:

```text
uv run python ../eval/spike/run_spike.py --backend both
```

Neo4j is configured only from the environment:

```text
NEO4J_URI=bolt://localhost:7687 \
NEO4J_USER=neo4j \
NEO4J_PASSWORD=... \
uv run python ../eval/spike/run_spike.py --backend neo4j
```

`--skip-neo4j-if-unavailable` records an unavailable Neo4j backend as skipped and still
produces both artifacts. Query failures are retained per CQ and do not abort the remaining
questions.

## Parity mode

`--parity` runs every competency question against the selected backend(s), composes a
fresh golden graph for each backend, and compares the normalized rows with
`eval/golden/expected_answers.json`:

```text
uv run python ../eval/spike/run_spike.py --backend oxigraph --parity
```

The command writes `eval/spike/parity/rows_<backend>.json` and
`eval/spike/parity/parity_report.md`. It exits `0` only when every requested backend
matches the oracle; a query error, missing expected entry, column mismatch, or row mismatch
is non-zero. `--skip-neo4j-if-unavailable` keeps the existing explicit skip behavior for a
missing Neo4j server.

The parity normalizer lives in `eval/spike/parity/normalizer.py` and is importable by tests.
Parity uses the same composition-metadata/shared-fallback resolver as regular spike runs;
the oracle records the logical parameters actually expected and an explicit
canonical-to-binding column mapping per CQ. For example:

```json
{
  "CQ01": {
    "parameters": {
      "review": "https://w3id.org/portolan/id/review/kgqa-rag"
    },
    "column_mapping": {
      "cluster": {"oxigraph": "cluster", "neo4j": "cluster"},
      "work": {"oxigraph": "work", "neo4j": "workIri"}
    },
    "rows": []
  }
}
```

Rows are compared as an order-insensitive set. Review ids are expanded to full review IRIs
for comparison, while the single binding-parameter conversion point sends the same logical
review to Oxigraph as an IRI and to Neo4j as its bare `reviewId`. Decimal and floating-point
values are rounded to six decimal places, and all emitted values are deterministic JSON.

## Composer contract

The composer is intentionally a separate module: `eval/golden/compose_golden.py`. The harness
imports it only when a run starts and accepts the first callable it finds in this order:
`compose`, `compose_graph`, `compose_golden`, `compose_into`.

The preferred API is:

```python
def compose(repository: GraphRepository, *, repo_root: Path) -> CompositionMetadata:
    """Write every golden layer through repository methods and return query context."""
```

`repo_root` is optional for compatibility. The function must write the graph into the supplied
repository; it must not return a second store or make the harness emit SPARQL/Cypher. The return
value may be `None`, a mapping, or a small metadata object. If present, the following optional
fields let the harness target non-default rows for parameterized questions:

```python
{
    "query_parameters": {
        11: {"similarityThreshold": 0.60},
        13: {"work": "<excluded-work-iri>"},
    },
    "review_iri": "<review-iri>",
    "seed_work_iri": "<seed-iri>",
    "problem_iri": "<problem-iri>",
    "assertion_iri": "<statement-iri>",
    "excluded_work_iri": "<excluded-work-iri>",
}
```

Without metadata, the harness uses the shared fallback parameter table in `run_spike.py`.
Composition metadata overrides that table, and the selected binding receives the converted
form only after the logical values have been chosen. This keeps the spike runnable while the
composer is being developed independently and prevents the SPARQL and Cypher files from
silently selecting different contexts.

## Measurement notes

- Every CQ gets three read-only submissions. `best_wall_time_seconds` is the fastest successful
  attempt; `error` is retained if any attempt failed. A completely failed CQ has
  `executed: false` and `row_count: null`, never a fabricated empty result.
- Query complexity is reported as non-comment source lines for the backend-specific CQ file.
- Validation is called through `GraphRepository.validate()`. On Neo4j the repository's defined
  boundary is an export to Turtle followed by external validation; the report records that path.
- Turtle triple counts are parsed with RDFLib. When both backends are run, the JSON also contains
  set differences between the parsed Oxigraph and Neo4j exports, with a short sample of each
  difference.
- Neo4j schema statements are applied and recorded individually. This is the one spike-only
  exception to the data repository boundary because `GraphRepository` has no schema-management
  operation. The harness never sends data writes or competency queries outside the repository.

The current v0.2 ontology file contains nine shapes because `ClusterPair` added Shape 9 after
the original seven-shape ADR wording. The report measures all nine and identifies accepted
property fragments separately from a fully enforced shape.

The repository contract has no reset/clear method. The harness reports that limitation; it
does not patch the backend or clear a live Neo4j database behind the repository boundary.

## Turtle export fidelity boundary

`Neo4jStore.export_turtle()` reconstructs the ontology's plain-Turtle view from the LPG.
The code keeps exactly two documented inherent losses in `EXPORT_INHERENT_LOSSES`:

- `named_graph_boundaries`: Neo4j Community has no named graphs, and plain Turtle has no
  graph/context slot. `reviewId` and `extractionRun` remain LPG partition metadata.
- `xsd_decimal_to_float`: Bolt has no decimal value, so decimal model values are stored as
  Neo4j floats and exported with the corresponding decimal datatype after narrowing.

All other triple differences are mapping defects. The live export-parity pytest compares the
fresh Oxigraph and Neo4j golden graphs after normalizing only the decimal narrowing above and
reports missing and extra triples grouped by predicate.

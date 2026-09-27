# Portolan user guide

This guide walks through the web UI (<http://localhost:8080> with the default Docker Compose
setup). For installation and configuration, see the [README](../README.md). For how the parts
fit together, see [architecture.md](architecture.md).

The screen has three columns. The **project rail** is on the left. The **workspace** in the
middle has the Ask and Research tabs. The **map** is on the right. Use **Hide map** / **Show map**
in the project header to collapse the map. The header also shows the project's counts: works,
citations, authors, concepts and PDFs.

## Projects

- **Create a project** under *New project* in the left rail. Give it a name and, optionally, a
  description. New projects are selected straight away.
- **Switch projects** by clicking one in the rail. The URL changes to `#/projects/<project-id>`,
  so each project can be bookmarked. The project id is also what the CLI commands take.
- **Delete a project** with the × next to its name. You must have no research run active for
  that project. Deleting removes the project and its run history. Works, authors and concepts
  are shared between projects, so they stay in the graph if another project still uses them.
- On a fresh install, the database starts with the demo project *Golden mini-graph (demo)*:
  about 20 CS/AI papers with citations, but no PDFs.

A new project with no works opens on the **Research** tab. Once a project has works, it opens on
**Ask**. After that, the last tab you used is remembered for each project.

## Research tab: manual runs

The Research tab starts harvests directly, without the chat agent.

1. Enter a **Topic or question** (sent to OpenAlex search), **Seed papers** (one per line:
   `doi:10.…`, `arxiv:…`, a bare DOI or arXiv id, or an OpenAlex `W…` id), or both. You need at
   least one of the two.
2. Optionally open **Advanced**:
   - **Max works** (default 100): the maximum number of works the run includes.
   - **Snowball depth** (0–2, default 2). At depth 0 the run only resolves seeds and searches.
     At depth 1 it also expands the core set (seeds plus the top search hits) backward through
     references and forward through citing works. Depth 2 adds a "chase" round: the pool is
     screened once, and the references of the best candidates not yet expanded are added.
   - **Download PDFs** (off by default in this form) and **Max PDFs** (default 50): download
     open-access PDFs for the included works, seeds first and then in score order. Ask mode can
     only quote from papers that have a PDF.
3. Click **Start research**. The run appears as a card at the top of the list. Only one run can
   be active per project at a time.

While a run is active, its card shows the current stage (`resolve`, `search`, `snowball`,
`screen`, `write`, `concepts`, `acquire`, `done`), a progress message and live counts. Click
**Cancel** to stop it. A run that was queued or running when the backend restarted is listed
with the note *Interrupted by a backend restart*. The newest 50 runs of each project are kept.

### Reading a run report

| Field | Meaning |
|---|---|
| **+N new works (T total)** | How many works this run added to the project, and the project's total afterwards. A re-run on the same topic often includes many works the project already had; this line shows what is actually new. |
| Candidates | Distinct works found by seeds, search and snowballing before screening. |
| Screened out | Candidates that did not make the cut (below the minimum score or beyond *Max works*). |
| Included | Works this run selected and wrote to the project, including works the project already had. Seeds are always included. |
| Citations | Citation links written between this run's included works. |
| Authors | Distinct authors of the included works. |
| Concepts | Concepts kept after the project-wide concept rebuild. |
| PDFs acquired / failed / skipped | Downloads that worked; works where no open-access PDF could be fetched; works skipped because they already had a PDF or the *Max PDFs* limit was reached. |
| Warnings | Non-fatal problems, such as a seed that could not be resolved or a failed download. |

Screening scores each candidate on four signals: word overlap with the query and seeds, links
to seeds and included works, co-citation by the core set, and citation count.

## Ask tab: chatting with the papers

The Ask tab holds chat **threads** for the project. Use the *Thread* dropdown to switch threads,
**New thread** to start one, and **Delete** to remove the current one. Threads are saved on the
server and survive restarts. The chat needs `OPENROUTER_API_KEY`. Without it, the tab shows
*Ask mode is unavailable* and the reason.

The composer has two modes: **Ask** and **Plan research**. Enter sends and Shift+Enter adds a
new line. **Stop** cancels an answer that is still streaming. While the agent works, an activity
list shows its steps, for example "Searching papers: …", "Reading p. 3–4 of …", "Following
citations: …".

### Ask mode answers and citations

The Ask agent uses the graph to find relevant papers, then reads their extracted text and cites
verbatim quotes with page numbers. Before the answer is shown, the server searches each paper
for each quote:

- **Verified paper citations** appear as clickable `[n]` chips. Hover or focus a chip to preview
  the work, year, page and the passage in context. Click it to open the in-app **PDF viewer** at
  that page with the quote highlighted.
- **Verified abstract citations** (shown as *Abstract* instead of a page number) come from a
  work without a usable PDF. Clicking the chip opens the work's detail on the map.
- **Unverified citations** are rendered differently and labelled *quote not found in the
  paper*. They are not links, because the server could not find the quote. Treat the claim as
  unsupported.

Below the answer, **Sources** lists every citation. **Not supported by the project's papers**
lists claims the agent could not back with a quote, plus any `[n]` marker in the text that has
no citation. While the Ask tab is open, the works cited in the latest (or hovered) answer are
highlighted on the map.

## Plan research mode

Switch the composer to **Plan research** and describe the literature you want. The Research
agent may ask up to two clarifying questions. It can preview OpenAlex searches and look up seed
papers. It then proposes a harvest as a **plan card** showing the query, seeds, year range, max
works, snowball depth, PDFs on or off, a description and a rationale.

- **Approve** runs the plan as a normal research run. The run also appears in the Research tab,
  and **View harvest run** on the resolved card jumps to it. When the run finishes, the agent
  summarises the map: clusters, foundational works, bridges, main path and emerging work.
- **Edit** lets you change the query, seeds, years, max works, snowball depth and PDF setting
  before you submit.
- **Reject** optionally takes a message telling the agent why, so it can propose something else.

While a plan is pending, the thread takes no new messages. Approve, edit or reject it first.
Pending plans are held in the backend's memory. **After a backend restart, a pending plan
expires.** Acting on it then shows *This plan expired after a server restart — ask again*, the
plan is marked expired, and the thread accepts new messages again, so you can repeat the request.

## The map

The map shows the project's works as nodes and citations as edges. Toolbar buttons:

- **Authors** and **Concepts** add author and concept nodes (off by default). Click one to list
  its linked works.
- **Fit** re-centres the view. **Search works** runs a full-text search over titles and
  abstracts. Pick a result to select it.

### Lenses

| Lens | What it shows |
|---|---|
| **Landscape** | All works, sized by citation count. |
| **Central** | The main path (the most-travelled citation chain from older to newer work) is emphasised; works are sized by PageRank. Available once the analysis has loaded. |
| **Frontier** | Recent works with momentum. Halo strength is the frontier score, and other works are dimmed. **Frontier list** opens a ranked list. Hover a work to see its score broken into components: velocity, local uptake, main path, cluster growth, new concept and preprint. The window covers the project's newest publication year and the two years before it, not the current calendar year. |
| **Gaps** | Structural gap hypotheses. Bridging gaps are drawn as dashed links between clusters, and evidence works are ringed. Selecting a gap dims unrelated works. **Gap board** opens the list (see below). |

**Color by** switches between **Cluster** and **Year**. Cluster colouring is the default once the
project has at least two clusters. The **cluster legend** lists the clusters with readable labels
(the first six, then *+N more*). Click a cluster to focus it and click again to clear.

### Timeline filter

The timeline under the map plots works per publication year. Click a bar to select a year, and
Shift+click another bar to extend the selection to a range. Works outside the range are greyed
out. **Clear** removes the filter, and **Hide** collapses the timeline.

### Work detail

Selecting a work opens its detail panel, which shows:

- year, cluster, PageRank and betweenness, and structural roles (*foundational*, *bridge*,
  *hub*, *emerging*, *peripheral*, each explained on hover);
- its frontier score components, if it is in the frontier window;
- the gaps it is evidence for (click one to open it on the gap board);
- identifiers, authors, concepts and the abstract;
- **DOI** and **arXiv** links, and **Open PDF**, which opens the in-app viewer at page 1, plus
  ↗ to open the original PDF in a new tab;
- the in-project **Cites** and **Cited by** lists. Click an entry to move to that work.

### PDF viewer and deep links

The viewer has Prev/Next buttons, zoom (−, +, Fit width), **Open original** and **Close**. It
has its own URL, so a citation can be bookmarked or shared:

```
#/projects/<project-id>/docs/<sha256>?page=<n>&q=<quote>&work=<work-id>
```

`page` is the page to open, `q` is the quote to find and highlight, and `work` is the work the
PDF belongs to. When a quote is given, the viewer shows *Passage found on p. N* or *Passage not
found — showing p. N*.

## The gap board

Open the **Gaps** lens and then **Gap board**. Hypotheses are grouped by type:

- **Bridging**: two clusters with similar concepts that rarely cite each other.
- **Unexplored combinations**: two well-studied concepts that share neighbours but never appear
  on the same work.
- **Stagnating clusters**: a sizeable cluster with little recent work.

Each card shows the statement, a detector confidence, the evidence works (click to select),
cluster and concept chips, and a collapsible **Metrics** list. **Show on map** focuses the gap on
the map. Card actions:

- **Check outside the project** (later **Check again**) searches OpenAlex for works outside the
  project that match the gap's key terms. The verdict is *Likely filled outside the project*
  (at least three recent outside hits), *Possibly open*, or *Unknown* (for example, the search
  failed). Up to five outside hits are listed. For stagnation gaps, only recent works are
  searched.
- **Accept** / **Reject** set your decision. Click the same button again to go back to
  *proposed*. Rejected gaps are hidden unless you tick **Show rejected**, and they stay rejected
  when the analysis runs again.
- **Add note** / **Edit note** attaches a free-text note.

Gaps are recomputed from the current graph every time the board loads. Your status, note and
last verification are stored separately and keyed to the gap. If a later harvest changes the
graph so that a gap you already acted on is no longer detected, its card stays on the board
marked **no longer detected** (stale).

## Rebuilding concepts for older projects

Projects harvested before the grounded-concept pipeline (ADR-0008) still carry the old,
ungrounded OpenAlex keywords, which often produce off-topic concepts and cluster labels. A new
research run rebuilds a project's concepts as a side effect. To rebuild them without a
harvest, run:

```bash
docker compose exec backend portolan project rebuild-concepts <project-id>
# or, in a local checkout: cd backend && uv run portolan project rebuild-concepts <project-id>
```

The command prints `kept=… filtered=… ungrounded=… text_phrases=…`. Reload the map to see the
new clusters and labels. `portolan project list` prints the project ids, and the id also
appears in the browser URL.

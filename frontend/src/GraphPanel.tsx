import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import type { GraphNode, ProjectGraph, WorkDetail as WorkDetailData, WorkSummary } from './api'
import { CitationMap, type CitationMapHandle } from './CitationMap'
import { Timeline, type YearRange } from './Timeline'
import { WorkDetail } from './WorkDetail'

export interface GraphPanelProps {
  projectId: string
  refreshToken: number
}

function linkedWorkIds(graph: ProjectGraph | null, node: GraphNode | null): Set<string> {
  if (!graph || !node || node.kind === 'work') return new Set<string>()

  const linked = new Set<string>()
  graph.edges.forEach((edge) => {
    if (edge.source !== node.id && edge.target !== node.id) return
    const otherId = edge.source === node.id ? edge.target : edge.source
    const other = graph.nodes.find((candidate) => candidate.id === otherId)
    if (other?.kind === 'work') linked.add(other.id)
  })
  return linked
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : 'The graph request failed.'
}

export function GraphPanel({ projectId, refreshToken }: GraphPanelProps) {
  const [graph, setGraph] = useState<ProjectGraph | null>(null)
  const [graphLoading, setGraphLoading] = useState(true)
  const [graphError, setGraphError] = useState<string | null>(null)
  const [showAuthors, setShowAuthors] = useState(false)
  const [showConcepts, setShowConcepts] = useState(false)
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<WorkSummary[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [yearFilter, setYearFilter] = useState<YearRange | null>(null)
  const [detail, setDetail] = useState<WorkDetailData | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const mapRef = useRef<CitationMapHandle>(null)

  useEffect(() => {
    let active = true
    queueMicrotask(() => {
      if (!active) return
      setGraphLoading(true)
      setGraphError(null)
      setGraph(null)
      setSelectedId(null)
      setDetail(null)
      setDetailError(null)
    })

    api.projectGraph(projectId, { authors: showAuthors, concepts: showConcepts })
      .then((nextGraph) => {
        if (!active) return
        setGraph(nextGraph)
      })
      .catch((reason: unknown) => {
        if (active) setGraphError(errorMessage(reason))
      })
      .finally(() => {
        if (active) setGraphLoading(false)
      })

    return () => {
      active = false
    }
  }, [projectId, refreshToken, showAuthors, showConcepts])

  useEffect(() => {
    const normalizedQuery = query.trim()
    if (!normalizedQuery) {
      return
    }

    let active = true
    const timer = window.setTimeout(() => {
      setSearchLoading(true)
      setSearchError(null)
      api.search(projectId, normalizedQuery, 50)
        .then((results) => {
          if (active) setSearchResults(results)
        })
        .catch((reason: unknown) => {
          if (active) {
            setSearchResults([])
            setSearchError(errorMessage(reason))
          }
        })
        .finally(() => {
          if (active) setSearchLoading(false)
        })
    }, 220)

    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [projectId, query])

  const selectedNode = useMemo(
    () => graph?.nodes.find((node) => node.id === selectedId) ?? null,
    [graph, selectedId],
  )
  const selectedLinkedWorks = useMemo(
    () => linkedWorkIds(graph, selectedNode),
    [graph, selectedNode],
  )
  const searchIds = useMemo(
    () => new Set(searchResults.map((result) => result.id)),
    [searchResults],
  )
  const highlightIds = query.trim() ? searchIds : selectedLinkedWorks
  const highlightingActive = Boolean(query.trim()) || selectedLinkedWorks.size > 0
  const hasWorks = graph?.nodes.some((node) => node.kind === 'work') ?? false

  useEffect(() => {
    if (!selectedNode || selectedNode.kind !== 'work') {
      return
    }

    let active = true
    api.work(selectedNode.id, projectId)
      .then((nextDetail) => {
        if (active) setDetail(nextDetail)
      })
      .catch((reason: unknown) => {
        if (active) setDetailError(errorMessage(reason))
      })
      .finally(() => {
        if (active) setDetailLoading(false)
      })

    return () => {
      active = false
    }
  }, [projectId, selectedNode])

  const selectNode = (id: string | null) => {
    setSelectedId(id)
    setDetail(null)
    setDetailError(null)
    setDetailLoading(Boolean(id && graph?.nodes.find((node) => node.id === id)?.kind === 'work'))
    if (!id) {
      setDetailLoading(false)
    }
  }

  const selectSearchResult = (id: string) => {
    if (graph?.nodes.some((node) => node.id === id)) {
      selectNode(id)
    }
  }

  const handleQueryChange = (value: string) => {
    setQuery(value)
    if (!value.trim()) {
      setSearchResults([])
      setSearchError(null)
      setSearchLoading(false)
    }
  }

  return (
    <aside id="graph-panel" className="graph-panel" aria-label="Citation graph panel">
      <div className="graph-panel__toolbar">
        <div className="graph-panel__toolbar-row">
          <span className="graph-panel__heading">Map</span>
          <button
            className="graph-panel__control"
            type="button"
            aria-pressed={showAuthors}
            onClick={() => setShowAuthors((visible) => !visible)}
          >
            Authors
          </button>
          <button
            className="graph-panel__control"
            type="button"
            aria-pressed={showConcepts}
            onClick={() => setShowConcepts((visible) => !visible)}
          >
            Concepts
          </button>
          <button
            className="graph-panel__control graph-panel__fit"
            type="button"
            onClick={() => mapRef.current?.fit()}
            disabled={!hasWorks}
          >
            Fit
          </button>
        </div>
        <label className="graph-panel__search">
          <span className="graph-panel__search-label">Search works</span>
          <input
            className="graph-panel__search-input"
            type="search"
            value={query}
            onChange={(event) => handleQueryChange(event.target.value)}
            placeholder="Title or identifier"
            aria-label="Search works"
          />
          {searchLoading ? <span className="graph-panel__search-status">Searching…</span> : null}
        </label>
        {searchError ? <p className="graph-panel__search-error">{searchError}</p> : null}
        {query.trim() && !searchLoading && !searchError ? (
          <p className="graph-panel__search-meta" role="status">
            {searchResults.length} matching {searchResults.length === 1 ? 'work' : 'works'}
          </p>
        ) : null}
        {query.trim() && searchResults.length > 0 ? (
          <ul className="graph-panel__search-results" aria-label="Matching works">
            {searchResults.slice(0, 8).map((result) => (
              <li key={result.id}>
                <button
                  className="graph-panel__search-result"
                  type="button"
                  onClick={() => selectSearchResult(result.id)}
                >
                  <span>{result.title}</span>
                  <span className="graph-panel__search-result-meta">
                    {result.year ?? 'Year unknown'} · {result.cited_by_count} citations
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="graph-panel__canvas-wrap">
        {graphLoading ? <p className="graph-panel__canvas-message">Loading map…</p> : null}
        {!graphLoading && graphError ? <p className="graph-panel__canvas-message graph-panel__error">{graphError}</p> : null}
        {!graphLoading && !graphError && graph && hasWorks ? (
          <CitationMap
            ref={mapRef}
            data={graph}
            selectedId={selectedId}
            highlightedIds={highlightIds}
            highlightingActive={highlightingActive}
            yearFilter={yearFilter}
            onSelect={selectNode}
          />
        ) : null}
        {!graphLoading && !graphError && graph && !hasWorks ? (
          <p className="graph-panel__canvas-message">Start a research run to build the map.</p>
        ) : null}
      </div>

      {selectedNode?.kind === 'work' ? (
        <section className="graph-panel__detail" aria-live="polite">
          {detailLoading ? <p className="graph-panel__detail-message">Loading work…</p> : null}
          {detailError ? <p className="graph-panel__detail-message graph-panel__error">{detailError}</p> : null}
          {detail ? <WorkDetail detail={detail} projectId={projectId} onSelectWork={selectNode} /> : null}
        </section>
      ) : null}

      {selectedNode && selectedNode.kind !== 'work' ? (
        <section className="graph-panel__detail graph-panel__selection" aria-live="polite">
          <p className="graph-panel__selection-kind">{selectedNode.kind}</p>
          <h2 className="graph-panel__selection-title">{selectedNode.label}</h2>
          <p className="graph-panel__selection-meta">
            {selectedLinkedWorks.size} linked {selectedLinkedWorks.size === 1 ? 'work' : 'works'}
          </p>
          {selectedLinkedWorks.size > 0 ? (
            <ul className="graph-panel__linked-works">
              {[...selectedLinkedWorks].map((workId) => {
                const work = graph?.nodes.find((node) => node.id === workId)
                if (!work) return null
                return (
                  <li key={work.id}>
                    <button
                      className="graph-panel__linked-work"
                      type="button"
                      onClick={() => selectNode(work.id)}
                    >
                      {work.label}
                    </button>
                  </li>
                )
              })}
            </ul>
          ) : (
            <p className="graph-panel__detail-message">No linked works in this map.</p>
          )}
        </section>
      ) : null}

      {graph ? (
        <Timeline
          key={projectId}
          nodes={graph.nodes}
          value={yearFilter}
          onChange={setYearFilter}
        />
      ) : null}
    </aside>
  )
}

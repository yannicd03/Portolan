import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import type {
  GapHypothesis,
  GapUpdateInput,
  GraphNode,
  ProjectAnalysis,
  ProjectFrontier,
  ProjectGraph,
  WorkDetail as WorkDetailData,
  WorkSummary,
} from './api'
import {
  CitationMap,
  clusterColor,
  type CitationMapColorMode,
  type CitationMapHandle,
  type MapLens,
} from './CitationMap'
import { FrontierBars, FrontierList } from './FrontierPanel'
import { GapBoard } from './GapBoard'
import { bridgingLinks, conceptLabeler, gapFocusWorkIds, gapsByWork } from './insights'
import { Timeline, type YearRange } from './Timeline'
import { WorkDetail } from './WorkDetail'

const LENSES: readonly { id: MapLens; label: string; title: string }[] = [
  { id: 'landscape', label: 'Landscape', title: 'All works, sized by citations.' },
  { id: 'central', label: 'Central', title: 'Main path emphasised; works sized by PageRank.' },
  { id: 'frontier', label: 'Frontier', title: 'Recent works with momentum; halo strength is the frontier score.' },
  { id: 'gaps', label: 'Gaps', title: 'Structural gap hypotheses: dashed bridging links, ringed evidence works.' },
]

export interface GraphPanelProps {
  projectId: string
  refreshToken: number
  highlightedWorkIds: readonly string[]
  openWorkRequest: { projectId: string; workId: string; token: number } | null
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

export function GraphPanel({ projectId, refreshToken, highlightedWorkIds, openWorkRequest }: GraphPanelProps) {
  const [graph, setGraph] = useState<ProjectGraph | null>(null)
  const [analysis, setAnalysis] = useState<ProjectAnalysis | null>(null)
  const [analysisUnavailable, setAnalysisUnavailable] = useState(false)
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
  const [colorModeOverride, setColorModeOverride] = useState<CitationMapColorMode | null>(null)
  const [focusedClusterId, setFocusedClusterId] = useState<string | null>(null)
  const [lens, setLens] = useState<MapLens>('landscape')
  const [frontier, setFrontier] = useState<ProjectFrontier | null>(null)
  const [frontierUnavailable, setFrontierUnavailable] = useState(false)
  const [gaps, setGaps] = useState<GapHypothesis[] | null>(null)
  const [gapsUnavailable, setGapsUnavailable] = useState(false)
  const [activeGapId, setActiveGapId] = useState<string | null>(null)
  const [showRejectedGaps, setShowRejectedGaps] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(true)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [showAllClusters, setShowAllClusters] = useState(false)
  const mapRef = useRef<CitationMapHandle>(null)
  const handledWorkRequest = useRef<number | null>(null)

  useEffect(() => {
    let active = true
    queueMicrotask(() => {
      if (!active) return
      setGraphLoading(true)
      setGraphError(null)
      setGraph(null)
      setAnalysis(null)
      setAnalysisUnavailable(false)
      setSelectedId(null)
      setDetail(null)
      setDetailError(null)
      setFocusedClusterId(null)
      setShowAllClusters(false)
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

    api.analysis(projectId)
      .then((nextAnalysis) => {
        if (!active) return
        setAnalysis(nextAnalysis)
        setAnalysisUnavailable(false)
      })
      .catch(() => {
        if (!active) return
        setAnalysis(null)
        setAnalysisUnavailable(true)
      })

    return () => {
      active = false
    }
  }, [projectId, refreshToken, showAuthors, showConcepts])

  useEffect(() => {
    let active = true
    queueMicrotask(() => {
      if (!active) return
      setFrontier(null)
      setFrontierUnavailable(false)
      setGaps(null)
      setGapsUnavailable(false)
      setActiveGapId(null)
    })

    // Both endpoints are optional: a failure only marks the lens unavailable.
    api.frontier(projectId)
      .then((nextFrontier) => {
        if (active) setFrontier(nextFrontier)
      })
      .catch(() => {
        if (active) setFrontierUnavailable(true)
      })
    // Fetch rejected gaps too so "show rejected" is a client-side filter.
    api.gaps(projectId, true)
      .then((nextGaps) => {
        if (active) setGaps(nextGaps)
      })
      .catch(() => {
        if (active) setGapsUnavailable(true)
      })

    return () => {
      active = false
    }
  }, [projectId, refreshToken])

  const clusters = analysis?.clusters ?? []
  const displayedClusters = showAllClusters ? clusters : clusters.slice(0, 6)
  const canUseClusters = clusters.length > 0
  const colorMode = canUseClusters && colorModeOverride === 'cluster'
    ? 'cluster'
    : colorModeOverride === 'year'
      ? 'year'
      : clusters.length >= 2 ? 'cluster' : 'year'
  const frontierById = useMemo(
    () => new Map((frontier?.works ?? []).map((work) => [work.work_id, work])),
    [frontier],
  )
  const frontierScores = useMemo(
    () => new Map((frontier?.works ?? []).map((work) => [work.work_id, work.score])),
    [frontier],
  )
  const visibleGaps = useMemo(
    () => (gaps ?? []).filter((gap) => showRejectedGaps || gap.status !== 'rejected'),
    [gaps, showRejectedGaps],
  )
  const gapsForWork = useMemo(() => gapsByWork(visibleGaps), [visibleGaps])
  const gapWorkIds = useMemo(() => new Set(gapsForWork.keys()), [gapsForWork])
  const gapLinks = useMemo(() => bridgingLinks(visibleGaps, analysis), [analysis, visibleGaps])
  const activeGap = useMemo(
    () => visibleGaps.find((gap) => gap.id === activeGapId) ?? null,
    [activeGapId, visibleGaps],
  )
  const gapFocusIds = useMemo(() => gapFocusWorkIds(activeGap, analysis), [activeGap, analysis])
  const conceptLabel = useMemo(() => conceptLabeler(graph, frontier), [frontier, graph])
  const workTitles = useMemo(() => {
    const titles = new Map<string, string>()
    graph?.nodes.forEach((node) => {
      if (node.kind === 'work') titles.set(node.id, node.label)
    })
    frontier?.works.forEach((work) => {
      if (!titles.has(work.work_id)) titles.set(work.work_id, work.title)
    })
    return titles
  }, [frontier, graph])
  const hoveredFrontierWork = lens === 'frontier' && hoveredId ? frontierById.get(hoveredId) ?? null : null

  const toggleCluster = (clusterId: string) => {
    setFocusedClusterId((current) => (current === clusterId ? null : clusterId))
  }

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
  const highlightIds = useMemo(
    () => new Set([...highlightedWorkIds, ...(query.trim() ? searchIds : selectedLinkedWorks)]),
    [highlightedWorkIds, query, searchIds, selectedLinkedWorks],
  )
  const highlightingActive = highlightIds.size > 0 || Boolean(query.trim())
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

  const chooseLens = (nextLens: MapLens) => {
    setLens(nextLens)
    setDrawerOpen(true)
    setHoveredId(null)
  }

  const openGap = (gapId: string) => {
    const gap = gaps?.find((item) => item.id === gapId)
    if (gap?.status === 'rejected') setShowRejectedGaps(true)
    setLens('gaps')
    setActiveGapId(gapId)
    setDrawerOpen(true)
  }

  const selectFromMap = (id: string | null) => {
    selectNode(id)
    if (lens !== 'gaps' || !id) return
    const related = gapsForWork.get(id)
    if (!related?.length) return
    if (!related.some((gap) => gap.id === activeGapId)) setActiveGapId(related[0].id)
    setDrawerOpen(true)
  }

  const updateGap = async (gapId: string, input: GapUpdateInput) => {
    const previous = gaps?.find((gap) => gap.id === gapId)
    if (!previous) return
    const replace = (next: GapHypothesis) => {
      setGaps((current) => current?.map((gap) => (gap.id === gapId ? next : gap)) ?? current)
    }
    // Optimistic: show the change immediately, roll back if the PATCH fails.
    replace({ ...previous, ...input })
    try {
      replace(await api.updateGap(projectId, gapId, input))
    } catch (reason) {
      replace(previous)
      throw reason
    }
  }

  const verifyGap = async (gapId: string) => {
    const updated = await api.verifyGap(projectId, gapId)
    setGaps((current) => current?.map((gap) => (gap.id === gapId ? updated : gap)) ?? current)
  }

  const clusterLabel = (clusterId: string) => (
    clusters.find((cluster) => cluster.id === clusterId)?.label ?? clusterId
  )

  useEffect(() => {
    if (!openWorkRequest || openWorkRequest.projectId !== projectId || handledWorkRequest.current === openWorkRequest.token || !graph?.nodes.some((node) => node.id === openWorkRequest.workId && node.kind === 'work')) return
    let active = true
    queueMicrotask(() => {
      if (!active || handledWorkRequest.current === openWorkRequest.token) return
      handledWorkRequest.current = openWorkRequest.token
      setSelectedId(openWorkRequest.workId)
      setDetail(null)
      setDetailError(null)
      setDetailLoading(true)
    })
    return () => { active = false }
  }, [graph, openWorkRequest, projectId])

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
        <div className="graph-panel__map-controls" aria-label="Map analysis controls">
          <span className="graph-panel__map-control-label">Lens:</span>
          <div className="graph-panel__segmented" role="group" aria-label="Map lens">
            {LENSES.map((item) => (
              <button
                key={item.id}
                className="graph-panel__segment"
                type="button"
                title={item.title}
                aria-pressed={lens === item.id}
                disabled={item.id === 'central' && !analysis}
                onClick={() => chooseLens(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
          {lens === 'frontier' || lens === 'gaps' ? (
            <button
              className="graph-panel__control graph-panel__drawer-toggle"
              type="button"
              aria-pressed={drawerOpen}
              aria-controls="graph-panel-lens-drawer"
              onClick={() => setDrawerOpen((open) => !open)}
            >
              {lens === 'gaps' ? 'Gap board' : 'Frontier list'}
            </button>
          ) : null}
        </div>
        <div className="graph-panel__map-controls" aria-label="Map color controls">
          <span className="graph-panel__map-control-label">Color by:</span>
          <div className="graph-panel__segmented" role="group" aria-label="Map color mode">
            <button
              className="graph-panel__segment"
              type="button"
              aria-pressed={colorMode === 'cluster'}
              disabled={!canUseClusters}
              onClick={() => setColorModeOverride('cluster')}
            >
              Cluster
            </button>
            <button
              className="graph-panel__segment"
              type="button"
              aria-pressed={colorMode === 'year'}
              onClick={() => setColorModeOverride('year')}
            >
              Year
            </button>
          </div>
        </div>
        {clusters.length > 0 ? (
          <div
            className={`graph-panel__cluster-legend${showAllClusters ? ' graph-panel__cluster-legend--expanded' : ''}`}
            aria-label="Citation clusters"
          >
            {displayedClusters.map((cluster) => (
              <button
                key={cluster.id}
                className="graph-panel__cluster-chip"
                type="button"
                aria-pressed={focusedClusterId === cluster.id}
                title={cluster.label}
                onClick={() => toggleCluster(cluster.id)}
              >
                <span
                  className="graph-panel__cluster-swatch"
                  style={{ backgroundColor: clusterColor(cluster.id, clusters) }}
                  aria-hidden="true"
                />
                <span className="graph-panel__cluster-label">{cluster.label}</span>
                <span className="graph-panel__cluster-size">{cluster.size}</span>
              </button>
            ))}
            {clusters.length > 6 ? (
              <button
                className="graph-panel__cluster-more"
                type="button"
                aria-expanded={showAllClusters}
                onClick={() => setShowAllClusters((visible) => !visible)}
              >
                {showAllClusters ? 'Show fewer' : `+${clusters.length - 6} more`}
              </button>
            ) : null}
          </div>
        ) : null}
        {analysisUnavailable ? <p className="graph-panel__analysis-hint">Analysis unavailable</p> : null}
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
            analysis={analysis}
            colorMode={colorMode}
            focusedClusterId={focusedClusterId}
            lens={lens}
            frontierScores={frontierScores}
            gapLinks={gapLinks}
            gapWorkIds={gapWorkIds}
            gapFocusIds={gapFocusIds}
            activeGapId={activeGap?.id ?? null}
            onSelectGap={openGap}
            onHoverNode={setHoveredId}
            selectedId={selectedId}
            highlightedIds={highlightIds}
            highlightingActive={highlightingActive}
            yearFilter={yearFilter}
            onSelect={selectFromMap}
          />
        ) : null}
        {hoveredFrontierWork ? (
          <div className="graph-panel__hover-card">
            <p className="graph-panel__hover-title">{hoveredFrontierWork.title}</p>
            <FrontierBars work={hoveredFrontierWork} compact />
          </div>
        ) : null}
        {!graphLoading && !graphError && graph && !hasWorks ? (
          <p className="graph-panel__canvas-message">Start a research run to build the map.</p>
        ) : null}
      </div>

      {drawerOpen && (lens === 'frontier' || lens === 'gaps') ? (
        <div className="graph-panel__drawer" id="graph-panel-lens-drawer">
          {lens === 'frontier' ? (
            <FrontierList
              works={frontier?.works ?? null}
              nowYear={frontier?.now_year ?? null}
              windowYears={frontier?.window_years ?? 2}
              unavailable={frontierUnavailable}
              selectedId={selectedId}
              onSelectWork={selectNode}
            />
          ) : (
            <GapBoard
              gaps={gaps}
              unavailable={gapsUnavailable}
              activeGapId={activeGap?.id ?? null}
              showRejected={showRejectedGaps}
              onShowRejectedChange={setShowRejectedGaps}
              onActivateGap={setActiveGapId}
              onSelectWork={selectNode}
              onUpdateGap={updateGap}
              onVerifyGap={verifyGap}
              onClose={() => setDrawerOpen(false)}
              workTitle={(workId) => workTitles.get(workId) ?? null}
              clusterLabel={clusterLabel}
              conceptLabel={conceptLabel}
            />
          )}
        </div>
      ) : null}

      {selectedNode?.kind === 'work' ? (
        <section className="graph-panel__detail" aria-live="polite">
          {detailLoading ? <p className="graph-panel__detail-message">Loading work…</p> : null}
          {detailError ? <p className="graph-panel__detail-message graph-panel__error">{detailError}</p> : null}
          {detail ? (
            <WorkDetail
              detail={detail}
              projectId={projectId}
              analysis={analysis}
              onSelectWork={selectNode}
              frontierWork={frontierById.get(detail.work.id) ?? null}
              gaps={gapsForWork.get(detail.work.id) ?? []}
              onSelectGap={openGap}
            />
          ) : null}
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

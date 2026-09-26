import { useEffect, useMemo, useState } from 'react'
import { api } from './api'
import type { CitationGraph, Health, SourceTier } from './api'
import { CitationMap } from './CitationMap'
import { TIER_COLORS, TIER_LABELS } from './tiers'

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [graph, setGraph] = useState<CitationGraph | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.health(), api.graph()])
      .then(([h, g]) => {
        setHealth(h)
        setGraph(g)
      })
      .catch((reason: Error) => setError(reason.message))
  }, [])

  const selected = useMemo(
    () => graph?.nodes.find((node) => node.id === selectedId) ?? null,
    [graph, selectedId],
  )
  const neighbours = useMemo(() => {
    if (!graph || !selectedId) return { cites: 0, citedBy: 0 }
    return {
      cites: graph.edges.filter((edge) => edge.source === selectedId).length,
      citedBy: graph.edges.filter((edge) => edge.target === selectedId).length,
    }
  }, [graph, selectedId])

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="wordmark">Portolan</span>
          <span className="tagline">citation map · golden mini-graph</span>
        </div>
        <div className={`status ${error ? 'status-error' : ''}`} role="status">
          {error
            ? `API unavailable: ${error}`
            : health
              ? `${health.store} · ${health.works} works · ${graph?.edges.length ?? 0} citations`
              : 'Connecting…'}
        </div>
      </header>

      <main className="layout">
        <section className="map-panel">
          {graph && graph.nodes.length > 0 ? (
            <CitationMap data={graph} selectedId={selectedId} onSelect={setSelectedId} />
          ) : (
            <div className="empty">
              {error
                ? 'The map needs the API.'
                : graph
                  ? 'The store is empty. Start the backend with PORTOLAN_SEED_GOLDEN=true to load the golden graph.'
                  : 'Loading…'}
            </div>
          )}
          <ul className="legend" aria-label="Source tier legend">
            {(Object.keys(TIER_COLORS) as SourceTier[]).map((tier) => (
              <li key={tier}>
                <span className="swatch" style={{ background: TIER_COLORS[tier] }} />
                {TIER_LABELS[tier]}
              </li>
            ))}
            <li className="legend-note">size = citations within the corpus</li>
          </ul>
        </section>

        <aside className="side">
          <section className="card">
            <h2>Work</h2>
            {selected ? (
              <>
                <p className="work-title">{selected.title}</p>
                <dl className="facts">
                  <dt>Year</dt>
                  <dd>{selected.year ?? '—'}</dd>
                  <dt>Source tier</dt>
                  <dd>{selected.sourceTier ? TIER_LABELS[selected.sourceTier] : '—'}</dd>
                  <dt>Survey</dt>
                  <dd>{selected.isSurvey ? 'yes' : 'no'}</dd>
                  <dt>Cites</dt>
                  <dd>{neighbours.cites} in corpus</dd>
                  <dt>Cited by</dt>
                  <dd>{neighbours.citedBy} in corpus</dd>
                </dl>
                <p className="iri">{selected.id}</p>
              </>
            ) : (
              <p className="hint">Select a node on the map to see the work.</p>
            )}
          </section>
        </aside>
      </main>
    </div>
  )
}

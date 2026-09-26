import Graph from 'graphology'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import { useEffect, useRef } from 'react'
import Sigma from 'sigma'
import type { CitationGraph } from './api'
import { TIER_COLORS, UNKNOWN_TIER_COLOR } from './tiers'

interface Props {
  data: CitationGraph
  selectedId: string | null
  onSelect: (id: string | null) => void
}

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

export function CitationMap({ data, selectedId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const rendererRef = useRef<Sigma | null>(null)
  const selectedRef = useRef<string | null>(selectedId)
  const onSelectRef = useRef(onSelect)

  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])

  useEffect(() => {
    const container = containerRef.current
    if (!container || data.nodes.length === 0) return

    const graph = new Graph({ type: 'directed', multi: false })
    const years = data.nodes.map((node) => node.year ?? 0).filter(Boolean)
    const minYear = years.length ? Math.min(...years) : 0
    data.nodes.forEach((node, index) => {
      graph.addNode(node.id, {
        label: node.title.length > 48 ? `${node.title.slice(0, 46)}…` : node.title,
        // Seed positions by year and order so the layout is stable between reloads.
        x: (node.year ?? minYear) - minYear,
        y: (index % 7) - 3,
        size: 5 + Math.sqrt(node.citedBy) * 3,
        color: node.sourceTier ? TIER_COLORS[node.sourceTier] : UNKNOWN_TIER_COLOR,
      })
    })
    for (const edge of data.edges) {
      if (!graph.hasEdge(edge.source, edge.target)) {
        graph.addDirectedEdge(edge.source, edge.target, { size: 1 })
      }
    }
    forceAtlas2.assign(graph, {
      iterations: 400,
      settings: { ...forceAtlas2.inferSettings(graph), gravity: 1.5, scalingRatio: 8 },
    })

    const edgeColor = cssVar('--edge', '#c9ccd2')
    const renderer = new Sigma(graph, container, {
      defaultEdgeType: 'arrow',
      defaultEdgeColor: edgeColor,
      labelColor: { color: cssVar('--text', '#1d2127') },
      labelFont: 'Inter, system-ui, sans-serif',
      labelSize: 12,
      labelRenderedSizeThreshold: 7,
      zIndex: true,
    })

    // Highlight the selected work and its citation neighbourhood.
    renderer.setSetting('nodeReducer', (node, attributes) => {
      const selected = selectedRef.current
      if (!selected) return attributes
      if (node === selected) return { ...attributes, highlighted: true, zIndex: 2 }
      if (graph.areNeighbors(node, selected)) return { ...attributes, zIndex: 1 }
      return { ...attributes, color: cssVar('--muted-node', '#dfe1e5'), label: '', zIndex: 0 }
    })
    renderer.setSetting('edgeReducer', (edge, attributes) => {
      const selected = selectedRef.current
      if (!selected) return attributes
      return graph.hasExtremity(edge, selected)
        ? { ...attributes, color: cssVar('--accent', '#2f6f9f'), size: 2 }
        : { ...attributes, hidden: true }
    })

    renderer.on('clickNode', ({ node }) => onSelectRef.current(node))
    renderer.on('clickStage', () => onSelectRef.current(null))
    rendererRef.current = renderer
    return () => {
      renderer.kill()
      rendererRef.current = null
    }
  }, [data])

  useEffect(() => {
    selectedRef.current = selectedId
    rendererRef.current?.refresh()
  }, [selectedId])

  return <div className="map" ref={containerRef} aria-label="Citation map" />
}

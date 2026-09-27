/* oxlint-disable react/only-export-components */
import Graph from 'graphology'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from 'react'
import Sigma from 'sigma'
import type { AnalysisCluster, GraphNode, ProjectAnalysis, ProjectGraph } from './api'
import type { YearRange } from './Timeline'

interface GraphNodeAttributes {
  color: string
  clusterColor: string
  kind: GraphNode['kind']
  label: string
  size: number
  yearColor: string
  x: number
  y: number
}

interface GraphEdgeAttributes {
  color: string
  kind: ProjectGraph['edges'][number]['kind'] | 'main_path'
  size: number
  type: 'arrow' | 'line'
}

export type CitationMapColorMode = 'cluster' | 'year'

export interface CitationMapHandle {
  fit: () => void
}

export interface CitationMapProps {
  data: ProjectGraph
  analysis?: ProjectAnalysis | null
  colorMode?: CitationMapColorMode
  focusedClusterId?: string | null
  mainPathActive?: boolean
  selectedId: string | null
  highlightedIds?: ReadonlySet<string>
  highlightingActive?: boolean
  yearFilter?: YearRange | null
  onSelect: (id: string | null) => void
}

const WORK_COLOR_START = [68, 112, 169]
const WORK_COLOR_END = [198, 106, 77]
const MISSING_YEAR_COLOR = '#9299a3'
const AUTHOR_COLOR = '#8f6bc2'
const CONCEPT_COLOR = '#c17b3c'

export const CLUSTER_COLORS = [
  '#2c7bb6',
  '#d95f02',
  '#1b9e77',
  '#7570b3',
  '#e6ab02',
  '#66a61e',
  '#e7298a',
  '#a6761d',
  '#1f78b4',
  '#d95f0e',
] as const

export function clusterColor(clusterId: string, clusters: readonly AnalysisCluster[]): string {
  const index = clusters.findIndex((cluster) => cluster.id === clusterId)
  return index >= 0 ? CLUSTER_COLORS[index % CLUSTER_COLORS.length] : MISSING_YEAR_COLOR
}

function cssVar(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? value as Record<string, unknown> : {}
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function shortLabel(value: string, maxLength = 48): string {
  return value.length > maxLength ? `${value.slice(0, maxLength - 1)}…` : value
}

function interpolateColor(start: number[], end: number[], amount: number): string {
  const channels = start.map((channel, index) => Math.round(channel + (end[index] - channel) * amount))
  return `rgb(${channels[0]}, ${channels[1]}, ${channels[2]})`
}

function workColor(year: number | null, minYear: number, maxYear: number): string {
  if (year === null) return MISSING_YEAR_COLOR
  const span = Math.max(1, maxYear - minYear)
  return interpolateColor(WORK_COLOR_START, WORK_COLOR_END, (year - minYear) / span)
}

function workYear(node: GraphNode): number | null {
  return numberValue(asRecord(node.data).year)
}

function workClusterColor(node: GraphNode, analysis: ProjectAnalysis | null | undefined): string {
  if (node.kind !== 'work' || !analysis) return MISSING_YEAR_COLOR
  const clusterId = analysis.works[node.id]?.cluster
  return clusterId ? clusterColor(clusterId, analysis.clusters) : MISSING_YEAR_COLOR
}

function visibleNodeIds(data: ProjectGraph, yearFilter: YearRange | null): Set<string> {
  if (!yearFilter) return new Set(data.nodes.map((node) => node.id))

  const start = Math.min(yearFilter.start, yearFilter.end)
  const end = Math.max(yearFilter.start, yearFilter.end)
  const visibleWorkIds = new Set<string>()
  data.nodes.forEach((node) => {
    if (node.kind !== 'work') return
    const year = workYear(node)
    if (year !== null && year >= start && year <= end) visibleWorkIds.add(node.id)
  })

  const nodesById = new Map(data.nodes.map((node) => [node.id, node]))
  const visibleIds = new Set(visibleWorkIds)
  data.edges.forEach((edge) => {
    const sourceIsVisibleWork = visibleWorkIds.has(edge.source)
    const targetIsVisibleWork = visibleWorkIds.has(edge.target)
    if (!sourceIsVisibleWork && !targetIsVisibleWork) return

    const linkedId = sourceIsVisibleWork ? edge.target : edge.source
    const linkedNode = nodesById.get(linkedId)
    if (linkedNode?.kind === 'author' || linkedNode?.kind === 'concept') {
      visibleIds.add(linkedNode.id)
    }
  })
  return visibleIds
}

function citedByCount(node: GraphNode): number {
  const count = numberValue(asRecord(node.data).cited_by_count)
  return count && count > 0 ? count : 0
}

function getNodeColor(node: GraphNode, minYear: number, maxYear: number): string {
  if (node.kind === 'author') return AUTHOR_COLOR
  if (node.kind === 'concept') return CONCEPT_COLOR
  return workColor(workYear(node), minYear, maxYear)
}

function getNodeSize(node: GraphNode): number {
  if (node.kind === 'author') return 4.5
  if (node.kind === 'concept') return 4
  return Math.min(18, 5 + Math.log1p(citedByCount(node)) * 2.2)
}

function createGraph(
  data: ProjectGraph,
  analysis: ProjectAnalysis | null | undefined,
): Graph<GraphNodeAttributes, GraphEdgeAttributes> {
  const graph = new Graph<GraphNodeAttributes, GraphEdgeAttributes>({ type: 'directed', multi: true })
  const works = data.nodes.filter((node) => node.kind === 'work')
  const years = works
    .map(workYear)
    .filter((year): year is number => year !== null)
  const minYear = years.length ? Math.min(...years) : 0
  const maxYear = years.length ? Math.max(...years) : 1

  data.nodes.forEach((node, index) => {
    const year = workYear(node)
    const row = Math.floor(index / 8)
    const column = index % 8
    const yearColor = getNodeColor(node, minYear, maxYear)
    const clusterColorValue = node.kind === 'work'
      ? workClusterColor(node, analysis)
      : yearColor
    graph.addNode(node.id, {
      label: shortLabel(node.label),
      kind: node.kind,
      color: yearColor,
      clusterColor: clusterColorValue,
      size: getNodeSize(node),
      yearColor,
      // Seed the layout so a reload does not begin with every node at the origin.
      x: (column - 3.5) * 2 + (year === null ? 0 : (year - minYear) * 0.015),
      y: (row - Math.max(0, Math.ceil(data.nodes.length / 8) / 2)) * 2,
    })
  })

  data.edges.forEach((edge) => {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) return
    const isCitation = edge.kind === 'cites'
    graph.addDirectedEdge(edge.source, edge.target, {
      kind: edge.kind,
      type: isCitation ? 'arrow' : 'line',
      color: isCitation ? cssVar('--graph-edge-strong', '#667383') : cssVar('--edge', '#c9ccd2'),
      size: isCitation ? 1.15 : 0.7,
    })
  })

  forceAtlas2.assign(graph, {
    iterations: Math.min(350, Math.max(120, data.nodes.length * 3)),
    settings: {
      ...forceAtlas2.inferSettings(graph),
      gravity: 1.25,
      scalingRatio: 8,
      slowDown: 2,
    },
  })

  // Add the overlay after layout so an inactive main path does not move the map.
  analysis?.main_path.edges.forEach((edge) => {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) return
    graph.addDirectedEdge(edge.source, edge.target, {
      kind: 'main_path',
      type: 'arrow',
      color: cssVar('--accent', '#216c79'),
      size: 2.8,
    })
  })
  return graph
}

export const CitationMap = forwardRef<CitationMapHandle, CitationMapProps>(function CitationMap(
  {
    data,
    analysis = null,
    colorMode = 'year',
    focusedClusterId = null,
    mainPathActive = false,
    selectedId,
    highlightedIds,
    highlightingActive = false,
    yearFilter = null,
    onSelect,
  },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const rendererRef = useRef<Sigma<GraphNodeAttributes, GraphEdgeAttributes> | null>(null)
  const selectedRef = useRef<string | null>(selectedId)
  const highlightedRef = useRef<ReadonlySet<string>>(highlightedIds ?? new Set<string>())
  const highlightingActiveRef = useRef(highlightingActive)
  const yearFilterRef = useRef<YearRange | null>(yearFilter)
  const yearVisibleRef = useRef<ReadonlySet<string>>(visibleNodeIds(data, yearFilter))
  const colorModeRef = useRef<CitationMapColorMode>(colorMode)
  const focusedClusterIdRef = useRef<string | null>(focusedClusterId)
  const focusedClusterWorkIdsRef = useRef<ReadonlySet<string>>(new Set<string>())
  const mainPathActiveRef = useRef(mainPathActive)
  const mainPathNodeIdsRef = useRef<ReadonlySet<string>>(new Set<string>())
  const onSelectRef = useRef(onSelect)

  useEffect(() => {
    selectedRef.current = selectedId
    highlightedRef.current = highlightedIds ?? new Set<string>()
    highlightingActiveRef.current = highlightingActive
    rendererRef.current?.refresh()
  }, [highlightedIds, highlightingActive, selectedId])

  useEffect(() => {
    colorModeRef.current = colorMode
    focusedClusterIdRef.current = focusedClusterId
    mainPathActiveRef.current = mainPathActive
    focusedClusterWorkIdsRef.current = focusedClusterId && analysis
      ? new Set(analysis.clusters.find((cluster) => cluster.id === focusedClusterId)?.work_ids ?? [])
      : new Set<string>()
    mainPathNodeIdsRef.current = new Set(analysis?.main_path.work_ids ?? [])
    rendererRef.current?.refresh()
  }, [analysis, colorMode, focusedClusterId, mainPathActive])

  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])

  useEffect(() => {
    yearFilterRef.current = yearFilter
    yearVisibleRef.current = visibleNodeIds(data, yearFilter)
    rendererRef.current?.refresh()
  }, [data, yearFilter])

  useImperativeHandle(ref, () => ({
    fit: () => {
      const renderer = rendererRef.current
      if (renderer) void renderer.getCamera().animatedReset({ duration: 300 })
    },
  }), [])

  useEffect(() => {
    const container = containerRef.current
    if (!container || data.nodes.length === 0) return

    const graph = createGraph(data, analysis)
    const neighbours = new Map<string, Set<string>>()
    data.edges.forEach(({ source, target }) => {
      if (!neighbours.has(source)) neighbours.set(source, new Set<string>())
      if (!neighbours.has(target)) neighbours.set(target, new Set<string>())
      neighbours.get(source)?.add(target)
      neighbours.get(target)?.add(source)
    })
    const renderer = new Sigma<GraphNodeAttributes, GraphEdgeAttributes>(graph, container, {
      defaultNodeType: 'circle',
      defaultEdgeType: 'line',
      defaultEdgeColor: cssVar('--edge', '#c9ccd2'),
      labelColor: { color: cssVar('--text', '#1d2127') },
      labelFont: 'Inter, system-ui, sans-serif',
      labelSize: 11,
      labelRenderedSizeThreshold: 6,
      zIndex: true,
      renderEdgeLabels: false,
      hideEdgesOnMove: true,
    })

    renderer.setSetting('nodeReducer', (node, attributes) => {
      const selected = selectedRef.current
      const focused = highlightedRef.current
      const isSelected = node === selected
      const isFocused = focused.has(node)
      const isNeighbour = selected ? neighbours.get(selected)?.has(node) ?? false : false
      const hasYearFilter = yearFilterRef.current !== null
      const isYearVisible = yearVisibleRef.current.has(node)
      const clusterWorkIds = focusedClusterWorkIdsRef.current
      const hasClusterFocus = focusedClusterIdRef.current !== null
      const isClusterFocused = clusterWorkIds.has(node)
      const pathNodeIds = mainPathNodeIdsRef.current
      const hasMainPathFocus = mainPathActiveRef.current
      const isMainPathNode = hasMainPathFocus && pathNodeIds.has(node)
      const hasSearchFocus = highlightingActiveRef.current || focused.size > 0
      const isSearchFocused = isFocused || isNeighbour
      const isEmphasisTarget = (
        (!hasSearchFocus || isSearchFocused)
        && (!hasClusterFocus || isClusterFocused)
        && (!hasMainPathFocus || isMainPathNode)
      )
      const nodeColor = colorModeRef.current === 'cluster'
        ? attributes.clusterColor
        : attributes.yearColor

      if (hasYearFilter && !isYearVisible) {
        return {
          ...attributes,
          color: cssVar('--muted-node', '#dfe1e5'),
          label: null,
          zIndex: 0,
        }
      }
      if (isSelected) return { ...attributes, color: nodeColor, highlighted: true, zIndex: 4 }
      if (!isEmphasisTarget) {
        return {
          ...attributes,
          color: cssVar('--muted-node', '#dfe1e5'),
          label: null,
          zIndex: 0,
        }
      }
      if (isNeighbour) return { ...attributes, color: nodeColor, highlighted: true, zIndex: 2 }
      return {
        ...attributes,
        color: nodeColor,
        highlighted: isMainPathNode,
        zIndex: isMainPathNode ? 4 : isFocused || isClusterFocused ? 2 : 1,
      }
    })
    renderer.setSetting('edgeReducer', (edge, attributes) => {
      const selected = selectedRef.current
      const focused = highlightedRef.current
      const extremitySelected = selected ? graph.hasExtremity(edge, selected) : false
      const edgeData = graph.getEdgeAttributes(edge)
      const source = graph.source(edge)
      const target = graph.target(edge)
      const clusterWorkIds = focusedClusterWorkIdsRef.current
      const hasClusterFocus = focusedClusterIdRef.current !== null
      const hasMainPathFocus = mainPathActiveRef.current
      const hasSearchFocus = highlightingActiveRef.current || focused.size > 0
      const connectsSearch = (
        focused.has(source)
        || focused.has(target)
        || selected === source
        || selected === target
      )
      const connectsCluster = clusterWorkIds.has(source) || clusterWorkIds.has(target)
      const hasYearFilter = yearFilterRef.current !== null
      const sourceIsVisible = yearVisibleRef.current.has(source)
      const targetIsVisible = yearVisibleRef.current.has(target)
      const isMainPathEdge = edgeData.kind === 'main_path'

      if (isMainPathEdge && (!hasMainPathFocus || !sourceIsVisible || !targetIsVisible)) {
        return { ...attributes, hidden: true }
      }
      if (isMainPathEdge && hasSearchFocus && !connectsSearch) {
        return { ...attributes, hidden: true }
      }
      if (isMainPathEdge && hasClusterFocus && !connectsCluster) {
        return { ...attributes, hidden: true }
      }
      if (isMainPathEdge) {
        return {
          ...attributes,
          color: cssVar('--accent', '#216c79'),
          size: 2.8,
          zIndex: 5,
        }
      }
      if (extremitySelected && !hasMainPathFocus) {
        return { ...attributes, color: cssVar('--accent', '#2f6f9f'), size: 2, zIndex: 3 }
      }
      if (hasYearFilter && !sourceIsVisible && !targetIsVisible) {
        return { ...attributes, hidden: true }
      }
      if (hasSearchFocus && !connectsSearch) {
        return { ...attributes, hidden: true }
      }
      if (hasClusterFocus && !connectsCluster) {
        return { ...attributes, hidden: true }
      }
      if (hasMainPathFocus) {
        return { ...attributes, color: cssVar('--edge', '#c9ccd2'), size: 0.55, zIndex: 0 }
      }
      if (edgeData.kind === 'cites') {
        return { ...attributes, color: cssVar('--graph-edge-strong', '#667383') }
      }
      return { ...attributes, color: cssVar('--edge', '#c9ccd2') }
    })

    renderer.on('clickNode', ({ node }) => onSelectRef.current(node))
    renderer.on('clickStage', () => onSelectRef.current(null))
    // The renderer owns WebGL and event listeners, so always kill it when the
    // project graph changes or the component leaves the tree.
    const previousRenderer = rendererRef.current
    previousRenderer?.kill()
    rendererRef.current = renderer

    return () => {
      renderer.kill()
      if (rendererRef.current === renderer) rendererRef.current = null
    }
  }, [analysis, data])

  return <div className="citation-map" ref={containerRef} aria-label="Citation graph" />
})

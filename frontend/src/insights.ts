import type {
  FrontierComponentName,
  GapHypothesis,
  GapStatus,
  GapType,
  GapVerdict,
  ProjectAnalysis,
  ProjectFrontier,
  ProjectGraph,
} from './api'
import type { GapLink } from './CitationMap'

export const FRONTIER_COMPONENTS: readonly { key: FrontierComponentName; label: string; title: string }[] = [
  { key: 'velocity', label: 'Velocity', title: 'Citations per year since publication, ranked among recent works.' },
  { key: 'local_uptake', label: 'Local uptake', title: 'Citations received from other works in this project.' },
  { key: 'main_path_leaf', label: 'Main path', title: 'Ends or extends the main citation path.' },
  { key: 'cluster_growth', label: 'Cluster growth', title: 'Its cluster is growing faster than the project overall.' },
  { key: 'new_concept', label: 'New concept', title: 'Carries a concept that first appeared in the window.' },
  { key: 'preprint', label: 'Preprint', title: 'Not yet published at a venue.' },
]

export const GAP_TYPE_ORDER: readonly GapType[] = ['bridging', 'matrix_void', 'stagnation']

export const GAP_TYPE_LABELS: Record<GapType, string> = {
  bridging: 'Bridging',
  matrix_void: 'Unexplored combinations',
  stagnation: 'Stagnating clusters',
}

export const GAP_TYPE_SHORT: Record<GapType, string> = {
  bridging: 'Bridging gap',
  matrix_void: 'Unexplored combination',
  stagnation: 'Stagnation',
}

export const GAP_STATUS_LABELS: Record<GapStatus, string> = {
  proposed: 'Proposed',
  accepted: 'Accepted',
  rejected: 'Rejected',
}

export const GAP_VERDICT_LABELS: Record<GapVerdict, string> = {
  likely_filled: 'Likely filled outside the project',
  possibly_open: 'Possibly open',
  unknown: 'Unknown',
}

/** Resolve concept labels from the graph and frontier, falling back to the id slug. */
export function conceptLabeler(
  graph: ProjectGraph | null,
  frontier: ProjectFrontier | null,
): (conceptId: string) => string {
  const labels = new Map<string, string>()
  frontier?.concepts.forEach((concept) => labels.set(concept.concept_id, concept.label))
  graph?.nodes.forEach((node) => {
    if (node.kind === 'concept') labels.set(node.id, node.label)
  })
  return (conceptId) => labels.get(conceptId)
    ?? conceptId.replace(/^concept:/, '').replace(/-\d+$/, '').replaceAll(/[-_]+/g, ' ')
}

/** Map each work id to the gaps naming it as evidence (in gap-list order). */
export function gapsByWork(gaps: readonly GapHypothesis[]): Map<string, GapHypothesis[]> {
  const byWork = new Map<string, GapHypothesis[]>()
  gaps.forEach((gap) => {
    gap.evidence.work_ids.forEach((workId) => {
      const list = byWork.get(workId)
      if (list) list.push(gap)
      else byWork.set(workId, [gap])
    })
  })
  return byWork
}

function mostCentralWork(clusterId: string, analysis: ProjectAnalysis): string | null {
  const cluster = analysis.clusters.find((item) => item.id === clusterId)
  let best: string | null = null
  let bestRank = -Infinity
  cluster?.work_ids.forEach((workId) => {
    const rank = analysis.works[workId]?.pagerank ?? 0
    if (rank > bestRank) {
      best = workId
      bestRank = rank
    }
  })
  return best
}

/**
 * Anchor each bridging gap between its two clusters: prefer the evidence work
 * belonging to each cluster (the detector names each cluster's most central
 * work), else the cluster's highest-PageRank work.
 */
export function bridgingLinks(
  gaps: readonly GapHypothesis[],
  analysis: ProjectAnalysis | null,
): GapLink[] {
  if (!analysis) return []
  const links: GapLink[] = []
  gaps.forEach((gap) => {
    if (gap.type !== 'bridging' || gap.evidence.cluster_ids.length < 2) return
    const [first, second] = gap.evidence.cluster_ids
    const anchor = (clusterId: string) => (
      gap.evidence.work_ids.find((workId) => analysis.works[workId]?.cluster === clusterId)
      ?? mostCentralWork(clusterId, analysis)
    )
    const source = anchor(first)
    const target = anchor(second)
    if (!source || !target || source === target) return
    links.push({ gapId: gap.id, source, target, label: gap.statement })
  })
  return links
}

/** Works a gap is about: its evidence works plus every work of its evidence clusters. */
export function gapFocusWorkIds(gap: GapHypothesis | null, analysis: ProjectAnalysis | null): Set<string> {
  const ids = new Set<string>()
  if (!gap) return ids
  gap.evidence.work_ids.forEach((workId) => ids.add(workId))
  gap.evidence.cluster_ids.forEach((clusterId) => {
    analysis?.clusters.find((cluster) => cluster.id === clusterId)?.work_ids.forEach((workId) => ids.add(workId))
  })
  return ids
}

export function formatPercent(value: number): string {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`
}

export function formatMetric(value: number): string {
  if (!Number.isFinite(value)) return String(value)
  if (Number.isInteger(value)) return String(value)
  return Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(3)
}

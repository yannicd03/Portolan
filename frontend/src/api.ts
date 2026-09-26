export type SourceTier = 'peerReviewed' | 'preprint' | 'officialBlog'

export interface Health {
  status: string
  store: string
  works: number
}

export interface WorkNode {
  id: string
  title: string
  year: number | null
  sourceTier: SourceTier | null
  isSurvey: boolean
  citedBy: number
}

export interface CitationEdge {
  source: string
  target: string
}

export interface CitationGraph {
  nodes: WorkNode[]
  edges: CitationEdge[]
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: 'application/json' } })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // keep the status text
    }
    throw new Error(`${response.status} ${detail}`)
  }
  return (await response.json()) as T
}

export const api = {
  health: () => getJson<Health>('/api/health'),
  graph: () => getJson<CitationGraph>('/api/graph'),
}

export interface Health {
  status: string
  store: string
  projects: number
}

export interface Project {
  id: string
  name: string
  description: string | null
  created_at: string
}

export interface ProjectStats {
  works: number
  citations: number
  authors: number
  concepts: number
  documents: number
}

export interface ProjectResponse {
  project: Project
  stats: ProjectStats
}

export interface CreateProjectInput {
  name: string
  description?: string | null
}

export interface WorkGraphData {
  year: number | null
  cited_by_count: number
  has_document: boolean
  in_degree: number
  out_degree: number
}

export interface WorkGraphNode {
  id: string
  kind: 'work'
  label: string
  data: WorkGraphData
}

export interface EntityGraphNode {
  id: string
  kind: 'author' | 'concept'
  label: string
  data: Record<string, unknown>
}

export type GraphNode = WorkGraphNode | EntityGraphNode

export type GraphEdgeKind = 'cites' | 'authored_by' | 'has_concept'

export interface GraphEdge {
  source: string
  target: string
  kind: GraphEdgeKind
}

export interface ProjectGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface ProjectGraphOptions {
  authors?: boolean
  concepts?: boolean
}

export interface WorkSummary {
  id: string
  title: string
  year: number | null
  cited_by_count: number
  document_sha256: string | null
}

export interface Work {
  id: string
  title: string
  year: number | null
  abstract: string | null
  doi: string | null
  arxiv_id: string | null
  openalex_id: string | null
  venue: string | null
  cited_by_count: number
  document_sha256: string | null
  [key: string]: unknown
}

export interface Author {
  id: string
  name: string
  orcid: string | null
}

export interface WorkAuthor {
  author: Author
  position: number
}

export interface Concept {
  id: string
  label: string
  aliases: string[]
}

export interface WorkConcept {
  concept: Concept
  score: number
}

export interface WorkDetail {
  work: Work
  cites: WorkSummary[]
  cited_by: WorkSummary[]
  authors: WorkAuthor[]
  concepts: WorkConcept[]
}

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface RunRequest {
  seeds: string[]
  query: string | null
  max_works: number
  snowball_depth: number
  acquire_pdfs: boolean
  max_pdfs: number
}

export interface RunProgress {
  stage: string
  message: string
  counts: Record<string, number>
  at: string
}

export interface RunReport {
  candidates_found: number
  screened_out: number
  included: number
  citations: number
  authors: number
  concepts: number
  pdfs_acquired: number
  pdfs_failed: number
  pdfs_skipped: number
  warnings: string[]
}

export interface Run {
  id: string
  project_id: string
  status: RunStatus
  request: RunRequest
  progress: RunProgress[]
  report: RunReport | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function detailMessage(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    const serialized = JSON.stringify(value)
    return serialized ?? String(value)
  } catch {
    return String(value)
  }
}

/** Make a path value safe while retaining `/` for backend `:path` routes. */
export function encodePathWithSlashes(value: string): string {
  // Encode every character except `/`; work IDs use slashes and the API route captures them.
  return encodeURIComponent(value).replaceAll('%2F', '/')
}

/** Fetch JSON, turning API error details into useful Error messages. */
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')
  const response = await fetch(path, { ...options, headers })

  if (!response.ok) {
    let detail = response.statusText || 'Request failed'
    try {
      const body: unknown = await response.json()
      if (isRecord(body) && 'detail' in body) {
        detail = detailMessage(body.detail)
      } else {
        detail = detailMessage(body)
      }
    } catch {
      // Keep the HTTP status text when the error body is not JSON.
    }
    throw new Error(`${response.status} ${detail}`)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function jsonOptions(body: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export const api = {
  health: (): Promise<Health> => request<Health>('/api/health'),

  projects: (): Promise<Project[]> => request<Project[]>('/api/projects'),

  project: (projectId: string): Promise<ProjectResponse> =>
    request<ProjectResponse>(`/api/projects/${encodeURIComponent(projectId)}`),

  createProject: (input: CreateProjectInput): Promise<Project> =>
    request<Project>('/api/projects', jsonOptions(input)),

  deleteProject: (projectId: string): Promise<void> =>
    request<void>(`/api/projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' }),

  projectGraph: (
    projectId: string,
    options: ProjectGraphOptions = {},
  ): Promise<ProjectGraph> => {
    const params = new URLSearchParams({
      authors: String(options.authors ?? true),
      concepts: String(options.concepts ?? true),
    })
    return request<ProjectGraph>(
      `/api/projects/${encodeURIComponent(projectId)}/graph?${params.toString()}`,
    )
  },

  search: (projectId: string, query: string, limit = 20): Promise<WorkSummary[]> => {
    const params = new URLSearchParams({ q: query, limit: String(limit) })
    return request<WorkSummary[]>(
      `/api/projects/${encodeURIComponent(projectId)}/search?${params.toString()}`,
    )
  },

  work: (workId: string, projectId: string): Promise<WorkDetail> =>
    request<WorkDetail>(
      `/api/works/${encodePathWithSlashes(workId)}?project=${encodeURIComponent(projectId)}`,
    ),

  startRun: (projectId: string, input: RunRequest): Promise<Run> =>
    request<Run>(`/api/projects/${encodeURIComponent(projectId)}/runs`, jsonOptions(input)),

  runs: (projectId: string): Promise<Run[]> =>
    request<Run[]>(`/api/projects/${encodeURIComponent(projectId)}/runs`),

  run: (runId: string): Promise<Run> =>
    request<Run>(`/api/runs/${encodeURIComponent(runId)}`),

  cancelRun: (runId: string): Promise<Run | undefined> =>
    request<Run | undefined>(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' }),

  documentPdfUrl: (sha256: string): string =>
    `/api/documents/${encodeURIComponent(sha256)}/pdf`,

  documentTextUrl: (sha256: string): string =>
    `/api/documents/${encodeURIComponent(sha256)}/text`,
}

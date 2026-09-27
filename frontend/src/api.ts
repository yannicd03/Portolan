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

export interface AnalysisCluster {
  id: string
  label: string
  size: number
  top_concepts: string[]
  work_ids: string[]
}

export type WorkRole = 'foundational' | 'bridge' | 'hub' | 'emerging' | 'peripheral'

export interface WorkAnalysis {
  cluster: string | null
  pagerank: number
  betweenness: number
  local_in: number
  local_out: number
  roles: WorkRole[]
}

export interface MainPath {
  work_ids: string[]
  edges: { source: string; target: string; spc: number }[]
}

export interface ProjectAnalysis {
  project_id: string
  computed_at: string
  clusters: AnalysisCluster[]
  works: Record<string, WorkAnalysis>
  main_path: MainPath
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
  /** Incremental counts added by the backend run registry (absent on older runs). */
  works_before?: number
  works_after?: number
  works_added?: number
  added_work_ids?: string[]
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

export interface ChatStatus {
  available: boolean
  model: string
  reason: string | null
}

export type CitationSource = 'paper' | 'abstract' | null

export interface VerifiedCitation {
  marker: number
  work_id: string
  title: string
  year: number | null
  quote: string
  page: number
  sha256: string | null
  offset: number | null
  verified: boolean
  source: CitationSource
}

export interface VerifiedAnswer {
  answer_markdown: string
  citations: VerifiedCitation[]
  unsupported: string[]
  model: string | null
  tool_calls: number
}

export interface ChatActivityEvent {
  text: string
  tool: string | null
}

export interface ResearchPlanArgs {
  query?: string | null
  seeds?: string[]
  from_year?: number | null
  to_year?: number | null
  max_works?: number
  snowball_depth?: number
  acquire_pdfs?: boolean
  rationale?: string
  [key: string]: unknown
}

export interface ResearchPlanPayload {
  tool_call_id: string
  args: ResearchPlanArgs
  description: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  answer: VerifiedAnswer | null
  events: ChatActivityEvent[]
  created_at: string
  error: string | null
  mode?: 'ask' | 'research' | null
  pending_plan?: ResearchPlanArgs | null
  plan_status?: 'pending' | 'approved' | 'edited' | 'rejected' | 'expired' | null
  final_args?: ResearchPlanArgs | null
  run_id?: string | null
}

export interface ChatThread {
  id: string
  project_id: string
  title: string
  created_at: string
  updated_at: string
  messages: ChatMessage[]
}

export interface ChatThreadSummary {
  id: string
  project_id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
}

export interface LocateHit {
  page: number
  offset: number
  snippet: string
}

export interface DocumentLocateResponse {
  found: boolean
  hits: LocateHit[]
}

export type FrontierComponentName =
  | 'velocity'
  | 'local_uptake'
  | 'main_path_leaf'
  | 'cluster_growth'
  | 'new_concept'
  | 'preprint'

export type FrontierComponents = Record<FrontierComponentName, number>

export interface FrontierWork {
  work_id: string
  title: string
  year: number | null
  score: number
  components: FrontierComponents
}

export interface FrontierConcept {
  concept_id: string
  label: string
  first_year: number
  adoption_by_year: Record<string, number>
}

export interface ProjectFrontier {
  now_year: number | null
  window_years: number
  works: FrontierWork[]
  concepts: FrontierConcept[]
}

export type GapType = 'bridging' | 'matrix_void' | 'stagnation'

export type GapStatus = 'proposed' | 'accepted' | 'rejected'

export type GapVerdict = 'likely_filled' | 'possibly_open' | 'unknown'

export interface GapEvidence {
  work_ids: string[]
  concept_ids: string[]
  cluster_ids: string[]
}

export interface GapOutsideHit {
  id: string
  title: string
  year: number | null
}

export interface GapVerification {
  query: string
  checked_at: string
  total_hits_sampled?: number
  outside_hits: GapOutsideHit[]
  verdict: GapVerdict
  error?: string
  [key: string]: unknown
}

export interface GapHypothesis {
  id: string
  type: GapType
  statement: string
  evidence: GapEvidence
  metrics: Record<string, number>
  confidence: number
  status: GapStatus
  note: string | null
  verification: GapVerification | null
  stale?: boolean
  search_terms?: string[]
}

export interface GapUpdateInput {
  status?: GapStatus
  note?: string | null
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

  analysis: (projectId: string): Promise<ProjectAnalysis> =>
    request<ProjectAnalysis>(`/api/projects/${encodeURIComponent(projectId)}/analysis`),

  frontier: (projectId: string, windowYears = 2): Promise<ProjectFrontier> =>
    request<ProjectFrontier>(
      `/api/projects/${encodeURIComponent(projectId)}/frontier?window_years=${encodeURIComponent(String(windowYears))}`,
    ),

  gaps: (projectId: string, includeRejected = false): Promise<GapHypothesis[]> =>
    request<GapHypothesis[]>(
      `/api/projects/${encodeURIComponent(projectId)}/gaps?include_rejected=${String(includeRejected)}`,
    ),

  updateGap: (projectId: string, gapId: string, input: GapUpdateInput): Promise<GapHypothesis> =>
    request<GapHypothesis>(
      `/api/projects/${encodeURIComponent(projectId)}/gaps/${encodeURIComponent(gapId)}`,
      { ...jsonOptions(input), method: 'PATCH' },
    ),

  verifyGap: (projectId: string, gapId: string): Promise<GapHypothesis> =>
    request<GapHypothesis>(
      `/api/projects/${encodeURIComponent(projectId)}/gaps/${encodeURIComponent(gapId)}/verify`,
      { method: 'POST' },
    ),

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

  chatStatus: (): Promise<ChatStatus> => request<ChatStatus>('/api/chat/status'),

  chatThreads: (projectId: string): Promise<ChatThreadSummary[]> =>
    request<ChatThreadSummary[]>(`/api/projects/${encodeURIComponent(projectId)}/chats`),

  createChat: (projectId: string, title?: string): Promise<ChatThread> =>
    request<ChatThread>(
      `/api/projects/${encodeURIComponent(projectId)}/chats`,
      jsonOptions(title ? { title } : {}),
    ),

  chatThread: (threadId: string, projectId: string): Promise<ChatThread> =>
    request<ChatThread>(
      `/api/chats/${encodeURIComponent(threadId)}?project=${encodeURIComponent(projectId)}`,
    ),

  deleteChat: (threadId: string, projectId: string): Promise<void> =>
    request<void>(
      `/api/chats/${encodeURIComponent(threadId)}?project=${encodeURIComponent(projectId)}`,
      { method: 'DELETE' },
    ),

  chatMessageUrl: (threadId: string, projectId: string): string =>
    `/api/chats/${encodeURIComponent(threadId)}/messages?project=${encodeURIComponent(projectId)}`,

  chatResumeUrl: (threadId: string, projectId: string): string =>
    `/api/chats/${encodeURIComponent(threadId)}/resume?project=${encodeURIComponent(projectId)}`,

  locateDocument: (sha256: string, query: string, page?: number): Promise<DocumentLocateResponse> => {
    const params = new URLSearchParams({ q: query })
    if (page !== undefined) params.set('page', String(page))
    return request<DocumentLocateResponse>(
      `/api/documents/${encodeURIComponent(sha256)}/locate?${params.toString()}`,
    )
  },

  documentPdfUrl: (sha256: string): string =>
    `/api/documents/${encodeURIComponent(sha256)}/pdf`,

  documentTextUrl: (sha256: string): string =>
    `/api/documents/${encodeURIComponent(sha256)}/text`,
}

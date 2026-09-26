import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { Project, ProjectStats } from './api'
import { GraphPanel } from './GraphPanel'
import { ProjectRail } from './ProjectRail'
import { ResearchThread } from './ResearchThread'
import { parseHashRoute, projectHref } from './route'
import type { HashRoute } from './route'

const PdfViewer = lazy(() => import('./PdfViewer'))

function savedGraphState(): boolean {
  try {
    return window.localStorage.getItem('portolan.graphCollapsed') === 'true'
  } catch {
    return false
  }
}

export default function App() {
  const [projects, setProjects] = useState<Project[]>([])
  const [route, setRoute] = useState<HashRoute | null>(() => parseHashRoute(window.location.hash))
  const [selectedId, setSelectedId] = useState<string | null>(() => parseHashRoute(window.location.hash)?.projectId ?? null)
  const [stats, setStats] = useState<ProjectStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshToken, setRefreshToken] = useState(0)
  const [graphCollapsed, setGraphCollapsed] = useState(savedGraphState)

  useEffect(() => {
    let active = true
    api.projects()
      .then((items) => {
        if (!active) return
        const ordered = [...items].sort((a, b) => b.created_at.localeCompare(a.created_at))
        setProjects(ordered)
        const fromHash = parseHashRoute(window.location.hash)?.projectId ?? null
        const next = ordered.find((project) => project.id === fromHash)?.id ?? ordered[0]?.id ?? null
        setSelectedId(next)
        if (next && next !== fromHash) window.location.hash = projectHref(next)
      })
      .catch((reason: Error) => {
        if (active) setError(reason.message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [])

  useEffect(() => {
    const onHashChange = () => {
      const nextRoute = parseHashRoute(window.location.hash)
      const id = nextRoute?.projectId
      if (id && projects.some((project) => project.id === id)) {
        if (id !== selectedId) {
          setStats(null)
          setSelectedId(id)
        }
        setRoute(nextRoute)
      }
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [projects, selectedId])

  useEffect(() => {
    if (!selectedId) return
    let active = true
    api.project(selectedId)
      .then(({ stats: nextStats }) => {
        if (active) {
          setStats(nextStats)
          setError(null)
        }
      })
      .catch((reason: Error) => {
        if (active) setError(reason.message)
      })
    return () => { active = false }
  }, [selectedId, refreshToken])

  const selectProject = useCallback((id: string) => {
    setStats(null)
    setSelectedId(id)
    window.location.hash = projectHref(id)
  }, [])

  const onCreated = useCallback((project: Project) => {
    setProjects((current) => [project, ...current])
    setError(null)
    selectProject(project.id)
  }, [selectProject])

  const onDeleted = useCallback((id: string) => {
    const remaining = projects.filter((project) => project.id !== id)
    setProjects(remaining)
    if (selectedId === id) {
      const next = remaining[0]?.id ?? null
      setSelectedId(next)
      setStats(null)
      window.location.hash = next ? projectHref(next) : ''
    }
  }, [projects, selectedId])

  const toggleGraph = () => {
    setGraphCollapsed((current) => {
      const next = !current
      try {
        window.localStorage.setItem('portolan.graphCollapsed', String(next))
      } catch {
        // The layout remains usable when browser storage is unavailable.
      }
      return next
    })
  }

  const selectedProject = projects.find((project) => project.id === selectedId) ?? null

  return (
    <div className={`app-shell ${graphCollapsed || !selectedProject ? 'graph-is-collapsed' : ''}`}>
      <ProjectRail
        projects={projects}
        selectedId={selectedId}
        onSelect={selectProject}
        onCreated={onCreated}
        onDeleted={onDeleted}
      />
      <main className="main-panel">
        {error && <div className="app-error" role="alert">{error}</div>}
        {selectedProject && selectedId ? (
          <>
            <header className="project-header">
              <div className="project-heading">
                <div>
                  <p className="eyebrow">Research workspace</p>
                  <h1>{selectedProject.name}</h1>
                  {selectedProject.description && <p className="project-description">{selectedProject.description}</p>}
                </div>
                <button type="button" className="graph-toggle" onClick={toggleGraph} aria-expanded={!graphCollapsed} aria-controls="graph-panel">
                  {graphCollapsed ? 'Show map' : 'Hide map'}
                </button>
              </div>
              <div className="stats" aria-label="Project statistics">
                <span><strong>{stats?.works ?? '—'}</strong> works</span>
                <span><strong>{stats?.citations ?? '—'}</strong> citations</span>
                <span><strong>{stats?.authors ?? '—'}</strong> authors</span>
                <span><strong>{stats?.concepts ?? '—'}</strong> concepts</span>
                <span><strong>{stats?.documents ?? '—'}</strong> PDFs</span>
              </div>
            </header>
            {route?.kind === 'doc' && route.projectId === selectedId ? (
              <Suspense fallback={<p className="research-empty">Loading PDF viewer…</p>}>
                <PdfViewer
                  key={`${route.projectId}/${route.sha256}/${route.page}/${route.quote ?? ''}`}
                  projectId={route.projectId}
                  sha256={route.sha256}
                  page={route.page}
                  quote={route.quote}
                  workId={route.workId}
                />
              </Suspense>
            ) : (
              <ResearchThread key={selectedId} projectId={selectedId} onRunFinished={() => setRefreshToken((value) => value + 1)} />
            )}
          </>
        ) : (
          <div className="welcome-state">
            <p className="eyebrow">Portolan</p>
            <h1>{loading ? 'Loading projects…' : 'Start a research workspace'}</h1>
            {!loading && <p>Create a project in the left rail to begin.</p>}
          </div>
        )}
      </main>
      {!graphCollapsed && selectedProject && selectedId && <GraphPanel key={selectedId} projectId={selectedId} refreshToken={refreshToken} />}
    </div>
  )
}

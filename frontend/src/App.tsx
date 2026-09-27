import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { Project, ProjectStats } from './api'
import { AskThread } from './AskThread'
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

type MainTab = 'ask' | 'research'

function savedTab(projectId: string): MainTab | null {
  try {
    const value = window.localStorage.getItem(`portolan.tab.${projectId}`)
    return value === 'ask' || value === 'research' ? value : null
  } catch {
    return null
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
  const [openRunRequest, setOpenRunRequest] = useState<{ projectId: string; runId: string; token: number } | null>(null)
  const [graphCollapsed, setGraphCollapsed] = useState(savedGraphState)
  const [tabByProject, setTabByProject] = useState<Record<string, MainTab>>({})
  const [activatedAskProjects, setActivatedAskProjects] = useState<Record<string, boolean>>({})
  const [highlightedWorkIds, setHighlightedWorkIds] = useState<string[]>([])
  const [openWorkRequest, setOpenWorkRequest] = useState<{ projectId: string; workId: string; token: number } | null>(null)

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
          setHighlightedWorkIds([])
          setOpenWorkRequest(null)
          setOpenRunRequest(null)
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
    setHighlightedWorkIds([])
    setOpenWorkRequest(null)
    setOpenRunRequest(null)
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
      setHighlightedWorkIds([])
      setOpenWorkRequest(null)
      setOpenRunRequest(null)
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

  const selectTab = (projectId: string, tab: MainTab) => {
    setTabByProject((current) => ({ ...current, [projectId]: tab }))
    if (tab === 'ask') setActivatedAskProjects((current) => ({ ...current, [projectId]: true }))
    if (tab === 'research') setHighlightedWorkIds([])
    try {
      window.localStorage.setItem(`portolan.tab.${projectId}`, tab)
    } catch {
      // The tabs remain usable when browser storage is unavailable.
    }
  }

  const openWork = useCallback((workId: string) => {
    if (!selectedId) return
    setGraphCollapsed(false)
    setOpenWorkRequest((current) => ({ projectId: selectedId, workId, token: (current?.token ?? 0) + 1 }))
  }, [selectedId])

  const openRun = (runId: string) => {
    if (!selectedId) return
    setOpenRunRequest((current) => ({ projectId: selectedId, runId, token: (current?.token ?? 0) + 1 }))
    selectTab(selectedId, 'research')
  }

  const selectedProject = projects.find((project) => project.id === selectedId) ?? null
  const selectedTab = selectedId
    ? tabByProject[selectedId] ?? savedTab(selectedId) ?? (stats && stats.works > 0 ? 'ask' : 'research')
    : 'research'

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
              <>
                <div className="main-tabs" role="tablist" aria-label="Workspace mode">
                  <button type="button" role="tab" id="ask-tab" aria-controls="ask-panel" aria-selected={selectedTab === 'ask'} onClick={() => selectTab(selectedId, 'ask')}>Ask</button>
                  <button type="button" role="tab" id="research-tab" aria-controls="research-panel" aria-selected={selectedTab === 'research'} onClick={() => selectTab(selectedId, 'research')}>Research</button>
                </div>
                {selectedTab === 'ask' || activatedAskProjects[selectedId] ? (
                  <div className="main-tab-panel" role="tabpanel" id="ask-panel" aria-labelledby="ask-tab" hidden={selectedTab !== 'ask'}>
                    <AskThread key={selectedId} projectId={selectedId} worksCount={stats?.works ?? null} onHighlightedWorkIds={setHighlightedWorkIds} onOpenWork={openWork} onOpenRun={openRun} onRunFinished={() => setRefreshToken((value) => value + 1)} />
                  </div>
                ) : null}
                <div className="main-tab-panel" role="tabpanel" id="research-panel" aria-labelledby="research-tab" hidden={selectedTab !== 'research'}>
                  <ResearchThread key={selectedId} projectId={selectedId} refreshToken={refreshToken} openRunRequest={openRunRequest?.projectId === selectedId ? openRunRequest : null} onRunFinished={() => setRefreshToken((value) => value + 1)} />
                </div>
              </>
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
      {!graphCollapsed && selectedProject && selectedId && <GraphPanel key={selectedId} projectId={selectedId} refreshToken={refreshToken} highlightedWorkIds={selectedTab === 'ask' && route?.kind !== 'doc' ? highlightedWorkIds : []} openWorkRequest={openWorkRequest?.projectId === selectedId ? openWorkRequest : null} />}
    </div>
  )
}

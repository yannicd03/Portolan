import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from './api'
import type { Run, RunRequest } from './api'
import { RunCard } from './RunCard'

interface ResearchThreadProps {
  projectId: string
  refreshToken: number
  openRunRequest: { runId: string; token: number } | null
  onRunFinished: () => void
}

function isActive(status: Run['status']): boolean {
  return status === 'queued' || status === 'running'
}

function isTerminal(status: Run['status']): boolean {
  return status === 'succeeded' || status === 'failed' || status === 'cancelled'
}

function readNumber(value: string, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

// Mirrors the backend defaults and bounds (ResearchRunRequest); values left at the
// default are not sent, which keeps the run request small.
const DEFAULT_CORE_SEARCH_HITS = 10
const MAX_CORE_SEARCH_HITS = 50
const DEFAULT_CHASE_TOP = 20
const MAX_CHASE_TOP = 100
const DEFAULT_MIN_SCORE = 0.15

function clampInt(value: string, fallback: number, max: number): number {
  return Math.min(max, Math.max(0, Math.floor(readNumber(value, fallback))))
}

function splitLines(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}

export function ResearchThread({ projectId, refreshToken, openRunRequest, onRunFinished }: ResearchThreadProps) {
  const [runs, setRuns] = useState<Run[]>([])
  const [topic, setTopic] = useState('')
  const [seeds, setSeeds] = useState('')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [maxWorks, setMaxWorks] = useState(100)
  const [snowballDepth, setSnowballDepth] = useState(2)
  const [acquirePdfs, setAcquirePdfs] = useState(true)
  const [maxPdfs, setMaxPdfs] = useState(50)
  const [exclude, setExclude] = useState('')
  const [coreSearchHits, setCoreSearchHits] = useState(DEFAULT_CORE_SEARCH_HITS)
  const [chaseTop, setChaseTop] = useState(DEFAULT_CHASE_TOP)
  // Kept as text so partial input such as "0." can be typed; validated on submit.
  const [minScore, setMinScore] = useState(String(DEFAULT_MIN_SCORE))
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [cancellingRunId, setCancellingRunId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [runsProjectId, setRunsProjectId] = useState<string | null>(null)
  const statusSnapshot = useRef(new Map<string, Run['status']>())
  const activeRunIdsRef = useRef<string[]>([])
  const pollInFlight = useRef(false)
  const scrolledRunToken = useRef<number | null>(null)

  useEffect(() => {
    let cancelled = false
    statusSnapshot.current.clear()

    api.runs(projectId)
      .then((loadedRuns) => {
        if (cancelled) return
        setRuns(loadedRuns)
        setRunsProjectId(projectId)
        setError(null)
      })
      .catch((reason: unknown) => {
        if (cancelled) return
        setRuns([])
        setRunsProjectId(projectId)
        setError(reason instanceof Error ? reason.message : 'Unable to load research runs.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [projectId, refreshToken, openRunRequest?.token])

  const projectRuns = useMemo(
    () => (runsProjectId === projectId ? runs : []),
    [projectId, runs, runsProjectId],
  )

  useEffect(() => {
    if (!openRunRequest || scrolledRunToken.current === openRunRequest.token
      || !projectRuns.some((run) => run.id === openRunRequest.runId)) return
    const card = document.getElementById(`research-run-${openRunRequest.runId}`)
    card?.scrollIntoView({ block: 'center' })
    card?.focus({ preventScroll: true })
    scrolledRunToken.current = openRunRequest.token
  }, [openRunRequest, projectRuns])

  useEffect(() => {
    const previousStatuses = statusSnapshot.current
    const completedRun = projectRuns.some((run) => {
      const previousStatus = previousStatuses.get(run.id)
      return previousStatus !== undefined && isActive(previousStatus) && isTerminal(run.status)
    })

    statusSnapshot.current = new Map(projectRuns.map((run) => [run.id, run.status]))
    if (completedRun) onRunFinished()
  }, [onRunFinished, projectRuns])

  const activeRunIds = useMemo(
    () => projectRuns.filter((run) => isActive(run.status)).map((run) => run.id),
    [projectRuns],
  )
  useEffect(() => {
    activeRunIdsRef.current = activeRunIds
  }, [activeRunIds])
  const activeRunKey = activeRunIds.join('\u001f')

  useEffect(() => {
    if (activeRunIdsRef.current.length === 0) return undefined
    let cancelled = false

    const poll = async () => {
      if (pollInFlight.current) return
      pollInFlight.current = true
      const ids = activeRunIdsRef.current
      try {
        const responses = await Promise.allSettled(ids.map((runId) => api.run(runId)))
        if (cancelled) return

        const updatedRuns = responses.flatMap((response) =>
          response.status === 'fulfilled' ? [response.value] : [],
        )
        const failedResponse = responses.find((response) => response.status === 'rejected')
        if (failedResponse?.status === 'rejected') {
          setError(
            failedResponse.reason instanceof Error
              ? failedResponse.reason.message
              : 'Unable to refresh research progress.',
          )
        }
        if (updatedRuns.length > 0) {
          setRuns((currentRuns) => {
            const byId = new Map(updatedRuns.map((run) => [run.id, run]))
            return currentRuns.map((run) => byId.get(run.id) ?? run)
          })
        }
      } finally {
        pollInFlight.current = false
      }
    }

    const intervalId = window.setInterval(() => void poll(), 1500)
    return () => {
      cancelled = true
      window.clearInterval(intervalId)
    }
  }, [activeRunKey, projectId])

  const handleStart = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmedTopic = topic.trim()
    const parsedSeeds = splitLines(seeds)
    if (!trimmedTopic && parsedSeeds.length === 0) {
      setError('Enter a topic or add at least one seed paper.')
      return
    }
    const trimmedMinScore = minScore.trim()
    const parsedMinScore = trimmedMinScore === '' ? DEFAULT_MIN_SCORE : Number(trimmedMinScore)
    if (!Number.isFinite(parsedMinScore) || parsedMinScore < 0 || parsedMinScore > 1) {
      setAdvancedOpen(true)
      setError('Minimum screening score must be a number between 0 and 1.')
      return
    }
    const parsedExclude = splitLines(exclude)

    setSubmitting(true)
    setError(null)

    const request: RunRequest = {
      seeds: parsedSeeds,
      query: trimmedTopic || null,
      max_works: Math.max(1, Math.floor(maxWorks)),
      snowball_depth: Math.min(2, Math.max(0, Math.floor(snowballDepth))),
      acquire_pdfs: acquirePdfs,
      max_pdfs: Math.max(0, Math.floor(maxPdfs)),
    }
    if (parsedExclude.length > 0) request.exclude = parsedExclude
    if (coreSearchHits !== DEFAULT_CORE_SEARCH_HITS) request.core_search_hits = coreSearchHits
    if (chaseTop !== DEFAULT_CHASE_TOP) request.chase_top = chaseTop
    if (parsedMinScore !== DEFAULT_MIN_SCORE) request.min_score = parsedMinScore

    try {
      const createdRun = await api.startRun(projectId, request)
      setRuns((currentRuns) => [createdRun, ...currentRuns.filter((run) => run.id !== createdRun.id)])
      setTopic('')
      setSeeds('')
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'Unable to start the research run.')
    } finally {
      setSubmitting(false)
    }
  }

  const handleCancel = async (runId: string) => {
    if (cancellingRunId !== null) return
    setCancellingRunId(runId)
    setError(null)
    try {
      await api.cancelRun(runId)
      const updatedRun = await api.run(runId)
      setRuns((currentRuns) => currentRuns.map((run) => (run.id === updatedRun.id ? updatedRun : run)))
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'Unable to cancel the research run.')
    } finally {
      setCancellingRunId(null)
    }
  }

  return (
    <section className="research-thread" aria-labelledby="research-heading">
      <div className="research-thread-heading">
        <div>
          <p className="eyebrow">Research</p>
          <h2 id="research-heading">Build your literature map</h2>
        </div>
      </div>

      <div className="research-run-list" aria-live="polite">
        {loading || runsProjectId !== projectId ? <p className="research-empty">Loading research runs…</p> : null}
        {!loading && runsProjectId === projectId && projectRuns.length === 0 ? (
          <p className="research-empty">Start with a topic or a seed paper to begin researching.</p>
        ) : null}
        {projectRuns.map((run) => (
          <div key={run.id} id={`research-run-${run.id}`} tabIndex={-1}>
            <RunCard
              run={run}
              cancelling={cancellingRunId === run.id}
              onCancel={handleCancel}
            />
          </div>
        ))}
      </div>

      <form className="research-composer" onSubmit={handleStart}>
        <div className="research-composer-field">
          <label className="research-composer-label" htmlFor="research-topic">
            Topic or question
          </label>
          <textarea
            className="research-composer-input"
            id="research-topic"
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
            placeholder="What should this project investigate?"
            rows={3}
          />
        </div>

        <div className="research-composer-field">
          <label className="research-composer-label" htmlFor="research-seeds">
            Seed papers <span>(DOI / arXiv / OpenAlex id, one per line)</span>
          </label>
          <textarea
            className="research-composer-input"
            id="research-seeds"
            value={seeds}
            onChange={(event) => setSeeds(event.target.value)}
            placeholder={'doi:10.18653/v1/N19-1423\narxiv:1706.03762'}
            rows={3}
          />
        </div>

        <div className="research-advanced">
          <button
            className="research-advanced-toggle"
            type="button"
            aria-expanded={advancedOpen}
            aria-controls="research-advanced-options"
            onClick={() => setAdvancedOpen((open) => !open)}
          >
            Advanced <span aria-hidden="true">{advancedOpen ? '▴' : '▾'}</span>
          </button>
          {advancedOpen ? (
            <div className="research-advanced-grid" id="research-advanced-options">
              <label className="research-composer-field" htmlFor="research-max-works">
                Max works
                <input
                  className="research-composer-input"
                  id="research-max-works"
                  type="number"
                  min={1}
                  value={maxWorks}
                  onChange={(event) => setMaxWorks(Math.max(1, Math.floor(readNumber(event.target.value, 100))))}
                />
              </label>
              <label className="research-composer-field" htmlFor="research-snowball-depth">
                Snowball depth
                <input
                  className="research-composer-input"
                  id="research-snowball-depth"
                  type="number"
                  min={0}
                  max={2}
                  value={snowballDepth}
                  onChange={(event) =>
                    setSnowballDepth(Math.min(2, Math.max(0, Math.floor(readNumber(event.target.value, 1)))))
                  }
                />
              </label>
              <label className="research-composer-field research-pdf-toggle" htmlFor="research-acquire-pdfs">
                <span>Download PDFs</span>
                <input
                  id="research-acquire-pdfs"
                  type="checkbox"
                  checked={acquirePdfs}
                  onChange={(event) => setAcquirePdfs(event.target.checked)}
                />
              </label>
              <label className="research-composer-field" htmlFor="research-max-pdfs">
                Max PDFs
                <input
                  className="research-composer-input"
                  id="research-max-pdfs"
                  type="number"
                  min={0}
                  value={maxPdfs}
                  onChange={(event) => setMaxPdfs(Math.max(0, Math.floor(readNumber(event.target.value, 50))))}
                />
              </label>
              <label className="research-composer-field" htmlFor="research-core-search-hits">
                Core search hits
                <input
                  className="research-composer-input"
                  id="research-core-search-hits"
                  type="number"
                  min={0}
                  max={MAX_CORE_SEARCH_HITS}
                  value={coreSearchHits}
                  onChange={(event) =>
                    setCoreSearchHits(clampInt(event.target.value, DEFAULT_CORE_SEARCH_HITS, MAX_CORE_SEARCH_HITS))
                  }
                />
                <span className="research-advanced-help">Top topic-search hits that join the seeds as core papers (0–50).</span>
              </label>
              <label className="research-composer-field" htmlFor="research-chase-top">
                Chase top candidates
                <input
                  className="research-composer-input"
                  id="research-chase-top"
                  type="number"
                  min={0}
                  max={MAX_CHASE_TOP}
                  value={chaseTop}
                  onChange={(event) => setChaseTop(clampInt(event.target.value, DEFAULT_CHASE_TOP, MAX_CHASE_TOP))}
                />
                <span className="research-advanced-help">Best candidates whose references are followed at depth 2 (0–100).</span>
              </label>
              <label className="research-composer-field" htmlFor="research-min-score">
                Minimum screening score
                <input
                  className="research-composer-input"
                  id="research-min-score"
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={minScore}
                  onChange={(event) => setMinScore(event.target.value)}
                />
                <span className="research-advanced-help">Candidates scoring below this are left out (0–1).</span>
              </label>
              <label className="research-composer-field research-advanced-wide" htmlFor="research-exclude">
                Exclude papers <span className="research-advanced-help">(DOI / arXiv / OpenAlex id, one per line)</span>
                <textarea
                  className="research-composer-input"
                  id="research-exclude"
                  value={exclude}
                  onChange={(event) => setExclude(event.target.value)}
                  placeholder="doi:10.1000/unrelated-paper"
                  rows={2}
                />
                <span className="research-advanced-help">Kept out of the map even when found by search or citations.</span>
              </label>
            </div>
          ) : null}
        </div>

        {error ? <p className="research-error" role="alert">{error}</p> : null}

        <div className="research-composer-actions">
          <button className="research-submit" type="submit" disabled={submitting}>
            {submitting ? 'Starting…' : 'Start research'}
          </button>
        </div>
      </form>
    </section>
  )
}

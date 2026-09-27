import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from './api'
import type { Run, RunRequest } from './api'
import { RunCard } from './RunCard'

interface ResearchThreadProps {
  projectId: string
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

export function ResearchThread({ projectId, onRunFinished }: ResearchThreadProps) {
  const [runs, setRuns] = useState<Run[]>([])
  const [topic, setTopic] = useState('')
  const [seeds, setSeeds] = useState('')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [maxWorks, setMaxWorks] = useState(100)
  const [snowballDepth, setSnowballDepth] = useState(1)
  const [acquirePdfs, setAcquirePdfs] = useState(false)
  const [maxPdfs, setMaxPdfs] = useState(50)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [cancellingRunId, setCancellingRunId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [runsProjectId, setRunsProjectId] = useState<string | null>(null)
  const statusSnapshot = useRef(new Map<string, Run['status']>())
  const activeRunIdsRef = useRef<string[]>([])
  const pollInFlight = useRef(false)

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
  }, [projectId])

  const projectRuns = useMemo(
    () => (runsProjectId === projectId ? runs : []),
    [projectId, runs, runsProjectId],
  )

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
    const parsedSeeds = seeds
      .split('\n')
      .map((seed) => seed.trim())
      .filter((seed) => seed.length > 0)
    if (!trimmedTopic && parsedSeeds.length === 0) {
      setError('Enter a topic or add at least one seed paper.')
      return
    }

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
          <RunCard
            key={run.id}
            run={run}
            cancelling={cancellingRunId === run.id}
            onCancel={handleCancel}
          />
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

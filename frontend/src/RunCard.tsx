import type { Run } from './api'

interface RunCardProps {
  run: Run
  cancelling: boolean
  onCancel: (runId: string) => void
}

const STATUS_LABELS: Record<Run['status'], string> = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

const REPORT_FIELDS: Array<{ key: keyof NonNullable<Run['report']>; label: string }> = [
  { key: 'candidates_found', label: 'Candidates' },
  { key: 'screened_out', label: 'Screened out' },
  { key: 'included', label: 'Included' },
  { key: 'citations', label: 'Citations' },
  { key: 'authors', label: 'Authors' },
  { key: 'concepts', label: 'Concepts' },
  { key: 'pdfs_acquired', label: 'PDFs acquired' },
  { key: 'pdfs_failed', label: 'PDFs failed' },
  { key: 'pdfs_skipped', label: 'PDFs skipped' },
]

function isActive(status: Run['status']): boolean {
  return status === 'queued' || status === 'running'
}

function formatStatus(status: Run['status']): string {
  return STATUS_LABELS[status]
}

function formatCountLabel(label: string): string {
  return label.replaceAll('_', ' ')
}

// Mirrors INTERRUPTED_ERROR in backend/portolan/api/runs.py.
const INTERRUPTED_ERROR = 'interrupted by a backend restart'

function formatIncrement(report: NonNullable<Run['report']>): string | null {
  const { works_added: added, works_after: total } = report
  if (typeof added !== 'number' || typeof total !== 'number') return null
  return `+${added} new work${added === 1 ? '' : 's'} (${total} total)`
}

function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString()
}

export function RunCard({ run, cancelling, onCancel }: RunCardProps) {
  const latestProgress = run.progress.at(-1)
  const report = run.report
  const seedCount = run.request.seeds.length
  const query = run.request.query?.trim()
  const increment = report ? formatIncrement(report) : null
  const interrupted = run.error === INTERRUPTED_ERROR

  return (
    <article className={`run-card run-card-${run.status}`}>
      <header className="run-card-header">
        <div>
          <span className={`run-card-status run-card-status-${run.status}`} role="status">
            {formatStatus(run.status)}
          </span>
          <time className="run-card-created" dateTime={run.created_at}>
            {formatTimestamp(run.created_at)}
          </time>
        </div>
        {isActive(run.status) ? (
          <button
            className="run-card-cancel"
            type="button"
            onClick={() => onCancel(run.id)}
            disabled={cancelling}
            aria-label={`Cancel research run${cancelling ? ' (cancelling)' : ''}`}
          >
            {cancelling ? 'Cancelling…' : 'Cancel'}
          </button>
        ) : null}
      </header>

      <p className="run-card-summary">
        {query || 'Seed paper discovery'} · {seedCount} seed{seedCount === 1 ? '' : 's'} · up to{' '}
        {run.request.max_works} works · snowball depth {run.request.snowball_depth}
      </p>

      {latestProgress ? (
        <section className="run-card-progress" aria-label="Research progress" aria-live="polite">
          <div className="run-card-progress-meta">
            <strong>{latestProgress.stage}</strong>
            <span>{latestProgress.message}</span>
          </div>
          {Object.keys(latestProgress.counts).length > 0 ? (
            <div className="run-card-counts">
              {Object.entries(latestProgress.counts).map(([key, value]) => (
                <span className="run-card-count" key={key}>
                  <strong>{value}</strong> {formatCountLabel(key)}
                </span>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}

      {report ? (
        <section className="run-card-report" aria-label="Research report">
          <h3>Report</h3>
          {increment ? <p className="run-card-summary">{increment}</p> : null}
          <dl className="run-card-report-grid">
            {REPORT_FIELDS.map(({ key, label }) => (
              <div className="run-card-report-item" key={key}>
                <dt>{label}</dt>
                <dd>{report[key]}</dd>
              </div>
            ))}
          </dl>
          {report.warnings.length > 0 ? (
            <div className="run-card-warnings">
              <h4>Warnings</h4>
              <ul>
                {report.warnings.map((warning, index) => (
                  <li key={`${warning}-${index}`}>{warning}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>
      ) : null}

      {interrupted ? (
        <p className="run-card-summary" style={{ color: 'var(--text-soft)', fontWeight: 'normal' }}>
          Interrupted by a backend restart
        </p>
      ) : run.error ? (
        <p className="run-card-error" role="alert">{run.error}</p>
      ) : null}
    </article>
  )
}

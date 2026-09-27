import type { FrontierWork } from './api'
import { FRONTIER_COMPONENTS, formatPercent } from './insights'

/** The frontier score broken into its labelled components; never a single opaque number. */
export function FrontierBars({ work, compact = false }: { work: FrontierWork; compact?: boolean }) {
  return (
    <div className={`frontier-bars${compact ? ' frontier-bars--compact' : ''}`}>
      <p className="frontier-bars__score">
        Frontier score <strong>{formatPercent(work.score)}</strong>
      </p>
      <dl className="frontier-bars__list">
        {FRONTIER_COMPONENTS.map((component) => {
          const value = work.components[component.key] ?? 0
          return (
            <div className="frontier-bars__row" key={component.key} title={component.title}>
              <dt>{component.label}</dt>
              <dd>
                <span
                  className="frontier-bars__track"
                  role="meter"
                  aria-label={component.label}
                  aria-valuemin={0}
                  aria-valuemax={1}
                  aria-valuenow={Number(value.toFixed(2))}
                >
                  <span className="frontier-bars__fill" style={{ width: formatPercent(value) }} />
                </span>
                <span className="frontier-bars__value">{value.toFixed(2)}</span>
              </dd>
            </div>
          )
        })}
      </dl>
    </div>
  )
}

export interface FrontierListProps {
  works: readonly FrontierWork[] | null
  nowYear: number | null
  windowYears: number
  unavailable: boolean
  selectedId: string | null
  onSelectWork: (workId: string) => void
}

/** Top-ten frontier works by score; selecting one selects it on the map. */
export function FrontierList({ works, nowYear, windowYears, unavailable, selectedId, onSelectWork }: FrontierListProps) {
  const top = (works ?? []).slice(0, 10)
  return (
    <section className="lens-drawer" aria-label="Frontier works">
      <header className="lens-drawer__header">
        <h2 className="lens-drawer__title">Frontier</h2>
        {nowYear !== null ? (
          <span className="lens-drawer__meta">
            {nowYear - windowYears + 1}–{nowYear}
          </span>
        ) : null}
      </header>
      {unavailable ? <p className="lens-drawer__message">Frontier unavailable.</p> : null}
      {!unavailable && works === null ? <p className="lens-drawer__message">Loading frontier…</p> : null}
      {!unavailable && works !== null && top.length === 0 ? (
        <p className="lens-drawer__message">No recent works in the frontier window.</p>
      ) : null}
      {top.length > 0 ? (
        <ol className="frontier-list">
          {top.map((work) => (
            <li key={work.work_id}>
              <button
                className="frontier-list__item"
                type="button"
                aria-current={selectedId === work.work_id ? 'true' : undefined}
                onClick={() => onSelectWork(work.work_id)}
              >
                <span className="frontier-list__title">{work.title}</span>
                <span className="frontier-list__meta">
                  {work.year ?? 'Year unknown'} · score {formatPercent(work.score)}
                </span>
                <span className="frontier-list__score" aria-hidden="true">
                  <span className="frontier-bars__fill" style={{ width: formatPercent(work.score) }} />
                </span>
              </button>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  )
}

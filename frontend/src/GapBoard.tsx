import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { GapHypothesis, GapStatus, GapUpdateInput } from './api'
import {
  GAP_STATUS_LABELS,
  GAP_TYPE_LABELS,
  GAP_TYPE_ORDER,
  GAP_TYPE_SHORT,
  GAP_VERDICT_LABELS,
  formatMetric,
  formatPercent,
} from './insights'

export interface GapBoardProps {
  gaps: readonly GapHypothesis[] | null
  unavailable: boolean
  activeGapId: string | null
  showRejected: boolean
  onShowRejectedChange: (value: boolean) => void
  onActivateGap: (gapId: string | null) => void
  onSelectWork: (workId: string) => void
  onUpdateGap: (gapId: string, input: GapUpdateInput) => Promise<void>
  onVerifyGap: (gapId: string) => Promise<void>
  onClose: () => void
  workTitle: (workId: string) => string | null
  clusterLabel: (clusterId: string) => string
  conceptLabel: (conceptId: string) => string
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback
}

function formatCheckedAt(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

interface GapCardProps extends Omit<GapBoardProps, 'gaps' | 'unavailable' | 'activeGapId' | 'showRejected' | 'onShowRejectedChange' | 'onClose'> {
  gap: GapHypothesis
  active: boolean
}

function GapCard({
  gap,
  active,
  onActivateGap,
  onSelectWork,
  onUpdateGap,
  onVerifyGap,
  workTitle,
  clusterLabel,
  conceptLabel,
}: GapCardProps) {
  const cardRef = useRef<HTMLElement>(null)
  const [verifying, setVerifying] = useState(false)
  const [verifyError, setVerifyError] = useState<string | null>(null)
  const [updateError, setUpdateError] = useState<string | null>(null)
  const [noteOpen, setNoteOpen] = useState(false)
  const [noteDraft, setNoteDraft] = useState(gap.note ?? '')
  const verification = gap.verification
  const outsideHits = verification?.outside_hits.slice(0, 5) ?? []
  const metrics = Object.entries(gap.metrics)

  useEffect(() => {
    if (active) cardRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [active])

  const update = (input: GapUpdateInput) => {
    setUpdateError(null)
    onUpdateGap(gap.id, input).catch((reason: unknown) => {
      setUpdateError(errorMessage(reason, 'Could not save the change.'))
    })
  }

  const setStatus = (status: GapStatus) => {
    update({ status: gap.status === status ? 'proposed' : status })
  }

  const saveNote = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const note = noteDraft.trim()
    update({ note: note ? note : null })
    setNoteOpen(false)
  }

  const verify = () => {
    setVerifying(true)
    setVerifyError(null)
    onVerifyGap(gap.id)
      .catch((reason: unknown) => setVerifyError(errorMessage(reason, 'The check failed.')))
      .finally(() => setVerifying(false))
  }

  const classes = [
    'gap-card',
    active ? 'gap-card--active' : '',
    gap.stale ? 'gap-card--stale' : '',
    gap.status === 'rejected' ? 'gap-card--rejected' : '',
  ].filter(Boolean).join(' ')

  return (
    <article ref={cardRef} className={classes} aria-labelledby={`gap-${gap.id}-statement`}>
      <header className="gap-card__header">
        <span className="gap-card__tag">{GAP_TYPE_SHORT[gap.type]}</span>
        <span className="gap-card__confidence" title="Detector confidence">
          {formatPercent(gap.confidence)} confidence
        </span>
        {gap.status !== 'proposed' ? (
          <span className={`gap-card__status gap-card__status--${gap.status}`}>{GAP_STATUS_LABELS[gap.status]}</span>
        ) : null}
        {gap.stale ? <span className="gap-card__stale">no longer detected</span> : null}
        <button
          className="gap-card__focus"
          type="button"
          aria-pressed={active}
          onClick={() => onActivateGap(active ? null : gap.id)}
        >
          {active ? 'Shown on map' : 'Show on map'}
        </button>
      </header>
      <p className="gap-card__statement" id={`gap-${gap.id}-statement`}>{gap.statement}</p>

      {gap.evidence.work_ids.length > 0 ? (
        <div className="gap-card__evidence">
          <span className="gap-card__label">Works</span>
          <ul className="gap-card__works">
            {gap.evidence.work_ids.map((workId) => {
              const title = workTitle(workId)
              return (
                <li key={workId}>
                  {title ? (
                    <button className="gap-card__work" type="button" onClick={() => onSelectWork(workId)}>
                      {title}
                    </button>
                  ) : (
                    <span className="gap-card__work gap-card__work--missing">{workId}</span>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      ) : null}
      {gap.evidence.concept_ids.length > 0 || gap.evidence.cluster_ids.length > 0 ? (
        <div className="gap-card__chips">
          {gap.evidence.cluster_ids.map((clusterId) => (
            <span className="gap-card__chip gap-card__chip--cluster" key={`cluster-${clusterId}`}>{clusterLabel(clusterId)}</span>
          ))}
          {gap.evidence.concept_ids.map((conceptId) => (
            <span className="gap-card__chip" key={`concept-${conceptId}`}>{conceptLabel(conceptId)}</span>
          ))}
        </div>
      ) : null}

      {metrics.length > 0 ? (
        <details className="gap-card__metrics">
          <summary>Metrics</summary>
          <dl>
            {metrics.map(([name, value]) => (
              <div key={name}>
                <dt>{name.replaceAll('_', ' ')}</dt>
                <dd>{formatMetric(value)}</dd>
              </div>
            ))}
          </dl>
        </details>
      ) : null}

      <div className="gap-card__verification">
        <div className="gap-card__verify-row">
          <button className="gap-card__button" type="button" onClick={verify} disabled={verifying}>
            {verification ? 'Check again' : 'Check outside the project'}
          </button>
          {verifying ? (
            <span className="gap-card__spinner" role="status">
              <span className="gap-card__spinner-dot" aria-hidden="true" />
              Searching OpenAlex…
            </span>
          ) : null}
          {!verifying && verification ? (
            <span className={`gap-card__verdict gap-card__verdict--${verification.verdict}`}>
              {GAP_VERDICT_LABELS[verification.verdict] ?? verification.verdict}
            </span>
          ) : null}
        </div>
        {verifyError ? <p className="gap-card__error" role="alert">{verifyError}</p> : null}
        {verification?.error ? <p className="gap-card__error">{verification.error}</p> : null}
        {verification ? (
          <>
            <p className="gap-card__verify-meta">
              “{verification.query}” · checked {formatCheckedAt(verification.checked_at)}
            </p>
            {outsideHits.length > 0 ? (
              <ul className="gap-card__hits" aria-label="Works outside the project">
                {outsideHits.map((hit) => (
                  <li key={hit.id}>
                    <span>{hit.title}</span>
                    <span className="gap-card__hit-year">{hit.year ?? 'Year unknown'}</span>
                  </li>
                ))}
              </ul>
            ) : verification.verdict !== 'unknown' ? (
              <p className="gap-card__verify-meta">No outside works combine these terms.</p>
            ) : null}
          </>
        ) : null}
      </div>

      <div className="gap-card__actions">
        <button
          className="gap-card__button gap-card__accept"
          type="button"
          aria-pressed={gap.status === 'accepted'}
          onClick={() => setStatus('accepted')}
        >
          Accept
        </button>
        <button
          className="gap-card__button gap-card__reject"
          type="button"
          aria-pressed={gap.status === 'rejected'}
          onClick={() => setStatus('rejected')}
        >
          Reject
        </button>
        <button
          className="gap-card__button"
          type="button"
          aria-expanded={noteOpen}
          onClick={() => {
            setNoteDraft(gap.note ?? '')
            setNoteOpen((open) => !open)
          }}
        >
          {gap.note ? 'Edit note' : 'Add note'}
        </button>
      </div>
      {gap.note && !noteOpen ? <p className="gap-card__note">{gap.note}</p> : null}
      {noteOpen ? (
        <form className="gap-card__note-form" onSubmit={saveNote}>
          <label className="gap-card__label" htmlFor={`gap-${gap.id}-note`}>Note</label>
          <textarea
            id={`gap-${gap.id}-note`}
            className="gap-card__note-input"
            value={noteDraft}
            rows={3}
            onChange={(event) => setNoteDraft(event.target.value)}
          />
          <div className="gap-card__actions">
            <button className="gap-card__button gap-card__save" type="submit">Save note</button>
            <button className="gap-card__button" type="button" onClick={() => setNoteOpen(false)}>Cancel</button>
          </div>
        </form>
      ) : null}
      {updateError ? <p className="gap-card__error" role="alert">{updateError}</p> : null}
    </article>
  )
}

/** Gap hypotheses grouped by type, with verification and user status controls. */
export function GapBoard(props: GapBoardProps) {
  const { gaps, unavailable, activeGapId, showRejected, onShowRejectedChange, onClose } = props
  const visible = (gaps ?? []).filter((gap) => showRejected || gap.status !== 'rejected')
  const rejectedCount = (gaps ?? []).filter((gap) => gap.status === 'rejected').length

  return (
    <section className="lens-drawer gap-board" aria-label="Gap board">
      <header className="lens-drawer__header">
        <h2 className="lens-drawer__title">Gap board</h2>
        <label className="gap-board__toggle">
          <input
            type="checkbox"
            checked={showRejected}
            onChange={(event) => onShowRejectedChange(event.target.checked)}
          />
          Show rejected{rejectedCount > 0 ? ` (${rejectedCount})` : ''}
        </label>
        <button className="lens-drawer__close" type="button" onClick={onClose} aria-label="Close gap board">
          ×
        </button>
      </header>
      {unavailable ? <p className="lens-drawer__message">Gaps unavailable.</p> : null}
      {!unavailable && gaps === null ? <p className="lens-drawer__message">Loading gaps…</p> : null}
      {!unavailable && gaps !== null && visible.length === 0 ? (
        <p className="lens-drawer__message">No gap hypotheses{rejectedCount > 0 && !showRejected ? ' (rejected ones hidden)' : ''}.</p>
      ) : null}
      {GAP_TYPE_ORDER.map((type) => {
        const group = visible.filter((gap) => gap.type === type)
        if (group.length === 0) return null
        return (
          <section className="gap-board__group" key={type} aria-label={GAP_TYPE_LABELS[type]}>
            <h3 className="gap-board__group-title">
              {GAP_TYPE_LABELS[type]} <span className="gap-board__count">{group.length}</span>
            </h3>
            {group.map((gap) => (
              <GapCard
                key={gap.id}
                {...props}
                gap={gap}
                active={gap.id === activeGapId}
              />
            ))}
          </section>
        )
      })}
    </section>
  )
}

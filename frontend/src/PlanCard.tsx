import { useState } from 'react'
import type { FormEvent } from 'react'
import type { ChatMessage, ResearchPlanArgs } from './api'

export type ResearchPlanDecision = 'approve' | 'edit' | 'reject'

export interface PlanCardProps {
  message: ChatMessage
  busy: boolean
  error: string | null
  retry?: () => void
  onDecision: (
    decision: ResearchPlanDecision,
    args?: ResearchPlanArgs,
    message?: string,
  ) => void
  onOpenRun: (runId: string) => void
}

interface PlanFormValues {
  query: string
  seeds: string
  fromYear: string
  toYear: string
  maxWorks: string
  snowballDepth: string
  acquirePdfs: boolean
}

interface ParsedFormValues {
  query: string | null
  seeds: string[]
  fromYear: number | null
  toYear: number | null
  maxWorks: number
  snowballDepth: number
  acquirePdfs: boolean
}

function stringArg(args: ResearchPlanArgs, key: string): string {
  const value = args[key]
  return typeof value === 'string' ? value : ''
}

function numberArg(args: ResearchPlanArgs, key: string, fallback: number): number {
  const value = args[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function optionalNumberArg(args: ResearchPlanArgs, key: string): number | null {
  const value = args[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function booleanArg(args: ResearchPlanArgs, key: string, fallback: boolean): boolean {
  const value = args[key]
  return typeof value === 'boolean' ? value : fallback
}

function seedsArg(args: ResearchPlanArgs): string[] {
  const value = args.seeds
  if (!Array.isArray(value)) return []
  return value.filter((seed): seed is string => typeof seed === 'string')
}

function formValues(args: ResearchPlanArgs): PlanFormValues {
  const fromYear = optionalNumberArg(args, 'from_year')
  const toYear = optionalNumberArg(args, 'to_year')
  return {
    query: stringArg(args, 'query'),
    seeds: seedsArg(args).join('\n'),
    fromYear: fromYear === null ? '' : String(fromYear),
    toYear: toYear === null ? '' : String(toYear),
    maxWorks: String(numberArg(args, 'max_works', 100)),
    snowballDepth: String(numberArg(args, 'snowball_depth', 2)),
    acquirePdfs: booleanArg(args, 'acquire_pdfs', true),
  }
}

function normalizedQuery(value: string): string | null {
  const trimmed = value.trim()
  return trimmed || null
}

function normalizedSeeds(value: string): string[] {
  return value
    .split('\n')
    .map((seed) => seed.trim())
    .filter(Boolean)
}

function arraysEqual(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

function parseInteger(
  value: string,
  label: string,
  minimum: number,
  maximum?: number,
): { value: number | null; error: string | null } {
  const trimmed = value.trim()
  if (!trimmed) return { value: null, error: null }
  if (!/^\d+$/u.test(trimmed)) return { value: null, error: `${label} must be a whole number.` }
  const parsed = Number(trimmed)
  if (!Number.isSafeInteger(parsed) || parsed < minimum || (maximum !== undefined && parsed > maximum)) {
    const range = maximum === undefined ? `${minimum} or higher` : `between ${minimum} and ${maximum}`
    return { value: null, error: `${label} must be ${range}.` }
  }
  return { value: parsed, error: null }
}

function parseForm(values: PlanFormValues): { parsed: ParsedFormValues | null; error: string | null } {
  const fromYear = parseInteger(values.fromYear, 'From year', 0, 9999)
  if (fromYear.error) return { parsed: null, error: fromYear.error }
  const toYear = parseInteger(values.toYear, 'To year', 0, 9999)
  if (toYear.error) return { parsed: null, error: toYear.error }
  if (fromYear.value !== null && toYear.value !== null && fromYear.value > toYear.value) {
    return { parsed: null, error: 'From year must be before or equal to to year.' }
  }

  const maxWorks = parseInteger(values.maxWorks, 'Max works', 1, 2000)
  if (maxWorks.error) return { parsed: null, error: maxWorks.error }
  const snowballDepth = parseInteger(values.snowballDepth, 'Snowball depth', 0, 2)
  if (snowballDepth.error) return { parsed: null, error: snowballDepth.error }
  if (maxWorks.value === null || snowballDepth.value === null) {
    return { parsed: null, error: 'Max works and snowball depth are required.' }
  }

  const query = normalizedQuery(values.query)
  const seeds = normalizedSeeds(values.seeds)
  if (query === null && seeds.length === 0) {
    return { parsed: null, error: 'Give a query or at least one seed.' }
  }

  return {
    parsed: {
      query,
      seeds,
      fromYear: fromYear.value,
      toYear: toYear.value,
      maxWorks: maxWorks.value,
      snowballDepth: snowballDepth.value,
      acquirePdfs: values.acquirePdfs,
    },
    error: null,
  }
}

function changedArgs(original: ResearchPlanArgs, next: ParsedFormValues): ResearchPlanArgs {
  const changed: ResearchPlanArgs = {}
  const originalQuery = normalizedQuery(stringArg(original, 'query'))
  if (originalQuery !== next.query) changed.query = next.query

  const originalSeeds = normalizedSeeds(seedsArg(original).join('\n'))
  if (!arraysEqual(originalSeeds, next.seeds)) changed.seeds = next.seeds

  const originalFromYear = optionalNumberArg(original, 'from_year')
  if (originalFromYear !== next.fromYear) changed.from_year = next.fromYear
  const originalToYear = optionalNumberArg(original, 'to_year')
  if (originalToYear !== next.toYear) changed.to_year = next.toYear

  const originalMaxWorks = numberArg(original, 'max_works', 100)
  if (originalMaxWorks !== next.maxWorks) changed.max_works = next.maxWorks
  const originalSnowballDepth = numberArg(original, 'snowball_depth', 1)
  if (originalSnowballDepth !== next.snowballDepth) changed.snowball_depth = next.snowballDepth
  const originalAcquirePdfs = booleanArg(original, 'acquire_pdfs', true)
  if (originalAcquirePdfs !== next.acquirePdfs) changed.acquire_pdfs = next.acquirePdfs
  return changed
}

function expiredMessage(value: string | null | undefined): boolean {
  const normalized = value?.toLowerCase() ?? ''
  return normalized.includes('expired') || normalized.includes('410')
}

function displayYearRange(args: ResearchPlanArgs): string {
  const fromYear = optionalNumberArg(args, 'from_year')
  const toYear = optionalNumberArg(args, 'to_year')
  if (fromYear === null && toYear === null) return 'Any year'
  if (fromYear === null) return `Through ${toYear}`
  if (toYear === null) return `From ${fromYear}`
  return `${fromYear}–${toYear}`
}

function planSummary(args: ResearchPlanArgs): string {
  const query = stringArg(args, 'query').trim()
  const seeds = seedsArg(args)
  const subject = query || (seeds.length > 0
    ? `${seeds.length} seed ${seeds.length === 1 ? 'paper' : 'papers'}`
    : 'No query or seeds')
  return `${subject} · ${displayYearRange(args)} · up to ${numberArg(args, 'max_works', 100)} works`
}

function PlanFacts({ args }: { args: ResearchPlanArgs }) {
  const seeds = seedsArg(args)
  const query = stringArg(args, 'query').trim()
  return (
    <dl className="plan-card__facts">
      <div className="plan-card__fact plan-card__fact--wide">
        <dt>Query</dt>
        <dd>{query || 'None'}</dd>
      </div>
      <div className="plan-card__fact plan-card__fact--wide">
        <dt>Seeds</dt>
        <dd>
          {seeds.length > 0 ? (
            <ul className="plan-card__seeds">
              {seeds.map((seed, index) => <li key={`${seed}-${index}`}>{seed}</li>)}
            </ul>
          ) : 'None'}
        </dd>
      </div>
      <div className="plan-card__fact">
        <dt>Years</dt>
        <dd>{displayYearRange(args)}</dd>
      </div>
      <div className="plan-card__fact">
        <dt>Max works</dt>
        <dd>{numberArg(args, 'max_works', 100)}</dd>
      </div>
      <div className="plan-card__fact">
        <dt>Snowball depth</dt>
        <dd>{numberArg(args, 'snowball_depth', 1)}</dd>
      </div>
      <div className="plan-card__fact">
        <dt>PDFs</dt>
        <dd>{booleanArg(args, 'acquire_pdfs', true) ? 'On' : 'Off'}</dd>
      </div>
    </dl>
  )
}

function PlanEditForm({
  values,
  onChange,
  onSubmit,
  onCancel,
  error,
  busy,
  idPrefix,
}: {
  values: PlanFormValues
  onChange: (values: PlanFormValues) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  onCancel: () => void
  error: string | null
  busy: boolean
  idPrefix: string
}) {
  function update<K extends keyof PlanFormValues>(key: K, value: PlanFormValues[K]) {
    onChange({ ...values, [key]: value })
  }
  return (
    <form className="plan-card__edit" onSubmit={onSubmit}>
      <div className="plan-card__edit-grid">
        <label className="plan-card__field plan-card__field--wide" htmlFor={`${idPrefix}-query`}>
          Query
          <input
            id={`${idPrefix}-query`}
            value={values.query}
            onChange={(event) => update('query', event.target.value)}
          />
        </label>
        <label className="plan-card__field plan-card__field--wide" htmlFor={`${idPrefix}-seeds`}>
          Seeds <span>(one per line)</span>
          <textarea
            id={`${idPrefix}-seeds`}
            rows={3}
            value={values.seeds}
            onChange={(event) => update('seeds', event.target.value)}
          />
        </label>
        <label className="plan-card__field" htmlFor={`${idPrefix}-from-year`}>
          From year
          <input
            id={`${idPrefix}-from-year`}
            inputMode="numeric"
            type="number"
            min={0}
            max={9999}
            value={values.fromYear}
            onChange={(event) => update('fromYear', event.target.value)}
          />
        </label>
        <label className="plan-card__field" htmlFor={`${idPrefix}-to-year`}>
          To year
          <input
            id={`${idPrefix}-to-year`}
            inputMode="numeric"
            type="number"
            min={0}
            max={9999}
            value={values.toYear}
            onChange={(event) => update('toYear', event.target.value)}
          />
        </label>
        <label className="plan-card__field" htmlFor={`${idPrefix}-max-works`}>
          Max works
          <input
            id={`${idPrefix}-max-works`}
            inputMode="numeric"
            type="number"
            min={1}
            max={2000}
            value={values.maxWorks}
            onChange={(event) => update('maxWorks', event.target.value)}
          />
        </label>
        <label className="plan-card__field" htmlFor={`${idPrefix}-snowball-depth`}>
          Snowball depth
          <input
            id={`${idPrefix}-snowball-depth`}
            inputMode="numeric"
            type="number"
            min={0}
            max={2}
            value={values.snowballDepth}
            onChange={(event) => update('snowballDepth', event.target.value)}
          />
        </label>
        <label className="plan-card__pdf-toggle" htmlFor={`${idPrefix}-acquire-pdfs`}>
          <span>Acquire PDFs</span>
          <input
            id={`${idPrefix}-acquire-pdfs`}
            type="checkbox"
            checked={values.acquirePdfs}
            onChange={(event) => update('acquirePdfs', event.target.checked)}
          />
        </label>
      </div>
      {error ? <p className="plan-card__form-error" role="alert">{error}</p> : null}
      <div className="plan-card__actions">
        <button className="plan-card__secondary" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className="plan-card__primary" type="submit" disabled={busy}>
          {busy ? 'Saving…' : 'Submit changes'}
        </button>
      </div>
    </form>
  )
}

export function PlanCard({ message, busy, error, retry, onDecision, onOpenRun }: PlanCardProps) {
  const pendingArgs = message.pending_plan ?? {}
  const displayArgs = message.final_args ?? pendingArgs
  const expired = expiredMessage(message.error) || expiredMessage(error)
  const resolvedStatus = expired ? 'expired' : message.plan_status && message.plan_status !== 'pending'
    ? message.plan_status
    : null
  const pending = resolvedStatus === null
  const [editing, setEditing] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [rejectMessage, setRejectMessage] = useState('')
  const [form, setForm] = useState(() => formValues(pendingArgs))
  const [formError, setFormError] = useState<string | null>(null)

  const headingId = `plan-card-heading-${message.id}`
  const fieldPrefix = `plan-card-${message.id}`
  const visibleError = error?.trim() || message.error?.trim() || null

  const approve = () => {
    if (!busy) onDecision('approve')
  }

  const submitEdit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const result = parseForm(form)
    if (result.error || result.parsed === null) {
      setFormError(result.error ?? 'Review the research plan values.')
      return
    }
    setFormError(null)
    const changed = changedArgs(pendingArgs, result.parsed)
    // An unchanged form is an approval of the plan as proposed.
    if (Object.keys(changed).length === 0) onDecision('approve')
    else onDecision('edit', changed)
  }

  const reject = () => {
    if (!busy) onDecision('reject', undefined, rejectMessage.trim() || undefined)
  }

  return (
    <section className={`plan-card${pending ? ' plan-card--pending' : ' plan-card--resolved'}`} aria-labelledby={headingId}>
      <div className="plan-card__header">
        <div>
          <p className="plan-card__eyebrow">Research plan</p>
          <h3 className="plan-card__heading" id={headingId}>
            {pending ? 'Review this harvest' : `Plan ${resolvedStatus}`}
          </h3>
        </div>
        {!pending ? <span className={`plan-card__status plan-card__status--${resolvedStatus}`}>{resolvedStatus}</span> : null}
      </div>

      {pending && editing ? (
        <PlanEditForm
          values={form}
          onChange={(next) => {
            setForm(next)
            if (formError) setFormError(null)
          }}
          onSubmit={submitEdit}
          onCancel={() => {
            setEditing(false)
            setFormError(null)
          }}
          error={formError}
          busy={busy}
          idPrefix={fieldPrefix}
        />
      ) : pending ? (
        <>
          <PlanFacts args={displayArgs} />
          {message.content.trim() ? (
            <div className="plan-card__description">
              <h4>Description</h4>
              <p>{message.content}</p>
            </div>
          ) : null}
          {typeof displayArgs.rationale === 'string' && displayArgs.rationale.trim() ? (
            <div className="plan-card__rationale">
              <h4>Rationale</h4>
              <p>{displayArgs.rationale}</p>
            </div>
          ) : null}
        </>
      ) : (
        <p className="plan-card__summary">{planSummary(displayArgs)}</p>
      )}

      {visibleError ? (
        <div className="plan-card__error" role="alert">
          <span>{expired ? 'This plan expired after a server restart — ask again' : visibleError}</span>
          {!expired && retry ? (
            <button className="plan-card__retry" type="button" onClick={retry} disabled={busy}>Retry</button>
          ) : null}
        </div>
      ) : null}

      {pending && !editing && !rejecting ? (
        <div className="plan-card__actions">
          <button className="plan-card__primary" type="button" onClick={approve} disabled={busy}>
            {busy ? 'Working…' : 'Approve'}
          </button>
          <button className="plan-card__secondary" type="button" onClick={() => setEditing(true)} disabled={busy}>Edit</button>
          <button className="plan-card__danger" type="button" onClick={() => setRejecting(true)} disabled={busy}>Reject</button>
        </div>
      ) : null}

      {pending && !editing && rejecting ? (
        <div className="plan-card__reject">
          <label className="plan-card__field plan-card__field--wide" htmlFor={`${fieldPrefix}-reject-message`}>
            Message <span>(optional)</span>
            <textarea
              id={`${fieldPrefix}-reject-message`}
              rows={2}
              value={rejectMessage}
              onChange={(event) => setRejectMessage(event.target.value)}
              placeholder="Tell the agent why you are rejecting this plan."
              disabled={busy}
            />
          </label>
          <div className="plan-card__actions">
            <button className="plan-card__secondary" type="button" onClick={() => setRejecting(false)} disabled={busy}>Cancel</button>
            <button className="plan-card__danger" type="button" onClick={reject} disabled={busy}>
              {busy ? 'Rejecting…' : 'Reject plan'}
            </button>
          </div>
        </div>
      ) : null}

      {!pending && message.run_id ? (
        <button className="plan-card__run-link" type="button" onClick={() => onOpenRun(message.run_id as string)}>
          View harvest run
        </button>
      ) : null}
    </section>
  )
}

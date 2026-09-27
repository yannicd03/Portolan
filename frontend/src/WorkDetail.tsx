import type {
  FrontierWork,
  GapHypothesis,
  ProjectAnalysis,
  WorkDetail as WorkDetailData,
  WorkRole,
  WorkSummary,
} from './api'
import { api } from './api'
import { clusterColor } from './CitationMap'
import { FrontierBars } from './FrontierPanel'
import { GAP_TYPE_SHORT, formatPercent } from './insights'
import { docHref } from './route'

interface WorkDetailProps {
  detail: WorkDetailData
  analysis?: ProjectAnalysis | null
  projectId: string
  onSelectWork: (workId: string) => void
  /** The work's frontier entry when it falls inside the frontier window. */
  frontierWork?: FrontierWork | null
  /** Gaps naming this work as evidence. */
  gaps?: readonly GapHypothesis[]
  onSelectGap?: (gapId: string) => void
}

const ROLE_TITLES: Record<WorkRole, string> = {
  foundational: 'Frequently cited by other works in this project.',
  bridge: 'Connects otherwise separate parts of the citation network.',
  hub: 'Has many citation links within this project.',
  emerging: 'A recent work gaining citations within this project.',
  peripheral: 'Has few citation links within this project.',
}

function externalLink(value: string, baseUrl: string): string {
  if (/^https?:\/\//i.test(value)) return value
  return `${baseUrl}${value.replace(/^(?:doi:|arxiv:)/i, '')}`
}

function SummaryList({
  heading,
  items,
  onSelectWork,
}: {
  heading: string
  items: WorkSummary[]
  onSelectWork: (workId: string) => void
}) {
  return (
    <section className="work-detail__section">
      <h3>{heading}</h3>
      {items.length > 0 ? (
        <ul className="work-detail__list">
          {items.map((item) => (
            <li key={item.id}>
              <button
                className="work-detail__list-button"
                type="button"
                onClick={() => onSelectWork(item.id)}
              >
                <span>{item.title}</span>
                <span className="work-detail__list-meta">
                  {item.year ?? 'Year unknown'} · {item.cited_by_count} citations
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="work-detail__empty">None recorded.</p>
      )}
    </section>
  )
}

export function WorkDetail({
  detail,
  analysis,
  projectId,
  onSelectWork,
  frontierWork = null,
  gaps = [],
  onSelectGap,
}: WorkDetailProps) {
  const work = detail.work
  const workAnalysis = analysis?.works[work.id]
  const cluster = analysis?.clusters.find((item) => item.id === workAnalysis?.cluster)
  const documentSha = work.document_sha256
  const doi = work.doi
  const arxivId = work.arxiv_id

  return (
    <article className="work-detail">
      <header className="work-detail__header">
        <p className="work-detail__eyebrow">Work</p>
        <h2 className="work-detail__title">{work.title}</h2>
        <p className="work-detail__subtitle">
          {work.year ?? 'Year unknown'}
          {work.venue ? ` · ${work.venue}` : ''}
          {` · ${work.cited_by_count} citations`}
        </p>
      </header>

      {workAnalysis ? (
        <section className="work-detail__section" aria-label="Network analysis">
          <h3>Network analysis</h3>
          <div className="work-detail__chips">
            <span className="work-detail__chip work-detail__cluster">
              <span
                className="work-detail__cluster-swatch"
                style={{ backgroundColor: cluster ? clusterColor(cluster.id, analysis?.clusters ?? []) : 'var(--muted-node)' }}
                aria-hidden="true"
              />
              {cluster?.label ?? 'Unclustered'}
            </span>
            {workAnalysis.roles.map((role) => (
              <span className="work-detail__chip" key={role} title={ROLE_TITLES[role]}>{role}</span>
            ))}
          </div>
          <p className="work-detail__local-citations">
            Local citations in: {workAnalysis.local_in} · out: {workAnalysis.local_out}
          </p>
        </section>
      ) : null}

      {frontierWork ? (
        <section className="work-detail__section" aria-label="Frontier">
          <h3>Frontier</h3>
          <FrontierBars work={frontierWork} />
        </section>
      ) : null}

      {gaps.length > 0 ? (
        <section className="work-detail__section" aria-label="Gap evidence">
          <h3>Evidence for gaps</h3>
          <ul className="work-detail__list">
            {gaps.map((gap) => (
              <li key={gap.id}>
                <button
                  className="work-detail__list-button"
                  type="button"
                  onClick={() => onSelectGap?.(gap.id)}
                  disabled={!onSelectGap}
                >
                  <span>{gap.statement}</span>
                  <span className="work-detail__list-meta">
                    {GAP_TYPE_SHORT[gap.type]} · {formatPercent(gap.confidence)} confidence
                    {gap.status !== 'proposed' ? ` · ${gap.status}` : ''}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <dl className="work-detail__facts">
        {work.doi ? (
          <>
            <dt>DOI</dt>
            <dd>{work.doi}</dd>
          </>
        ) : null}
        {work.arxiv_id ? (
          <>
            <dt>arXiv</dt>
            <dd>{work.arxiv_id}</dd>
          </>
        ) : null}
        {work.openalex_id ? (
          <>
            <dt>OpenAlex</dt>
            <dd>{work.openalex_id}</dd>
          </>
        ) : null}
      </dl>

      {detail.authors.length > 0 ? (
        <section className="work-detail__section">
          <h3>Authors</h3>
          <div className="work-detail__chips">
            {detail.authors.map(({ author }) => (
              <span className="work-detail__chip" key={author.id}>
                {author.name}
              </span>
            ))}
          </div>
        </section>
      ) : null}

      {detail.concepts.length > 0 ? (
        <section className="work-detail__section">
          <h3>Concepts</h3>
          <div className="work-detail__chips">
            {detail.concepts.map(({ concept }) => (
              <span className="work-detail__chip" key={concept.id}>
                {concept.label}
              </span>
            ))}
          </div>
        </section>
      ) : null}

      {work.abstract ? (
        <section className="work-detail__section">
          <h3>Abstract</h3>
          <p className="work-detail__abstract">{work.abstract}</p>
        </section>
      ) : null}

      {doi || arxivId || documentSha ? (
        <section className="work-detail__links" aria-label="Work links">
          {doi ? (
            <a
              className="work-detail__link"
              href={externalLink(doi, 'https://doi.org/')}
              target="_blank"
              rel="noreferrer"
            >
              DOI
            </a>
          ) : null}
          {arxivId ? (
            <a
              className="work-detail__link"
              href={externalLink(arxivId, 'https://arxiv.org/abs/')}
              target="_blank"
              rel="noreferrer"
            >
              arXiv
            </a>
          ) : null}
          {documentSha ? (
            <>
              <a className="work-detail__link" href={docHref(projectId, documentSha, { page: 1, workId: work.id })}>
                Open PDF
              </a>
              <a
                className="work-detail__link"
                href={api.documentPdfUrl(documentSha)}
                target="_blank"
                rel="noreferrer"
                aria-label="Open original PDF in a new tab"
                title="Open original PDF in a new tab"
              >
                ↗
              </a>
            </>
          ) : null}
        </section>
      ) : null}

      <SummaryList heading="Cites" items={detail.cites} onSelectWork={onSelectWork} />
      <SummaryList heading="Cited by" items={detail.cited_by} onSelectWork={onSelectWork} />
    </article>
  )
}

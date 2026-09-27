import { useId, useRef, useState, type ReactNode } from 'react'
import type { VerifiedAnswer, VerifiedCitation } from './api'
import { docHref } from './route'

export interface AnswerContentProps {
  projectId: string
  answer: VerifiedAnswer
  onOpenWork: (workId: string) => void
}

interface LocateHit {
  page: number
  offset: number
  snippet: string
}

interface LocateResponse {
  found: boolean
  hits: LocateHit[]
}

type LocateResult = LocateResponse | null

const locateCache = new Map<string, Promise<LocateResult>>()

function citationIsPaper(citation: VerifiedCitation): boolean {
  return citation.verified && citation.source === 'paper' && Boolean(citation.sha256)
}

function citationIsAbstract(citation: VerifiedCitation): boolean {
  return citation.verified && citation.source === 'abstract'
}

function citationPageLabel(citation: VerifiedCitation): string {
  return citation.source === 'abstract' || citation.page <= 0 ? 'Abstract' : `p. ${citation.page}`
}

function unresolvedCitation(marker: number): VerifiedCitation {
  return {
    marker,
    work_id: '',
    title: 'Citation unavailable',
    year: null,
    quote: '',
    page: 0,
    sha256: null,
    offset: null,
    verified: false,
    source: null,
  }
}

function locateCacheKey(citation: VerifiedCitation): string | null {
  if (!citationIsPaper(citation) || !citation.sha256) return null
  return `${citation.sha256}:${citation.page}:${citation.quote}`
}

function locateCitation(citation: VerifiedCitation): Promise<LocateResult> {
  const key = locateCacheKey(citation)
  if (!key || !citation.sha256 || citation.quote.trim().length < 3) return Promise.resolve(null)

  const cached = locateCache.get(key)
  if (cached) return cached

  const query = new URLSearchParams({ q: citation.quote, page: String(citation.page) })
  const request = fetch(
    `/api/documents/${encodeURIComponent(citation.sha256)}/locate?${query.toString()}`,
    { headers: { Accept: 'application/json' } },
  )
    .then(async (response): Promise<LocateResult> => {
      if (!response.ok) return null
      return (await response.json()) as LocateResponse
    })
    .catch(() => null)

  locateCache.set(key, request)
  return request
}

function CitationPreview({ citation, locate }: { citation: VerifiedCitation; locate: LocateResult | 'loading' }) {
  const hit = locate !== 'loading' ? locate?.hits[0] : undefined

  return (
    <span className="ask-citation-preview" role="tooltip">
      <strong className="ask-citation-preview__title">{citation.title || 'Untitled work'}</strong>
      <span className="ask-citation-preview__meta">
        {citation.year ?? 'Year unknown'} · {citationPageLabel(citation)}
      </span>
      {citation.quote ? <span className="ask-citation-preview__quote">“{citation.quote}”</span> : null}
      {locate === 'loading' ? <span className="ask-citation-preview__snippet">Finding this passage…</span> : null}
      {hit ? <span className="ask-citation-preview__snippet">{hit.snippet}</span> : null}
      {locate !== 'loading' && locate && !hit ? (
        <span className="ask-citation-preview__snippet">The quoted passage could not be located.</span>
      ) : null}
    </span>
  )
}

function CitationChip({
  projectId,
  citation,
  onOpenWork,
}: {
  projectId: string
  citation: VerifiedCitation
  onOpenWork: (workId: string) => void
}) {
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const [locate, setLocate] = useState<LocateResult | 'loading'>(null)
  const locateRequestKey = useRef<string | null>(null)
  const locateRequestToken = useRef(0)
  const citationId = useId()
  const showPreview = hovered || focused
  const paperCitation = citationIsPaper(citation)
  const abstractCitation = citationIsAbstract(citation)
  const navigable = paperCitation || abstractCitation
  const previewId = `citation-preview-${citationId}`

  const requestLocate = () => {
    const key = locateCacheKey(citation)
    if (!paperCitation || !key || locateRequestKey.current === key) return
    locateRequestKey.current = key
    const token = locateRequestToken.current + 1
    locateRequestToken.current = token
    setLocate('loading')
    void locateCitation(citation).then((result) => {
      if (locateRequestToken.current === token) setLocate(result)
    })
  }

  const openWork = () => {
    if (abstractCitation) onOpenWork(citation.work_id)
  }

  const className = [
    'ask-citation-chip',
    citation.verified ? 'ask-citation-chip--verified' : 'ask-citation-chip--unverified',
    navigable ? 'ask-citation-chip--link' : 'ask-citation-chip--static',
  ].join(' ')
  const label = citation.verified
    ? `Citation ${citation.marker}: ${citation.title || 'Untitled work'}`
    : `Citation ${citation.marker}: quote not found in the paper`
  const chipProps = {
    className,
    onFocus: () => {
      setFocused(true)
      requestLocate()
    },
    onBlur: () => setFocused(false),
    'aria-describedby': showPreview ? previewId : undefined,
    'aria-label': label,
    title: citation.verified ? undefined : 'quote not found in the paper',
  }

  return (
    <span
      className="ask-citation"
      onMouseEnter={() => {
        setHovered(true)
        requestLocate()
      }}
      onMouseLeave={() => setHovered(false)}
    >
      {paperCitation && citation.sha256 ? (
        <a
          {...chipProps}
          href={docHref(projectId, citation.sha256, {
            page: citation.page,
            quote: citation.quote,
            workId: citation.work_id,
          })}
        >
          [{citation.marker}]
        </a>
      ) : (
        <button
          {...chipProps}
          type="button"
          onClick={abstractCitation ? openWork : undefined}
          aria-disabled={!abstractCitation}
        >
          [{citation.marker}]
        </button>
      )}
      {showPreview ? (
        <span id={previewId}>
          <CitationPreview citation={citation} locate={paperCitation ? locate : null} />
        </span>
      ) : null}
    </span>
  )
}

interface InlineOptions {
  projectId: string
  citations: Map<number, VerifiedCitation>
  onOpenWork: (workId: string) => void
}

function renderInline(value: string, options: InlineOptions, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const tokenPattern = /(`[^`\n]*`|\*\*[^*\n]+\*\*|__[^_\n]+__|\[(\d+)\]|\*[^*\n]+\*|_[^_\n]+_)/g
  let cursor = 0
  let tokenIndex = 0

  for (const match of value.matchAll(tokenPattern)) {
    const token = match[0]
    const start = match.index ?? cursor
    if (start > cursor) nodes.push(value.slice(cursor, start))

    const key = `${keyPrefix}-${tokenIndex}`
    tokenIndex += 1
    if (token.startsWith('`')) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>)
    } else if (token.startsWith('**') || token.startsWith('__')) {
      nodes.push(
        <strong key={key}>
          {renderInline(token.slice(2, -2), options, `${key}-strong`)}
        </strong>,
      )
    } else if (token.startsWith('*') || token.startsWith('_')) {
      nodes.push(
        <em key={key}>
          {renderInline(token.slice(1, -1), options, `${key}-em`)}
        </em>,
      )
    } else {
      const marker = Number(match[2])
      const citation = options.citations.get(marker) ?? unresolvedCitation(marker)
      nodes.push(
        <CitationChip
          key={key}
          projectId={options.projectId}
          citation={citation}
          onOpenWork={options.onOpenWork}
        />,
      )
    }
    cursor = start + token.length
  }

  if (cursor < value.length) nodes.push(value.slice(cursor))
  return nodes
}

type Block =
  | { kind: 'paragraph'; lines: string[] }
  | { kind: 'ul' | 'ol'; items: string[] }

function parseBlocks(markdown: string): Block[] {
  const blocks: Block[] = []
  let paragraph: string[] = []
  let list: Extract<Block, { kind: 'ul' | 'ol' }> | null = null

  const flushParagraph = () => {
    if (paragraph.length > 0) blocks.push({ kind: 'paragraph', lines: paragraph })
    paragraph = []
  }
  const flushList = () => {
    if (list) blocks.push(list)
    list = null
  }

  for (const line of markdown.replace(/\r\n?/g, '\n').split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) {
      flushParagraph()
      flushList()
      continue
    }

    const unordered = trimmed.match(/^[-*+]\s+(.+)$/)
    const ordered = trimmed.match(/^\d+[.)]\s+(.+)$/)
    if (unordered || ordered) {
      flushParagraph()
      const kind = unordered ? 'ul' : 'ol'
      const item = (unordered ?? ordered)?.[1] ?? ''
      if (!list || list.kind !== kind) {
        flushList()
        list = { kind, items: [] }
      }
      list.items.push(item)
      continue
    }

    flushList()
    paragraph.push(trimmed)
  }

  flushParagraph()
  flushList()
  return blocks
}

function AnswerMarkdown({
  answer,
  projectId,
  onOpenWork,
}: AnswerContentProps) {
  const citations = new Map(answer.citations.map((citation) => [citation.marker, citation]))
  const options: InlineOptions = { projectId, citations, onOpenWork }

  return (
    <div className="ask-answer-markdown">
      {parseBlocks(answer.answer_markdown).map((block, blockIndex) => {
        if (block.kind === 'paragraph') {
          return (
            <p key={`paragraph-${blockIndex}`}>
              {renderInline(block.lines.join(' '), options, `paragraph-${blockIndex}`)}
            </p>
          )
        }
        const List = block.kind === 'ul' ? 'ul' : 'ol'
        return (
          <List key={`${block.kind}-${blockIndex}`}>
            {block.items.map((item, itemIndex) => (
              <li key={`${block.kind}-${blockIndex}-${itemIndex}`}>
                {renderInline(item, options, `${block.kind}-${blockIndex}-${itemIndex}`)}
              </li>
            ))}
          </List>
        )
      })}
    </div>
  )
}

function SourceList({ answer }: { answer: VerifiedAnswer }) {
  if (answer.citations.length === 0) return null

  return (
    <section className="ask-sources" aria-label="Sources">
      <h3>Sources</h3>
      <ol>
        {answer.citations.map((citation) => (
          <li key={`${citation.marker}-${citation.work_id}`}>
            <span className="ask-source-marker">[{citation.marker}]</span>{' '}
            <span>{citation.title || 'Untitled work'}</span>
            <span className="ask-source-meta">
              {' '}
              · {citation.year ?? 'Year unknown'} · {citationPageLabel(citation)}
            </span>
          </li>
        ))}
      </ol>
    </section>
  )
}

function UnsupportedClaims({ claims }: { claims: string[] }) {
  if (claims.length === 0) return null

  return (
    <section className="ask-unsupported" aria-label="Not supported by the project's papers">
      <h3>Not supported by the project's papers</h3>
      <ul>
        {claims.map((claim, index) => <li key={`${index}-${claim}`}>{claim}</li>)}
      </ul>
    </section>
  )
}

export function AnswerContent({ projectId, answer, onOpenWork }: AnswerContentProps) {
  return (
    <div className="ask-answer">
      <AnswerMarkdown answer={answer} projectId={projectId} onOpenWork={onOpenWork} />
      <SourceList answer={answer} />
      <UnsupportedClaims claims={answer.unsupported} />
    </div>
  )
}

export default AnswerContent

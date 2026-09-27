import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { AnswerContent } from './AnswerContent'
import { api } from './api'
import { PlanCard } from './PlanCard'
import type {
  ChatActivityEvent,
  ChatMessage,
  ChatStatus,
  ChatThread,
  ChatThreadSummary,
  ResearchPlanArgs,
  ResearchPlanPayload,
  VerifiedAnswer,
} from './api'
import {
  createSseParserState,
  finishSseParser,
  parseSseChunk,
  type SseEvent,
  type SseParserState,
} from './sse'

export interface AskThreadProps {
  projectId: string
  worksCount: number | null
  onHighlightedWorkIds: (workIds: string[]) => void
  onOpenWork: (workId: string) => void
  onOpenRun: (runId: string) => void
  onRunFinished: () => void
}

interface ChatError extends Error {
  status?: number
}

interface ActivityTrailProps {
  events: ChatActivityEvent[]
  live?: boolean
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error && reason.message ? reason.message : fallback
}

function responseDetail(value: unknown, fallback: string): string {
  if (isRecord(value) && typeof value.detail === 'string' && value.detail.trim()) {
    return value.detail
  }
  if (typeof value === 'string' && value.trim()) return value
  return fallback
}

function threadSummary(thread: ChatThread): ChatThreadSummary {
  return {
    id: thread.id,
    project_id: thread.project_id,
    title: thread.title,
    created_at: thread.created_at,
    updated_at: thread.updated_at,
    message_count: thread.messages.length,
  }
}

function threadLabel(thread: ChatThreadSummary): string {
  return thread.title.trim() || 'New conversation'
}

function parseJson(data: string): unknown {
  try {
    return JSON.parse(data) as unknown
  } catch {
    return null
  }
}

function verifiedAnswer(value: unknown): VerifiedAnswer | null {
  if (!isRecord(value) || typeof value.answer_markdown !== 'string') return null
  if (!Array.isArray(value.citations) || !Array.isArray(value.unsupported)) return null
  return value as unknown as VerifiedAnswer
}

function researchAnswer(value: unknown): string | null {
  return isRecord(value) && value.mode === 'research' && typeof value.answer_markdown === 'string'
    ? value.answer_markdown
    : null
}

function researchPlan(value: unknown): ResearchPlanPayload | null {
  if (!isRecord(value) || typeof value.tool_call_id !== 'string' || !isRecord(value.args)
    || typeof value.description !== 'string') return null
  return value as unknown as ResearchPlanPayload
}

function markdownAnswer(markdown: string): VerifiedAnswer {
  return { answer_markdown: markdown, citations: [], unsupported: [], model: null, tool_calls: 0 }
}

function eventText(value: unknown): string | null {
  if (!isRecord(value) || typeof value.text !== 'string') return null
  return value.text
}

function activityEvents(message: ChatMessage): ChatActivityEvent[] {
  return Array.isArray(message.events) ? message.events : []
}

function unavailableReason(status: ChatStatus | null): string {
  const reason = status?.reason?.trim()
  if (!reason || reason.includes('OPENROUTER_API_KEY')) {
    return 'Ask mode needs OPENROUTER_API_KEY in .env.'
  }
  return reason
}

function ActivityTrail({ events, live = false }: ActivityTrailProps) {
  const [expanded, setExpanded] = useState(false)
  const isExpanded = live || expanded

  if (events.length === 0 && !live) return null

  return (
    <div className="ask-activity">
      <button
        className="ask-activity-toggle"
        type="button"
        aria-expanded={isExpanded}
        onClick={() => setExpanded((current) => !(live || current))}
      >
        {live ? 'Working…' : `${events.length} ${events.length === 1 ? 'step' : 'steps'}`}
        <span aria-hidden="true">{isExpanded ? '▴' : '▾'}</span>
      </button>
      {isExpanded ? (
        <ol className="ask-activity-list" aria-label="Chat activity">
          {events.map((event, index) => <li key={`${event.text}-${index}`}>{event.text}</li>)}
          {live && events.length === 0 ? <li>Preparing an answer…</li> : null}
        </ol>
      ) : null}
    </div>
  )
}

function ChatMessageView({
  message,
  projectId,
  onOpenWork,
  onHoverAnswer,
  onSelectAnswer,
  onDecision,
  planBusy,
  planError,
  onRetryPlan,
  onOpenRun,
}: {
  message: ChatMessage
  projectId: string
  onOpenWork: (workId: string) => void
  onHoverAnswer: (messageId: string | null) => void
  onSelectAnswer: (messageId: string) => void
  onDecision: (decision: 'approve' | 'edit' | 'reject', args?: ResearchPlanArgs, message?: string) => void
  planBusy: boolean
  planError: string | null
  onRetryPlan?: () => void
  onOpenRun: (runId: string) => void
}) {
  if (message.role === 'user') {
    return (
      <article className="ask-message ask-message-user">
        <p className="ask-message-content">{message.content}</p>
      </article>
    )
  }

  const answer = message.answer
  const research = message.mode === 'research'
  return (
    <article
      className={`ask-message ask-message-assistant${message.error ? ' ask-message-error' : ''}`}
      tabIndex={answer ? 0 : undefined}
      onMouseEnter={answer ? () => onHoverAnswer(message.id) : undefined}
      onMouseLeave={answer ? (event) => {
        if (!event.currentTarget.contains(document.activeElement)) onHoverAnswer(null)
      } : undefined}
      onFocus={answer ? () => onHoverAnswer(message.id) : undefined}
      onBlur={answer ? (event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) onHoverAnswer(null)
      } : undefined}
      onClick={answer ? () => onSelectAnswer(message.id) : undefined}
      aria-label={answer ? 'Assistant answer' : 'Assistant message'}
    >
      <span className="ask-message-meta">{research ? 'Research' : 'Ask'}</span>
      <ActivityTrail events={activityEvents(message)} />
      <div className="ask-message-content">
        {message.pending_plan ? (
          <PlanCard message={message} busy={planBusy} error={planError} retry={onRetryPlan} onDecision={onDecision} onOpenRun={onOpenRun} />
        ) : answer ? (
          <AnswerContent projectId={projectId} answer={answer} onOpenWork={onOpenWork} />
        ) : research && !message.error ? (
          <AnswerContent projectId={projectId} answer={markdownAnswer(message.content)} onOpenWork={onOpenWork} />
        ) : (
          <p>{message.content}</p>
        )}
      </div>
    </article>
  )
}

async function readSseResponse(
  response: Response,
  handleEvent: (event: SseEvent) => void,
): Promise<void> {
  if (!response.ok) throw await responseError(response)
  if (!response.body) throw new Error('Chat mode returned no stream.')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let parser: SseParserState = createSseParserState()
  while (true) {
    const result = await reader.read()
    if (result.done) break
    const parsed = parseSseChunk(parser, decoder.decode(result.value, { stream: true }))
    parser = parsed.state
    parsed.events.forEach(handleEvent)
  }
  const finalChunk = decoder.decode()
  if (finalChunk) {
    const parsed = parseSseChunk(parser, finalChunk)
    parser = parsed.state
    parsed.events.forEach(handleEvent)
  }
  finishSseParser(parser).events.forEach(handleEvent)
}

async function responseError(response: Response): Promise<ChatError> {
  let detail: unknown = null
  try {
    detail = await response.json()
  } catch {
    try {
      detail = await response.text()
    } catch {
      detail = null
    }
  }
  const error = new Error(responseDetail(detail, response.statusText || 'Ask request failed.')) as ChatError
  error.status = response.status
  return error
}

export function AskThread({ projectId, worksCount, onHighlightedWorkIds, onOpenWork, onOpenRun, onRunFinished }: AskThreadProps) {
  const [status, setStatus] = useState<ChatStatus | null>(null)
  const [statusLoading, setStatusLoading] = useState(true)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [threadSummaries, setThreadSummaries] = useState<ChatThreadSummary[]>([])
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null)
  const [thread, setThread] = useState<ChatThread | null>(null)
  const [threadsLoading, setThreadsLoading] = useState(true)
  const [threadLoading, setThreadLoading] = useState(false)
  const [composerValue, setComposerValue] = useState('')
  const [composerMode, setComposerMode] = useState<'ask' | 'research'>(worksCount === 0 ? 'research' : 'ask')
  const [error, setError] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [streamEvents, setStreamEvents] = useState<ChatActivityEvent[]>([])
  const [streamingAnswer, setStreamingAnswer] = useState<VerifiedAnswer | null>(null)
  const [streamingResearchAnswer, setStreamingResearchAnswer] = useState<string | null>(null)
  const [streamingPlan, setStreamingPlan] = useState<ResearchPlanPayload | null>(null)
  const [planError, setPlanError] = useState<{ id: string; message: string; retry: boolean } | null>(null)
  const [resumingPlanId, setResumingPlanId] = useState<string | null>(null)
  const [streamError, setStreamError] = useState<string | null>(null)
  const [hoveredAnswerId, setHoveredAnswerId] = useState<string | null>(null)
  const [selectedAnswerId, setSelectedAnswerId] = useState<string | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  const projectGenerationRef = useRef(0)
  const streamGenerationRef = useRef(0)
  const threadLoadRef = useRef(0)
  const selectedThreadRef = useRef<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const initialThreadRef = useRef<{ projectId: string; promise: Promise<ChatThread> } | null>(null)
  const highlightCallbackRef = useRef(onHighlightedWorkIds)
  const modeTouchedRef = useRef(false)
  const lastDecisionRef = useRef<{ id: string; decision: 'approve' | 'edit' | 'reject'; args?: ResearchPlanArgs; message?: string } | null>(null)

  useEffect(() => {
    highlightCallbackRef.current = onHighlightedWorkIds
  }, [onHighlightedWorkIds])

  useEffect(() => {
    if (!modeTouchedRef.current && worksCount !== null) {
      setComposerMode(worksCount === 0 ? 'research' : 'ask')
    }
  }, [worksCount])

  const currentProject = useCallback(
    (generation: number, threadId?: string, streamGeneration?: number): boolean => (
      projectGenerationRef.current === generation
      && (threadId === undefined || selectedThreadRef.current === threadId)
      && (streamGeneration === undefined || streamGenerationRef.current === streamGeneration)
    ),
    [],
  )

  const loadThread = useCallback((threadId: string, generation: number) => {
    const requestId = threadLoadRef.current + 1
    threadLoadRef.current = requestId
    setThreadLoading(true)
    setThread(null)
    api.chatThread(threadId, projectId)
      .then((loaded) => {
        if (!currentProject(generation, threadId) || threadLoadRef.current !== requestId) return
        setThread(loaded)
        setSelectedThreadId(loaded.id)
        selectedThreadRef.current = loaded.id
        setError(null)
      })
      .catch((reason: unknown) => {
        if (!currentProject(generation, threadId) || threadLoadRef.current !== requestId) return
        setThread(null)
        setError(errorMessage(reason, 'Unable to load this chat thread.'))
      })
      .finally(() => {
        if (currentProject(generation, threadId) && threadLoadRef.current === requestId) {
          setThreadLoading(false)
        }
      })
  }, [currentProject, projectId])

  useEffect(() => {
    const generation = projectGenerationRef.current + 1
    projectGenerationRef.current = generation
    abortRef.current?.abort()
    abortRef.current = null
    selectedThreadRef.current = null
    if (initialThreadRef.current && initialThreadRef.current.projectId !== projectId) {
      initialThreadRef.current = null
    }
    threadLoadRef.current += 1

    let active = true
    queueMicrotask(() => {
      if (!active || !currentProject(generation)) return
      setStatus(null)
      setStatusError(null)
      setStatusLoading(true)
      setThreadSummaries([])
      setSelectedThreadId(null)
      setThread(null)
      setThreadsLoading(true)
      setThreadLoading(false)
      setComposerValue('')
      setError(null)
      setStreaming(false)
      setStopping(false)
      setStreamEvents([])
      setStreamingAnswer(null)
      setStreamingResearchAnswer(null)
      setStreamingPlan(null)
      setPlanError(null)
      setResumingPlanId(null)
      setStreamError(null)
      setHoveredAnswerId(null)
      setSelectedAnswerId(null)
      highlightCallbackRef.current([])
    })
    api.chatStatus()
      .then((nextStatus) => {
        if (!active || !currentProject(generation)) return
        setStatus(nextStatus)
      })
      .catch((reason: unknown) => {
        if (!active || !currentProject(generation)) return
        setStatus(null)
        setStatusError(errorMessage(reason, 'Unable to check Ask mode availability.'))
      })
      .finally(() => {
        if (active && currentProject(generation)) setStatusLoading(false)
      })

    const loadThreads = async () => {
      try {
        const summaries = await api.chatThreads(projectId)
        if (!active || !currentProject(generation)) return
        if (summaries.length > 0) {
          setThreadSummaries(summaries)
          selectedThreadRef.current = summaries[0].id
          setSelectedThreadId(summaries[0].id)
          loadThread(summaries[0].id, generation)
          return
        }

        // Share the initial create request across StrictMode's development
        // effect replay so a project gets one empty thread, not two.
        const pending = initialThreadRef.current?.projectId === projectId
          ? initialThreadRef.current.promise
          : api.createChat(projectId)
        initialThreadRef.current = { projectId, promise: pending }
        const created = await pending
        if (!active || !currentProject(generation)) return
        setThreadSummaries([threadSummary(created)])
        selectedThreadRef.current = created.id
        setSelectedThreadId(created.id)
        setThread(created)
        setError(null)
      } catch (reason: unknown) {
        if (!active || !currentProject(generation)) return
        setError(errorMessage(reason, 'Unable to load chat threads.'))
      } finally {
        if (active && currentProject(generation)) setThreadsLoading(false)
      }
    }
    void loadThreads()

    return () => {
      active = false
      abortRef.current?.abort()
      abortRef.current = null
      projectGenerationRef.current += 1
    }
  }, [currentProject, loadThread, projectId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: 'nearest' })
  }, [thread?.messages.length, streaming, streamingAnswer, streamingResearchAnswer, streamingPlan, streamEvents.length])

  const createThread = async () => {
    if (streaming) return
    const generation = projectGenerationRef.current
    setError(null)
    try {
      const created = await api.createChat(projectId)
      if (!currentProject(generation)) return
      setThreadSummaries((current) => [threadSummary(created), ...current])
      selectedThreadRef.current = created.id
      setSelectedThreadId(created.id)
      setThread(created)
      setSelectedAnswerId(null)
      setHoveredAnswerId(null)
      setComposerValue('')
      setPlanError(null)
    } catch (reason: unknown) {
      if (currentProject(generation)) setError(errorMessage(reason, 'Unable to create a chat thread.'))
    }
  }

  const selectThread = (threadId: string) => {
    if (streaming || threadId === selectedThreadId) return
    const generation = projectGenerationRef.current
    selectedThreadRef.current = threadId
    setSelectedThreadId(threadId)
    setSelectedAnswerId(null)
    setHoveredAnswerId(null)
    setStreamError(null)
    setStreamEvents([])
    setStreamingAnswer(null)
    setStreamingResearchAnswer(null)
    setStreamingPlan(null)
    setPlanError(null)
    loadThread(threadId, generation)
  }

  const deleteThread = async () => {
    if (streaming || !selectedThreadId) return
    const currentId = selectedThreadId
    const selected = threadSummaries.find((item) => item.id === currentId)
    const label = selected ? threadLabel(selected) : 'this conversation'
    if (!window.confirm(`Delete ${label}? This cannot be undone.`)) return

    const generation = projectGenerationRef.current
    setError(null)
    try {
      await api.deleteChat(currentId, projectId)
      if (!currentProject(generation, currentId)) return
      const remaining = threadSummaries.filter((item) => item.id !== currentId)
      setThreadSummaries(remaining)
      setSelectedAnswerId(null)
      setHoveredAnswerId(null)
      if (remaining.length > 0) {
        selectedThreadRef.current = remaining[0].id
        setSelectedThreadId(remaining[0].id)
        loadThread(remaining[0].id, generation)
      } else {
        const replacement = await api.createChat(projectId)
        if (!currentProject(generation)) return
        setThreadSummaries([threadSummary(replacement)])
        selectedThreadRef.current = replacement.id
        setSelectedThreadId(replacement.id)
        setThread(replacement)
      }
    } catch (reason: unknown) {
      if (currentProject(generation)) setError(errorMessage(reason, 'Unable to delete this chat thread.'))
    }
  }

  const answerEntries = useMemo(() => {
    const entries: Array<{ id: string; answer: VerifiedAnswer }> = []
    thread?.messages.forEach((message) => {
      if (message.role === 'assistant' && message.answer) entries.push({ id: message.id, answer: message.answer })
    })
    if (streamingAnswer) entries.push({ id: 'streaming-answer', answer: streamingAnswer })
    return entries
  }, [streamingAnswer, thread?.messages])

  const latestAnswer = answerEntries.at(-1) ?? null
  const focusedAnswer = answerEntries.find((item) => item.id === hoveredAnswerId)
    ?? answerEntries.find((item) => item.id === selectedAnswerId)
    ?? latestAnswer

  useEffect(() => {
    const workIds = focusedAnswer
      ? [...new Set(focusedAnswer.answer.citations.map((citation) => citation.work_id))]
      : []
    highlightCallbackRef.current(workIds)
  }, [focusedAnswer])

  const reloadThread = useCallback(async (
    threadId: string,
    generation: number,
    delays: number[],
    streamGeneration?: number,
    waitForAssistant = false,
    afterMessageId?: string,
  ): Promise<void> => {
    for (const [index, delay] of delays.entries()) {
      if (delay > 0) await new Promise<void>((resolve) => window.setTimeout(resolve, delay))
      if (!currentProject(generation, threadId, streamGeneration)) return
      try {
        const loaded = await api.chatThread(threadId, projectId)
        if (!currentProject(generation, threadId, streamGeneration)) return
        setThread(loaded)
        setThreadSummaries((current) => [
          threadSummary(loaded),
          ...current.filter((item) => item.id !== loaded.id),
        ])
        const lastMessage = loaded.messages.at(-1)
        const lastAttempt = index === delays.length - 1
        if (!waitForAssistant || (lastMessage?.role === 'assistant' && lastMessage.id !== afterMessageId) || lastAttempt) {
          setStreamingAnswer(null)
          setStreamingResearchAnswer(null)
          setStreamingPlan(null)
          setStreamEvents([])
          return
        }
      } catch (reason: unknown) {
        if (index === delays.length - 1) {
          setError(errorMessage(reason, 'Unable to refresh the chat thread.'))
        }
      }
    }
  }, [currentProject, projectId])

  const sendMessage = async () => {
    const content = composerValue.trim()
    const threadId = selectedThreadRef.current
    if (!content || !threadId || !status?.available || streaming || threadLoading
      || thread?.messages.some((message) => message.plan_status === 'pending') || streamingPlan) return
    const mode = composerMode

    const generation = projectGenerationRef.current
    const streamGeneration = streamGenerationRef.current + 1
    streamGenerationRef.current = streamGeneration
    const controller = new AbortController()
    abortRef.current = controller
    const optimistic: ChatMessage = {
      id: `pending-${streamGeneration}`,
      role: 'user',
      content,
      mode,
      answer: null,
      events: [],
      created_at: new Date().toISOString(),
      error: null,
    }

    setThread((current) => current?.id === threadId
      ? { ...current, messages: [...current.messages, optimistic] }
      : current)
    setComposerValue('')
    setError(null)
    setStreamError(null)
    setStreamEvents([])
    setStreamingAnswer(null)
    setStreamingResearchAnswer(null)
    setStreamingPlan(null)
    setStreaming(true)
    setStopping(false)

    const stillCurrent = () => currentProject(generation, threadId, streamGeneration)
    const handleEvent = (event: SseEvent) => {
      if (!stillCurrent()) return
      const payload = parseJson(event.data)
      if (event.event === 'status') {
        const text = eventText(payload)
        if (text) {
          setStreamEvents((current) => [...current, { text, tool: null }])
        }
      } else if (event.event === 'answer') {
        const markdown = researchAnswer(payload)
        if (markdown !== null) {
          setStreamingResearchAnswer(markdown)
          return
        }
        const answer = verifiedAnswer(payload)
        if (!answer) {
          setStreamError(`${mode === 'ask' ? 'Ask' : 'Research'} mode returned an invalid answer.`)
          return
        }
        setStreamingAnswer(answer)
      } else if (event.event === 'plan') {
        const plan = researchPlan(payload)
        if (plan) setStreamingPlan(plan)
        else setStreamError('Research mode returned an invalid plan.')
      } else if (event.event === 'error') {
        setStreamError(responseDetail(payload, `${mode === 'ask' ? 'Ask' : 'Research'} mode failed.`))
      }
    }

    try {
      const response = await fetch(api.chatMessageUrl(threadId, projectId), {
        method: 'POST',
        headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, mode }),
        signal: controller.signal,
      })
      await readSseResponse(response, handleEvent)
      if (!stillCurrent()) return
      setStreaming(false)
      setStopping(false)
      abortRef.current = null
      await reloadThread(threadId, generation, [0], streamGeneration)
    } catch (reason: unknown) {
      if (!stillCurrent()) return
      const wasAborted = controller.signal.aborted
      if (wasAborted) {
        // The API may append its cancellation error just after the client
        // closes the stream, so give the worker a few chances to persist it.
        await reloadThread(threadId, generation, [100, 350, 800], streamGeneration, true)
        return
      }

      setStreaming(false)
      setStopping(false)
      abortRef.current = null

      const chatError = reason as ChatError
      if (chatError.status === 503) {
        setStatus((current) => ({
          available: false,
          model: current?.model ?? '',
          reason: chatError.message,
        }))
      } else if (chatError.status === 409) {
        setStreamError('This thread already has an active answer or a pending plan. Review the plan before sending another message.')
      } else {
        setStreamError(errorMessage(reason, `${mode === 'ask' ? 'Ask' : 'Research'} mode failed.`))
      }
      await reloadThread(threadId, generation, [0], streamGeneration)
    } finally {
      // If the project was changed while fetch was pending, the cleanup has
      // already invalidated this stream and no state from it is applied.
      if (stillCurrent()) {
        setStreaming(false)
        setStopping(false)
      }
    }
  }

  const resumePlan = async (
    messageId: string,
    decision: 'approve' | 'edit' | 'reject',
    args?: ResearchPlanArgs,
    message?: string,
  ) => {
    const threadId = selectedThreadRef.current
    if (!threadId || !status?.available || streaming || threadLoading
      || !thread?.messages.some((item) => item.id === messageId && item.plan_status === 'pending')) return

    lastDecisionRef.current = { id: messageId, decision, args, message }
    const generation = projectGenerationRef.current
    const streamGeneration = streamGenerationRef.current + 1
    streamGenerationRef.current = streamGeneration
    const controller = new AbortController()
    abortRef.current = controller
    setResumingPlanId(messageId)
    setPlanError(null)
    setStreamError(null)
    setStreamEvents([])
    setStreamingAnswer(null)
    setStreamingResearchAnswer(null)
    setStreamingPlan(null)
    setStreaming(true)
    setStopping(false)

    const stillCurrent = () => currentProject(generation, threadId, streamGeneration)
    const handleEvent = (event: SseEvent) => {
      if (!stillCurrent()) return
      const payload = parseJson(event.data)
      if (event.event === 'status') {
        const text = eventText(payload)
        if (text) setStreamEvents((current) => [...current, { text, tool: null }])
      } else if (event.event === 'answer') {
        const markdown = researchAnswer(payload)
        if (markdown !== null) {
          setStreamingResearchAnswer(markdown)
          onRunFinished()
        } else {
          setStreamError('Research mode returned an invalid answer.')
        }
      } else if (event.event === 'plan') {
        const plan = researchPlan(payload)
        if (plan) setStreamingPlan(plan)
        else setStreamError('Research mode returned an invalid plan.')
      } else if (event.event === 'error') {
        setStreamError(responseDetail(payload, 'Research mode failed.'))
      }
    }

    try {
      const response = await fetch(api.chatResumeUrl(threadId, projectId), {
        method: 'POST',
        headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, ...(args ? { args } : {}), ...(message ? { message } : {}) }),
        signal: controller.signal,
      })
      await readSseResponse(response, handleEvent)
      if (!stillCurrent()) return
      setStreaming(false)
      setStopping(false)
      setResumingPlanId(null)
      abortRef.current = null
      await reloadThread(threadId, generation, [0], streamGeneration)
    } catch (reason: unknown) {
      if (!stillCurrent()) return
      if (controller.signal.aborted) {
        // The plan message is the thread's last assistant message, so wait
        // for a newer one before treating the reload as settled.
        await reloadThread(threadId, generation, [100, 350, 800], streamGeneration, true, messageId)
        return
      }
      setStreaming(false)
      setStopping(false)
      setResumingPlanId(null)
      abortRef.current = null
      const chatError = reason as ChatError
      if (chatError.status === 409 || chatError.status === 410) {
        setPlanError({ id: messageId, message: 'This plan expired after a server restart — ask again', retry: false })
      } else {
        setPlanError({ id: messageId, message: errorMessage(reason, 'Unable to resume research.'), retry: true })
      }
      if (chatError.status === 503) {
        setStatus((current) => ({ available: false, model: current?.model ?? '', reason: chatError.message }))
      }
      // Refresh either way: a 410 persists the expiry on the plan message.
      await reloadThread(threadId, generation, [0], streamGeneration)
    } finally {
      if (stillCurrent()) {
        setStreaming(false)
        setStopping(false)
        setResumingPlanId(null)
      }
    }
  }

  const retryPlan = (messageId: string) => {
    const last = lastDecisionRef.current
    if (last?.id === messageId) void resumePlan(messageId, last.decision, last.args, last.message)
  }

  const submitMessage = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void sendMessage()
  }

  const stopMessage = () => {
    if (!streaming || stopping) return
    setStopping(true)
    abortRef.current?.abort()
  }

  const displayMessages = thread?.messages ?? []
  const pendingPlan = displayMessages.some((message) => message.plan_status === 'pending') || streamingPlan !== null
  const expiredPlan = displayMessages.some((message) => message.plan_status === 'pending' && message.error === 'plan expired')
    || planError?.message.includes('expired') === true
  const unavailable = status !== null && !status.available
  const canCompose = Boolean(status?.available && thread && !threadLoading)

  return (
    <section className="ask-thread" aria-labelledby="ask-heading">
      <div className="ask-heading">
        <div>
          <p className="eyebrow">Ask</p>
          <h2 id="ask-heading">Chat about this project</h2>
        </div>
        <div className="ask-thread-controls" aria-label="Chat thread controls">
          <label className="ask-thread-label" htmlFor="ask-thread-select">Thread</label>
          <select
            className="ask-thread-select"
            id="ask-thread-select"
            value={selectedThreadId ?? ''}
            disabled={threadsLoading || streaming || threadSummaries.length === 0}
            onChange={(event) => selectThread(event.target.value)}
          >
            {threadSummaries.length === 0 ? <option value="">No threads</option> : null}
            {threadSummaries.map((item) => (
              <option key={item.id} value={item.id}>{threadLabel(item)}</option>
            ))}
          </select>
          <button
            className="ask-thread-action"
            type="button"
            onClick={() => void createThread()}
            disabled={streaming || threadsLoading || threadLoading}
          >
            New thread
          </button>
          <button
            className="ask-thread-action"
            type="button"
            onClick={() => void deleteThread()}
            disabled={streaming || !selectedThreadId}
          >
            Delete
          </button>
        </div>
      </div>

      {statusLoading ? <p className="ask-status">Checking Ask mode…</p> : null}
      {statusError ? <p className="ask-error" role="alert">{statusError}</p> : null}
      {error ? <p className="ask-error" role="alert">{error}</p> : null}
      {unavailable ? (
        <div className="ask-unavailable" role="status">
          <strong>Ask mode is unavailable.</strong>
          <p>{unavailableReason(status)}</p>
        </div>
      ) : null}

      <div className="ask-messages" aria-live="polite" aria-label="Chat messages">
        {threadsLoading || threadLoading ? <p className="ask-status">Loading conversation…</p> : null}
        {!threadsLoading && !threadLoading && thread && displayMessages.length === 0 && !streaming ? (
          <p className="ask-empty">Ask about the project or plan a research harvest.</p>
        ) : null}
        {displayMessages.map((message) => (
          <ChatMessageView
            key={message.id}
            message={message}
            projectId={projectId}
            onOpenWork={onOpenWork}
            onHoverAnswer={setHoveredAnswerId}
            onSelectAnswer={setSelectedAnswerId}
            onDecision={(decision, args, messageText) => void resumePlan(message.id, decision, args, messageText)}
            planBusy={resumingPlanId === message.id}
            planError={planError?.id === message.id ? planError.message : null}
            onRetryPlan={planError?.id === message.id && planError.retry ? () => retryPlan(message.id) : undefined}
            onOpenRun={onOpenRun}
          />
        ))}
        {streamingPlan ? (
          <ChatMessageView
            message={{
              id: 'streaming-plan', role: 'assistant', mode: 'research', content: streamingPlan.description,
              answer: null, events: [], created_at: new Date().toISOString(), error: null,
              pending_plan: streamingPlan.args, plan_status: 'pending', final_args: null, run_id: null,
            }}
            projectId={projectId}
            onOpenWork={onOpenWork}
            onHoverAnswer={setHoveredAnswerId}
            onSelectAnswer={setSelectedAnswerId}
            onDecision={() => {}}
            planBusy
            planError={null}
            onOpenRun={onOpenRun}
          />
        ) : null}
        {streamingAnswer ? (
          <article
            className="ask-message ask-message-assistant ask-message-streaming"
            tabIndex={0}
            aria-label="Assistant answer"
            onMouseEnter={() => setHoveredAnswerId('streaming-answer')}
            onMouseLeave={(event) => {
              if (!event.currentTarget.contains(document.activeElement)) setHoveredAnswerId(null)
            }}
            onFocus={() => setHoveredAnswerId('streaming-answer')}
            onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setHoveredAnswerId(null)
            }}
            onClick={() => setSelectedAnswerId('streaming-answer')}
          >
            <ActivityTrail events={streamEvents} live={false} />
            <span className="ask-message-meta">Ask</span>
            <div className="ask-message-content">
              <AnswerContent projectId={projectId} answer={streamingAnswer} onOpenWork={onOpenWork} />
            </div>
          </article>
        ) : null}
        {streamingResearchAnswer !== null ? (
          <article className="ask-message ask-message-assistant ask-message-streaming" aria-label="Research answer">
            <span className="ask-message-meta">Research</span>
            <ActivityTrail events={streamEvents} />
            <div className="ask-message-content">
              <AnswerContent projectId={projectId} answer={markdownAnswer(streamingResearchAnswer)} onOpenWork={onOpenWork} />
            </div>
          </article>
        ) : null}
        {streaming && !streamingAnswer && streamingResearchAnswer === null && !streamingPlan ? (
          <div className="ask-stream-status" aria-live="polite">
            <ActivityTrail events={streamEvents} live />
          </div>
        ) : null}
        {streamError ? <p className="ask-error" role="alert">{streamError}</p> : null}
        <div ref={messagesEndRef} />
      </div>

      {canCompose ? (
        <form className="ask-composer" onSubmit={submitMessage}>
          <div className="ask-mode-switch" role="group" aria-label="Message mode">
            <button className="ask-mode-option" type="button" aria-pressed={composerMode === 'ask'} disabled={streaming || pendingPlan} onClick={() => { modeTouchedRef.current = true; setComposerMode('ask') }}>Ask</button>
            <button className="ask-mode-option" type="button" aria-pressed={composerMode === 'research'} disabled={streaming || pendingPlan} onClick={() => { modeTouchedRef.current = true; setComposerMode('research') }}>Plan research</button>
          </div>
          <label className="ask-composer-label" htmlFor="ask-composer-input">Question</label>
          <textarea
            className="ask-composer-input"
            id="ask-composer-input"
            rows={3}
            value={composerValue}
            disabled={streaming || pendingPlan}
            placeholder={composerMode === 'ask' ? 'Ask what the project’s papers say…' : 'Describe the literature you want to find…'}
            onChange={(event) => setComposerValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                if (!pendingPlan) void sendMessage()
              }
            }}
          />
          <div className="ask-composer-actions">
            <span className="ask-composer-hint">{expiredPlan ? 'This plan expired. Start a new thread to ask again.' : pendingPlan ? 'Approve, edit or reject the plan first' : 'Enter sends · Shift+Enter adds a line'}</span>
            {streaming ? (
              <button className="ask-stop" type="button" onClick={stopMessage} disabled={stopping}>
                {stopping ? 'Stopping…' : 'Stop'}
              </button>
            ) : (
              <button className="ask-send" type="submit" disabled={!composerValue.trim() || pendingPlan}>
                Send
              </button>
            )}
          </div>
        </form>
      ) : null}
    </section>
  )
}

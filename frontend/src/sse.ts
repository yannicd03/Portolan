/** One parsed Server-Sent Events frame. */
export interface SseEvent {
  event: string
  data: string
}

/** State carried between arbitrary fetch ReadableStream chunks. */
export interface SseParserState {
  buffer: string
  event: string
  data: string[]
}

export function createSseParserState(): SseParserState {
  return { buffer: '', event: '', data: [] }
}

function emitFrame(state: SseParserState): SseEvent | null {
  if (state.data.length === 0) {
    state.event = ''
    return null
  }

  const frame: SseEvent = {
    event: state.event || 'message',
    data: state.data.join('\n'),
  }
  state.event = ''
  state.data = []
  return frame
}

function readLine(state: SseParserState, line: string, events: SseEvent[]): void {
  // SSE comments are useful as keep-alives and do not form an event.
  if (line.startsWith(':')) return
  if (line === '') {
    const frame = emitFrame(state)
    if (frame) events.push(frame)
    return
  }

  const separator = line.indexOf(':')
  const field = separator < 0 ? line : line.slice(0, separator)
  let value = separator < 0 ? '' : line.slice(separator + 1)
  if (value.startsWith(' ')) value = value.slice(1)
  if (field === 'event') state.event = value
  if (field === 'data') state.data.push(value)
}

/**
 * Parse complete SSE lines from one arbitrary chunk.
 *
 * The returned state contains both an incomplete line and an incomplete event,
 * so callers can pass it unchanged to the next invocation. This keeps the
 * parser independent of network chunk boundaries and straightforward to unit
 * test without a browser or a ReadableStream.
 */
export function parseSseChunk(
  state: SseParserState,
  chunk: string,
): { events: SseEvent[]; state: SseParserState } {
  const next: SseParserState = {
    buffer: state.buffer + chunk,
    event: state.event,
    data: [...state.data],
  }
  const events: SseEvent[] = []

  while (true) {
    const newline = next.buffer.indexOf('\n')
    if (newline < 0) break
    let line = next.buffer.slice(0, newline)
    next.buffer = next.buffer.slice(newline + 1)
    if (line.endsWith('\r')) line = line.slice(0, -1)
    readLine(next, line, events)
  }

  return { events, state: next }
}

/** Flush the final line/event when a stream ends without a trailing blank line. */
export function finishSseParser(state: SseParserState): { events: SseEvent[]; state: SseParserState } {
  const next: SseParserState = {
    buffer: state.buffer,
    event: state.event,
    data: [...state.data],
  }
  const events: SseEvent[] = []
  if (next.buffer) readLine(next, next.buffer.replace(/\r$/u, ''), events)
  const frame = emitFrame(next)
  if (frame) events.push(frame)
  return { events, state: next }
}

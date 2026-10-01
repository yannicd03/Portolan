export interface SearchItemRange {
  itemIndex: number
  start: number
  end: number
}

export interface PageMatch {
  ranges: SearchItemRange[]
}

export interface QuoteTextItem {
  str: string
}

export interface QuoteMarkedContent {
  type: string
}

export type QuoteContentItem = QuoteTextItem | QuoteMarkedContent

export interface QuoteContent {
  items: readonly QuoteContentItem[]
}

interface SearchChar {
  char: string
  itemIndex: number
  rawStart: number
  rawEnd: number
}

const graphemeSegmenter = typeof Intl.Segmenter === 'function'
  ? new Intl.Segmenter(undefined, { granularity: 'grapheme' })
  : null

function isWhitespace(value: string): boolean {
  return /\s/u.test(value)
}

function isBreakHyphen(value: string): boolean {
  return value === '-' || value === '‐' || value === '‑' || value === '‒' || value === '–' || value === '—'
}

function appendNormalizedChars(target: SearchChar[], value: string, itemIndex: number, rawStart: number): void {
  const rawEnd = rawStart + value.length
  const normalized = value.normalize('NFKC').toLocaleLowerCase()
  for (const character of Array.from(normalized)) {
    target.push({ char: character, itemIndex, rawStart, rawEnd })
  }
}

function appendSearchChars(target: SearchChar[], value: string, itemIndex: number): void {
  if (graphemeSegmenter) {
    for (const segment of graphemeSegmenter.segment(value)) {
      appendNormalizedChars(target, segment.segment, itemIndex, segment.index)
    }
    return
  }

  // Older engines without Intl.Segmenter still get correct UTF-16 boundaries;
  // the segmenter path additionally keeps combining sequences together.
  let rawOffset = 0
  for (const original of Array.from(value)) {
    appendNormalizedChars(target, original, itemIndex, rawOffset)
    rawOffset += original.length
  }
}

function normalizedPageText(strings: readonly string[]): { text: string; chars: SearchChar[] } {
  const source: SearchChar[] = []
  strings.forEach((value, itemIndex) => {
    appendSearchChars(source, value, itemIndex)
    if (itemIndex < strings.length - 1) {
      source.push({ char: ' ', itemIndex: -1, rawStart: 0, rawEnd: 0 })
    }
  })

  const chars: SearchChar[] = []
  for (let index = 0; index < source.length; index += 1) {
    const current = source[index]
    if (isBreakHyphen(current.char)) {
      let next = index + 1
      let hasWhitespace = false
      while (next < source.length && isWhitespace(source[next].char)) {
        hasWhitespace = true
        next += 1
      }
      if (hasWhitespace) {
        index = next - 1
        continue
      }
    }

    if (isWhitespace(current.char)) {
      if (chars.at(-1)?.char === ' ') continue
      chars.push({ ...current, char: ' ' })
      continue
    }
    chars.push(current)
  }

  return { text: chars.map(({ char }) => char).join(''), chars }
}

function textStrings(items: readonly QuoteContentItem[]): string[] {
  const strings: string[] = []
  for (const item of items) {
    // This is the same filter used by pdf.js TextLayer.#processItems: marked
    // content records have no `str`, while empty text items are retained.
    if ('str' in item && typeof item.str === 'string') strings.push(item.str)
  }
  return strings
}

export function normalizeQuote(value: string): string {
  return normalizedPageText([value]).text.trim()
}

export function findPageMatch(content: QuoteContent, query: string): PageMatch | null {
  if (!query) return null

  const normalized = normalizedPageText(textStrings(content.items))
  const start = normalized.text.indexOf(query)
  if (start < 0) return null

  // `indexOf` uses UTF-16 offsets, while `chars` has one entry per code point.
  const charStart = Array.from(normalized.text.slice(0, start)).length
  const charEnd = charStart + Array.from(query).length
  const ranges = new Map<number, SearchItemRange>()
  for (let index = charStart; index < charEnd; index += 1) {
    const current = normalized.chars[index]
    if (!current || current.itemIndex < 0 || current.rawEnd <= current.rawStart) continue
    const existing = ranges.get(current.itemIndex)
    if (existing) {
      existing.start = Math.min(existing.start, current.rawStart)
      existing.end = Math.max(existing.end, current.rawEnd)
    } else {
      ranges.set(current.itemIndex, {
        itemIndex: current.itemIndex,
        start: current.rawStart,
        end: current.rawEnd,
      })
    }
  }

  return { ranges: [...ranges.values()] }
}

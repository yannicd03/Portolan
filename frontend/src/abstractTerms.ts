/** Rule-based matching of a work's own concepts inside its abstract text. */

export interface TermConcept {
  id: string
  label: string
  aliases: readonly string[]
}

export type AbstractSegment =
  | { kind: 'text'; text: string }
  | { kind: 'term'; text: string; conceptId: string }

interface Mention {
  start: number
  end: number
  conceptId: string
  /** Position of the concept in the input list; earlier concepts win exact ties. */
  rank: number
}

// Hyphens, dashes and whitespace are interchangeable between the words of a term.
const WORD_SEPARATOR = '[\\s\\u2010-\\u2015-]+'
const SPLIT_SEPARATOR = /[\s‐-―-]+/u
// A term must not start or end inside a word ("rag" never matches in "storage").
const BEFORE = '(?<![\\p{L}\\p{N}])'
const AFTER = '(?![\\p{L}\\p{N}])'
const MIN_SURFACE_LENGTH = 2

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** Build one case-insensitive pattern for a surface form, or null when it is too short. */
function surfacePattern(surface: string): RegExp | null {
  const words = surface.trim().split(SPLIT_SEPARATOR).filter(Boolean)
  if (words.join(' ').length < MIN_SURFACE_LENGTH) return null
  // A cheap plural allowance on the last word ("language model" also matches "language models").
  const plural = /\p{L}$/u.test(words[words.length - 1]) ? '(?:e?s)?' : ''
  return new RegExp(`${BEFORE}${words.map(escapeRegExp).join(WORD_SEPARATOR)}${plural}${AFTER}`, 'giu')
}

/**
 * Split abstract text into plain and concept-mention segments.
 *
 * Every label and alias is a surface form.  Overlapping mentions resolve to the
 * longest one, and only the first mention of each concept is marked so that the
 * abstract stays readable.  Concatenating the segment texts returns the input.
 */
export function segmentAbstract(
  text: string,
  concepts: readonly TermConcept[],
): AbstractSegment[] {
  const candidates: Mention[] = []
  concepts.forEach((concept, rank) => {
    const surfaces = new Set([concept.label, ...concept.aliases].map((surface) => surface.trim().toLowerCase()))
    surfaces.forEach((surface) => {
      const pattern = surfacePattern(surface)
      if (!pattern) return
      for (const match of text.matchAll(pattern)) {
        const start = match.index
        candidates.push({ start, end: start + match[0].length, conceptId: concept.id, rank })
      }
    })
  })

  // Longest first, then leftmost, then the concept listed first (highest scored).
  candidates.sort((a, b) => (b.end - b.start) - (a.end - a.start) || a.start - b.start || a.rank - b.rank)
  const accepted: Mention[] = []
  candidates.forEach((candidate) => {
    const overlaps = accepted.some((other) => candidate.start < other.end && other.start < candidate.end)
    if (!overlaps) accepted.push(candidate)
  })
  accepted.sort((a, b) => a.start - b.start)

  const seen = new Set<string>()
  const segments: AbstractSegment[] = []
  let cursor = 0
  accepted.forEach((mention) => {
    if (seen.has(mention.conceptId)) return
    seen.add(mention.conceptId)
    if (mention.start > cursor) segments.push({ kind: 'text', text: text.slice(cursor, mention.start) })
    segments.push({ kind: 'term', text: text.slice(mention.start, mention.end), conceptId: mention.conceptId })
    cursor = mention.end
  })
  if (cursor < text.length) segments.push({ kind: 'text', text: text.slice(cursor) })
  return segments
}

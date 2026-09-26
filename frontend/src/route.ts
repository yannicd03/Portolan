export type HashRoute =
  | { kind: 'project'; projectId: string }
  | { kind: 'doc'; projectId: string; sha256: string; page: number; quote: string | null; workId: string | null }

function decodeSegment(value: string): string | null {
  try {
    return decodeURIComponent(value)
  } catch {
    return null
  }
}

export function parseHashRoute(hash: string): HashRoute | null {
  const [path, query = ''] = hash.replace(/^#/, '').split('?', 2)
  const docMatch = path.match(/^\/projects\/([^/]+)\/docs\/([^/]+)$/)
  if (docMatch) {
    const projectId = decodeSegment(docMatch[1])
    const sha256 = decodeSegment(docMatch[2])
    if (!projectId || !sha256) return null
    const params = new URLSearchParams(query)
    const parsedPage = Number(params.get('page'))
    const page = Number.isSafeInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1
    return {
      kind: 'doc', projectId, sha256, page,
      quote: params.get('q') || null,
      workId: params.get('work') || null,
    }
  }

  const projectMatch = path.match(/^\/projects\/([^/]+)$/)
  if (!projectMatch) return null
  const projectId = decodeSegment(projectMatch[1])
  return projectId ? { kind: 'project', projectId } : null
}

export function projectHref(projectId: string): string {
  return `#/projects/${encodeURIComponent(projectId)}`
}

export function docHref(
  projectId: string,
  sha256: string,
  options: { page?: number; quote?: string | null; workId?: string | null } = {},
): string {
  const params = new URLSearchParams({ page: String(options.page ?? 1) })
  if (options.quote) params.set('q', options.quote)
  if (options.workId) params.set('work', options.workId)
  return `${projectHref(projectId)}/docs/${encodeURIComponent(sha256)}?${params.toString()}`
}

import { useCallback, useEffect, useRef, useState, type CSSProperties, type RefObject } from 'react'
import * as pdfjs from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from 'pdfjs-dist'
import { api } from './api'
import { findPageMatch, normalizeQuote, type PageMatch, type SearchItemRange } from './pdfQuoteMatch'
import { docHref, projectHref } from './route'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

type TextContent = Awaited<ReturnType<PDFPageProxy['getTextContent']>>

export interface PdfViewerProps {
  projectId: string
  sha256: string
  page: number
  quote: string | null
  workId: string | null
}

interface PageSize {
  width: number
  height: number
}

interface PassageState {
  state: 'idle' | 'searching' | 'found' | 'missing'
  page: number
}

interface PageViewProps {
  pageNumber: number
  scale: number
  size: PageSize | null
  forceRender: boolean
  scrollRootRef: RefObject<HTMLDivElement | null>
  match: PageMatch | null
  getPage: (pageNumber: number) => Promise<PDFPageProxy>
  getTextContent: (pageNumber: number) => Promise<TextContent>
  onPageSize: (pageNumber: number, size: PageSize) => void
  onPageElement: (pageNumber: number, element: HTMLDivElement | null) => void
  onMatchRendered: (pageNumber: number, textLayer: HTMLDivElement) => void
}

interface LayerState {
  textLayer: pdfjs.TextLayer
  textDivs: HTMLElement[]
}

function applyHighlights(textDivs: HTMLElement[], ranges: SearchItemRange[]): boolean {
  const byItem = new Map(ranges.map((range) => [range.itemIndex, range]))
  let highlighted = false
  textDivs.forEach((textDiv, itemIndex) => {
    const range = byItem.get(itemIndex)
    if (!range) return
    const value = textDiv.textContent ?? ''
    const start = Math.max(0, Math.min(range.start, value.length))
    const end = Math.max(start, Math.min(range.end, value.length))
    if (start === end) return

    const fragment = document.createDocumentFragment()
    if (start > 0) fragment.append(document.createTextNode(value.slice(0, start)))
    const hit = document.createElement('mark')
    hit.className = 'pdf-hit'
    hit.textContent = value.slice(start, end)
    fragment.append(hit)
    if (end < value.length) fragment.append(document.createTextNode(value.slice(end)))
    textDiv.replaceChildren(fragment)
    highlighted = true
  })
  return highlighted
}

function clearCanvas(canvas: HTMLCanvasElement | null): void {
  if (!canvas) return
  canvas.width = 0
  canvas.height = 0
  canvas.style.width = ''
  canvas.style.height = ''
}

function isCancellationError(reason: unknown): boolean {
  return reason instanceof Error && /abort|cancel/i.test(reason.message)
}

function PageView({
  pageNumber,
  scale,
  size,
  forceRender,
  scrollRootRef,
  match,
  getPage,
  getTextContent,
  onPageSize,
  onPageElement,
  onMatchRendered,
}: PageViewProps) {
  const pageElementRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const textLayerElementRef = useRef<HTMLDivElement | null>(null)
  const layerStateRef = useRef<LayerState | null>(null)
  const matchRef = useRef<PageMatch | null>(match)
  const onMatchRenderedRef = useRef(onMatchRendered)
  const [nearViewport, setNearViewport] = useState(false)
  const [renderError, setRenderError] = useState<string | null>(null)

  useEffect(() => {
    matchRef.current = match
    const layerState = layerStateRef.current
    if (!layerState) return
    const highlighted = match ? applyHighlights(layerState.textDivs, match.ranges) : false
    if (highlighted && textLayerElementRef.current) {
      onMatchRenderedRef.current(pageNumber, textLayerElementRef.current)
    }
  }, [match, pageNumber])

  useEffect(() => {
    onMatchRenderedRef.current = onMatchRendered
  }, [onMatchRendered])

  useEffect(() => {
    const element = pageElementRef.current
    const root = scrollRootRef.current
    if (!element || !root) return
    const observer = new IntersectionObserver(
      ([entry]) => setNearViewport(entry.isIntersecting),
      { root, rootMargin: '1200px 0px' },
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [scrollRootRef])

  useEffect(() => {
    const element = pageElementRef.current
    onPageElement(pageNumber, element)
    return () => onPageElement(pageNumber, null)
  }, [onPageElement, pageNumber])

  const shouldRender = nearViewport || forceRender
  const effectiveScale = Math.max(scale, 0.1)

  useEffect(() => {
    let cancelled = false
    let renderTask: RenderTask | null = null
    let textLayer: pdfjs.TextLayer | null = null

    const resetLayers = () => {
      textLayer?.cancel()
      renderTask?.cancel()
      if (layerStateRef.current?.textLayer === textLayer) layerStateRef.current = null
      textLayerElementRef.current?.replaceChildren()
      clearCanvas(canvasRef.current)
    }

    if (!shouldRender) {
      resetLayers()
      return () => undefined
    }

    setRenderError(null)
    const renderPage = async () => {
      const page = await getPage(pageNumber)
      if (cancelled) return

      const baseViewport = page.getViewport({ scale: 1 })
      onPageSize(pageNumber, { width: baseViewport.width, height: baseViewport.height })
      const viewport = page.getViewport({ scale: effectiveScale })
      const canvas = canvasRef.current
      const textLayerElement = textLayerElementRef.current
      const context = canvas?.getContext('2d', { alpha: false })
      if (!canvas || !textLayerElement || !context) throw new Error('This browser cannot render PDF canvases.')

      const outputScale = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.max(1, Math.floor(viewport.width * outputScale))
      canvas.height = Math.max(1, Math.floor(viewport.height * outputScale))
      canvas.style.width = `${viewport.width}px`
      canvas.style.height = `${viewport.height}px`
      context.setTransform(outputScale, 0, 0, outputScale, 0, 0)
      textLayerElement.replaceChildren()

      const textContent = await getTextContent(pageNumber)
      if (cancelled) return
      // The glyph sizing rules in index.css read this; pdf.js 6 only sets the
      // per-span --font-height/--scale-x/--rotate and --min-font-size.
      textLayerElement.style.setProperty('--total-scale-factor', `${effectiveScale}`)
      textLayer = new pdfjs.TextLayer({
        textContentSource: textContent,
        container: textLayerElement,
        viewport,
      })
      textLayerElement.style.width = `${viewport.width}px`
      textLayerElement.style.height = `${viewport.height}px`
      layerStateRef.current = { textLayer, textDivs: [] }
      renderTask = page.render({ canvasContext: context, canvas, viewport })
      await Promise.all([renderTask.promise, textLayer.render()])
      if (cancelled) return

      layerStateRef.current = { textLayer, textDivs: textLayer.textDivs }
      const currentMatch = matchRef.current
      if (currentMatch && applyHighlights(textLayer.textDivs, currentMatch.ranges)) {
        onMatchRenderedRef.current(pageNumber, textLayerElement)
      }
    }

    void renderPage().catch((reason: unknown) => {
      if (cancelled || isCancellationError(reason)) return
      setRenderError(reason instanceof Error ? reason.message : 'The page could not be rendered.')
    })

    return () => {
      cancelled = true
      resetLayers()
    }
  }, [effectiveScale, getPage, getTextContent, onPageSize, pageNumber, shouldRender])

  const style: CSSProperties = size
    ? {
        width: `${size.width * effectiveScale}px`,
        minHeight: `${size.height * effectiveScale}px`,
      }
    : { width: '100%', aspectRatio: '1 / 1.414' }

  return (
    <div
      ref={pageElementRef}
      className="pdf-page-shell"
      data-page={pageNumber}
      style={style}
      aria-label={`Page ${pageNumber}`}
    >
      <canvas ref={canvasRef} className="pdf-page-canvas" aria-hidden="true" />
      <div ref={textLayerElementRef} className="pdf-viewer__text-layer" />
      {renderError ? <p className="pdf-page-error">{renderError}</p> : null}
    </div>
  )
}

function clampPage(page: number, total: number): number {
  if (!Number.isFinite(page)) return 1
  return Math.min(Math.max(Math.trunc(page), 1), Math.max(total, 1))
}

export default function PdfViewer({ projectId, sha256, page, quote, workId }: PdfViewerProps) {
  const [documentProxy, setDocumentProxy] = useState<PDFDocumentProxy | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [workTitle, setWorkTitle] = useState<string | null>(null)
  const [activePage, setActivePage] = useState(Math.max(1, Math.trunc(page)))
  const [zoomFactor, setZoomFactor] = useState(1)
  const [containerWidth, setContainerWidth] = useState(0)
  const [pageSizes, setPageSizes] = useState<Record<number, PageSize>>({})
  const [searchMatch, setSearchMatch] = useState<{ page: number; match: PageMatch } | null>(null)
  const [passage, setPassage] = useState<PassageState>({ state: 'idle', page: Math.max(1, Math.trunc(page)) })

  const loadingTaskRef = useRef<ReturnType<typeof pdfjs.getDocument> | null>(null)
  const scrollContainerRef = useRef<HTMLDivElement | null>(null)
  const pageElementsRef = useRef(new Map<number, HTMLDivElement>())
  const pagePromisesRef = useRef(new Map<number, Promise<PDFPageProxy>>())
  const textPromisesRef = useRef(new Map<number, Promise<TextContent>>())
  const matchScrolledRef = useRef(false)

  const requestedPage = Math.max(1, Math.trunc(page))
  const normalizedQuote = quote ? normalizeQuote(quote) : ''

  useEffect(() => {
    let active = true
    const loadingTask = pdfjs.getDocument({ url: api.documentPdfUrl(sha256) })
    loadingTaskRef.current = loadingTask
    setLoading(true)
    setLoadError(null)
    setDocumentProxy(null)
    setPageSizes({})
    pagePromisesRef.current.clear()
    textPromisesRef.current.clear()

    void loadingTask.promise
      .then((nextDocument) => {
        if (!active) return
        setDocumentProxy(nextDocument)
        setLoading(false)
      })
      .catch((reason: unknown) => {
        if (!active || isCancellationError(reason)) return
        setLoading(false)
        setLoadError(reason instanceof Error ? reason.message : 'The PDF could not be loaded.')
      })

    return () => {
      active = false
      if (loadingTaskRef.current === loadingTask) loadingTaskRef.current = null
      void loadingTask.destroy().catch(() => undefined)
    }
  }, [sha256])

  useEffect(() => {
    if (!workId) {
      setWorkTitle(null)
      return
    }
    let active = true
    api.work(workId, projectId)
      .then((detail) => {
        if (active) setWorkTitle(detail.work.title)
      })
      .catch(() => {
        if (active) setWorkTitle(null)
      })
    return () => { active = false }
  }, [projectId, workId])

  const getPage = useCallback(async (pageNumber: number): Promise<PDFPageProxy> => {
    if (!documentProxy) throw new Error('The PDF is still loading.')
    const existing = pagePromisesRef.current.get(pageNumber)
    if (existing) return existing
    const next = documentProxy.getPage(pageNumber)
    pagePromisesRef.current.set(pageNumber, next)
    return next
  }, [documentProxy])

  const getTextContent = useCallback(async (pageNumber: number): Promise<TextContent> => {
    const existing = textPromisesRef.current.get(pageNumber)
    if (existing) return existing
    const next = getPage(pageNumber).then((pdfPage) => pdfPage.getTextContent())
    textPromisesRef.current.set(pageNumber, next)
    return next
  }, [getPage])

  const onPageSize = useCallback((pageNumber: number, size: PageSize) => {
    setPageSizes((current) => {
      const previous = current[pageNumber]
      if (previous && previous.width === size.width && previous.height === size.height) return current
      return { ...current, [pageNumber]: size }
    })
  }, [])

  const onPageElement = useCallback((pageNumber: number, element: HTMLDivElement | null) => {
    if (element) pageElementsRef.current.set(pageNumber, element)
    else pageElementsRef.current.delete(pageNumber)
  }, [])

  const updateVisiblePage = useCallback(() => {
    const root = scrollContainerRef.current
    if (!root) return
    const rootBounds = root.getBoundingClientRect()
    const center = rootBounds.top + rootBounds.height / 2
    let nearestPage = activePage
    let nearestDistance = Number.POSITIVE_INFINITY
    pageElementsRef.current.forEach((element, pageNumber) => {
      const bounds = element.getBoundingClientRect()
      const elementCenter = bounds.top + bounds.height / 2
      const distance = Math.abs(elementCenter - center)
      if (distance < nearestDistance) {
        nearestDistance = distance
        nearestPage = pageNumber
      }
    })
    if (nearestPage !== activePage) setActivePage(nearestPage)
  }, [activePage])

  useEffect(() => {
    const root = scrollContainerRef.current
    if (!root) return
    let frame = 0
    const onScroll = () => {
      if (frame) return
      frame = window.requestAnimationFrame(() => {
        frame = 0
        updateVisiblePage()
      })
    }
    root.addEventListener('scroll', onScroll, { passive: true })
    updateVisiblePage()
    return () => {
      root.removeEventListener('scroll', onScroll)
      if (frame) window.cancelAnimationFrame(frame)
    }
  }, [updateVisiblePage])

  useEffect(() => {
    const root = scrollContainerRef.current
    if (!root) return
    const resizeObserver = new ResizeObserver(() => setContainerWidth(root.clientWidth))
    resizeObserver.observe(root)
    setContainerWidth(root.clientWidth)
    return () => resizeObserver.disconnect()
  }, [documentProxy])

  const scrollToPage = useCallback((pageNumber: number, behavior: ScrollBehavior = 'auto') => {
    const element = pageElementsRef.current.get(pageNumber)
    if (!element) return
    element.scrollIntoView({ behavior, block: 'start' })
    setActivePage(pageNumber)
  }, [])

  useEffect(() => {
    if (!documentProxy) return
    const targetPage = clampPage(requestedPage, documentProxy.numPages)
    setActivePage(targetPage)
    const frame = window.requestAnimationFrame(() => scrollToPage(targetPage))
    return () => window.cancelAnimationFrame(frame)
  }, [documentProxy, requestedPage, scrollToPage])

  useEffect(() => {
    if (!documentProxy) return
    let active = true
    const targetPage = clampPage(requestedPage, documentProxy.numPages)
    matchScrolledRef.current = false
    setSearchMatch(null)

    if (!normalizedQuote) {
      setPassage({ state: 'idle', page: targetPage })
      return () => { active = false }
    }

    setPassage({ state: 'searching', page: targetPage })
    const search = async () => {
      let found: { page: number; match: PageMatch } | null = null
      const targetContent = await getTextContent(targetPage)
      const targetMatch = findPageMatch(targetContent, normalizedQuote)
      if (targetMatch) {
        found = { page: targetPage, match: targetMatch }
      } else {
        for (let pageNumber = 1; pageNumber <= documentProxy.numPages; pageNumber += 1) {
          if (pageNumber === targetPage) continue
          const content = await getTextContent(pageNumber)
          const match = findPageMatch(content, normalizedQuote)
          if (match) {
            found = { page: pageNumber, match }
            break
          }
        }
      }
      if (!active) return
      if (found) {
        setSearchMatch(found)
        setPassage({ state: 'found', page: found.page })
        setActivePage(found.page)
        window.requestAnimationFrame(() => scrollToPage(found.page, 'auto'))
      } else {
        setPassage({ state: 'missing', page: targetPage })
        setActivePage(targetPage)
        window.requestAnimationFrame(() => scrollToPage(targetPage, 'auto'))
      }
    }

    void search().catch((reason: unknown) => {
      if (!active || isCancellationError(reason)) return
      setPassage({ state: 'missing', page: targetPage })
    })
    return () => { active = false }
  }, [documentProxy, getTextContent, normalizedQuote, requestedPage, scrollToPage])

  const onMatchRendered = useCallback((pageNumber: number, textLayer: HTMLDivElement) => {
    if (matchScrolledRef.current || searchMatch?.page !== pageNumber) return
    const firstHit = textLayer.querySelector<HTMLElement>('mark.pdf-hit')
    if (!firstHit) return
    matchScrolledRef.current = true
    firstHit.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [searchMatch])

  const pageCount = documentProxy?.numPages ?? 0
  const visiblePage = pageCount > 0 ? clampPage(activePage, pageCount) : Math.max(1, activePage)
  const availableWidth = Math.max(containerWidth - 32, 320)
  const passageMessage = passage.state === 'searching'
    ? 'Searching for passage…'
    : passage.state === 'found'
      ? `Passage found on p. ${passage.page}`
      : passage.state === 'missing'
        ? `Passage not found — showing p. ${passage.page}`
        : null

  const navigateToPage = (nextPage: number) => {
    const target = clampPage(nextPage, pageCount)
    setActivePage(target)
    window.location.hash = docHref(projectId, sha256, { page: target, quote, workId })
  }

  const zoomOut = () => setZoomFactor((value) => Math.max(0.5, Number((value - 0.1).toFixed(2))))
  const zoomIn = () => setZoomFactor((value) => Math.min(2.5, Number((value + 0.1).toFixed(2))))

  return (
    <section className="pdf-viewer" aria-label="PDF viewer">
      <header className="pdf-viewer__toolbar">
        <div className="pdf-viewer__title">
          <p className="pdf-viewer__eyebrow">Document</p>
          <h2>{workTitle ?? 'PDF viewer'}</h2>
        </div>
        <div className="pdf-viewer__controls">
          <span className="pdf-viewer__page-indicator">p. {visiblePage} / {pageCount || '—'}</span>
          <button type="button" className="pdf-viewer__button" onClick={() => navigateToPage(visiblePage - 1)} disabled={!documentProxy || visiblePage <= 1} aria-label="Previous page">Prev</button>
          <button type="button" className="pdf-viewer__button" onClick={() => navigateToPage(visiblePage + 1)} disabled={!documentProxy || visiblePage >= pageCount} aria-label="Next page">Next</button>
          <span className="pdf-viewer__separator" aria-hidden="true" />
          <button type="button" className="pdf-viewer__button" onClick={zoomOut} aria-label="Zoom out">−</button>
          <button type="button" className="pdf-viewer__button" onClick={zoomIn} aria-label="Zoom in">+</button>
          <button type="button" className="pdf-viewer__button" onClick={() => setZoomFactor(1)} aria-label="Fit page to width">Fit width</button>
          <a className="pdf-viewer__link" href={api.documentPdfUrl(sha256)} target="_blank" rel="noreferrer">Open original</a>
          <button type="button" className="pdf-viewer__button pdf-viewer__close" onClick={() => { window.location.hash = projectHref(projectId) }} aria-label="Close PDF viewer">Close</button>
        </div>
      </header>
      {passageMessage ? <p className="pdf-viewer__passage" aria-live="polite">{passageMessage}</p> : null}
      {loadError ? (
        <p className="pdf-viewer__message pdf-viewer__message--error" role="alert">Could not load PDF: {loadError}</p>
      ) : loading || !documentProxy ? (
        <p className="pdf-viewer__message">Loading PDF…</p>
      ) : (
        <div ref={scrollContainerRef} className="pdf-viewer__scroll">
          <div className="pdf-viewer__pages">
            {Array.from({ length: pageCount }, (_, index) => {
              const pageNumber = index + 1
              const size = pageSizes[pageNumber] ?? null
              const pageScale = (availableWidth / (size?.width ?? 612)) * zoomFactor
              return (
                <PageView
                  key={pageNumber}
                  pageNumber={pageNumber}
                  scale={pageScale}
                  size={size}
                  forceRender={pageNumber === visiblePage || searchMatch?.page === pageNumber || pageNumber === requestedPage}
                  scrollRootRef={scrollContainerRef}
                  match={searchMatch?.page === pageNumber ? searchMatch.match : null}
                  getPage={getPage}
                  getTextContent={getTextContent}
                  onPageSize={onPageSize}
                  onPageElement={onPageElement}
                  onMatchRendered={onMatchRendered}
                />
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}

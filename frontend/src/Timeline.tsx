import { useMemo, useState } from 'react'
import type { GraphNode } from './api'

export interface YearRange {
  start: number
  end: number
}

interface TimelineBar {
  year: number
  count: number
}

export interface TimelineProps {
  nodes: readonly GraphNode[]
  value: YearRange | null
  onChange: (value: YearRange | null) => void
}

const CHART_WIDTH = 640
const CHART_HEIGHT = 112
const PLOT_LEFT = 10
const PLOT_RIGHT = 10
const PLOT_TOP = 12
const PLOT_BOTTOM = 28

function timelineBars(nodes: readonly GraphNode[]): TimelineBar[] {
  const counts = new Map<number, number>()
  nodes.forEach((node) => {
    if (node.kind !== 'work') return
    const year = node.data.year
    if (typeof year !== 'number' || !Number.isFinite(year)) return
    const normalizedYear = Math.trunc(year)
    counts.set(normalizedYear, (counts.get(normalizedYear) ?? 0) + 1)
  })

  return [...counts.entries()]
    .sort(([firstYear], [secondYear]) => firstYear - secondYear)
    .map(([year, count]) => ({ year, count }))
}

function rangeLabel(value: YearRange | null): string | null {
  if (!value) return null
  const start = Math.min(value.start, value.end)
  const end = Math.max(value.start, value.end)
  return start === end ? String(start) : `${start}–${end}`
}

export function Timeline({ nodes, value, onChange }: TimelineProps) {
  const [collapsed, setCollapsed] = useState(false)
  const [anchorYear, setAnchorYear] = useState<number | null>(null)
  const bars = useMemo(() => timelineBars(nodes), [nodes])
  const selectedStart = value ? Math.min(value.start, value.end) : null
  const selectedEnd = value ? Math.max(value.start, value.end) : null
  const selectedLabel = rangeLabel(value)
  const maxCount = bars.reduce((maximum, bar) => Math.max(maximum, bar.count), 0)
  const plotWidth = CHART_WIDTH - PLOT_LEFT - PLOT_RIGHT
  const plotHeight = CHART_HEIGHT - PLOT_TOP - PLOT_BOTTOM
  const slotWidth = bars.length > 0 ? plotWidth / bars.length : plotWidth

  const selectYear = (year: number, extend: boolean) => {
    if (!extend) {
      setAnchorYear(year)
      onChange({ start: year, end: year })
      return
    }

    const anchor = anchorYear ?? value?.start ?? year
    setAnchorYear(anchor)
    onChange({
      start: Math.min(anchor, year),
      end: Math.max(anchor, year),
    })
  }

  const clearFilter = () => {
    setAnchorYear(null)
    onChange(null)
  }

  return (
    <section className={`timeline${collapsed ? ' timeline--collapsed' : ''}`} aria-label="Publication timeline">
      <header className="timeline__header">
        <div className="timeline__heading">
          <h2 className="timeline__title">Timeline</h2>
          {selectedLabel ? <span className="timeline__range">{selectedLabel}</span> : null}
        </div>
        <div className="timeline__actions">
          {selectedLabel ? (
            <button className="timeline__clear" type="button" onClick={clearFilter}>
              Clear
            </button>
          ) : null}
          <button
            className="timeline__toggle"
            type="button"
            aria-expanded={!collapsed}
            aria-controls="timeline-chart"
            onClick={() => setCollapsed((current) => !current)}
          >
            {collapsed ? 'Show' : 'Hide'}
          </button>
        </div>
      </header>

      {!collapsed ? (
        <div className="timeline__body" id="timeline-chart">
          {bars.length > 0 ? (
            <svg
              className="timeline__chart"
              viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
              aria-label="Works by publication year"
            >
              <line
                className="timeline__axis"
                x1={PLOT_LEFT}
                x2={CHART_WIDTH - PLOT_RIGHT}
                y1={CHART_HEIGHT - PLOT_BOTTOM}
                y2={CHART_HEIGHT - PLOT_BOTTOM}
              />
              {bars.map((bar, index) => {
                const barHeight = maxCount > 0 ? (bar.count / maxCount) * plotHeight : 0
                const width = Math.max(2, slotWidth - 4)
                const inset = slotWidth >= 6 ? 2 : Math.max(0, (slotWidth - width) / 2)
                const x = PLOT_LEFT + index * slotWidth + inset
                const y = CHART_HEIGHT - PLOT_BOTTOM - barHeight
                const isActive = selectedStart !== null && selectedEnd !== null
                  && bar.year >= selectedStart && bar.year <= selectedEnd
                const showLabel = bars.length <= 12
                  || index === 0
                  || index === bars.length - 1
                  || index % Math.ceil(bars.length / 8) === 0

                return (
                  <g className="timeline__bar-group" key={bar.year}>
                    <rect
                      className={`timeline__bar${isActive ? ' timeline__bar--active' : ''}`}
                      x={x}
                      y={y}
                      width={width}
                      height={barHeight}
                      role="button"
                      tabIndex={0}
                      aria-label={`${bar.year}: ${bar.count} works`}
                      aria-pressed={isActive}
                      onClick={(event) => selectYear(bar.year, event.shiftKey)}
                      onKeyDown={(event) => {
                        if (event.key !== 'Enter' && event.key !== ' ') return
                        event.preventDefault()
                        selectYear(bar.year, event.shiftKey)
                      }}
                    >
                      <title>{bar.year}: {bar.count} works</title>
                    </rect>
                    {showLabel ? (
                      <text
                        className="timeline__label"
                        x={x + width / 2}
                        y={CHART_HEIGHT - 8}
                        textAnchor="middle"
                      >
                        {bar.year}
                      </text>
                    ) : null}
                  </g>
                )
              })}
            </svg>
          ) : (
            <p className="timeline__empty">No publication years in this map.</p>
          )}
        </div>
      ) : null}
    </section>
  )
}

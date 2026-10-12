import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { darkChart, lightChart } from '../lib/chart-theme'
import { setThemeChoice } from '../lib/theme'
import { EntityGraph } from './EntityGraph'

const { chart } = vi.hoisted(() => ({ chart: { on: vi.fn(), dispose: vi.fn(), resize: vi.fn(), setOption: vi.fn(), getDataURL: vi.fn(() => 'data:image/png;base64,AAAA') } }))
vi.mock('echarts/core', () => ({ init: () => chart, use: vi.fn() }))

const nodes = [{ id: 'a', text: 'Acme', type: 'ORG', article_count: 4 }, { id: 'b', text: 'Jane', type: 'PERSON', article_count: 2 }]
const edges = [{ source: 'a', target: 'b', weight: 3, score: 0.5, recent_weight: 0 }]
let clicked: HTMLAnchorElement[] = []

beforeEach(() => {
  vi.clearAllMocks()
  clicked = []
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { clicked.push(this) })
})
afterEach(() => { act(() => setThemeChoice('system')); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('downloads the drawn graph as a PNG on the current theme\'s background', () => {
  act(() => setThemeChoice('dark'))
  render(<EntityGraph nodes={nodes} edges={edges} onSelect={() => {}} onSelectEdge={() => {}} />)

  fireEvent.click(screen.getByRole('button', { name: 'Download PNG' }))

  expect(chart.getDataURL).toHaveBeenCalledWith({ type: 'png', pixelRatio: 2, backgroundColor: darkChart.background })
  expect(clicked).toHaveLength(1)
  expect(clicked[0].download).toBe('entity-graph.png')
  expect(clicked[0].href).toBe('data:image/png;base64,AAAA')
  expect(lightChart.background).toBe('#ffffff')
})

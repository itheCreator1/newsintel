import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { darkChart, lightChart } from '../lib/chart-theme'
import { setThemeChoice } from '../lib/theme'
import { BarChart } from './BarChart'

const { chart } = vi.hoisted(() => ({ chart: { on: vi.fn(), off: vi.fn(), dispose: vi.fn(), resize: vi.fn(), setOption: vi.fn() } }))
vi.mock('echarts/core', () => ({ init: () => chart, use: vi.fn() }))

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
})
afterEach(() => { act(() => setThemeChoice('system')); vi.unstubAllGlobals() })

const axisColour = () => chart.setOption.mock.lastCall![0].xAxis.axisLabel.color

it('draws in the palette of the current theme and redraws when it changes', () => {
  act(() => setThemeChoice('dark'))
  render(<BarChart items={[{ id: 'a', label: 'A', value: 2 }]} valueLabel="articles" ariaLabel="Bars" onSelect={() => {}} />)
  expect(axisColour()).toBe(darkChart.axisLabel)

  act(() => setThemeChoice('light'))
  expect(axisColour()).toBe(lightChart.axisLabel)
})

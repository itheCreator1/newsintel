import { render } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { GeoChart } from './GeoChart'

// The stand-in keeps the one rule of the real chart that matters here (echarts 6.1.0): drawing or
// resizing a map into a box with no width or no height throws.
const { chart, box } = vi.hoisted(() => {
  const box = { width: 0, height: 0 }
  const size = { width: 0, height: 0 }
  const guard = () => {
    if (size.width === 0 || size.height === 0) throw new TypeError("Cannot read properties of null (reading '0')")
  }
  const chart = {
    on: vi.fn(),
    dispose: vi.fn(),
    getWidth: () => size.width,
    getHeight: () => size.height,
    resize: vi.fn(() => { Object.assign(size, box); guard() }),
    setOption: vi.fn((_option: { series: { data: unknown[] }[] }) => guard()),
    reset: () => Object.assign(size, box),
  }
  return { chart, box }
})
vi.mock('echarts/core', () => ({ init: () => { chart.reset(); return chart }, registerMap: vi.fn(), use: vi.fn() }))

let resized: () => void
const setBox = (width: number, height: number) => Object.assign(box, { width, height })
const renderChart = (items = [{ code: 'GR', label: 'Greece', value: 3 }]) =>
  render(<GeoChart items={items} unit="articles" ariaLabel="Map of articles by source country" onSelect={() => {}} />)

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(() => box.width)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(() => box.height)
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { resized = callback }
    observe() {}
    disconnect() {}
  })
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('waits for its container to have area before drawing the map', () => {
  setBox(0, 0)
  renderChart()
  expect(chart.setOption).not.toHaveBeenCalled()

  setBox(600, 400)
  resized()
  expect(chart.resize).toHaveBeenCalledTimes(1)
  expect(chart.setOption).toHaveBeenCalledTimes(1)
})

it('leaves the drawn map alone when its container collapses, and resizes when it returns', () => {
  setBox(600, 400)
  renderChart()
  expect(chart.setOption).toHaveBeenCalledTimes(1)

  // What a hidden or just-removed container reports.
  setBox(600, 0)
  resized()
  expect(chart.resize).not.toHaveBeenCalled()

  setBox(300, 200)
  resized()
  expect(chart.resize).toHaveBeenCalledTimes(1)
  expect(chart.setOption).toHaveBeenCalledTimes(1)
})

it('draws new data that arrived while the container had no area once it has area again', () => {
  setBox(600, 400)
  const view = renderChart()
  setBox(0, 0)
  resized()
  view.rerender(<GeoChart items={[{ code: 'US', label: 'United States', value: 2 }]} unit="articles" ariaLabel="Map of articles by source country" onSelect={() => {}} />)
  expect(chart.setOption).toHaveBeenCalledTimes(1)

  setBox(600, 400)
  resized()
  expect(chart.setOption).toHaveBeenCalledTimes(2)
  expect(chart.setOption.mock.lastCall![0].series[0].data).toEqual([{ name: 'US', value: 2, selected: false }])
})

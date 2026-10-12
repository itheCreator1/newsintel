import { expect, it } from 'vitest'
import { chartThemeFor, darkChart, lightChart } from './chart-theme'

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map(start => parseInt(hex.slice(start, start + 2), 16) / 255)
    .map(channel => channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const contrast = (a: string, b: string) => {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (light + 0.05) / (dark + 0.05)
}

it('has a dark palette with every colour the light one has', () => {
  expect(Object.keys(darkChart).sort()).toEqual(Object.keys(lightChart).sort())
  expect(Object.keys(darkChart.entities).sort()).toEqual(Object.keys(lightChart.entities).sort())
  expect(darkChart.groups).toHaveLength(lightChart.groups.length)
  expect(chartThemeFor('dark')).toBe(darkChart)
  expect(chartThemeFor('light')).toBe(lightChart)
})

it('keeps nodes, groups and axis labels readable on each theme\'s card', () => {
  // Graphics need 3:1 against what they sit on (WCAG 1.4.11); text needs 4.5:1.
  for (const [theme, card] of [[lightChart, '#ffffff'], [darkChart, '#292929']] as const) {
    for (const colour of [...Object.values(theme.entities), ...theme.groups, theme.highlight]) {
      expect(contrast(colour, card), `${colour} on ${card}`).toBeGreaterThanOrEqual(3)
    }
    expect(contrast(theme.axisLabel, card)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(theme.labelText, card)).toBeGreaterThanOrEqual(4.5)
  }
})

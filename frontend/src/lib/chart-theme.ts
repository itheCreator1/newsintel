'use client'

import { useTheme, type Theme } from './theme'

/** ECharts takes literal colors, not CSS variables, so the chart palettes live here, matched by hand
 * to the light and dark tokens in globals.css (Fluent brand blue on white, or on Fluent's dark neutrals). */
const lightBase = {
  // The card a chart sits on, for an exported image.
  background: '#ffffff',
  axisLabel: '#616161',
  axisLine: '#d1d1d1',
  splitLine: '#ebebeb',
  bar: 'rgba(15, 108, 189, 0.45)',
  barStrong: 'rgba(15, 108, 189, 0.7)',
  highlight: '#0f6cbd',
  highlightShadow: 'rgba(15, 108, 189, 0.35)',
  brushFill: 'rgba(15, 108, 189, 0.12)',
  mapArea: '#f0f0f0',
  mapBorder: '#c7c7c7',
  mapLow: '#cfe4fa',
  mapHover: '#2886de',
  mapSelected: '#0c3b5e',
  labelText: '#242424',
  labelHalo: 'rgba(255, 255, 255, 0.9)',
  edge: '#8a8a8a',
  nodeBorder: '#ffffff',
  // Fluent dark yellow: a dashed new connection stands out from the grey edges and still reads on white.
  newEdge: '#c19c00',
  // Fluent purple: a dotted stated link reads apart from both co-occurrence greys and the new-edge yellow.
  statedEdge: '#8764b8',
}

export type ChartTheme = typeof lightBase & { entities: Record<string, string>; groups: string[] }

/** Graph group colors, largest group first: the entity hues plus a teal. */
const groupsOf = (entities: Record<string, string>, teal: string) => [
  entities.PERSON, entities.ORG, entities.GPE, entities.COUNTRY, entities.LOCATION, entities.EVENT, entities.PRODUCT, teal,
]

/** Entity-type node colors from Fluent's shared palette: distinct hues, all dark enough to read on white. */
const lightEntities: Record<string, string> = {
  PERSON: '#0f6cbd', ORG: '#038387', GPE: '#ca5010', COUNTRY: '#d13438',
  LOCATION: '#8764b8', EVENT: '#c239b3', PRODUCT: '#498205', OTHER: '#616161',
}

// The same hues from Fluent's lighter tints, so nodes and labels read on the dark card (#292929).
const darkEntities: Record<string, string> = {
  PERSON: '#479ef5', ORG: '#4bc6c9', GPE: '#f7894a', COUNTRY: '#ee6b72',
  LOCATION: '#a787d6', EVENT: '#d873cf', PRODUCT: '#85b44c', OTHER: '#adadad',
}

export const lightChart: ChartTheme = { ...lightBase, entities: lightEntities, groups: groupsOf(lightEntities, '#008b94') }

export const darkChart: ChartTheme = {
  background: '#292929',
  axisLabel: '#adadad',
  axisLine: '#575757',
  splitLine: '#383838',
  bar: 'rgba(71, 158, 245, 0.5)',
  barStrong: 'rgba(71, 158, 245, 0.8)',
  highlight: '#479ef5',
  highlightShadow: 'rgba(71, 158, 245, 0.4)',
  brushFill: 'rgba(71, 158, 245, 0.16)',
  mapArea: '#333333',
  mapBorder: '#575757',
  mapLow: '#0c3b5e',
  mapHover: '#62abf5',
  mapSelected: '#cfe4fa',
  labelText: '#ededed',
  labelHalo: 'rgba(26, 26, 26, 0.9)',
  edge: '#8a8a8a',
  nodeBorder: '#1a1a1a',
  newEdge: '#fde300',
  statedEdge: '#b696e0',
  entities: darkEntities,
  groups: groupsOf(darkEntities, '#58d3db'),
}

export const chartThemeFor = (theme: Theme): ChartTheme => theme === 'dark' ? darkChart : lightChart

/** The palette for the page's current theme; a chart redraws when it changes. */
export function useChartTheme(): ChartTheme {
  return chartThemeFor(useTheme().resolved)
}

/** ECharts takes literal colors, not CSS variables, so the chart palette lives here, matched by hand
 * to the light tokens in globals.css (Fluent brand blue on white). */
export const chartTheme = {
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
} as const

/** Entity-type node colors from Fluent's shared palette: distinct hues, all dark enough to read on white. */
export const entityColors: Record<string, string> = {
  PERSON: '#0f6cbd', ORG: '#038387', GPE: '#ca5010', COUNTRY: '#d13438',
  LOCATION: '#8764b8', EVENT: '#c239b3', PRODUCT: '#498205', OTHER: '#616161',
}

/** Graph group colors, largest group first: the entity hues plus Fluent teal, all readable on white. */
export const groupColors = [
  entityColors.PERSON, entityColors.ORG, entityColors.GPE, entityColors.COUNTRY,
  entityColors.LOCATION, entityColors.EVENT, entityColors.PRODUCT, '#00b7c3',
]

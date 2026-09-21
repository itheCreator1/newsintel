// Mirrors backend/tests/fixtures/server.py: the fixture server moves every feed pubDate forward by
// whole days so the newest lands on yesterday (UTC). Pass a date as written in the fixture XML.
const ANCHOR = Date.UTC(2026, 8, 14)
const DAY = 86_400_000

export function fixtureDate(isoDate: string): string {
  const today = new Date()
  const offset = Math.max(0, Math.floor((Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()) - ANCHOR) / DAY))
  return new Date(Date.parse(isoDate) + offset * DAY).toISOString().slice(0, 10)
}

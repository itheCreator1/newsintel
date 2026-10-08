/** Wikidata in words and links: candidate reasons, identifiers, history entries. */

const WIKIDATA = 'https://www.wikidata.org/wiki/'

export const qidHref = (qid: string) => `${WIKIDATA}${qid}`

/** One stated reason of a candidate (label:en, type_matches, sitelinks:120) in words. */
export function reasonText(reason: string): string {
  const [code, value] = reason.split(':', 2)
  switch (code) {
    case 'label': return `Exact label (${value})`
    case 'alias': return `Exact alias (${value})`
    case 'variant': return `${value} label is already a name`
    case 'type_matches': return 'Type matches'
    case 'type_differs': return 'Type differs'
    case 'sitelinks': return `${value} sitelink${value === '1' ? '' : 's'}`
    default: return reason
  }
}

const SCHEMES = [
  { scheme: 'viaf', label: 'VIAF', href: (value: string) => `https://viaf.org/viaf/${value}` },
  { scheme: 'isni', label: 'ISNI', href: (value: string) => `https://isni.org/isni/${value.replace(/\s+/g, '')}` },
  { scheme: 'lcnaf', label: 'LCNAF', href: (value: string) => `https://id.loc.gov/authorities/names/${value}.html` },
] as const

export interface IdentifierLink { scheme: string; label: string; value: string; href: string }

/** The identifiers read off the item, VIAF, ISNI, LCNAF, each linked to its own record. */
export function identifierLinks(identifiers: Record<string, string>): IdentifierLink[] {
  return SCHEMES.flatMap(({ scheme, label, href }) => identifiers[scheme] ? [{ scheme, label, value: identifiers[scheme], href: href(identifiers[scheme]) }] : [])
}

/** A label or description in the entity's language, else English, else Greek. */
export function itemText(texts: Record<string, string>, language: string): string | null {
  return texts[language] ?? texts.en ?? texts.el ?? null
}

interface Change { action: string; before: Record<string, unknown> | null; after: Record<string, unknown> | null }

/** A Wikidata history entry in words; null for any other action. */
export function wikidataChange({ action, before, after }: Change): string | null {
  const was = String(before?.qid ?? '')
  const now = String(after?.qid ?? '')
  switch (action) {
    case 'wikidata_linked': return `Linked to Wikidata ${now}`
    case 'wikidata_unlinked': return `Unlinked from Wikidata ${was}`
    case 'wikidata_redirected': return `Wikidata merged ${was} into ${now}; the link moved`
    case 'wikidata_missing': return `Wikidata deleted ${was}; the link is kept`
    default: return null
  }
}

/** A QID as typed: trimmed, upper case; null when it is not one. */
export function typedQid(text: string): string | null {
  const qid = text.trim().toUpperCase()
  return /^Q[1-9]\d*$/.test(qid) ? qid : null
}

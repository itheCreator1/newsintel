import type { PickerOption } from '../components/CheckboxPicker'

/** The ISO 3166-1 codes the backend's country lexicon knows (backend/app/nlp/data/countries-en.json). */
const COUNTRY_CODES = 'AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW'.split(' ')

/** The languages the backend's detector (lingua) can name, as ISO 639-1 codes. */
const LANGUAGE_CODES = 'af ar az be bg bn bs ca cs cy da de el en eo es et eu fa fi fr ga gu he hi hr hu hy id is it ja ka kk ko la lg lt lv mi mk mn mr ms nb nl nn pa pl pt ro ru sk sl sn so sq sr st sv sw ta te th tl tn tr ts uk ur vi xh yo zh zu'.split(' ')

type NameType = 'region' | 'language'

function displayNames(locale: string, type: NameType): Intl.DisplayNames | null {
  try {
    return new Intl.DisplayNames(locale, { type, fallback: 'none' })
  } catch {
    return null
  }
}

const names = {
  region: { en: displayNames('en', 'region'), el: displayNames('el', 'region') },
  language: { en: displayNames('en', 'language'), el: displayNames('el', 'language') },
}

/** A real name for the code, or null when Intl doesn't know it ("Unknown Region", "root", the code itself). */
function nameOf(type: NameType, locale: 'en' | 'el', code: string): string | null {
  let name: string | undefined
  try {
    name = names[type][locale]?.of(code)
  } catch {
    return null
  }
  if (!name || name.toLowerCase() === code.toLowerCase() || name === 'root' || /^unknown\b/i.test(name)) return null
  if (type === 'region' && /^άγνωστη/i.test(name)) return null
  return name
}

/** "Greece (GR)"; a code no country has stays as it is. */
export function countryLabel(code: string): string {
  const value = code.toUpperCase()
  const name = nameOf('region', 'en', value)
  return name ? `${name} (${value})` : value
}

/** "Greek (el)"; a code no language has (such as "und") stays as it is. */
export function languageLabel(code: string): string {
  const value = code.toLowerCase()
  const name = nameOf('language', 'en', value)
  return name ? `${name} (${value})` : value
}

/** Lowercase without accents, so "Ελλάδα", "ελλαδα" and "ΕΛΛΑΔΑ" all match. */
export function foldSearchText(text: string): string {
  return text.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase().replace(/ς/g, 'σ')
}

export interface NamedOption extends PickerOption { search: string }

function buildOptions(type: NameType, codes: string[], label: (code: string) => string): NamedOption[] {
  return codes
    .map(code => {
      const label_ = label(code)
      const greek = nameOf(type, 'el', code) ?? ''
      return { value: code, label: label_, search: foldSearchText(`${label_} ${greek}`) }
    })
    .sort((a, b) => a.label.localeCompare(b.label))
}

export const COUNTRY_OPTIONS: NamedOption[] = buildOptions('region', COUNTRY_CODES, countryLabel)
export const LANGUAGE_OPTIONS: NamedOption[] = buildOptions('language', LANGUAGE_CODES, languageLabel)

/** Whether the option's English name, Greek name or code contains the term; an empty term matches all. */
export function matchesOption(option: PickerOption & { search?: string }, term: string): boolean {
  const needle = foldSearchText(term.trim())
  if (!needle) return true
  return (option.search ?? foldSearchText(`${option.label} ${option.value}`)).includes(needle)
}

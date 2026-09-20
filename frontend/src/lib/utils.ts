export { cn } from "cn"

export const plural = (count: number, noun: string, many = `${noun}s`) => `${count} ${count === 1 ? noun : many}`

const regionNames = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames(['en'], { type: 'region' }) : undefined

/** The English name of a two-letter country code, or the code itself when it has none. */
export function countryName(code: string): string {
  try { return regionNames?.of(code.toUpperCase()) ?? code } catch { return code }
}

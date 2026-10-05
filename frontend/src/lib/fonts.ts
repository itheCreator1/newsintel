import { JetBrains_Mono, Manrope, PT_Serif } from 'next/font/google'

/** Applied via `.variable` on a page's own root element, not html/body — this pilot redesign
 * (Overview + Search) shouldn't change the font on the seven un-redesigned routes. */
export const displayFont = Manrope({ subsets: ['latin'], weight: ['500', '600', '700', '800'], variable: '--font-display' })
export const monoFont = JetBrains_Mono({ subsets: ['latin'], weight: ['500', '600'], variable: '--font-mono-data' })
/** The article reader's body face (Typora's Newsprint theme). Applied on the reader's own root. */
export const readerFont = PT_Serif({ subsets: ['latin'], weight: ['400', '700'], style: ['normal', 'italic'], variable: '--font-reader' })

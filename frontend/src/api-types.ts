import type { components } from './types.generated'

export type Feed = components['schemas']['FeedResponse']
export type FeedFetch = components['schemas']['FetchResponse']
export type Article = components['schemas']['ArticleResponse']
export type ArticleProvenance = components['schemas']['ArticleProvenance']
export interface CursorPage<T> { items: T[]; next_cursor: string | null }
